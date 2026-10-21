"""SVG ``<image>`` must never let a renderer page reach outside itself.

``<image href>`` was admitted for raster crops. Every locator that could make
Chromium fetch something -- a network, protocol-relative, file or script URL,
a non-raster ``data:`` document, the ``xlink:href`` spelling, a CSS ``url()``,
or a presentation attribute pointing at an external document -- must be
refused at admission, before any CSP or request blocking is relied upon.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from services.svg_contract import validate_svg
from utils.renderer_isolation import (
    RendererPolicyError,
    admit_authored_html,
    admit_static_output_html,
)
from utils.renderer_markup_policy import SVG_PRESENTATION_TAGS, TAG_ATTRIBUTES

ADMISSIONS = (admit_authored_html, admit_static_output_html)


def _raster(fmt: str) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, fmt)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


PNG = _raster("PNG")
JPEG = _raster("JPEG")
WEBP = _raster("WEBP")
DATA_PNG = f"data:image/png;base64,{PNG}"


def _page(inner: str) -> str:
    return (
        '<html><head></head><body><svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 10 10">{inner}</svg></body></html>'
    )


def _image(href: str, extra: str = "") -> str:
    return f'<image x="0" y="0" width="2" height="2" href="{href}"{extra}/>'


REJECTED_HREFS = {
    "http": "http://evil.test/a.png",
    "https": "https://evil.test/a.png",
    "protocol_relative": "//evil.test/a.png",
    "relative_path": "/app_data/images/a.png",
    "file": "file:///C:/Windows/win.ini",
    "javascript": "javascript:alert(1)",
    "data_svg": "data:image/svg+xml;base64,PHN2Zy8+",
    "data_html": "data:text/html;base64,PGgxPg==",
    "data_png_label_svg_bytes": "data:image/png;base64,PHN2Zy8+",
    "data_png_not_base64": "data:image/png,%89PNG",
    "entity_hex_scheme": "&#x68;ttps://evil.test/a.png",
    "entity_named_colon": "https&colon;//evil.test/a.png",
    "entity_tab_in_scheme": "ht&#9;tps://evil.test/a.png",
    "leading_whitespace": "   https://evil.test/a.png",
    "raster_slot_reference": "source-slot-p1-r1",
}


@pytest.mark.parametrize("admit", ADMISSIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("href", REJECTED_HREFS.values(), ids=REJECTED_HREFS.keys())
def test_non_raster_image_href_is_refused(admit, href):
    with pytest.raises(RendererPolicyError):
        admit(_page(_image(href)))


REJECTED_IMAGES = {
    "xlink_href": _image(DATA_PNG, ' xlink:href="https://evil.test/a.png"'),
    "xlink_href_upper": _image(DATA_PNG, ' XLINK:HREF="https://evil.test/a.png"'),
    "duplicate_href": _image(DATA_PNG, ' HREF="https://evil.test/a.png"'),
    "style_url": _image(DATA_PNG, ' style="mask-image:url(https://evil.test/m.svg)"'),
    "style_url_css_escape": _image(
        DATA_PNG, ' style="mask-image:u\\72l(https://evil.test/m.svg)"'
    ),
    "style_url_entity": _image(
        DATA_PNG, ' style="mask-image:&#x75;rl(https://evil.test/m.svg)"'
    ),
    "clip_path_external": _image(DATA_PNG, ' clip-path="url(https://evil.test/c.svg#c)"'),
    "clip_path_protocol_relative": _image(DATA_PNG, ' clip-path="url(//evil.test/c.svg#c)"'),
    "clip_path_relative_document": _image(DATA_PNG, ' clip-path="url(c.svg#c)"'),
    "clip_path_entity": _image(
        DATA_PNG, ' clip-path="url(&#x68;ttps://evil.test/c.svg#c)"'
    ),
    "mask_external": _image(DATA_PNG, ' mask="url(https://evil.test/m.svg#m)"'),
    "fill_external": _image(DATA_PNG, ' fill="url(https://evil.test/g.svg#g)"'),
    "stroke_external": _image(DATA_PNG, ' stroke="url(file:///c/g.svg#g)"'),
    "opacity_not_number": _image(DATA_PNG, ' opacity="url(https://evil.test/x)"'),
    "image_outside_svg": f'</svg><image href="{DATA_PNG}"></image><svg>',
    "foreign_object_wrapper": f'<foreignObject><img src="{DATA_PNG}"></foreignObject>',
    "use_reference": '<use href="https://evil.test/s.svg#s"/>',
}


@pytest.mark.parametrize("admit", ADMISSIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("inner", REJECTED_IMAGES.values(), ids=REJECTED_IMAGES.keys())
def test_external_reference_on_svg_image_is_refused(admit, inner):
    with pytest.raises(RendererPolicyError):
        admit(_page(inner))


ADMITTED_IMAGES = {
    "png": _image(DATA_PNG),
    "jpeg": _image(f"data:image/jpeg;base64,{JPEG}"),
    "webp": _image(f"data:image/webp;base64,{WEBP}"),
    "local_clip_path": (
        '<defs><clipPath id="c"><circle cx="1" cy="1" r="1"/></clipPath></defs>'
        + _image(DATA_PNG, ' clip-path="url(#c)" opacity="0.5" transform="translate(1 1)"')
    ),
    "solid_paint": _image(DATA_PNG, ' fill="#ffffff" stroke="none"'),
}


@pytest.mark.parametrize("admit", ADMISSIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("inner", ADMITTED_IMAGES.values(), ids=ADMITTED_IMAGES.keys())
def test_embedded_raster_and_local_references_are_admitted(admit, inner):
    admit(_page(inner))


def test_every_presentation_bearing_tag_gets_the_value_checks():
    assert "image" in SVG_PRESENTATION_TAGS
    for tag in SVG_PRESENTATION_TAGS:
        assert {"fill", "stroke", "clip-path", "mask", "opacity"} <= TAG_ATTRIBUTES[tag]
    for tag, attributes in TAG_ATTRIBUTES.items():
        if attributes & {"fill", "stroke", "clip-path", "mask"}:
            assert tag in SVG_PRESENTATION_TAGS, tag


def _canvas_svg(inner: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
        f'viewBox="0 0 1280 720"><text x="10" y="20" font-family="Noto Sans KR" '
        f'font-size="12" fill="#000000">t</text>{inner}</svg>'
    )


SLOT = "source-slot-p1-r1"
MANIFEST = {SLOT: {"required": True, "allowOmit": False}}


def _contract_image(href: str, extra: str = "") -> str:
    return (
        f'<image x="0" y="0" width="2" height="2" href="{href}" '
        f'preserveAspectRatio="xMidYMid meet"{extra}/>'
    )


def test_svg_contract_admits_only_manifest_slots_for_canonical_pages():
    validate_svg(_canvas_svg(_contract_image(SLOT)), source_manifest=MANIFEST)
    for href in ("https://evil.test/a.png", "//evil.test/a.png", DATA_PNG,
                 "source-slot-unknown", "file:///c/a.png"):
        with pytest.raises(ValueError):
            validate_svg(_canvas_svg(_contract_image(href)), source_manifest=MANIFEST)


@pytest.mark.parametrize("href", [
    "https://evil.test/a.png", "//evil.test/a.png", "javascript:alert(1)",
    "data:image/svg+xml;base64,PHN2Zy8+", f"data:image/webp;base64,{WEBP}",
    SLOT,
])
def test_svg_contract_materialized_pages_require_png_or_jpeg_bytes(href):
    validate_svg(_canvas_svg(_contract_image(DATA_PNG)))
    with pytest.raises(ValueError):
        validate_svg(_canvas_svg(_contract_image(href)))


@pytest.mark.parametrize("extra", [
    ' xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://evil.test/a.png"',
    ' clip-path="url(https://evil.test/c.svg#c)"',
    ' mask="url(https://evil.test/m.svg#m)"',
    ' fill="url(https://evil.test/g.svg#g)"',
    ' style="mask-image:url(https://evil.test/m.svg)"',
])
def test_svg_contract_refuses_external_references_beside_the_slot(extra):
    with pytest.raises(ValueError):
        validate_svg(_canvas_svg(_contract_image(SLOT, extra)), source_manifest=MANIFEST)


@pytest.mark.parametrize("admit", ADMISSIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("element", ["text", "tspan"])
@pytest.mark.parametrize("value", ["default", "preserve"])
def test_xml_space_survives_svg_preview_and_isolation_preflight(admit, element, value):
    import re
    import xml.etree.ElementTree as ET
    from services.svg_renderer import svg_preview_html

    root = ET.fromstring(_canvas_svg(_contract_image(SLOT)))
    text = root.find("{http://www.w3.org/2000/svg}text")
    text.text = "  원문  A "
    span = ET.SubElement(text, "{http://www.w3.org/2000/svg}tspan")
    span.text, span.tail = " B  C ", "  끝  "
    (text if element == "text" else span).set("{http://www.w3.org/XML/1998/namespace}space", value)
    canonical = ET.tostring(root, encoding="unicode")
    materialized = canonical.replace(f'href="{SLOT}"', f'href="{DATA_PNG}"')
    wrapper = svg_preview_html(canonical, materialized_svg=materialized)
    admitted = admit(wrapper)
    match = re.search(r"<svg\b.*?</svg>", admitted.html, re.DOTALL)
    measured = ET.fromstring(match.group(0))
    measured_text = measured.find("{http://www.w3.org/2000/svg}text")
    assert "".join(measured_text.itertext()) == "  원문  A  B  C   끝  "
    target = measured_text if element == "text" else measured_text[0]
    assert target.get("{http://www.w3.org/XML/1998/namespace}space") == value
    assert measured.find("{http://www.w3.org/2000/svg}image").get("href") == DATA_PNG


@pytest.mark.parametrize("admit", ADMISSIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("inner", [
    '<text xml:space="invalid">t</text>',
    '<text xml:space=" preserve">t</text>',
    '<text xml:space="">t</text>',
    '<text xml:base="https://evil.test/">t</text>',
    '<text xmlns:other="urn:untrusted" other:space="preserve">t</text>',
    '<text xml:space="preserve" onclick="alert(1)">t</text>',
    '<text xml:space="preserve" style="fill:url(https://evil.test/f)">t</text>',
    '<g xml:space="preserve"><text>t</text></g>',
    '<rect xml:space="preserve"/>',
])
def test_xml_space_keeps_isolation_value_namespace_and_active_content_guards(admit, inner):
    with pytest.raises(RendererPolicyError):
        admit(_page(inner))
