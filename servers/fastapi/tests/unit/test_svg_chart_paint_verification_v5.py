"""v5 native chart paint is proven after publication, never on the render critical path."""
from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.drivers.svg import chart_paint_verification as verification
from services.generation_core.drivers.svg.chart_paint_verification import (
    EVIDENCE_KEY, UNVERIFIED_REASON, WITNESS_SOURCE_KEY, chart_paint_fallbacks, current_paint_evidence,
    seal_paint_evidence, verification_manifest,
)
from services.generation_core.drivers.svg.export_native_wire import bundle_entry, parse_bundle_native_objects
from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V4 as V4, REQUIREMENTS_VERSION_V5 as V5, SNAPSHOT_REVISION_KEY,
    apply_requested_native_quality,
)
from services.generation_core.native_objects import slide_native_objects
from services.generation_core.quality.page_failure_kind import quality_keeps_content
from tests.unit.test_svg_native_chart_retention_v5 import NS, SOURCE, _page

PASSED = {"passed": True, "reason": "chart_content_paint_captured_native_composition_retained",
          "scope": "emitted-native-raster-composition"}
FAILED = {"passed": False, "reason": "chart_content_paint_proof_incomplete"}


def _published(revision=V5):
    from services.generation_core.drivers.svg.publish import svg_slide_content
    page = _page()
    content = svg_slide_content(page, role="content", source=SOURCE, pt_per_px=.75, revision=revision)
    return page, content


def _slide(content, svg, properties=None, slide_id="slide-1"):
    return SimpleNamespace(id=slide_id, svg_content=svg, content=content, properties=properties or {"image": "x.png"})


def _presentation(revision=V5):
    return SimpleNamespace(theme={"svg": {"styleSnapshot": {SNAPSHOT_REVISION_KEY: revision}}})


class _Sessions:
    """A session factory whose ``get`` returns the stored slide rows."""

    def __init__(self, rows):
        self.rows, self.commits = rows, 0

    def __call__(self):
        outer = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, _model, key):
                return outer.rows.get(key)

            def add(self, _row):
                pass

            async def commit(self):
                outer.commits += 1
        return Session()


def _measured_quality():
    return {"passed": True, "failures": [], "measurement_complete": True, "source_digest": "sha256:" + "0" * 64,
            "png_digest": "sha256:" + "1" * 64}


# --- pending at render -------------------------------------------------------

def test_render_passes_record_pending_paint_and_keep_content_proof():
    page = _page()
    quality = _measured_quality()
    capture = SimpleNamespace(quality=quality, png=b"unmeasured")
    apply_requested_native_quality(quality, page, page["svg_content"], SOURCE, pt_per_px=.75, revision=V5,
                                   render_capture=capture, post_render=True, paint_deferred=True)
    requested = quality["requestedNativeObjects"]
    assert quality["passed"] is True and requested["passed"] is True
    assert all(item == {"passed": False, "status": "pending", "reason": "native_chart_capture_not_measured"}
               for item in requested["paintEvidence"].values())
    assert requested["contentRetentionProven"] is False
    assert all(item["passed"] for item in requested["drawingEvidence"].values())
    # Without deferral the same measured pass still demands the in-capture proof.
    strict = _measured_quality()
    apply_requested_native_quality(strict, page, page["svg_content"], SOURCE, pt_per_px=.75, revision=V5,
                                   render_capture=capture, post_render=True)
    assert any(item["kind"] == "measurement_incomplete" for item in strict["failures"])


def test_deferred_render_still_requires_content_geometry():
    page = _page()
    root = ET.fromstring(page["svg_content"])
    next(node for node in root.iter(NS + "text") if node.text == "60").text = "61"
    page["svg_content"] = ET.tostring(root, encoding="unicode")
    from services.generation_core.drivers.svg.native_declarations import bind_native_declarations
    bind_native_declarations(page, page["svg_content"], page["nativeDeclarations"])
    quality = _measured_quality()
    apply_requested_native_quality(quality, page, page["svg_content"], SOURCE, pt_per_px=.75, revision=V5,
                                   render_capture=SimpleNamespace(), post_render=True, paint_deferred=True)
    assert quality["passed"] is False
    assert any(item["kind"] == "native_requested_chart_missing" for item in quality["failures"])


