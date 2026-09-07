"""K1 ceiling: the per-verbosity cap and the one bounded condensation call.

Mirrors ``tests/unit/test_outline_density.py``'s coverage of
``expand_thin_outline`` for the ceiling/condense half added 2026-09-08.
"""

from __future__ import annotations

import asyncio
import json

from models.presentation_outline_model import PresentationOutlineModel
from utils.get_env_programme import is_outline_density_ceiling_enabled
from utils.llm_calls import outline_density as density_mod
from utils.llm_calls import outline_density_ceiling as ceiling_mod


def _run(coro):
    return asyncio.run(coro)


def _outline(*contents: str) -> PresentationOutlineModel:
    return PresentationOutlineModel(slides=[{"content": text} for text in contents])


# ---------------------------------------------------------------------------
# The getter
# ---------------------------------------------------------------------------


def test_the_ceiling_knob_defaults_off():
    assert is_outline_density_ceiling_enabled() is False


# ---------------------------------------------------------------------------
# ceiling_for_verbosity
# ---------------------------------------------------------------------------


def test_ceiling_for_verbosity_is_per_verbosity():
    assert density_mod.ceiling_for_verbosity("concise") == 400
    assert density_mod.ceiling_for_verbosity("standard") == 700
    assert density_mod.ceiling_for_verbosity("text-heavy") == 1100


def test_ceiling_for_verbosity_falls_back_to_standard():
    assert density_mod.ceiling_for_verbosity(None) == 700
    assert density_mod.ceiling_for_verbosity("") == 700
    assert density_mod.ceiling_for_verbosity("unknown") == 700


# ---------------------------------------------------------------------------
# above_ceiling_indices / receipt fields
# ---------------------------------------------------------------------------


def test_above_ceiling_indices_are_the_ones_over_the_cap():
    outline = _outline("a" * 100, "b" * 800, "c" * 200)

    assert ceiling_mod.above_ceiling_indices(outline, ceiling=700) == [1]
    assert ceiling_mod.above_ceiling_indices(outline, ceiling=0) == []


def test_the_receipt_reports_the_ceiling_and_above_ceiling_indices():
    receipt = density_mod.outline_density_receipt(
        _outline("a" * 100, "b" * 800), floor=0, ceiling=700
    )

    assert receipt["ceiling"] == 700
    assert receipt["aboveCeiling"] == [1]
    assert receipt["condensed"] is False
    # Existing keys are untouched by the extension.
    assert receipt["floor"] == 0
    assert receipt["belowFloor"] == []
    assert receipt["expanded"] is False


def test_the_receipt_defaults_ceiling_fields_when_unused():
    receipt = density_mod.outline_density_receipt(_outline("a" * 10), floor=0)

    assert receipt["ceiling"] == 0
    assert receipt["aboveCeiling"] == []
    assert receipt["condensed"] is False


# ---------------------------------------------------------------------------
# merge_condense
# ---------------------------------------------------------------------------


def test_only_the_requested_slides_may_change_and_only_downward():
    outline = _outline("a" * 800, "b" * 40, "c" * 800)
    merged = ceiling_mod.merge_condense(
        outline,
        {
            "slides": [
                {"index": 0, "content": "short cut of the first slide"},
                {"index": 1, "content": "someone tried to rewrite a slide nobody asked about"},
                {"index": 2, "content": "d" * 900},  # longer than what it replaced
            ]
        },
        indices=[0, 2],
    )

    assert merged is not None
    contents = [slide.content for slide in merged.slides]
    assert contents[0] == "short cut of the first slide"
    assert contents[1] == "b" * 40  # not requested
    assert contents[2] == "c" * 800  # longer than the original: rejected


def test_a_condensation_that_changes_nothing_is_reported_as_nothing():
    outline = _outline("a" * 800)

    assert ceiling_mod.merge_condense(outline, {"slides": []}, indices=[0]) is None
    assert ceiling_mod.merge_condense(outline, "not json", indices=[0]) is None


# ---------------------------------------------------------------------------
# enforce_floor_after_condense -- the merge rule's floor half
# ---------------------------------------------------------------------------


def test_a_shorter_slide_still_at_or_above_the_floor_is_kept():
    original = _outline("a" * 800)
    candidate = _outline("b" * 500)

    result = ceiling_mod.enforce_floor_after_condense(original, candidate, floor=350)

    assert result is candidate
    assert result.slides[0].content == "b" * 500


def test_a_shorter_slide_that_falls_below_the_floor_is_reverted():
    original = _outline("a" * 800)
    candidate = _outline("b" * 100)  # below the 350 floor

    result = ceiling_mod.enforce_floor_after_condense(original, candidate, floor=350)

    assert result is original
    assert result.slides[0].content == "a" * 800


def test_no_floor_in_force_means_no_reversion():
    original = _outline("a" * 800)
    candidate = _outline("b" * 10)

    result = ceiling_mod.enforce_floor_after_condense(original, candidate, floor=0)

    assert result is candidate


def test_identical_candidate_is_returned_unchanged():
    original = _outline("a" * 800)

    assert ceiling_mod.enforce_floor_after_condense(original, original, floor=350) is original


def test_a_mixed_batch_reverts_only_the_slide_that_violates_the_floor():
    original = _outline("a" * 800, "c" * 800)
    candidate = _outline("b" * 500, "d" * 100)  # first ok, second below floor

    result = ceiling_mod.enforce_floor_after_condense(original, candidate, floor=350)

    assert result is not original and result is not candidate
    assert result.slides[0].content == "b" * 500
    assert result.slides[1].content == "c" * 800


