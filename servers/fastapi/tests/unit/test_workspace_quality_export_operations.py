from __future__ import annotations

from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.public_artifact import PublicArtifact
from models.sql.public_admission import PublicOperationAdmission
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceArtifact,
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import WorkspaceCommandRequest
from services import workspace_artifact_publication_service as publication
from services.public_export_operation import PublicExportOperationError
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import (
    finalize_workspace_operation_success,
    sanitize_workspace_event_payload,
)
from services.workspace_quality_export_operation_handlers import (WorkspaceExportOperationHandler)
from services.workspace_quality_operation_handler import WorkspaceQualityReviewOperationHandler
from services.workspace_quality_operation_handler import (
    build_export_release_review_receipt,
    validate_export_release_review_receipt,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id

TABLES = [
    User.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    PresentationExportAttempt.__table__,
    WorkspaceArtifact.__table__,
    PublicArtifact.__table__,
    PublicOperationAdmission.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
]


def test_release_review_receipt_is_evidence_and_revision_bound() -> None:
    review_operation_id = uuid.uuid4()
    admission = SimpleNamespace(
        id=uuid.uuid4(),
        planned_request_limit=8,
        retry_request_limit=2,
        initial_request_count=2,
        retry_request_count=1,
        estimated_consumed_microunits=120,
        actual_consumed_microunits=115,
        actual_cost_unavailable_count=0,
    )
    measured = {
        "reviewed_count": 2,
        "remaining_issue_count": 0,
        "incomplete_measurement_count": 0,
        "unresolved_confirmed_issue_count": 0,
        "fixed_count": 1,
        "slide_results": [
            {"slide_index": 0, "remaining_issues": []},
            {"slide_index": 1, "remaining_issues": []},
        ],
    }
    runtime_binding_digest = "sha256:" + "d" * 64
    source_render_identity_digest = "sha256:" + "e" * 64
    result_render_identity_digest = "sha256:" + "f" * 64
    checker_policy_digest = "sha256:" + "1" * 64
    post_review_gate = {
        "schemaVersion": "export-release-post-review-gate/v2",
        "profileId": "ordinary",
        "baseDeckHtmlDigest": "sha256:" + "a" * 64,
        "resultDeckHtmlDigest": "sha256:" + "c" * 64,
        "renderRuntimeBindingDigest": runtime_binding_digest,
        "sourceRenderIdentityDigest": source_render_identity_digest,
        "resultRenderIdentityDigest": result_render_identity_digest,
        "checkerPolicyDigest": checker_policy_digest,
        "fixedCount": 1,
        "slideCount": 2,
        "issues": [],
        "gatePassed": True,
    }
    post_review_gate["evidenceDigest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            post_review_gate,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    receipt = build_export_release_review_receipt(
        operation_id=review_operation_id,
        source_revision=7,
        result_revision=8,
        slide_count=2,
        review_result=measured,
        admission=admission,
        base_deck_html_digest="sha256:" + "a" * 64,
        base_quality_receipt_digest="sha256:" + "b" * 64,
        result_deck_html_digest="sha256:" + "c" * 64,
        render_runtime_binding_digest=runtime_binding_digest,
        source_render_identity_digest=source_render_identity_digest,
        result_render_identity_digest=result_render_identity_digest,
        checker_policy_digest=checker_policy_digest,
        base_evidence_kind="generation-quality-v3",
        base_evidence_operation_id=None,
        post_review_gate=post_review_gate,
    )
    assert receipt["gatePassed"] is True
    assert validate_export_release_review_receipt(
        receipt,
        operation_id=review_operation_id,
        source_revision=7,
        result_revision=8,
    ) == receipt["receiptDigest"].removeprefix("sha256:")
    safe_receipt = sanitize_workspace_event_payload(
        {"release_review_receipt": receipt},
        operation_kind="quality.review",
    )["release_review_receipt"]
    assert validate_export_release_review_receipt(
        safe_receipt,
        operation_id=review_operation_id,
        source_revision=7,
        result_revision=8,
    ) == receipt["receiptDigest"].removeprefix("sha256:")

    blocked = build_export_release_review_receipt(
        operation_id=review_operation_id,
        source_revision=7,
        result_revision=7,
        slide_count=2,
        review_result={**measured, "incomplete_measurement_count": 1},
        admission=admission,
        base_deck_html_digest="sha256:" + "a" * 64,
        base_quality_receipt_digest="sha256:" + "b" * 64,
        result_deck_html_digest="sha256:" + "c" * 64,
        render_runtime_binding_digest=runtime_binding_digest,
        source_render_identity_digest=source_render_identity_digest,
        result_render_identity_digest=result_render_identity_digest,
        checker_policy_digest=checker_policy_digest,
        base_evidence_kind="generation-quality-v3",
        base_evidence_operation_id=None,
        post_review_gate=post_review_gate,
    )
    assert blocked["gatePassed"] is False
    with pytest.raises(
        NonRetryableWorkspaceOperationError,
        match="release_quality_review_blocked",
    ):
        validate_export_release_review_receipt(
            blocked,
            operation_id=review_operation_id,
            source_revision=7,
            result_revision=7,
        )


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_export(maker, *, revision: int = 7, mode: str = "authored"):
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="export",
            n_slides=1,
            language="ko",
            mode=mode,
            theme={},
            revision=revision,
        )
        if mode == "authored":
            # Authored decks predate the driver removal (2026-11-10).
            await add_existing_authored_presentation(session, presentation)
        else:
            session.add(presentation)
        await session.flush()
        conversation = WorkspaceConversation(presentation_id=presentation.id)
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            conversation_id=conversation.id,
            presentation_id=presentation.id,
            anchor_position=1,
            kind="export.pptx",
            state="running",
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            request_payload={},
            expected_revision=revision,
            attempt_token="attempt-1",
            fencing_token=3,
        )
        session.add(operation)
        await session.flush()
    return presentation.id, operation.id


