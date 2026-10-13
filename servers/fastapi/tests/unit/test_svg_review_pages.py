"""reviewPages has one rule, shared by every writer of a published SVG deck."""

from services.generation_core.quality.page_outcome import review_page_indices


def test_only_verified_and_passed_pages_leave_review():
    qualities = [
        {"verified": True, "passed": True, "classification": "passed"},
        {"verified": False, "passed": True},
        {"verified": True, "passed": False},
        {},
        "not-a-mapping",
    ]
    assert review_page_indices(qualities) == [1, 2, 3, 4]


def test_a_fully_classified_clean_deck_has_no_review_pages():
    assert review_page_indices([{"verified": True, "passed": True}] * 3) == []


def test_svg_quality_status_public_projection_keeps_closed_states_and_no_source():
    from services.workspace_operation_runner import sanitize_workspace_event_payload

    result = sanitize_workspace_event_payload({
        "kind": "quality.review", "mode": "analyze_only", "source_format": "svg",
        "qualityStatus": {
            "artifact": {"status": "available", "pageCount": 2, "source": "private-source"},
            "measured": {"status": "passed", "currentPages": [0, 1, True, -1]},
            "semanticCurrent": {"status": "partial", "currentPages": [0]},
            "designFindings": {"status": "flagged", "flaggedPages": [0]},
            "totalPages": 2, "reviewedPages": [0], "advisoryOnly": True,
            "raw_response": "private-answer", "pageDigests": ["private-digest"],
        },
    }, operation_kind="quality.review")
    assert result["source_format"] == "svg" and result["mode"] == "analyze_only"
    assert result["qualityStatus"]["measured"] == {"status": "passed", "currentPages": [0, 1]}
    assert result["qualityStatus"]["semanticCurrent"]["status"] == "partial"
    assert "private" not in str(result)
    assert sanitize_workspace_event_payload({"qualityStatus": {"artifact": {"status": []}}},
        operation_kind="quality.review") == {"qualityStatus": {}}


def _review_slide(text="original", png=b"saved-render"):
    from types import SimpleNamespace
    from services.svg_contract import source_digest
    from utils.hashing import sha256_hex

    svg = f'<svg xmlns="http://www.w3.org/2000/svg"><text>{text}</text></svg>'
    return SimpleNamespace(svg_content=svg, is_svg=lambda: True,
        content={"svgReceipt": {"png_sha256": sha256_hex(png), "quality": {
            "source_digest": source_digest(svg), "png_digest": "sha256:" + sha256_hex(png),
            "measurement_complete": True, "passed": True, "verified": True}}})


def test_partial_review_preserves_only_current_untouched_page_evidence():
    from services.svg_export_visual_review import (
        assemble_page_advisory, current_page_advisories, split_page_advisories, svg_quality_status,
    )

    slides = [_review_slide("a"), _review_slide("b"), _review_slide("c")]
    original = split_page_advisories({"model": "reviewer", "slides": []}, slides, [0, 1, 2])
    slides[1] = _review_slide("changed")
    untouched = current_page_advisories(slides, original)
    assert set(untouched) == {0, 2}
    assert assemble_page_advisory(slides, list(untouched.values())) is None
    status = svg_quality_status(slides, None, page_advisories=list(untouched.values()))
    assert status["semanticCurrent"]["status"] == "partial"
    assert status["reviewedPages"] == [0, 2]
    revised = split_page_advisories({"model": "reviewer", "slides": [{"index": 0, "issues": ["text_too_small"]}]}, slides, [1])
    complete = assemble_page_advisory(slides, [*untouched.values(), *revised])
    assert complete is not None
    assert complete["vision"]["slides"][0]["index"] == 1
    status = svg_quality_status(slides, complete)
    assert status["semanticCurrent"]["status"] == "current"
    assert status["measured"]["status"] == "passed"
    assert status["designFindings"]["flaggedPages"] == [1]


def test_saved_png_change_cannot_rebind_old_page_review_to_new_render():
    from services.svg_export_visual_review import (
        assemble_page_advisory, current_page_advisories, legacy_page_advisories, split_page_advisories, svg_visual_review,
    )

    slides = [_review_slide()]
    records = split_page_advisories({"slides": []}, slides, [0])
    full = assemble_page_advisory(slides, records)
    slides[0] = _review_slide(png=b"different-render")
    assert current_page_advisories(slides, records) == {}
    assert legacy_page_advisories(slides, full) == []
    assert svg_visual_review(slides=slides, advisory=full)["reasonCode"] == "svg_visual_advisory_stale"


