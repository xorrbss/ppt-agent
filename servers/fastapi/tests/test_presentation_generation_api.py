import asyncio
import types
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.v1.ppt.endpoints.presentation_generate import generate_presentation_sync
from constants.presentation import MAX_NUMBER_OF_SLIDES
from models.generate_presentation_request import GeneratePresentationRequest
from services.presentation_generation_pipeline import check_if_api_request_is_valid


def _fake_request() -> types.SimpleNamespace:
    # Minimal stand-in for the FastAPI Request passed as request_http.
    # build_export_cookie_header only reads headers.get("cookie"),
    # cookies.get(...), and request.state.auth_username.
    return types.SimpleNamespace(
        headers={},
        cookies={},
        state=types.SimpleNamespace(),
    )


class FakeAsyncSession:
    async def get(self, *_args, **_kwargs):
        return None

    def add(self, *_args, **_kwargs):
        return None

    def add_all(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None


class TestPresentationGenerationAPI:
    def test_sync_route_refuses_svg_before_any_generation_work(self):
        request = GeneratePresentationRequest(
            content="Create a presentation about artificial intelligence",
            n_slides=5,
            language="English",
        )
        with patch(
            "services.presentation_generation_pipeline.check_if_api_request_is_valid",
            new=AsyncMock(),
        ) as mock_validate, patch(
            "services.presentation_generation_pipeline.generate_presentation_handler",
            new=AsyncMock(),
        ) as mock_handler:
            with pytest.raises(HTTPException) as caught:
                asyncio.run(generate_presentation_sync(_fake_request(), request, sql_session=FakeAsyncSession()))

        assert caught.value.status_code == 422
        assert caught.value.detail == "svg_generation_requires_async_route"
        mock_validate.assert_not_awaited()
        mock_handler.assert_not_awaited()

    @pytest.mark.parametrize("export_as", ["pdf", "pptx"])
    def test_sync_route_refuses_authored_with_the_removal_code(self, export_as):
        request = GeneratePresentationRequest(
            content="Create a presentation about artificial intelligence and machine learning",
            n_slides=5,
            language="English",
            export_as=export_as,
            template="korean-biz",
            strategy="authored",
        )

        with patch(
            "services.presentation_generation_pipeline.check_if_api_request_is_valid",
            new=AsyncMock(),
        ) as mock_validate, patch(
            "services.presentation_generation_pipeline.generate_presentation_handler",
            new=AsyncMock(),
        ) as mock_handler:
            with pytest.raises(HTTPException) as caught:
                asyncio.run(
                    generate_presentation_sync(
                        _fake_request(), request, sql_session=FakeAsyncSession()
                    )
                )

        assert caught.value.status_code == 422
        assert caught.value.detail == "authored_strategy_removed"
        mock_validate.assert_not_awaited()
        mock_handler.assert_not_awaited()

    def test_generate_presentation_with_no_content(self):
        with pytest.raises(ValidationError):
            GeneratePresentationRequest.model_validate(
                {
                    "n_slides": 5,
                    "language": "English",
                    "export_as": "pdf",
                    "template": "korean-biz",
                }
            )

    # Slide-count admission is shared by every generation entry point
    # (check_if_api_request_is_valid); the sync route no longer reaches it.
    def test_generate_presentation_with_n_slides_less_than_one(self):
        request = GeneratePresentationRequest(
            content="Create a presentation about artificial intelligence and machine learning",
            n_slides=0,
            language="English",
            export_as="pdf",
            template="korean-biz",
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(check_if_api_request_is_valid(request, FakeAsyncSession()))

        assert exc.value.status_code == 400
        assert exc.value.detail == "Number of slides must be greater than 0"

    def test_generate_presentation_accepts_maximum_slide_count(self):
        request = GeneratePresentationRequest(
            content="Create a long presentation",
            n_slides=MAX_NUMBER_OF_SLIDES,
            language="English",
            export_as="pptx",
            template="korean-biz",
        )

        (presentation_id,) = asyncio.run(check_if_api_request_is_valid(request, FakeAsyncSession()))

        assert isinstance(presentation_id, uuid.UUID)

    def test_generate_presentation_rejects_slide_count_above_maximum(self):
        request = GeneratePresentationRequest(
            content="Create a presentation that is too long",
            n_slides=MAX_NUMBER_OF_SLIDES + 1,
            language="English",
            export_as="pptx",
            template="korean-biz",
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(check_if_api_request_is_valid(request, FakeAsyncSession()))

        assert exc.value.status_code == 400
        assert (
            exc.value.detail
            == f"Number of slides cannot be greater than {MAX_NUMBER_OF_SLIDES}"
        )

    def test_generate_presentation_with_invalid_export_type(self):
        with pytest.raises(ValidationError):
            GeneratePresentationRequest.model_validate(
                {
                    "content": "Create a presentation about artificial intelligence and machine learning",
                    "n_slides": 5,
                    "language": "English",
                    "export_as": "invalid_type",
                    "template": "korean-biz",
                }
            )
