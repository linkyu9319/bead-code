from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont


@dataclass(frozen=True)
class ColorPair:
    dark: tuple[int, int, int]
    light: tuple[int, int, int]
    dark_hex: str
    light_hex: str
    contrast_ratio: float

    @classmethod
    def from_hex(cls, dark: str, light: str) -> "ColorPair":
        try:
            dark_rgb = ImageColor.getrgb(dark)
            light_rgb = ImageColor.getrgb(light)
        except ValueError as exc:
            raise ValueError("颜色必须是 #RRGGBB 格式") from exc
        if len(dark_rgb) != 3 or len(light_rgb) != 3:
            raise ValueError("颜色必须是不透明 RGB 颜色")

        dark_hex = "#" + "".join(f"{value:02X}" for value in dark_rgb)
        light_hex = "#" + "".join(f"{value:02X}" for value in light_rgb)
        return cls(
            dark=dark_rgb,
            light=light_rgb,
            dark_hex=dark_hex,
            light_hex=light_hex,
            contrast_ratio=_contrast_ratio(dark_rgb, light_rgb),
        )


def render_preview(
    bead_grid: np.ndarray,
    colors: ColorPair,
    cell_px: int,
) -> Image.Image:
    """生成无网格扫码图；使用整数倍最近邻扩展，保持模块边缘锐利。"""
    rgb = np.where(bead_grid[..., None], colors.dark, colors.light).astype(np.uint8)
    image = Image.fromarray(rgb, mode="RGB")
    return image.resize(
        (image.width * cell_px, image.height * cell_px),
        resample=Image.Resampling.NEAREST,
    )


def render_blueprint(
    bead_grid: np.ndarray,
    colors: ColorPair,
    cell_px: int,
    dark_label: str,
    light_label: str,
) -> Image.Image:
    """生成每格带实体色号、行列坐标和十格粗线的施工图。"""
    grid_size = int(bead_grid.shape[0])
    label_margin = max(48, cell_px * 3)
    grid_pixels = grid_size * cell_px
    canvas = Image.new(
        "RGB",
        (grid_pixels + label_margin * 2, grid_pixels + label_margin * 2),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    label_styles = {
        True: _build_label_style(draw, dark_label, colors.dark, cell_px),
        False: _build_label_style(draw, light_label, colors.light, cell_px),
    }

    left = label_margin
    top = label_margin
    for row in range(grid_size):
        for col in range(grid_size):
            x0 = left + col * cell_px
            y0 = top + row * cell_px
            is_dark = bool(bead_grid[row, col])
            color = colors.dark if is_dark else colors.light
            draw.rectangle((x0, y0, x0 + cell_px, y0 + cell_px), fill=color)
            label, label_font, text_color, text_width, text_height = label_styles[is_dark]
            draw.text(
                (
                    x0 + (cell_px - text_width) / 2,
                    y0 + (cell_px - text_height) / 2 - 1,
                ),
                label,
                fill=text_color,
                font=label_font,
            )

    # 十格粗线用于快速定位；每格细线保留逐颗拼豆的施工精度。
    for index in range(grid_size + 1):
        position = index * cell_px
        width = 2 if index % 10 == 0 else 1
        line_color = "#64748B" if index % 10 == 0 else "#CBD5E1"
        draw.line(
            (left + position, top, left + position, top + grid_pixels),
            fill=line_color,
            width=width,
        )
        draw.line(
            (left, top + position, left + grid_pixels, top + position),
            fill=line_color,
            width=width,
        )

    for index in range(0, grid_size, 5):
        label = str(index + 1)
        x = left + index * cell_px + 2
        y = top + index * cell_px + 2
        draw.text((x, top - 18), label, fill="#0F172A", font=font)
        draw.text((left - 34, y), label, fill="#0F172A", font=font)

    title = f"QR bead blueprint  {grid_size} x {grid_size}"
    draw.text((left, 8), title, fill="#0F172A", font=font)
    return canvas


def _build_label_style(
    draw: ImageDraw.ImageDraw,
    label: str,
    background: tuple[int, int, int],
    cell_px: int,
) -> tuple[str, ImageFont.ImageFont, str, int, int]:
    """为不同长度色号选择能完整放入单颗拼豆格的字号和前景色。"""
    font_size = max(5, min(10, int(cell_px * 0.42)))
    while True:
        font = ImageFont.load_default(size=font_size)
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        width, height = right - left, bottom - top
        if width <= cell_px - 2 or font_size <= 5:
            break
        font_size -= 1

    text_color = "#FFFFFF" if _relative_luminance(background) < 0.42 else "#0F172A"
    return label, font, text_color, width, height


def _contrast_ratio(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
) -> float:
    lighter = max(_relative_luminance(first), _relative_luminance(second))
    darker = min(_relative_luminance(first), _relative_luminance(second))
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for value in rgb:
        normalized = value / 255.0
        channels.append(
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

