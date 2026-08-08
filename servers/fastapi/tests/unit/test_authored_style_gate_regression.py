"""Regression coverage for conservative STYLE_REFERENCE quality-gate review.

This file deliberately asserts review orchestration and policy only.  Snapshot,
asset-publication, and compare-and-swap persistence behavior has dedicated tests.
"""

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
from tests.unit.authored_quality_test_metrics import complete_browser_metrics
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


async def _engine(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def _critique(
    severity: str = "high", detail: str = "visible clipping"
) -> SlideCritique:
    return SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="overflow", severity=severity, detail=detail)],
    )


def _clean() -> SlideCritique:
    return SlideCritique(needs_fix=False)


def _worsened() -> SlideCritique:
    return SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(type="overflow", severity="high", detail="first"),
            CritiqueIssue(type="overlap", severity="high", detail="second"),
        ],
    )


def _review_fixture() -> tuple[PresentationModel, list[SlideModel]]:
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content="Cover context"),
            SlideOutlineModel(content="Current nonzero context"),
            SlideOutlineModel(content="Final context"),
        ]
    )
    presentation = PresentationModel(
        content="Style reference gate",
        n_slides=3,
        language="English",
        title="Style reference gate",
        outlines=outline.model_dump(mode="json"),
        mode="authored",
        theme={"mode": "authored", "style": "default"},
    )
    slides = [
        SlideModel(
            presentation=presentation.id,
            layout_group="authored",
            layout=f"authored:{role}",
            index=index,
            content={"__authored__": True, "role": role},
            html_content=f"<html><body>original-{index}</body></html>",
            properties={},
        )
        for index, role in enumerate(("COVER", "CONTENT", "CLOSING"))
    ]
    return presentation, slides


def _style_plan() -> SimpleNamespace:
    return SimpleNamespace(
        roles=("COVER", "CONTENT", "CLOSING"),
        reference_mode=STYLE_REFERENCE,
        brand=Brand(topic="Style reference gate"),
        design_system="STYLE DESIGN",
    )


def _install_style_reference_stubs(monkeypatch, critique, compare, revise):
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

    monkeypatch.setattr(
        quality_service,
        "resolve_authored_style",
        lambda _: SimpleNamespace(id="default", reference_mode=STYLE_REFERENCE),
    )
    monkeypatch.setattr(
        quality_service, "prepare_authored_deck", lambda *_: _style_plan()
    )
    monkeypatch.setattr(
        quality_service,
        "render_html_to_png",
        lambda html: _async_value(html.encode()),
    )
    monkeypatch.setattr(quality_service, "critique_authored", critique)
    monkeypatch.setattr(
        quality_service, "critique_authored_style_comparison", compare
    )
    monkeypatch.setattr(quality_service, "revise_authored_deck", revise)
    monkeypatch.setattr(quality_service, "inspect_authored_ocr", fake_ocr)
    monkeypatch.setattr(
        quality_service,
        "_observe_critique_cache",
        lambda images, contexts, **_: (0, len(images)),
    )
    monkeypatch.setattr(
        quality_service,
        "_observe_style_comparison_cache",
        lambda baselines, candidates, contexts, **_: (0, len(candidates)),
    )
    # Publishing mechanics are intentionally outside this orchestration regression.
    monkeypatch.setattr(quality_service, "_write_reviewed_png", lambda *_: "review.png")


@pytest.mark.parametrize(
    ("final", "expected"),
    [
        (_clean(), (True, "improved")),
        (_critique("high"), (False, "unchanged")),
        (_worsened(), (False, "worsened")),
        (
            SlideCritique(
                needs_fix=False,
                issues=[CritiqueIssue(type="other", severity="low", detail="mismatch")],
            ),
            (False, "final_inconsistent"),
        ),
        (None, (False, "final_unavailable")),
        (
            _critique("low", "REGRESSION: new footer clipping"),
            (False, "regression"),
        ),
        (
            _critique("high", "INDETERMINATE: source detail is unreadable"),
            (False, "final_indeterminate"),
        ),
    ],
    ids=[
        "improved",
        "equal",
        "worsened",
        "inconsistent",
        "unavailable",
        "regression",
        "indeterminate",
    ],
)
def test_style_reference_gate_decisions_are_conservative(final, expected):
    """STYLE_REFERENCE accepts only a determinate strict improvement."""
    assert quality_service._style_acceptance_decision(_critique(), final) == expected


