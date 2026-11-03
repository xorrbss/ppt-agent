import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.presentation_outline_model import PresentationOutlineModel
from tests.mocks.llm import content_event
from utils.llm_calls import generate_presentation_outlines as outline_module


def _collect_async_chunks(generator) -> list[Any]:
    async def _collect():
        chunks = []
        async for chunk in generator:
            chunks.append(chunk)
        return chunks

    return asyncio.run(_collect())


def test_get_user_prompt_uses_autodetect_defaults():
    prompt = outline_module.get_user_prompt(
        content="Build quarterly strategy deck",
        n_slides=None,
        language="  ",
        additional_context=None,
        tone="professional",
        instructions=None,
    )

    assert "Number of Slides: auto-detect" in prompt
    assert "Language: auto-detect" in prompt
    assert "Tone: professional" in prompt
    assert "Context: None" in prompt


def test_generate_ppt_outline_streams_json_chunks_and_keeps_schema_shape():
    captured_kwargs = {}

    async def is_disconnected():
        return False

    async def fake_stream_generate_events(_client, **kwargs):
        captured_kwargs.update(kwargs)
        yield content_event('{"slides": [')
        yield content_event('{"content": "## Intro\\nBullet"}')
        yield content_event("]}")

    with patch.object(outline_module, "get_model", return_value="fake-model"), patch.object(
        outline_module, "get_client", return_value=object()
    ), patch.object(outline_module, "get_llm_config", return_value={}), patch.object(
        outline_module,
        "get_generate_kwargs",
        side_effect=lambda **kwargs: kwargs,
    ), patch.object(
        outline_module, "stream_generate_events", side_effect=fake_stream_generate_events
    ):
        chunks = _collect_async_chunks(
            outline_module.generate_ppt_outline(
                content="topic",
                n_slides=1,
                language="English",
                disconnect_checker=is_disconnected,
            )
        )

    parsed = json.loads("".join(chunks))
    validated = PresentationOutlineModel.model_validate(parsed)
    assert len(validated.slides) == 1
    assert validated.slides[0].content.startswith("## Intro")
    assert captured_kwargs["disconnect_checker"] is is_disconnected
    assert captured_kwargs["inactivity_timeout_seconds"] == 120
    # 2026-09-05: the outline no longer forces low reasoning; with no
    # CODEX_REASONING_EFFORT configured the provider default applies.
    assert "extra_body" not in captured_kwargs


def test_generate_ppt_outline_buffers_and_condenses_when_the_ceiling_is_on(monkeypatch):
    """OUTLINE_DENSITY_CEILING_ENABLED buffers the stream through the ceiling seam.

    Mirrors the floor's own buffering rule: a rewrite has to replace text the
    generator already yielded, so the whole outline is emitted in one chunk
    instead of streamed incrementally, and only the ceiling step runs when the
    floor is off (no source document to expand from).
    """
    monkeypatch.setenv("OUTLINE_DENSITY_CEILING_ENABLED", "true")
    calls: list = []

    async def fake_stream_generate_events(_client, **kwargs):
        yield content_event('{"slides": [{"content": "## Intro\\nBullet"}]}')

    async def fake_apply_density_ceiling_to_text(text, **kwargs):
        calls.append((text, kwargs))
        return "condensed-output"

    with patch.object(outline_module, "get_model", return_value="fake-model"), patch.object(
        outline_module, "get_client", return_value=object()
    ), patch.object(outline_module, "get_llm_config", return_value={}), patch.object(
        outline_module,
        "get_generate_kwargs",
        side_effect=lambda **kwargs: kwargs,
    ), patch.object(
        outline_module, "stream_generate_events", side_effect=fake_stream_generate_events
    ), patch.object(
        outline_module,
        "apply_density_ceiling_to_text",
        side_effect=fake_apply_density_ceiling_to_text,
    ):
        chunks = _collect_async_chunks(
            outline_module.generate_ppt_outline(
                content="topic",
                n_slides=1,
                language="English",
                verbosity="standard",
            )
        )

    assert chunks == ["condensed-output"]
    assert len(calls) == 1
    text, kwargs = calls[0]
    assert json.loads(text) == {"slides": [{"content": "## Intro\nBullet"}]}
    assert kwargs == {"verbosity": "standard", "ceiling": 700, "floor": 350}


