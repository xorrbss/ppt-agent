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
import asyncio
import io
import json
import re
import inspect
import subprocess
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from services.svg_renderer import admit_preserved_raster_preview
from tests.support.windows_symbol_font import requires_windows_symbol_font


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


def _symbol_svg() -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120" viewBox="0 0 400 120">'
            '<text id="fact-a" x="10" y="35" font-family="Noto Sans KR" font-weight="700" '
            'font-size="24" fill="#081D49" xml:space="preserve">A  \u21bb<tspan id="fact-b" font-weight="800">\u25b8 B</tspan> \u25be \u2610</text></svg>')


def _html_svg(html: str):
    from services.svg_renderer import _svg_fragment
    return ET.fromstring(_svg_fragment(html).group(1))


def _measurement_identity(root):
    return [(n.get("id"), n.get("data-qid")) for n in root.iter()
            if n.get("id") is not None or n.get("data-qid") is not None]


@requires_windows_symbol_font
def test_template_symbols_materialize_only_after_worker_admission():
    from services.svg_renderer import svg_preview_html
    from utils.svg_template_fonts import template_font_faces, trusted_font_marker
    from workers.public_svg_pdf_export import admit_slide_wrapper_html

    canonical = _symbol_svg()
    wrapper = svg_preview_html(canonical, width=400, height=120, source_font_profile=trusted_font_marker())
    before = _html_svg(wrapper)
    admitted = admit_slide_wrapper_html(wrapper)
    after = _html_svg(admitted.html)
    assert _symbol_svg() == canonical
    assert "Segoe UI Symbol" not in wrapper
    assert "presenton-template-fallback:" in wrapper
    assert _measurement_identity(before) == _measurement_identity(after)
    assert "".join(before.itertext()) == "".join(after.itertext())
    added = [n for n in after.iter() if n.get("font-family") == "Segoe UI Symbol"]
    assert added and all(set(n.attrib) == {"font-family", "font-weight"} for n in added)
    assert {n.get("font-weight") for n in added} == {"700", "800"}
    assert all(n.get("data-qid") is None and n.get("id") is None for n in added)
    faces = template_font_faces(canonical)
    assert len(faces) == 2
    for family, weight, digest, payload in faces:
        assert digest in admitted.asset_digests
        assert base64.b64encode(payload).decode() in admitted.html
        assert f"font-family:'{family}';font-style:normal;font-weight:{weight};" in admitted.html


@requires_windows_symbol_font
def test_template_fallback_binding_and_author_family_fail_closed():
    from services.svg_renderer import svg_preview_html
    from utils.svg_template_fonts import trusted_font_marker
    from workers.public_svg_pdf_export import admit_slide_wrapper_html

    wrapper = svg_preview_html(_symbol_svg(), width=400, height=120, source_font_profile=trusted_font_marker())
    forged = re.sub(r"presenton-template-fallback:[a-f0-9]{64}", "presenton-template-fallback:" + "0" * 64, wrapper)
    with pytest.raises(Exception, match="public_render_trusted_font_invalid"):
        admit_slide_wrapper_html(forged)
    for unsafe in (_symbol_svg().replace('font-weight="800"', 'font-weight="800" font-family="Segoe UI Symbol"'),
                   _symbol_svg().replace("\u21bb", "\U0010ffff")):
        with pytest.raises(ValueError):
            svg_preview_html(unsafe, width=400, height=120, source_font_profile=trusted_font_marker())


