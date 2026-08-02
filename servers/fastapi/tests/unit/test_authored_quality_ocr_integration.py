import asyncio
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation as presentation_endpoint
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
from services import authored_quality_ocr as ocr_service
from services import authored_quality_review_service as quality_service
from utils.authored_dom_quality import DomQualityIssue, DomQualityReport
from utils.authored_ocr_quality import OcrQualityResult, OcrRuntime
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, STYLE_REFERENCE, Brand
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


def _run(coro):
    return asyncio.run(coro)


def test_ocr_readiness_is_fail_closed_and_does_not_expose_runtime_paths(monkeypatch):
    private_path = r"C:\private\runtime\tesseract.exe"
    monkeypatch.setattr(
        ocr_service,
        "inspect_tesseract",
        lambda **_: OcrRuntime(
            status="language_missing",
            command=(private_path,),
            version=f"secret at {private_path}",
            requested_languages=("kor", "eng"),
            available_languages=("eng",),
            tessdata_dir=r"C:\private\tessdata",
            inspection_seconds=0.01,
            failure_reason="missing_languages:kor",
        ),
    )

    readiness = ocr_service._build_ocr_readiness()

    assert readiness == {
        "schema_version": 1,
        "component": "authored_quality_ocr",
        "ready": False,
        "code": "ocr_language_missing",
        "requested_languages": ["eng", "kor"],
        "available_languages": ["eng"],
        "missing_languages": ["kor"],
        "tessdata_configured": True,
        "inspection_ms": readiness["inspection_ms"],
    }
    serialized = str(readiness)
    assert private_path not in serialized
    assert "tessdata_dir" not in readiness
    assert "command" not in readiness
    assert "version" not in readiness


def test_ocr_language_accepts_korean_codes_and_plain_label():
    for language in ("ko", "kor", "KR", "Korean", "한국어", "Korean (한국어)"):
        assert ocr_service.ocr_language(language) == "kor+eng"


def test_quality_review_readiness_route_precedes_dynamic_presentation_route(
    monkeypatch,
):
    payload = {"component": "authored_quality_ocr", "ready": True}

    async def fake_readiness():
        return payload

    monkeypatch.setattr(presentation_endpoint, "get_ocr_readiness", fake_readiness)
    routes = list(presentation_endpoint.PRESENTATION_ROUTER.routes)
    readiness_route = next(
        route for route in routes if route.path == "/presentation/quality-review/readiness"
    )
    dynamic_route = next(
        route for route in routes if route.path == "/presentation/{id}/quality-review"
    )

    assert routes.index(readiness_route) < routes.index(dynamic_route)
    assert _run(readiness_route.endpoint()) == payload


async def _engine(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def _clean() -> SlideCritique:
    return SlideCritique(needs_fix=False, issues=[])


def _text_defect() -> SlideCritique:
    return SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type="overflow",
                severity="high",
                detail="Revenue number is clipped",
            )
        ],
    )


def _ocr_result(
    text: str | None,
    *,
    cache,
    status: str = "success",
    failure_reason: str | None = None,
) -> OcrQualityResult:
    available = text is not None and status == "success"
    if available:
        cache.calls += 1
    return OcrQualityResult(
        available=available,
        status=status,
        failure_reason=failure_reason,
        text=text,
        text_digest=f"digest-{text}" if text is not None else None,
        metrics={},
        calls=cache.calls,
        cache_hits=cache.cache_hits,
        elapsed_ms=1.0,
    )


def _assert_private_ocr_contract(check: dict) -> None:
    assert {
        "status",
        "executed",
        "available",
        "comparison",
        "baseline_omissions",
        "candidate_omissions",
    } <= check.keys()
    serialized = str(check).casefold()
    assert "raw_text" not in serialized
    assert "ocr_text" not in serialized
    for payload_name in ("reference", "baseline", "candidate"):
        payload = check.get(payload_name)
        if payload:
            assert "text" not in payload


def test_full_reauthor_always_routes_ocr_even_for_non_text_vqa() -> None:
    signals = quality_service._ocr_route_signals(
        None,
        _clean(),
        {"path": "full_fallback", "element_changes": []},
    )

    assert signals == ["repair:full_fallback"]


