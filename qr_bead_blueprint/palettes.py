from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PALETTE_FILES = {
    "mard-221": "mard-221.json",
    "coco-291": "coco-291.json",
    "artkal-c-197": "artkal-c-197.json",
}


@dataclass(frozen=True)
class BeadColor:
    code: str
    hex: str
    rgb: tuple[int, int, int]
    lab: tuple[float, float, float]


@dataclass(frozen=True)
class BeadPalette:
    key: str
    title: str
    colors: tuple[BeadColor, ...]


@dataclass(frozen=True)
class PalettePair:
    key: str
    title: str
    dark: BeadColor
    light: BeadColor


def available_palettes() -> tuple[tuple[str, str], ...]:
    """返回 Web、CLI 与后端共用的稳定色卡标识。"""
    return tuple((key, load_palette(key).title) for key in PALETTE_FILES)


@lru_cache(maxsize=None)
def load_palette(key: str) -> BeadPalette:
    """加载随版本固定的实体拼豆色卡，避免线上生成依赖外部网站。"""
    filename = PALETTE_FILES.get(key)
    if filename is None:
        raise ValueError(f"不支持的色号标准：{key}")

    path = Path(__file__).with_name("palettes") / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    colors = tuple(
        _build_color(item["code"], item["hex"])
        for item in payload["colors"]
    )
    if not colors:
        raise ValueError(f"色卡 {key} 没有可用颜色")
    return BeadPalette(key=payload["key"], title=payload["title"], colors=colors)


def match_palette_pair(key: str, dark_hex: str, light_hex: str) -> PalettePair:
    """按 CIE Lab 视觉距离将页面颜色映射为指定品牌的可购买色号。"""
    palette = load_palette(key)
    return PalettePair(
        key=palette.key,
        title=palette.title,
        dark=_nearest_color(palette, dark_hex),
        light=_nearest_color(palette, light_hex),
    )


def match_palette_color(key: str, target_hex: str) -> BeadColor:
    """将任意颜色映射为指定品牌中视觉距离最近的可购买色号。"""
    return _nearest_color(load_palette(key), target_hex)


def _nearest_color(palette: BeadPalette, target_hex: str) -> BeadColor:
    target = _build_color("target", target_hex)
    return min(
        palette.colors,
        key=lambda candidate: sum(
            (candidate.lab[index] - target.lab[index]) ** 2
            for index in range(3)
        ),
    )


def _build_color(code: str, value: str) -> BeadColor:
    normalized = value.strip().upper()
    if len(normalized) != 7 or not normalized.startswith("#"):
        raise ValueError("颜色必须是 #RRGGBB 格式")
    try:
        rgb = tuple(int(normalized[index:index + 2], 16) for index in (1, 3, 5))
    except ValueError as exc:
        raise ValueError("颜色必须是 #RRGGBB 格式") from exc
    return BeadColor(code=code, hex=normalized, rgb=rgb, lab=_rgb_to_lab(rgb))


def _rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """把 sRGB 转为 CIE Lab；比直接比较 RGB 更接近人眼对色差的感受。"""
    linear = []
    for value in rgb:
        channel = value / 255.0
        linear.append(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    x = (linear[0] * 0.4124 + linear[1] * 0.3576 + linear[2] * 0.1805) / 0.95047
    y = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
    z = (linear[0] * 0.0193 + linear[1] * 0.1192 + linear[2] * 0.9505) / 1.08883

    def pivot(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    fx, fy, fz = pivot(x), pivot(y), pivot(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)
