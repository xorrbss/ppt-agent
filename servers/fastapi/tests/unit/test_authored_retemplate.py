import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from api.v1.ppt.endpoints.presentation import (
    build_authored_retemplate_generation_request,
)
from models.presentation_from_template import RetemplatePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from utils.authored_styles import AuthoredStyle
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION


def _source_presentation() -> PresentationModel:
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(
                content="2026년 매출은 125억 원이며 전년 대비 18% 증가했다."
            ),
            SlideOutlineModel(
                content="핵심 고객은 공공기관 12곳이며 계약 갱신율은 94%다."
            ),
        ]
    )
    return PresentationModel(
        content="사업 성과 보고",
        n_slides=2,
        language="Korean",
        title="AI 전환 자본",
        file_paths=["source.pdf"],
        outlines=outline.model_dump(mode="json"),
        instructions="수치와 고유명사를 정확하게 유지",
        tone="professional",
        verbosity="standard",
        include_table_of_contents=True,
        include_title_slide=True,
        web_search=True,
        theme={"mode": "authored", "style": "default", "primary": "#245BE7"},
        mode="authored",
    )


def _request(style: str = "signal") -> RetemplatePresentationRequest:
    return RetemplatePresentationRequest(authored_style=style, vision_qa=True)


def _http_request() -> Request:
    return Request({"type": "http", "headers": []})


def test_retemplate_builds_new_authored_deck_from_saved_llm_manuscript(
    fake_async_session,
):
    source = _source_presentation()
    original_snapshot = source.model_dump(mode="json")
    fake_async_session._get_results[source.id] = source

    generation_request = asyncio.run(
        build_authored_retemplate_generation_request(
            source.id,
            _request(),
            fake_async_session,
        )
    )

    # The source deck is read, never rewritten.
    assert source.model_dump(mode="json") == original_snapshot

    assert generation_request.template == "authored"
    assert generation_request.authored_style == "signal"
    assert generation_request.vision_qa is True
    assert generation_request.slides_markdown == [
        slide.content for slide in source.get_presentation_outline().slides
    ]
    assert generation_request.instructions == source.instructions
    assert generation_request.include_title_slide is False
    assert generation_request.include_table_of_contents is False
    assert generation_request.n_slides is None


def test_retemplate_exact_snapshots_fresh_html_before_queue(fake_async_session):
    source = _source_presentation()
    fake_async_session._get_results[source.id] = source
    slides = [
        SlideModel(
            presentation=source.id,
            layout_group="authored",
            layout="authored:content",
            index=index,
            content={"__authored__": True, "image": "images/stale.png"},
            properties={"image": "images/stale.png"},
            html_content=f"<section>latest web HTML {index}</section>",
        )
        for index in range(2)
    ]

    class _SlideResult:
        def scalars(self):
            return self

        def all(self):
            return slides

    fake_async_session.execute = AsyncMock(return_value=_SlideResult())
    exact_style = AuthoredStyle(
        id="exact-private",
        name="Exact",
        description="Private exact test style",
        preview_bg="#FFFFFF",
        preview_accent="#111111",
        brief="Preserve source pixels.",
        reference_mode=EXACT_RECONSTRUCTION,
    )
    snapshot = AsyncMock(return_value="a" * 32)

    with (
        patch(
            "api.v1.ppt.endpoints.presentation.load_authored_styles",
            return_value=[exact_style],
        ),
        patch(
            "api.v1.ppt.endpoints.presentation.validate_exact_reconstruction_style"
        ),
        patch(
            "api.v1.ppt.endpoints.presentation.create_exact_source_snapshot",
            new=snapshot,
        ),
    ):
        generation_request = asyncio.run(
            build_authored_retemplate_generation_request(
                source.id,
                _request("exact-private"),
                fake_async_session,
            )
        )

    snapshot.assert_awaited_once_with(
        "exact-private",
        slides,
        expected_slide_count=2,
    )
    assert generation_request._authored_exact_snapshot_id == "a" * 32
    assert generation_request.slides_markdown == [
        slide.content for slide in source.get_presentation_outline().slides
    ]
    assert all("latest web HTML" in slide.html_content for slide in slides)
    assert all(slide.properties["image"] == "images/stale.png" for slide in slides)


