"""Deck acceptance checks and WebP rasterisation for the sample-deck generator.

Acceptance (plan §2): exactly 12 pages, every page 16:9 within tolerance, and
every order-guard keyword present on its assigned page's extracted text.
Rasterisation renders each accepted page with pypdfium2 and re-encodes it as
WebP at a quality that fits the design's byte caps (§4/D8), stepping down
from quality 88 by 4 until it fits or falls below quality 60.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

import pypdfium2
from PIL import Image

#: 16:9 at the sample canvas's own precision (docs use "1.778 ± 0.01").
ASPECT_RATIO_16_9 = 1280 / 720
ASPECT_RATIO_TOLERANCE = 0.01

THUMB_LONG_EDGE = 320
STAGE_BYTE_CAP = 96 * 1024
THUMB_BYTE_CAP = 14 * 1024
START_QUALITY = 88
QUALITY_STEP = 4
MIN_QUALITY = 60


@dataclass(frozen=True)
class PdfPageInfo:
    index: int  # 1-based slide number
    width: float
    height: float
    text: str


@dataclass(frozen=True)
class RasterResult:
    stage_bytes: bytes
    thumb_bytes: bytes
    stage_quality: int
    thumb_quality: int


def read_pdf_pages(pdf_path: Path) -> list[PdfPageInfo]:
    """Read every page's size (PDF points) and extracted text via pypdfium2."""
    pages: list[PdfPageInfo] = []
    document = pypdfium2.PdfDocument(str(pdf_path))
    try:
        for i in range(len(document)):
            page = document[i]
            try:
                width, height = page.get_size()
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
                pages.append(PdfPageInfo(index=i + 1, width=width, height=height, text=text))
            finally:
                page.close()
    finally:
        document.close()
    return pages


def check_deck_acceptance(
    pages: Sequence[PdfPageInfo],
    expected_page_count: int,
    guard_keywords: Mapping[int, Sequence[str]],
) -> list[str]:
    """Return acceptance problems (plan §2); an empty list means the deck is accepted."""
    problems: list[str] = []
    if len(pages) != expected_page_count:
        problems.append(f"expected {expected_page_count} pages, got {len(pages)}")

    for page in pages:
        if page.height <= 0:
            problems.append(f"slide {page.index}: non-positive page height")
            continue
        ratio = page.width / page.height
        if abs(ratio - ASPECT_RATIO_16_9) > ASPECT_RATIO_TOLERANCE:
            problems.append(f"slide {page.index}: aspect ratio {ratio:.4f} is not 16:9 (1.778 +/- 0.01)")

    pages_by_index = {page.index: page for page in pages}
    for slide_no, keywords in guard_keywords.items():
        page = pages_by_index.get(slide_no)
        if page is None:
            problems.append(f"slide {slide_no}: order-guard slide is missing from the deck")
            continue
        for keyword in keywords:
            if keyword not in page.text:
                problems.append(f"slide {slide_no}: order-guard keyword '{keyword}' not found in extracted text")
    return problems


def thumb_dimensions(width: int, height: int, long_edge: int = THUMB_LONG_EDGE) -> Tuple[int, int]:
    """Scale ``(width, height)`` so its long edge is ``long_edge`` (rounded)."""
    if width >= height:
        return long_edge, round(height * long_edge / width)
    return round(width * long_edge / height), long_edge


def encode_webp_within_cap(image: Image.Image, byte_cap: int) -> Tuple[Optional[bytes], int]:
    """Encode ``image`` as WebP, stepping quality down from 88 by 4 until it fits.

    Returns ``(payload, quality)`` on success, or ``(None, last_quality_tried)``
    when the image still exceeds ``byte_cap`` at quality 60.
    """
    quality = START_QUALITY
    last_quality = quality
    while quality >= MIN_QUALITY:
        buffer = BytesIO()
        image.save(buffer, format="WEBP", quality=quality)
        payload = buffer.getvalue()
        last_quality = quality
        if len(payload) <= byte_cap:
            return payload, quality
        quality -= QUALITY_STEP
    return None, last_quality


def render_page_to_image(pdf_path: Path, page_index: int, canvas_width: int) -> Image.Image:
    """Render page ``page_index`` (1-based) at ``scale = 2 * canvas_width / page_width_pt``."""
    document = pypdfium2.PdfDocument(str(pdf_path))
    try:
        page = document[page_index - 1]
        try:
            width_pt, _height_pt = page.get_size()
            scale = 2 * canvas_width / width_pt
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def rasterize_page(pdf_path: Path, page_index: int, canvas: Tuple[int, int]) -> Optional[RasterResult]:
    """Render, downsample (LANCZOS) and WebP-encode one page's stage and thumb images.

    Returns ``None`` if either image cannot fit its byte cap even at quality 60.
    """
    canvas_width, canvas_height = canvas
    rendered = render_page_to_image(pdf_path, page_index, canvas_width)
    stage_image = rendered.resize((canvas_width, canvas_height), Image.LANCZOS)
    thumb_w, thumb_h = thumb_dimensions(canvas_width, canvas_height)
    thumb_image = rendered.resize((thumb_w, thumb_h), Image.LANCZOS)

    stage_bytes, stage_quality = encode_webp_within_cap(stage_image, STAGE_BYTE_CAP)
    thumb_bytes, thumb_quality = encode_webp_within_cap(thumb_image, THUMB_BYTE_CAP)
    if stage_bytes is None or thumb_bytes is None:
        return None
    return RasterResult(
        stage_bytes=stage_bytes,
        thumb_bytes=thumb_bytes,
        stage_quality=stage_quality,
        thumb_quality=thumb_quality,
    )
