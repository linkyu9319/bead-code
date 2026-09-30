from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import BlueprintConfig, QrDecodeError, convert_qr_to_blueprint
from .palettes import PALETTE_FILES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将支付宝等静态二维码转换为可扫码的拼豆图纸",
    )
    parser.add_argument("input", type=Path, help="收款码图片路径")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="输出目录；默认在输入图片旁创建同名 _bead_blueprint 目录",
    )
    parser.add_argument(
        "--beads-per-module",
        type=int,
        choices=range(1, 5),
        default=2,
        metavar="1-4",
        help="一个二维码模块使用几乘几颗拼豆，默认 2",
    )
    parser.add_argument("--bead-size-mm", type=float, default=5.0, help="拼豆直径，默认 5mm")
    parser.add_argument("--dark-color", default="#111827", help="深色拼豆颜色")
    parser.add_argument("--light-color", default="#FFFFFF", help="浅色拼豆颜色")
    parser.add_argument(
        "--palette",
        choices=PALETTE_FILES,
        default="mard-221",
        help="实体拼豆色号标准，默认 MARD 221",
    )
    parser.add_argument(
        "--allow-low-contrast",
        action="store_true",
        help="允许低对比色；可能导致实物无法扫码",
    )
    parser.add_argument(
        "--preserve-source-modules",
        action="store_true",
        help="保留平台原始模块；仅用于干净重建无法复扫的兼容场景",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 管道环境可能仍使用本地代码页，显式 UTF-8 避免中文执行摘要乱码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    output = args.output or args.input.with_name(f"{args.input.stem}_bead_blueprint")
    config = BlueprintConfig(
        beads_per_module=args.beads_per_module,
        bead_size_mm=args.bead_size_mm,
        dark_color=args.dark_color,
        light_color=args.light_color,
        palette=args.palette,
        allow_low_contrast=args.allow_low_contrast,
        preserve_source_modules=args.preserve_source_modules,
    )

    try:
        result = convert_qr_to_blueprint(args.input, output, config)
    except (FileNotFoundError, ValueError, QrDecodeError) as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 2

    print("生成成功，并已通过自动扫码复验。")
    print(f"QR 版本：Version {result.version}（{result.module_size}×{result.module_size} 模块）")
    print(f"拼豆图纸：{result.bead_grid_size}×{result.bead_grid_size} 颗")
    print(f"深色拼豆：{result.dark_beads} 颗")
    print(f"浅色拼豆：{result.light_beads} 颗")
    print(
        f"色号标准：{result.palette_title}（深色 {result.dark_code} / 浅色 {result.light_code}）"
    )
    print(f"成品尺寸：约 {result.physical_size_mm / 10:.1f}×{result.physical_size_mm / 10:.1f} cm")
    print(f"二维码摘要：{result.payload_sha256[:16]}…（不保存明文）")
    print(f"输出目录：{result.output_dir}")
    return 0

