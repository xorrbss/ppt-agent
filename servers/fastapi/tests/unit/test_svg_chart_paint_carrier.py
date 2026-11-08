"""Optional chart-proof transport; pixel evidence is tested by the probe suite."""
from __future__ import annotations

import asyncio
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from utils.renderer_isolation import AdmittedHtml, RendererPolicyError, encode_request


def _admitted(html="<html><body>fixture</body></html>"):
    from utils.renderer_worker_response import source_digest_for
    return AdmittedHtml(html=html, nonce="nonce", source_digest=source_digest_for(html),
                        asset_digests=(), node_count=2)


def _png(width=32, height=24):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def test_absent_wire_is_identical_and_present_manifest_is_not_null():
    html = "<html>한글</html>"
    expected = json.dumps({"schema_version": "authored-render-request/v1", "html": html,
                           "timeout": 12, "width_px": 32, "height_px": 24},
                          separators=(",", ":")).encode() + b"\n"
    assert encode_request(html, 12, width_px=32, height_px=24) == expected
    assert encode_request(html, 12, width_px=32, height_px=24,
                          chart_witness_manifest=None) == expected
    wire = json.loads(encode_request(html, 12, width_px=32, height_px=24,
                                     chart_witness_manifest={"fixture": 1}))
    assert wire["chart_witness_manifest"] == {"fixture": 1}


@pytest.mark.parametrize("bad", [None, [], "manifest", 3])
def test_worker_rejects_non_object_manifest(bad):
    from utils.authored_renderer_worker import _validate_request
    request = json.loads(encode_request("<html></html>", 12))
    request["chart_witness_manifest"] = bad
    with pytest.raises(RendererPolicyError, match="chart witness manifest"):
        _validate_request(request)


def test_worker_delegates_schema_without_widening_svg_authority(monkeypatch):
    from utils import authored_renderer_worker as worker, slide_capture_chart_paint as proof
    calls = []
    monkeypatch.setattr(proof, "validate_manifest", lambda manifest, **kw: calls.append((manifest, kw)))
    request = json.loads(encode_request("<html></html>", 12, width_px=32, height_px=24,
                                        chart_witness_manifest={"fixture": 1}))
    assert worker._validate_request(request) == request
    assert calls == [({"fixture": 1}, {"width_px": 32, "height_px": 24, "source_html": "<html></html>"})]
    request["canonical_svg"] = "<svg/>"
    with pytest.raises(RendererPolicyError, match="canonical SVG binding"):
        worker._validate_request(request)


def test_atomic_manifest_bypasses_every_cache_and_preserves_scope(monkeypatch):
    from utils import slide_capture as capture, slide_capture_chart_paint as proof
    from utils.slide_capture_deck_scope import current_render_deck_scope
    calls = []
    active = []

    class Gate:
        async def __aenter__(self):
            active.append(True)
        async def __aexit__(self, *args):
            active.pop()

    def forbidden(*args, **kwargs):
        pytest.fail("manifest capture entered a legacy cache or alias")

    async def isolated(html, timeout, **kwargs):
        assert active and current_render_deck_scope() == "chart-deck"
        calls.append(kwargs)
        return bytes([len(calls)]), {"nativeChartPaintProof": {"complete": False, "frame": len(calls)}}

    monkeypatch.setattr(proof, "validate_manifest", lambda *a, **kw: {})
    monkeypatch.setattr(capture, "ensure_renderer_enabled", lambda: None)
    monkeypatch.setattr(capture, "_bind_worker_client", lambda: None)
    monkeypatch.setattr(capture, "_get_render_sem", Gate)
    monkeypatch.setattr(capture, "_render_html_in_isolated_worker", isolated)
    for name in ["_peek_cached_capture", "_peek_cached_browser_metrics", "_cache_capture",
                 "_cache_browser_metrics", "render_html_to_png", "_render_html_to_png_uncached"]:
        monkeypatch.setattr(capture, name, forbidden)

    async def run():
        results = []
        for manifest in [{"fixture": 1}, {"fixture": 2}, {"fixture": 1}]:
            results.append(await capture.render_html_to_png_with_metrics(
                "<html></html>", deck_scope="chart-deck", width_px=32, height_px=24,
                chart_witness_manifest=manifest))
        return results

    results = asyncio.run(run())
    assert [result.png for result in results] == [b"\x01", b"\x02", b"\x03"]
    assert [call["chart_witness_manifest"] for call in calls] == [{"fixture": 1}, {"fixture": 2}, {"fixture": 1}]
    assert all(result.browser_metrics["nativeChartPaintProof"]["complete"] is False for result in results)
    assert current_render_deck_scope() is None


@pytest.mark.parametrize("fit,policy", [(True, None), (False, {"enabled": True})])
def test_manifest_cannot_silently_use_fit(monkeypatch, fit, policy):
    from utils import slide_capture as capture
    monkeypatch.setattr(capture, "ensure_renderer_enabled", lambda: None)
    with pytest.raises(ValueError, match="unchanged source"):
        asyncio.run(capture.render_html_to_png_with_metrics(
            "<html></html>", fit=fit, fit_policy=policy, chart_witness_manifest={"fixture": 1}))


def test_manifest_respects_existing_kill_switch(monkeypatch):
    from utils import slide_capture as capture
    def disabled():
        raise RendererPolicyError("disabled fixture")
    monkeypatch.setattr(capture, "ensure_renderer_enabled", disabled)
    with pytest.raises(RendererPolicyError, match="disabled fixture"):
        asyncio.run(capture.render_html_to_png_with_metrics("<html></html>", chart_witness_manifest={}))


def test_no_manifest_atomic_cache_path_retains_old_shape(monkeypatch):
    from utils import slide_capture as capture
    from utils.slide_capture_result import RenderCapture
    cached = RenderCapture.from_payload(html_digest="fixture", png=b"png", browser_metrics={"elements": []},
                                        runtime_counters={})
    monkeypatch.setattr(capture, "ensure_renderer_enabled", lambda: None)
    monkeypatch.setattr(capture, "_peek_cached_capture", lambda *a, **kw: cached)
    result = asyncio.run(capture.render_html_to_png_with_metrics("<html></html>"))
    assert result.png == cached.png and result.browser_metrics == cached.browser_metrics
    assert "nativeChartPaintProof" not in result.browser_metrics


