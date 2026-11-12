from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import qrcode
import zxingcpp
from qrcode.constants import (
    ERROR_CORRECT_H,
    ERROR_CORRECT_L,
    ERROR_CORRECT_M,
    ERROR_CORRECT_Q,
)
from PIL import Image

from .palettes import PalettePair, load_palette, match_palette_pair
from .render import ColorPair, render_blueprint, render_preview


class QrDecodeError(ValueError):
    """输入图片无法恢复为可靠二维码模块时抛出。"""


class QrCapacityError(QrDecodeError):
    """内容过长，连最低纠错等级都无法编码；只能保留平台原始矩阵。"""


# 干净重建时优先 H 级；内容过长时逐级降级，避免合法长链接被整体拒绝。
ERROR_CORRECTION_FALLBACK = (
    ("H", ERROR_CORRECT_H),
    ("Q", ERROR_CORRECT_Q),
    ("M", ERROR_CORRECT_M),
    ("L", ERROR_CORRECT_L),
)


@dataclass(frozen=True)
class BlueprintConfig:
    beads_per_module: int = 2
    quiet_zone_modules: int = 4
    bead_size_mm: float = 5.0
    blueprint_cell_px: int = 22
    preview_cell_px: int = 10
    dark_color: str = "#111827"
    light_color: str = "#FFFFFF"
    palette: str = "mard-221"
    allow_low_contrast: bool = False
    preserve_source_modules: bool = False

    def validate(self) -> None:
        if not 1 <= self.beads_per_module <= 4:
            raise ValueError("beads_per_module 必须在 1 到 4 之间")
        if self.quiet_zone_modules < 4:
            raise ValueError("二维码静区不能少于 4 个模块")
        if self.bead_size_mm <= 0:
            raise ValueError("bead_size_mm 必须大于 0")
        if not 6 <= self.blueprint_cell_px <= 48:
            raise ValueError("blueprint_cell_px 必须在 6 到 48 之间")
        if not 4 <= self.preview_cell_px <= 32:
            raise ValueError("preview_cell_px 必须在 4 到 32 之间")

        colors = ColorPair.from_hex(self.dark_color, self.light_color)
        load_palette(self.palette)
        if not self.allow_low_contrast and colors.contrast_ratio < 4.5:
            raise ValueError(
                f"深浅颜色对比度仅 {colors.contrast_ratio:.2f}:1，低于安全阈值 4.5:1；"
                "请更换颜色，或明确使用 --allow-low-contrast"
            )


@dataclass(frozen=True)
class BlueprintResult:
    output_dir: Path
    version: int
    module_size: int
    bead_grid_size: int
    dark_beads: int
    light_beads: int
    physical_size_mm: float
    payload_sha256: str
    scan_verified: bool
    palette_key: str
    palette_title: str
    dark_code: str
    light_code: str
    dark_hex: str
    light_hex: str
    error_correction: str = "H"


@dataclass(frozen=True)
class DecodedQr:
    # 兼容性说明：ZXing 兜底解码只能拿到文本，无法拿到模块矩阵，
    # 因此 modules/version 允许为空，仅兼容模式要求矩阵存在。
    payload: str
    modules: np.ndarray | None
    version: int | None
    # 实际使用的纠错等级；兼容模式沿用平台原码，无法确定时记为 "source"。
    error_correction: str = "H"


def qr_version_from_size(module_size: int) -> int:
    """根据标准 QR 边长反推版本，拒绝被普通缩放污染的非标准矩阵。"""
    if module_size < 21 or module_size > 177 or (module_size - 21) % 4 != 0:
        raise QrDecodeError(
            f"识别结果为 {module_size}×{module_size}，不是标准 QR 模块尺寸"
        )
    return (module_size - 21) // 4 + 1


