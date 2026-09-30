from __future__ import annotations

import json
from pathlib import Path

import pytest
import qrcode
from fastapi.testclient import TestClient
from PIL import Image

from qr_bead_blueprint.core import (
    BlueprintConfig,
    QrDecodeError,
    convert_qr_to_blueprint,
    qr_version_from_size,
    verify_rendered_qr,
)
from qr_bead_blueprint.palettes import available_palettes, match_palette_pair
from qr_bead_blueprint.image_mode import ImageBlueprintConfig, convert_image_to_blueprint
from web_app import app, job_store


def test_qr_version_from_size() -> None:
    assert qr_version_from_size(21) == 1
    assert qr_version_from_size(25) == 2
    assert qr_version_from_size(177) == 40
    with pytest.raises(QrDecodeError):
        qr_version_from_size(22)


def test_rejects_low_contrast() -> None:
    with pytest.raises(ValueError, match="对比度"):
        BlueprintConfig(dark_color="#AAAAAA", light_color="#BBBBBB").validate()


def test_palette_standards_resolve_to_purchase_codes() -> None:
    palettes = dict(available_palettes())
    assert set(palettes) == {"mard-221", "coco-291", "artkal-c-197"}

    matches = [
        match_palette_pair(key, "#07C160", "#FFFFFF")
        for key in palettes
    ]
    assert all(match.dark.code and match.light.code for match in matches)
    assert all(match.dark.hex.startswith("#") for match in matches)
    assert len({(match.dark.code, match.dark.hex) for match in matches}) >= 2


def test_end_to_end_conversion(tmp_path: Path) -> None:
    payload = "https://example.com/pay?id=ivy-test-2026"
    source = tmp_path / "payment-code.png"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(source)

    output = tmp_path / "result"
    result = convert_qr_to_blueprint(
        source,
        output,
        BlueprintConfig(beads_per_module=2, bead_size_mm=5.0),
    )

    assert result.scan_verified is True
    assert result.bead_grid_size == (result.module_size + 8) * 2
    assert result.dark_beads + result.light_beads == result.bead_grid_size**2
    assert (output / "scan_preview.png").is_file()
    assert (output / "blueprint.png").is_file()
    assert (output / "blueprint.pdf").is_file()
    assert (output / "materials.csv").is_file()

    materials = (output / "materials.csv").read_text(encoding="utf-8-sig")
    assert result.palette_title in materials
    assert result.dark_code in materials
    assert result.light_code in materials

    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["scan_verified"] is True
    assert metadata["source_qr_version"] >= 1
    assert metadata["payload_length"] == len(payload)
    assert payload not in (output / "metadata.json").read_text(encoding="utf-8")


def test_verification_falls_back_to_zxing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = "https://qr.alipay.com/example"
    preview = tmp_path / "preview.png"
    qrcode.make(payload).save(preview)

    class EmptyOpenCvDetector:
        def detectAndDecode(self, _image):
            return "", None, None

    monkeypatch.setattr("qr_bead_blueprint.core.cv2.QRCodeDetector", EmptyOpenCvDetector)

    assert verify_rendered_qr(preview, payload) is True


def test_web_generation_and_direct_downloads(tmp_path: Path) -> None:
    payload = "https://example.com/pay?id=ivy-web-test"
    source = tmp_path / "web-code.png"
    qrcode.make(payload).save(source)

    with TestClient(app) as client, source.open("rb") as image:
        response = client.post(
            "/api/generate",
            files={"image": ("web-code.png", image, "image/png")},
            data={
                "platform": "other",
                "beads_per_module": "1",
                "bead_size_mm": "5",
                "dark_color": "#111827",
                "light_color": "#FFFFFF",
                "palette": "coco-291",
                "compatibility_mode": "auto",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["scan_verified"] is True
        assert body["summary"]["palette_key"] == "coco-291"
        assert body["summary"]["dark_code"]
        assert body["summary"]["light_code"]

        blueprint = client.get(body["files"]["blueprint.png"])
        assert blueprint.status_code == 200
        assert blueprint.headers["content-type"] == "image/png"
        assert blueprint.headers["content-disposition"].startswith("inline;")

        mobile_download = client.get(f'{body["files"]["blueprint.png"]}?download=1')
        assert mobile_download.status_code == 200
        assert mobile_download.headers["content-disposition"].startswith("attachment;")

        pdf = client.get(body["files"]["blueprint.pdf"])
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")

        assert "package.zip" not in body["files"]

        job_store.discard(body["job_id"])


def test_regular_image_conversion_creates_labeled_blueprint(tmp_path: Path) -> None:
    source = tmp_path / "character.png"
    image = Image.new("RGB", (96, 64), "#1677FF")
    for x in range(48, 96):
        for y in range(64):
            image.putpixel((x, y), (255, y * 3, 80))
    image.save(source)

    output = tmp_path / "image-result"
    result = convert_image_to_blueprint(
        source,
        output,
        ImageBlueprintConfig(grid_size=32, color_limit=8, palette="mard-221"),
    )

    assert result.grid_size == 32
    assert 1 <= len(result.materials) <= 8
    assert sum(item.count for item in result.materials) == 32**2
    assert (output / "preview.png").is_file()
    assert (output / "blueprint.png").is_file()
    assert (output / "blueprint.pdf").is_file()
    assert (output / "materials.csv").is_file()


def test_web_regular_image_generation(tmp_path: Path) -> None:
    source = tmp_path / "avatar.png"
    Image.new("RGB", (80, 120), "#07C160").save(source)

    with TestClient(app) as client, source.open("rb") as image:
        response = client.post(
            "/api/generate-image",
            files={"image": ("avatar.png", image, "image/png")},
            data={
                "grid_size": "48",
                "color_limit": "16",
                "bead_size_mm": "5",
                "palette": "artkal-c-197",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "image"
        assert body["summary"]["grid_size"] == 48
        assert body["summary"]["total_beads"] == 48**2
        assert body["summary"]["color_count"] == 1

        preview = client.get(body["files"]["preview.png"])
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"

        blueprint = client.get(f'{body["files"]["blueprint.png"]}?download=1')
        assert blueprint.status_code == 200
        assert blueprint.headers["content-disposition"].startswith("attachment;")

        job_store.discard(body["job_id"])

