import asyncio
import uuid
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from services import authored_quality_review_service as quality_service
from utils.llm_calls.author_slide import Brand
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


def _run(coro):
    return asyncio.run(coro)


async def _make_engine(db_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def _presentation() -> PresentationModel:
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content="Cover content"),
            SlideOutlineModel(content="Detail content"),
        ]
    )
    return PresentationModel(
        content="Quality review",
        n_slides=2,
        language="Korean",
        title="Quality review",
        outlines=outline.model_dump(mode="json"),
        mode="authored",
        theme={
            "mode": "authored",
            "style": "default",
            "primary": "#2563EB",
            "fonts": "Noto Sans KR",
        },
    )


def _slides(presentation_id):
    return [
        SlideModel(
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:COVER",
            index=0,
            content={
                "__authored__": True,
                "image": "authored/original/slide_0.png",
                "role": "COVER",
            },
            html_content="<html><body>old cover</body></html>",
            properties={"image": "authored/original/slide_0.png"},
        ),
        SlideModel(
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:CLOSING",
            index=1,
            content={
                "__authored__": True,
                "image": "authored/original/slide_1.png",
                "role": "CLOSING",
            },
            html_content="<html><body>old detail</body></html>",
            properties={"image": "authored/original/slide_1.png"},
        ),
    ]


def test_existing_authored_deck_repairs_only_flagged_slide_and_saves_version(
    tmp_path, monkeypatch
):
    async def runner():
        engine = await _make_engine(tmp_path / "quality-review.db")
        presentation = _presentation()
        task = AsyncPresentationGenerationTaskModel(
            status="pending", message="queued"
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(task)
            await session.commit()

            review_calls = 0

            async def fake_render(html):
                return html.encode()

            async def fake_critique(pngs, contexts=None):
                nonlocal review_calls
                review_calls += 1
                if review_calls == 1:
                    return [
                        SlideCritique(
                            needs_fix=True,
                            issues=[
                                CritiqueIssue(
                                    type="overflow",
                                    severity="high",
                                    detail="Title is clipped",
                                )
                            ],
                        ),
                        SlideCritique(needs_fix=False),
                    ]
                return [SlideCritique(needs_fix=False) for _ in pngs]

            async def fake_revise(
                htmls,
                pngs,
                contents,
                roles,
                brand,
                design_system,
                **kwargs,
            ):
                assert kwargs["slide_indices"] == [0, 1]
                assert kwargs["total_slides"] == 2
                return [
                    "<html><body>fixed cover</body></html>",
                    htmls[1],
                ], [b"fixed-cover", pngs[1]], [0]

            monkeypatch.setattr(
                quality_service, "resolve_authored_style", lambda style_id: object()
            )
            monkeypatch.setattr(
                quality_service,
                "prepare_authored_deck",
                lambda outline, brand, style: SimpleNamespace(
                    roles=("COVER", "CLOSING"),
                    brand=Brand(topic="Quality review"),
                    design_system="DESIGN",
                ),
            )
            monkeypatch.setattr(
                quality_service, "render_html_to_png", fake_render
            )
            monkeypatch.setattr(
                quality_service, "critique_authored", fake_critique
            )
            monkeypatch.setattr(
                quality_service, "revise_authored_deck", fake_revise
            )
            monkeypatch.setattr(
                quality_service,
                "_write_reviewed_png",
                lambda presentation_id, index, png: (
                    f"authored/{presentation_id}/slide_{index}.png"
                ),
            )

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(
                    scope="all", mode="analyze_and_fix"
                ),
                task,
                session,
            )

            live_slides = list(
                await session.scalars(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation.id)
                    .order_by(SlideModel.index)
                )
            )
            versions = list(
                await session.scalars(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id
                        == presentation.id
                    )
                )
            )

            assert live_slides[0].html_content == (
                "<html><body>fixed cover</body></html>"
            )
            assert live_slides[1].html_content == (
                "<html><body>old detail</body></html>"
            )
            assert len(versions) == 1
            assert versions[0].label == "고품질 검수 전"
            assert task.status == "completed"
            assert task.data["fixed_slide_indices"] == [0]
            assert task.data["remaining_issue_count"] == 0
            assert task.data["version_saved"] is True

        await engine.dispose()

    _run(runner())