def decode_qr_image(input_path: Path) -> DecodedQr:
    """解码二维码；优先取 OpenCV 的模块矩阵，取不到时用 ZXing 只解内容。"""
    image_bytes = np.fromfile(input_path, dtype=np.uint8)
    image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
    if image is None:
        raise QrDecodeError(f"无法读取图片：{input_path}")

    detector = cv2.QRCodeDetector()
    payload, _points, straight = detector.detectAndDecode(image)
    if payload:
        modules = _modules_from_straight(straight)
        version = qr_version_from_size(int(modules.shape[0])) if modules is not None else None
        return DecodedQr(
            payload=payload,
            modules=modules,
            version=version,
            error_correction="source",
        )

    # 兼容性说明：OpenCV 对部分合法 QR 版本和掩码（实测 v18/v24/v27 等）会解不出内容，
    # 但同一张图 ZXing 可以正常解码。干净重建只需要内容，因此这里兜底避免误拒合法码。
    payload = _decode_payload_with_zxing(image)
    if not payload:
        raise QrDecodeError(
            "没有识别到可解码二维码；请使用清晰原图，并确保二维码四周没有被裁掉"
        )
    return DecodedQr(payload=payload, modules=None, version=None, error_correction="source")


def _modules_from_straight(straight: np.ndarray | None) -> np.ndarray | None:
    """把 OpenCV 校正结果转成模块矩阵；不合格时返回 None，由调用方决定降级策略。"""
    if straight is None or straight.size == 0:
        return None
    if straight.ndim == 3:
        straight = cv2.cvtColor(straight, cv2.COLOR_BGR2GRAY)
    if straight.shape[0] != straight.shape[1]:
        return None
    module_size = int(straight.shape[0])
    if module_size < 21 or module_size > 177 or (module_size - 21) % 4 != 0:
        # 常见于被普通缩放或截图污染的原图：内容可用，但不适合原样照抄模块。
        return None
    return np.asarray(straight < 128, dtype=np.bool_)


def _decode_payload_with_zxing(image: np.ndarray) -> str:
    """使用独立解码器只取文本内容；失败返回空字符串。"""
    try:
        barcodes = zxingcpp.read_barcodes(
            image,
            formats=zxingcpp.BarcodeFormat.QRCode,
        )
    except Exception:  # pragma: no cover - 第三方库内部异常不应中断整个请求
        return ""
    for barcode in barcodes:
        if barcode.text:
            return barcode.text
    return ""


def build_bead_grid(
    modules: np.ndarray,
    quiet_zone_modules: int,
    beads_per_module: int,
) -> np.ndarray:
    """将每个 QR 模块完整扩展为拼豆块，禁止插值以免破坏编码结构。"""
    with_quiet_zone = np.pad(
        modules,
        pad_width=quiet_zone_modules,
        mode="constant",
        constant_values=False,
    )
    return np.repeat(
        np.repeat(with_quiet_zone, beads_per_module, axis=0),
        beads_per_module,
        axis=1,
    )


def regenerate_clean_qr(payload: str) -> DecodedQr:
    """使用同一收款内容重建无 Logo 的二维码；H 级放不下时逐级降级。

    安全规则：原图中央头像会占用真实模块；若直接照抄，拼豆误差会与既有遮挡叠加，
    因此默认重建。但很长的收款链接无法在 H 级编码，此时降级到 Q/M/L，
    并把实际等级写进元数据，避免整条合法链接被拒绝。
    """
    last_error: Exception | None = None
    for label, level in ERROR_CORRECTION_FALLBACK:
        qr = qrcode.QRCode(
            version=None,
            error_correction=level,
            box_size=1,
            border=0,
        )
        qr.add_data(payload, optimize=0)
        try:
            qr.make(fit=True)
        except Exception as exc:  # qrcode 在容量溢出时抛 ValueError("Invalid version (was 41...)")
            last_error = exc
            continue
        modules = np.asarray(qr.get_matrix(), dtype=np.bool_)
        return DecodedQr(
            payload=payload,
            modules=modules,
            version=qr_version_from_size(int(modules.shape[0])),
            error_correction=label,
        )

    raise QrCapacityError(
        f"二维码内容长度 {len(payload)} 字节，超出最低纠错等级（L）的编码上限；"
        "只能保留平台原始模块，请使用兼容模式"
    ) from last_error


