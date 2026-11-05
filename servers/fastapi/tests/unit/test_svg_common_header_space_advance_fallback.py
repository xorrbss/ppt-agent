"""A header on a renderer whose space advance cannot be measured takes the sealed-constant path.

The probe (services.svg_common_header_space_advance) runs here against a
deterministic stand-in renderer, so these tests need no browser: the stand-in
draws every probe text at the bundled font's own width plus a chosen space
deviation, which lets each probe check pass or fail on purpose.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from services import svg_common_header_space_advance as space_advance
from services import svg_renderer
from services.svg_common_header import (
    MEASUREMENT_VERSION,
    SPACE_FALLBACK_KEY,
    SPACE_FALLBACK_VERSIONS,
    _digest,
    _line_evidence,
    common_header_digest,
    prepare_common_header,
    validate_common_header_binding,
)
from services.svg_common_header_v2 import MEASUREMENT_VERSION as V2_MEASUREMENT_VERSION, prepare_common_header_v2
from services.svg_text_advance import text_advance
from utils.hashing import canonical_json_digest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FROZEN = FIXTURES / "svg_common_header_frozen_measurements"
LIVE4 = FIXTURES / "svg_common_header_live4" / "failed_titles.json"
CANVAS = {"width": 1280, "height": 720}
IDENTITY = {"renderer_version": "stand-in", "platform": "test", "machine": "test",
            "chrome_runtime_digest": "sha256:" + "0" * 64}
IDENTITY = {**IDENTITY, "digest": canonical_json_digest(IDENTITY)}
# A Linux-like renderer: only a space between two Hangul syllables draws wider.
LINEAR_DELTAS_EM = {"hangul|hangul": 1.76 / 34, "hangul|other": 0.0, "other|hangul": 0.0, "other|other": 0.0}


def _frozen(name):
    return json.loads((FROZEN / name).read_text(encoding="utf-8"))


class StandInRenderer:
    """Draws the probe's text nodes; ``mode`` picks which probe check it breaks."""

    def __init__(self, mode="linear"):
        self.mode, self.calls = mode, 0

    async def __call__(self, svg, *, width, height, **_render_options):
        self.calls += 1
        if self.mode == "unavailable":
            raise ValueError("svg_renderer_unavailable")
        elements = [{"qid": "svg-0"}]
        for index, node in enumerate(ET.fromstring(svg), start=1):
            text, size = node.text, float(node.get("font-size"))
            drawn = text_advance(text, size, node.get("font-weight"), node.get("font-family"))
            for context in space_advance.space_contexts(text):
                drawn += LINEAR_DELTAS_EM[context] * size
                if self.mode == "nonlinear":
                    drawn += 0.5  # a fixed pixel per space does not scale with the size
            if self.mode == "glyph" and " " not in text:
                drawn += 0.01 * size * len(text)  # the renderer does not follow the font's glyphs
            elements.append({"qid": f"svg-{index}", "has_owned_text": True, "font_loaded": True,
                             "owned_text": text, "text_fragments": [{"width": drawn}]})
        return SimpleNamespace(quality={"measurement_complete": True, "browser_metrics": {"elements": elements}})


@pytest.fixture
def renderer(monkeypatch):
    """Install a stand-in renderer with an empty measurement cache; returns a mode setter."""
    monkeypatch.setattr(space_advance, "_measurements", {})
    monkeypatch.setattr(space_advance, "_failures", {})
    monkeypatch.setattr(space_advance, "renderer_identity", lambda: dict(IDENTITY))

    def install(mode="linear"):
        stand_in = StandInRenderer(mode)
        monkeypatch.setattr(svg_renderer, "render_svg_with_metrics", stand_in)
        return stand_in

    return install


def _v1_snapshot():
    # The options the frozen glyph/v2 fixture was prepared with.
    return {"tokens": {"heading_font": "Noto Sans KR"}, "common_header": {
        "alignment": "center", "eyebrow": {"enabled": True}, "subline": {"enabled": True},
        "divider": {"enabled": True}}}


