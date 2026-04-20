import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from enums.llm_provider import LLMProvider
from models.llm_message import LLMUserMessage
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from models.sql.slide import SlideModel
from services.llm_client import LLMClient
from templates.presentation_layout import PresentationLayoutModel, SlideLayoutModel
from utils.llm_calls.edit_slide import get_edited_slide_content
from utils.llm_calls.generate_presentation_structure import (
    generate_presentation_structure,
)
from utils.llm_calls.generate_slide_content import get_slide_content_from_type_and_outline
from utils.llm_calls.select_slide_type_on_edit import get_slide_layout_from_prompt


def _build_client() -> LLMClient:
    client = object.__new__(LLMClient)
    client.llm_provider = LLMProvider.OPENAI
    client.tool_calls_handler = SimpleNamespace(parse_tools=lambda tools: None)
    return client


def _build_layout() -> PresentationLayoutModel:
    return PresentationLayoutModel(
        name="Test Layout",
        slides=[
            SlideLayoutModel(
                id="layout-1",
                name="Title Slide",
                description="Single title layout",
                json_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            )
        ],
    )


def _build_slide() -> SlideModel:
    return SlideModel(
        presentation=uuid.uuid4(),
        layout_group="default",
        layout="layout-1",
        index=0,
        content={"title": "Current title"},
    )