@requires_windows_symbol_font
def test_template_no_fallback_keeps_one_face_and_raster_symbols_keep_measurement_owners():
    from services.svg_renderer import svg_preview_html
    from utils.svg_template_fonts import trusted_font_marker
    from workers.public_svg_pdf_export import admit_slide_wrapper_html

    plain = _symbol_svg().replace("\u21bb", "A").replace("\u25b8", "B").replace("\u25be", "C").replace("\u2610", "D")
    wrapper = svg_preview_html(plain, width=400, height=120, source_font_profile=trusted_font_marker())
    assert "presenton-template-fallback:" not in wrapper
    assert admit_slide_wrapper_html(wrapper).html.count("@font-face") == 1
    slot = "source-slot-raster-p1-ffffffffffffffff"
    canonical = _symbol_svg().replace('</svg>', f'<image x="200" y="60" width="40" height="40" href="{slot}" preserveAspectRatio="xMidYMid meet"/></svg>')
    resolved = canonical.replace(slot, _tiny_png_data_url())
    admitted, raster_wrapper = admit_preserved_raster_preview(canonical, resolved, width=400, height=120)
    assert _measurement_identity(_html_svg(raster_wrapper)) == _measurement_identity(_html_svg(admitted.html))
    assert "Segoe UI Symbol" in admitted.html


@requires_windows_symbol_font
def test_template_quality_proves_canonical_and_measured_fallback_through_metric_codec(monkeypatch):
    from services.svg_renderer import render_svg_with_metrics
    from utils.hashing import sha256_prefixed
    from utils.slide_capture_result import RenderCapture
    from utils.svg_template_fonts import trusted_font_marker
    from workers.public_svg_pdf_export import admit_slide_wrapper_html

    async def capture(html, **kwargs):
        admitted = admit_slide_wrapper_html(html)
        assert "Segoe UI Symbol" in admitted.html
        return RenderCapture.from_payload(html_digest=sha256_prefixed(html.encode()),
            png=base64.b64decode(_tiny_png_data_url().split(',')[1]), runtime_counters={},
            browser_metrics={"fonts_ready": True, "metrics_cache_key": "a\x00b", "elements": [{
                "qid": "svg-1", "has_owned_text": True, "owned_text": "".join(ET.fromstring(_symbol_svg()).itertext()),
                "font_size": 24, "font_loaded": True, "bbox": {"x": 10, "y": 10, "width": 200, "height": 30}}]})

    monkeypatch.setattr("utils.slide_capture.render_html_to_png_with_metrics", capture)
    result = asyncio.run(render_svg_with_metrics(_symbol_svg(), width=400, height=120, minimum_font_px=12,
                                               source_font_profile=trusted_font_marker()))
    profile = result.quality["trusted_font_materialization"]
    proof, measured = profile["fallback"], profile["render_fallback"]
    assert proof["canonical_svg_digest"] == sha256_prefixed(_symbol_svg().encode())
    assert proof["canonical_svg_digest"] != measured["canonical_svg_digest"]
    assert proof["runs"] == measured["runs"]
    assert {cp for run in proof["runs"] for cp in run["codepoints"]} == {0x21bb, 0x25b8, 0x25be, 0x2610}
    assert all(run["synthetic_bold"] and run["face_weight"] == 400 for run in proof["runs"])
    assert json.loads(json.dumps(result.quality, ensure_ascii=False))["trusted_font_materialization"] == profile
    assert result.quality["browser_metrics"]["metrics_cache_key"] == "a\u241fb"
    assert result.quality["passed"] is True