def test_client_wrapper_omits_absent_keyword(monkeypatch):
    from utils import slide_capture_worker_client as client
    calls = []
    async def implementation(*args, **kwargs):
        calls.append(kwargs)
        return b"png", {}
    monkeypatch.setattr(client, "_render_html_in_isolated_worker_impl", implementation)
    asyncio.run(client._render_html_in_isolated_worker("<html></html>", 12))
    asyncio.run(client._render_html_in_isolated_worker("<html></html>", 12, chart_witness_manifest={"fixture": 1}))
    assert "chart_witness_manifest" not in calls[0]
    assert calls[1]["chart_witness_manifest"] == {"fixture": 1}


@pytest.mark.parametrize("manifest", [None, {"fixture": 1}])
def test_worker_finalizes_after_capabilities_and_omits_absent_keyword(monkeypatch, manifest):
    from utils import authored_renderer_worker as worker, slide_capture as capture
    from utils import slide_capture_chart_paint as proof
    from services import svg_renderer
    admitted = _admitted()
    events = []
    async def render(source, **kwargs):
        events.append(("render", kwargs))
        return b"png", {"elements": [], **({"nativeChartPaintProof": {"complete": False}} if manifest is not None else {})}
    def finalize(metrics, png, received, **kwargs):
        assert metrics["capabilityRegistry"] == {"fixture": True}
        assert kwargs["wrapper_html"] == admitted.html
        assert received == manifest and png == b"png"
        events.append(("finalize", {}))
        return {**metrics, "nativeChartPaintProof": {"complete": False, "bound": True}}
    monkeypatch.setattr(worker, "admit_authored_html", lambda source: admitted)
    monkeypatch.setattr(svg_renderer, "materialize_trusted_svg_fonts", lambda source, value: value)
    monkeypatch.setattr(capture, "_render_admitted_html_in_worker", render)
    monkeypatch.setattr(worker, "capability_metadata", lambda **kwargs: {"fixture": True})
    monkeypatch.setattr(proof, "finalize_chart_paint_proof", finalize)
    monkeypatch.setattr(proof, "validate_manifest", lambda *args, **kwargs: {})
    request = {"html": admitted.html, "timeout": 12}
    if manifest is not None:
        request["chart_witness_manifest"] = manifest
    result = asyncio.run(worker._render(request))
    kwargs = events[0][1]
    if manifest is None:
        assert "chart_witness_manifest" not in kwargs
        assert "nativeChartPaintProof" not in result["metrics"]
        assert len(events) == 1
    else:
        assert kwargs["chart_witness_manifest"] == manifest
        assert result["metrics"]["nativeChartPaintProof"] == {"complete": False, "bound": True}
        assert [event[0] for event in events] == ["render", "finalize"]


@pytest.mark.parametrize("mismatch", [None, "dimensions", "dpr"])
def test_capture_checks_raw_frame_and_collects_before_glyph_navigation(monkeypatch, mismatch):
    from utils import slide_capture_chart_adapter as pipe, slide_capture_chart_paint as proof
    admitted = _admitted()
    events = []
    png = _png(width=40 if mismatch == "dimensions" else 32)
    manifest = {"fixture": 1}
    def command(method, params):
        if method == "Runtime.evaluate":
            if "svgMaterialsReady" in params.get("expression", ""):
                events.append("materials")
                return {"result": {"value": {"svgMaterialsReady": True}}}
            return {"result": {"value": {"dpr": 2 if mismatch == "dpr" else 1, "width": 32, "height": 24}}}
        assert method == "Page.captureScreenshot"
        events.append("screenshot")
        return {"data": base64.b64encode(png).decode()}
    def drive(docs, timeout, **kwargs):
        assert docs == (admitted,)
        return [kwargs["capture"](command, "frame", admitted, {})]
    def collect(**kwargs):
        assert kwargs["command"] is command and kwargs["manifest"] == manifest
        assert kwargs["source_html"] == admitted.html and kwargs["device_scale_factor"] == 1
        assert Image.open(io.BytesIO(kwargs["final_png"])).size == (32, 24)
        events.append("collect")
        return {"complete": False, "reason": "fixture_incomplete"}
    def glyph(html, metrics, png, **kwargs):
        assert metrics["nativeChartPaintProof"]["complete"] is False
        events.append("glyph_navigation")
        return metrics
    monkeypatch.setattr(pipe, "drive_admitted_htmls_over_pipe", drive)
    monkeypatch.setattr(pipe, "probe_glyph_clipping_sync", glyph)
    monkeypatch.setattr(proof, "collect_chart_paint_proof", collect)
    def render():
        return pipe.render_admitted_html_over_pipe(
            admitted, 12, width_px=32, height_px=24, chart_witness_manifest=manifest,
            blocked_urls=(), chrome_pipe=lambda *args: None, find_chrome=lambda: "fixture",
            instrument_html=lambda *args: ("fixture", "key"), runtime_value=lambda _: json.dumps({"elements": []}))
    if mismatch is not None:
        with pytest.raises(RuntimeError, match="chart paint raster"):
            render()
        assert "collect" not in events and "glyph_navigation" not in events
    else:
        image, metrics = render()
        assert events == ["materials", "screenshot", "collect", "glyph_navigation"]
        assert metrics["nativeChartPaintProof"]["complete"] is False


