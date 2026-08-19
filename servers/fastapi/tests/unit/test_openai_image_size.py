import base64
from unittest.mock import AsyncMock, Mock, patch

import pytest

from services.image_generation_service import (
    OPENAI_IMAGE_DEFAULT_SIZE,
    ImageGenerationService,
    resolve_openai_image_size,
)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-image-2", "1536x1024"),
        ("gpt-image-1.5", "1536x1024"),
        ("dall-e-3", "1792x1024"),
    ],
)
def test_known_models_request_landscape_sizes(model, expected):
    width, height = (int(part) for part in expected.split("x"))

    assert resolve_openai_image_size(model) == expected
    assert width > height


def test_unknown_model_falls_back_to_square():
    assert resolve_openai_image_size("some-future-model") == OPENAI_IMAGE_DEFAULT_SIZE
    assert OPENAI_IMAGE_DEFAULT_SIZE == "1024x1024"


@pytest.mark.anyio
async def test_generate_image_openai_sends_landscape_size(monkeypatch, tmp_path):
    payload = base64.b64encode(b"png-bytes").decode()
    generate = AsyncMock(
        return_value=Mock(data=[Mock(b64_json=payload)]),
    )
    client = Mock(images=Mock(generate=generate))
    service = object.__new__(ImageGenerationService)
    service.settings = None

    with patch(
        "services.image_generation_service.get_openai_api_key_env",
        return_value="test-key",
    ), patch(
        "services.image_generation_service.AsyncOpenAI", return_value=client
    ):
        await service.generate_image_openai(
            "prompt", str(tmp_path), "gpt-image-2", "medium"
        )

    assert generate.await_args.kwargs["size"] == "1536x1024"
