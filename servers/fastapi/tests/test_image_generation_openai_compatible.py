import pytest
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from services.image_generation_service import ImageGenerationService


class TestImageGenerationOpenAICompatible:
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture
    def mock_images_directory(self, tmp_path):
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        return str(images_dir)

    def test_get_image_gen_func_openai_compatible_selected(self, mock_images_directory):
        # get_image_gen_func first short-circuits on is_image_generation_disabled,
        # then checks every provider selector in order before reaching
        # is_openai_compatible_selected. All earlier selectors (including
        # nanobanana_pro and open_webui) must be False for the
        # openai_compatible branch to be reached.
        with patch.multiple(
            "services.image_generation_service",
            is_image_generation_disabled=Mock(return_value=False),
            is_pixabay_selected=Mock(return_value=False),
            is_pixels_selected=Mock(return_value=False),
            is_gemini_flash_selected=Mock(return_value=False),
            is_nanobanana_pro_selected=Mock(return_value=False),
            is_dalle3_selected=Mock(return_value=False),
            is_gpt_image_1_5_selected=Mock(return_value=False),
            is_gpt_image_2_selected=Mock(return_value=False),
            is_comfyui_selected=Mock(return_value=False),
            is_open_webui_selected=Mock(return_value=False),
            is_openai_compatible_selected=Mock(return_value=True),
        ):
            service = ImageGenerationService(mock_images_directory)
            assert (
                service.image_gen_func
                == service.generate_image_openai_compatible
            )

    @pytest.mark.anyio
    async def test_generate_image_openai_compatible_success(
        self, mock_images_directory
    ):
        service = ImageGenerationService(mock_images_directory)

        # Mock environment variables
        with patch(
            "services.image_generation_service.get_openai_compat_image_base_url_env",
            return_value="https://api.example.com/v1",
        ):
            with patch(
                "services.image_generation_service.get_openai_compat_image_api_key_env",
                return_value="sk-test-key",
            ):
                with patch(
                    "services.image_generation_service.get_openai_compat_image_model_env",
                    return_value="custom-model",
                ):
                    # Mock AsyncOpenAI client
                    with patch(
                        "services.image_generation_service.AsyncOpenAI"
                    ) as MockClient:
                        mock_client_instance = MockClient.return_value

                        # Mock response with b64_json
                        mock_response = Mock()
                        mock_data = Mock()
                        # Create a valid base64 image (1x1 pixel transparent png)
                        b64_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
                        mock_data.b64_json = b64_image
                        mock_data.url = None
                        mock_response.data = [mock_data]

                        mock_client_instance.images.generate = AsyncMock(
                            return_value=mock_response
                        )

                        image_path = await service.generate_image_openai_compatible(
                            "test prompt", mock_images_directory
                        )

                        # Verify client initialization
                        MockClient.assert_called_with(
                            base_url="https://api.example.com/v1", api_key="sk-test-key"
                        )

                        # Verify generate call (no response_format sent)
                        mock_client_instance.images.generate.assert_called_with(
                            model="custom-model",
                            prompt="test prompt",
                            n=1,
                            size="1024x1024",
                        )

                        # Verify file creation
                        assert os.path.exists(image_path)
                        assert image_path.startswith(mock_images_directory)

    @pytest.mark.anyio
    async def test_generate_image_openai_compatible_missing_config(
        self, mock_images_directory
    ):
        service = ImageGenerationService(mock_images_directory)

        with patch(
            "services.image_generation_service.get_openai_compat_image_base_url_env",
            return_value=None,
        ):
            with pytest.raises(
                ValueError,
                match="OPENAI_COMPAT_IMAGE_BASE_URL, OPENAI_COMPAT_IMAGE_API_KEY and OPENAI_COMPAT_IMAGE_MODEL must be set",
            ):
                await service.generate_image_openai_compatible(
                    "test prompt", mock_images_directory
                )

    @pytest.mark.anyio
    async def test_generate_image_openai_compatible_missing_model(
        self, mock_images_directory
    ):
        service = ImageGenerationService(mock_images_directory)

        with patch(
            "services.image_generation_service.get_openai_compat_image_base_url_env",
            return_value="https://api.example.com/v1",
        ):
            with patch(
                "services.image_generation_service.get_openai_compat_image_api_key_env",
                return_value="sk-test-key",
            ):
                with patch(
                    "services.image_generation_service.get_openai_compat_image_model_env",
                    return_value=None,
                ):
                    with pytest.raises(
                        ValueError,
                        match="OPENAI_COMPAT_IMAGE_BASE_URL, OPENAI_COMPAT_IMAGE_API_KEY and OPENAI_COMPAT_IMAGE_MODEL must be set",
                    ):
                        await service.generate_image_openai_compatible(
                            "test prompt", mock_images_directory
                        )

    @pytest.mark.anyio
    async def test_generate_image_openai_compatible_url_response(
        self, mock_images_directory
    ):
        """Providers that return a URL instead of b64_json should be handled correctly."""
        service = ImageGenerationService(mock_images_directory)

        with patch(
            "services.image_generation_service.get_openai_compat_image_base_url_env",
            return_value="https://api.example.com/v1",
        ):
            with patch(
                "services.image_generation_service.get_openai_compat_image_api_key_env",
                return_value="sk-test-key",
            ):
                with patch(
                    "services.image_generation_service.get_openai_compat_image_model_env",
                    return_value="custom-model",
                ):
                    with patch(
                        "services.image_generation_service.AsyncOpenAI"
                    ) as MockClient:
                        mock_client_instance = MockClient.return_value
                        mock_data = Mock()
                        mock_data.b64_json = None
                        mock_data.url = "https://api.example.com/images/result.png"
                        mock_response = Mock()
                        mock_response.data = [mock_data]
                        mock_client_instance.images.generate = AsyncMock(
                            return_value=mock_response
                        )

                        fake_image_bytes = b"\x89PNG\r\n\x1a\n"
                        mock_dl_resp = AsyncMock()
                        mock_dl_resp.status = 200
                        mock_dl_resp.read = AsyncMock(return_value=fake_image_bytes)
                        mock_session = AsyncMock()
                        mock_session.get = AsyncMock(return_value=mock_dl_resp)
                        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                        mock_session.__aexit__ = AsyncMock(return_value=False)

                        with patch(
                            "services.image_generation_service.aiohttp.ClientSession",
                            return_value=mock_session,
                        ):
                            image_path = await service.generate_image_openai_compatible(
                                "test prompt", mock_images_directory
                            )

                        assert os.path.exists(image_path)
                        assert image_path.startswith(mock_images_directory)

    @pytest.mark.anyio
    async def test_generate_image_openai_compatible_relative_url_response(
        self, mock_images_directory
    ):
        """Relative URLs in responses are resolved against the configured base URL origin."""
        service = ImageGenerationService(mock_images_directory)

        with patch(
            "services.image_generation_service.get_openai_compat_image_base_url_env",
            return_value="https://api.example.com/v1",
        ):
            with patch(
                "services.image_generation_service.get_openai_compat_image_api_key_env",
                return_value="sk-test-key",
            ):
                with patch(
                    "services.image_generation_service.get_openai_compat_image_model_env",
                    return_value="custom-model",
                ):
                    with patch(
                        "services.image_generation_service.AsyncOpenAI"
                    ) as MockClient:
                        mock_client_instance = MockClient.return_value
                        mock_data = Mock()
                        mock_data.b64_json = None
                        mock_data.url = "/images/result.png"
                        mock_response = Mock()
                        mock_response.data = [mock_data]
                        mock_client_instance.images.generate = AsyncMock(
                            return_value=mock_response
                        )

                        fake_image_bytes = b"\x89PNG\r\n\x1a\n"
                        mock_dl_resp = AsyncMock()
                        mock_dl_resp.status = 200
                        mock_dl_resp.read = AsyncMock(return_value=fake_image_bytes)
                        mock_session = AsyncMock()
                        mock_session.get = AsyncMock(return_value=mock_dl_resp)
                        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                        mock_session.__aexit__ = AsyncMock(return_value=False)

                        with patch(
                            "services.image_generation_service.aiohttp.ClientSession",
                            return_value=mock_session,
                        ) as MockSession:
                            image_path = await service.generate_image_openai_compatible(
                                "test prompt", mock_images_directory
                            )

                        # Relative URL must be resolved against the origin
                        call_args = mock_session.get.call_args
                        called_url = call_args[0][0]
                        assert called_url == "https://api.example.com/images/result.png"
                        assert os.path.exists(image_path)