def test_legacy_svg_only_review_cannot_prove_current_png_or_unreviewed_subset():
    from services.generation_core.quality.advisory_record import advisory_record
    from services.svg_export_visual_review import (
        assemble_page_advisory, legacy_page_advisories, slide_page_digests,
        split_page_advisories, svg_quality_status, svg_visual_review,
    )

    slides = [_review_slide("a"), _review_slide("b")]
    legacy = advisory_record({"model": "old-reviewer", "slides": []}, slide_page_digests(slides))
    slides[0] = _review_slide("a", png=b"new-render-same-svg")
    assert legacy_page_advisories(slides, legacy) == []
    review = svg_visual_review(slides=slides, advisory=legacy)
    assert review["status"] == "failed"
    assert review["reasonCode"] == "svg_visual_advisory_png_evidence_missing"
    status = svg_quality_status(slides, legacy)
    assert status["semanticCurrent"]["status"] != "current"
    assert status["reviewedPages"] == []
    subset = split_page_advisories({"model": "new-reviewer", "slides": []}, slides, [0])
    assert assemble_page_advisory(slides, subset) is None
    status = svg_quality_status(slides, legacy, page_advisories=subset)
    assert status["semanticCurrent"]["status"] == "partial"
    assert status["reviewedPages"] == [0]


def test_incomplete_matching_measurement_is_never_reported_as_passed():
    from services.svg_export_visual_review import svg_quality_status

    slide = _review_slide()
    slide.content["svgReceipt"]["quality"]["measurement_complete"] = False
    status = svg_quality_status([slide], None)
    assert status["measured"]["status"] == "unmeasured"
    assert status["measured"]["currentPages"] == []


def test_svg_review_request_rejects_repair_mode_and_boolean_indices():
    import pytest
    from pydantic import ValidationError
    from models.svg_quality_review import SvgQualityReviewRequest

    for payload in ({"mode": "analyze_and_fix"}, {"scope": "subset", "slide_indices": [True]},
                    {"scope": "current", "slide_indices": []}):
        with pytest.raises(ValidationError):
            SvgQualityReviewRequest.model_validate(payload)


def _review_execution(monkeypatch, *, is_svg=True):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    import uuid
    from services import svg_quality_review_service as module

    slide = _review_slide()
    presentation = SimpleNamespace(owner_id=None, lifecycle_status="published", revision=7,
                                   is_svg=lambda: is_svg, theme={"svg": {"reviewPages": []}})
    session = SimpleNamespace(get=AsyncMock(return_value=presentation), scalar=AsyncMock(return_value=presentation),
                              add=Mock(), commit=AsyncMock(), rollback=AsyncMock())
    # Compatibility task lookup is separate from the presentation lookup.
    async def get(model, key):
        return presentation if model is module.PresentationModel else None
    session.get = AsyncMock(side_effect=get)
    @asynccontextmanager
    async def factory():
        yield session
    claim = SimpleNamespace(operation_id=uuid.uuid4(), presentation_id=uuid.uuid4(), owner_id=None, owner_scope=None,
        expected_revision=7, attempt_token="current-attempt", fencing_token=3, attempt_number=1,
        request_payload={"context": {"scope": "all", "slide_indices": [], "mode": "analyze_only"}})
    context = SimpleNamespace(claim=claim, progress=AsyncMock(return_value=True), raise_if_cancelled=AsyncMock())
    monkeypatch.setattr(module, "_assert_fence", AsyncMock())
    monkeypatch.setattr(module, "_slides", AsyncMock(return_value=[slide]))
    monkeypatch.setattr(module, "_read_png", lambda receipt: b"saved-render")
    monkeypatch.setattr(module, "run_vision_advisory", AsyncMock(return_value={"slides": [], "model": "reviewer"}))
    monkeypatch.setattr(module, "publish_presentation_revision", AsyncMock(return_value=7))
    return module, factory, context, presentation, slide, session


def test_analyze_only_handler_rejects_authored_before_provider_call(monkeypatch):
    import asyncio
    import pytest
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
    from services.workspace_quality_operation_handler import WorkspaceQualityReviewOperationHandler

    module, factory, context, _, _, _ = _review_execution(monkeypatch, is_svg=False)
    handler = WorkspaceQualityReviewOperationHandler(session_factory=factory)
    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_quality_review_svg_required"):
        asyncio.run(handler.execute(context))
    module.run_vision_advisory.assert_not_awaited()


