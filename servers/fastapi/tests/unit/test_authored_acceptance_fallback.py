import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import authored_quality_review_service as quality_service
from utils.authored_dom_quality import STYLE_REFERENCE, DomQualityReport
from utils.authored_ocr_quality import OcrQualityResult
from utils.llm_calls.author_slide import Brand
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


def _defect(detail: str = "title is clipped") -> SlideCritique:
    return SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="overflow", severity="high", detail=detail)],
    )


def _clean() -> SlideCritique:
    return SlideCritique(needs_fix=False, issues=[])


async def _engine(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def _mock_text_preserving_ocr(monkeypatch) -> None:
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

    monkeypatch.setattr(quality_service, "inspect_authored_ocr", fake_ocr)


@pytest.mark.parametrize(
    (
        "outcome",
        "expected_fixed",
        "expected_reason",
        "expected_failures",
        "expected_revise_calls",
        "expected_compare_calls",
        "expected_path",
        "expected_fallback_attempts",
    ),
    [
        ("success", [0], None, 0, 2, 2, "full_fallback", 1),
        ("failure", [], "full_reauthor_failed", 1, 2, 1, "full_fallback", 1),
        ("indeterminate", [], "final_indeterminate", 0, 2, 2, "full_fallback", 1),
        (
            "initial_indeterminate",
            [],
            "final_indeterminate",
            0,
            1,
            1,
            "local_patch",
            0,
        ),
    ],
)
def test_rejected_local_patch_gets_one_bounded_full_fallback(
    tmp_path,
    monkeypatch,
    outcome,
    expected_fixed,
    expected_reason,
    expected_failures,
    expected_revise_calls,
    expected_compare_calls,
    expected_path,
    expected_fallback_attempts,
):
    _mock_text_preserving_ocr(monkeypatch)

    async def scenario():
        engine = await _engine(tmp_path / f"fallback-{outcome}.db")
        presentation = PresentationModel(
            content="Fallback gate",
            n_slides=1,
            language="English",
            title="Fallback gate",
            outlines=PresentationOutlineModel(
                slides=[SlideOutlineModel(content="Slide context")]
            ).model_dump(mode="json"),
            mode="authored",
            theme={"mode": "authored", "style": "default"},
        )
        slide = SlideModel(
            presentation=presentation.id,
            layout_group="authored",
            layout="authored:CONTENT",
            index=0,
            content={"__authored__": True, "role": "CONTENT"},
            html_content="<html><body><h1>original</h1></body></html>",
            properties={},
        )
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        revise_calls = []
        compare_calls = []

        monkeypatch.setattr(
            quality_service,
            "resolve_authored_style",
            lambda _: SimpleNamespace(id="default", reference_mode=STYLE_REFERENCE),
        )
        monkeypatch.setattr(
            quality_service,
            "prepare_authored_deck",
            lambda *_, **_kwargs: SimpleNamespace(
                roles=("CONTENT",),
                reference_mode=STYLE_REFERENCE,
                brand=Brand(topic="Fallback gate"),
                design_system="STYLE DESIGN",
            ),
        )

        async def fake_render(html, timeout=60):
            return html.encode()

        async def fake_initial_review(_pngs, contexts=None, **_kwargs):
            return [_defect()]

        async def fake_compare(_baselines, _candidates, contexts=None, **_kwargs):
            compare_calls.append(list(contexts or []))
            if len(compare_calls) == 1:
                if outcome == "initial_indeterminate":
                    return [_defect("INDETERMINATE: comparison provider unavailable")]
                return [_defect("same defect, rephrased")]
            if outcome == "success":
                return [_clean()]
            return [_defect("INDETERMINATE: comparison provider unavailable")]

        async def fake_revise(htmls, pngs, *_args, **kwargs):
            revise_calls.append(kwargs)
            if len(revise_calls) == 1:
                return (
                    ["<html><body><h1>local patch</h1></body></html>"],
                    [b"local-png"],
                    [0],
                    [
                        {
                            "local_index": 0,
                            "slide_index": 0,
                            "path": "local_patch",
                            "attempted": True,
                            "succeeded": True,
                            "patch": {
                                "attempted": True,
                                "succeeded": True,
                                "failure_detail": None,
                            },
                            "fallback": {
                                "attempted": False,
                                "succeeded": False,
                                "failure_detail": None,
                            },
                            "element_changes": [],
                        }
                    ],
                )
            assert kwargs["force_full_reauthor_indices"] == [0]
            if outcome == "failure":
                failure = {
                    "stage": "full_reauthor",
                    "code": "full_reauthor_failed",
                    "error_type": "RuntimeError",
                }
                return (
                    htmls,
                    pngs,
                    [],
                    [
                        {
                            "local_index": 0,
                            "slide_index": 0,
                            "path": "full_fallback",
                            "attempted": True,
                            "succeeded": False,
                            "failure_detail": failure,
                            "patch": {
                                "attempted": False,
                                "succeeded": False,
                                "failure_detail": None,
                            },
                            "fallback": {
                                "attempted": True,
                                "succeeded": False,
                                "failure_detail": failure,
                            },
                            "element_changes": [],
                        }
                    ],
                )
            return (
                ["<html><body><h1>full fallback</h1></body></html>"],
                [b"fallback-png"],
                [0],
                [
                    {
                        "local_index": 0,
                        "slide_index": 0,
                        "path": "full_fallback",
                        "attempted": True,
                        "succeeded": True,
                        "failure_detail": None,
                        "patch": {
                            "attempted": False,
                            "succeeded": False,
                            "failure_detail": None,
                        },
                        "fallback": {
                            "attempted": True,
                            "succeeded": True,
                            "failure_detail": None,
                        },
                        "element_changes": [],
                    }
                ],
            )

        monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
        monkeypatch.setattr(quality_service, "critique_authored", fake_initial_review)
        monkeypatch.setattr(
            quality_service, "critique_authored_style_comparison", fake_compare
        )
        monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)
        monkeypatch.setattr(
            quality_service,
            "inspect_authored_dom",
            lambda *_args, **_kwargs: DomQualityReport(
                STYLE_REFERENCE, (), (), 1, "available"
            ),
        )
        monkeypatch.setattr(
            quality_service,
            "_observe_critique_cache",
            lambda images, contexts, **kwargs: (0, len(images)),
        )
        monkeypatch.setattr(
            quality_service,
            "_observe_style_comparison_cache",
            lambda baselines, candidates, contexts: (0, len(candidates)),
        )
        monkeypatch.setattr(
            quality_service, "_write_reviewed_png", lambda *_args: "review.png"
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add(slide)
            session.add(task)
            await session.commit()
            # Exercise the retained legacy implementation directly.  The public
            # quality.review entry point is maximum-repair-only and must not
            # route to this full-page fallback.
            await quality_service._run_legacy_authored_quality_review_unlocked(
                presentation.id,
                AuthoredQualityReviewRequest(
                    scope="all",
                    mode="analyze_and_fix",
                    slidespec_opt_in=False,
                ),
                task,
                session,
            )

        assert len(revise_calls) == expected_revise_calls
        assert len(compare_calls) == expected_compare_calls
        assert task.data["fixed_slide_indices"] == expected_fixed
        decision = task.data["candidate_decisions"][0]
        assert decision["path"] == expected_path
        if expected_path == "full_fallback":
            assert decision["initial_rejection_reason"] == "unchanged"
        else:
            assert "initial_rejection_reason" not in decision
        assert decision["applied"] is bool(expected_fixed)
        assert decision["rejection_reason"] == expected_reason
        assert task.data["slide_results"][0]["applied"] is bool(expected_fixed)
        assert task.data["metrics"]["fallback_attempts"] == expected_fallback_attempts
        assert task.data["metrics"]["fallback_failures"] == expected_failures
        assert (
            task.data["metrics"]["fallback_attempted"]
            == task.data["metrics"]["fallback_attempts"]
        )
        assert (
            task.data["metrics"]["fallback_succeeded"]
            == task.data["metrics"]["fallback_successes"]
        )
        assert (
            task.data["metrics"]["fallback_failed"]
            == task.data["metrics"]["fallback_failures"]
        )
        assert (
            task.data["metrics"]["patch_attempted"]
            == task.data["metrics"]["patch_attempts"]
        )
        assert (
            task.data["metrics"]["patch_succeeded"]
            == task.data["metrics"]["patch_successes"]
        )
        assert (
            task.data["metrics"]["patch_failed"]
            == task.data["metrics"]["patch_failures"]
        )
        await engine.dispose()

    asyncio.run(scenario())
