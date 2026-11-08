"""Consent-bound remeasurement of stored SVGs, without author purchases.

The failed quality receipt remains failed until the renderer actually measures
the same source and PNG again. This producer grants no provider permission and
does not turn an old quality flag into a passing receipt.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
from models.sql.workspace import WorkspaceOperation
from services.svg_timeout_repurchase import _live
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import canonical_json_sha256, sha256_hex


SVG_CLASSIFICATION_REMEASUREMENT = "svg-source-remeasure/v1"
CLASSIFICATION_APPROVAL_PREFIX = "svg-source-remeasure:"
REMEASUREMENT_SCHEMA = "svg-cache-only-source-remeasure/v1"
SVG_SOURCE_REMEASUREMENT_DISPATCH = "svg-source-remeasure-dispatch/v1"
DISPATCH_APPROVAL_PREFIX = "svg-source-remeasure-dispatch:"


def remeasurement_digest(value):
    return canonical_json_sha256(value, allow_nan=True)


def author_witness_digest(payload):
    return remeasurement_digest({"attempts": [
        {"kind": attempt.get("kind"), "raw_response": attempt.get("raw_response"),
         "receipt": attempt.get("receipt")}
        for attempt in payload.get("attempts", [])
    ]})


class ClassificationRemeasurementRejected(ValueError):
    pass


@dataclass
class ClassificationRemeasurementRecovery:
    rows: list[AuthoredSlideCheckpoint]
    manifest: dict

    @property
    def manifest_hash(self):
        return remeasurement_digest(self.manifest)


def _reject(code="svg_classification_remeasurement_not_safe"):
    raise ClassificationRemeasurementRejected(code)


async def build_classification_remeasurement_manifest(session, operation, *, presentation_revision, lock=True,
                                                       dispatch_only=False):
    def locked(statement):
        return statement.with_for_update() if lock else statement

    now = get_current_utc_datetime()
    if (operation.kind != "presentation.generate" or operation.state != "failed"
            or operation.error_code not in ({"generation_strategy_capability_missing"} if dispatch_only else
                                            {"svg_source_classification_missing", "svg_source_quality_failed"})
            or _live(operation.lease_expires_at, now)):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id)))
    if (job is None or job.state != "failed" or job.request_payload.get("strategy") != "svg"
            or str(job.id) != str(operation.request_payload.get("job_id"))
            or _live(job.lease_expires_at, now)):
        _reject()
    snapshot = job.svg_style_snapshot or {}
    from services.svg_authoring_profile import validate_authoring_profile

    profile = validate_authoring_profile(snapshot.get("authoring_profile"))
    if profile != {"id": "art-direction-v1", "version": 7}:
        _reject()
    frozen_profile = job.request_payload.get("_internal_svg_authoring_profile")
    if frozen_profile is not None and validate_authoring_profile(frozen_profile) != profile:
        _reject()
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id)))
    if (admission is None or admission.state != "failed" or admission.terminal_at is None
            or admission.owner_id != operation.owner_id
            or admission.estimated_reserved_microunits or admission.actual_reserved_microunits):
        _reject()
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(
        PublicResourceLease.admission_id == admission.id).order_by(PublicResourceLease.scope_type))))
    if any(row.state not in {"released", "cancelled", "expired"} for row in leases):
        _reject("svg_classification_remeasurement_active_lease")
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id).order_by(
        PublicProviderLogicalCall.stage, PublicProviderLogicalCall.slide_index, PublicProviderLogicalCall.logical_call_ordinal))))
    if (dispatch_only and calls) or (not dispatch_only and not calls) or any(
            row.state != "reported" or row.outcome != "succeeded" or row.reconciled_at is None for row in calls):
        _reject("svg_classification_remeasurement_provider_unresolved")
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id
        ).where(ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeReservation.id))))
    if any(row.state in {"reserved", "uncertain"} for row in reservations) or any(row.estimated_reserved for row in budgets):
        _reject("svg_classification_remeasurement_provider_unresolved")
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    # Only the two server-derived false-positive reasons below are eligible.
    # Arbitrary quality failures cannot select this no-author recovery path.
    if job.total_slides not in {2, 4} or [row.slide_index for row in rows] != list(range(job.total_slides)):
        _reject()
    original_manifest, original_approval_hash, strategy_source_id = None, None, str(operation.id)
    if dispatch_only:
        target_payload = rows[1].svg_checkpoint or {}
        marker = target_payload.get("cache_only_remeasurement") or {}
        archive = target_payload.get("classification_remeasurement_archive") or {}
        original_manifest = archive.get("approval_manifest") or {}
        original_approval_hash = archive.get("approval_manifest_hash")
        strategy_source_id = original_manifest.get("operation_id")
        if (marker.get("status") != "pending" or marker.get("operation_id") != str(operation.id)
                or marker.get("remeasured_metrics_digest") or marker.get("dispatch_approval_manifest_hash")
                or marker.get("schema") != REMEASUREMENT_SCHEMA
                or marker.get("revision") != SVG_CLASSIFICATION_REMEASUREMENT
                or marker.get("source_operation_id") != strategy_source_id
                or str(operation.retry_of_operation_id) != strategy_source_id
                or original_approval_hash != remeasurement_digest(original_manifest)
                or marker.get("approval_manifest_hash") != original_approval_hash
                or original_manifest.get("recovery_revision") != SVG_CLASSIFICATION_REMEASUREMENT
                or original_manifest.get("job_id") != str(job.id)
                or original_manifest.get("presentation_id") != str(operation.presentation_id)
                or original_manifest.get("frozen_request_digest") != remeasurement_digest(job.request_payload)
                or original_manifest.get("style_digest") != remeasurement_digest(snapshot)
                or original_manifest.get("target_indices") != [1]
                or original_manifest.get("expected_page_count") != job.total_slides
                or original_manifest.get("max_additional_author_calls") != 0):
            _reject("svg_source_remeasure_dispatch_not_safe")
        original = await session.get(WorkspaceOperation, UUID(strategy_source_id),
                                     with_for_update=lock)
        from services.authored_vnext.deck_ir.strategy import validate_sealed_generation_strategy, GenerationStrategyCapabilityError

        if (original is None or original.owner_id != operation.owner_id
                or original.owner_scope != operation.owner_scope or original.presentation_id != operation.presentation_id
                or original.kind != "presentation.generate" or original.state != "failed"
                or original.error_code != original_manifest.get("source_error_code")
                or remeasurement_digest(original.request_payload) != original_manifest.get("operation_request_digest")
                or original.request_payload.get("generation_strategy") != operation.request_payload.get("generation_strategy")):
            _reject("svg_source_remeasure_dispatch_not_safe")
        receipt = operation.request_payload.get("generation_strategy")
        try:
            validate_sealed_generation_strategy(receipt, operation_id=str(original.id))
        except (GenerationStrategyCapabilityError, AttributeError, TypeError):
            _reject("svg_source_remeasure_dispatch_not_safe")
        if receipt.get("operationId") == str(operation.id):
            _reject("svg_source_remeasure_dispatch_not_safe")
        original_calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == UUID(original_manifest["old_admission"]["id"])
        ).order_by(PublicProviderLogicalCall.stage, PublicProviderLogicalCall.slide_index,
                   PublicProviderLogicalCall.logical_call_ordinal))))
        if [row.model_dump(mode="json") for row in original_calls] != original_manifest["old_logical_calls"]:
            _reject("svg_classification_remeasurement_provider_unresolved")
    from services.svg_presentation_checkpoint import _read_png, quality_browser_metrics

    pages, targets, recovery_reason = [], [], None
    for row in rows:
        p = row.svg_checkpoint or {}
        q = p.get("quality") or {}
        try:
            metrics = quality_browser_metrics(q)
        except ValueError:
            _reject()
        source = p.get("svg_content")
        attempts = p.get("attempts") or []
        if (not isinstance(source, str) or not source or not p.get("input_digest")
                or not attempts or p.get("inflight") or p.get("terminal")
                or (p.get("cache_only_remeasurement") and not dispatch_only) or _read_png(p) is None
                or q.get("measurement_complete") is not True or q.get("geometry_passed") is not True
                or not isinstance(metrics, dict)
                or q.get("source_digest") not in {sha256_hex(source.encode("utf-8")), "sha256:" + sha256_hex(source.encode("utf-8"))}
                or q.get("png_digest") not in {p.get("png_sha256"), "sha256:" + str(p.get("png_sha256"))}
                or attempts[-1].get("receipt", {}).get("validation_error")):
            _reject()
        page = {
            "id": str(row.id), "slide_index": row.slide_index,
            "row_digest": remeasurement_digest(row.model_dump(mode="json")),
            "source_sha256": sha256_hex(source.encode("utf-8")), "png_sha256": p["png_sha256"],
            "input_digest": p["input_digest"], "author_receipt_digest": author_witness_digest(p),
            "browser_metrics_digest": remeasurement_digest(metrics),
        }
        if dispatch_only:
            original_page = next((page for page in original_manifest.get("pages", []) if page.get("slide_index") == row.slide_index), None)
            if original_page is None or any(page[key] != original_page.get(key) for key in (
                    "id", "source_sha256", "png_sha256", "input_digest", "author_receipt_digest", "browser_metrics_digest")):
                _reject("svg_source_remeasure_dispatch_not_safe")
            if row.slide_index == 1:
                if (p.get("quality") != archive.get("checkpoint_row", {}).get("svg_checkpoint", {}).get("quality")
                        or remeasurement_digest(archive.get("checkpoint_row")) != original_page.get("row_digest")
                        or marker.get("style_digest") != original_manifest.get("style_digest")
                        or marker.get("reference_digest") != original_manifest.get("reference_digest")
                        or any(marker.get(key) != original_page.get(key) for key in (
                            "source_sha256", "png_sha256", "input_digest", "author_receipt_digest", "browser_metrics_digest"))):
                    _reject("svg_source_remeasure_dispatch_not_safe")
        if q.get("passed") is not True:
            if (row.slide_index != 1 or q.get("passed") is not False
                    or not q.get("failures")
                    or sum(attempt.get("kind") != "local" for attempt in attempts) != 2):
                _reject()
            kinds = {failure.get("kind") for failure in q["failures"]}
            source_error = original_manifest["source_error_code"] if dispatch_only else operation.error_code
            if (source_error == "svg_source_classification_missing" and job.total_slides == 4
                    and kinds == {"source_classification_unproven"}
                    and (q.get("source_classification_association") or {}).get("status") == "unproven"):
                recovery_reason = "classification_legend_paint"
            elif (source_error == "svg_source_quality_failed" and job.total_slides == 2
                    and kinds == {"native_requested_chart_missing"}
                    and len(q["failures"]) == 1 and q["failures"][0].get("id") == "chart-1"):
                native = q.get("requestedNativeObjects") or {}
                projection = native.get("projection") or {}
                if (native.get("passed") is not False or native.get("requiredCharts") != 1
                        or projection.get("dropped") != [{"id": "native-chart-chart-1", "kind": "chart",
                                                          "reason": "native_chart_label_not_drawn"}]):
                    _reject()
                recovery_reason = "native_single_series_title_unit"
            else:
                _reject()
            targets.append(row)
        pages.append(page)
    if len(targets) != 1:
        _reject()
    manifest = {
        "recovery_revision": SVG_SOURCE_REMEASUREMENT_DISPATCH if dispatch_only else SVG_CLASSIFICATION_REMEASUREMENT,
        "dispatch_only": dispatch_only,
        "recovery_reason": recovery_reason, "expected_page_count": job.total_slides,
        "source_error_code": operation.error_code,
        "operation_id": str(operation.id), "operation_version": operation.version,
        "operation_fence": operation.fencing_token, "operation_attempt": operation.attempt_number,
        "operation_request_digest": remeasurement_digest(operation.request_payload),
        "operation_error_digest": remeasurement_digest(operation.error_detail),
        "presentation_id": str(operation.presentation_id), "presentation_revision": presentation_revision,
        "job_id": str(job.id), "job_attempt": job.attempt_number,
        "job_attempt_token_digest": remeasurement_digest(job.attempt_token),
        "frozen_request_digest": remeasurement_digest(job.request_payload),
        "admitted_request_digest": job.request_sha256,
        "style_digest": remeasurement_digest(snapshot), "reference_digest": remeasurement_digest(snapshot.get("references")),
        "frozen_profile": profile, "pages": pages, "target_indices": [1],
        "old_admission": admission.model_dump(mode="json"),
        "old_logical_calls": [row.model_dump(mode="json") for row in calls],
        "old_resource_leases": [row.model_dump(mode="json") for row in leases],
        "old_runtime_budgets": [row.model_dump(mode="json") for row in budgets],
        "old_runtime_reservations": [row.model_dump(mode="json") for row in reservations],
        "max_additional_author_calls": 0, "mandatory_render_count": 1,
        "source_and_png_must_remain_identical": True,
        "old_ledger_writes_permitted": False, "deck_vqa_uses_existing_admission_budget": True,
        "retry_max_attempts": operation.max_attempts,
    }
    if dispatch_only:
        manifest.update(original_approval_manifest_hash=original_approval_hash,
                        strategy_source_operation_id=strategy_source_id,
                        pending_marker_digest=remeasurement_digest(marker))
    return ClassificationRemeasurementRecovery(targets, manifest)


async def verify_classification_remeasurement(session, operation, request, *, presentation_revision):
    dispatch_only = request.recovery_revision == SVG_SOURCE_REMEASUREMENT_DISPATCH
    prefix = DISPATCH_APPROVAL_PREFIX if dispatch_only else CLASSIFICATION_APPROVAL_PREFIX
    if (request.action != "retry" or request.expected_operation_version is None
            or request.expected_revision is None or not request.action_id
            or not request.action_id.startswith(prefix)):
        _reject("svg_classification_remeasurement_approval_required")
    if operation.version != request.expected_operation_version:
        _reject("operation_state_conflict")
    recovery = await build_classification_remeasurement_manifest(session, operation,
        presentation_revision=presentation_revision, dispatch_only=dispatch_only)
    if request.action_id != prefix + recovery.manifest_hash:
        _reject("svg_classification_remeasurement_manifest_conflict")
    return recovery


def reset_classification_remeasurement_checkpoints(session, recovery, *, operation_id, now):
    page_by_id = {page["id"]: page for page in recovery.manifest["pages"]}
    for row in recovery.rows:
        archive = row.model_dump(mode="json")
        old = deepcopy(row.svg_checkpoint)
        page = page_by_id[str(row.id)]
        if recovery.manifest.get("dispatch_only"):
            marker = old["cache_only_remeasurement"]
            old["source_remeasurement_dispatch_archive"] = {
                "checkpoint_row": archive, "approval_manifest": deepcopy(recovery.manifest),
                "approval_manifest_hash": recovery.manifest_hash,
                "previous_operation_id": marker["operation_id"],
            }
            marker.update(operation_id=str(operation_id), dispatch_approval_manifest_hash=recovery.manifest_hash,
                          dispatch_source_operation_id=recovery.manifest["operation_id"])
            row.svg_checkpoint = old
            row.updated_at = now
            session.add(row)
            continue
        row.svg_checkpoint = {
            **old,
            "classification_remeasurement_archive": {
                "checkpoint_row": archive, "approval_manifest": deepcopy(recovery.manifest),
                "approval_manifest_hash": recovery.manifest_hash,
            },
            "cache_only_remeasurement": {
                "schema": REMEASUREMENT_SCHEMA, "revision": SVG_CLASSIFICATION_REMEASUREMENT,
                "recovery_reason": recovery.manifest["recovery_reason"],
                "expected_page_count": recovery.manifest["expected_page_count"],
                "operation_id": str(operation_id), "source_operation_id": recovery.manifest["operation_id"],
                **{key: page[key] for key in ("slide_index", "source_sha256", "png_sha256", "input_digest",
                    "author_receipt_digest", "browser_metrics_digest")},
                "style_digest": recovery.manifest["style_digest"],
                "reference_digest": recovery.manifest["reference_digest"],
                "approval_manifest_hash": recovery.manifest_hash, "status": "pending",
            },
        }
        row.render_status = "pending"
        row.persist_status = "pending"
        row.failed_stage = row.error = row.failed_at = None
        row.updated_at = now
        session.add(row)
