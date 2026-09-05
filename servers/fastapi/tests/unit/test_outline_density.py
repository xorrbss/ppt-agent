"""K1: the outline density receipt and the one bounded expansion call.

See ``docs/authored-quality-speed-programme-design-20260905.md`` section 8.
The receipt is emitted for every outline; the expansion runs at most once per
deck, only for the slides below the floor, only from a real source document,
and only when it produced MORE text than it replaced.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls import outline_density as density_mod

ZERO_WIDTH = "​"


def _run(coro):
    return asyncio.run(coro)


def _outline(*contents: str) -> PresentationOutlineModel:
    return PresentationOutlineModel(slides=[{"content": text} for text in contents])


# ---------------------------------------------------------------------------
# usable_chars / receipt
# ---------------------------------------------------------------------------


def test_zero_width_padding_never_counts_as_content():
    assert density_mod.usable_chars("abc" + ZERO_WIDTH * 600) == 3


def test_whitespace_is_collapsed_before_counting():
    assert density_mod.usable_chars("a\n\n   b\t c") == len("a b c")


def test_the_receipt_reports_every_slide_the_median_and_the_floor():
    receipt = density_mod.outline_density_receipt(
        _outline("a" * 100, "b" * 400, "c" * 700), floor=350
    )

    assert receipt["schemaVersion"] == density_mod.RECEIPT_SCHEMA_VERSION
    assert receipt["slideChars"] == [100, 400, 700]
    assert receipt["median"] == 400
    assert receipt["floor"] == 350
    assert receipt["belowFloor"] == [0]
    assert receipt["expanded"] is False


def test_with_the_floor_off_nothing_is_below_it_but_the_median_is_still_measured():
    receipt = density_mod.outline_density_receipt(_outline("a" * 10), floor=0)

    assert receipt["median"] == 10
    assert receipt["belowFloor"] == []


def test_thin_slide_indices_are_the_ones_under_the_floor():
    outline = _outline("a" * 100, "b" * 400, "c" * 20)

    assert density_mod.thin_slide_indices(outline, floor=350) == [0, 2]
    assert density_mod.thin_slide_indices(outline, floor=0) == []


# ---------------------------------------------------------------------------
# merge_expansion
# ---------------------------------------------------------------------------


def test_only_the_requested_slides_may_change_and_only_upward():
    outline = _outline("short", "b" * 400, "also short")
    merged = density_mod.merge_expansion(
        outline,
        {
            "slides": [
                {"index": 0, "content": "a much longer rewrite of the first slide"},
                {"index": 1, "content": "someone tried to rewrite a slide nobody asked about"},
                {"index": 2, "content": "tiny"},
            ]
        },
        indices=[0, 2],
    )

    assert merged is not None
    contents = [slide.content for slide in merged.slides]
    assert contents[0] == "a much longer rewrite of the first slide"
    assert contents[1] == "b" * 400  # not requested
    assert contents[2] == "also short"  # shorter than what it replaced


def test_an_expansion_that_changes_nothing_is_reported_as_nothing():
    outline = _outline("short")

    assert density_mod.merge_expansion(outline, {"slides": []}, indices=[0]) is None
    assert density_mod.merge_expansion(outline, "not json", indices=[0]) is None


# ---------------------------------------------------------------------------
# expand_thin_outline, against a fake provider
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


def test_one_call_rewrites_only_the_thin_slides(monkeypatch):
    calls: list = []
    _install_provider(
        monkeypatch,
        {"slides": [{"index": 1, "content": "expanded " * 60}]},
        calls=calls,
    )
    outline = _outline("a" * 400, "b" * 40, "c" * 400)

    expanded = _run(
        density_mod.expand_thin_outline(outline, source_text="source", floor=350)
    )

    assert len(calls) == 1
    contents = [slide.content for slide in expanded.slides]
    assert contents[0] == "a" * 400 and contents[2] == "c" * 400
    assert density_mod.usable_chars(contents[1]) > 40


def test_a_prompt_only_deck_never_calls_the_provider(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, {"slides": []}, calls=calls)
    outline = _outline("b" * 40)

    assert _run(density_mod.expand_thin_outline(outline, source_text="", floor=350)) is outline
    assert calls == []


def test_a_dense_outline_never_calls_the_provider(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, {"slides": []}, calls=calls)
    outline = _outline("a" * 700, "b" * 700)

    assert (
        _run(density_mod.expand_thin_outline(outline, source_text="src", floor=350))
        is outline
    )
    assert calls == []


def test_a_provider_failure_returns_the_original_outline(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, RuntimeError("429"), calls=calls)
    outline = _outline("b" * 40)

    assert (
        _run(density_mod.expand_thin_outline(outline, source_text="src", floor=350))
        is outline
    )
    assert len(calls) == 1


def test_the_source_document_is_bounded(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, {"slides": []}, calls=calls)

    _run(
        density_mod.expand_thin_outline(
            _outline("b" * 40), source_text="x" * (density_mod.MAX_SOURCE_CHARS + 5000),
            floor=350,
        )
    )

    prompt = str(calls[0]["messages"][1].content)
    assert "x" * density_mod.MAX_SOURCE_CHARS in prompt
    assert "x" * (density_mod.MAX_SOURCE_CHARS + 1) not in prompt


# ---------------------------------------------------------------------------
# The streamed-text entry points the outline stage uses
# ---------------------------------------------------------------------------


def _stream_text(*contents: str) -> str:
    return json.dumps({"slides": [{"content": text} for text in contents]})


def test_the_receipt_is_logged_for_every_outline(caplog):
    with caplog.at_level("INFO"):
        receipt = density_mod.log_outline_density(_stream_text("a" * 10), floor=0)

    assert receipt["median"] == 10
    assert "outline_density median=10 floor=0" in caplog.text


def test_a_malformed_outline_is_left_for_the_caller_to_report():
    assert density_mod.log_outline_density("{not json", floor=350) is None
    assert (
        _run(
            density_mod.apply_density_floor_to_text(
                "{not json", source_text="src", floor=350
            )
        )
        == "{not json"
    )


def test_a_dense_stream_is_returned_byte_for_byte(monkeypatch):
    calls: list = []
    _install_provider(monkeypatch, {"slides": []}, calls=calls)
    text = _stream_text("a" * 700, "b" * 700)

    assert (
        _run(density_mod.apply_density_floor_to_text(text, source_text="src", floor=350))
        == text
    )
    assert calls == []


def test_an_expanded_stream_keeps_every_other_key_of_the_provider_object(monkeypatch):
    _install_provider(
        monkeypatch, {"slides": [{"index": 0, "content": "expanded " * 60}]}, calls=[]
    )
    text = json.dumps({"title": "Deck", "slides": [{"content": "b" * 40}]})

    emitted = _run(
        density_mod.apply_density_floor_to_text(text, source_text="src", floor=350)
    )
    payload = json.loads(emitted)

    assert payload["title"] == "Deck"
    assert payload["slides"][0]["content"].startswith("expanded")


@pytest.mark.parametrize("floor", [0, 350])
def test_the_receipt_survives_an_outline_with_no_slides(floor):
    receipt = density_mod.outline_density_receipt(_outline(), floor=floor)

    assert receipt["median"] == 0 and receipt["slideChars"] == []


# ---------------------------------------------------------------------------
# Durable admission identity of the expansion call
# ---------------------------------------------------------------------------


def test_the_expansion_call_asks_for_its_own_semantic_slot_prefix(monkeypatch):
    """The one thing that keeps it from colliding inside the outline stage."""

    calls: list = []
    _install_provider(
        monkeypatch,
        {"slides": [{"index": 0, "content": "expanded " * 60}]},
        calls=calls,
    )

    _run(
        density_mod.expand_thin_outline(
            _outline("b" * 40), source_text="source", floor=350
        )
    )

    assert len(calls) == 1
    assert calls[0]["semantic_slot_prefix"] == "outline-expansion"
    assert density_mod.EXPANSION_SEMANTIC_SLOT_PREFIX == "outline-expansion"


def test_the_expansion_never_shares_a_durable_call_key_with_the_outline_stream():
    """Durable admission keys a call by (operation, stage, slide, slot).

    The expansion runs inside the SAME ``outline`` stage as the outline stream,
    so a shared logical key would make the second call look like a replay of a
    terminal receipt and be refused outright
    (``public_provider_terminal_replay_unsupported``).
    """

    from uuid import uuid4

    from services.provider_usage_instrumentation import ProviderUsageScope

    scope = ProviderUsageScope(
        job_id=uuid4(),
        operation="authored_generation",
        stage="outline",
        attempt_number=1,
    )
    expansion = f"{density_mod.EXPANSION_SEMANTIC_SLOT_PREFIX}.initial"
    others = ["stream.primary", "structured.initial", "structured.parse-repair"]

    keys = {scope.public_logical_call_key(slot) for slot in [expansion, *others]}
    ordinals = {
        scope.public_logical_call_ordinal(slot) for slot in [expansion, *others]
    }

    assert scope.public_logical_call_key(expansion) == "outline-expansion.initial"
    assert len(keys) == 1 + len(others)
    assert len(ordinals) == 1 + len(others)