def _v2_candidate(contract):
    fields = [{"node_id": field["node_id"], "role": field["role"], "value_ref": field["value_ref"],
               "box": field["box"], "font_family": field["font"]["family"], "font_weight": field["font"]["weight"],
               "font_size_px": field["font"]["size_px"], "line_height_px": field["line_height_px"],
               "baseline_offset_px": field["baseline_offset_px"], "max_lines": field["max_lines"],
               "alignment": field["alignment"], "optional": field["optional"],
               "dependent_node_ids": field["dependent_node_ids"]} for field in contract["fields"]]
    return {"candidate_id": contract["candidate_id"], "revision": contract["revision"],
            "svg": contract["svg_template"], "body_box": contract["body_box"],
            "design_intent": contract["design_intent"], "shared_style": contract["shared_style"], "fields": fields}


def _prepare_v1(slides):
    return prepare_common_header(_v1_snapshot(), {"slides": copy.deepcopy(slides)}, CANVAS)


def _prepare_v2(frozen):
    return prepare_common_header_v2(_v2_candidate(frozen["contract"]), {"slides": copy.deepcopy(frozen["slides"])},
                                    CANVAS, snapshot={})


def _validate_all(contract, bindings, slides):
    for index, (binding, slide) in enumerate(zip(bindings, slides, strict=True)):
        validate_common_header_binding(contract, binding, index=index, count=len(slides),
                                       content=slide["content"], title=slide["title"])


def _reseal(contract, bindings):
    """Re-digest a deliberately edited contract so only its provenance check can reject it."""
    contract = {**contract}
    contract.pop("digest")
    contract["digest"] = (common_header_digest(contract) if contract["version"] == "svg-common-header/v1"
                          else _digest(contract))
    resealed = []
    for binding in bindings:
        binding = {key: value for key, value in binding.items() if key != "digest"}
        binding["header_digest"] = contract["digest"]
        binding["digest"] = _digest(binding)
        resealed.append(binding)
    return contract, resealed


def _without(value, *keys):
    return {key: item for key, item in value.items() if key not in keys}


def test_a_measured_renderer_seals_glyph_v3_and_paint_v4_contracts(renderer):
    stand_in = renderer("linear")
    v1_frozen, v2_frozen = _frozen("v1_glyph_v2.json"), _frozen("v2_glyph_paint_v3.json")

    contract, bindings = _prepare_v1(v1_frozen["slides"])
    assert contract["measurement_version"] == MEASUREMENT_VERSION
    assert SPACE_FALLBACK_KEY not in contract
    assert contract["space_advance"]["renderer"] == IDENTITY
    assert {(face["family"], face["weight"]) for face in contract["space_advance"]["faces"]} == {
        ("Noto Sans KR", 800), ("Noto Sans KR", 700), ("Noto Sans KR", 400)}
    _validate_all(contract, bindings, v1_frozen["slides"])
    # The measured hangul|hangul deviation is what the line widths carry.
    face = next(face for face in contract["space_advance"]["faces"] if face["weight"] == 800)
    assert face["contexts"]["hangul|hangul"][0]["space_delta_em"] == pytest.approx(
        LINEAR_DELTAS_EM["hangul|hangul"], abs=1e-9)

    v2_contract, v2_bindings = _prepare_v2(v2_frozen)
    assert v2_contract["measurement_version"] == V2_MEASUREMENT_VERSION
    assert SPACE_FALLBACK_KEY not in v2_contract
    assert v2_contract["space_advance"]["renderer"] == IDENTITY
    _validate_all(v2_contract, v2_bindings, v2_frozen["slides"])
    # One probe render per face, run once for the whole process.
    assert stand_in.calls == len(space_advance.PROBE_FACES)


