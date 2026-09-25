"""Coverage for admit_preserved_raster_preview's N-image-slot generalization.

Previously hard-restricted to exactly one embedded crop (full_page_preserve).
A redesign page can carry N crops (screenshot/logo/table captures), so the
raster measurement-gate size fix (raster_measure_via_materialized_admission)
routes those pages through this same authorized-materialized admission --
the export worker's path for expanded base64, with a much larger wire/decode
budget than the 2MB canonical-SVG-contract limit. This must therefore accept
more than one slot.
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from services.svg_renderer import admit_preserved_raster_preview


def _tiny_png_data_url() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(10, 20, 30)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _canonical_svg(slots: list[str]) -> str:
    images = "".join(
        f'<image x="{i * 40}" y="0" width="40" height="50" href="{slot}" preserveAspectRatio="xMidYMid meet"/>'
        for i, slot in enumerate(slots)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{40 * len(slots)}" height="50" '
        f'viewBox="0 0 {40 * len(slots)} 50">{images}</svg>'
    )


def _materialized_svg(canonical: str, slots: list[str]) -> str:
    result = canonical
    for slot in slots:
        result = result.replace(f'href="{slot}"', f'href="{_tiny_png_data_url()}"')
    return result


def test_admits_two_embedded_crops():
    slots = ["source-slot-raster-p1-aaaaaaaaaaaaaaaa", "source-slot-raster-p1-bbbbbbbbbbbbbbbb"]
    canonical = _canonical_svg(slots)
    materialized = _materialized_svg(canonical, slots)
    admitted, wrapper = admit_preserved_raster_preview(canonical, materialized, width=80, height=50)
    assert admitted.html
    assert "svg-0" in wrapper or "data-qid" in wrapper


def test_admits_single_embedded_crop_full_page_preserve_shape():
    """The pre-existing one-slot caller (full_page_preserve) keeps working."""
    slots = ["source-slot-raster-p1-cccccccccccccccc"]
    canonical = _canonical_svg(slots)
    materialized = _materialized_svg(canonical, slots)
    admitted, _ = admit_preserved_raster_preview(canonical, materialized, width=40, height=50)
    assert admitted.html


def test_rejects_zero_image_slots():
    canonical = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"></svg>'
    with pytest.raises(ValueError, match="raster_preview_image_slots_invalid"):
        admit_preserved_raster_preview(canonical, canonical, width=10, height=10)


def _redesign_shaped_svg(slot: str) -> str:
    """A redesign page shape: one embedded crop plus real Noto Sans KR text,
    unlike full_page_preserve's image-only pages."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="50" viewBox="0 0 120 50">'
        '<text x="0" y="12" font-family="Noto Sans KR" font-size="12" fill="#081D49">제목입니다</text>'
        f'<image x="0" y="15" width="40" height="35" href="{slot}" preserveAspectRatio="xMidYMid meet"/>'
        '</svg>'
    )


def test_redesign_page_gets_embedded_trusted_font():
    """The bug this closes: font_unresolved was nondeterministic because this
    path never embedded @font-face, unlike the normal svg_preview_html path
    (materialize_trusted_svg_fonts) -- Chrome fell back to whatever "Noto Sans
    KR"-ish font the OS happened to have. The admitted HTML must now carry the
    packaged face inline, with CSP relaxed to allow it (font-src data:)."""
    slot = "source-slot-raster-p1-dddddddddddddddd"
    canonical = _redesign_shaped_svg(slot)
    materialized = canonical.replace(f'href="{slot}"', f'href="{_tiny_png_data_url()}"')
    admitted, _ = admit_preserved_raster_preview(canonical, materialized, width=120, height=50)
    assert "@font-face" in admitted.html
    assert "font-family:'Noto Sans KR'" in admitted.html
    assert "data:font/ttf;base64," in admitted.html
    assert "font-src data:" in admitted.html
    assert "font-src &#x27;none&#x27;" not in admitted.html


def test_full_page_preserve_shape_gets_no_font_embedding():
    """Image-only pages (no real text) stay a byte-for-byte no-op: nothing to
    render text with, so nothing should be embedded."""
    slots = ["source-slot-raster-p1-eeeeeeeeeeeeeeee"]
    canonical = _canonical_svg(slots)
    materialized = _materialized_svg(canonical, slots)
    admitted, _ = admit_preserved_raster_preview(canonical, materialized, width=40, height=50)
    assert "@font-face" not in admitted.html
    assert "font-src &#x27;none&#x27;" in admitted.html