def test_generate_ppt_outline_returns_http_exception_chunk_on_failure():
    async def failing_stream(_client, **_kwargs):
        raise TimeoutError("provider timed out")
        yield  # pragma: no cover

    with patch.object(outline_module, "get_model", return_value="fake-model"), patch.object(
        outline_module, "get_client", return_value=object()
    ), patch.object(
        outline_module,
        "get_llm_config",
        return_value={},
    ), patch.object(
        outline_module,
        "get_generate_kwargs",
        side_effect=lambda **kwargs: kwargs,
    ), patch.object(
        outline_module,
        "stream_generate_events",
        side_effect=failing_stream,
    ), patch.object(
        outline_module,
        "handle_llm_client_exceptions",
        return_value=HTTPException(status_code=408, detail="LLM timeout"),
    ):
        chunks = _collect_async_chunks(
            outline_module.generate_ppt_outline(
                content="topic",
                n_slides=1,
                language="English",
            )
        )

    assert len(chunks) == 1
    assert isinstance(chunks[0], HTTPException)
    assert chunks[0].status_code == 408


def test_generate_ppt_outline_raises_non_http_control_failure():
    class PublicControlFailure(RuntimeError):
        code = "public_provider_pricing_unavailable"
        retryable = False

    failure = PublicControlFailure("public_provider_pricing_required")

    async def failing_stream(_client, **_kwargs):
        raise failure
        yield  # pragma: no cover

    with patch.object(outline_module, "get_model", return_value="fake-model"), patch.object(
        outline_module, "get_client", return_value=object()
    ), patch.object(
        outline_module,
        "get_llm_config",
        return_value={},
    ), patch.object(
        outline_module,
        "get_generate_kwargs",
        side_effect=lambda **kwargs: kwargs,
    ), patch.object(
        outline_module,
        "stream_generate_events",
        side_effect=failing_stream,
    ), patch.object(
        outline_module,
        "handle_llm_client_exceptions",
        return_value=failure,
    ):
        with pytest.raises(
            PublicControlFailure, match="public_provider_pricing_required"
        ):
            _collect_async_chunks(
                outline_module.generate_ppt_outline(
                    content="topic",
                    n_slides=1,
                    language="English",
                )
            )


def test_presentation_outline_model_schema_validation_rejects_invalid_ai_payload():
    invalid_payload = {"slides": [{"not_content": "missing expected key"}]}

    with pytest.raises(ValidationError):
        PresentationOutlineModel.model_validate(invalid_payload)


@pytest.mark.parametrize(
    ("verbosity", "floor", "bullets"),
    [
        ("concise", "at least about 80 words", "3 to 4 bullets"),
        ("standard", "at least about 150 words", "4 to 6 bullets"),
        (None, "at least about 150 words", "4 to 6 bullets"),
        ("text-heavy", "at least about 250 words", "5 to 7 bullets"),
    ],
)
def test_slide_text_has_a_content_floor_per_verbosity(verbosity, floor, bullets):
    """Each verbosity states a floor of concrete content, not a per-bullet cap.

    The 08-19 ceiling (at most N bullets of M characters) plus the 08-28 low
    reasoning effort collapsed the outline from ~600 to 128 characters per
    slide on the same prompt. With free composition back, the floor returns:
    every bullet carries one concrete fact, and padding is forbidden by name.
    """
    system = outline_module.get_system_prompt(verbosity)

    assert floor in system
    assert bullets in system
    assert "one concrete fact, figure, date, named example" in system
    assert "never padding or repetition to reach the length" in system
    budget_line = system.split("Each slide's body")[1].splitlines()[0]
    assert "at most" not in budget_line