@pytest.mark.parametrize(("mode", "cause"), (
    ("nonlinear", "svg_common_header_space_probe_nonlinear:"),
    ("glyph", "svg_common_header_space_probe_glyph_nonconforming:"),
    ("unavailable", "svg_renderer_unavailable"),
))
def test_an_unmeasurable_probe_falls_back_to_the_sealed_constant_and_records_why(renderer, mode, cause):
    renderer(mode)
    v1_frozen, v2_frozen = _frozen("v1_glyph_v2.json"), _frozen("v2_glyph_paint_v3.json")

    contract, bindings = _prepare_v1(v1_frozen["slides"])
    assert contract["measurement_version"] == SPACE_FALLBACK_VERSIONS[MEASUREMENT_VERSION] == "svg-common-header-glyph/v2"
    assert "space_advance" not in contract
    fallback = contract[SPACE_FALLBACK_KEY]
    assert fallback["probe_version"] == space_advance.PROBE_VERSION
    assert fallback["renderer"] == IDENTITY
    assert fallback["reason"].startswith(space_advance.UNMEASURABLE + ":" + cause)
    _validate_all(contract, bindings, v1_frozen["slides"])
    # Apart from the sealed reason, the contract and its page bindings are the
    # ones the previous glyph/v2 code froze, byte for byte.
    assert _without(contract, SPACE_FALLBACK_KEY, "digest") == _without(v1_frozen["contract"], "digest")
    assert ([_without(binding, "header_digest", "digest") for binding in bindings]
            == [_without(binding, "header_digest", "digest") for binding in v1_frozen["bindings"]])

    v2_contract, v2_bindings = _prepare_v2(v2_frozen)
    assert v2_contract["measurement_version"] == "svg-common-header-glyph-paint/v3"
    assert "space_advance" not in v2_contract
    assert v2_contract[SPACE_FALLBACK_KEY]["reason"].startswith(space_advance.UNMEASURABLE + ":" + cause)
    _validate_all(v2_contract, v2_bindings, v2_frozen["slides"])
    assert _without(v2_contract, SPACE_FALLBACK_KEY, "digest") == _without(v2_frozen["contract"], "digest")
    assert ([_without(binding, "header_digest", "digest") for binding in v2_bindings]
            == [_without(binding, "header_digest", "digest") for binding in v2_frozen["bindings"]])


def test_an_unavailable_renderer_identity_falls_back_without_a_renderer(renderer, monkeypatch):
    renderer("linear")

    def unavailable():
        raise RuntimeError("chrome not found")

    monkeypatch.setattr(space_advance, "renderer_identity", unavailable)
    slides = _frozen("v1_glyph_v2.json")["slides"]
    contract, bindings = _prepare_v1(slides)
    assert contract["measurement_version"] == "svg-common-header-glyph/v2"
    assert contract[SPACE_FALLBACK_KEY] == {
        "probe_version": space_advance.PROBE_VERSION, "renderer": None,
        "reason": space_advance.UNMEASURABLE + ":renderer_identity_unavailable:RuntimeError"}
    _validate_all(contract, bindings, slides)


def test_a_header_prepared_on_an_event_loop_before_the_probe_takes_the_fallback(renderer):
    stand_in = renderer("linear")
    slides = _frozen("v1_glyph_v2.json")["slides"]

    async def admission():
        # A path that skipped ensure_space_advance_measurement(): the loop is
        # never blocked on a probe render, and the deck does not fail.
        missed = _prepare_v1(slides)
        assert stand_in.calls == 0
        await space_advance.ensure_space_advance_measurement()
        return missed, _prepare_v1(slides)

    (missed, missed_bindings), (measured, _measured_bindings) = asyncio.run(admission())
    assert missed["measurement_version"] == "svg-common-header-glyph/v2"
    assert missed[SPACE_FALLBACK_KEY]["reason"] == space_advance.UNMEASURED
    assert missed[SPACE_FALLBACK_KEY]["renderer"] == IDENTITY
    _validate_all(missed, missed_bindings, slides)
    assert measured["measurement_version"] == MEASUREMENT_VERSION


