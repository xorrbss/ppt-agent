"""Wires the canvas content budget into outline generation.

Pins docs/canvas-expansion-content-budget-spec-20260929.md (rev 2) sections
4-6: f=1 canvases (and no canvas budget at all) must produce today's exact
prompts, schema and buffering; f<1 canvases must scale the prompt targets,
ceiling phrase, schema cap and floor per the spec table, and add the
slide-count sentence only when the slide count is unconstrained.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime as real_datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.mocks.llm import content_event
from utils.get_dynamic_models import outline_model
from utils.llm_calls import generate_presentation_outlines as outline_module
from utils.llm_calls.outline_canvas_budget import budget_for_canvas

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "outline_prompt_baseline"

FLOOR_CHARS = 350

# f=1: 16:9 baseline and A4 landscape (different aspect ratio, same factor).
WIDE_16X9 = budget_for_canvas("wide-16x9", 1280, 720, 64, floor_chars=FLOOR_CHARS)
A4_LANDSCAPE = budget_for_canvas("a4-landscape", 1188, 840, 60, floor_chars=FLOOR_CHARS)
# f=0.51: the spec's worked 1:1 example.
SQUARE_1X1 = budget_for_canvas("square-1x1", 720, 720, 64, floor_chars=FLOOR_CHARS)


def _collect_async_chunks(generator) -> list[Any]:
    async def _collect():
        chunks = []
        async for chunk in generator:
            chunks.append(chunk)
        return chunks

    return asyncio.run(_collect())


class _FixedDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime(2026, 1, 1)


# ---------------------------------------------------------------------------
# Invariant 4: f=1 (and no budget at all) is byte-identical to today.
# ---------------------------------------------------------------------------


def test_system_prompt_is_byte_identical_at_f_equal_one_and_with_no_budget():
    expected = json.loads((FIXTURE_DIR / "system_prompts.json").read_text(encoding="utf-8"))
    for verbosity in (None, "concise", "standard", "text-heavy"):
        for include_title in (True, False):
            for include_toc in (False, True):
                key = f"{verbosity}|{include_title}|{include_toc}"
                baseline = expected[key]
                assert (
                    outline_module.get_system_prompt(verbosity, include_title, include_toc)
                    == baseline
                )
                for budget in (WIDE_16X9, A4_LANDSCAPE):
                    assert (
                        outline_module.get_system_prompt(
                            verbosity, include_title, include_toc, budget
                        )
                        == baseline
                    )


def test_user_prompt_is_byte_identical_at_f_equal_one_and_with_no_budget(monkeypatch):
    monkeypatch.setattr(outline_module, "datetime", _FixedDatetime)
    expected = json.loads((FIXTURE_DIR / "user_prompts.json").read_text(encoding="utf-8"))
    for n_slides in (None, 8):
        for language in (None, "English", "  "):
            key = f"{n_slides}|{language}"
            baseline = expected[key]
            kwargs = dict(
                content="Build quarterly strategy deck",
                n_slides=n_slides,
                language=language,
                additional_context=None,
                tone="professional",
                instructions=None,
            )
            assert outline_module.get_user_prompt(**kwargs) == baseline
            for budget in (WIDE_16X9, A4_LANDSCAPE):
                assert (
                    outline_module.get_user_prompt(**kwargs, canvas_budget=budget) == baseline
                )


def test_schema_max_length_is_unchanged_at_f_equal_one_and_with_no_budget():
    baseline = json.loads(
        (FIXTURE_DIR / "schema_max_length_8.json").read_text(encoding="utf-8")
    )
    for verbosity in (None, "concise", "standard", "text-heavy"):
        key = f"{verbosity}|8"
        expected_props = baseline[key]["$defs"]["SlideOutlineModelWithNSlides"]["properties"]
        expected_max_length = expected_props["content"]["maxLength"]
        expected_min_length = expected_props["content"]["minLength"]
        for budget in (None, WIDE_16X9, A4_LANDSCAPE):
            max_length = budget.schema_max_length[verbosity or "standard"] if budget else None
            model = outline_model(8, verbosity, max_length)
            schema = model.model_json_schema()
            props = schema["$defs"]["SlideOutlineModelWithNSlides"]["properties"]
            assert props["content"]["maxLength"] == expected_max_length
            assert props["content"]["minLength"] == expected_min_length
            assert schema["properties"]["slides"]["minItems"] == 8
            assert schema["properties"]["slides"]["maxItems"] == 8


def test_density_buffers_is_unaffected_by_canvas_budget():
    """f=1 canvas budgets must not change whether the stream buffers."""

    async def fake_stream_generate_events(_client, **kwargs):
        yield content_event('{"slides": [{"content": "## Intro\\nBullet"}]}')

    for budget in (None, WIDE_16X9, A4_LANDSCAPE):
        with patch.object(
            outline_module, "get_model", return_value="fake-model"
        ), patch.object(outline_module, "get_client", return_value=object()), patch.object(
            outline_module, "get_llm_config", return_value={}
        ), patch.object(
            outline_module, "get_generate_kwargs", side_effect=lambda **kwargs: kwargs
        ), patch.object(
            outline_module,
            "stream_generate_events",
            side_effect=fake_stream_generate_events,
        ), patch.object(
            outline_module, "log_outline_density"
        ) as fake_log:
            chunks = _collect_async_chunks(
                outline_module.generate_ppt_outline(
                    content="topic",
                    n_slides=1,
                    language="English",
                    canvas_budget=budget,
                )
            )
            # Streamed chunk-by-chunk (not buffered into one payload).
            assert chunks == ['{"slides": [{"content": "## Intro\\nBullet"}]}']
            assert fake_log.call_count == 1


# ---------------------------------------------------------------------------
# f<1: the spec's worked 1:1 (720x720, margin 64) example.
# ---------------------------------------------------------------------------


def test_square_1x1_factor_matches_the_spec_worked_example():
    assert SQUARE_1X1.factor == Decimal("0.51")
    assert SQUARE_1X1.floor == 179
    assert SQUARE_1X1.ceiling["standard"] == 357
    assert SQUARE_1X1.schema_max_length["standard"] == 510


def test_square_1x1_standard_system_prompt_states_the_scaled_budget():
    system = outline_module.get_system_prompt("standard", True, False, SQUARE_1X1)
    assert "about 77 words (Korean: about 255 characters) in 2 to 3 bullets" in system
    assert "at most about 357 characters" in system


def test_square_1x1_schema_cap_is_510_with_and_without_n_slides():
    for n_slides in (8, None):
        model = outline_model(n_slides, "standard", SQUARE_1X1.schema_max_length["standard"])
        schema = model.model_json_schema()
        props = schema["$defs"]["SlideOutlineModelWithNSlides"]["properties"]
        assert props["content"]["maxLength"] == 510
        if n_slides is None:
            assert "minItems" not in schema["properties"]["slides"]
            assert "maxItems" not in schema["properties"]["slides"]
        else:
            assert schema["properties"]["slides"]["minItems"] == 8
            assert schema["properties"]["slides"]["maxItems"] == 8


def test_square_1x1_user_prompt_adds_the_slide_count_sentence_only_when_n_slides_is_none():
    with_none = outline_module.get_user_prompt(
        content="topic", n_slides=None, language="English", canvas_budget=SQUARE_1X1
    )
    assert (
        "Choose the slide count so that each slide fits its budget; prefer "
        "more slides over denser slides." in with_none
    )

    with_n_slides = outline_module.get_user_prompt(
        content="topic", n_slides=8, language="English", canvas_budget=SQUARE_1X1
    )
    assert "Choose the slide count" not in with_n_slides

    no_budget = outline_module.get_user_prompt(
        content="topic", n_slides=None, language="English"
    )
    assert "Choose the slide count" not in no_budget

    f_equal_one = outline_module.get_user_prompt(
        content="topic", n_slides=None, language="English", canvas_budget=WIDE_16X9
    )
    assert "Choose the slide count" not in f_equal_one


def test_generate_ppt_outline_passes_the_scaled_floor_to_the_floor_step():
    """floor=179 (the 1:1 scaled floor), not 350, reaches the floor step."""

    calls: list = []

    async def fake_stream_generate_events(_client, **kwargs):
        yield content_event('{"slides": [{"content": "## Intro\\nBullet"}]}')

    async def fake_apply_density_floor_to_text(text, **kwargs):
        calls.append(kwargs)
        return text

    with patch.object(outline_module, "get_model", return_value="fake-model"), patch.object(
        outline_module, "get_client", return_value=object()
    ), patch.object(outline_module, "get_llm_config", return_value={}), patch.object(
        outline_module, "get_generate_kwargs", side_effect=lambda **kwargs: kwargs
    ), patch.object(
        outline_module, "stream_generate_events", side_effect=fake_stream_generate_events
    ), patch.object(
        outline_module,
        "apply_density_floor_to_text",
        side_effect=fake_apply_density_floor_to_text,
    ):
        _collect_async_chunks(
            outline_module.generate_ppt_outline(
                content="topic",
                n_slides=1,
                language="English",
                additional_context="a source document",
                canvas_budget=SQUARE_1X1,
            )
        )

    assert len(calls) == 1
    assert calls[0]["floor"] == 179


def test_generate_ppt_outline_condensation_still_needs_the_flag_even_when_the_canvas_scales(
    monkeypatch,
):
    """Invariant: the canvas scales the ceiling NUMBER, not the mechanism."""
    monkeypatch.delenv("OUTLINE_DENSITY_CEILING_ENABLED", raising=False)

    async def fake_stream_generate_events(_client, **kwargs):
        yield content_event('{"slides": [{"content": "## Intro\\nBullet"}]}')

    with patch.object(outline_module, "get_model", return_value="fake-model"), patch.object(
        outline_module, "get_client", return_value=object()
    ), patch.object(outline_module, "get_llm_config", return_value={}), patch.object(
        outline_module, "get_generate_kwargs", side_effect=lambda **kwargs: kwargs
    ), patch.object(
        outline_module, "stream_generate_events", side_effect=fake_stream_generate_events
    ), patch.object(
        outline_module, "apply_density_ceiling_to_text"
    ) as fake_condense, patch.object(
        outline_module, "log_outline_density"
    ) as fake_log:
        chunks = _collect_async_chunks(
            outline_module.generate_ppt_outline(
                content="topic",
                n_slides=1,
                language="English",
                canvas_budget=SQUARE_1X1,
            )
        )

    fake_condense.assert_not_called()
    # Not buffered: streamed as today, and the receipt still carries the
    # canvas-scaled ceiling even though condensation never ran.
    assert chunks == ['{"slides": [{"content": "## Intro\\nBullet"}]}']
    assert fake_log.call_args.kwargs["ceiling"] == 0
    assert fake_log.call_args.kwargs["canvas"] == "square-1x1"
    assert fake_log.call_args.kwargs["factor"] == 0.51


def test_receipt_carries_canvas_and_factor():
    from utils.llm_calls.outline_density import (
        RECEIPT_SCHEMA_VERSION,
        outline_density_receipt,
    )

    outline = type("Outline", (), {"slides": []})()
    receipt = outline_density_receipt(outline, canvas="square-1x1", factor=0.51)
    assert receipt["schemaVersion"] == RECEIPT_SCHEMA_VERSION == "outline-density/v2"
    assert receipt["canvas"] == "square-1x1"
    assert receipt["factor"] == 0.51

    default_receipt = outline_density_receipt(outline)
    assert default_receipt["canvas"] is None
    assert default_receipt["factor"] is None
