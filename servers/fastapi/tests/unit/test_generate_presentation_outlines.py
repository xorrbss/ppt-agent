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
        outline_module, "is_codex_selected", return_value=True
    ), patch.object(
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
    assert captured_kwargs["extra_body"] == {"reasoning": {"effort": "low"}}


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


def test_presentation_outline_model_schema_validation_rejects_invalid_ai_payload():
    invalid_payload = {"slides": [{"not_content": "missing expected key"}]}

    with pytest.raises(ValidationError):
        PresentationOutlineModel.model_validate(invalid_payload)


@pytest.mark.parametrize(
    ("verbosity", "bullets", "per_bullet"),
    [
        ("concise", "at most 3 bullets", "about 24 characters"),
        ("standard", "at most 5 bullets", "about 60 characters"),
        (None, "at most 5 bullets", "about 60 characters"),
        ("text-heavy", "at most 6 bullets", "about 100 characters"),
    ],
)
def test_slide_text_budget_is_a_ceiling_per_verbosity(verbosity, bullets, per_bullet):
    """Each verbosity caps slide text instead of setting a floor.

    The authored pipeline renders this outline text verbatim, so a floor here
    reached the slide as text no later stage could trim. The cap is expressed
    per bullet: a self-audited per-slide total made the model narrate its own
    corrections into the content field.
    """
    system = outline_module.get_system_prompt(verbosity)

    assert bullets in system
    assert per_bullet in system
    assert "The body is those bullets and nothing else." in system
    assert "at least ~" not in system


@pytest.mark.parametrize(
    ("verbosity", "expected"),
    [
        ("concise", "22px or larger"),
        ("standard", "18px or larger"),
        ("text-heavy", "at least 12px (9pt equivalent)"),
        (None, "18px or larger"),
    ],
)
def test_authored_type_scale_follows_deck_verbosity(verbosity, expected):
    """A deck that carries less text per slide may set that text larger.

    Verbosity reaches the authoring prompt only through Brand, so a deck that
    kept the 12px floor spent the room it saved on empty bands instead of type.
    """
    from utils.llm_calls.author_slide import Brand, build_design_system

    brand = Brand(topic="t", verbosity=verbosity or "standard")

    assert expected in build_design_system(brand)


def test_concise_type_scale_leaves_the_page_number_specification_alone():
    """The page number is furniture, so it keeps its own 12px size."""
    from utils.llm_calls.author_slide import Brand, build_design_system

    design_system = build_design_system(Brand(topic="t", verbosity="concise"))

    assert "12px type (9pt equivalent)" in design_system
    assert "below 15px (11pt equivalent)" in design_system