@pytest.mark.parametrize("manifest,reject", [(None, False), ({"fixture": 1}, False), ({"fixture": 2}, True)])
def test_client_validates_binding_without_caching_chart_metrics(monkeypatch, manifest, reject):
    from utils import slide_capture_worker_client as client, slide_capture_chart_paint as proof
    html = _admitted().html
    metrics = {"elements": [], **({"nativeChartPaintProof": {"complete": False}} if manifest is not None else {})}
    response = json.dumps({"source_digest": _admitted().source_digest}).encode()
    events = []
    sent = []

    class Input:
        def write(self, request):
            sent.append(json.loads(request))
        async def drain(self):
            pass
        def close(self):
            pass
        async def wait_closed(self):
            pass

    class Output:
        async def readexactly(self, limit):
            raise asyncio.IncompleteReadError(response, limit)

    class Process:
        stdin = Input()
        stdout = Output()
        returncode = 0
        async def wait(self):
            return 0

    async def create(*args, **kwargs):
        return Process()

    def validate(received_metrics, png, received_manifest, **kwargs):
        events.append("validate")
        assert received_metrics == metrics and png == b"png" and received_manifest == manifest
        assert kwargs == {"wrapper_html": html, "width_px": 32, "height_px": 24}
        if reject:
            raise ValueError("fixture binding mismatch")

    monkeypatch.setattr(client, "_admit_render_source", lambda source, **kwargs: _admitted(source))
    monkeypatch.setattr(client, "worker_command", lambda: ["isolated-fixture"])
    monkeypatch.setattr(client, "build_worker_environment", lambda: {})
    monkeypatch.setattr(client.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(client, "bind_to_parent_lifetime", lambda process: None)
    monkeypatch.setattr(client, "_decode_worker_response", lambda payload: (b"png", metrics))
    monkeypatch.setattr(client, "_cache_browser_metrics", lambda *args, **kwargs: events.append("cache"))
    monkeypatch.setattr(proof, "validate_chart_paint_binding", validate)
    kwargs = {"chart_witness_manifest": manifest} if manifest is not None else {}
    task = client._render_html_in_isolated_worker_impl(html, 12, width_px=32, height_px=24, **kwargs)
    if reject:
        with pytest.raises(ValueError, match="binding mismatch"):
            asyncio.run(task)
    else:
        assert asyncio.run(task) == (b"png", metrics)
    assert events == (["cache"] if manifest is None else ["validate"])
    assert ("chart_witness_manifest" in sent[0]) is (manifest is not None)


@pytest.mark.parametrize("font_manifest", [None, {"font_fixture": True}])
@pytest.mark.parametrize("manifest", [None, {"fixture": 1}])
def test_svg_forwards_optional_carrier_on_regular_and_font_paths(monkeypatch, font_manifest, manifest):
    from services import svg_renderer as svg
    from utils import slide_capture as capture, slide_capture_worker_client as client
    from utils import renderer_worker_policy, slide_capture_chart_paint as proof
    from utils.slide_capture_result import RenderCapture
    source = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="24"><rect width="32" height="24"/></svg>'
    wrapper = "<html><body>" + source + "</body></html>"
    prepared_manifest = {**manifest, "captureContext": {"fixture": True}} if manifest is not None else None
    calls = []
    metrics = {"elements": [], **({"nativeChartPaintProof": {"complete": False}} if manifest is not None else {})}
    async def regular(html, **kwargs):
        calls.append(("regular", html, kwargs))
        return RenderCapture.from_payload(html_digest="fixture", png=b"png", browser_metrics=metrics, runtime_counters={})
    async def isolated(html, timeout, **kwargs):
        calls.append(("isolated", html, kwargs))
        return b"png", metrics
    monkeypatch.setattr(svg, "svg_preview_html", lambda *args, **kwargs: wrapper)
    monkeypatch.setattr(svg, "_template_manifest_materials", lambda *args: (SimpleNamespace(digest="fixture"), [], []))
    monkeypatch.setattr(capture, "render_html_to_png_with_metrics", regular)
    monkeypatch.setattr(client, "_render_html_in_isolated_worker", isolated)
    monkeypatch.setattr(renderer_worker_policy, "ensure_renderer_enabled", lambda: None)
    monkeypatch.setattr(capture, "_get_render_sem", lambda: asyncio.Semaphore(1))
    def prepare(received, **kwargs):
        assert received == manifest
        assert kwargs == {"canonical_svg": source, "wrapper_html": wrapper, "width_px": 32, "height_px": 24}
        return prepared_manifest
    monkeypatch.setattr(proof, "prepare_chart_capture_manifest", prepare)
    result = asyncio.run(svg.render_svg_with_metrics(source, width=32, height=24,
                        font_manifest=font_manifest, chart_witness_manifest=manifest))
    assert calls[0][0] == ("regular" if font_manifest is None else "isolated")
    assert calls[0][1] == wrapper
    kwargs = calls[0][2]
    assert ("chart_witness_manifest" in kwargs) is (manifest is not None)
    if manifest is not None:
        assert kwargs["chart_witness_manifest"] == prepared_manifest
        assert result.quality["browser_metrics"]["nativeChartPaintProof"]["complete"] is False
    assert "nativeChartPaintProof" not in result.quality


def test_preserved_raster_defers_manifest_source_validation_until_wrapper(monkeypatch):
    from utils import authored_renderer_worker as worker, slide_capture_chart_paint as proof
    request = json.loads(encode_request("<svg/>", 12, preserved_raster_svg="<svg/>",
                                        chart_witness_manifest={"fixture": 1}))
    calls = []
    monkeypatch.setattr(proof, "validate_manifest", lambda manifest, **kwargs: calls.append(kwargs))
    worker._validate_request(request)
    assert len(calls) == 1 and "source_html" not in calls[0]


def test_worker_validates_reconstructed_raster_wrapper_before_capture(monkeypatch):
    from utils import authored_renderer_worker as worker, slide_capture as capture
    from utils import slide_capture_chart_paint as proof
    from services import svg_renderer
    wrapper = "<html><body>reconstructed</body></html>"
    events = []
    manifest = {"fixture": 1}
    def admit(*args, **kwargs):
        events.append("reconstruct")
        return _admitted(wrapper), wrapper
    def validate(received, **kwargs):
        assert received == manifest and kwargs["source_html"] == wrapper
        events.append("validate")
    async def render(admitted, **kwargs):
        events.append("capture")
        return b"png", {"nativeChartPaintProof": {"complete": False}}
    def finalize(metrics, png, received, **kwargs):
        assert kwargs["wrapper_html"] == wrapper
        return metrics
    monkeypatch.setattr(svg_renderer, "admit_preserved_raster_preview", admit)
    monkeypatch.setattr(capture, "_render_admitted_html_in_worker", render)
    monkeypatch.setattr(proof, "validate_manifest", validate)
    monkeypatch.setattr(proof, "finalize_chart_paint_proof", finalize)
    monkeypatch.setattr(worker, "capability_metadata", lambda **kwargs: {})
    result = asyncio.run(worker._render({"html": "<svg/>", "timeout": 12,
        "preserved_raster_svg": "<svg/>", "chart_witness_manifest": manifest}))
    assert result["metrics"]["nativeChartPaintProof"]["complete"] is False
    assert events == ["reconstruct", "validate", "capture"]


def _chart_test_font_bytes(*, variable=True, postscript="Fixture-Thin", variable_name="FixtureVariable"):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    builder = FontBuilder(1000, isTTF=True)
    glyphs = [".notdef", "A", "B"]
    builder.setupGlyphOrder(glyphs)
    builder.setupCharacterMap({65: "A", 66: "B"})
    outlines = {}
    for name in glyphs:
        pen = TTGlyphPen(None)
        if name != ".notdef":
            pen.moveTo((0, 0)); pen.lineTo((250, 700)); pen.lineTo((500, 0)); pen.closePath()
        outlines[name] = pen.glyph()
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics({name: (500, 0) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Fixture Variable", "styleName": "Thin",
        "uniqueFontIdentifier": postscript, "fullName": postscript, "psName": postscript})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    if variable:
        builder.setupFvar([("wght", 100, 100, 900, "Weight")], [])
    builder.font["name"].setName(variable_name, 25, 3, 1, 0x409)
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


def _chart_font_evidence(monkeypatch, embedded, *, selected="FixtureVariable", text="AB", custom=True,
                         glyph_count=2, installed=()):
    from utils import slide_capture_chart_paint as paint
    monkeypatch.setattr(paint, "_evaluate", lambda _command, _script: [{"index": 0, "text": text}])
    installed_calls = []
    def installed_faces(name):
        installed_calls.append(name)
        return installed
    monkeypatch.setattr(paint, "_installed_face", installed_faces)
    def command(method, _params):
        if method in {"DOM.enable", "CSS.enable"}:
            return {}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": 2}
        assert method == "CSS.getPlatformFontsForNode"
        return {"fonts": [{"postScriptName": selected, "isCustomFont": custom, "glyphCount": glyph_count}]}
    return paint._glyph_font_evidence(command, embedded, {}), installed_calls


@pytest.fixture(scope="module")
def chart_actual_noto_subset():
    """Rebuild the exact material observed by the authorized font-only CDP run."""
    from services.svg_default_fonts import FONT_FAMILY, _NOTO_AXES, font_material
    from services.svg_render_font_subset import SubsetFace, subset_face
    from utils.hashing import sha256_prefixed
    charset = "".join(map(chr, [32, 48, 49, 51, 54, 57, 65, 66, 71, 83, 97,
                                100, 101, 104, 108, 109, 110, 112, 114, 115, 116]))
    source_digest, payload = font_material(FONT_FAMILY)
    subset = subset_face(SubsetFace(FONT_FAMILY, "100 900", _NOTO_AXES, source_digest), payload, charset)
    assert sha256_prefixed(subset) == "sha256:69221cf97f8255dbb7572f7c82bd1c363b2740ef4289e77e5503b53e12f50b09"
    return subset


def test_chart_font_actual_variable_name25_binds_exact_admitted_subset(monkeypatch, chart_actual_noto_subset):
    from utils.slide_capture_chart_paint import _font_faces
    faces = _font_faces(chart_actual_noto_subset)
    assert faces[0]["postScriptName"] == "NotoSansKR-Thin"
    assert faces[0]["variablePostScriptName"] == "NotoSansKR"
    result, installed = _chart_font_evidence(monkeypatch, faces, selected="NotoSansKR", text="Alpha")
    assert result["complete"] is True and installed == []
    observed = result["runs"][0]["faces"][0]
    assert observed["postScriptName"] == "NotoSansKR"
    assert observed["fontDigest"] == "sha256:69221cf97f8255dbb7572f7c82bd1c363b2740ef4289e77e5503b53e12f50b09"


def test_chart_font_base_name6_remains_valid(monkeypatch):
    from utils.slide_capture_chart_paint import _font_faces
    faces = _font_faces(_chart_test_font_bytes(variable=False))
    result, installed = _chart_font_evidence(monkeypatch, faces, selected="Fixture-Thin")
    assert result["complete"] is True and installed == []


def test_chart_font_static_name25_cannot_authorize_variable_alias(monkeypatch):
    from utils.slide_capture_chart_paint import _font_faces
    faces = _font_faces(_chart_test_font_bytes(variable=False))
    assert "variablePostScriptName" not in faces[0]
    result, installed = _chart_font_evidence(monkeypatch, faces)
    assert result == {"complete": False, "reason": "font_glyph_coverage_unproven"}
    assert installed == []


@pytest.mark.parametrize("name", ["Fixture", "FixtureVariable-Regular", "Fixture Variable", "fixturevariable", "Unknown", "", None])
def test_chart_font_near_prefix_family_or_unknown_name_cannot_match(monkeypatch, name):
    from utils.slide_capture_chart_paint import _font_faces
    faces = _font_faces(_chart_test_font_bytes())
    result, installed = _chart_font_evidence(monkeypatch, faces, selected=name)
    assert result == {"complete": False, "reason": "font_glyph_coverage_unproven"}
    assert installed == []


def test_chart_font_variable_identity_still_requires_actual_subset_cmap(monkeypatch, chart_actual_noto_subset):
    from utils.slide_capture_chart_paint import _font_faces
    result, installed = _chart_font_evidence(monkeypatch, _font_faces(chart_actual_noto_subset),
                                            selected="NotoSansKR", text="Alpha\U0001f984")
    assert result == {"complete": False, "reason": "font_glyph_coverage_unproven"}
    assert installed == []


def test_chart_font_distinct_materials_cannot_union_coverage(monkeypatch):
    from utils.slide_capture_chart_paint import _font_faces
    faces = _font_faces(_chart_test_font_bytes(postscript="FixtureA-Thin"))
    faces += _font_faces(_chart_test_font_bytes(postscript="FixtureB-Thin"))
    result, installed = _chart_font_evidence(monkeypatch, faces)
    assert result == {"complete": False, "reason": "selected_font_resource_ambiguous"}
    assert installed == []


def test_chart_font_collection_faces_cannot_union_coverage(monkeypatch):
    from fontTools.ttLib import TTCollection, TTFont
    from utils.slide_capture_chart_paint import _font_faces
    collection = TTCollection()
    collection.fonts = [TTFont(io.BytesIO(_chart_test_font_bytes(postscript=name)))
                        for name in ("FixtureA-Thin", "FixtureB-Thin")]
    output = io.BytesIO()
    try:
        collection.save(output)
    finally:
        collection.close()
    faces = _font_faces(output.getvalue())
    assert len({face["fontDigest"] for face in faces}) == 1
    assert {face["faceIndex"] for face in faces} == {0, 1}
    result, installed = _chart_font_evidence(monkeypatch, faces)
    assert result == {"complete": False, "reason": "selected_font_resource_ambiguous"}
    assert installed == []


def test_chart_font_same_face_declared_under_both_names_is_deduplicated(monkeypatch):
    from utils.slide_capture_chart_paint import _font_faces
    faces = _font_faces(_chart_test_font_bytes(postscript="FixtureVariable"))
    result, installed = _chart_font_evidence(monkeypatch, faces + faces)
    assert result["complete"] is True and installed == []
    assert len(result["runs"][0]["faces"]) == 1


def test_chart_font_system_name6_path_remains_separate(monkeypatch):
    from utils.slide_capture_chart_paint import _font_faces
    faces = _font_faces(_chart_test_font_bytes(variable=False))
    result, installed = _chart_font_evidence(monkeypatch, [], selected="Fixture-Thin", custom=False, installed=faces)
    assert result["complete"] is True and installed == ["Fixture-Thin"]
    assert result["runs"][0]["faces"][0]["isCustomFont"] is False


def test_chart_font_system_face_cannot_borrow_custom_variable_alias(monkeypatch):
    from utils.slide_capture_chart_paint import _font_faces
    result, installed = _chart_font_evidence(monkeypatch, _font_faces(_chart_test_font_bytes()), custom=False)
    assert result == {"complete": False, "reason": "font_glyph_coverage_unproven"}
    assert installed == ["FixtureVariable"]


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_chart_font_platform_glyph_count_requirement_is_preserved(monkeypatch, count):
    from utils.slide_capture_chart_paint import _font_faces
    result, installed = _chart_font_evidence(monkeypatch, _font_faces(_chart_test_font_bytes()), glyph_count=count)
    assert result == {"complete": False, "reason": "font_glyph_coverage_unproven"}
    assert installed == []


class _CarrierPlaneAbort(BaseException):
    pass


class _LifecycleCdp:
    """Stateful CDP boundary for the actual frozen driver and chart adapter."""

    def __init__(self, *, bad_loader=False, uncommitted=False, same_context=False,
                 retained_state=None, cleanup_failure=False, readiness=(), ready_default=True):
        self.bad_loader = bad_loader
        self.uncommitted = uncommitted
        self.same_context = same_context
        self.retained_state = retained_state
        self.cleanup_failure = cleanup_failure
        self.readiness = list(readiness)
        self.ready_default = ready_default
        self.ready_checks = []
        self.calls = []
        self.targets = {}
        self.sessions = {}
        self.contexts = []
        self.navigation_commits = []
        self.blank_checks = []
        self.loaded_html = []
        self.gates = []
        self.screenshots = []
        self.close_calls = 0

    @staticmethod
    def _empty_state():
        return dict.fromkeys(("prior_js", "prior_dom", "chart", "svg", "metric", "font",
                              "selection", "adopted", "stylesheet", "body"), 0)

    def _page(self, session_id):
        assert session_id in self.sessions
        return self.targets[self.sessions[session_id]]

    def command(self, command_id, method, params=None, *, session_id=None):
        params = dict(params or {})
        self.calls.append((command_id, method, params, session_id))
        if method == "Target.createBrowserContext":
            context = "context-" + str(len(self.contexts) + 1)
            self.contexts.append(context)
            assert params == {"disposeOnDetach": True}
            return {"browserContextId": context}
        if method == "Target.createTarget":
            assert params["url"] == "data:," and params["browserContextId"] in self.contexts
            target = "target-" + str(len(self.targets) + 1)
            self.targets[target] = {"context": params["browserContextId"], "frame": "frame-" + target,
                "loader": "loader-" + target + "-0", "url": "data:,", "generation": 0,
                "execution": 100 * (len(self.targets) + 1), "state": self._empty_state(),
                "html": None, "background": {}}
            return {"targetId": target}
        if method == "Target.attachToTarget":
            assert params["targetId"] in self.targets and params["flatten"] is True
            session = "session-" + str(len(self.sessions) + 1)
            self.sessions[session] = params["targetId"]
            return {"sessionId": session}
        if method in ("Target.closeTarget", "Target.disposeBrowserContext"):
            key = "targetId" if method == "Target.closeTarget" else "browserContextId"
            assert params[key] in (self.targets if key == "targetId" else self.contexts)
            if self.cleanup_failure and params[key].endswith("-2"):
                raise RuntimeError("fixture sibling teardown failure")
            return {"success": True} if key == "targetId" else {}
        if method == "Browser.setDownloadBehavior":
            assert params == {"behavior": "deny", "browserContextId": self.contexts[-1]}
            return {}
        page = self._page(session_id)
        if method in ("Page.enable", "Runtime.enable", "Network.enable", "Network.setBlockedURLs",
                      "Page.setBypassCSP", "Emulation.setDeviceMetricsOverride"):
            return {}
        if method == "Emulation.setDefaultBackgroundColorOverride":
            page["background"] = params
            return {}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": page["frame"], "loaderId": page["loader"], "url": page["url"]}}}
        if method == "Page.createIsolatedWorld":
            assert params == {"frameId": page["frame"], "worldName": "chart-plane-reset/v1"}
            return {"executionContextId": page["execution"]}
        if method == "Page.navigate":
            assert params == {"url": "data:,"}
            prior_loader, prior_context = page["loader"], page["execution"]
            next_loader = prior_loader if self.bad_loader else "loader-" + self.sessions[session_id] + "-" + str(page["generation"] + 1)
            if not self.uncommitted:
                page["loader"] = next_loader
                page["url"] = "data:,"
                page["generation"] += 1
                if not self.same_context:
                    page["execution"] += 1
                page["state"] = self._empty_state()
                if self.retained_state is not None:
                    page["state"][self.retained_state] = 1
                page["html"] = None
                self.navigation_commits.append((session_id, prior_loader, next_loader, prior_context,
                                                page["execution"], dict(page["state"])))
            return {"frameId": page["frame"], "loaderId": next_loader}
        if method == "Page.setDocumentContent":
            assert params["frameId"] == page["frame"]
            page["html"] = params["html"]
            page["state"].update(svg=1, metric=1, body=1)
            self.loaded_html.append((session_id, page["generation"], page["execution"], params["html"]))
            return {}
        if method == "Page.captureScreenshot":
            self.screenshots.append((session_id, page["generation"], page["execution"]))
            return {"data": base64.b64encode(_png()).decode(), "captureSequence": len(self.screenshots)}
        assert method == "Runtime.evaluate", method
        expression = params.get("expression", "")
        if "chartPlaneReadyV1" in expression:
            state = self.readiness.pop(0) if self.readiness else self.ready_default
            self.ready_checks.append((session_id, page["generation"], state))
            if isinstance(state, BaseException):
                raise state
            if isinstance(state, dict):
                return state
            return {"result": {"value": {"chartPlaneReadyV1": state}}}
        if "chartPlaneBlankV1" in expression:
            # Check the protocol asks about each guarded state; the state model
            # supplies the response rather than copying the adapter's predicate.
            for token in ("__chartPlanePriorDocument", "chart-plane-prior-document", "__chartPaint",
                          "querySelector('svg')", "__codex_authored_dom_metrics", "styleSheets",
                          "adoptedStyleSheets", "document.fonts", "getSelection()", "childNodes"):
                assert token in expression
            assert params.get("awaitPromise") is True
            clean = not any(page["state"].values())
            self.blank_checks.append((session_id, page["generation"], page["execution"], dict(page["state"]), page["background"]))
            return {"result": {"value": {"chartPlaneBlankV1": clean}}}
        if "globalThis.__chartPlanePriorDocument=true" in expression:
            page["state"].update(prior_js=1, prior_dom=1, body=1)
            return {"result": {"value": True}}
        if "carrierAllOnGateV1" in expression:
            value = {"carrierAllOnGateV1": True, "generation": page["generation"],
                     "execution": page["execution"], "html": page["html"]}
            self.gates.append((session_id, value))
            return {"result": {"value": value}}
        if "carrierSeedPriorStateV1" in expression:
            page["state"] = dict.fromkeys(page["state"], 1)
            return {"result": {"value": True}}
        if "svgMaterialsReady" in expression:
            return {"result": {"value": {"svgMaterialsReady": True}}}
        if "window.devicePixelRatio" in expression:
            return {"result": {"value": {"dpr": 1, "width": 32, "height": 24}}}
        assert "__codex_authored_dom_metrics" in expression
        return {"result": {"value": json.dumps({"elements": [], "carrierGeneration": page["generation"],
                                                   "carrierExecution": page["execution"], "carrierHtml": page["html"]})}}

    def close(self):
        self.close_calls += 1
        self.calls.append((None, "transport.close", {}, None))


def _exercise_reused_chart_planes(monkeypatch, transport, *, plane_failure=None):
    from utils import slide_capture_chart_adapter as adapter, slide_capture_chart_paint as proof
    from utils.slide_capture_pipe_render import drive_admitted_htmls_over_pipe as frozen_drive
    assert adapter.drive_admitted_htmls_over_pipe is frozen_drive
    original_shared = adapter._SharedChartPipe
    shared_instances, instruments, callbacks, accepted = [], [], [], []
    def shared_factory(*args, **kwargs):
        result = original_shared(*args, **kwargs)
        shared_instances.append(result)
        return result
    def instrument(html, **kwargs):
        assert kwargs == {"script_nonce": "nonce", "issue_capability": False, "width_px": 32, "height_px": 24}
        value = html.replace("</body>", "<i data-instrumented-pass='" + str(len(instruments)) + "'></i></body>")
        instruments.append(value)
        return value, "metrics-key"
    def collect(**kwargs):
        plane_values = []
        for index in range(2):
            def observe(command, metrics, index=index):
                gate = command("Runtime.evaluate", {"expression": "({carrierAllOnGateV1:true})", "returnByValue": True})["result"]["value"]
                assert gate["carrierAllOnGateV1"] is True
                assert gate["generation"] == index + 1 == metrics["carrierGeneration"]
                assert gate["execution"] == metrics["carrierExecution"]
                assert gate["html"] == instruments[index + 1] == metrics["carrierHtml"]
                shot = command("Page.captureScreenshot", {"format": "png", "fromSurface": True, "captureBeyondViewport": False})
                callbacks.append((index, gate, shot["captureSequence"]))
                command("Runtime.evaluate", {"expression": "({carrierSeedPriorStateV1:true})", "returnByValue": True})
                command("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
                if plane_failure is not None and index == 0:
                    raise plane_failure
                return {"plane": index, "execution": gate["execution"], "captureSequence": shot["captureSequence"]}
            try:
                value = kwargs["diagnostic_capture"](observe)
            except BaseException:
                physical_creates = sum(call[1] == "Target.createTarget" for call in transport.calls)
                with pytest.raises(RuntimeError, match="lifecycle unavailable"):
                    kwargs["diagnostic_capture"](lambda *args: pytest.fail("poisoned sibling callback ran"))
                assert sum(call[1] == "Target.createTarget" for call in transport.calls) == physical_creates
                raise
            plane_values.append(value)
        return {"complete": True, "fixturePlaneResults": plane_values}
    monkeypatch.setattr(adapter, "_SharedChartPipe", shared_factory)
    monkeypatch.setattr(adapter, "new_capture_timing", lambda: None)
    monkeypatch.setattr(adapter, "probe_glyph_clipping_sync", lambda html, metrics, png, **kwargs: metrics)
    monkeypatch.setattr(proof, "collect_chart_paint_proof", collect)
    def run():
        result = adapter.render_admitted_html_over_pipe(
            _admitted("<html><body><svg id='source-svg'></svg></body></html>"), 1,
            chart_witness_manifest={"fixture": True}, width_px=32, height_px=24,
            blocked_urls=("http://*",), chrome_pipe=lambda *args: transport, find_chrome=lambda: "fake-chrome",
            instrument_html=instrument, runtime_value=lambda result: result.get("result", {}).get("value"))
        accepted.append(result)
        return result
    return SimpleNamespace(run=run, shared=shared_instances, instruments=instruments, callbacks=callbacks, accepted=accepted)


def _assert_chart_transport_teardown_once(transport):
    assert transport.close_calls == 1
    for method, key, identities in (("Target.closeTarget", "targetId", tuple(transport.targets)),
                                    ("Target.disposeBrowserContext", "browserContextId", tuple(transport.contexts))):
        for identity in identities:
            assert sum(name == method and params.get(key) == identity for _, name, params, _ in transport.calls) == 1
    assert transport.calls[-1][1] == "transport.close"


def test_private_chart_sibling_reuses_transport_but_commits_fresh_documents(monkeypatch):
    transport = _LifecycleCdp()
    fixture = _exercise_reused_chart_planes(monkeypatch, transport)
    png, metrics = fixture.run()
    assert Image.open(io.BytesIO(png)).size == (32, 24)
    assert len(transport.contexts) == len(transport.targets) == len(transport.sessions) == 2
    for method in ("Target.createBrowserContext", "Target.createTarget", "Target.attachToTarget"):
        assert sum(name == method for _, name, _, _ in transport.calls) == 2
    assert len(transport.navigation_commits) == len(transport.blank_checks) == 2
    commits = transport.navigation_commits
    assert commits[0][1] != commits[0][2] != commits[1][2]
    assert commits[0][3] != commits[0][4] == commits[1][3] != commits[1][4]
    assert all(not any(state.values()) and background == {} for _, _, _, state, background in transport.blank_checks)
    sibling_session = commits[0][0]
    assert all(commit[0] == sibling_session for commit in commits)
    resets = [params for _, name, params, session in transport.calls
              if name == "Emulation.setDefaultBackgroundColorOverride" and session == sibling_session and not params]
    assert resets == [{}, {}]
    assert [html for _, _, _, html in transport.loaded_html] == fixture.instruments
    assert len(fixture.instruments) == 3 and len(set(fixture.instruments)) == 3
    assert [value["execution"] for value in metrics["nativeChartPaintProof"]["fixturePlaneResults"]] == [commits[0][4], commits[1][4]]
    assert [sequence for _, _, sequence in fixture.callbacks] == [2, 3]
    assert len(transport.gates) == len(fixture.callbacks) == 2
    primary_session = next(session for session in transport.sessions if session != sibling_session)
    assert all(session == sibling_session for session, _ in transport.gates)
    assert next(item for item in transport.loaded_html if item[0] == primary_session)[1] == 0
    assert len(fixture.accepted) == 1 and fixture.shared[0].poisoned is False
    assert [call[0] for call in transport.calls if call[0] is not None] == list(range(1, len(transport.calls)))
    _assert_chart_transport_teardown_once(transport)


@pytest.mark.parametrize("failure", [RuntimeError("plane failed"), _CarrierPlaneAbort("plane cancelled")])
def test_private_chart_sibling_failure_and_baseexception_poison_reuse(monkeypatch, failure):
    transport = _LifecycleCdp()
    fixture = _exercise_reused_chart_planes(monkeypatch, transport, plane_failure=failure)
    with pytest.raises(type(failure)) as caught:
        fixture.run()
    assert caught.value is failure
    assert not fixture.accepted and len(fixture.callbacks) == 1
    assert fixture.shared[0].poisoned is True and fixture.shared[0].closed is True
    _assert_chart_transport_teardown_once(transport)


@pytest.mark.parametrize("transport_kwargs,reason", [
    ({"bad_loader": True}, "navigation identity invalid"),
    ({"same_context": True}, "execution context was not replaced"),
    *[({"retained_state": field}, "blank document retained prior state") for field in
      ("prior_js", "prior_dom", "chart", "svg", "metric", "font", "selection", "adopted", "stylesheet", "body")],
])
def test_private_chart_sibling_rejects_unproved_document_replacement(monkeypatch, transport_kwargs, reason):
    transport = _LifecycleCdp(**transport_kwargs)
    fixture = _exercise_reused_chart_planes(monkeypatch, transport)
    with pytest.raises(RuntimeError, match=reason):
        fixture.run()
    assert not fixture.accepted and not fixture.callbacks
    assert fixture.shared[0].poisoned is True
    _assert_chart_transport_teardown_once(transport)


def test_private_chart_sibling_requires_committed_navigation(monkeypatch):
    from utils import slide_capture_chart_adapter as adapter
    ticks = iter((0, 10))
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    transport = _LifecycleCdp(uncommitted=True)
    fixture = _exercise_reused_chart_planes(monkeypatch, transport)
    with pytest.raises(RuntimeError, match="blank navigation did not commit"):
        fixture.run()
    assert not fixture.accepted and not fixture.callbacks
    assert fixture.shared[0].poisoned is True
    _assert_chart_transport_teardown_once(transport)


def test_private_chart_sibling_cleanup_failure_still_disposes_and_closes_transport(monkeypatch):
    transport = _LifecycleCdp(cleanup_failure=True)
    fixture = _exercise_reused_chart_planes(monkeypatch, transport)
    with pytest.raises(RuntimeError, match="chart sibling cleanup failed"):
        fixture.run()
    assert not fixture.accepted and fixture.shared[0].closed is True
    _assert_chart_transport_teardown_once(transport)
    fixture.shared[0].close()
    assert transport.close_calls == 1


@pytest.mark.parametrize("first_state", [
    False,
    RuntimeError("Execution context was destroyed"),
    RuntimeError("Cannot find default execution context"),
    RuntimeError("Cannot find context with specified id"),
])
def test_private_chart_sibling_retries_only_transient_readiness(monkeypatch, first_state):
    from utils import slide_capture_chart_adapter as adapter
    ticks = iter(range(100))
    waits = []
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: next(ticks) / 1000,
                                                        sleep=lambda delay: waits.append(delay)))
    transport = _LifecycleCdp(readiness=(first_state, True))
    fixture = _exercise_reused_chart_planes(monkeypatch, transport)
    fixture.run()
    assert [state for _, _, state in transport.ready_checks] == [first_state, True, True]
    assert waits == [.01] and len(fixture.callbacks) == 2
    assert fixture.shared[0].poisoned is False
    _assert_chart_transport_teardown_once(transport)


