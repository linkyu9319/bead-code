from __future__ import annotations

import json
from pathlib import Path

import pytest
import qrcode
from fastapi.testclient import TestClient

from qr_bead_blueprint.core import (
    BlueprintConfig,
    QrDecodeError,
    convert_qr_to_blueprint,
    qr_version_from_size,
    verify_rendered_qr,
)
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
                "compatibility_mode": "auto",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["scan_verified"] is True

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