def test_retemplate_style_reference_does_not_snapshot_current_html(
    fake_async_session,
):
    source = _source_presentation()
    fake_async_session._get_results[source.id] = source
    snapshot = AsyncMock()

    with patch(
        "api.v1.ppt.endpoints.presentation.create_exact_source_snapshot",
        new=snapshot,
    ):
        asyncio.run(
            build_authored_retemplate_generation_request(
                source.id,
                _request(),
                fake_async_session,
            )
        )

    snapshot.assert_not_awaited()


def test_retemplate_exact_snapshot_failure_is_422_before_queue(fake_async_session):
    source = _source_presentation()
    fake_async_session._get_results[source.id] = source
    slides = [
        SlideModel(
            presentation=source.id,
            layout_group="authored",
            layout="authored:content",
            index=index,
            content={"__authored__": True},
            html_content=f"<section>slide {index}</section>",
        )
        for index in range(2)
    ]

    class _SlideResult:
        def scalars(self):
            return self

        def all(self):
            return slides

    fake_async_session.execute = AsyncMock(return_value=_SlideResult())
    exact_style = AuthoredStyle(
        id="exact-private",
        name="Exact",
        description="Private exact test style",
        preview_bg="#FFFFFF",
        preview_accent="#111111",
        brief="Preserve source pixels.",
        reference_mode=EXACT_RECONSTRUCTION,
    )
    with (
        patch(
            "api.v1.ppt.endpoints.presentation.load_authored_styles",
            return_value=[exact_style],
        ),
        patch(
            "api.v1.ppt.endpoints.presentation.validate_exact_reconstruction_style"
        ),
        patch(
            "api.v1.ppt.endpoints.presentation.create_exact_source_snapshot",
            new=AsyncMock(side_effect=ValueError("private source failure")),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            build_authored_retemplate_generation_request(
                source.id,
                _request("exact-private"),
                fake_async_session,
            )
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "authored_exact_source_snapshot_invalid"


def test_retemplate_rejects_non_authored_deck(fake_async_session):
    source = _source_presentation()
    source.mode = "template"
    source.layout = {"name": "adaptive", "ordered": False, "slides": []}
    fake_async_session._get_results[source.id] = source

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            build_authored_retemplate_generation_request(
                source.id,
                _request(),
                fake_async_session,
            )
        )

    assert exc_info.value.status_code == 400


def test_retemplate_rejects_unknown_authored_style(fake_async_session):
    source = _source_presentation()
    fake_async_session._get_results[source.id] = source

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            build_authored_retemplate_generation_request(
                source.id,
                _request("not-a-real-authored-style"),
                fake_async_session,
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "AI-authored template not found"


def test_retemplate_accepts_legacy_authored_deck_from_slide_sentinel(
    fake_async_session,
):
    source = _source_presentation()
    source.mode = "template"
    source.theme = None
    source.layout = {"name": "adaptive", "ordered": False, "slides": []}
    fake_async_session._get_results[source.id] = source
    authored_slide = SlideModel(
        presentation=source.id,
        layout_group="authored",
        layout="authored:content",
        index=0,
        content={"__authored__": True},
        html_content="<section>legacy authored slide</section>",
    )

    class _SlideResult:
        def scalars(self):
            return self

        def all(self):
            return [authored_slide]

    fake_async_session.execute = AsyncMock(return_value=_SlideResult())
    generation_request = asyncio.run(
        build_authored_retemplate_generation_request(
            source.id,
            _request(),
            fake_async_session,
        )
    )

    assert generation_request.slides_markdown == [
        "2026년 매출은 125억 원이며 전년 대비 18% 증가했다.",
        "핵심 고객은 공공기관 12곳이며 계약 갱신율은 94%다.",
    ]
    fake_async_session.execute.assert_awaited_once()


def test_retemplate_rejects_missing_semantic_manuscript(fake_async_session):
    source = _source_presentation()
    source.outlines = None
    fake_async_session._get_results[source.id] = source

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            build_authored_retemplate_generation_request(
                source.id,
                _request(),
                fake_async_session,
            )
        )

    assert exc_info.value.status_code == 400
    assert "semantic content" in exc_info.value.detail