def test_a_failed_probe_is_reported_on_the_loop_and_retried_by_the_next_ensure(renderer):
    renderer("unavailable")
    slides = _frozen("v1_glyph_v2.json")["slides"]

    async def generation():
        await space_advance.ensure_space_advance_measurement()
        failed = _prepare_v1(slides)[0]
        renderer("linear")
        await space_advance.ensure_space_advance_measurement()
        return failed, _prepare_v1(slides)[0]

    failed, retried = asyncio.run(generation())
    assert failed[SPACE_FALLBACK_KEY]["reason"] == space_advance.UNMEASURABLE + ":svg_renderer_unavailable"
    assert retried["measurement_version"] == MEASUREMENT_VERSION


def test_a_rejected_face_falls_back_only_for_the_header_that_draws_it(renderer, monkeypatch):
    renderer("linear")
    slides = _frozen("v1_glyph_v2.json")["slides"]
    measured, _ = _prepare_v1(slides)
    assert measured["measurement_version"] == MEASUREMENT_VERSION
    measurement = space_advance._measurements[IDENTITY["digest"]]
    subline_key = next(key for key in measurement["faces"] if key[:2] == ("Noto Sans KR", 400))
    monkeypatch.setitem(space_advance._measurements, IDENTITY["digest"], {
        **measurement, "faces": {key: entry for key, entry in measurement["faces"].items() if key != subline_key},
        "rejected": {subline_key: "svg_common_header_space_probe_nonlinear:H가"}})

    contract, bindings = _prepare_v1(slides)
    assert contract["measurement_version"] == "svg-common-header-glyph/v2"
    assert contract[SPACE_FALLBACK_KEY]["reason"] == (
        space_advance.UNMEASURABLE + ":svg_common_header_space_probe_nonlinear:H가")
    _validate_all(contract, bindings, slides)
    # A header without a subline draws only measured faces.
    title_only, _ = prepare_common_header({"tokens": {"heading_font": "Noto Sans KR"}},
                                          {"slides": copy.deepcopy(slides)}, CANVAS)
    assert title_only["measurement_version"] == MEASUREMENT_VERSION


def test_stored_contracts_of_every_measurement_version_validate_without_a_measurement(renderer, monkeypatch):
    renderer("linear")
    v1_frozen, v2_frozen = _frozen("v1_glyph_v2.json"), _frozen("v2_glyph_paint_v3.json")
    measured_v1 = (*_prepare_v1(v1_frozen["slides"]), v1_frozen["slides"])
    measured_v2 = (*_prepare_v2(v2_frozen), v2_frozen["slides"])
    renderer("nonlinear")
    monkeypatch.setattr(space_advance, "_measurements", {})
    fallback_v1 = (*_prepare_v1(v1_frozen["slides"]), v1_frozen["slides"])
    fallback_v2 = (*_prepare_v2(v2_frozen), v2_frozen["slides"])
    # glyph/v1 differs from glyph/v2 only in measuring spaces with the font alone.
    glyph_v1_contract = {**_without(v1_frozen["contract"], "digest"), "measurement_version": "svg-common-header-glyph/v1"}
    glyph_v1_bindings = []
    for binding in v1_frozen["bindings"]:
        widths, boxes = _line_evidence(binding["lines"], glyph_v1_contract["font"], glyph_v1_contract["title_box"],
                                       glyph_v1_contract["baseline_offset_px"], glyph_v1_contract["line_height_px"],
                                       glyph_v1_contract["alignment"], "svg-common-header-glyph/v1")
        glyph_v1_bindings.append({**binding, "line_widths": widths, "glyph_boxes": boxes})
    glyph_v1_contract["digest"] = None
    glyph_v1 = (*_reseal(glyph_v1_contract, glyph_v1_bindings), v1_frozen["slides"])
    live4 = json.loads(LIVE4.read_text(encoding="utf-8"))["pages"]

    # Validation reads the sealed values; it never measures, so it holds on a
    # process that has no renderer at all.
    def no_renderer():
        raise AssertionError("validation must not identify or probe the renderer")

    monkeypatch.setattr(space_advance, "renderer_identity", no_renderer)
    stored = {"glyph/v1": glyph_v1, "glyph/v2": (v1_frozen["contract"], v1_frozen["bindings"], v1_frozen["slides"]),
              "glyph/v2+fallback": fallback_v1, "glyph/v3": measured_v1,
              "paint/v3": (v2_frozen["contract"], v2_frozen["bindings"], v2_frozen["slides"]),
              "paint/v3+fallback": fallback_v2, "paint/v4": measured_v2}
    assert [stored[name][0]["measurement_version"].rsplit("-", 1)[-1] for name in stored] == [
        "glyph/v1", "glyph/v2", "glyph/v2", "glyph/v3", "paint/v3", "paint/v3", "paint/v4"]
    for contract, bindings, slides in stored.values():
        _validate_all(contract, bindings, slides)
    for page in live4.values():
        assert page["contract"]["measurement_version"] == "svg-common-header-glyph-paint/v2"
        validate_common_header_binding(page["contract"], page["binding"], index=page["page_index"],
                                       count=page["binding"]["page_count"])


