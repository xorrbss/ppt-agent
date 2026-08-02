"""Run the native authored OCR contract inside a production Docker image.

This is deliberately dependency-light: it uses runtime Pillow plus the authored
OCR utility, writes no image to disk, and never prints recognized OCR text.
Run it from ``/app/servers/fastapi`` after the image is built.
"""

from __future__ import annotations

import asyncio
import os
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from utils.authored_ocr_quality import (
    AuthoredOcrCache,
    inspect_authored_ocr,
    inspect_tesseract,
)

TEXT = "가격 2026 +12%"
FONT_CANDIDATES = (
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path(r"C:\Windows\Fonts\malgun.ttf"),
)


def _font() -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=48)
    raise RuntimeError("korean_smoke_font_not_found")


def _image() -> bytes:
    image = Image.new("RGB", (900, 180), "white")
    ImageDraw.Draw(image).text((36, 56), TEXT, fill="black", font=_font())
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _fail(reason: str) -> int:
    print(f"DOCKER_OCR_SMOKE=failed reason={reason}")
    return 1


def main() -> int:
    # Dockerfiles set this already; setting the documented default here makes
    # accidental environment changes visible in the emitted runtime metadata.
    os.environ.setdefault("AUTHORED_OCR_TESSDATA_PATH", "/app/tessdata")
    runtime = inspect_tesseract(
        explicit_command=None,
        tessdata_dir=None,
        language="kor+eng",
        timeout_seconds=5,
    )
    if runtime.status != "ready":
        return _fail(runtime.failure_reason or runtime.status)
    try:
        result = asyncio.run(
            inspect_authored_ocr(
                _image(),
                expected_text=TEXT,
                language="kor+eng",
                cache=AuthoredOcrCache(),
            )
        )
    except RuntimeError as exc:
        return _fail(str(exc))
    if not result.available:
        return _fail(result.failure_reason or result.status)
    metrics = result.metrics
    classes = metrics.get("character_classes", {})
    omissions = {
        name: int(classes.get(name, {}).get("missing_or_substituted", -1))
        for name in ("korean", "digits", "symbols")
    }
    cer = float(metrics.get("cer", 1.0))
    if cer > 0.10 or any(value != 0 for value in omissions.values()):
        return _fail(f"accuracy_gate cer={cer:.4f} omissions={omissions}")
    print(
        "DOCKER_OCR_SMOKE=passed"
        f" version={runtime.version or 'unknown'}"
        f" languages={','.join(runtime.available_languages)}"
        f" tessdata={runtime.tessdata_dir or 'default'}"
        f" cer={cer:.4f} omissions={omissions}"
        f" elapsed_ms={result.elapsed_ms:.1f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