def verify_rendered_qr(preview_path: Path, expected_payload: str) -> bool:
    """使用双引擎重新扫描生成结果，避免单一解码器误判或漏判。"""
    image_bytes = np.fromfile(preview_path, dtype=np.uint8)
    image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
    if image is None:
        return False

    payload, _points, _straight = cv2.QRCodeDetector().detectAndDecode(image)
    if payload == expected_payload:
        return True

    # 兼容性说明：OpenCV 对部分合法 QR 版本和掩码会返回空内容，ZXing 用作独立复验兜底。
    barcodes = zxingcpp.read_barcodes(
        image,
        formats=zxingcpp.BarcodeFormat.QRCode,
        try_rotate=False,
    )
    return any(barcode.text == expected_payload for barcode in barcodes)


def convert_qr_to_blueprint(
    input_path: str | Path,
    output_dir: str | Path,
    config: BlueprintConfig | None = None,
) -> BlueprintResult:
    """将一张可扫描二维码转换为拼豆图纸及用量清单。"""
    config = config or BlueprintConfig()
    config.validate()

    source = Path(input_path).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"输入图片不存在：{source}")
    target.mkdir(parents=True, exist_ok=True)

    source_qr = decode_qr_image(source)
    # 微信等平台码可能携带 OpenCV 不能稳定重建的 ECI 特征，此时允许原样保留平台矩阵。
    if config.preserve_source_modules:
        if source_qr.modules is None:
            raise QrDecodeError(
                "兼容模式需要从原图恢复完整模块矩阵，但当前图片只能解出内容；"
                "请换用更清晰的原图，或改用干净重建模式"
            )
        decoded = source_qr
    else:
        decoded = regenerate_clean_qr(source_qr.payload)
    if decoded.modules is None or decoded.version is None:  # pragma: no cover
        raise QrDecodeError("内部错误：二维码模块矩阵缺失")
    bead_grid = build_bead_grid(
        decoded.modules,
        config.quiet_zone_modules,
        config.beads_per_module,
    )
    # 色卡映射必须先于扫码复验：预览与实物采购使用同一组实体近似色。
    palette = match_palette_pair(
        config.palette,
        config.dark_color,
        config.light_color,
    )
    colors = ColorPair.from_hex(palette.dark.hex, palette.light.hex)
    if not config.allow_low_contrast and colors.contrast_ratio < 4.5:
        raise ValueError(
            f"{palette.title} 匹配后的色号对比度仅 {colors.contrast_ratio:.2f}:1，"
            "低于安全阈值 4.5:1；请更换颜色或允许低对比色后充分实测"
        )

    preview_path = target / "scan_preview.png"
    blueprint_path = target / "blueprint.png"
    pdf_path = target / "blueprint.pdf"
    render_preview(bead_grid, colors, config.preview_cell_px).save(preview_path)

    scan_verified = verify_rendered_qr(preview_path, decoded.payload)
    if not scan_verified:
        _mark_failed_output(target)
        raise QrDecodeError(
            "生成后的扫码预览未能通过自动复扫，已保留输出供排查，但不要据此制作或收款"
        )

    (target / "VERIFICATION_FAILED.txt").unlink(missing_ok=True)
    blueprint = render_blueprint(
        bead_grid,
        colors,
        config.blueprint_cell_px,
        palette.dark.code,
        palette.light.code,
    )
    blueprint.save(blueprint_path)
    blueprint.convert("RGB").save(pdf_path, "PDF", resolution=300.0)

    dark_beads = int(np.count_nonzero(bead_grid))
    total_beads = int(bead_grid.size)
    light_beads = total_beads - dark_beads
    bead_grid_size = int(bead_grid.shape[0])
    physical_size_mm = bead_grid_size * config.bead_size_mm
    payload_sha256 = hashlib.sha256(decoded.payload.encode("utf-8")).hexdigest()

    _write_materials_csv(
        target / "materials.csv",
        palette,
        dark_beads,
        light_beads,
    )
    _write_metadata(
        target / "metadata.json",
        source_qr,
        decoded,
        config,
        bead_grid_size,
        dark_beads,
        light_beads,
        physical_size_mm,
        payload_sha256,
        scan_verified,
        palette,
    )

    return BlueprintResult(
        output_dir=target,
        version=decoded.version,
        module_size=int(decoded.modules.shape[0]),
        bead_grid_size=bead_grid_size,
        dark_beads=dark_beads,
        light_beads=light_beads,
        physical_size_mm=physical_size_mm,
        payload_sha256=payload_sha256,
        scan_verified=scan_verified,
        palette_key=palette.key,
        palette_title=palette.title,
        dark_code=palette.dark.code,
        light_code=palette.light.code,
        dark_hex=palette.dark.hex,
        light_hex=palette.light.hex,
        error_correction=decoded.error_correction,
    )


