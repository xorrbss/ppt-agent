"""The page layout OCR pass never interprets slide text as vertical text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from services.source_content import raster_redesign_ocr as ocr


@pytest.mark.parametrize("psm, expected", [(ocr.LAYOUT_PSM, True), (11, False)])
def test_layout_pass_disables_vertical_text_detection(monkeypatch, psm, expected):
    commands: list[list[str]] = []

    def fake_run(command, **_):
        commands.append(command)
        return SimpleNamespace(returncode=1)  # stop before reading any output

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="local_ocr_failed"):
        ocr._run_tesseract(
            Image.new("RGB", (40, 20), "white"),
            executable=Path("tesseract"),
            tessdata=Path("tessdata"),
            psm=psm,
            variant="luma",
            timeout_seconds=5,
        )
    command = commands[0]
    flags = [command[i + 1] for i, part in enumerate(command[:-1]) if part == "-c"]
    assert ("textord_tabfind_vertical_text=0" in flags) is expected
    assert command[command.index("--psm") + 1] == str(psm)