def test_private_chart_sibling_never_accepts_persistent_loading(monkeypatch):
    from utils import slide_capture_chart_adapter as adapter
    ticks = iter((0, 10))
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: next(ticks),
                                                        sleep=lambda _: pytest.fail("expired reset deadline slept")))
    transport = _LifecycleCdp(ready_default=False)
    fixture = _exercise_reused_chart_planes(monkeypatch, transport)
    with pytest.raises(RuntimeError, match="blank document did not become ready"):
        fixture.run()
    assert not fixture.callbacks and not fixture.accepted
    assert fixture.shared[0].poisoned is True
    _assert_chart_transport_teardown_once(transport)


@pytest.mark.parametrize("state,reason", [
    (RuntimeError("unexpected evaluator failure"), "unexpected evaluator failure"),
    ({"exceptionDetails": {"text": "fixture JS failure"}}, "blank readiness evaluation failed"),
    ({"result": {"value": {"unknown": True}}}, "blank readiness invalid"),
])
def test_private_chart_sibling_rejects_nontransient_readiness_failure(monkeypatch, state, reason):
    from utils import slide_capture_chart_adapter as adapter
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: 0,
                                                        sleep=lambda _: pytest.fail("invalid readiness retried")))
    transport = _LifecycleCdp(readiness=(state,))
    fixture = _exercise_reused_chart_planes(monkeypatch, transport)
    with pytest.raises(RuntimeError, match=reason):
        fixture.run()
    assert len(transport.ready_checks) == 1
    assert not fixture.callbacks and not fixture.accepted
    assert fixture.shared[0].poisoned is True
    _assert_chart_transport_teardown_once(transport)
