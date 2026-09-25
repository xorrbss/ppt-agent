"""Redesign pages through the product browser gate (render_svg_with_metrics).

Mirrors svg_presentation_service for raster pages: materialized SVG with raster
data URLs, required_text from the page's title/body regions (minus regions a
dense-group pixel fallback carries), the A4 landscape document canvas, and the
packaged template font profile.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re

import pytest
from PIL import Image

import test_raster_redesign_layout_fixtures as fx
from services.document_canvas import document_canvas_for_orientation
from services.source_content.raster_redesign_layout import render_redesign_page
from services.svg_renderer import render_svg_with_metrics
from utils.slide_capture import find_chrome
from utils.svg_template_fonts import trusted_font_marker

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")


def _materialize(svg: str) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (60, 110, 200)).save(buffer, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    return re.sub(r'href="source-slot-[^"]+"', f'href="{url}"', svg)


def _gate(analysis, number, *, template_font=True):
    result = render_redesign_page(analysis, number)
    replaced = {rid for advisory in result.advisories for rid in advisory["replacedRegionIds"]}
    page = analysis.pages[number - 1]
    required = [r.text for r in page.regions if r.role in {"title", "body"} and r.text and r.text.strip()
                and r.region_id not in replaced]
    extra = {"source_font_profile": trusted_font_marker()} if template_font else {}
    rendered = asyncio.run(render_svg_with_metrics(
        result.svg, width=1188, height=840, required_text=required, materialized_svg=_materialize(result.svg),
        physical_canvas=document_canvas_for_orientation("landscape"), **extra,
    ))
    return rendered.quality


CASES = [("deck", number) for number in range(1, 6)] + [("semantic", number) for number in range(1, 4)]


@pytest.mark.parametrize(("deck", "number"), CASES)
def test_fixture_pages_pass_the_product_gate(deck, number):
    analysis = fx.deck() if deck == "deck" else fx.semantic_deck()
    quality = _gate(analysis, number)
    assert quality["passed"], quality["failures"]


@pytest.mark.parametrize(("deck", "number"), CASES)
def test_no_text_overlap_or_small_text_even_without_embedded_font(deck, number):
    analysis = fx.deck() if deck == "deck" else fx.semantic_deck()
    quality = _gate(analysis, number, template_font=False)
    kinds = {failure["kind"] for failure in quality["failures"]}
    assert not kinds & {"text_overlap", "text_off_canvas", "text_clipped", "small_body_text",
                        "required_text_missing", "measurement_incomplete"}, quality["failures"]