# ---------------------------------------------------------------------------
# condense_dense_outline, against a fake provider
# ---------------------------------------------------------------------------


def _install_provider(monkeypatch, response, *, calls: list):
    import llmai

    from utils import llm_config, llm_provider, llm_utils

    async def fake_generate(client, model, **kwargs):
        calls.append(kwargs)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(llmai, "get_client", lambda *a, **k: object())
    monkeypatch.setattr(llm_config, "get_llm_config", lambda *a, **k: None)
    monkeypatch.setattr(llm_config, "enforce_llm_egress_policy", lambda *a, **k: None)
    monkeypatch.setattr(llm_provider, "get_model", lambda *a, **k: "fake-model")
    monkeypatch.setattr(
        llm_utils, "generate_structured_with_schema_retries", fake_generate
    )


def test_one_call_rewrites_only_the_dense_slides(monkeypatch):
    calls: list = []
    _install_provider(
        monkeypatch,
        {"slides": [{"index": 1, "content": "condensed slide"}]},
        calls=calls,
    )
    outline = _outline("a" * 100, "b" * 900, "c" * 100)

    condensed = _run(
        ceiling_mod.condense_dense_outline(outline, verbosity="standard", ceiling=700)
    )

    assert len(calls) == 1
    contents = [slide.content for slide in condensed.slides]
    assert contents[0] == "a" * 100 and contents[2] == "c" * 100
    assert contents[1] == "condensed slide"


def test_a_slide_within_ceiling_never_calls_the_provider(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, {"slides": []}, calls=calls)
    outline = _outline("a" * 100)

    assert (
        _run(ceiling_mod.condense_dense_outline(outline, verbosity="standard", ceiling=700))
        is outline
    )
    assert calls == []


def test_ceiling_zero_never_calls_the_provider(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, {"slides": []}, calls=calls)
    outline = _outline("a" * 900)

    assert (
        _run(ceiling_mod.condense_dense_outline(outline, verbosity="standard", ceiling=0))
        is outline
    )
    assert calls == []


def test_a_provider_failure_returns_the_original_outline(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, RuntimeError("429"), calls=calls)
    outline = _outline("a" * 900)

    assert (
        _run(ceiling_mod.condense_dense_outline(outline, verbosity="standard", ceiling=700))
        is outline
    )
    assert len(calls) == 1


def test_the_condensation_call_asks_for_its_own_semantic_slot_prefix(monkeypatch):
    calls: list = []
    _install_provider(
        monkeypatch, {"slides": [{"index": 0, "content": "short"}]}, calls=calls
    )

    _run(
        ceiling_mod.condense_dense_outline(
            _outline("a" * 900), verbosity="standard", ceiling=700
        )
    )

    assert len(calls) == 1
    assert calls[0]["semantic_slot_prefix"] == "outline-condensation"
    assert ceiling_mod.CONDENSE_SEMANTIC_SLOT_PREFIX == "outline-condensation"


# ---------------------------------------------------------------------------
# apply_density_ceiling_to_text -- the streamed-text entry point
# ---------------------------------------------------------------------------


def _stream_text(*contents: str) -> str:
    return json.dumps({"slides": [{"content": text} for text in contents]})


def test_a_malformed_outline_is_returned_unchanged():
    assert (
        _run(
            ceiling_mod.apply_density_ceiling_to_text(
                "{not json", verbosity="standard", ceiling=700
            )
        )
        == "{not json"
    )


def test_a_slide_under_ceiling_is_returned_byte_for_byte(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, {"slides": []}, calls=calls)
    text = _stream_text("a" * 100, "b" * 200)

    assert (
        _run(
            ceiling_mod.apply_density_ceiling_to_text(
                text, verbosity="standard", ceiling=700
            )
        )
        == text
    )
    assert calls == []


def test_condensation_shorter_and_above_floor_is_accepted(monkeypatch):
    _install_provider(
        monkeypatch, {"slides": [{"index": 0, "content": "x" * 500}]}, calls=[]
    )
    text = json.dumps({"title": "Deck", "slides": [{"content": "y" * 900}]})

    emitted = _run(
        ceiling_mod.apply_density_ceiling_to_text(
            text, verbosity="standard", ceiling=700, floor=350
        )
    )
    payload = json.loads(emitted)

    assert payload["title"] == "Deck"
    assert payload["slides"][0]["content"] == "x" * 500


def test_condensation_below_floor_is_rejected_and_original_kept(monkeypatch):
    _install_provider(
        monkeypatch, {"slides": [{"index": 0, "content": "x" * 100}]}, calls=[]
    )
    text = _stream_text("y" * 900)

    emitted = _run(
        ceiling_mod.apply_density_ceiling_to_text(
            text, verbosity="standard", ceiling=700, floor=350
        )
    )

    assert emitted == text


def test_condensation_longer_than_original_is_rejected(monkeypatch):
    _install_provider(
        monkeypatch, {"slides": [{"index": 0, "content": "x" * 950}]}, calls=[]
    )
    text = _stream_text("y" * 900)

    emitted = _run(
        ceiling_mod.apply_density_ceiling_to_text(
            text, verbosity="standard", ceiling=700
        )
    )

    assert emitted == text