def test_preserved_v5_gate_defers_paint_only_when_asked():
    from services.generation_core.drivers.svg.render import _apply_preserved_v5_chart_quality
    page = _page()
    quality = _measured_quality()
    _apply_preserved_v5_chart_quality(quality, page, page["svg_content"], SOURCE, revision=V5, pt_per_px=.75,
                                      render_capture=SimpleNamespace(), paint_deferred=True)
    assert quality["passed"] is True and quality_keeps_content(quality)


def test_render_and_remake_passes_send_no_chart_witness_manifest():
    from services import svg_page_remake
    from services.generation_core.drivers.svg import render
    for module in (render, svg_page_remake):
        source = inspect.getsource(module)
        assert "chart_witness_render_kwargs" not in source and "chart_witness_manifest" not in source
        assert "paint_deferred=True" in source or '"paint_deferred": True' in source


@pytest.mark.parametrize("revision", [None, V4])
def test_paint_deferral_changes_nothing_before_v5(revision):
    page = _page()
    results = []
    for deferred in (False, True):
        quality = _measured_quality()
        apply_requested_native_quality(quality, page, page["svg_content"], SOURCE, pt_per_px=.75,
                                       revision=revision, **({"paint_deferred": True} if deferred else {}))
        results.append(quality)
    assert results[0] == results[1]


# --- publication keeps the witness source ------------------------------------

@pytest.mark.parametrize("revision", [None, V4])
def test_earlier_revisions_publish_no_witness_source(revision):
    _, content = _published(revision)
    assert WITNESS_SOURCE_KEY not in content


def test_v5_publication_keeps_declarations_that_rebuild_the_manifest():
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    page, content = _published()
    assert content[WITNESS_SOURCE_KEY]["declarations"] == page["nativeDeclarations"]
    assert "nativeDeclarations" not in content["svgReceipt"]
    rebuilt = verification_manifest(content, page["svg_content"])
    assert rebuilt == build_chart_witness_manifest(page, page["svg_content"], revision=V5)
    assert verification_manifest(content, page["svg_content"].replace("Gamma", "Delta")) is None


# --- verification stores digest-bound evidence --------------------------------

def test_verification_runs_the_unchanged_proof_and_stores_bound_evidence(monkeypatch):
    from services import svg_chart_paint_verification as service
    from services.generation_core.drivers.svg import chart_content_paint
    page, content = _published()
    svg = page["svg_content"]
    slide = _slide(content, svg)
    calls, validated = [], []

    async def render(source, **kwargs):
        calls.append((source, kwargs))
        return SimpleNamespace(quality={"fixture": True}, png=b"png", wrapper_html="<html/>", source_digest="d")

    def validate(manifest, quality, *, capture):
        validated.append((manifest, quality, capture))
        return dict(PASSED)

    async def inputs(session, presentation, slide):
        return {"width": 1280, "height": 720}

    monkeypatch.setattr(chart_content_paint, "validate_chart_content_paint", validate)
    monkeypatch.setattr(service, "_render_inputs", inputs)
    row = SimpleNamespace(svg_content=svg, properties={"image": "x.png"})
    sessions = _Sessions({"slide-1": row})
    fallbacks = asyncio.run(service.deck_chart_paint_fallbacks(sessions, presentation=_presentation(),
        slides=[slide], remaining_seconds=lambda: 10_000, render=render))
    assert fallbacks == [[]]
    (source, kwargs), = calls
    manifest = verification_manifest(content, svg)
    assert source == svg and kwargs["chart_witness_manifest"] == manifest
    assert kwargs["timeout"] == verification.VERIFICATION_TIMEOUT_SECONDS == 120
    assert validated[0][0] == manifest and validated[0][1] == {"fixture": True}
    record = row.properties[EVIDENCE_KEY]
    assert row.properties["image"] == "x.png" and sessions.commits == 1
    from services.svg_contract import source_digest
    assert record["svgDigest"] == source_digest(svg) and record["manifestDigest"] == manifest["manifestDigest"]
    assert record["chartIds"] == ["native-chart-sales"] and record["paint"] == PASSED
    assert current_paint_evidence(row.properties, content, svg) == record
    # A second export reuses the stored evidence without capturing again.
    slide.properties = row.properties
    assert asyncio.run(service.deck_chart_paint_fallbacks(sessions, presentation=_presentation(),
        slides=[slide], remaining_seconds=lambda: 10_000, render=render)) == [[]]
    assert len(calls) == 1


