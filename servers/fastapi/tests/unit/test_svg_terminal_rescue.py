"""The SVG terminal rescue ladder: keep / complete-content fallback / placeholder.

Drives ``generation_core/drivers/svg/terminal.rescue_terminal_page`` directly,
with the real fallback builders, the real source binder and verifier, and the
real source render step (``render.attempt_render``); only the browser
(``render_svg_with_metrics``) and the checkpoint/PNG stores are faked.
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import xml.etree.ElementTree as ET

import pytest

from test_svg_source_layout import manifest_for
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import terminal
from services.generation_core.drivers.svg.fallback import FALLBACK_GROUP_ID
from services.generation_core.drivers.svg.source_fallback import line_slices, source_fallback_page
from services.generation_core.quality.page_failure_kind import (
    CONTENT, CONTENT_FAILURE_KINDS, LAYOUT, LAYOUT_FAILURE_KINDS, page_failure_kind, quality_keeps_content,
)
from services.generation_core.quality.terminal_policy import TerminalPolicy
from services.generation_core.runtime.profile import resolve_svg_runtime_profile
from services.svg_contract import SVG_NS, source_digest, validate_svg
from services.svg_presentation_prepare import GenerationContext
from services.svg_source_layout import source_pages, verify_source_receipt
from utils.hashing import sha256_hex

WIDTH, HEIGHT = 1280, 720
WHOLE_PAGE = {"digest": "sha256:style", "source_authoring_contract": {"version": "svg-source-whole-page/v1"}}


# --------------------------------------------------------------------------
# page_failure_kind routing table
# --------------------------------------------------------------------------

#: The kinds behind the 45-day deck failures, with the route each must take.
_OBSERVED = [
    ("text_overlap", LAYOUT), ("writing_policy_visible_symbols", LAYOUT), ("asset_icon_omitted", LAYOUT),
    ("required_text_missing", CONTENT), ("native_requested_table_missing", CONTENT),
    ("source_image_omitted", CONTENT),
]


@pytest.mark.parametrize("kind,expected", _OBSERVED)
def test_observed_failure_kinds_route_by_what_the_page_lost(kind, expected):
    assert page_failure_kind(kind) == expected


def test_unknown_and_malformed_kinds_are_content():
    assert page_failure_kind("a_kind_nobody_emits_yet") == CONTENT
    assert page_failure_kind(None) == CONTENT
    assert not CONTENT_FAILURE_KINDS & LAYOUT_FAILURE_KINDS


@pytest.mark.parametrize("quality,kept", [
    ({"passed": False, "failures": [{"kind": "text_overlap"}, {"kind": "small_body_text"}]}, True),
    ({"passed": False, "failures": [{"kind": "text_overlap"}, {"kind": "required_text_missing"}]}, False),
    ({"passed": False, "failures": [{"kind": "something_new"}]}, False),
    ({"passed": False, "failures": ["not-a-dict"]}, False),
    ({"passed": True, "failures": []}, True),
    ({"passed": False, "failures": []}, False),  # failed with no reason: unexplained
    (None, False),
])
def test_quality_keeps_content(quality, kept):
    assert quality_keeps_content(quality) is kept


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


def _quality(svg, *, passed=True, failures=()):
    return {"passed": passed, "verified": False, "failures": list(failures), "source_digest": source_digest(svg)}


class _Stores:
    def __init__(self):
        self.saved, self.pngs = {}, {}

    async def checkpoint(self, _job, index, *, payload=None):
        if payload is not None:
            self.saved[index] = payload
        return self.saved.get(index, {})

    def write_png(self, png):
        digest = sha256_hex(png)
        self.pngs[digest] = png
        return {"png_sha256": digest, "png_path": f"svg/{digest}.png"}

    def read_png(self, payload):
        return self.pngs.get(payload.get("png_sha256"))


def _renderer(monkeypatch, *, fail_markers=(), failures=()):
    """Fake browser: renders any SVG unless it contains one of ``fail_markers``."""

    async def render(svg, **_kwargs):
        if any(marker in svg for marker in fail_markers):
            raise ValueError("svg_render_failed")
        return SimpleNamespace(png=b"png:" + svg.encode(), quality=_quality(
            svg, passed=not failures, failures=failures))

    mock = AsyncMock(side_effect=render)
    monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", mock)
    return mock


def _ctx(*, source_reflow=False, profile=None, manifest=None, snapshot=None, planned=None, count=3):
    return GenerationContext(
        source_reflow=source_reflow, raster_reconstruction=False, raster_analysis=None, raster_pin=None,
        source_document=manifest, source_units=source_pages(manifest) if manifest is not None else None,
        canonical_assets=False, canvas=SimpleNamespace(width_px=WIDTH, height_px=HEIGHT), physical_kwargs={},
        snapshot=snapshot if snapshot is not None else {}, source_font_profile=None, count=count,
        roles=["content"] * count, manifests=[{}] * count, planned=planned or [{}] * count,
        source_manifest=None, source_pointer=None, asset_context=None, asset_revision=None, service=None,
        semaphore=asyncio.Semaphore(count), render_semaphore=asyncio.Semaphore(count),
        runtime_profile=profile or resolve_svg_runtime_profile(source_reflow=source_reflow))


def _request():
    return SimpleNamespace(instructions=None, _document_canvas=None, writing_policy=None, strategy="svg",
                           aspect_ratio="16:9", content="deck")


def _error(index=1, code="svg_repair_budget_exhausted"):
    return AuthoredStageError(slide_index=index, stage="render", code=code, retryable=False)


def _rescue(ctx, stores, payload, *, index=1, content="제목\n- 첫째 항목\n- 둘째 항목", error=None):
    return asyncio.run(terminal.rescue_terminal_page(
        ctx=ctx, request=_request(), index=index, content=content, payload=payload,
        error=error or _error(index), job=None, checkpoint=stores.checkpoint, write_png=stores.write_png,
        read_png=stores.read_png, presentation_id=None, async_session_maker=None))


def _rendered_payload(stores, svg, quality, *, cleared=False):
    payload = {"schema_version": "svg-slide-checkpoint/v1", "source_format": "svg", "input_digest": "in",
               "attempts": [{"kind": "provider", "receipt": {}}], "svg_content": None if cleared else svg,
               "last_valid_svg": svg, "source_digest": sha256_hex(svg.encode()), "quality": quality}
    payload.update(stores.write_png(b"png:" + svg.encode()))
    return payload


_AUTHORED = (f'<svg xmlns="{SVG_NS}" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'
             '<text x="10" y="40" font-family="Pretendard" font-size="20" fill="#111111">authored</text></svg>')


# --------------------------------------------------------------------------
# ordinary deck: a / b / c
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cleared", [False, True])
def test_a_layout_failure_keeps_the_last_render_flagged(monkeypatch, cleared):
    renderer = _renderer(monkeypatch)
    stores = _Stores()
    payload = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED, passed=False,
        failures=[{"kind": "text_overlap", "pairs": []}]), cleared=cleared)

    rescued, png = _rescue(_ctx(), stores, payload)

    assert rescued["svg_content"] == _AUTHORED
    assert png == b"png:" + _AUTHORED.encode()
    assert rescued["terminal"] == {"index": 1, "reason": "svg:svg_repair_budget_exhausted",
                                   "remakeAttempted": False, "remakeOutcome": "verbatim_flag_only"}
    renderer.assert_not_awaited()


def test_a_a_page_whose_png_is_gone_is_not_kept(monkeypatch):
    _renderer(monkeypatch)
    stores = _Stores()
    payload = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED, passed=False,
        failures=[{"kind": "text_overlap"}]))
    stores.pngs.clear()

    rescued, _png = _rescue(_ctx(), stores, payload)

    assert rescued["terminal"]["remakeOutcome"] == "svg_fallback_page"


def test_framed_template_rescue_keeps_only_an_existing_intact_page(monkeypatch):
    from models.svg_template_frame import FrameRect, SvgTemplateFrame, SvgTemplateFrameElement, SvgTemplateFontAsset
    from services.svg_canvas import svg_canvas_for_aspect_ratio
    from services.svg_master_furniture import master_furniture_receipt
    from services.svg_templates.frame_composition import adapt_frame
    from services.svg_templates.frame_fonts import artifact_digest, build_font_manifest, bytes_digest
    from services.svg_trusted_fonts import FONT_DIRECTORY
    from services.svg_templates.generation import (
        compose_template_page, template_page_context, validate_template_publication,
    )
    from utils.hashing import canonical_json_digest

    renderer = _renderer(monkeypatch)
    stores = _Stores()
    canvas = svg_canvas_for_aspect_ratio("16:9")
    font_bytes = (FONT_DIRECTORY / "Pretendard-Regular.ttf").read_bytes()
    font = SvgTemplateFontAsset(asset_id="pretendard", digest=bytes_digest(font_bytes),
        family="Pretendard", face="Regular", weight=400, data_base64=base64.b64encode(font_bytes).decode())
    element = SvgTemplateFrameElement(
        element_id="header", source_part="slide1.xml", source_shape_id="1",
        source_xml_digest="sha256:" + "a" * 64, kind="rect", layer="foreground",
        bounds=FrameRect(x=0, y=0, width=1280, height=20),
        svg=f'<rect xmlns="{SVG_NS}" id="svg-system-template-header" x="0" y="0" width="1280" height="20" fill="#123456"/>',
    )
    frame = SvgTemplateFrame(
        frame_id="frame-1", source_page=0, source_digest="sha256:" + "b" * 64,
        source_canvas=canvas, elements=(element,),
        body_region=FrameRect(x=64, y=64, width=1152, height=592),
        font_manifest=build_font_manifest((font,)), digest="",
    )
    frame = frame.model_copy(update={"digest": artifact_digest(frame)})
    adapted = adapt_frame(frame, canvas)
    selection = {
        "schema_version": "svg-template-selection/v1", "mode": "template-redesign",
        "template_pin": {"templateId": "test", "revision": 1, "digest": "sha256:" + "c" * 64},
        "frame_map": {"2": "frame-1"}, "package": {"frames": [frame.model_dump(mode="json")], "body_evidence": [],
                                                 "font_manifest": frame.font_manifest.model_dump(mode="json")},
        "adaptations": {"frame-1": adapted.model_dump(mode="json")},
    }
    selection["digest"] = canonical_json_digest(selection)
    snapshot = {"svg_template": selection}
    ctx = _ctx(snapshot=snapshot)
    content = "template body"
    context = template_page_context(snapshot, 1, 3, content)
    svg = compose_template_page(snapshot, f'<svg xmlns="{SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720"><rect id="body-rect" x="128" y="128" width="200" height="100" fill="#123456"/><text id="body-text" x="140" y="180" font-family="Pretendard" font-weight="400" font-size="20" fill="#FFFFFF">body</text></svg>', context)
    quality = _quality(svg, passed=False, failures=[{"kind": "text_overlap"}])
    quality.update(measurement_complete=True, template_preservation={
        "page_context_digest": context["digest"], "frame_digest": context["frame_digest"],
        "adaptation_digest": context["adaptation_digest"],
        "svg_digest": canonical_json_digest({"svg": svg}), "body_paint_measured": True,
    })
    payload = _rendered_payload(stores, svg, quality, cleared=True)
    payload.update(svg_template_page=context, system_furniture=master_furniture_receipt(
        svg, snapshot, index=1, n=3, width=1280, height=720, template_page_context=context))
    rescued, png = _rescue(ctx, stores, payload, content=content)
    assert rescued["terminal"]["remakeOutcome"] == "verbatim_flag_only"
    assert rescued["svg_content"] == svg and png == b"png:" + svg.encode()
    assert rescued["svg_template_page"] == context
    assert rescued["quality"]["template_preservation"] == quality["template_preservation"]
    renderer.assert_not_awaited()

    for key in ("svg_template_page", "system_furniture", "quality"):
        invalid = deepcopy(payload)
        invalid.pop(key)
        assert _rescue(ctx, stores, invalid, content=content) is None
    invalid = deepcopy(payload)
    invalid["svg_template_page"]["page_count"] = 4
    assert _rescue(ctx, stores, invalid, content=content) is None
    assert _rescue(ctx, stores, {"attempts": []}, content=content) is None
    renderer.assert_not_awaited()
    with pytest.raises(ValueError):
        validate_template_publication(snapshot, [], SimpleNamespace(slides=[]))

    ordinary, _png = _rescue(_ctx(), stores, {"attempts": []}, content=content)
    assert ordinary["terminal"]["remakeOutcome"] == "svg_fallback_page"
    assert renderer.await_count == 1


@pytest.mark.parametrize("payload_kind", ["content_failure", "never_rendered", "unbound_digest"])
def test_b_content_loss_or_no_render_gets_the_complete_outline_fallback(monkeypatch, payload_kind):
    _renderer(monkeypatch)
    stores = _Stores()
    if payload_kind == "never_rendered":
        payload = {"schema_version": "svg-slide-checkpoint/v1", "input_digest": "in", "attempts": []}
    else:
        payload = _rendered_payload(stores, _AUTHORED, _quality(_AUTHORED, passed=False,
            failures=[{"kind": "text_overlap"}, {"kind": "required_text_missing", "count": 1}]))
        if payload_kind == "unbound_digest":
            payload["quality"]["failures"] = [{"kind": "text_overlap"}]
            payload["source_digest"] = "0" * 64
    bullets = [f"항목 {i} " + "긴 설명 문장 " * 12 for i in range(25)]
    content = "분기 보고\n" + "\n".join("- " + bullet for bullet in bullets)

    rescued, png = _rescue(_ctx(), stores, payload, content=content)

    assert rescued["terminal"] == {"index": 1, "reason": "author:attempts_exhausted",
                                   "remakeAttempted": False, "remakeOutcome": "svg_fallback_page"}
    assert FALLBACK_GROUP_ID in rescued["svg_content"]
    assert png is not None and sha256_hex(png) == rescued["png_sha256"]
    assert rescued["source_digest"] == sha256_hex(rescued["svg_content"].encode())
    assert rescued["input_digest"] == "in" and rescued["terminal_error"] == "svg_repair_budget_exhausted"
    shown = "".join("".join(node.itertext()) for node in ET.fromstring(rescued["svg_content"]).iter(
        f"{{{SVG_NS}}}text"))
    shown = "".join(shown.split())
    for bullet in bullets:
        assert "".join(bullet.split()) in shown  # nothing truncated


def test_c_unrenderable_fallback_publishes_the_failure_placeholder(monkeypatch):
    _renderer(monkeypatch, fail_markers=("• ",))  # every outline fallback has a bullet
    stores = _Stores()

    rescued, png = _rescue(_ctx(), stores, {"input_digest": "in", "attempts": []},
                           content="회의 결과 요약\n- 본문")

    assert rescued["terminal"] == {"index": 1, "reason": "author:placeholder",
                                   "remakeAttempted": False, "remakeOutcome": "svg_failure_placeholder"}
    assert "회의 결과 요약" in rescued["svg_content"]
    assert png is not None and stores.saved[1] is rescued


def test_c_the_deck_fails_only_when_even_the_placeholder_cannot_render(monkeypatch):
    _renderer(monkeypatch, fail_markers=("<svg",))
    assert _rescue(_ctx(), _Stores(), {"attempts": []}) is None


def test_exact_reconstruction_still_fails_closed(monkeypatch):
    renderer = _renderer(monkeypatch)
    profile = resolve_svg_runtime_profile(source_reflow=False, raster_reconstruction=True)
    assert profile.terminal_policy is TerminalPolicy.FAIL

    assert _rescue(_ctx(profile=profile), _Stores(), {"attempts": []}) is None
    renderer.assert_not_awaited()


def test_a_non_exhaustion_error_is_never_rescued(monkeypatch):
    _renderer(monkeypatch)
    # An integrity code (store I/O, fence or revision): a substitute page cannot answer it.
    assert _rescue(_ctx(), _Stores(), {"attempts": []},
                   error=_error(code="svg_asset_screen_derivative_failed")) is None


@pytest.mark.parametrize("attempts,reason", [(2, "validated_candidate_unavailable"),
                                             (3, "validated_adaptation_exhausted")])
def test_quality_recovery_deck_is_rescued_after_adaptation_is_exhausted(monkeypatch, attempts, reason):
    _renderer(monkeypatch)
    monkeypatch.setattr("services.svg_authoring_profile.read_quality_recovery_policy",
                        lambda _snapshot: {"version": "test-policy"})
    stores = _Stores()
    reservation = {"status": "reserved", "candidate_id": "candidate"}
    payload = {"input_digest": "in", "attempts": [{"kind": "provider", "receipt": {}}] * attempts,
               "quality_recovery_fallback": reservation}

    rescued, _png = _rescue(_ctx(), stores, payload)

    assert rescued["terminal"]["remakeOutcome"] == "svg_fallback_page"
    assert rescued["quality_recovery_fallback"] == {"status": "blocked", "reason": reason,
                                                    "prior_reservation": reservation}


# --------------------------------------------------------------------------
# source-reflow deck: a / b / c
# --------------------------------------------------------------------------

_LONG = "가나다라 마바사 아자차카 타파하 " * 50 + "끝"
_SOURCE_BLOCKS = [
    ("text", {"text": "분기 실적 <요약> & \"검토\""}),
    ("text", {"text": "첫 줄\n둘째 줄  두 칸 띄움\n" + _LONG}),
    ("table", {"rows": [[{"text": "구분"}, {"text": "값"}],
                        [{"text": "매출 " * 25 + "합계"}, {"text": "12%"}]], "columnWidthsEmu": [300, 100]}),
    ("image", {"contentDigest": "sha256:image", "geometry": {"x": 0, "y": 0, "cx": 400, "cy": 300}}),
    ("shape", {"preset": "rect", "geometry": {"x": 100, "y": 100, "cx": 2000, "cy": 1000}}),
]


def _source_units(manifest):
    page = manifest.units[0]
    units = []
    for block in page.blocks:
        if block.kind == "text":
            units.append(block.payload["text"])
        elif block.kind == "table":
            units.extend(cell["text"] for row in block.payload["rows"] for cell in row)
    return units


def _source_ctx(manifest):
    planned = [{"source-slot-a": SimpleNamespace(occurrence_id="p0:3")}]
    return _ctx(source_reflow=True, manifest=manifest, snapshot=dict(WHOLE_PAGE), planned=planned, count=1)


def test_source_fallback_keeps_every_source_unit_verbatim_and_verifies():
    manifest = manifest_for(_SOURCE_BLOCKS)
    svg, receipt = source_fallback_page(manifest, 0, WHOLE_PAGE, image_slots={"p0:3": "source-slot-a"},
                                        width=WIDTH, height=HEIGHT, minimum_font_px=12)

    assert verify_source_receipt(svg, receipt, manifest, 0)
    texts = ["".join(node.itertext()) for node in ET.fromstring(svg).iter(f"{{{SVG_NS}}}text")]
    for unit in _source_units(manifest):
        assert unit in texts  # byte for byte, one text element per unit
    assert receipt["native_tables"] and "source-slot-a" in svg


def test_source_fallback_shrinks_and_flows_instead_of_dropping_text():
    manifest = manifest_for([("text", {"text": "제목"}), ("text", {"text": _LONG * 6})])
    svg, receipt = source_fallback_page(manifest, 0, WHOLE_PAGE, image_slots={}, width=WIDTH,
                                        height=HEIGHT, minimum_font_px=12)

    assert verify_source_receipt(svg, receipt, manifest, 0)
    body = [node for node in ET.fromstring(svg).iter(f"{{{SVG_NS}}}text") if "".join(node.itertext()) == _LONG * 6]
    assert len(body) == 1 and float(body[0].get("font-size")) < 20


@pytest.mark.parametrize("text", ["", "a", "한 줄", "줄\n바꿈\n", "  앞 공백", "x" * 500, "단어 " * 200])
def test_line_slices_concatenate_back_exactly(text):
    slices = line_slices(text, 10)
    assert "".join(text[start:end] for start, end in slices) == text
    assert all(end >= start for start, end in slices)


def test_source_fallback_refuses_charts_and_other_contracts():
    manifest = manifest_for([("text", {"text": "본문"})])
    with pytest.raises(ValueError, match="contract_unsupported"):
        source_fallback_page(manifest, 0, {"digest": "sha256:style"}, image_slots={}, width=WIDTH,
                             height=HEIGHT, minimum_font_px=12)


def _source_rendered_payload(stores, manifest, failures):
    svg, receipt = source_fallback_page(manifest, 0, WHOLE_PAGE, image_slots={"p0:3": "source-slot-a"},
                                        width=WIDTH, height=HEIGHT, minimum_font_px=12)
    payload = _rendered_payload(stores, svg, _quality(svg, passed=False, failures=failures))
    payload["source_preservation"] = receipt
    return payload


def test_source_a_layout_failure_keeps_the_verbatim_render(monkeypatch):
    renderer = _renderer(monkeypatch)
    manifest = manifest_for(_SOURCE_BLOCKS)
    stores = _Stores()
    payload = _source_rendered_payload(stores, manifest, [{"kind": "writing_policy_visible_symbols"}])

    rescued, png = _rescue(_source_ctx(manifest), stores, payload, index=0,
                           error=_error(0, "svg_source_quality_failed"))

    assert png is None  # a source page is read from the checkpoint store
    assert rescued["terminal"]["reason"] == "svg:svg_source_quality_failed"
    assert rescued["terminal"]["remakeOutcome"] == "verbatim_flag_only"
    renderer.assert_not_awaited()


@pytest.mark.parametrize("payload_kind", ["content_failure", "not_verbatim", "never_rendered"])
def test_source_b_content_loss_gets_the_verbatim_source_fallback(monkeypatch, payload_kind):
    _renderer(monkeypatch)
    manifest = manifest_for(_SOURCE_BLOCKS)
    stores = _Stores()
    if payload_kind == "never_rendered":
        payload = {"input_digest": "in", "attempts": [{"kind": "provider", "receipt": {}}]}
    else:
        payload = _source_rendered_payload(stores, manifest, [{"kind": "required_text_missing", "count": 1}]
                                           if payload_kind == "content_failure" else [{"kind": "text_overlap"}])
        if payload_kind == "not_verbatim":
            payload["source_preservation"] = {**payload["source_preservation"], "page_digest": "sha256:other"}

    rescued, png = _rescue(_source_ctx(manifest), stores, payload, index=0,
                           error=_error(0, "svg_source_quality_failed"))

    assert png is None
    assert rescued["terminal"] == {"index": 0, "reason": "author:source_verbatim_fallback",
                                   "remakeAttempted": False, "remakeOutcome": "svg_source_fallback_page"}
    assert verify_source_receipt(rescued["svg_content"], rescued["source_preservation"], manifest, 0)
    assert rescued["source_preservation"]["rendering_verified"] is True
    assert rescued["png_sha256"] in stores.pngs
    assert rescued["attempts"][-1]["kind"] == "local"
    texts = ["".join(node.itertext()) for node in ET.fromstring(rescued["svg_content"]).iter(
        f"{{{SVG_NS}}}text")]
    for unit in _source_units(manifest):
        assert unit in texts


def test_source_b_a_measured_failure_still_publishes_the_rendered_fallback(monkeypatch):
    _renderer(monkeypatch, failures=[{"kind": "small_body_text", "qid": "svg-3", "minimum_px": 12}])
    manifest = manifest_for(_SOURCE_BLOCKS)
    rescued, _png = _rescue(_source_ctx(manifest), _Stores(), {"attempts": []}, index=0,
                            error=_error(0, "svg_source_quality_failed"))

    assert rescued["terminal"]["remakeOutcome"] == "svg_source_fallback_page"
    assert rescued["quality"]["passed"] is False  # flagged, measured, and published
    assert rescued["source_preservation"]["rendering_verified"] is False


def test_source_c_a_page_the_fallback_cannot_lay_out_gets_the_placeholder(monkeypatch):
    _renderer(monkeypatch)
    manifest = manifest_for(_SOURCE_BLOCKS)
    ctx = replace(_source_ctx(manifest), snapshot={"digest": "sha256:style"})  # another source contract

    rescued, png = _rescue(ctx, _Stores(), {"attempts": []}, index=0, error=_error(0, "svg_source_quality_failed"))

    assert png is None
    assert rescued["terminal"]["remakeOutcome"] == "svg_failure_placeholder"
    assert "source_preservation" not in rescued
    assert "분기 실적" in "".join(ET.fromstring(rescued["svg_content"]).itertext())
    validate_svg(rescued["svg_content"], width=WIDTH, height=HEIGHT, allow_system_page_number=False,
                 source_manifest={})


def test_source_reflow_knob_off_keeps_failing(monkeypatch):
    monkeypatch.setenv("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", "false")
    _renderer(monkeypatch)
    manifest = manifest_for(_SOURCE_BLOCKS)
    assert _rescue(_source_ctx(manifest), _Stores(), {"attempts": []}, index=0,
                   error=_error(0, "svg_source_quality_failed")) is None


# --------------------------------------------------------------------------
# quality-recovery screen pages reach the ladder (quality_recovery.py)
# --------------------------------------------------------------------------


def _screen_harness(monkeypatch, *, candidate):
    from services.generation_core.drivers.svg import quality_recovery

    _renderer(monkeypatch)
    monkeypatch.setattr("services.svg_authoring_profile.read_quality_recovery_policy",
                        lambda _snapshot: {"version": "test-policy"})
    monkeypatch.setattr(quality_recovery, "ensure_recovery_authority", AsyncMock())
    monkeypatch.setattr(quality_recovery, "refresh_recovery_source_authority", AsyncMock())
    monkeypatch.setattr(quality_recovery, "recovery_pool", lambda *_args: [])
    monkeypatch.setattr("services.generation_core.drivers.svg.fallback.select_validated_recovery_candidate",
                        lambda *_args, **_kwargs: candidate)
    stores = _Stores()
    ctx = _ctx()
    ctx.quality_recovery_input_binding = None  # local evidence binding is covered elsewhere
    ctx.quality_recovery_source_evidence = None
    hooks = SimpleNamespace(checkpoint=stores.checkpoint, write_png=stores.write_png, read_png=stores.read_png,
                            async_session_maker=None)
    outline = SimpleNamespace(slides=[SimpleNamespace(content=f"제목 {i}\n- 항목 {i}") for i in range(3)])
    state = SimpleNamespace(fatal_error=None)
    return quality_recovery, stores, ctx, hooks, outline, state


def _pending(quality_recovery, index):
    payload = {"input_digest": "in", "attempts": [{"kind": "provider", "receipt": {}}] * 2}
    return quality_recovery.ScreenRecoveryPending(payload, _error(index, code="svg_validation_failed"))


def _recover(quality_recovery, ctx, outline, pages, state, hooks, attempt_loop):
    return asyncio.run(quality_recovery.recover_screen_pages(
        ctx, _request(), None, outline, None, pages, state, hooks, attempt_loop))


_CANDIDATE = {"id": "candidate", "revision": 1, "svg_digest": "s", "png_digest": "p",
              "selection_evidence": {}, "svg_content": "<svg/>"}


def test_a_screen_page_without_a_validated_candidate_is_rescued(monkeypatch):
    quality_recovery, stores, ctx, hooks, outline, state = _screen_harness(monkeypatch, candidate=None)
    kept = ({"svg_content": "<svg/>"}, b"png")
    attempt_loop = AsyncMock()
    pages = [kept, _pending(quality_recovery, 1), kept]

    result = _recover(quality_recovery, ctx, outline, pages, state, hooks, attempt_loop)

    rescued, png = result[1]
    assert png is not None
    assert rescued["terminal"]["remakeOutcome"] == "svg_fallback_page"
    assert rescued["quality_recovery_fallback"] == {"status": "blocked", "reason": "validated_candidate_unavailable",
                                                    "prior_reservation": None}
    assert result[0] is kept and result[2] is kept
    attempt_loop.assert_not_awaited()
    assert stores.saved[1]["terminal"] == rescued["terminal"]


def test_an_exhausted_adaptation_is_rescued_and_not_marked_adapted(monkeypatch):
    quality_recovery, stores, ctx, hooks, outline, state = _screen_harness(monkeypatch, candidate=_CANDIDATE)

    async def exhausted(_ctx, _request, _presentation_id, index, _content, payload, *_args):
        payload["attempts"].append({"kind": "provider", "receipt": {}})
        raise _error(index, code="svg_repair_budget_exhausted")

    result = _recover(quality_recovery, ctx, outline, [_pending(quality_recovery, 0)], state, hooks,
                      AsyncMock(side_effect=exhausted))

    rescued, _png = result[0]
    marker = rescued["quality_recovery_fallback"]
    assert marker["status"] == "blocked" and marker["reason"] == "validated_adaptation_exhausted"
    assert marker["prior_reservation"]["status"] == "reserved"
    assert marker["prior_reservation"]["candidate_id"] == "candidate"
    assert rescued["terminal"]["remakeOutcome"] == "svg_fallback_page"


def test_a_successful_adaptation_is_still_marked_adapted(monkeypatch):
    quality_recovery, _stores, ctx, hooks, outline, state = _screen_harness(monkeypatch, candidate=_CANDIDATE)

    async def adapted(_ctx, _request, _presentation_id, _index, _content, payload, *_args):
        return payload, b"png"

    result = _recover(quality_recovery, ctx, outline, [_pending(quality_recovery, 0)], state, hooks,
                      AsyncMock(side_effect=adapted))

    assert result[0][0]["quality_recovery_fallback"]["status"] == "adapted"
    assert "terminal" not in result[0][0]


@pytest.mark.parametrize("raised", [
    _error(0, code="svg_provider_attempt_uncertain"),
    ValueError("svg_validated_recovery_reservation_stale"),
])
def test_an_integrity_error_during_adaptation_stays_fatal(monkeypatch, raised):
    quality_recovery, stores, ctx, hooks, outline, state = _screen_harness(monkeypatch, candidate=_CANDIDATE)

    with pytest.raises(type(raised)) as failed:
        _recover(quality_recovery, ctx, outline, [_pending(quality_recovery, 0)], state, hooks,
                 AsyncMock(side_effect=raised))
    assert failed.value is raised
    assert "terminal" not in stores.saved.get(0, {})


def test_a_declined_rescue_keeps_the_screen_error(monkeypatch):
    quality_recovery, _stores, ctx, hooks, outline, state = _screen_harness(monkeypatch, candidate=None)
    _renderer(monkeypatch, fail_markers=("<svg",))  # neither fallback nor placeholder renders
    pending = _pending(quality_recovery, 0)

    with pytest.raises(AuthoredStageError) as failed:
        _recover(quality_recovery, ctx, outline, [pending], state, hooks, AsyncMock())
    assert failed.value is pending.error
