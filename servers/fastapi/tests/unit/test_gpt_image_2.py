from unittest.mock import AsyncMock, Mock, patch

import pytest

from services.image_generation_service import ImageGenerationService


def test_get_image_gen_func_selects_gpt_image_2(tmp_path):
    with patch.multiple(
        "services.image_generation_service",
        is_image_generation_disabled=Mock(return_value=False),
        is_pixabay_selected=Mock(return_value=False),
        is_pixels_selected=Mock(return_value=False),
        is_gemini_flash_selected=Mock(return_value=False),
        is_nanobanana_pro_selected=Mock(return_value=False),
        is_dalle3_selected=Mock(return_value=False),
        is_gpt_image_1_5_selected=Mock(return_value=False),
        is_gpt_image_2_selected=Mock(return_value=True),
    ):
        service = ImageGenerationService(str(tmp_path))

    assert service.image_gen_func == service.generate_image_openai_gpt_image_2


@pytest.mark.anyio
async def test_generate_gpt_image_2_uses_configured_quality(monkeypatch, tmp_path):
    monkeypatch.setenv("GPT_IMAGE_2_QUALITY", "high")
    service = object.__new__(ImageGenerationService)
    service.generate_image_openai = AsyncMock(return_value=str(tmp_path / "image.png"))

    result = await service.generate_image_openai_gpt_image_2("prompt", str(tmp_path))

    assert result == str(tmp_path / "image.png")
    service.generate_image_openai.assert_awaited_once_with(
        "prompt", str(tmp_path), "gpt-image-2", "high"
    )