def test_verification_without_budget_or_capture_leaves_charts_drawn(monkeypatch):
    from services import svg_chart_paint_verification as service
    page, content = _published()
    slide = _slide(content, page["svg_content"])
    sessions = _Sessions({})

    async def never(*args, **kwargs):
        raise AssertionError("no capture without budget")

    expected = [[{"id": "native-chart-sales", "reason": UNVERIFIED_REASON}]]
    for remaining in (None, lambda: service.EXPORT_RESERVE_SECONDS):
        assert asyncio.run(service.deck_chart_paint_fallbacks(sessions, presentation=_presentation(),
            slides=[slide], remaining_seconds=remaining, render=never)) == expected

    async def broken(*args, **kwargs):
        raise TimeoutError("capture exceeded")

    async def inputs(session, presentation, slide):
        return {}

    monkeypatch.setattr(service, "_render_inputs", inputs)
    assert asyncio.run(service.deck_chart_paint_fallbacks(sessions, presentation=_presentation(),
        slides=[slide], remaining_seconds=lambda: 10_000, render=broken)) == expected
    assert sessions.commits == 0


def test_failed_proof_is_stored_and_keeps_the_chart_drawn_with_its_reason():
    page, content = _published()
    svg = page["svg_content"]
    record = seal_paint_evidence(svg, verification_manifest(content, svg), content, FAILED)
    sidecar = slide_native_objects(content)
    assert chart_paint_fallbacks(content, {EVIDENCE_KEY: record}, svg, sidecar, revision=V5) == [
        {"id": "native-chart-sales", "reason": "native_chart_paint_failed:chart_content_paint_proof_incomplete"}]


# --- stale evidence is rejected ------------------------------------------------

def test_stale_or_tampered_evidence_never_applies():
    page, content = _published()
    svg = page["svg_content"]
    record = seal_paint_evidence(svg, verification_manifest(content, svg), content, PASSED)
    sidecar = slide_native_objects(content)
    assert chart_paint_fallbacks(content, {EVIDENCE_KEY: record}, svg, sidecar, revision=V5) == []
    unverified = [{"id": "native-chart-sales", "reason": UNVERIFIED_REASON}]
    # Another svg (an edit or remake) under the same slide properties.
    edited = svg.replace(">Alpha<", ">Alpha <")
    assert current_paint_evidence({EVIDENCE_KEY: record}, content, edited) is None
    assert chart_paint_fallbacks(content, {EVIDENCE_KEY: record}, edited, sidecar, revision=V5) == unverified
    # A changed NativeObjects sidecar with unchanged svg bytes.
    changed = deepcopy(content)
    changed["nativeObjects"]["objects"][0]["payload"]["title"] = "Other"
    assert current_paint_evidence({EVIDENCE_KEY: record}, changed, svg) is None
    # A failed record edited into a pass, or a sealed record with a forged digest.
    forged = {**seal_paint_evidence(svg, verification_manifest(content, svg), content, FAILED), "paint": PASSED}
    assert current_paint_evidence({EVIDENCE_KEY: forged}, content, svg) is None
    assert current_paint_evidence({EVIDENCE_KEY: {**record, "digest": "sha256:" + "0" * 64}}, content, svg) is None
    # Evidence without the published witness source cannot be checked.
    bare = {key: value for key, value in content.items() if key != WITNESS_SOURCE_KEY}
    assert chart_paint_fallbacks(bare, {EVIDENCE_KEY: record}, svg, sidecar, revision=V5) == unverified


