"""Environment-gated, real Tesseract smoke coverage for authored OCR."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from utils.authored_ocr_quality import (
    AuthoredOcrCache,
    inspect_authored_ocr,
    inspect_tesseract,
)

_TEXT = "\uac00\uaca9 2026 +12%"


def _korean_font() -> ImageFont.FreeTypeFont:
    candidates = (
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=48)
    pytest.skip("korean_smoke_font_not_found")


def _image_bytes() -> bytes:
    image = Image.new("RGB", (900, 180), "white")
    ImageDraw.Draw(image).text((36, 56), _TEXT, fill="black", font=_korean_font())
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_authored_ocr_real_tesseract_kor_eng_smoke() -> None:
    """Use the local binary only; missing binary/language data is an explicit skip."""

    runtime = inspect_tesseract(
        explicit_command=None,
        tessdata_dir=None,
        language="kor+eng",
        timeout_seconds=5,
    )
    if runtime.status != "ready":
        pytest.skip(
            f"tesseract_kor_eng_unavailable:{runtime.failure_reason or runtime.status}"
        )

    result = asyncio.run(
        inspect_authored_ocr(
            _image_bytes(),
            expected_text=_TEXT,
            language="kor+eng",
            cache=AuthoredOcrCache(),
        )
    )
    payload = result.to_dict()
    if not payload["available"]:
        pytest.skip(
            "tesseract_execution_unavailable:"
            f"{payload['failure_reason'] or payload['status']}"
        )

    assert payload["status"] == "success"
    assert payload["calls"] == 1
    assert payload["cache_hits"] == 0
    metrics = payload["metrics"]
    assert metrics["available"] is True
    # A zero exit code only proves that the subprocess ran.  Keep the live smoke
    # useful by also requiring the installed kor+eng data to recognize the
    # representative Korean, numeric, and symbol content with no material loss.
    assert metrics["cer"] <= 0.10
    for category in ("korean", "digits", "symbols"):
        assert metrics["character_classes"][category]["missing_or_substituted"] == 0
    assert {"kor", "eng"}.issubset(runtime.available_languages)