def test_the_ceiling_off_by_default_prompt_is_byte_identical_to_before_the_knob():
    """OUTLINE_DENSITY_CEILING_ENABLED defaults off; the prompt must not move.

    Pins the exact wording the pre-knob prompt used, so a future edit to the
    ceiling branch cannot silently leak into the default (off) path.
    """
    system = outline_module.get_system_prompt("standard")

    assert (
        "Each slide's body is a bullet list of at least about 150 words "
        "(Korean: about 500 characters) in 4 to 6 bullets. Every bullet is a "
        "full statement that carries one concrete fact, figure, date, named "
        "example, or cause-and-effect detail, not a label or a heading. The "
        "body is those bullets and nothing else: no restatement, no "
        "adjectives for their own sake, no connective prose, and never "
        "padding or repetition to reach the length — when the material "
        "honestly has less to say, say less.\n"
    ) in system
    assert "at most" not in system
    assert "budget fits" not in system


@pytest.mark.parametrize(
    ("verbosity", "ceiling"),
    [("concise", 400), ("standard", 700), (None, 700), ("text-heavy", 1100)],
)
def test_the_ceiling_on_states_the_per_verbosity_cap_and_the_trade(
    monkeypatch, verbosity, ceiling
):
    monkeypatch.setenv("OUTLINE_DENSITY_CEILING_ENABLED", "true")

    system = outline_module.get_system_prompt(verbosity)

    assert f"and at most about {ceiling} characters" in system
    assert (
        "When the source has more to say than that budget fits, keep the "
        "load-bearing facts and drop the rest rather than compressing every "
        "bullet into a heading." in system
    )


@pytest.mark.parametrize(
    ("verbosity", "expected_body_px"),
    [("concise", 20), ("standard", 18), ("text-heavy", 16), (None, 18)],
)
def test_authored_type_scale_follows_deck_verbosity(
    monkeypatch, verbosity, expected_body_px
):
    """A deck that carries less text per slide may set that text larger.

    Verbosity reaches the authoring prompt only through Brand, so a deck that
    kept the 12px floor spent the room it saved on empty bands instead of type.

    Updated 2026-09-05 (design section G2): each verbosity now states a RAMP
    rather than one "N px or larger" floor, so the assertion moved from the
    floor sentence to the ramp's body tier. The ramp is the switchable half of
    the prompt bundle, so this test states the knob it is measuring.
    """
    from services.generation_core.authoring.author_slide import Brand, build_design_system

    monkeypatch.setenv("AUTHORED_PROMPT_BUNDLE_ENABLED", "true")
    brand = Brand(topic="t", verbosity=verbosity or "standard")

    assert f"body copy and bullets {expected_body_px}px" in build_design_system(brand)


def test_concise_type_scale_leaves_the_page_number_specification_alone(monkeypatch):
    """The page number is furniture, so it keeps its own 12px size."""
    from services.generation_core.authoring.author_slide import Brand, build_design_system

    monkeypatch.setenv("AUTHORED_PROMPT_BUNDLE_ENABLED", "true")
    design_system = build_design_system(Brand(topic="t", verbosity="concise"))

    assert "12px type (9pt equivalent)" in design_system
    # One floor for every verbosity now (was 15/13/12): the ramp, not the
    # floor, is what makes a concise deck set its text larger.
    assert "below 12px (9pt equivalent)" in design_system
    assert "below 15px" not in design_system


def test_title_slide_presenter_and_date_only_when_user_provided():
    prompt = outline_module.get_system_prompt(None, True, False)
    assert "only when the user's content, instructions or source documents state it" in prompt
    assert "leave that line out entirely" in prompt
    assert "Never write a placeholder such as [Presenter Name]" in prompt
    assert "never use today's date as the presentation date" in prompt
    assert "only when the user provided them" in prompt
    # The unconditional demand for a presenter/date line is gone.
    assert "Include presenter name in first slide." not in prompt
    assert "Title slide must only contain title, presenter name, date and overview." not in prompt


def test_svg_author_rules_forbid_invented_presenter_details():
    from services.generation_core.authoring.author_slide_svg import SVG_AUTHOR_RULES

    flat = " ".join(SVG_AUTHOR_RULES.split())
    assert "only when the slide content states it" in flat
    assert "bracketed placeholder such as [Presenter Name]" in flat
