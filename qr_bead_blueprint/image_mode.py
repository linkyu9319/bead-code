from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .palettes import load_palette, match_palette_color
from .render import render_color_blueprint, render_color_preview


@dataclass(frozen=True)
class ImageBlueprintConfig:
    grid_size: int = 48
    color_limit: int = 16
    bead_size_mm: float = 5.0
    blueprint_cell_px: int = 22
    preview_cell_px: int = 10
    palette: str = "mard-221"

    def validate(self) -> None:
        if self.grid_size not in {32, 48, 64}:
            raise ValueError("图片尺寸仅支持 32、48 或 64 格")
        if self.color_limit not in {8, 16, 24, 32}:
            raise ValueError("颜色数量仅支持 8、16、24 或 32 色")
        if self.bead_size_mm <= 0:
            raise ValueError("bead_size_mm 必须大于 0")
        load_palette(self.palette)


@dataclass(frozen=True)
class ImageMaterial:
    code: str
    hex: str
    count: int


@dataclass(frozen=True)
class ImageBlueprintResult:
    output_dir: Path
    grid_size: int
    physical_size_mm: float
    palette_key: str
    palette_title: str
    materials: tuple[ImageMaterial, ...]


def convert_image_to_blueprint(
    input_path: str | Path,
    output_dir: str | Path,
    config: ImageBlueprintConfig | None = None,
) -> ImageBlueprintResult:
    """将普通图片裁切、减色并映射为可购买色号的拼豆施工图。"""
    config = config or ImageBlueprintConfig()
    config.validate()
    source = Path(input_path).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"输入图片不存在：{source}")
    target.mkdir(parents=True, exist_ok=True)

    pixel_image = _prepare_pixel_image(source, config.grid_size, config.color_limit)
    color_grid, label_grid, materials = _map_to_bead_palette(pixel_image, config.palette)

    preview = render_color_preview(color_grid, config.preview_cell_px)
    blueprint = render_color_blueprint(color_grid, label_grid, config.blueprint_cell_px)
    preview.save(target / "preview.png")
    blueprint.save(target / "blueprint.png")
    blueprint.convert("RGB").save(target / "blueprint.pdf", "PDF", resolution=300.0)
    _write_materials(target / "materials.csv", config.palette, materials)
    _write_metadata(target / "metadata.json", config, materials)

    palette = load_palette(config.palette)
    return ImageBlueprintResult(
        output_dir=target,
        grid_size=config.grid_size,
        physical_size_mm=config.grid_size * config.bead_size_mm,
        palette_key=palette.key,
        palette_title=palette.title,
        materials=materials,
    )


def _prepare_pixel_image(source: Path, grid_size: int, color_limit: int) -> Image.Image:
    """按中心裁切为正方形，并先在视觉颜色空间减色以避免材料种类失控。"""
    try:
        with Image.open(source) as opened:
            transposed = ImageOps.exif_transpose(opened)
            if "A" in transposed.getbands():
                rgba = transposed.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                rgb = background.convert("RGB")
            else:
                rgb = transposed.convert("RGB")
            fitted = ImageOps.fit(
                rgb,
                (grid_size, grid_size),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法读取图片，请上传 PNG、JPG 或 WEBP 原图") from exc

    quantized = fitted.quantize(
        colors=color_limit,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    return quantized.convert("RGB")


def _map_to_bead_palette(
    pixel_image: Image.Image,
    palette_key: str,
) -> tuple[np.ndarray, np.ndarray, tuple[ImageMaterial, ...]]:
    """批量复用相同源色的 Lab 匹配结果，并合并映射到同一实体色号的数量。"""
    pixels = np.asarray(pixel_image, dtype=np.uint8)
    unique_colors = np.unique(pixels.reshape(-1, 3), axis=0)
    mapping = {}
    for color in unique_colors:
        rgb = tuple(int(value) for value in color)
        value = "#" + "".join(f"{channel:02X}" for channel in rgb)
        mapping[rgb] = match_palette_color(palette_key, value)

    color_grid = np.empty_like(pixels)
    label_grid = np.empty(pixels.shape[:2], dtype=object)
    counts: Counter[str] = Counter()
    matched_colors = {}
    for row in range(pixels.shape[0]):
        for column in range(pixels.shape[1]):
            source_rgb = tuple(int(value) for value in pixels[row, column])
            matched = mapping[source_rgb]
            color_grid[row, column] = matched.rgb
            label_grid[row, column] = matched.code
            counts[matched.code] += 1
            matched_colors[matched.code] = matched

    materials = tuple(
        ImageMaterial(code=code, hex=matched_colors[code].hex, count=count)
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return color_grid, label_grid, materials


def _write_materials(
    path: Path,
    palette_key: str,
    materials: tuple[ImageMaterial, ...],
) -> None:
    palette = load_palette(palette_key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["色号标准", "色号", "颜色", "实际颗数", "建议准备（含8%损耗）"])
        for material in materials:
            writer.writerow(
                [
                    palette.title,
                    material.code,
                    material.hex,
                    material.count,
                    (material.count * 108 + 99) // 100,
                ]
            )


def _write_metadata(
    path: Path,
    config: ImageBlueprintConfig,
    materials: tuple[ImageMaterial, ...],
) -> None:
    payload = {
        "mode": "image",
        "grid_size": config.grid_size,
        "color_limit": config.color_limit,
        "bead_size_mm": config.bead_size_mm,
        "palette_key": config.palette,
        "materials": [asdict(material) for material in materials],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