# --- export: native only when verified ------------------------------------------

def test_export_keeps_unverified_v5_chart_drawn_and_verified_chart_native(tmp_path):
    from tests.unit.test_svg_native_objects_export_scene import _chart_group, _chart_object, _convert, _svg, _texts
    svg = _svg(_chart_group("native-chart-revenue", "분기별 매출"))
    content = {"nativeObjects": {"schemaVersion": "native-objects/v1",
                                 "objects": [_chart_object("native-chart-revenue", "분기별 매출")]}}
    reason = UNVERIFIED_REASON
    gated = bundle_entry(content, chart_paint_fallbacks=[{"id": "native-chart-revenue", "reason": reason}])
    assert gated["chartPaintFallbacks"] == [{"id": "native-chart-revenue", "reason": reason}]
    for entry, native in ((gated, 0), (bundle_entry(content), 1)):
        pages = parse_bundle_native_objects([entry], 1)
        (tmp_path / str(native)).mkdir()
        readback, deck = _convert(tmp_path / str(native), [svg], pages)
        receipt = readback["nativeObjects"]
        assert (receipt["offered"], receipt["native"], receipt["fellBack"]) == (1, native, 1 - native)
        charts = [shape for shape in deck.slides[0].shapes if getattr(shape, "has_chart", False)]
        assert len(charts) == native
        if not native:
            assert receipt["reasons"] == [{"page": 0, "id": "native-chart-revenue", "kind": "chart",
                                           "reason": reason}]
            assert "Q1" in _texts(deck.slides[0])


def test_chart_paint_fallback_wire_is_strict():
    from tests.unit.test_svg_native_objects_export_scene import _chart_object
    content = {"nativeObjects": {"schemaVersion": "native-objects/v1",
                                 "objects": [_chart_object("c1", "t"), _chart_object("c2", "t")]}}
    valid = bundle_entry(content, chart_paint_fallbacks=[{"id": "c1", "reason": "r"}, {"id": "c2", "reason": "r"}])
    assert parse_bundle_native_objects([valid], 1)[0].chart_paint_fallbacks == (("c1", "r"), ("c2", "r"))
    for bad in ([{"id": "missing", "reason": "r"}], [{"id": "c1", "reason": ""}],
                [{"id": "c2", "reason": "r"}, {"id": "c1", "reason": "r"}],
                [{"id": "c1", "reason": "r"}, {"id": "c1", "reason": "r"}], [{"id": "c1"}]):
        with pytest.raises(ValueError, match="svg_export_native_objects_invalid"):
            parse_bundle_native_objects([{**bundle_entry(content), "chartPaintFallbacks": bad}], 1)


def test_source_preserved_charts_keep_their_source_authority():
    page, content = _published()
    content = deepcopy(content)
    content["svgReceipt"]["source_preservation"] = {"whole_page_native_groups": [{"element_id": "native-chart-sales"}]}
    assert chart_paint_fallbacks(content, {}, page["svg_content"], slide_native_objects(content), revision=V5) == []


# --- earlier revisions unchanged ------------------------------------------------

@pytest.mark.parametrize("revision", [None, V4])
def test_earlier_revisions_skip_verification_and_keep_the_bundle(revision):
    from services import svg_chart_paint_verification as service
    from services.svg_export_operation import _native_object_entries
    page, content = _published(revision)
    slide = _slide(content, page["svg_content"])

    async def never(*args, **kwargs):
        raise AssertionError("no verification before v5")

    assert asyncio.run(service.deck_chart_paint_fallbacks(_Sessions({}), presentation=_presentation(revision),
        slides=[slide], remaining_seconds=lambda: 10_000, render=never)) is None
    assert chart_paint_fallbacks(content, {}, page["svg_content"], slide_native_objects(content),
                                 revision=revision) == []
    assert _native_object_entries([slide]) == ([bundle_entry(content)] if bundle_entry(content) else None)


