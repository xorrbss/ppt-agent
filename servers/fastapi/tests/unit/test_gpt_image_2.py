import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

import pytest

from services.image_generation_service import ImageGenerationService
from utils.runtime_settings import RuntimeSettings
from services.authored_icon_pack.provider import transparent_model_for, _quality_for


@pytest.mark.parametrize("model, method", [
    ("gpt-image-2", "generate_image_openai_gpt_image_2"),
    ("gpt-image-2.5-flare", "generate_image_openai_gpt_image_2_5"),
    ("gpt-image-2.5-sunburst", "generate_image_openai_gpt_image_2_5"),
])
def test_get_image_gen_func_selects_gpt_image_2(tmp_path, model, method):
    settings = RuntimeSettings.from_sources(environ={"IMAGE_PROVIDER": model})
    service = ImageGenerationService(str(tmp_path), settings=settings)
    assert service.image_gen_func == getattr(service, method)
    assert transparent_model_for(settings) == model


@pytest.mark.anyio
@pytest.mark.parametrize("model, quality_key, quality", [
    ("gpt-image-2", "GPT_IMAGE_2_QUALITY", "high"),
    *[(model, "GPT_IMAGE_2_5_QUALITY", quality)
      for model in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst")
      for quality in (None, "low", "medium", "high", "xhigh", "max", "auto")],
])
async def test_generate_gpt_image_2_uses_configured_quality(tmp_path, model, quality_key, quality):
    image_bytes = b"png-response-bytes"
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={
            "created": 0,
            "data": [{"b64_json": base64.b64encode(image_bytes).decode()}],
        })

    settings = RuntimeSettings.from_sources(environ={
        "IMAGE_PROVIDER": model, quality_key: quality, "OPENAI_API_KEY": "test-key",
    })
    service = ImageGenerationService(str(tmp_path), settings=settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        async with AsyncOpenAI(api_key="test-key", http_client=http_client) as client:
            with patch("services.image_generation_service.AsyncOpenAI", return_value=client):
                result = await service.image_gen_func("prompt", str(tmp_path))

    assert await asyncio.to_thread(Path(result).read_bytes) == image_bytes
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/images/generations"
    payload = json.loads(requests[0].content)
    assert payload == {
        "model": model, "prompt": "prompt", "n": 1,
        "quality": quality or "medium", "size": "1536x1024",
    }
    assert _quality_for(model, settings) == (quality or "medium")