def test_exact_source_over_replays_seven_donut_pixels_without_artifact_dependency():
    from PIL import Image, ImageChops
    from utils.slide_capture_chart_paint import _source_over_original_color

    # Immutable diagnostic pixel observations; original source/artifact paths
    # are deliberately unnecessary for this portable byte-arithmetic check.
    native_values = [(36, 98, 235, 112), (28, 99, 241, 18), (37, 99, 235, 251),
                     (36, 98, 235, 177), (34, 100, 236, 82),
                     (36, 99, 236, 157), (35, 93, 243, 22)]
    original_values = [(159, 186, 246), (239, 244, 254), (40, 101, 235),
                       (103, 146, 241), (184, 205, 249), (159, 112, 163), (224, 91, 95)]
    def image(mode, values):
        result = Image.new(mode, (7, 1))
        result.putdata(values)
        return result
    result = _source_over_original_color(
        image("RGBA", native_values),
        image("RGB", [(255, 255, 255)] * 5 + [(242, 171, 171), (229, 95, 95)]),
        image("RGB", original_values), image("L", [255] * 5 + [0, 0]),
        image("RGB", [(v, v, v) for v in (112, 18, 251, 177, 82, 97, 6)]),
        image("RGB", [(v, v, v) for v in (112, 18, 251, 177, 82, 98, 5)]),
        image("L", [0] * 5 + [98, 187]), image("L", [255] * 7))
    assert list(result.getdata()) == [255] * 5 + [0, 0]
    response_error = ImageChops.difference(
        image("RGB", [(v, v, v) for v in (112, 18, 251, 177, 82, 97, 6)]),
        image("RGB", [(v, v, v) for v in (112, 18, 251, 177, 82, 98, 5)]),
    ).convert("L").point(lambda value: 255 if value else 0)
    corrected = ImageChops.lighter(response_error, ImageChops.subtract(image("L", [255] * 5 + [0, 0]), result))
    assert list(corrected.getdata()) == [0] * 5 + [255, 255]