def test_provenance_cannot_be_moved_between_measurement_versions(renderer, monkeypatch):
    renderer("linear")
    v1_frozen, v2_frozen = _frozen("v1_glyph_v2.json"), _frozen("v2_glyph_paint_v3.json")
    measured, measured_bindings = _prepare_v1(v1_frozen["slides"])
    renderer("nonlinear")
    monkeypatch.setattr(space_advance, "_measurements", {})
    fallback, fallback_bindings = _prepare_v1(v1_frozen["slides"])
    record = fallback[SPACE_FALLBACK_KEY]
    live4 = json.loads(LIVE4.read_text(encoding="utf-8"))["pages"]["25"]

    tampered = [
        # A measured contract that also claims a fallback.
        _reseal({**measured, SPACE_FALLBACK_KEY: record}, measured_bindings),
        # A fallback reason on a version that never had one.
        _reseal({**fallback, "measurement_version": "svg-common-header-glyph/v1"}, fallback_bindings),
        _reseal({**live4["contract"], SPACE_FALLBACK_KEY: record}, [live4["binding"]]),
        # A malformed record.
        _reseal({**fallback, SPACE_FALLBACK_KEY: {**record, "reason": "svg_common_header_space_advance_fine"}},
                fallback_bindings),
        _reseal({**fallback, SPACE_FALLBACK_KEY: {**record, "reason": record["reason"] + "\nforged"}},
                fallback_bindings),
        _reseal({**fallback, SPACE_FALLBACK_KEY: {**record, "space_delta_em": 0.05}}, fallback_bindings),
        _reseal({**fallback, SPACE_FALLBACK_KEY: {**record, "renderer": {**IDENTITY, "platform": "win32"}}},
                fallback_bindings),
        # The sealed constant contract cannot carry a measurement as well.
        _reseal({**fallback, "space_advance": measured["space_advance"]}, fallback_bindings),
    ]
    # Resealing alone is accepted, so each rejection below is the provenance check.
    for contract, bindings in (_reseal(measured, measured_bindings), _reseal(fallback, fallback_bindings)):
        validate_common_header_binding(contract, bindings[0])
    for contract, bindings in tampered:
        with pytest.raises(ValueError, match="svg_common_header_digest_mismatch"):
            validate_common_header_binding(contract, bindings[0])
    v2_contract, v2_bindings = _prepare_v2(v2_frozen)
    assert v2_contract[SPACE_FALLBACK_KEY]["reason"].startswith(space_advance.UNMEASURABLE)
    contract, bindings = _reseal({**v2_contract, "measurement_version": "svg-common-header-glyph-paint/v2"},
                                 v2_bindings)
    with pytest.raises(ValueError, match="svg_common_header_digest_mismatch"):
        validate_common_header_binding(contract, bindings[0])


def test_a_synchronous_caller_probes_a_failing_renderer_once(renderer):
    stand_in = renderer("unavailable")
    slides = _frozen("v1_glyph_v2.json")["slides"]
    for _ in range(3):
        assert _prepare_v1(slides)[0]["measurement_version"] == "svg-common-header-glyph/v2"
    assert stand_in.calls == 1
    # An explicit ensure is the retry.
    asyncio.run(space_advance.ensure_space_advance_measurement())
    assert stand_in.calls == 2