@requires_windows_symbol_font
@pytest.mark.parametrize("case,missing", [
    ("ordered", False), ("missing_glyph", True), ("unmeasured_owner", True),
    ("legacy_reordered", True), ("legacy_complete", False),
    ("hidden", False), ("clipped", False), ("font_unresolved", False),
])
def test_required_literal_uses_document_order_only_for_measured_owners(monkeypatch, case, missing):
    from services.svg_renderer import render_svg_with_metrics
    from utils.hashing import sha256_prefixed
    from utils.slide_capture_result import RenderCapture
    from utils.svg_template_fonts import trusted_font_marker

    literal = "".join(ET.fromstring(_symbol_svg()).itertext())
    chunks = [("svg-1", "A  "), ("fallback-a", "\u21bb"), ("fallback-b", "\u25b8"),
              ("svg-2", " B"), ("svg-1", " "), ("fallback-c", "\u25be"),
              ("svg-1", " "), ("fallback-d", "\u2610")]
    # DOM element order visits the existing parent tspan before its fallback child.
    owners = ["svg-1", "fallback-a", "svg-2", "fallback-b", "fallback-c", "fallback-d"]
    elements = [{"qid": qid, "has_owned_text": True,
        "owned_text": "".join(value for owner, value in chunks if owner == qid),
        "font_size": 24, "font_loaded": True,
        "bbox": {"x": 10 + index * 50, "y": 10, "width": 20, "height": 25}}
        for index, qid in enumerate(owners)]
    metrics = {"fonts_ready": True, "elements": elements}
    if case == "legacy_complete":
        elements[0]["owned_text"] = literal
        for item in elements[1:]:
            item["owned_text"] = ""
    elif case != "legacy_reordered":
        metrics["ordered_owned_text"] = [{"qid": qid, "text": value} for qid, value in chunks]
        if case == "missing_glyph":
            metrics["ordered_owned_text"] = [item for item in metrics["ordered_owned_text"]
                                             if item["qid"] != "fallback-b"]
        if case == "unmeasured_owner":
            next(item for item in elements if item["qid"] == "fallback-b")["has_owned_text"] = False
        if case == "hidden":
            elements[0]["css_visible"] = False
        if case == "clipped":
            elements[0]["glyph_clipped"] = True
        if case == "font_unresolved":
            elements[0]["font_loaded"] = False

    async def capture(html, **kwargs):
        return RenderCapture.from_payload(html_digest=sha256_prefixed(html.encode()), png=b"png",
                                          browser_metrics=metrics, runtime_counters={})

    monkeypatch.setattr("utils.slide_capture.render_html_to_png_with_metrics", capture)
    result = asyncio.run(render_svg_with_metrics(_symbol_svg(), width=400, height=120, minimum_font_px=12,
        required_text=[literal], source_font_profile=trusted_font_marker()))
    actual = [failure for failure in result.quality["failures"] if failure["kind"] == "required_text_missing"]
    assert bool(actual) is missing
    if missing:
        assert actual[0]["count"] == 1
    guard = {"hidden": "text_hidden", "clipped": "text_clipped", "font_unresolved": "font_unresolved"}.get(case)
    if guard:
        assert any(failure["kind"] == guard for failure in result.quality["failures"])


def test_ordered_text_probe_walks_each_text_once_with_closest_owner():
    from utils.slide_capture import _instrument_authored_html, MEASUREMENT_CACHE_VERSION, DOM_METRICS_VERSION

    source = inspect.getsource(_instrument_authored_html)
    collector = re.search(r"const orderedOwnedText = \(\) => \{\{.*?\n    \}\};", source, re.S).group()
    collector = collector.replace("{{", "{").replace("}}", "}")
    script = '''
const nodes = [['parent', 'A '], ['child', '\u25b8'], ['parent', ' B'], [null, 'ignored']].map(([qid, value]) => ({
 textContent: value, parentElement: {closest: selector => qid ? {getAttribute: key => qid} : null}
}));
const NodeFilter = {SHOW_TEXT: 4};
const isTrustedFontResourceText = () => false;
let visits = 0;
const document = {body: {}, createTreeWalker: (root, mode) => {
 if (root !== document.body || mode !== NodeFilter.SHOW_TEXT) throw Error('scope');
 let i=0; return {nextNode: () => { visits++; return nodes[i++] || null; }};
}};
''' + collector + '''
const result = orderedOwnedText();
console.log(JSON.stringify({result, visits}));
'''
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True,
                               encoding="utf-8")
    evidence = json.loads(completed.stdout)
    assert evidence == {"result": [{"qid": "parent", "text": "A "}, {"qid": "child", "text": "\u25b8"},
                                   {"qid": "parent", "text": " B"}], "visits": 5}
    assert "ordered_owned_text: orderedOwnedText()" in source
    assert MEASUREMENT_CACHE_VERSION == "authored-measurement/v19-exclude-trusted-font-resource-text"
    assert DOM_METRICS_VERSION == "authored-dom-metrics-v31-exclude-trusted-font-resource-text"