@pytest.mark.parametrize("changed", [
    {"current": (111, 111, 111)},  # Same-color foreign overlay still attenuates causality.
    {"original": (160, 186, 246)},
    {"native": (36, 98, 235, 111)},
    {"native": (37, 98, 235, 112)},
    {"background": (254, 255, 255)},
    {"native": (1, 98, 235, 112)},  # No exact integer premultiplied round-trip.
    {"owned_alpha": 1}, {"original_contribution": 0}, {"appearance": 0},
    {"native": (36, 98, 235, 0)},
])
def test_exact_source_over_rejects_unproved_color_or_response(changed):
    from PIL import Image
    from utils.slide_capture_chart_paint import _source_over_original_color

    values = dict(native=(36, 98, 235, 112), background=(255, 255, 255),
                  original=(159, 186, 246), appearance=255, expected=(112, 112, 112),
                  current=(112, 112, 112), owned_alpha=0, original_contribution=255)
    values.update(changed)
    images = [Image.new("RGBA" if key == "native" else "RGB" if isinstance(value, tuple) else "L", (1, 1), value)
              for key, value in values.items()]
    assert _source_over_original_color(*images).getbbox() is None


def test_exact_source_over_accepts_actual_nonwhite_backdrop_without_color_fit():
    from PIL import Image
    from utils.slide_capture_chart_paint import _source_over_original_color

    values = [("RGBA", (40, 80, 120, 128)), ("RGB", (10, 100, 200)),
              ("RGB", (25, 90, 160)), ("L", 255), ("RGB", (127, 127, 127)),
              ("RGB", (127, 127, 127)), ("L", 0), ("L", 255)]
    assert _source_over_original_color(*(Image.new(mode, (1, 1), value) for mode, value in values)).getpixel((0, 0)) == 255