def test_style_reference_gate_requires_a_usable_initial_critique():
    inconsistent = SlideCritique(
        needs_fix=False,
        issues=[CritiqueIssue(type="other", severity="low", detail="contradiction")],
    )

    assert quality_service._style_acceptance_decision(None, _clean()) == (
        False,
        "initial_unavailable",
    )
    assert quality_service._style_acceptance_decision(inconsistent, _clean()) == (
        False,
        "initial_inconsistent",
    )


def test_style_reference_all_scope_reuses_initial_critiques_and_reviews_each_candidate_once(
    tmp_path,
    monkeypatch,
):
    async def scenario():
        engine = await _engine(tmp_path / "style-reference-all.db")
        presentation, slides = _review_fixture()
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        initial_calls = []
        comparison_calls = []
        initial = [_critique(), _clean(), _critique()]

        async def fake_critique(pngs, contexts=None):
            initial_calls.append((list(pngs), list(contexts or [])))
            assert len(initial_calls) == 1
            return initial

        async def fake_compare(baselines, candidates, contexts=None, **kwargs):
            comparison_calls.append(
                (list(baselines), list(candidates), list(contexts or []))
            )
            assert len(comparison_calls) == 1
            return [_clean(), _critique("high", "still clipped")]

        async def fake_revise(
            htmls, pngs, contents, roles, brand, design_system, **kwargs
        ):
            assert kwargs["initial_critiques"] == initial
            assert kwargs["slide_indices"] == [0, 1, 2]
            assert kwargs["total_slides"] == 3
            return (
                [
                    "<html><body>candidate-zero</body></html>",
                    htmls[1],
                    "<html><body>candidate-two</body></html>",
                ],
                [b"candidate-zero", pngs[1], b"candidate-two"],
                [0, 2],
            )

        _install_style_reference_stubs(
            monkeypatch, fake_critique, fake_compare, fake_revise
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(slides)
            session.add(task)
            await session.commit()

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(scope="all", mode="analyze_and_fix"),
                task,
                session,
            )

            assert initial_calls == [
                (
                    [slide.html_content.encode() for slide in slides],
                    ["Cover context", "Current nonzero context", "Final context"],
                )
            ]
            # Final comparison is made only for the M revised candidates, once each.
            assert comparison_calls == [
                (
                    [
                        b"<html><body>original-0</body></html>",
                        b"<html><body>original-2</body></html>",
                    ],
                    [b"candidate-zero", b"candidate-two"],
                    ["Cover context", "Final context"],
                )
            ]
            # The equal/worse candidate must not be published merely because it changed.
            assert task.data["fixed_slide_indices"] == [0]
        await engine.dispose()

    _run(scenario())


def test_style_reference_current_nonzero_scope_keeps_absolute_position_and_finalizes_once(
    tmp_path,
    monkeypatch,
):
    async def scenario():
        engine = await _engine(tmp_path / "style-reference-current.db")
        presentation, slides = _review_fixture()
        task = AsyncPresentationGenerationTaskModel(status="pending", message="queued")
        initial_calls = []
        comparison_calls = []
        initial = [_critique()]

        async def fake_critique(pngs, contexts=None):
            initial_calls.append((list(pngs), list(contexts or [])))
            assert len(initial_calls) == 1
            return initial

        async def fake_compare(baselines, candidates, contexts=None, **kwargs):
            comparison_calls.append(
                (list(baselines), list(candidates), list(contexts or []))
            )
            assert len(comparison_calls) == 1
            return [_clean()]

        async def fake_revise(
            htmls, pngs, contents, roles, brand, design_system, **kwargs
        ):
            assert kwargs["initial_critiques"] == initial
            assert kwargs["slide_indices"] == [1]
            assert kwargs["total_slides"] == 3
            assert roles == ["CONTENT"]
            return ["<html><body>candidate-one</body></html>"], [b"candidate-one"], [0]

        _install_style_reference_stubs(
            monkeypatch, fake_critique, fake_compare, fake_revise
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(presentation)
            session.add_all(slides)
            session.add(task)
            await session.commit()

            await quality_service.run_authored_quality_review(
                presentation.id,
                AuthoredQualityReviewRequest(
                    scope="current", slide_indices=[1], mode="analyze_and_fix"
                ),
                task,
                session,
            )

            assert initial_calls == [
                ([b"<html><body>original-1</body></html>"], ["Current nonzero context"])
            ]
            assert comparison_calls == [
                (
                    [b"<html><body>original-1</body></html>"],
                    [b"candidate-one"],
                    ["Current nonzero context"],
                )
            ]
            assert task.data["fixed_slide_indices"] == [1]
        await engine.dispose()

    _run(scenario())


async def _async_value(value):
    return value