def test_command_contract_accepts_quality_and_export_and_rejects_credentials():
    for kind in ("quality.review", "export.pptx", "export.pdf"):
        request = WorkspaceCommandRequest(
            command="실행",
            command_type=kind,
            idempotency_key=f"key-{kind}",
            expected_revision=2,
        )
        assert request.command_type == kind
    with pytest.raises(ValidationError):
        WorkspaceCommandRequest(
            command="내보내기",
            command_type="export.pdf",
            idempotency_key="secret-test",
            context={"cookie": "must-not-persist"},
        )


def test_quality_review_result_sanitizer_keeps_only_user_visible_summary():
    payload = {
        "kind": "quality.review",
        "message": "검수가 완료되었습니다.",
        "mode": "analyze_and_fix",
        "scope": "all",
        "inspected_revision": 11,
        "reviewed_count": 3,
        "issue_slide_count": 2,
        "fixed_count": 1,
        "fixed_slide_indices": [0, True, -1, 2],
        "remaining_issue_count": 1,
        "remaining_slide_indices": [2],
        "pattern_cluster_count": 2,
        "pattern_recipe_count": 1,
        "pattern_reuse_attempts": 3,
        "pattern_reuse_hits": 2,
        "pattern_reuse_applied": 1,
        "remaining_issues": [
            {
                "slide_index": 2,
                "issues": [
                    {
                        "type": "low-contrast",
                        "severity": "medium",
                        "detail": "본문 대비를 확인하세요.",
                        "fix_action": "contrast-adjustment",
                        "applied": False,
                        "provider_trace": "must-not-persist",
                    }
                ],
            }
        ],
        "version_saved": True,
        "slide_results": [
            {
                "slide_index": 2,
                "path": "automatic-layout-fix",
                "attempted": True,
                "applied": True,
                "resolved_issues": ["low-contrast"],
                "candidate_rejections": [
                    {
                        "plan_id": "rp-safe",
                        "source": "deterministic",
                        "render_status": "rendered",
                        "validation_status": "rejected",
                        "reason_codes": ["dom.unintended_overlap"],
                        "private_dom": "must-not-persist",
                    }
                ],
                "before": "private slide HTML",
                "after": "private fixed HTML",
                "element_changes": [{"dom": "private DOM"}],
            }
        ],
        "local_checks": [{"dom": "private telemetry"}],
        "ocr_checks": [{"text": "private OCR output"}],
    }

    assert sanitize_workspace_event_payload(
        payload, operation_kind="quality.review"
    ) == {
        "kind": "quality.review",
        "message": "검수가 완료되었습니다.",
        "mode": "analyze_and_fix",
        "scope": "all",
        "inspected_revision": 11,
        "reviewed_count": 3,
        "issue_slide_count": 2,
        "fixed_count": 1,
        "fixed_slide_indices": [0, 2],
        "remaining_issue_count": 1,
        "remaining_slide_indices": [2],
        "pattern_cluster_count": 2,
        "pattern_recipe_count": 1,
        "pattern_reuse_attempts": 3,
        "pattern_reuse_hits": 2,
        "pattern_reuse_applied": 1,
        "remaining_issues": [
            {
                "slide_index": 2,
                "type": "low-contrast",
                "severity": "medium",
                "message": "본문 대비를 확인하세요.",
                "description": "본문 대비를 확인하세요.",
                "fix_action": "contrast-adjustment",
                "applied": False,
            }
        ],
        "slide_results": [
            {
                "slide_index": 2,
                "attempted": True,
                "applied": True,
                "fix_action": "automatic-layout-fix",
                "resolved_issues": ["low-contrast"],
                "candidate_rejections": [
                    {
                        "plan_id": "rp-safe",
                        "source": "deterministic",
                        "render_status": "rendered",
                        "validation_status": "rejected",
                        "reason_codes": ["dom.unintended_overlap"],
                    }
                ],
            }
        ],
        "version_saved": True,
    }


