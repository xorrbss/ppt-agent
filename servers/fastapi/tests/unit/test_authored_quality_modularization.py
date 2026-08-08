import asyncio
import warnings
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.slide import SlideModel
from services import authored_quality_review_service as quality_service
from services.authored_quality_acceptance import local_acceptance_decision
from tests.unit.test_authored_quality_review import _make_engine, _presentation, _slides
from utils.authored_dom_quality import DomQualityIssue, DomQualityReport
from utils.llm_calls.author_slide import Brand
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


def _report(*issues: DomQualityIssue, telemetry_status: str = "available"):
    return DomQualityReport(
        reference_mode="style_reference",
        issues=issues,
        compact_dom=(),
        inspected_elements=1,
        telemetry_status=telemetry_status,
    )


def _issue(category: str, severity: str, qid: str = "title"):
    return DomQualityIssue(
        category=category,
        severity=severity,
        detail=f"{category} on {qid}",
        qids=(qid,),
    )


def test_local_gate_rejects_retained_high_issue_even_when_vqa_is_clean():
    baseline = _report(
        _issue("overflow", "high"),
        _issue("clipping", "medium"),
    )
    candidate = _report(
        _issue("overflow", "high"),
        _issue("clipping", "medium"),
    )

    assert local_acceptance_decision(baseline, candidate) == (
        False,
        "local_high_remaining",
    )


def test_local_gate_requires_strict_improvement_for_existing_local_defects():
    baseline = _report(_issue("clipping", "medium"))
    unchanged = _report(_issue("clipping", "medium"))
    improved = _report(_issue("clipping", "low"))

    assert local_acceptance_decision(baseline, unchanged) == (
        False,
        "local_not_improved",
    )
    assert local_acceptance_decision(baseline, improved) == (True, "improved")


def test_local_gate_allows_vqa_only_fix_when_both_local_reports_are_clean():
    assert local_acceptance_decision(_report(), _report()) == (True, "local_clean")


def test_local_gate_rejects_new_issue_and_unavailable_candidate_telemetry():
    assert local_acceptance_decision(_report(), _report(_issue("contrast", "low"))) == (
        False,
        "local_regression",
    )
    assert local_acceptance_decision(
        _report(_issue("clipping", "medium")),
        _report(telemetry_status="unavailable"),
    ) == (False, "local_candidate_unavailable")


def test_review_service_keeps_helper_facade_for_existing_integrations():
    assert quality_service._local_acceptance_decision is local_acceptance_decision
    assert quality_service._acceptance_decision.__module__.endswith(
        "authored_quality_acceptance"
    )
    assert quality_service._build_slide_results.__module__.endswith(
        "authored_quality_results"
    )


def test_sqlalchemy_execution_boundary_avoids_sqlmodel_deprecation(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "sqlmodel-session-compat.db")
        async with SQLModelAsyncSession(engine) as session:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                rows = await quality_service._execute_sqlalchemy(
                    session, select(SlideModel)
                )
            assert rows.scalars().all() == []
        await engine.dispose()

    asyncio.run(runner())


def test_service_rejects_vqa_clean_candidate_with_retained_local_high_issue(
    tmp_path, monkeypatch
):
    """Regression for the live case where image comparison missed DOM defects."""

    async def runner():
        engine = await _make_engine(tmp_path / "retained-local-high.db")
        presentation = _presentation()
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(_slides(presentation.id))
            session.add(task)
            await session.commit()

            async def fake_render(html):
                return html.encode()

            async def fake_critique(pngs, contexts=None, **kwargs):
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

            async def fake_compare(baselines, candidates, contexts=None, **kwargs):
                # This is the defect: image comparison claims the candidate is clean.
                return [SlideCritique(needs_fix=False)]

            async def fake_revise(htmls, pngs, *args, **kwargs):
                return (
                    ["<html><body>fixed cover</body></html>", htmls[1]],
                    [b"fixed-cover", pngs[1]],
                    [0],
                )

            def fake_dom(html, _metrics, *, reference_mode):
                issues = ()
                if "cover" in html:
                    issues = (
                        _issue("overflow", "high"),
                        _issue("clipping", "medium"),
                    )
                return DomQualityReport(reference_mode, issues, (), 1, "available")

            async def skip_ocr(**kwargs):
                return False, True, None

            monkeypatch.setattr(
                quality_service, "resolve_authored_style", lambda _style: object()
            )
            monkeypatch.setattr(
                quality_service,
                "prepare_authored_deck",
                lambda *args: SimpleNamespace(
                    roles=("COVER", "CLOSING"),
                    brand=Brand(topic="Quality review"),
                    design_system="DESIGN",
                ),
            )
            monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
            monkeypatch.setattr(
                quality_service, "get_cached_browser_metrics", lambda _: {}
            )
            monkeypatch.setattr(quality_service, "inspect_authored_dom", fake_dom)
            monkeypatch.setattr(quality_service, "critique_authored", fake_critique)
            monkeypatch.setattr(
                quality_service,
                "critique_authored_style_comparison",
                fake_compare,
            )
            monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)
            monkeypatch.setattr(quality_service, "_evaluate_candidate_ocr", skip_ocr)

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
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
            assert live_slides[0].html_content == "<html><body>old cover</body></html>"
            assert task.data["fixed_count"] == 0
            assert task.data["rejection_reasons"] == {"local_high_remaining": 1}
            assert task.data["candidate_decisions"][0]["reason"] == (
                "local_high_remaining"
            )

        await engine.dispose()

    asyncio.run(runner())