def test_chart_proof_capture_alone_launches_with_frame_pacing_flags(monkeypatch):
    from utils import authored_renderer_session as session_module, slide_capture
    from utils.slide_capture_pipe import CHART_PROOF_CHROME_ARGS, _ChromePipe
    launched = []

    class FakePipe:
        def __init__(self, chrome, profile, **kwargs):
            launched.append(kwargs)

    seen = {}

    def fake_render(admitted, timeout, **kwargs):
        kwargs["chrome_pipe"]("chrome", "profile")
        seen.setdefault("calls", []).append(kwargs)
        return b"", {}

    monkeypatch.setattr(slide_capture, "_ChromePipe", FakePipe)
    import utils.slide_capture_chart_adapter as chart_adapter
    import utils.slide_capture_pipe_render as pipe_render
    monkeypatch.setattr(chart_adapter, "render_admitted_html_over_pipe", fake_render)
    monkeypatch.setattr(pipe_render, "render_admitted_html_over_pipe", fake_render)
    session_module.render_admitted_html_over_pipe(SimpleNamespace(), 5)
    session_module.render_admitted_html_over_pipe(SimpleNamespace(), 5, chart_witness_manifest={"fixture": 1})
    assert launched == [{}, {"extra_args": CHART_PROOF_CHROME_ARGS}]
    assert CHART_PROOF_CHROME_ARGS == ("--disable-frame-rate-limit", "--disable-gpu-vsync")
    with pytest.raises(ValueError, match="not admitted"):
        _ChromePipe("chrome", "profile", extra_args=("--no-sandbox",))


def test_session_chart_capture_uses_its_own_flagged_chrome(monkeypatch):
    from utils import authored_renderer_session as session_module, slide_capture
    from utils.slide_capture_pipe import CHART_PROOF_CHROME_ARGS
    launched = []

    class FakePipe:
        def __init__(self, chrome, profile, **kwargs):
            launched.append(kwargs)

        def command(self, *args, **kwargs):
            return {}

        def close(self):
            pass

    def fake_render(admitted, timeout, **kwargs):
        kwargs["chrome_pipe"]("chrome", "profile")
        return b"", {}

    import utils.slide_capture_chart_adapter as chart_adapter
    import utils.slide_capture_pipe_render as pipe_render
    monkeypatch.setattr(slide_capture, "_ChromePipe", FakePipe)
    monkeypatch.setattr(chart_adapter, "render_admitted_html_over_pipe", fake_render)
    monkeypatch.setattr(pipe_render, "render_admitted_html_over_pipe", fake_render)
    session = session_module.RendererChromiumSession()
    try:
        session.render(SimpleNamespace(), 5)
        session.render(SimpleNamespace(), 5, chart_witness_manifest={"fixture": 1})
        session.render(SimpleNamespace(), 5)
    finally:
        session.close()
    # One retained default Chromium for ordinary renders; the proof gets its own.
    assert launched == [{}, {"extra_args": CHART_PROOF_CHROME_ARGS}]


def test_verification_render_inputs_follow_the_published_page():
    from services import svg_chart_paint_verification as service
    page, content = _published()
    presentation = SimpleNamespace(theme={"svg": {"styleSnapshot": {SNAPSHOT_REVISION_KEY: V5}}})
    receipt = {**content["svgReceipt"], "readability_policy": {"fixture": 1}}
    slide = _slide({**content, "svgReceipt": receipt}, page["svg_content"])
    inputs = asyncio.run(service._render_inputs(None, presentation, slide))
    assert inputs["readability_policy"] == {"fixture": 1}
    assert {"width", "height"} <= set(inputs) and "materialized_svg" not in inputs
    raster = _slide({**content, "svgReceipt": {**receipt, "rasterApprovalPin": {}}}, page["svg_content"])
    assert asyncio.run(service._render_inputs(None, presentation, raster)) is None
