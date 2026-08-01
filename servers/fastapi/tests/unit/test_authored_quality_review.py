import asyncio
import json
import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

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
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    Brand,
)
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


def _exact_plan(source_paths):
    return SimpleNamespace(
        roles=("CONTENT", "CONTENT"),
        reference_images=tuple((path,) for path in source_paths),
        reference_mode=EXACT_RECONSTRUCTION,
        brand=Brand(topic="Quality review"),
        design_system="DESIGN",
    )


def test_exact_quality_review_compares_ordered_source_render_pairs_once_analyze_only(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=quality_service.__name__)
    async def runner():
        engine = await _make_engine(tmp_path / "exact-analyze.db")
        presentation = _presentation()
        presentation.theme["style"] = "exact-source"
        presentation.theme["reference_mode"] = EXACT_RECONSTRUCTION
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        source_paths = (tmp_path / "source-0.png", tmp_path / "source-1.png")
        source_paths[0].write_bytes(b"source-0")
        source_paths[1].write_bytes(b"source-1")
        style = SimpleNamespace(reference_mode=EXACT_RECONSTRUCTION)
        critique_calls = []

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(task)
            await session.commit()

            async def fake_render(html):
                return html.encode()

            async def fake_critique(pngs, contexts=None, **kwargs):
                critique_calls.append((list(pngs), list(contexts or ()), kwargs))
                return [SlideCritique(needs_fix=False) for _ in pngs]

            monkeypatch.setattr(quality_service, "resolve_authored_style", lambda _: style)
            monkeypatch.setattr(
                quality_service, "prepare_authored_deck", lambda *args: _exact_plan(source_paths)
            )
            monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
            monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
            monkeypatch.setattr(
                quality_service,
                "_observe_critique_cache",
                lambda images, contexts, **_: (0, len(images)),
            )

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_only"),
                task,
                session,
            )

            assert critique_calls == [
                (
                    [b"<html><body>old cover</body></html>", b"<html><body>old detail</body></html>"],
                    ["Cover content", "Detail content"],
                    {
                        "source_images": [b"source-0", b"source-1"],
                        "reference_mode": EXACT_RECONSTRUCTION,
                    },
                )
            ]
            assert task.data["fixed_slide_indices"] == []
            assert task.data["version_saved"] is False

        await engine.dispose()

    _run(runner())
    review_events = [
        json.loads(record.getMessage().partition(" ")[2])
        for record in caplog.records
        if record.getMessage().startswith("authored_quality_review ")
    ]
    assert [
        (
            event["stage"],
            event["critique_batch_count"],
            event["critique_slide_request_count"],
        )
        for event in review_events
        if event["critique_batch_count"]
    ] == [("initial_review", 1, 2)]


