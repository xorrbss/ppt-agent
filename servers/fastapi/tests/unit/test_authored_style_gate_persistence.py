"""STYLE_REFERENCE acceptance-gate persistence contracts.

The broader authored-review suite already exercises process-local serialization,
stale-write CAS rollback, immutable asset publication, and version restore/undo.
These tests isolate the STYLE_REFERENCE persistence behavior that is not covered by
the exact-reconstruction cases.
"""

import asyncio
from types import SimpleNamespace

import pytest
from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from services import authored_quality_review_service as quality_service
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tests.unit.authored_quality_test_metrics import complete_browser_metrics
from tests.unit.test_authored_quality_review import (
    _make_engine,
    _presentation,
    _slides,
)
from utils.authored_ocr_quality import OcrQualityResult
from utils.llm_calls.author_slide import STYLE_REFERENCE, Brand
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


@pytest.fixture(autouse=True)
def _complete_browser_telemetry(monkeypatch):
    monkeypatch.setattr(
        quality_service,
        "get_cached_browser_metrics",
        complete_browser_metrics,
    )


def _run(coro):
    return asyncio.run(coro)


def _defect(detail: str = "clipped") -> SlideCritique:
    return SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type="overflow",
                severity="high",
                detail=detail,
            )
        ],
    )


def _patch_style_reference_review(monkeypatch, final_critiques, write_png):
    critique_calls = 0

    async def fake_render(html):
        return html.encode()

    async def fake_critique(pngs, contexts=None, **kwargs):
        nonlocal critique_calls
        critique_calls += 1
        if critique_calls == 1:
            return [_defect("cover clipped"), _defect("detail clipped")]
        return final_critiques

    async def fake_style_comparison(
        baseline_pngs, candidate_pngs, contexts=None, **kwargs
    ):
        return final_critiques

    async def fake_revise(htmls, pngs, *args, **kwargs):
        return (
            [
                "<html><body>candidate cover</body></html>",
                "<html><body>candidate detail</body></html>",
            ],
            [b"candidate-cover-png", b"candidate-detail-png"],
            [0, 1],
        )

    async def fake_ocr(
        _image, *, expected_text=None, language="eng", cache=None, **_kwargs
    ):
        cache.calls += 1
        return OcrQualityResult(
            available=True,
            status="success",
            failure_reason=None,
            text="preserved slide text",
            text_digest="preserved-slide-text",
            metrics={},
            calls=cache.calls,
            cache_hits=cache.cache_hits,
            elapsed_ms=0.0,
        )

    style = SimpleNamespace(id="default", reference_mode=STYLE_REFERENCE)
    plan = SimpleNamespace(
        roles=("COVER", "CLOSING"),
        reference_images=((), ()),
        reference_mode=STYLE_REFERENCE,
        brand=Brand(topic="Quality review"),
        design_system="DESIGN",
    )
    monkeypatch.setattr(quality_service, "resolve_authored_style", lambda _: style)
    monkeypatch.setattr(quality_service, "prepare_authored_deck", lambda *args: plan)
    monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
    monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
    monkeypatch.setattr(
        quality_service,
        "critique_authored_style_comparison",
        fake_style_comparison,
    )
    monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)
    monkeypatch.setattr(quality_service, "inspect_authored_ocr", fake_ocr)
    monkeypatch.setattr(quality_service, "_write_reviewed_png", write_png)


async def _live_slides_and_versions(session, presentation_id):
    session.expire_all()
    slides = list(
        await session.exec(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index)
        )
    )
    versions = list(
        await session.exec(
            select(PresentationVersionModel).where(
                PresentationVersionModel.presentation_id == presentation_id
            )
        )
    )
    return slides, versions