def _write_materials_csv(
    path: Path,
    palette: PalettePair,
    dark_beads: int,
    light_beads: int,
) -> None:
    # 额外预留 8% 损耗，避免熨烫失败或颜色瑕疵导致实物中途缺豆。
    rows = [
        ("深色", palette.dark.code, palette.dark.hex, dark_beads),
        ("浅色/静区", palette.light.code, palette.light.hex, light_beads),
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["色号标准", "用途", "色号", "参考颜色", "精确数量", "建议准备数量(+8%)"])
        for label, code, color, count in rows:
            writer.writerow(
                [palette.title, label, code, color, count, (count * 108 + 99) // 100]
            )


def _write_metadata(
    path: Path,
    source_qr: DecodedQr,
    decoded: DecodedQr,
    config: BlueprintConfig,
    bead_grid_size: int,
    dark_beads: int,
    light_beads: int,
    physical_size_mm: float,
    payload_sha256: str,
    scan_verified: bool,
    palette: PalettePair,
) -> None:
    # 隐私规则：元数据只保留摘要，不落盘支付宝收款链接明文。
    metadata = {
        "source_qr_version": source_qr.version,
        "source_qr_module_size": (
            None if source_qr.modules is None else int(source_qr.modules.shape[0])
        ),
        "source_modules_available": source_qr.modules is not None,
        "matrix_mode": (
            "preserve_source_modules"
            if config.preserve_source_modules
            else f"regenerated_error_correction_{decoded.error_correction.lower()}"
        ),
        "qr_version": decoded.version,
        "qr_module_size": int(decoded.modules.shape[0]),
        "error_correction": decoded.error_correction,
        "error_correction_degraded": (
            decoded.error_correction not in {"H", "source"}
        ),
        "quiet_zone_modules": config.quiet_zone_modules,
        "beads_per_module": config.beads_per_module,
        "bead_grid_size": bead_grid_size,
        "bead_size_mm": config.bead_size_mm,
        "physical_size_mm": round(physical_size_mm, 2),
        "palette_key": palette.key,
        "palette_title": palette.title,
        "requested_dark_color": config.dark_color.upper(),
        "requested_light_color": config.light_color.upper(),
        "dark_code": palette.dark.code,
        "light_code": palette.light.code,
        "dark_color": palette.dark.hex,
        "light_color": palette.light.hex,
        "dark_beads": dark_beads,
        "light_beads": light_beads,
        "total_beads": dark_beads + light_beads,
        "payload_length": len(decoded.payload),
        "payload_sha256": payload_sha256,
        "scan_verified": scan_verified,
    }
    path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mark_failed_output(target: Path) -> None:
    """清除旧的成功产物，只留下失败预览和醒目标记，避免误用于真实收款。"""
    for name in ("blueprint.png", "blueprint.pdf", "materials.csv", "metadata.json"):
        (target / name).unlink(missing_ok=True)
    (target / "VERIFICATION_FAILED.txt").write_text(
        "自动扫码复验失败。此目录不是可交付图纸，请勿制作或用于收款。\n",
        encoding="utf-8",
    )