def test_exact_quality_review_reuses_initial_critique_rechecks_only_candidates_and_accepts_improvement(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=quality_service.__name__)
    async def runner():
        engine = await _make_engine(tmp_path / "exact-fix.db")
        presentation = _presentation()
        presentation.theme["style"] = "exact-source"
        presentation.theme["reference_mode"] = EXACT_RECONSTRUCTION
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        source_paths = (tmp_path / "source-0.png", tmp_path / "source-1.png")
        source_paths[0].write_bytes(b"source-0")
        source_paths[1].write_bytes(b"source-1")
        style = SimpleNamespace(reference_mode=EXACT_RECONSTRUCTION)
        critique_calls = []

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(task)
            await session.commit()

            async def fake_render(html):
                return html.encode()

            async def fake_critique(pngs, contexts=None, **kwargs):
                critique_calls.append((list(pngs), kwargs))
                if len(critique_calls) == 1:
                    return [
                        SlideCritique(
                            needs_fix=True,
                            issues=[CritiqueIssue(type="overflow", severity="high", detail="clipped")],
                        ),
                        SlideCritique(needs_fix=False),
                    ]
                return [SlideCritique(needs_fix=False)]

            async def fake_revise(*args, **kwargs):
                assert kwargs["initial_critiques"][0].needs_fix is True
                assert kwargs["reference_images_by_slide"] == [
                    (source_paths[0],), (source_paths[1],)
                ]
                return ["<html><body>fixed</body></html>", args[0][1]], [b"fixed", args[1][1]], [0]

            monkeypatch.setattr(quality_service, "resolve_authored_style", lambda _: style)
            monkeypatch.setattr(quality_service, "prepare_authored_deck", lambda *args: _exact_plan(source_paths))
            monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
            monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
            monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)
            monkeypatch.setattr(quality_service, "_write_reviewed_png", lambda *_: "authored/fixed.png")
            monkeypatch.setattr(
                quality_service,
                "_observe_critique_cache",
                lambda images, contexts, **_: (0, len(images)),
            )

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                task,
                session,
            )

            live_slides = list(await session.scalars(select(SlideModel).where(SlideModel.presentation == presentation.id).order_by(SlideModel.index)))
            versions = list(await session.scalars(select(PresentationVersionModel).where(PresentationVersionModel.presentation_id == presentation.id)))
            assert critique_calls == [
                ([b"<html><body>old cover</body></html>", b"<html><body>old detail</body></html>"], {"source_images": [b"source-0", b"source-1"], "reference_mode": EXACT_RECONSTRUCTION}),
                ([b"fixed"], {"source_images": [b"source-0"], "reference_mode": EXACT_RECONSTRUCTION}),
            ]
            assert live_slides[0].html_content == "<html><body>fixed</body></html>"
            assert live_slides[1].html_content == "<html><body>old detail</body></html>"
            assert len(versions) == 1
            assert task.data["fixed_slide_indices"] == [0]
            assert task.data["remaining_issue_count"] == 0

        await engine.dispose()

    _run(runner())
    review_events = [
        json.loads(record.getMessage().partition(" ")[2])
        for record in caplog.records
        if record.getMessage().startswith("authored_quality_review ")
    ]
    assert [
        (
            event["stage"],
            event["critique_batch_count"],
            event["critique_slide_request_count"],
        )
        for event in review_events
        if event["critique_batch_count"]
    ] == [("initial_review", 1, 2), ("final_review", 1, 1)]


@pytest.mark.parametrize(
    "final_critique",
    [
        SlideCritique(
            needs_fix=True,
            issues=[
                CritiqueIssue(
                    type="overflow",
                    severity="high",
                    detail="still clipped",
                )
            ],
        ),
        SlideCritique(
            needs_fix=False,
            issues=[
                CritiqueIssue(
                    type="other",
                    severity="low",
                    detail="inconsistent structured result",
                )
            ],
        ),
        None,
    ],
    ids=["not-improved", "inconsistent", "indeterminate"],
)
def test_exact_quality_review_rejects_non_improving_candidate_without_snapshot(
    tmp_path, monkeypatch, final_critique
):
    async def runner():
        engine = await _make_engine(tmp_path / "exact-reject.db")
        presentation = _presentation()
        presentation.theme["style"] = "exact-source"
        presentation.theme["reference_mode"] = EXACT_RECONSTRUCTION
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        source_paths = (tmp_path / "source-0.png", tmp_path / "source-1.png")
        source_paths[0].write_bytes(b"source-0")
        source_paths[1].write_bytes(b"source-1")
        style = SimpleNamespace(reference_mode=EXACT_RECONSTRUCTION)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(task)
            await session.commit()

            critique_calls = 0

            async def fake_critique(pngs, contexts=None, **kwargs):
                nonlocal critique_calls
                critique_calls += 1
                if critique_calls == 1:
                    return [
                        SlideCritique(
                            needs_fix=True,
                            issues=[
                                CritiqueIssue(
                                    type="overflow",
                                    severity="high",
                                    detail="clipped",
                                )
                            ],
                        )
                        for _ in pngs
                    ]
                return [final_critique]

            async def fake_revise(htmls, pngs, *args, **kwargs):
                return ["<html><body>worse</body></html>", htmls[1]], [b"worse", pngs[1]], [0]

            monkeypatch.setattr(quality_service, "resolve_authored_style", lambda _: style)
            monkeypatch.setattr(quality_service, "prepare_authored_deck", lambda *args: _exact_plan(source_paths))
            monkeypatch.setattr(quality_service, "render_html_to_png", lambda html: _async_bytes(html.encode()))
            monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
            monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                task,
                session,
            )

            live_slide = (await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation.id)
                .where(SlideModel.index == 0)
            )).one()
            versions = list(await session.scalars(select(PresentationVersionModel).where(PresentationVersionModel.presentation_id == presentation.id)))
            assert live_slide.html_content == "<html><body>old cover</body></html>"
            assert versions == []
            assert task.data["fixed_slide_indices"] == []
            assert task.data["version_saved"] is False

        await engine.dispose()

    _run(runner())


async def _async_bytes(value):
    return value