def test_generate_structured_skips_validation_when_disabled():
    client = _build_client()
    call_messages = []

    async def fake_generate(**kwargs):
        call_messages.append(kwargs["messages"])
        return {"title": 123}

    client._generate_structured_once = AsyncMock(side_effect=fake_generate)

    response = asyncio.run(
        client.generate_structured(
            model="test-model",
            messages=[LLMUserMessage(content="Generate JSON")],
            response_format={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            validate_schema=False,
        )
    )

    assert response == {"title": 123}
    assert len(call_messages) == 1
    assert len(call_messages[0]) == 1


def test_generate_structured_retries_with_validation_feedback():
    client = _build_client()
    call_messages = []
    responses = [
        {"title": 123},
        {"title": "Valid title"},
    ]

    async def fake_generate(**kwargs):
        call_messages.append(kwargs["messages"])
        return responses[len(call_messages) - 1]

    client._generate_structured_once = AsyncMock(side_effect=fake_generate)

    with patch("services.llm_client.LOGGER.warning") as mock_warning:
        response = asyncio.run(
            client.generate_structured(
                model="test-model",
                messages=[LLMUserMessage(content="Generate JSON")],
                response_format={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                    "additionalProperties": False,
                },
                validate_schema=True,
            )
        )

    assert response == {"title": "Valid title"}
    assert len(call_messages) == 2
    feedback_message = call_messages[1][-1]
    assert isinstance(feedback_message, LLMUserMessage)
    assert "Validation errors:" in feedback_message.content
    assert "$.title" in feedback_message.content
    assert '"title": 123' in feedback_message.content
    mock_warning.assert_called_once()
    assert "$.title" in mock_warning.call_args.args[3]


def test_generate_structured_returns_last_invalid_response_at_max_loop_count():
    client = _build_client()
    call_messages = []
    responses = [
        {"title": 123},
        {"title": False},
        {"title": "should not be used"},
    ]

    async def fake_generate(**kwargs):
        call_messages.append(kwargs["messages"])
        return responses[len(call_messages) - 1]

    client._generate_structured_once = AsyncMock(side_effect=fake_generate)

    response = asyncio.run(
        client.generate_structured(
            model="test-model",
            messages=[LLMUserMessage(content="Generate JSON")],
            response_format={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            validate_schema=True,
            validate_schema_max_loop_count=2,
        )
    )

    assert response == {"title": False}
    assert len(call_messages) == 2


def test_generate_structured_uses_strict_schema_for_validation():
    client = _build_client()
    call_messages = []
    responses = [
        {"title": "Only title"},
        {"title": "Valid title", "subtitle": "Valid subtitle"},
    ]

    async def fake_generate(**kwargs):
        call_messages.append(kwargs["messages"])
        return responses[len(call_messages) - 1]

    client._generate_structured_once = AsyncMock(side_effect=fake_generate)

    response = asyncio.run(
        client.generate_structured(
            model="test-model",
            messages=[LLMUserMessage(content="Generate JSON")],
            response_format={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                },
            },
            strict=True,
            validate_schema=True,
        )
    )

    assert response == {"title": "Valid title", "subtitle": "Valid subtitle"}
    assert len(call_messages) == 2
    feedback_message = call_messages[1][-1]
    assert "required property" in feedback_message.content
    assert "subtitle" in feedback_message.content


def test_generate_structured_preserves_no_content_retries():
    client = _build_client()
    client._generate_structured_once = AsyncMock(
        side_effect=[None, None, {"title": "Valid title"}]
    )

    response = asyncio.run(
        client.generate_structured(
            model="test-model",
            messages=[LLMUserMessage(content="Generate JSON")],
            response_format={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
        )
    )

    assert response == {"title": "Valid title"}
    assert client._generate_structured_once.await_count == 3


def test_edit_slide_enables_schema_validation():
    mock_client = SimpleNamespace(
        generate_structured=AsyncMock(
            return_value={
                "title": "Edited title",
                "__speaker_note__": "x" * 120,
            }
        )
    )

    with patch("utils.llm_calls.edit_slide.LLMClient", return_value=mock_client), patch(
        "utils.llm_calls.edit_slide.get_model",
        return_value="test-model",
    ):
        response = asyncio.run(
            get_edited_slide_content(
                prompt="Update the title",
                slide=_build_slide(),
                language="English",
                slide_layout=_build_layout().slides[0],
            )
        )

    assert response["title"] == "Edited title"
    assert mock_client.generate_structured.await_args.kwargs["validate_schema"] is True


def test_generate_presentation_structure_enables_schema_validation():
    mock_client = SimpleNamespace(
        generate_structured=AsyncMock(return_value={"slides": [0]})
    )
    mock_response_model = SimpleNamespace(
        model_json_schema=lambda: {
            "type": "object",
            "properties": {
                "slides": {
                    "type": "array",
                    "items": {"type": "integer"},
                }
            },
            "required": ["slides"],
            "additionalProperties": False,
        }
    )

    with patch(
        "utils.llm_calls.generate_presentation_structure.LLMClient",
        return_value=mock_client,
    ), patch(
        "utils.llm_calls.generate_presentation_structure.get_model",
        return_value="test-model",
    ), patch(
        "utils.llm_calls.generate_presentation_structure.get_presentation_structure_model_with_n_slides",
        return_value=mock_response_model,
    ):
        response = asyncio.run(
            generate_presentation_structure(
                presentation_outline=PresentationOutlineModel(
                    slides=[SlideOutlineModel(content="Outline content")]
                ),
                presentation_layout=_build_layout(),
            )
        )

    assert response.slides == [0]
    assert mock_client.generate_structured.await_args.kwargs["validate_schema"] is True


def test_generate_slide_content_enables_schema_validation():
    mock_client = SimpleNamespace(
        generate_structured=AsyncMock(
            return_value={
                "title": "Slide title",
                "__speaker_note__": "x" * 120,
            }
        )
    )

    with patch(
        "utils.llm_calls.generate_slide_content.LLMClient",
        return_value=mock_client,
    ), patch(
        "utils.llm_calls.generate_slide_content.get_model",
        return_value="test-model",
    ):
        response = asyncio.run(
            get_slide_content_from_type_and_outline(
                slide_layout=_build_layout().slides[0],
                outline=SlideOutlineModel(content="Slide outline"),
                language="English",
            )
        )

    assert response["title"] == "Slide title"
    assert mock_client.generate_structured.await_args.kwargs["validate_schema"] is True


def test_select_slide_type_on_edit_enables_schema_validation():
    mock_client = SimpleNamespace(generate_structured=AsyncMock(return_value={"index": 0}))
    layout = _build_layout()

    with patch(
        "utils.llm_calls.select_slide_type_on_edit.LLMClient",
        return_value=mock_client,
    ), patch(
        "utils.llm_calls.select_slide_type_on_edit.get_model",
        return_value="test-model",
    ):
        response = asyncio.run(
            get_slide_layout_from_prompt(
                prompt="Use the first layout",
                layout=layout,
                slide=_build_slide(),
            )
        )

    assert response.id == "layout-1"
    assert mock_client.generate_structured.await_args.kwargs["validate_schema"] is True