def test_quality_review_result_sanitizer_promotes_maximum_repair_telemetry():
    payload = {
        "kind": "quality.review",
        "issues": [
            {
                "slide_index": 1,
                "issues": [{"code": "overflow", "detail": "Title is clipped."}],
            }
        ],
        "remaining_issues": [
            {
                "slide_index": 3,
                "issues": [{"code": "contrast", "detail": "Contrast remains low."}],
            }
        ],
        "fixed_slide_indices": [1, True, 2],
        "remaining_issue_indices": [3, -1, True],
        # Public aggregates may originate from either legacy top-level output
        # or the executor's nested telemetry; private nested fields may not.
        "metrics": {
            "reviewed_count": 4,
            "issue_slide_count": 2,
            "confirmed_issue_count": 3,
            "fixed_count": 2,
            "resolved_issue_count": 2,
            "remaining_slide_count": 1,
            "remaining_issue_count": 1,
            "pattern_cluster_count": 2,
            "pattern_representative_count": 3,
            "pattern_recipe_count": 2,
            "pattern_reuse_attempts": 4,
            "pattern_reuse_hits": 3,
            "pattern_reuse_applied": 2,
            "composite_plan_count": 1,
            "composite_operations_merged": 2,
            "render_candidates_rejected_early": 5,
            "full_render_count": 2,
            "llm_pattern_call_count": 1,
            "llm_eligible_count": 8,
            "llm_response_count": 1,
            "llm_schema_pass_count": 1,
            "llm_valid_plan_count": 1,
            "llm_provider_failure_count": 2,
            "llm_schema_reject_count": 3,
            "llm_preflight_pass_count": 1,
            "llm_rendered_count": 1,
            "proposal_preflight_unprojectable_count": 4,
            "negative_cache_hit_count": 5,
            "llm_circuit_open_count": 2,
            "no_candidate_reasons": [
                "llm_provider_error",
                "negative_cache_hit",
                "llm_provider_error",
                7,
            ],
            "individual_fallback_count": 1,
            "total_ms": 91.25,
            "deck_wall_ms": 44.5,
            "stage_timings_ms": {
                "preflight": 3.5,
                "render": 20,
                "private_trace": "must-not-persist",
                "bad": -1,
            },
            "private_candidate_diagnostics": [{"html": "must-not-persist"}],
        },
        # Explicit top-level values win over nested compatibility aliases.
        "resolved_issue_count": 3,
        "candidate_decisions": [{"model_prompt": "must-not-persist"}],
    }

    safe = sanitize_workspace_event_payload(payload, operation_kind="quality.review")

    assert safe["detailed_issue_count"] == 3
    assert safe["confirmed_issue_count"] == 3
    assert safe["resolved_issue_count"] == 3
    assert safe["remaining_issue_indices"] == [3]
    assert safe["fixed_slide_indices"] == [1, 2]
    assert safe["stage_timings_ms"] == {"preflight": 3.5, "render": 20.0}
    assert safe["total_ms"] == 91.25
    assert safe["wall_time_ms"] == 44.5
    assert safe["aggregate_stage_time_ms"] == 23.5
    assert {
        key: safe[key]
        for key in (
            "pattern_cluster_count",
            "pattern_representative_count",
            "pattern_recipe_count",
            "pattern_reuse_attempts",
            "pattern_reuse_hits",
            "pattern_reuse_applied",
            "composite_plan_count",
            "composite_operations_merged",
            "render_candidates_rejected_early",
            "full_render_count",
            "llm_pattern_call_count",
            "llm_eligible_count",
            "llm_response_count",
            "llm_schema_pass_count",
            "llm_valid_plan_count",
            "llm_provider_failure_count",
            "llm_schema_reject_count",
            "llm_preflight_pass_count",
            "llm_rendered_count",
            "proposal_preflight_unprojectable_count",
            "negative_cache_hit_count",
            "llm_circuit_open_count",
            "individual_fallback_count",
        )
    } == {
        "pattern_cluster_count": 2,
        "pattern_representative_count": 3,
        "pattern_recipe_count": 2,
        "pattern_reuse_attempts": 4,
        "pattern_reuse_hits": 3,
        "pattern_reuse_applied": 2,
        "composite_plan_count": 1,
        "composite_operations_merged": 2,
        "render_candidates_rejected_early": 5,
        "full_render_count": 2,
        "llm_pattern_call_count": 1,
        "llm_eligible_count": 8,
        "llm_response_count": 1,
        "llm_schema_pass_count": 1,
        "llm_valid_plan_count": 1,
        "llm_provider_failure_count": 2,
        "llm_schema_reject_count": 3,
        "llm_preflight_pass_count": 1,
        "llm_rendered_count": 1,
        "proposal_preflight_unprojectable_count": 4,
        "negative_cache_hit_count": 5,
        "llm_circuit_open_count": 2,
        "individual_fallback_count": 1,
    }
    assert safe["no_candidate_reasons"] == [
        "llm_provider_error",
        "negative_cache_hit",
    ]
    assert "metrics" not in safe
    assert "candidate_decisions" not in safe