async def _style_scenario(
    tmp_path: Path,
    monkeypatch,
    *,
    analyze_only: bool = False,
    candidate_text: str = "Revenue 42%",
    fallback_text: str | None = None,
    unavailable: bool = False,
):
    engine = await _engine(tmp_path / "ocr-review.db")
    presentation = PresentationModel(
        content="OCR review",
        n_slides=1,
        language="English",
        title="OCR review",
        outlines=PresentationOutlineModel(
            slides=[SlideOutlineModel(content="Outline content is not OCR truth")]
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
    revise_calls: list[dict] = []
    ocr_images: list[bytes] = []

    monkeypatch.setattr(
        quality_service,
        "resolve_authored_style",
        lambda _: SimpleNamespace(id="default", reference_mode=STYLE_REFERENCE),
    )
    monkeypatch.setattr(
        quality_service,
        "prepare_authored_deck",
        lambda *_: SimpleNamespace(
            roles=("CONTENT",),
            reference_mode=STYLE_REFERENCE,
            brand=Brand(topic="OCR review", language="English"),
            design_system="STYLE DESIGN",
        ),
    )

    async def fake_render(html):
        return html.encode()

    def fake_metrics(html):
        text = "Revenue 42%"
        return {"elements": [{"qid": "title", "visible": True, "rendered_text": text}]}

    def fake_dom(html, _metrics, *, reference_mode):
        if "original" in html and not analyze_only:
            issues = (
                DomQualityIssue(
                    "content",
                    "high",
                    "rendered number is missing",
                    ("title",),
                    STYLE_REFERENCE,
                ),
            )
        else:
            issues = ()
        return DomQualityReport(reference_mode, issues, (), 1, "available")

    async def fake_initial(_pngs, contexts=None, **_kwargs):
        return [_clean() if analyze_only else _text_defect()]

    async def fake_compare(_baseline, _candidate, contexts=None, **_kwargs):
        return [_clean()]

    async def fake_revise(htmls, pngs, *_args, **kwargs):
        revise_calls.append(kwargs)
        if len(revise_calls) == 1:
            return (
                ["<html><body><h1>candidate</h1></body></html>"],
                [b"candidate-png"],
                [0],
                [
                    {
                        "local_index": 0,
                        "slide_index": 0,
                        "path": "local_patch",
                        "attempted": True,
                        "succeeded": True,
                        "patch": {"attempted": True, "succeeded": True},
                        "fallback": {"attempted": False, "succeeded": False},
                        "element_changes": [
                            {"element_id": "title", "action": "setText"}
                        ],
                    }
                ],
            )
        assert kwargs["force_full_reauthor_indices"] == [0]
        return (
            ["<html><body><h1>fallback</h1></body></html>"],
            [b"fallback-png"],
            [0],
            [
                {
                    "local_index": 0,
                    "slide_index": 0,
                    "path": "full_fallback",
                    "attempted": True,
                    "succeeded": True,
                    "patch": {"attempted": False, "succeeded": False},
                    "fallback": {"attempted": True, "succeeded": True},
                    "element_changes": [],
                }
            ],
        )

    async def fake_ocr(image, *, expected_text=None, language="eng", cache=None, **_):
        ocr_images.append(image)
        if b"original" in image:
            if unavailable:
                return _ocr_result(
                    None,
                    cache=cache,
                    status="unavailable",
                    failure_reason="tesseract_executable_not_found",
                )
            return _ocr_result("Revenue 42%", cache=cache)
        if image == b"candidate-png":
            return _ocr_result(candidate_text, cache=cache)
        if image == b"fallback-png":
            return _ocr_result(fallback_text or "Revenue 42%", cache=cache)
        raise AssertionError(f"unexpected OCR image: {image!r}")

    async def no_snapshot(*_args, **_kwargs):
        return None

    monkeypatch.setattr(quality_service, "render_html_to_png", fake_render)
    monkeypatch.setattr(quality_service, "get_cached_browser_metrics", fake_metrics)
    monkeypatch.setattr(quality_service, "inspect_authored_dom", fake_dom)
    monkeypatch.setattr(quality_service, "critique_authored", fake_initial)
    monkeypatch.setattr(
        quality_service, "critique_authored_style_comparison", fake_compare
    )
    monkeypatch.setattr(quality_service, "revise_authored_deck", fake_revise)
    monkeypatch.setattr(quality_service, "inspect_authored_ocr", fake_ocr)
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
    monkeypatch.setattr(quality_service.version_service, "snapshot_slides", no_snapshot)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(presentation)
        session.add(slide)
        session.add(task)
        await session.commit()
        await quality_service.run_authored_quality_review(
            presentation.id,
            AuthoredQualityReviewRequest(
                scope="all",
                mode="analyze_only" if analyze_only else "analyze_and_fix",
            ),
            task,
            session,
        )
    await engine.dispose()
    return task, revise_calls, ocr_images


def test_analyze_only_clean_slide_routes_skip_and_reports_zero_ocr(
    tmp_path, monkeypatch
):
    task, revise_calls, ocr_images = _run(
        _style_scenario(tmp_path, monkeypatch, analyze_only=True)
    )

    assert revise_calls == []
    assert ocr_images == []
    assert task.data["metrics"]["ocr_executed"] is False
    assert task.data["metrics"]["ocr_requests"] == 0
    assert task.data["metrics"]["ocr_calls"] == 0
    check = task.data["ocr_checks"][0]
    _assert_private_ocr_contract(check)
    assert check["status"] == "routed_skip"
    assert check["skip_reason"] == "no_text_fidelity_signal"


def test_style_patch_preserves_text_and_exposes_normalized_ocr_contract(
    tmp_path, monkeypatch
):
    task, revise_calls, _ocr_images = _run(_style_scenario(tmp_path, monkeypatch))

    assert len(revise_calls) == 1
    assert task.data["fixed_slide_indices"] == [0]
    assert task.data["candidate_decisions"][0]["path"] == "local_patch"
    metrics = task.data["metrics"]
    assert metrics["ocr_executed"] is True
    assert metrics["ocr_requests"] == 3
    assert metrics["ocr_calls"] == 2
    assert metrics["ocr_cache_hits"] == 1
    assert metrics["stage_timings_ms"]["ocr"] >= 0
    check = task.data["ocr_checks"][0]
    _assert_private_ocr_contract(check)
    assert check["status"] == "executed"
    assert check["available"] is True
    assert check["comparison"]["no_regression"] is True
    assert check["baseline"]["role"] == "baseline_render"
    assert check["candidate"]["role"] == "candidate_render"


def test_ocr_regression_gets_one_bounded_fallback_and_reuses_baseline(
    tmp_path, monkeypatch
):
    task, revise_calls, ocr_images = _run(
        _style_scenario(
            tmp_path,
            monkeypatch,
            candidate_text="Revenue",
            fallback_text="Revenue 42%",
        )
    )

    assert len(revise_calls) == 2
    assert task.data["fixed_slide_indices"] == [0]
    decision = task.data["candidate_decisions"][0]
    assert decision["path"] == "full_fallback"
    assert decision["initial_rejection_reason"] == "ocr_regression"
    assert task.data["metrics"]["fallback_attempts"] == 1
    assert task.data["metrics"]["ocr_requests"] == 5
    assert task.data["metrics"]["ocr_calls"] == 3
    assert task.data["metrics"]["ocr_cache_hits"] == 2
    assert sum(b"original" in image for image in ocr_images) == 1
    check = task.data["ocr_checks"][0]
    assert len(check["candidate_attempts"]) == 2
    assert check["candidate"]["path"] == "full_fallback"


def test_ocr_unavailable_fails_closed_without_fallback_and_is_not_cached(
    tmp_path, monkeypatch
):
    task, revise_calls, ocr_images = _run(
        _style_scenario(tmp_path, monkeypatch, unavailable=True)
    )

    assert len(revise_calls) == 1
    assert task.data["fixed_slide_indices"] == []
    assert task.data["candidate_decisions"][0]["reason"] == "ocr_unavailable"
    assert task.data["metrics"]["fallback_attempts"] == 0
    # Failed baseline OCR is retried for the candidate gate, never cached.
    assert sum(b"original" in image for image in ocr_images) == 2
    check = task.data["ocr_checks"][0]
    _assert_private_ocr_contract(check)
    assert check["status"] == "unavailable"
    assert check["available"] is False


def test_exact_ocr_requires_strict_improvement_only_for_initial_content_loss(
    monkeypatch,
):
    async def scenario(baseline_text: str, candidate_text: str):
        async def fake_ocr(
            image, *, expected_text=None, language="eng", cache=None, **_
        ):
            mapping = {
                b"source": "Revenue 42%",
                b"baseline": baseline_text,
                b"candidate": candidate_text,
            }
            return _ocr_result(mapping[image], cache=cache)

        monkeypatch.setattr(quality_service, "inspect_authored_ocr", fake_ocr)
        runtime = quality_service._RequestOcrRuntime("eng")
        entry = await quality_service._capture_initial_ocr(
            runtime=runtime,
            slide_index=0,
            reference_mode=EXACT_RECONSTRUCTION,
            source_image=b"source",
            baseline_image=b"baseline",
            baseline_metrics={},
            signals=["vqa:overflow"],
        )
        required, accepted, reason = await quality_service._evaluate_candidate_ocr(
            runtime=runtime,
            entry=entry,
            reference_mode=EXACT_RECONSTRUCTION,
            source_image=b"source",
            baseline_image=b"baseline",
            candidate_image=b"candidate",
            baseline_metrics={},
            candidate_metrics={},
            signals=[],
            path="local_patch",
        )
        return required, accepted, reason, entry

    preserved = _run(scenario("Revenue 42%", "Revenue 42%"))
    unresolved = _run(scenario("Revenue", "Revenue"))
    repaired = _run(scenario("Revenue", "Revenue 42%"))

    assert preserved[:3] == (True, True, None)
    assert preserved[3]["comparison"]["strict_improvement_required"] is False
    assert unresolved[:3] == (True, False, "ocr_not_improved")
    assert unresolved[3]["comparison"]["strict_improvement_required"] is True
    assert repaired[:3] == (True, True, None)
    assert repaired[3]["comparison"]["strict_improvement"] is True
