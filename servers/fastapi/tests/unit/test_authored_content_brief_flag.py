"""Focused contracts for the authored content-brief rollout flag."""

import asyncio
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from services import presentation_generation_pipeline as presentation_generate
from models.generate_presentation_request import GeneratePresentationRequest
from utils.get_env_programme import is_authored_content_brief_enabled


def test_authored_content_brief_flag_defaults_on_and_accepts_false(monkeypatch):
    monkeypatch.delenv("AUTHORED_CONTENT_BRIEF_ENABLED", raising=False)
    assert is_authored_content_brief_enabled() is True

    monkeypatch.setenv("AUTHORED_CONTENT_BRIEF_ENABLED", "false")
    assert is_authored_content_brief_enabled() is False


def test_false_skips_brief_for_an_unnormalized_authored_request(
    monkeypatch,
    fake_async_session,
):
    """Durable/internal callers need not have normalized ``template`` yet."""

    monkeypatch.setenv("AUTHORED_CONTENT_BRIEF_ENABLED", "false")
    request = GeneratePresentationRequest(
        content="Generate an authored deck",
        n_slides=2,
        language="English",
        export_as="pptx",
        strategy="authored",
        template="korean-biz",
    )

    async def stop_after_brief_gate(*_args, **_kwargs):
        yield HTTPException(status_code=408, detail="stop after brief gate")

    with patch.object(
        presentation_generate.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_generation_context",
        new=AsyncMock(),
    ), patch.object(
        presentation_generate,
        "run_content_brief_with_checkpoint",
        new=AsyncMock(),
    ) as brief, patch.object(
        presentation_generate,
        "generate_ppt_outline",
        side_effect=stop_after_brief_gate,
    ), patch.object(
        presentation_generate.CONCURRENT_SERVICE,
        "run_task",
        new=Mock(),
    ):
        with pytest.raises(HTTPException, match="stop after brief gate"):
            asyncio.run(
                presentation_generate.generate_presentation_handler(
                    request=request,
                    presentation_id=uuid.uuid4(),
                    async_status=None,
                    sql_session=fake_async_session,
                )
            )

    brief.assert_not_awaited()