def test_quality_review_result_sanitizer_rejects_nonfinite_or_negative_telemetry():
    safe = sanitize_workspace_event_payload(
        {
            "metrics": {
                "full_render_count": -1,
                "llm_pattern_call_count": float("nan"),
                "resolved_issue_count": 1.5,
                "stage_timings_ms": {"valid": 1, "infinite": float("inf")},
            }
        },
        operation_kind="quality.review",
    )

    assert "full_render_count" not in safe
    assert "llm_pattern_call_count" not in safe
    assert "resolved_issue_count" not in safe
    assert safe["stage_timings_ms"] == {"valid": 1.0}


def test_quality_review_result_sanitizer_removes_invalid_top_level_telemetry():
    safe = sanitize_workspace_event_payload(
        {
            "full_render_count": -1,
            "resolved_issue_count": 1.5,
            "total_ms": float("nan"),
            "wall_time_ms": float("inf"),
            "aggregate_stage_time_ms": -2,
            "no_candidate_reasons": [7, "", "provider_error"],
        },
        operation_kind="quality.review",
    )

    assert "full_render_count" not in safe
    assert "resolved_issue_count" not in safe
    assert "total_ms" not in safe
    assert "wall_time_ms" not in safe
    assert "aggregate_stage_time_ms" not in safe
    assert safe["no_candidate_reasons"] == ["provider_error"]


def test_quality_review_parallel_stage_aggregate_is_not_capped_like_a_count():
    safe = sanitize_workspace_event_payload(
        {
            "kind": "quality.review",
            "metrics": {
                "deck_wall_ms": 608_000.0,
                "stage_timings_ms": {
                    "llm_proposal": 557_264.0,
                    "ocr_vqa": 367_892.0,
                    "render": 222_175.0,
                },
            },
        },
        operation_kind="quality.review",
    )

    assert safe["wall_time_ms"] == 608_000.0
    assert safe["aggregate_stage_time_ms"] == 1_147_331.0