def test_style_reference_mixed_gate_publishes_only_strictly_improved_slide(
    tmp_path, monkeypatch
):
    async def runner():
        engine = await _make_engine(tmp_path / "style-mixed.db")
        presentation = _presentation()
        presentation_id = presentation.id
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        writes = []

        def write_png(presentation_id, position, png):
            writes.append((presentation_id, position, png))
            return f"authored/reviewed/accepted-{position}.png"

        _patch_style_reference_review(
            monkeypatch,
            [SlideCritique(needs_fix=False), _defect("detail still clipped")],
            write_png,
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            original_slides = _slides(presentation.id)
            session.add(presentation)
            session.add_all(original_slides)
            session.add(task)
            await session.commit()

            original_second_content = dict(original_slides[1].content)
            original_second_properties = dict(original_slides[1].properties)
            await quality_service.run_authored_quality_review(
                presentation_id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                task,
                session,
            )

            live, versions = await _live_slides_and_versions(session, presentation_id)
            await session.refresh(task)
            accepted_uri = "authored/reviewed/accepted-0.png"
            assert writes == [(presentation_id, 0, b"candidate-cover-png")]
            assert live[0].html_content == "<html><body>candidate cover</body></html>"
            assert live[0].content["image"] == accepted_uri
            assert live[0].properties["image"] == accepted_uri
            assert live[1].html_content == "<html><body>old detail</body></html>"
            assert live[1].content == original_second_content
            assert live[1].properties == original_second_properties
            assert len(versions) == 1
            assert task.data["fixed_slide_indices"] == [0]
            assert task.data["remaining_slide_indices"] == [1]
            assert task.data["version_saved"] is True

        await engine.dispose()

    _run(runner())


def test_style_reference_zero_accepted_skips_png_writes_and_snapshot(
    tmp_path, monkeypatch
):
    async def runner():
        engine = await _make_engine(tmp_path / "style-zero-accepted.db")
        presentation = _presentation()
        presentation_id = presentation.id
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        writes = []

        _patch_style_reference_review(
            monkeypatch,
            [_defect("cover unchanged"), _defect("detail worsened")],
            lambda *args: writes.append(args),
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            originals = _slides(presentation.id)
            original_dumps = [slide.model_dump(mode="json") for slide in originals]
            session.add(presentation)
            session.add_all(originals)
            session.add(task)
            await session.commit()

            await quality_service.run_authored_quality_review(
                presentation_id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                task,
                session,
            )

            live, versions = await _live_slides_and_versions(session, presentation_id)
            await session.refresh(task)
            assert writes == []
            assert [slide.html_content for slide in live] == [
                slide["html_content"] for slide in original_dumps
            ]
            assert [slide.content for slide in live] == [
                slide["content"] for slide in original_dumps
            ]
            assert [slide.properties for slide in live] == [
                slide["properties"] for slide in original_dumps
            ]
            assert versions == []
            assert task.data["fixed_slide_indices"] == []
            assert task.data["version_saved"] is False

        await engine.dispose()

    _run(runner())


def test_style_reference_png_write_failure_leaves_live_slides_and_history_untouched(
    tmp_path, monkeypatch
):
    async def runner():
        engine = await _make_engine(tmp_path / "style-write-failure.db")
        presentation = _presentation()
        presentation_id = presentation.id
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        writes = []

        def fail_second_write(presentation_id, position, png):
            writes.append((presentation_id, position, png))
            if position == 1:
                raise RuntimeError("simulated atomic publish failure")
            return "authored/reviewed/unreferenced-first.png"

        _patch_style_reference_review(
            monkeypatch,
            [SlideCritique(needs_fix=False), SlideCritique(needs_fix=False)],
            fail_second_write,
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            originals = _slides(presentation.id)
            original_dumps = [slide.model_dump(mode="json") for slide in originals]
            session.add(presentation)
            session.add_all(originals)
            session.add(task)
            await session.commit()

            with pytest.raises(RuntimeError, match="simulated atomic publish failure"):
                await quality_service.run_authored_quality_review(
                    presentation_id,
                    AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                    task,
                    session,
                )

            live, versions = await _live_slides_and_versions(session, presentation_id)
            assert writes == [
                (presentation_id, 0, b"candidate-cover-png"),
                (presentation_id, 1, b"candidate-detail-png"),
            ]
            assert [slide.html_content for slide in live] == [
                slide["html_content"] for slide in original_dumps
            ]
            assert [slide.content for slide in live] == [
                slide["content"] for slide in original_dumps
            ]
            assert [slide.properties for slide in live] == [
                slide["properties"] for slide in original_dumps
            ]
            assert versions == []

        await engine.dispose()

    _run(runner())