def test_svg_review_publishes_advice_under_cas_without_revision_or_drawing_change(monkeypatch):
    import asyncio

    module, factory, context, presentation, slide, session = _review_execution(monkeypatch)
    before = slide.svg_content, slide.content.copy(), presentation.revision
    result = asyncio.run(module.execute_svg_quality_review(context, factory))
    assert result.result_revision == 7
    assert (slide.svg_content, slide.content, presentation.revision) == before
    assert result.payload["mode"] == "analyze_only"
    assert result.payload["fixed_count"] == 0
    kwargs = module.publish_presentation_revision.await_args.kwargs
    assert kwargs["advance_revision"] is False
    assert kwargs["expected_revision"] == 7
    assert kwargs["operation_id"] == context.claim.operation_id
    assert kwargs["attempt_token"] == "current-attempt"
    module.run_vision_advisory.assert_awaited_once()
    session.commit.assert_awaited_once()


def test_svg_review_cas_fence_conflict_rolls_back_advice_and_reports_lease_loss(monkeypatch):
    import asyncio
    import pytest
    from services.presentation_publication_service import PresentationPublicationConflict
    from services.workspace_operation_handler_registry import WorkspaceOperationLeaseLost
    from services.svg_quality_review_service import _assert_fence as assert_fence
    from unittest.mock import AsyncMock

    module, factory, context, _, _, session = _review_execution(monkeypatch)
    module.publish_presentation_revision.side_effect = PresentationPublicationConflict("stale_operation_fence")
    with pytest.raises(WorkspaceOperationLeaseLost):
        asyncio.run(module.execute_svg_quality_review(context, factory))
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
    context.claim.owner_scope = "fixture-owner"
    acquired = []
    statements = []
    async def admission(*_):
        acquired.append("admission")
    async def operation(statement):
        acquired.append("operation")
        statements.append(statement)
        return context.claim.operation_id
    monkeypatch.setattr(module, "_public_admission_for_operation", AsyncMock(side_effect=admission))
    session.scalar = AsyncMock(side_effect=operation)
    asyncio.run(assert_fence(session, context.claim, lock=True))
    assert acquired == ["admission", "operation"]
    assert statements[-1]._for_update_arg is not None
    predicate = str(statements[-1])
    assert all(name in predicate for name in ("owner_id", "owner_scope", "fencing_token",
        "attempt_token", "cancel_requested_at", "lease_expires_at", "expected_revision"))
    acquired.clear()
    asyncio.run(assert_fence(session, context.claim, lock=False))
    assert acquired == ["operation"] and statements[-1]._for_update_arg is None
    session.scalar = AsyncMock(return_value=None)
    with pytest.raises(WorkspaceOperationLeaseLost):
        asyncio.run(assert_fence(session, context.claim, lock=True))


def test_stale_measurement_and_missing_saved_png_stop_svg_review_before_provider(monkeypatch):
    import asyncio
    import pytest
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError

    module, factory, context, _, slide, _ = _review_execution(monkeypatch)
    slide.content["svgReceipt"]["quality"]["source_digest"] = "sha256:stale"
    with pytest.raises(NonRetryableWorkspaceOperationError, match="measurement_stale"):
        asyncio.run(module.execute_svg_quality_review(context, factory))
    module.run_vision_advisory.assert_not_awaited()
    module, factory, context, _, slide, _ = _review_execution(monkeypatch)
    slide.content["svgReceipt"]["quality"]["measurement_complete"] = False
    with pytest.raises(NonRetryableWorkspaceOperationError, match="measurement_stale"):
        asyncio.run(module.execute_svg_quality_review(context, factory))
    module.run_vision_advisory.assert_not_awaited()
    module, factory, context, _, _, _ = _review_execution(monkeypatch)
    monkeypatch.setattr(module, "_read_png", lambda receipt: None)
    with pytest.raises(NonRetryableWorkspaceOperationError, match="render_unavailable"):
        asyncio.run(module.execute_svg_quality_review(context, factory))
    module.run_vision_advisory.assert_not_awaited()