@pytest.mark.parametrize("mode", ["unsupported", "analyze_and_fix"])
def test_quality_review_rejects_unknown_mode_before_execution(mode):
    claim = WorkspaceOperationClaim(
        operation_id=uuid.uuid4(),
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        kind="quality.review",
        request_payload={"context": {"mode": mode}},
        expected_revision=0,
        attempt_number=1,
        attempt_token="attempt",
        fencing_token=1,
    )

    class Context:
        async def extend_lease(self, *, seconds):
            self.extended_lease_seconds = seconds
            return True

        async def raise_if_cancelled(self):
            return None

        async def progress(self, **_kwargs):
            return True

    context = Context()
    context.claim = claim
    with pytest.raises(
        NonRetryableWorkspaceOperationError,
        match="quality_review_request_invalid",
    ):
        asyncio.run(WorkspaceQualityReviewOperationHandler().execute(context))


def test_export_publication_is_immutable_fenced_and_revision_neutral(
    tmp_path: Path, monkeypatch
):
    async def scenario():
        engine, maker = await _database(tmp_path / "export.db")
        app_data = tmp_path / "app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"immutable pptx")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        monkeypatch.setenv("WORKSPACE_EXPORT_ARTIFACT_TTL_SECONDS", "3600")
        try:
            presentation_id, operation_id = await _seed_export(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pptx",
                )
                result_revision = await publication.publish_workspace_export_artifact(
                    session,
                    artifact=artifact,
                    attempt_token="attempt-1",
                    fencing_token=3,
                    export_format="pptx",
                )
            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                artifact_row = await session.get(WorkspaceArtifact, artifact.artifact_id)
                terminal = await session.scalar(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == operation_id,
                        WorkspaceOperationEvent.event_type == "operation.result",
                    )
                )
                outbox = await session.scalar(
                    select(WorkspaceOutbox).where(
                        WorkspaceOutbox.operation_id == operation_id
                    )
                )
                assert result_revision == presentation.revision == 7
                assert operation.state == "succeeded"
                assert operation.progress == {
                    "stage": "export_completed",
                    "percent": 100,
                }
                assert operation.result["source_revision"] == 7
                assert "expires_at" in operation.result
                assert "path" not in operation.result
                assert "cookie" not in str(operation.result).lower()
                assert artifact_row.status == "ready"
                assert artifact_row.expires_at is not None
                assert artifact.absolute_path.read_bytes() == b"immutable pptx"
                assert terminal is not None and outbox is not None
                assert terminal.payload["percent"] == 100

            claim = WorkspaceOperationClaim(
                operation_id=operation_id,
                owner_id=None,
                owner_scope="anonymous",
                conversation_id=operation.conversation_id,
                presentation_id=presentation_id,
                kind="export.pptx",
                request_payload={},
                expected_revision=7,
                attempt_number=1,
                attempt_token="attempt-1",
                fencing_token=3,
            )
            assert await finalize_workspace_operation_success(
                claim,
                WorkspaceOperationResult(
                    payload={"status": "handler-returned"},
                    result_revision=7,
                ),
                session_factory=maker,
            )
            async with maker() as session:
                operation = await session.get(WorkspaceOperation, operation_id)
                events = (
                    await session.scalars(
                        select(WorkspaceOperationEvent).where(
                            WorkspaceOperationEvent.operation_id == operation_id,
                            WorkspaceOperationEvent.event_type == "operation.result",
                        )
                    )
                ).all()
                assert operation.result["artifact_id"] == str(artifact.artifact_id)
                assert "status" not in operation.result
                assert len(events) == 1

                resolved = await publication.resolve_workspace_export_artifact(
                    session,
                    artifact_id=artifact.artifact_id,
                )
                assert resolved.absolute_path == artifact.absolute_path
                assert resolved.absolute_path.is_file()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error_code", "retryable", "recorded_code", "recorded_state"),
    [
        ("svg_source_export_format_unsupported", False, "svg_source_export_format_unsupported", "failed"),
        # The SVG path used to re-raise this bare; the runner recorded it as a
        # retryable internal error and retried an expired admission to dead_letter.
        (
            "public_export_admission_deadline_exhausted",
            False,
            "public_export_admission_deadline_exhausted",
            "failed",
        ),
        ("public_export_worker_failed", True, "presentation_export_failed", "queued"),
    ],
)
def test_svg_export_failures_use_the_shared_public_export_classification(
    tmp_path: Path,
    monkeypatch,
    error_code: str,
    retryable: bool,
    recorded_code: str,
    recorded_state: str,
) -> None:
    from services.workspace_operation_handler_registry import WorkspaceOperationExecutionError
    from services.workspace_operation_runner import _transition_claim_failure

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "svg-export-error.db")
        try:
            presentation_id, operation_id = await _seed_export(maker, mode="svg")
            async with maker() as session, session.begin():
                operation = await session.get(WorkspaceOperation, operation_id)

            async def fail_svg_export(*_args, **_kwargs):
                raise PublicExportOperationError(error_code)

            monkeypatch.setattr(
                "services.svg_export_operation.execute_svg_workspace_export",
                fail_svg_export,
            )
            claim = WorkspaceOperationClaim(
                operation_id=operation_id,
                owner_id=None,
                owner_scope="anonymous",
                conversation_id=operation.conversation_id,
                presentation_id=presentation_id,
                kind="export.pdf",
                request_payload={},
                expected_revision=7,
                attempt_number=1,
                attempt_token="attempt-1",
                fencing_token=3,
            )
            context = SimpleNamespace(
                claim=claim,
                raise_if_cancelled=lambda: asyncio.sleep(0),
            )
            with pytest.raises(WorkspaceOperationExecutionError) as raised:
                await WorkspaceExportOperationHandler(
                    session_factory=maker,
                    internal_header_provider=lambda: None,
                ).execute(context)
            error = raised.value
            assert error.retryable is retryable
            assert error.code == recorded_code
            assert isinstance(error.__cause__, PublicExportOperationError)
            if retryable:
                assert error.detail["reason_code"] == error_code

            # The runner records exactly what the handler classified.
            assert await _transition_claim_failure(
                claim,
                code=error.code,
                retryable=error.retryable,
                detail=error.detail,
                session_factory=maker,
            )
            async with maker() as session:
                recorded = await session.get(WorkspaceOperation, operation_id)
            assert recorded.state == recorded_state
            assert recorded.error_code == recorded_code
            assert recorded.error_detail["retryable"] is retryable
            if retryable:
                assert recorded.error_detail["reason_code"] == error_code
            else:
                assert recorded.next_attempt_at is None
                assert recorded.error_detail["retry_count"] == recorded.retry_count
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_export_attempt_and_artifact_terminalize_in_one_publication_commit(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "linked-attempt.db")
        app_data = tmp_path / "app-data"
        source = app_data / "exports" / "linked.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"linked immutable pptx")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_export(maker)
            source_snapshot = "c" * 64
            provenance = {
                "schema_version": 1,
                "adapter_chain": ["general"],
                "capability_evidence": {"selected_adapter": "general"},
                "fallback_reason": None,
                "asset_digests": {},
                "compiler_runtime": {
                    "compiler": "presentation-export",
                    "compiler_version": "0.4.3",
                    "runtime": "chromium-dom-export",
                    "runtime_version": "presenton.render-artifact/v1",
                },
                "freshness": {
                    "source_revision": 7,
                    "source_snapshot_sha256": source_snapshot,
                    "package_sha256": None,
                    "asset_digests_match": True,
                    "package_digest_match": True,
                },
            }
            async with maker() as session, session.begin():
                attempt = PresentationExportAttempt(
                    owner_id=None,
                    owner_scope="anonymous",
                    presentation_id=presentation_id,
                    workspace_operation_id=operation_id,
                    format="pptx",
                    state="running",
                    source_revision=7,
                    source_snapshot_sha256=source_snapshot,
                    request_hash="b" * 64,
                    idempotency_key_hash="a" * 64,
                    requested_adapter="auto",
                    selected_adapter=None,
                    provenance_schema_version=1,
                    provenance=provenance,
                    started_at=datetime.now(UTC),
                )
                session.add(attempt)
                await session.flush()
                attempt_id = attempt.id

            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pptx",
                    export_attempt_id=attempt_id,
                    expected_source_sha256=hashlib.sha256(
                        b"linked immutable pptx"
                    ).hexdigest(),
                    expected_source_size_bytes=len(b"linked immutable pptx"),
                )
                await publication.publish_workspace_export_artifact(
                    session,
                    artifact=artifact,
                    attempt_token="attempt-1",
                    fencing_token=3,
                    export_format="pptx",
                    export_source_snapshot_sha256=source_snapshot,
                    export_selected_adapter="general",
                    export_provenance=provenance,
                )

            async with maker() as session:
                attempt = await session.get(PresentationExportAttempt, attempt_id)
                artifact_row = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                assert attempt.state == "succeeded"
                assert attempt.artifact_sha256 == artifact.sha256
                assert attempt.artifact_size_bytes == artifact.size_bytes
                assert artifact_row.export_attempt_id == attempt_id
                assert artifact_row.status == "ready"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_export_artifact_mutation_before_publication_compensates_without_success(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "artifact-toctou.db")
        app_data = tmp_path / "app-data-toctou"
        source = app_data / "exports" / "source.pptx"
        source.parent.mkdir(parents=True)
        payload = b"router-verified-pptx"
        source.write_bytes(payload)
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_export(maker)
            source_snapshot = "c" * 64
            provenance = {
                "schema_version": 1,
                "adapter_chain": ["general"],
                "capability_evidence": {"selected_adapter": "general"},
                "fallback_reason": None,
                "asset_digests": {},
                "compiler_runtime": {
                    "compiler": "presentation-export",
                    "compiler_version": "0.4.3",
                    "runtime": "chromium-dom-export",
                    "runtime_version": "presenton.render-artifact/v1",
                },
                "freshness": {
                    "source_revision": 7,
                    "source_snapshot_sha256": source_snapshot,
                    "package_sha256": None,
                    "asset_digests_match": True,
                    "package_digest_match": True,
                },
            }
            async with maker() as session, session.begin():
                attempt = PresentationExportAttempt(
                    owner_id=None,
                    owner_scope="anonymous",
                    presentation_id=presentation_id,
                    workspace_operation_id=operation_id,
                    format="pptx",
                    state="running",
                    source_revision=7,
                    source_snapshot_sha256=source_snapshot,
                    request_hash="b" * 64,
                    idempotency_key_hash="a" * 64,
                    requested_adapter="auto",
                    provenance_schema_version=1,
                    provenance=provenance,
                    started_at=datetime.now(UTC),
                )
                session.add(attempt)
                await session.flush()
                attempt_id = attempt.id

            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pptx",
                    export_attempt_id=attempt_id,
                    expected_source_sha256=hashlib.sha256(payload).hexdigest(),
                    expected_source_size_bytes=len(payload),
                )
                artifact.absolute_path.write_bytes(b"mutated after prepare")
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError,
                    match="workspace_artifact_source_digest_mismatch",
                ):
                    await publication.publish_workspace_export_artifact(
                        session,
                        artifact=artifact,
                        attempt_token="attempt-1",
                        fencing_token=3,
                        export_format="pptx",
                        export_source_snapshot_sha256=source_snapshot,
                        export_selected_adapter="general",
                        export_provenance=provenance,
                    )

            assert not artifact.absolute_path.exists()
            async with maker() as session:
                artifact_row = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                attempt = await session.get(PresentationExportAttempt, attempt_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert artifact_row is not None and artifact_row.status == "deleted"
                assert attempt is not None and attempt.state == "running"
                assert operation is not None and operation.state == "running"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_export_lease_loss_compensates_and_expired_artifact_is_not_resolved(
    tmp_path: Path, monkeypatch
):
    async def scenario():
        engine, maker = await _database(tmp_path / "lease-and-expiry.db")
        app_data = tmp_path / "app-data-lease"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"lease")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_export(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pptx",
                )
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError,
                    match="operation_lease_lost",
                ):
                    await publication.publish_workspace_export_artifact(
                        session,
                        artifact=artifact,
                        attempt_token="stale-attempt",
                        fencing_token=2,
                        export_format="pptx",
                    )
            assert not artifact.absolute_path.exists()

            source.write_bytes(b"expiry")
            presentation_id, operation_id = await _seed_export(maker, revision=7)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pptx",
                )
                await publication.publish_workspace_export_artifact(
                    session,
                    artifact=artifact,
                    attempt_token="attempt-1",
                    fencing_token=3,
                    export_format="pptx",
                )
            expired_at = datetime.now(UTC) - timedelta(seconds=1)
            async with maker() as session, session.begin():
                row = await session.get(WorkspaceArtifact, artifact.artifact_id)
                row.expires_at = expired_at
            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError,
                    match="workspace_export_expired",
                ):
                    await publication.resolve_workspace_export_artifact(
                        session,
                        artifact_id=artifact.artifact_id,
                    )
            swept = await publication.sweep_workspace_artifact_cleanup(
                session_factory=maker,
                now=datetime.now(UTC),
            )
            assert swept["deleted"] == 1
            assert not artifact.absolute_path.exists()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_stale_export_publication_compensates_external_file(
    tmp_path: Path, monkeypatch
):
    async def scenario():
        engine, maker = await _database(tmp_path / "stale.db")
        app_data = tmp_path / "app-data-stale"
        source = app_data / "exports" / "deck.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"pdf")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_export(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pdf",
                )
            async with maker() as session, session.begin():
                presentation = await session.get(PresentationModel, presentation_id)
                presentation.revision = 8
            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError,
                    match="stale_presentation_revision",
                ):
                    await publication.publish_workspace_export_artifact(
                        session,
                        artifact=artifact,
                        attempt_token="attempt-1",
                        fencing_token=3,
                        export_format="pdf",
                    )
            assert not artifact.absolute_path.exists()
            async with maker() as session:
                row = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert row.status == "deleted"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("lost_ack", [False, True])
