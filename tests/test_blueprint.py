from __future__ import annotations

import json
from pathlib import Path

import pytest
import qrcode
from fastapi.testclient import TestClient

from qr_bead_blueprint.core import (
    BlueprintConfig,
    QrCapacityError,
    QrDecodeError,
    convert_qr_to_blueprint,
    qr_version_from_size,
    regenerate_clean_qr,
    verify_rendered_qr,
)
from qr_bead_blueprint.palettes import available_palettes, match_palette_pair
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


def test_input_decode_falls_back_to_zxing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenCV 解不出的大版本码，不应被直接拒收（实测 v18/v24/v27 会失败）。"""
    payload = "https://qr.alipay.com/" + "c" * 170
    source = tmp_path / "large-code.png"
    qr = qrcode.QRCode(
        version=24,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        border=4,
        box_size=8,
    )
    qr.add_data(payload)
    qr.make(fit=False)
    qr.make_image().save(source)

    class EmptyOpenCvDetector:
        def detectAndDecode(self, _image):
            return "", None, None

    monkeypatch.setattr("qr_bead_blueprint.core.cv2.QRCodeDetector", EmptyOpenCvDetector)

    output = tmp_path / "result"
    result = convert_qr_to_blueprint(source, output, BlueprintConfig(beads_per_module=1))

    assert result.scan_verified is True
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["source_modules_available"] is False
    assert metadata["source_qr_version"] is None


def test_long_payload_survives_when_opencv_cannot_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回归用例：长内容 + OpenCV 解不出，之前会直接 422，现在应完整生成。"""
    payload = "https://qr.alipay.com/" + "a" * 1400
    source = tmp_path / "long-code.png"
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        border=4,
        box_size=30,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr.make_image().save(source)

    class EmptyOpenCvDetector:
        def detectAndDecode(self, _image):
            return "", None, None

    monkeypatch.setattr("qr_bead_blueprint.core.cv2.QRCodeDetector", EmptyOpenCvDetector)

    output = tmp_path / "result"
    result = convert_qr_to_blueprint(source, output, BlueprintConfig(beads_per_module=1))

    assert result.scan_verified is True
    assert result.error_correction in {"Q", "M", "L"}
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["error_correction_degraded"] is True
    assert payload not in (output / "metadata.json").read_text(encoding="utf-8")


def test_preserve_mode_requires_module_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "code.png"
    qrcode.make("https://example.com/pay?id=no-matrix").save(source)

    class EmptyOpenCvDetector:
        def detectAndDecode(self, _image):
            return "", None, None

    monkeypatch.setattr("qr_bead_blueprint.core.cv2.QRCodeDetector", EmptyOpenCvDetector)

    with pytest.raises(QrDecodeError, match="兼容模式"):
        convert_qr_to_blueprint(
            source,
            tmp_path / "out",
            BlueprintConfig(preserve_source_modules=True),
        )


def test_regenerate_degrades_error_correction_for_long_payload() -> None:
    """H 级放不下的长链接应降级重建，而不是整体失败。"""
    decoded = regenerate_clean_qr("https://qr.alipay.com/" + "a" * 1400)
    assert decoded.error_correction in {"Q", "M", "L"}
    assert decoded.modules is not None
    assert decoded.version is not None and decoded.version <= 40


def test_regenerate_rejects_payload_beyond_lowest_level() -> None:
    with pytest.raises(QrCapacityError, match="低纠错等级"):
        regenerate_clean_qr("https://qr.alipay.com/" + "a" * 4000)


def test_auto_mode_falls_back_to_preserve_on_capacity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内容超出 H 级容量时，应自动降级到兼容模式而不是返回 422/500。"""
    from web_app import _convert_with_fallback

    source = tmp_path / "code.png"
    qrcode.make("https://example.com/pay?id=capacity").save(source)

    def raise_capacity(_payload: str):
        raise QrCapacityError("模拟容量溢出")

    monkeypatch.setattr(
        "qr_bead_blueprint.core.regenerate_clean_qr",
        raise_capacity,
    )

    _result, _result_dir, used_mode = _convert_with_fallback(
        upload_path=source,
        root=tmp_path / "job",
        beads_per_module=1,
        bead_size_mm=5.0,
        dark_color="#111827",
        light_color="#FFFFFF",
        palette="mard-221",
        compatibility_mode="auto",
        allow_low_contrast=False,
    )

    assert used_mode == "preserve"


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