def test_skipped_svg_advisory_never_publishes_task_or_theme_success(monkeypatch):
    import asyncio
    from copy import deepcopy
    import pytest
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError

    module, factory, context, presentation, _, session = _review_execution(monkeypatch)
    before = deepcopy(presentation.theme)
    module.run_vision_advisory.return_value = {"skipped": "design_review_schema_or_coverage_invalid",
        "schemaRejectionReason": "axis_keys", "parsedPageCount": 1, "parsedPageIndexCoverage": True,
        "schemaRejectionFields": {"fieldNames": ["private-response-field"]}}
    with pytest.raises(NonRetryableWorkspaceOperationError, match="svg_quality_review_advisory_unavailable") as failure:
        asyncio.run(module.execute_svg_quality_review(context, factory))
    assert failure.value.detail["stage"] == "advisory:design_review_schema_or_coverage_invalid:axis_keys:terminal=unmeasured:indices=complete"
    assert failure.value.detail["current"] == failure.value.detail["total"] == 1
    assert "private-response-field" not in str(failure.value.detail)
    assert presentation.theme == before
    module.run_vision_advisory.assert_awaited_once()
    module.publish_presentation_revision.assert_not_awaited()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


def test_measurement_becoming_incomplete_during_review_blocks_publication(monkeypatch):
    import asyncio
    from copy import deepcopy
    import pytest
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError

    module, factory, context, presentation, slide, session = _review_execution(monkeypatch)
    before = deepcopy(presentation.theme)
    async def incomplete_after_review(*args, **kwargs):
        slide.content["svgReceipt"]["quality"]["measurement_complete"] = False
        return {"model": "reviewer", "slides": []}
    module.run_vision_advisory.side_effect = incomplete_after_review
    with pytest.raises(NonRetryableWorkspaceOperationError, match="measurement_stale"):
        asyncio.run(module.execute_svg_quality_review(context, factory))
    assert presentation.theme == before
    module.run_vision_advisory.assert_awaited_once()
    module.publish_presentation_revision.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_final_review_selection_rejects_client_evidence_and_partial_artifact_scope():
    import uuid
    import pytest
    from pydantic import ValidationError
    from models.svg_quality_review import SvgQualityReviewRequest
    artifact = uuid.uuid4()
    assert SvgQualityReviewRequest().target == "source_svg"
    assert SvgQualityReviewRequest(target="final_pptx", artifact_id=artifact).artifact_id == artifact
    for payload in ({"target": "final_pptx"},
                    {"target": "source_svg", "artifact_id": artifact},
                    {"target": "final_pptx", "artifact_id": artifact, "scope": "subset", "slide_indices": [0]},
                    {"target": "final_pptx", "artifact_id": artifact, "status": "passed"},
                    {"target": "final_pptx", "artifact_id": artifact, "evidence": {}}):
        with pytest.raises(ValidationError):
            SvgQualityReviewRequest.model_validate(payload)


def test_svg_review_admission_owns_one_call_and_zero_retry_without_source_change(monkeypatch):
    import asyncio
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import services.svg_quality_review_service as review
    import services.public_operation_admission as admission
    owner = uuid.uuid4()
    slide = _review_slide()
    before = slide.svg_content, slide.content.copy()
    presentation = SimpleNamespace(id=uuid.uuid4(), owner_id=owner, theme={})
    operation = SimpleNamespace(id=uuid.uuid4(), owner_id=owner, max_attempts=3,
        request_payload={"context": {"mode": "analyze_only", "scope": "all"}})
    row = SimpleNamespace(retry_request_limit=9)
    admitted = AsyncMock(return_value=row)
    monkeypatch.setattr(admission, "admit_public_operation_compatible_v2", admitted)
    monkeypatch.setattr(admission, "current_principal_context", lambda: SimpleNamespace(owner_id=owner))
    monkeypatch.setattr(review, "_slides", AsyncMock(return_value=[slide]))
    monkeypatch.setattr(review, "_read_png", lambda receipt: b"saved-render")
    session = SimpleNamespace(flush=AsyncMock())
    asyncio.run(review.admit_svg_quality_review_operation(session, operation=operation, presentation=presentation))
    plan = admitted.await_args.kwargs["request_plan"]
    assert plan.initial_request_limit == 1 and plan.retry_request_limit == 0
    assert row.retry_request_limit == 0 and operation.max_attempts == 1
    assert (slide.svg_content, slide.content) == before