def test_export_publication_commit_failure_compensates_but_lost_ack_reconciles(
    tmp_path: Path, monkeypatch, lost_ack: bool
):
    async def scenario():
        engine, maker = await _database(tmp_path / f"commit-{lost_ack}.db")
        app_data = tmp_path / f"app-data-commit-{lost_ack}"
        source = app_data / "exports" / "deck.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"commit boundary")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_export(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pdf",
                )
                original_commit = session.commit

                async def fail_or_lose_ack():
                    if lost_ack:
                        await original_commit()
                    raise RuntimeError("injected commit boundary failure")

                monkeypatch.setattr(session, "commit", fail_or_lose_ack)
                if lost_ack:
                    assert (
                        await publication.publish_workspace_export_artifact(
                            session,
                            artifact=artifact,
                            attempt_token="attempt-1",
                            fencing_token=3,
                            export_format="pdf",
                        )
                        == 7
                    )
                else:
                    with pytest.raises(
                        RuntimeError,
                        match="injected commit boundary failure",
                    ):
                        await publication.publish_workspace_export_artifact(
                            session,
                            artifact=artifact,
                            attempt_token="attempt-1",
                            fencing_token=3,
                            export_format="pdf",
                        )
            async with maker() as session:
                operation = await session.get(WorkspaceOperation, operation_id)
                artifact_row = await session.get(WorkspaceArtifact, artifact.artifact_id)
                if lost_ack:
                    assert operation.state == "succeeded"
                    assert artifact_row.status == "ready"
                    assert artifact.absolute_path.exists()
                else:
                    assert operation.state == "running"
                    assert artifact_row.status == "deleted"
                    assert not artifact.absolute_path.exists()
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation.revision == 7
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_export_download_resolution_is_owner_scoped(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "owner.db")
        app_data = tmp_path / "app-data-owner"
        source = app_data / "exports" / "owner.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"owner")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        owner_a = uuid.uuid4()
        owner_b = uuid.uuid4()
        owner_token = set_current_owner_id(owner_a)
        try:
            async with maker() as session, session.begin():
                session.add(
                    User(
                        id=owner_a,
                        username=f"owner-{owner_a.hex}",
                        hashed_password="test-only",
                    )
                )
            presentation_id, operation_id = await _seed_export(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=7,
                    kind="export.pptx",
                )
                await publication.publish_workspace_export_artifact(
                    session,
                    artifact=artifact,
                    attempt_token="attempt-1",
                    fencing_token=3,
                    export_format="pptx",
                )
                assert (
                    await publication.resolve_workspace_export_artifact(
                        session,
                        artifact_id=artifact.artifact_id,
                    )
                ).absolute_path == artifact.absolute_path
        finally:
            reset_current_owner_id(owner_token)

        other_token = set_current_owner_id(owner_b)
        try:
            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError,
                    match="workspace_export_not_found",
                ):
                    await publication.resolve_workspace_export_artifact(
                        session,
                        artifact_id=artifact.artifact_id,
                    )
        finally:
            reset_current_owner_id(other_token)
            await engine.dispose()

    asyncio.run(scenario())
