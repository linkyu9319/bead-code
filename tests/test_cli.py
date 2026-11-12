from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from qr_bead_blueprint import cli


@pytest.mark.parametrize("level", ["source", "H", "Q", "M", "L"])
def test_cli_only_warns_for_actual_correction_degradation(
    level: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = SimpleNamespace(
        version=1, module_size=21, error_correction=level, bead_grid_size=29,
        dark_beads=300, light_beads=541, palette_title="MARD 221",
        dark_code="H7", light_code="H1", physical_size_mm=145,
        payload_sha256="test-sha", output_dir=tmp_path,
    )
    monkeypatch.setattr(cli, "convert_qr_to_blueprint", lambda *args: result)
    output = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", output)
    monkeypatch.setattr(cli.sys, "stderr", io.StringIO())

    assert cli.main([str(tmp_path / "qr.png")]) == 0
    text = output.getvalue()
    assert ("已降为" in text) == (level in {"Q", "M", "L"})
    if level == "source":
        assert "沿用平台原码" in text
        assert "source 级" not in text
