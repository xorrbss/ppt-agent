"""Consent-bound SVG timeout recovery, without asserting remote cancellation.

Local reconciliation can leave a remote request's outcome unknown. This
revision explicitly authorizes another purchase; it never edits that ledger or
claims the original purchase stopped. The service action still owns owner/CAS,
idempotency, admission, and the transaction that rebinds the generation job.
"""
from __future__ import annotations
from services.generation_core.quality.review_coverage import review_coverage

from copy import deepcopy
from dataclasses import dataclass
from datetime import timezone
from uuid import UUID

from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import (
    PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease,
)
from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import canonical_json_sha256, sha256_hex


SVG_TIMEOUT_REPURCHASE = "svg-provider-timeout-repurchase/v1"
SVG_TIMEOUT_REPURCHASE_V2 = "svg-provider-timeout-repurchase/v2"
SVG_TIMEOUT_REPURCHASE_V3 = "svg-provider-timeout-repurchase/v3"
APPROVAL_ACTION_PREFIX = "svg-timeout-repurchase:"


class TimeoutRepurchaseRejected(ValueError):
    pass


@dataclass
class TimeoutRepurchaseRecovery:
    rows: list[AuthoredSlideCheckpoint]
    manifest: dict

    @property
    def manifest_hash(self):
        return canonical_json_sha256(self.manifest)


def _digest(value):
    return canonical_json_sha256(value, allow_nan=True)


def _live(expires, now):
    if expires is None:
        return False
    return expires.replace(tzinfo=expires.tzinfo or timezone.utc) > now


def _reject(code="svg_timeout_repurchase_not_safe"):
    raise TimeoutRepurchaseRejected(code)


def _incident_authoring_profile():
    from services.svg_timeout_repurchase_incident import incident_registry
    return incident_registry()["authoring_profile"]


def _bounded_completion_shape(manifest):
    """Whether a manifest is one of the registered incident's bounded approvals."""
    from services.svg_timeout_repurchase_incident import bounded_completion_shape
    return bounded_completion_shape(manifest)


async def _completion_operation_manifest(session, operation, admission=None, *, lock=True):
    from services.svg_timeout_repurchase_incident import completion_operation_manifest
    return await completion_operation_manifest(session, operation, admission, lock=lock)


async def sample_completion_admission_deadline(session, operation, *, owner_id, tenant_id,
        policy, now, deadline, auth_expires_at=None, server_hard_deadline_at=None):
    from services import svg_timeout_repurchase_incident as incident
    return await incident.sample_completion_admission_deadline(
        session, operation, owner_id=owner_id, tenant_id=tenant_id, policy=policy, now=now,
        deadline=deadline, auth_expires_at=auth_expires_at, server_hard_deadline_at=server_hard_deadline_at)


async def sample_completion_runtime_profile(session, job, profile):
    """Lower concurrency only for the exact operation-bound recovery contract."""
    from dataclasses import replace
    from models.sql.workspace import WorkspaceOperation
    if job is None or job.workspace_operation_id is None:
        return profile, None
    operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
    manifest = (operation.request_payload.get('svg_timeout_repurchase_manifest') or {}) if operation else {}
    if manifest.get("append_only_recovery"):
        await verify_append_only_child_lineage(session, operation)
        return replace(profile, author_concurrency=1, render_concurrency=1, illustration_budget=0), None
    if not manifest.get('bounded_completion_recovery'):
        return profile, None
    # Preparation's session remains open through publication. Only the detached
    # pre-SDK reservation transaction may hold admission locks across accounting.
    await _completion_operation_manifest(session, operation, lock=False)
    return replace(profile, author_concurrency=1, render_concurrency=1), manifest['execution_profile']['cached_first_page_indexes']


async def build_timeout_repurchase_manifest(session, operation, *, presentation_revision, lock=True,
                                           recovery_revision=SVG_TIMEOUT_REPURCHASE,
    stage_review_decision: dict | None = None,
):
    """Lock and bind a terminal local snapshot, also usable for read-only review.

The returned hash is the approval contract. It is not permission to retry;
``verify_timeout_repurchase`` requires that exact hash in the action request.
"""
    if stage_review_decision is not None and operation.error_code != "presentation_generation_failed":
        _reject("svg_timeout_repurchase_manifest_conflict")
    if (recovery_revision == SVG_TIMEOUT_REPURCHASE_V3
        and (operation.request_payload.get("svg_timeout_repurchase_manifest") or {}).get("append_only_recovery")
        and operation.error_code == "http_error_500"):
        return await build_zero_dispatch_append_only_manifest(
            session, operation, presentation_revision=presentation_revision, lock=lock, stage_review_decision=stage_review_decision)
    if (recovery_revision == SVG_TIMEOUT_REPURCHASE_V3 and operation.error_code in {"svg_source_quality_failed", "presentation_generation_failed"}
            and (operation.request_payload.get("svg_timeout_repurchase_manifest") or {}).get("append_only_recovery")):
        return await build_zero_dispatch_append_only_manifest(session, operation,
            presentation_revision=presentation_revision, lock=lock, known_result=True, stage_review_decision=stage_review_decision)
    if recovery_revision == SVG_TIMEOUT_REPURCHASE_V3 and operation.error_code == "svg_source_quality_failed":
        return await build_append_only_svg_recovery_manifest(
            session, operation, presentation_revision=presentation_revision, lock=lock)
    if recovery_revision == SVG_TIMEOUT_REPURCHASE_V3:
        # Outside append-only recovery, revision three exists only for the one
        # registered incident; its read-only verifier owns every stage.
        from services.svg_timeout_repurchase_incident import build_incident_manifest
        return await build_incident_manifest(session, operation, presentation_revision=presentation_revision,
                                             lock=lock, recovery_revision=recovery_revision)
    now = get_current_utc_datetime()
    mixed_recovery = recovery_revision == SVG_TIMEOUT_REPURCHASE_V2
    if recovery_revision not in {SVG_TIMEOUT_REPURCHASE, SVG_TIMEOUT_REPURCHASE_V2}:
        _reject()
    def locked(statement):
        return statement.with_for_update() if lock else statement
    if (operation.kind != "presentation.generate" or operation.state != "failed"
            or operation.error_code != ("svg_source_classification_missing" if mixed_recovery
                                        else "public_provider_call_timeout")
            or _live(operation.lease_expires_at, now)):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id,
    )))
    if (job is None or job.state != "failed" or job.request_payload.get("strategy") != "svg"
            or not job.svg_style_snapshot or _live(job.lease_expires_at, now)):
        _reject()
    linked_id = operation.request_payload.get("job_id")
    if linked_id is not None and str(job.id) != str(linked_id):
        _reject()
    if mixed_recovery and (job.total_slides < 1 or job.svg_style_snapshot.get("authoring_profile")
                           != _incident_authoring_profile()):
        _reject()
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id,
    )))
    if (admission is None or admission.state not in {"failed", "deadline_exceeded"}
            or admission.terminal_at is None or admission.owner_id != operation.owner_id
            or admission.estimated_reserved_microunits or admission.actual_reserved_microunits):
        _reject()
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(
        PublicResourceLease.admission_id == admission.id,
    ).order_by(PublicResourceLease.scope_type))))
    if any(lease.state not in {"released", "cancelled", "expired"} for lease in leases):
        _reject("svg_timeout_repurchase_active_lease")
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id,
    ).order_by(PublicProviderLogicalCall.stage, PublicProviderLogicalCall.slide_index,
               PublicProviderLogicalCall.logical_call_ordinal))))
    if not calls or any(call.state == "reserved" or call.reconciled_at is None
                        or call.outcome not in {"succeeded", "failed", "cancelled"} for call in calls):
        _reject("svg_timeout_repurchase_active_call")
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id,
    ).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id,
    ).where(ProviderRuntimeBudget.job_id == job.id).order_by(
        ProviderRuntimeReservation.id))))
    if any(row.state in {"reserved", "uncertain"} for row in reservations):
        _reject("svg_timeout_repurchase_active_call")
    if any(row.estimated_reserved for row in budgets):
        _reject("svg_timeout_repurchase_active_call")
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id,
    ).order_by(AuthoredSlideCheckpoint.slide_index))))
    if not rows or len(rows) != job.total_slides or [row.slide_index for row in rows] != list(range(job.total_slides)):
        _reject()
    from services.svg_presentation_checkpoint import _read_png

    unknown, completed = [], []
    for row in rows:
        payload = row.svg_checkpoint or {}
        if not payload.get("input_digest"):
            _reject()
        if payload.get("svg_content") and payload.get("png_sha256") and (payload.get("quality") or {}).get("passed") is True:
            if payload.get("inflight"):
                _reject()
            if _read_png(payload) is None:
                _reject("svg_timeout_repurchase_completed_cache_missing")
            completed.append({"id": str(row.id), "slide_index": row.slide_index,
                              "row_digest": _digest(row.model_dump(mode="json")),
                              "input_digest": payload["input_digest"],
                              "source_digest": sha256_hex(payload["svg_content"].encode("utf-8")),
                              "png_digest": payload["png_sha256"],
                              "author_digest": _digest(payload.get("attempts", []))})
            continue
        ordinal = payload.get("inflight")
        if mixed_recovery:
            failures = payload.get("provider_failures") or []
            attempts = payload.get("attempts") or []
            kinds = {f.get("kind") for f in (payload.get("quality") or {}).get("failures", [])}
            if (payload.get("inflight")
                    or payload.get("retained_provider_attempt_fence") or payload.get("recovery_revision")
                    or len(failures) != 1 or failures[0].get("code") != "public_provider_overload"):
                _reject("svg_timeout_repurchase_attempt_budget_exhausted")
            ordinal = 2 if payload.get("svg_content") else 1
            if failures[0].get("ordinal") != ordinal:
                _reject()
            if ordinal == 2:
                if (len(attempts) != 1 or attempts[0].get("kind") == "local"
                        or not attempts[0].get("raw_response") or not payload.get("svg_content")
                        or not payload.get("png_sha256") or _read_png(payload) is None
                        or (payload.get("quality") or {}).get("passed") is not False
                        or kinds != {"source_classification_unproven", "provider_repair_unavailable"}
                        or attempts[0].get("receipt", {}).get("validation_error")):
                    _reject()
            elif attempts or payload.get("svg_content") or payload.get("png_sha256"):
                _reject()
        # Only one remaining author attempt is available under the existing
        # two-attempt page budget. Never reset an exhausted ordinal.
        if not mixed_recovery and (type(ordinal) is not int or ordinal != 1 or payload.get("svg_content")
                or payload.get("png_sha256") or payload.get("attempts") or payload.get("provider_failures")
                or payload.get("retained_provider_attempt_fence")):
            _reject("svg_timeout_repurchase_attempt_budget_exhausted")
        witnesses = [call for call in calls if call.slide_index == row.slide_index
                     and call.stage in {"author", "repair"}
                     and call.state in {"unavailable", "cancelled", "failed"}
                     and call.outcome in {"failed", "cancelled"}]
        if len(witnesses) != 1:
            _reject()
        if mixed_recovery:
            reported = [c for c in calls if c.slide_index == row.slide_index and c.stage == "author"
                        and c.state == "reported" and c.outcome == "succeeded"]
            if (witnesses[0].stage != ("repair" if ordinal == 2 else "author")
                    or len(reported) != (1 if ordinal == 2 else 0)):
                _reject()
        unknown.append({"id": str(row.id), "slide_index": row.slide_index,
                        "input_digest": payload["input_digest"], "previous_ordinal": ordinal,
                        "consumed_attempt_count": ordinal,
                        **({"max_additional_author_calls": 1, "next_provider_ordinal": ordinal + 1,
                            "previous_source_digest": sha256_hex(payload["svg_content"].encode("utf-8"))
                                if payload.get("svg_content") else None} if mixed_recovery else {}),
                        "row_digest": _digest(row.model_dump(mode="json")),
                        "logical_call_ids": [str(call.id) for call in witnesses]})
    if not unknown or not completed:
        _reject()
    unknown_call_ids = {call_id for item in unknown for call_id in item["logical_call_ids"]}
    if any((call.state in {"unavailable", "cancelled"} or call.outcome == "cancelled")
           and str(call.id) not in unknown_call_ids for call in calls):
        _reject()
    from utils.llm_provider import get_llm_provider, get_model
    from utils.llm_calls.author_model_pin import authoring_model

    manifest = {
        "recovery_revision": recovery_revision,
        "operation_id": str(operation.id), "operation_version": operation.version,
        "operation_fence": operation.fencing_token, "operation_attempt": operation.attempt_number,
        "operation_attempt_token_digest": _digest(operation.attempt_token),
        "operation_request_digest": _digest(operation.request_payload),
        "operation_error_digest": _digest(operation.error_detail),
        "presentation_id": str(operation.presentation_id), "presentation_revision": presentation_revision,
        **({"owner_id": str(operation.owner_id), "tenant_id": admission.tenant_id} if mixed_recovery else {}),
        "job_id": str(job.id), "job_attempt": job.attempt_number,
        "job_attempt_token_digest": _digest(job.attempt_token),
        "frozen_request_digest": _digest(job.request_payload), "admitted_request_digest": job.request_sha256,
        "frozen_style_digest": _digest(job.svg_style_snapshot),
        "frozen_profile": job.svg_style_snapshot.get("authoring_profile"),
        "completed_checkpoints": completed, "unknown_checkpoints": unknown,
        "old_admission": admission.model_dump(mode="json"),
        "old_logical_calls": [call.model_dump(mode="json") for call in calls],
        "old_resource_leases": [lease.model_dump(mode="json") for lease in leases],
        "old_runtime_budgets": [row.model_dump(mode="json") for row in budgets],
        "old_runtime_reservations": [row.model_dump(mode="json") for row in reservations],
        "retry_max_attempts": operation.max_attempts,
        "max_additional_author_calls": len(unknown),
        "author_attempt_budget_per_page": 2,
        **({"max_additional_vqa_calls": 1, "additional_purchase_budget_per_target": 1,
            "previous_attempts_remain_consumed": True} if mixed_recovery else {}),
        "new_author_provider": get_llm_provider().value,
        "new_author_model": authoring_model(get_model()),
        "deck_vqa_uses_existing_admission_budget": True,
        "remote_termination_confirmed": False,
        "duplicate_billing_possible": True,
        "old_ledger_writes_permitted": False,
    }
    target_ids = {item["id"] for item in unknown}
    return TimeoutRepurchaseRecovery([row for row in rows if str(row.id) in target_ids], manifest)


async def verify_timeout_repurchase(session, operation, request, *, presentation_revision):
    if (request.action != "retry" or request.expected_operation_version is None
            or request.expected_revision is None or not request.action_id
            or not request.action_id.startswith(APPROVAL_ACTION_PREFIX)):
        _reject("svg_timeout_repurchase_approval_required")
    if operation.version != request.expected_operation_version:
        _reject("operation_state_conflict")
    recovery = await build_timeout_repurchase_manifest(session, operation,
        presentation_revision=presentation_revision, recovery_revision=request.recovery_revision, stage_review_decision=getattr(request, "stage_review_decision", None))
    if request.action_id != APPROVAL_ACTION_PREFIX + recovery.manifest_hash:
        _reject("svg_timeout_repurchase_manifest_conflict")
    return recovery


async def verify_repurchase_provider_target(session, operation, admission, *, stage, slide_index,
                                          logical_call_key, provider_id, model_id):
    if (operation.request_payload.get("svg_timeout_repurchase_manifest") or {}).get("append_only_recovery"):
        return await verify_append_only_provider_target(session, operation, admission,
            stage=stage, slide_index=slide_index, logical_call_key=logical_call_key,
            provider_id=provider_id, model_id=model_id)
    """Pre-SDK refusal of unapproved pages, extra ordinals, or resumed purchases."""
    if operation.request_payload.get('recovery_revision') == SVG_TIMEOUT_REPURCHASE_V3:
        from services.svg_timeout_repurchase_incident import verify_incident_provider_target
        return await verify_incident_provider_target(session, operation, admission,
            stage=stage, slide_index=slide_index, logical_call_key=logical_call_key,
            provider_id=provider_id, model_id=model_id)
    if operation.request_payload.get("recovery_revision") != SVG_TIMEOUT_REPURCHASE_V2:
        return
    manifest = operation.request_payload.get("svg_timeout_repurchase_manifest") or {}
    if (manifest.get("recovery_revision") != SVG_TIMEOUT_REPURCHASE_V2
            or _digest(manifest) != operation.request_payload.get("svg_timeout_repurchase_manifest_hash")
            or manifest.get("owner_id") != str(operation.owner_id)
            or manifest.get("owner_id") != str(admission.owner_id)
            or manifest.get("tenant_id") != admission.tenant_id
            or manifest.get("operation_id") != str(operation.retry_of_operation_id)
            or manifest.get("presentation_id") != str(operation.presentation_id)
            or str(operation.request_payload.get("job_id")) != manifest.get("job_id")
            or type(manifest.get("max_additional_author_calls")) is not int
            or manifest.get("max_additional_author_calls", 0) < 1
            or manifest.get("max_additional_author_calls") != len(manifest.get("unknown_checkpoints", []))
            or manifest.get("max_additional_vqa_calls") != 1):
        _reject("svg_timeout_repurchase_provider_target_not_approved")
    job = await session.get(PresentationGenerationJob, UUID(manifest["job_id"]))
    if (job is None or job.workspace_operation_id != operation.id
            or _digest(job.request_payload) != manifest.get("frozen_request_digest")
            or _digest(job.svg_style_snapshot) != manifest.get("frozen_style_digest")):
        _reject("svg_timeout_repurchase_provider_target_not_approved")
    suffix = f".resume-{job.attempt_number}" if job.attempt_number > 1 else ""
    if stage in {"author", "repair"}:
        targets = [p for p in manifest.get("unknown_checkpoints", []) if p.get("slide_index") == slide_index]
        if (len(targets) != 1 or targets[0].get("consumed_attempt_count") not in {1, 2}
                or targets[0].get("max_additional_author_calls") != 1
                or targets[0].get("next_provider_ordinal") != targets[0]["consumed_attempt_count"] + 1
                or stage != "repair"
                or logical_call_key != f"svg-author-{slide_index}-{targets[0]['next_provider_ordinal']}" + suffix
                or provider_id != manifest.get("new_author_provider") or model_id != manifest.get("new_author_model")):
            _reject("svg_timeout_repurchase_provider_target_not_approved")
        stages = {"author", "repair"}
    elif stage == "vqa":
        if slide_index not in {None, -1} or logical_call_key != "authored.vision-advisory" + suffix:
            _reject("svg_timeout_repurchase_provider_target_not_approved")
        stages = {"vqa"}
    else:
        _reject("svg_timeout_repurchase_provider_target_not_approved")
    existing = await session.scalar(select(PublicProviderLogicalCall.id).where(
        PublicProviderLogicalCall.admission_id == admission.id,
        PublicProviderLogicalCall.stage.in_(stages),
        PublicProviderLogicalCall.slide_index == (-1 if slide_index is None else slide_index)))
    if existing is not None:
        _reject("svg_timeout_repurchase_purchase_already_consumed")


def reset_timeout_repurchase_checkpoints(session, recovery, *, operation_id, now):
    """Archive only consent-bound unknown rows; completed sources are untouched."""
    if recovery.manifest.get("append_only_recovery"):
        _reject("svg_timeout_repurchase_original_checkpoint_immutable")
    from services.svg_timeout_repurchase_incident import reset_incident_checkpoints
    recovery = reset_incident_checkpoints(session, recovery, operation_id=operation_id, now=now)
    if recovery is None:
        return
    targets = {item["id"]: item for item in recovery.manifest["unknown_checkpoints"]}
    for row in recovery.rows:
        previous = deepcopy(row.svg_checkpoint)
        witness = targets[str(row.id)]
        archive = row.model_dump(mode="json")
        revision = recovery.manifest["recovery_revision"]
        if revision in {SVG_TIMEOUT_REPURCHASE_V2, SVG_TIMEOUT_REPURCHASE_V3}:
            previous_source = previous.get("svg_content")
            previous = {**previous, "attempts": [], "provider_failures": []}
            for key in ("svg_content", "png_sha256", "png_path", "quality", "last_valid_svg",
                        "last_valid_quality", "repair_unavailable", "terminal", "terminal_error",
                        "model_attempt_count", "nativeDeclarations"):
                previous.pop(key, None)
            if revision == SVG_TIMEOUT_REPURCHASE_V2:
                from services.svg_timeout_repurchase_incident import classification_repair_hint
                previous['repair_hint'] = classification_repair_hint(previous_source)
        row.svg_checkpoint = {
            **deepcopy(previous), "inflight": None,
            "recovery_revision": revision,
            "recovery_operation_id": str(operation_id),
            "previous_checkpoint": deepcopy(archive["svg_checkpoint"]),
            "previous_checkpoint_row": archive,
            "timeout_repurchase_approval_manifest": deepcopy(recovery.manifest),
            "retained_provider_attempt_fence": {
                "consumed_attempt_count": witness["consumed_attempt_count"],
                "previous_ordinal": witness["previous_ordinal"],
                "old_operation_id": recovery.manifest["operation_id"],
                "old_operation_fence": recovery.manifest["operation_fence"],
                "old_job_attempt": recovery.manifest["job_attempt"],
                "old_job_attempt_token_digest": recovery.manifest["job_attempt_token_digest"],
                "logical_call_ids": witness["logical_call_ids"],
                "approval_manifest_hash": recovery.manifest_hash,
                **({"max_additional_author_calls": 1,
                    "slide_index": row.slide_index} if revision in {SVG_TIMEOUT_REPURCHASE_V2, SVG_TIMEOUT_REPURCHASE_V3} else {}),
            },
        }
        row.author_status = row.illustrate_status = row.render_status = row.persist_status = "pending"
        row.failed_stage = row.error = row.failed_at = None
        row.author_completed_at = row.render_completed_at = None
        row.updated_at = now
        session.add(row)


async def build_append_only_svg_recovery_manifest(session, operation, *, presentation_revision, lock=True):
    from dataclasses import asdict
    from models.sql.workspace import WorkspaceOperation
    from services.svg_authoring_profile import read_authoring_profile
    from services.generation_core.runtime.profile import resolve_svg_runtime_profile
    from utils.llm_provider import get_llm_provider, get_model
    from utils.llm_calls.author_model_pin import authoring_model
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    now = get_current_utc_datetime()
    def locked(statement):
        return statement.with_for_update() if lock else statement
    if (operation.kind != "presentation.generate" or operation.state != "failed"
        or operation.error_code not in {"svg_source_quality_failed", "svg_provider_attempt_uncertain", "public_provider_call_timeout"}
        or _live(operation.lease_expires_at, now)):
        _reject()
    job = await session.scalar(locked(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id)))
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id)))
    if (job is None or admission is None or job.state != "failed"
        or job.request_payload.get("strategy") != "svg" or not job.svg_style_snapshot
        or _live(job.lease_expires_at, now) or admission.state not in {"failed", "deadline_exceeded"}
        or admission.terminal_at is None or operation.owner_id != get_current_owner_id()
        or admission.owner_id != operation.owner_id or admission.tenant_id != get_current_tenant_id()
        or admission.estimated_reserved_microunits or admission.actual_reserved_microunits
        or operation.request_payload.get("job_id") != str(job.id)):
        _reject()
    profile = read_authoring_profile(job.svg_style_snapshot)
    if not profile or profile.get("version", 0) < 8:
        _reject()
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id).order_by(
        PublicProviderLogicalCall.stage, PublicProviderLogicalCall.slide_index,
        PublicProviderLogicalCall.logical_call_ordinal))))
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(
        PublicResourceLease.admission_id == admission.id).order_by(PublicResourceLease.id))))
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id).where(
        ProviderRuntimeBudget.job_id == job.id).order_by(ProviderRuntimeReservation.id))))
    if any(lease.state not in {"released", "cancelled", "expired"} for lease in leases):
        _reject("svg_timeout_repurchase_active_lease")
    if (any(c.state == "reserved" or c.reconciled_at is None or c.outcome not in {"succeeded", "failed", "cancelled"} for c in calls)
        or any(r.state in {"reserved", "uncertain"} for r in reservations)
        or any(b.estimated_reserved for b in budgets)):
        _reject("svg_timeout_repurchase_active_call")
    if [r.slide_index for r in rows] != list(range(job.total_slides)):
        _reject()
    if await session.scalar(select(WorkspaceOperation.id).where(
        WorkspaceOperation.retry_of_operation_id == operation.id)):
        _reject("svg_timeout_repurchase_purchase_already_consumed")
    unknown, cached, unstarted = [], [], []
    for row in rows:
        payload = row.svg_checkpoint or {}
        if payload.get("recovery_revision") or payload.get("retained_provider_attempt_fence"):
            _reject()
        witnesses = [c for c in calls if c.slide_index == row.slide_index and c.stage in {"author", "repair"}]
        item = {"id": str(row.id), "slide_index": row.slide_index,
            "row_digest": _digest(row.model_dump(mode="json")), "input_digest": payload.get("input_digest"),
            "logical_call_ids": [str(c.id) for c in witnesses], "consumed_attempt_count": len(witnesses)}
        svg = payload.get("svg_content")
        if svg:
            if (payload.get("inflight") or not item["input_digest"] or row.author_status != "completed"
                or not witnesses or any(c.outcome != "succeeded" for c in witnesses)):
                _reject()
            attempts = payload.get("attempts", [])
            if sum(a.get("kind") != "local" for a in attempts) != len(witnesses):
                _reject()
            item.update(source_digest=sha256_hex(svg.encode("utf-8")),
                author_digest=_digest(attempts), native_digest=_digest(payload.get("nativeDeclarations")),
                quality_digest=_digest(payload.get("quality")), max_additional_author_calls=0)
            cached.append(item)
        elif payload.get("inflight"):
            ordinal = payload["inflight"]
            if (type(ordinal) is not int or ordinal != len(witnesses) or ordinal < 1
                or payload.get("attempts") or payload.get("provider_failures")
                or not item["input_digest"] or not witnesses
                or witnesses[-1].state not in {"cancelled", "unavailable"}
                or witnesses[-1].outcome != "cancelled"):
                _reject()
            item.update(previous_ordinal=ordinal, next_provider_ordinal=ordinal+1,
                max_additional_author_calls=1, original_logical_call_id=str(witnesses[-1].id),
                original_request_fingerprint=witnesses[-1].idempotency_key_hash)
            unknown.append(item)
        else:
            if (witnesses or payload.get("attempts") or payload.get("provider_failures")
                or row.author_status != "pending" or payload.get("png_sha256")):
                _reject()
            item.update(next_provider_ordinal=1, max_additional_author_calls=2)
            unstarted.append(item)
    if not unknown or not cached:
        _reject()
    initial_consumed, retry_consumed = admission.initial_request_count, admission.retry_request_count
    if initial_consumed + retry_consumed != len(calls):
        _reject()
    initial_remaining = admission.planned_request_limit - initial_consumed
    retry_remaining = admission.retry_request_limit - retry_consumed
    if min(initial_remaining, retry_remaining) < 0 or retry_remaining < len(unknown):
        _reject("svg_timeout_repurchase_budget_exhausted")
    runtime = asdict(resolve_svg_runtime_profile(source_reflow=bool(job.request_payload.get("source_content_manifest_pin"))))
    manifest = {"schema": "append-only-svg-recovery/v1", "append_only_recovery": True,
        "recovery_revision": SVG_TIMEOUT_REPURCHASE_V3, "operation_id": str(operation.id),
        "operation_version": operation.version, "operation_fence": operation.fencing_token,
        "operation_digest": _digest(operation.model_dump(mode="json")),
        "presentation_id": str(operation.presentation_id), "presentation_revision": presentation_revision,
        "owner_id": str(operation.owner_id), "tenant_id": str(admission.tenant_id),
        "job_id": str(job.id), "job_digest": _digest(job.model_dump(mode="json")),
        "frozen_request_digest": _digest(job.request_payload), "admitted_request_digest": job.request_sha256,
        "frozen_style_digest": _digest(job.svg_style_snapshot), "frozen_profile": profile,
        "source_manifest_presentation_id": str(operation.presentation_id),
        "source_manifest_revision": operation.expected_revision + 1,
        "source_manifest_digest": (rows[0].svg_checkpoint or {}).get("source_manifest_digest"),
        "source_manifest_pointer_digest": _digest((rows[0].svg_checkpoint or {}).get("source_manifest_ref")),
        "unknown_checkpoints": unknown, "completed_checkpoints": cached, "unstarted_checkpoints": unstarted,
        "old_admission": admission.model_dump(mode="json"),
        "old_logical_calls": [c.model_dump(mode="json") for c in calls],
        "old_resource_leases": [lease.model_dump(mode="json") for lease in leases],
        "old_runtime_budgets": [b.model_dump(mode="json") for b in budgets],
        "old_runtime_reservations": [r.model_dump(mode="json") for r in reservations],
        "previous_consumed_author_calls": sum(c.stage in {"author", "repair"} for c in calls),
        "previous_consumed_logical_calls": len(calls),
        "initial_remaining": initial_remaining, "retry_remaining": retry_remaining,
        "total_logical_call_limit": admission.planned_request_limit + admission.retry_request_limit,
        "remaining_logical_call_limit": initial_remaining + retry_remaining,
        "wrapper_physical_call_limit": 2 * (admission.planned_request_limit + admission.retry_request_limit),
        "max_additional_author_calls": len(unknown), "additional_purchase_budget_per_target": 1,
        "wrapper_physical_limit_per_logical": 2, "runtime_profile": runtime,
        "new_author_provider": get_llm_provider().value, "new_author_model": authoring_model(get_model()),
        "old_ledger_writes_permitted": False, "old_checkpoint_writes_permitted": False,
        "old_job_rebind_permitted": False, "previous_attempts_remain_consumed": True,
        "remote_effective_model": "unmeasured", "actual_physical_attempts": "unmeasured"}
    return TimeoutRepurchaseRecovery(rows, manifest)


async def verify_append_only_child_lineage(session, operation, admission=None):
    from models.sql.workspace import WorkspaceOperation
    manifest = operation.request_payload.get("svg_timeout_repurchase_manifest") or {}
    if (manifest.get("append_only_recovery") is not True
        or operation.request_payload.get("svg_timeout_repurchase_manifest_hash") != _digest(manifest)
        or str(operation.retry_of_operation_id) != manifest.get("operation_id")
        or str(operation.owner_id) != manifest.get("owner_id")
        or str(operation.request_payload.get("principal_tenant_id")) != manifest.get("tenant_id")):
        _reject("svg_timeout_repurchase_manifest_conflict")
    parent = await session.get(WorkspaceOperation, UUID(manifest["operation_id"]))
    parent_job = await session.get(PresentationGenerationJob, UUID(manifest["job_id"]))
    if (parent is None or parent_job is None
        or _digest(parent.model_dump(mode="json")) != manifest["operation_digest"]
        or _digest(parent_job.model_dump(mode="json")) != manifest["job_digest"]):
        _reject("svg_timeout_repurchase_manifest_conflict")
    if manifest.get("zero_dispatch_descendant") or manifest.get("known_result_descendant"):
        origin = await session.get(WorkspaceOperation, UUID(manifest["origin_operation_id"]))
        origin_job = await session.get(PresentationGenerationJob, UUID(manifest["origin_job_id"]))
        if (origin is None or origin_job is None
            or _digest(origin.model_dump(mode="json")) != manifest["origin_operation_digest"]
            or _digest(origin_job.model_dump(mode="json")) != manifest["origin_job_digest"]
            or _digest(parent.request_payload.get("svg_timeout_repurchase_manifest")) != manifest["previous_approval_manifest_hash"]):
            _reject("svg_timeout_repurchase_manifest_conflict")
    job = await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id))
    if (job is None or str(job.id) != operation.request_payload.get("job_id")
        or job.id == parent_job.id or job.presentation_id == parent_job.presentation_id
        or _digest(job.svg_style_snapshot) != manifest["frozen_style_digest"]
        or job.request_payload.get("_svg_recovery_lineage", {}).get("approval_manifest_hash") != _digest(manifest)):
        _reject("svg_timeout_repurchase_manifest_conflict")
    if admission is not None and (admission.owner_id != operation.owner_id
        or str(admission.tenant_id) != manifest["tenant_id"]
        or admission.planned_request_limit > manifest["initial_remaining"]
        or admission.retry_request_limit > manifest["retry_remaining"]):
        _reject("svg_timeout_repurchase_manifest_conflict")
    return manifest, job


async def verify_append_only_provider_target(session, operation, admission, *, stage,
        slide_index, logical_call_key, provider_id, model_id):
    manifest, job = await verify_append_only_child_lineage(session, operation, admission)
    cached = {p["slide_index"]: p for p in manifest["completed_checkpoints"]}
    targets = {p["slide_index"]: p for p in manifest["unknown_checkpoints"]}
    unstarted = {p["slide_index"]: p for p in manifest["unstarted_checkpoints"]}
    if stage in {"author", "repair"}:
        if (slide_index in cached and not cached[slide_index].get("known_quality_repair_approved")) or provider_id != manifest["new_author_provider"] or model_id != manifest["new_author_model"]:
            _reject("svg_timeout_repurchase_provider_target_not_approved")
        target = targets.get(slide_index) or unstarted.get(slide_index) or cached.get(slide_index)
        if target is None:
            _reject("svg_timeout_repurchase_provider_target_not_approved")
        calls = list(await session.scalars(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission.id,
            PublicProviderLogicalCall.slide_index == slide_index,
            PublicProviderLogicalCall.stage.in_({"author", "repair"}))))
        suffix = f".resume-{job.attempt_number}" if job.attempt_number > 1 else ""
        allowed_ordinals = range(target["next_provider_ordinal"], target["next_provider_ordinal"] + target["max_additional_author_calls"])
        keys = {f"svg-author-{slide_index}-{ordinal}{suffix}" for ordinal in allowed_ordinals}
        if logical_call_key not in keys or len(calls) >= target["max_additional_author_calls"]:
            _reject("svg_timeout_repurchase_purchase_already_consumed")
        if (slide_index in targets or slide_index in cached) and stage != "repair":
            _reject("svg_timeout_repurchase_provider_target_not_approved")
    elif stage == "vqa":
        plan = job.request_payload.get("_internal_svg_stage_plan") or {}
        partitions = plan.get("partitions") or []
        if manifest.get("inherited_stage_execution"):
            from services.provider_usage_instrumentation import ProviderUsageScope
            rounds = manifest["remaining_vqa_rounds"]
            allowed_keys = {ProviderUsageScope(job_id=job.id,operation="authored_generation",stage="vqa",attempt_number=job.attempt_number,round_ordinal=ordinal).public_logical_call_key("authored.vision-advisory") for ordinal in rounds}
            if logical_call_key not in allowed_keys:
                _reject("svg_timeout_repurchase_provider_target_not_approved")
        if not partitions or logical_call_key is None or not logical_call_key.startswith("authored.vision-advisory"):
            _reject("svg_timeout_repurchase_provider_target_not_approved")
        existing = list(await session.scalars(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission.id, PublicProviderLogicalCall.stage == "vqa")))
        if len(existing) >= len(manifest.get("remaining_vqa_rounds", partitions)):
            _reject("svg_timeout_repurchase_purchase_already_consumed")
    else:
        _reject("svg_timeout_repurchase_provider_target_not_approved")



async def build_zero_dispatch_append_only_manifest(session, operation, *, presentation_revision, lock=True, known_result=False,
    stage_review_decision: dict | None = None,
):
    from models.sql.workspace import WorkspaceOperation
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    now = get_current_utc_datetime()
    def locked(statement):
        return statement.with_for_update() if lock else statement
    previous, job = await verify_append_only_child_lineage(session, operation)
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id)))
    if (operation.state not in {"failed", "dead_letter"} or operation.error_code not in ({"svg_source_quality_failed", "presentation_generation_failed"} if known_result else {"http_error_500"})
        or _live(operation.lease_expires_at, now) or job.state != "failed" or _live(job.lease_expires_at, now)
        or admission is None or admission.state not in {"failed", "deadline_exceeded"}
        or admission.terminal_at is None or (not known_result and (admission.initial_request_count or admission.retry_request_count))
        or admission.estimated_reserved_microunits or admission.actual_reserved_microunits
        or admission.owner_id != get_current_owner_id() or admission.tenant_id != get_current_tenant_id()):
        _reject()
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id))))
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(ProviderRuntimeBudget.job_id == job.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id).where(
        ProviderRuntimeBudget.job_id == job.id))))
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(PublicResourceLease.admission_id == admission.id))))
    if known_result:
        if (any(c.state != "reported" or c.outcome != "succeeded" for c in calls)
                or admission.initial_request_count + admission.retry_request_count != len(calls)
                or any(r.state in {"reserved", "inflight", "uncertain"} for r in reservations)
                or any(b.estimated_reserved for b in budgets)):
            _reject("svg_timeout_repurchase_active_call")
    elif calls or reservations or any(b.estimated_reserved or b.actual_consumed for b in budgets):
        _reject("svg_timeout_repurchase_active_call")
    if any(lease.state not in {"released", "cancelled", "expired"} for lease in leases):
        _reject("svg_timeout_repurchase_active_call")
    if await session.scalar(select(WorkspaceOperation.id).where(WorkspaceOperation.retry_of_operation_id == operation.id)):
        _reject("svg_timeout_repurchase_purchase_already_consumed")
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id).order_by(AuthoredSlideCheckpoint.slide_index))))
    if [r.slide_index for r in rows] != list(range(job.total_slides)):
        _reject()
    origin_id = previous.get("origin_operation_id", previous["operation_id"])
    origin_job_id = previous.get("origin_job_id", previous["job_id"])
    origin = await session.get(WorkspaceOperation, UUID(origin_id))
    origin_job = await session.get(PresentationGenerationJob, UUID(origin_job_id))
    groups = {name: {p["slide_index"]: p for p in previous[name]}
        for name in ("completed_checkpoints", "unknown_checkpoints", "unstarted_checkpoints")}
    rebound = {name: [] for name in groups}
    for row in rows:
        saved = row.svg_checkpoint or {}
        if saved.get("inflight"):
            _reject("svg_timeout_repurchase_active_call")
        name = next((name for name, pages in groups.items() if row.slide_index in pages), None)
        if name is None:
            _reject()
        witness = groups[name][row.slide_index]
        if known_result and name in {"unknown_checkpoints", "unstarted_checkpoints"} and saved.get("svg_content"):
            if not saved.get("attempts") or saved.get("inflight") or saved.get("provider_failures"):
                _reject("svg_timeout_repurchase_manifest_conflict")
            target_stage = "repair" if name == "unknown_checkpoints" else "author"
            key_digest = sha256_hex(f"svg-author-{row.slide_index}-{witness['next_provider_ordinal']}".encode("utf-8"))
            matched = [c for c in calls if c.stage == target_stage and c.slide_index == row.slide_index
                and c.provider_id == previous["new_author_provider"] and c.model_id == previous["new_author_model"]
                and c.idempotency_key_hash == sha256_hex(f"public-provider-call/v2:{admission.operation_id}:{target_stage}:{row.slide_index}:{c.logical_call_ordinal}:{key_digest}".encode("utf-8"))]
            if len(matched) != 1:
                _reject("svg_timeout_repurchase_manifest_conflict")
            name = "completed_checkpoints"
            witness = {**deepcopy(witness), "consumed_attempt_count": witness["consumed_attempt_count"] + 1,
                "logical_call_ids": witness["logical_call_ids"] + [str(matched[0].id)],
                "source_digest": sha256_hex(saved["svg_content"].encode("utf-8")),
                "author_digest": _digest(saved.get("attempts", [])),
                "native_digest": _digest(saved.get("nativeDeclarations")),
                "quality_digest": _digest(saved.get("quality")), "max_additional_author_calls": 0,
                "input_digest": saved.get("input_digest"), "known_replacement_preserved": target_stage == "repair"}

        if name == "completed_checkpoints":
            svg = saved.get("svg_content", "")
            if (sha256_hex(svg.encode("utf-8")) != witness["source_digest"]
                or _digest(saved.get("attempts", [])) != witness["author_digest"]):
                _reject("svg_timeout_repurchase_manifest_conflict")
        elif saved.get("svg_content") or saved.get("attempts") or saved.get("provider_failures"):
            _reject("svg_timeout_repurchase_manifest_conflict")
        item = {**deepcopy(witness), "id": str(row.id), "row_digest": _digest(row.model_dump(mode="json"))}
        rebound[name].append(item)
    manifest = {**deepcopy(previous), **rebound,
        "zero_dispatch_descendant": not known_result, "known_result_descendant": known_result, "previous_approval_manifest_hash": _digest(previous),
        "origin_operation_id": origin_id, "origin_job_id": origin_job_id,
        "origin_operation_digest": _digest(origin.model_dump(mode="json")),
        "origin_job_digest": _digest(origin_job.model_dump(mode="json")),
        "operation_id": str(operation.id), "operation_version": operation.version,
        "operation_fence": operation.fencing_token, "operation_digest": _digest(operation.model_dump(mode="json")),
        "presentation_id": str(operation.presentation_id), "presentation_revision": presentation_revision,
        "source_manifest_presentation_id": previous.get("source_manifest_presentation_id", str(origin.presentation_id)),
        "source_manifest_revision": previous.get("source_manifest_revision", origin.expected_revision + 1),
        "source_manifest_parent_revision": previous.get("source_manifest_parent_revision", previous["presentation_revision"]),
        "job_id": str(job.id), "job_digest": _digest(job.model_dump(mode="json")),
        "frozen_request_digest": _digest(job.request_payload), "admitted_request_digest": job.request_sha256,
        "old_admission": admission.model_dump(mode="json"), "old_logical_calls": [c.model_dump(mode="json") for c in calls],
        "old_resource_leases": [lease.model_dump(mode="json") for lease in leases],
        "old_runtime_budgets": [b.model_dump(mode="json") for b in budgets], "old_runtime_reservations": [r.model_dump(mode="json") for r in reservations],
        "initial_remaining": admission.planned_request_limit - admission.initial_request_count, "retry_remaining": admission.retry_request_limit - admission.retry_request_count,
        "remaining_logical_call_limit": admission.planned_request_limit + admission.retry_request_limit - admission.initial_request_count - admission.retry_request_count,
        "zero_new_provider_calls_in_preserved_failed_child": True}
    if known_result:
        baseline_initial, baseline_retry, _, _ = await verified_append_only_consumption_baseline(session, operation)
        manifest["previous_consumed_logical_calls"] = baseline_initial + baseline_retry + len(calls)
        manifest["previous_consumed_author_calls"] += sum(c.stage in {"author", "repair"} for c in calls)
        manifest["max_additional_author_calls"] = len(manifest["unknown_checkpoints"])
        manifest["approved_replacement_already_consumed"] = not manifest["unknown_checkpoints"]
        for entry in manifest["completed_checkpoints"]:
            entry["max_additional_author_calls"] = 0
            entry.pop("known_quality_repair_approved", None)
            if entry.get("known_replacement_preserved"):
                continue
            source = next(r.svg_checkpoint for r in rows if str(r.id) == entry["id"])
            if (source.get("quality") or {}).get("passed") is False:
                entry["max_additional_author_calls"] = 1
                entry["known_quality_repair_approved"] = True
                entry["next_provider_ordinal"] = entry["consumed_attempt_count"] + 1
    if known_result and operation.error_code == "presentation_generation_failed":
        if manifest["unknown_checkpoints"] or manifest["unstarted_checkpoints"] or any((r.svg_checkpoint.get("quality") or {}).get("passed") is not True for r in rows):
            _reject("svg_timeout_repurchase_not_applicable")
        stage_binding = (await _bind_reviewed_stage_receipts(session, previous, operation, job, rows, calls, stage_review_decision) if stage_review_decision is not None else _bind_completed_stage_receipts(previous, operation, job, rows, calls))
        manifest["inherited_stage_execution"] = stage_binding
        manifest["remaining_vqa_rounds"] = stage_binding["remaining_rounds"]
        manifest["max_additional_author_calls"] = 0
        for entry in manifest["completed_checkpoints"]:
            entry["max_additional_author_calls"] = 0
            entry.pop("known_quality_repair_approved", None)
    if not known_result and previous.get("inherited_stage_execution"):
        manifest["inherited_stage_execution"] = await _rebind_inherited_stage_execution(session, previous, operation, job, rows)
    await _bind_confirmed_cache_inputs(session, manifest, operation, rows)
    return TimeoutRepurchaseRecovery(rows, manifest)


async def verified_append_only_consumption_baseline(session, operation):
    """Verify every immutable ancestor ledger; keep child consumption separate."""
    from models.sql.workspace import WorkspaceOperation
    cursor = operation
    seen = set()
    initial = retry = 0
    expected_total = None
    while True:
        if str(cursor.id) in seen:
            _reject("svg_timeout_repurchase_manifest_conflict")
        seen.add(str(cursor.id))
        manifest, _ = await verify_append_only_child_lineage(session, cursor)
        if expected_total is None:
            expected_total = manifest["previous_consumed_logical_calls"]
        original = manifest["old_admission"]
        admission = await session.get(PublicOperationAdmission, UUID(original["id"]))
        if admission is None or _digest(admission.model_dump(mode="json")) != _digest(original):
            _reject("svg_timeout_repurchase_manifest_conflict")
        calls = list(await session.scalars(select(PublicProviderLogicalCall).where(PublicProviderLogicalCall.admission_id == admission.id)))
        actual = sorted((c.model_dump(mode="json") for c in calls), key=lambda c: c["id"])
        expected = sorted(manifest["old_logical_calls"], key=lambda c: c["id"])
        if (_digest(actual) != _digest(expected) or any(c["state"] == "reserved" for c in actual)
                or admission.initial_request_count + admission.retry_request_count != len(actual)):
            _reject("svg_timeout_repurchase_manifest_conflict")
        initial += admission.initial_request_count
        retry += admission.retry_request_count
        if not (manifest.get("zero_dispatch_descendant") or manifest.get("known_result_descendant")):
            if initial + retry != expected_total:
                _reject("svg_timeout_repurchase_manifest_conflict")
            return initial, retry, manifest["total_logical_call_limit"], admission.retry_request_limit
        cursor = await session.get(WorkspaceOperation, UUID(manifest["operation_id"]))


def _bind_completed_stage_receipts(previous, operation, job, rows, calls):
    from services.generation_core.quality.advisory_record import record_matches, advisory_flagged_pages
    from utils.llm_provider import get_llm_provider, get_model
    plan = job.request_payload.get("_internal_svg_stage_plan") or {}
    seed = next(r for r in rows if r.slide_index == 0)
    progress = seed.svg_checkpoint.get("trusted_stage_execution") or {}
    if (progress.get("operation_id") != str(operation.id) or progress.get("plan_digest") != _digest(plan)
            or progress.get("authority") != (plan.get("source_lineage") or {}).get("execution_authority")
            or len(progress.get("stages", [])) != len(plan.get("partitions", []))):
        _reject("svg_timeout_repurchase_manifest_conflict")
    accepted, remaining, used = [], [], set()
    provider = str(get_llm_provider().value)
    model = str(get_model())
    for ordinal, indices in enumerate(plan["partitions"]):
        stage = progress["stages"][ordinal]
        if stage.get("indices") != indices:
            _reject("svg_timeout_repurchase_manifest_conflict")
        pages = [next(r.svg_checkpoint for r in rows if r.slide_index == i) for i in indices]
        inputs = [_digest({"svg": p.get("svg_content"), "png": p.get("png_sha256")}) for p in pages]
        if stage.get("page_digests") != inputs:
            _reject("svg_timeout_repurchase_manifest_conflict")
        if stage.get("status") != "accepted":
            if stage.get("advisory") is not None:
                _reject("svg_timeout_repurchase_manifest_conflict")
            remaining.append(ordinal)
            continue
        advisory = stage.get("advisory") or {}
        digests = [sha256_hex(p["svg_content"].encode()) for p in pages]
        if (not record_matches(advisory, digests) or advisory_flagged_pages(advisory)
                or advisory.get("vision", {}).get("model") != model):
            _reject("svg_timeout_repurchase_manifest_conflict")
        bindings = advisory.get("pageAdvisories") or []
        if len(bindings) != len(pages) or any(b.get("pngDigest") != p.get("png_sha256") for b,p in zip(bindings,pages,strict=True)):
            _reject("svg_timeout_repurchase_manifest_conflict")
        from services.provider_usage_instrumentation import ProviderUsageScope
        semantic_keys = {"authored.vision-advisory", ProviderUsageScope(job_id=job.id, operation="authored_generation", stage="vqa", attempt_number=job.attempt_number, round_ordinal=ordinal).public_logical_call_key("authored.vision-advisory")}
        expected_hashes = {sha256_hex(f"public-provider-call/v2:{operation.id}:vqa:-1:{c.logical_call_ordinal}:{sha256_hex(key.encode())}".encode()) for c in calls if c.stage == "vqa" for key in semantic_keys}
        matching = [c for c in calls if c.stage == "vqa" and c.state == "reported" and c.outcome == "succeeded"
            and c.provider_id == provider and c.model_id == model and str(c.id) not in used and c.idempotency_key_hash in expected_hashes]
        if len(matching) != 1:
            _reject("svg_timeout_repurchase_manifest_conflict")
        used.add(str(matching[0].id))
        accepted.append({"ordinal": ordinal, "stage": deepcopy(stage), "logical_call": matching[0].model_dump(mode="json")})
    if len(used) != sum(c.stage == "vqa" for c in calls):
        _reject("svg_timeout_repurchase_manifest_conflict")
    return {"schema": "immutable-stage-receipt-inheritance/v1", "source_operation_id": str(operation.id),
        "source_job_id": str(job.id), "owner_id": str(operation.owner_id), "tenant_id": previous["tenant_id"],
        "source_progress_digest": _digest(progress), "plan_digest": _digest(plan),
        "frozen_style_digest": previous["frozen_style_digest"], "provider": provider, "model": model,
        "accepted": accepted, "remaining_rounds": remaining}


async def _rebind_inherited_stage_execution(session, previous, operation, job, rows):
    """Rebind a zero-dispatch child without changing its accepted ancestor receipt."""
    from utils.llm_provider import get_llm_provider, get_model
    from utils.owner_context import get_current_tenant_id
    binding = deepcopy(previous["inherited_stage_execution"])
    seed = next((row for row in rows if row.slide_index == 0), None)
    payload = seed.svg_checkpoint if seed is not None else {}
    inherited = payload.get("inherited_stage_execution") or {}
    original = inherited.get("original_progress")
    progress = payload.get("trusted_stage_execution")
    accepted = binding.get("accepted") or []
    expected = deepcopy(original or {})
    expected["operation_id"] = str(operation.id)
    expected["stages"] = [deepcopy(entry["stage"]) for entry in accepted]
    plan = job.request_payload.get("_internal_svg_stage_plan") or {}
    compared_progress = deepcopy(progress or {})
    current_stages = compared_progress.get("stages") or []
    if current_stages[:len(accepted)] != expected["stages"] or any(stage.get("status") == "accepted" for stage in current_stages[len(accepted):]):
        _reject("svg_timeout_repurchase_manifest_conflict")
    compared_progress["stages"] = expected["stages"]
    if (inherited.get("binding") != binding or _digest(original) != binding.get("source_progress_digest")
        or compared_progress != expected or binding.get("plan_digest") != _digest(plan)
        or binding.get("owner_id") != str(operation.owner_id) or binding.get("tenant_id") != str(get_current_tenant_id())
        or binding.get("provider") != str(get_llm_provider().value) or binding.get("model") != get_model()):
        _reject("svg_timeout_repurchase_manifest_conflict")
    for entry in accepted:
        stage = entry["stage"]
        pages = [next((row.svg_checkpoint for row in rows if row.slide_index == index), {}) for index in stage["indices"]]
        if stage.get("page_digests") != [_digest({"svg": page.get("svg_content"), "png": page.get("png_sha256")}) for page in pages]:
            _reject("svg_timeout_repurchase_manifest_conflict")
        receipt = entry["logical_call"]
        call = await session.get(PublicProviderLogicalCall, UUID(receipt["id"]))
        admission = await session.get(PublicOperationAdmission, call.admission_id) if call is not None else None
        if (call is None or call.model_dump(mode="json") != receipt or call.state != "reported" or call.outcome != "succeeded"
            or admission is None or str(admission.operation_id) != entry.get("source_operation_id", binding["source_operation_id"])
            or str(admission.owner_id) != str(operation.owner_id) or str(admission.tenant_id) != str(get_current_tenant_id())):
            _reject("svg_timeout_repurchase_manifest_conflict")
    binding["source_progress_digest"] = _digest(progress)
    binding["current_source_operation_id"] = str(operation.id)
    binding["current_source_job_id"] = str(job.id)
    return binding


async def _bind_confirmed_cache_inputs(session, manifest, operation, rows):
    """Bind confirmed results to actual author input instead of pre-author placeholders."""
    from models.sql.workspace import WorkspaceOperation
    from utils.owner_context import get_current_tenant_id
    allowed = set()
    ancestor = operation
    while ancestor is not None:
        if str(ancestor.id) in allowed or ancestor.owner_id != operation.owner_id:
            _reject("svg_timeout_repurchase_manifest_conflict")
        allowed.add(str(ancestor.id))
        ancestor = await session.get(WorkspaceOperation, ancestor.retry_of_operation_id) if ancestor.retry_of_operation_id else None
    for entry in manifest.get("completed_checkpoints", []):
        saved = next(row.svg_checkpoint for row in rows if row.slide_index == entry["slide_index"])
        actual_input = saved.get("input_digest")
        if entry.get("input_digest") == actual_input:
            continue
        if (not isinstance(actual_input, str) or not actual_input or not entry.get("logical_call_ids")
            or sha256_hex(saved.get("svg_content", "").encode()) != entry.get("source_digest")
            or _digest(saved.get("attempts", [])) != entry.get("author_digest")
            or _digest(saved.get("nativeDeclarations")) != entry.get("native_digest")):
            _reject("svg_timeout_repurchase_manifest_conflict")
        receipts = []
        for logical_id in entry["logical_call_ids"]:
            call = await session.get(PublicProviderLogicalCall, UUID(str(logical_id)))
            admission = await session.get(PublicOperationAdmission, call.admission_id) if call is not None else None
            if (call is None or call.state != "reported" or call.outcome != "succeeded" or call.stage not in {"author", "repair"}
                or call.slide_index != entry["slide_index"] or admission is None or str(admission.operation_id) not in allowed
                or admission.owner_id != operation.owner_id or admission.tenant_id != str(get_current_tenant_id())):
                _reject("svg_timeout_repurchase_manifest_conflict")
            receipts.append({"logical_call_id": str(call.id), "operation_id": str(admission.operation_id)})
        entry["previous_input_digest"] = entry.get("input_digest")
        entry["input_digest"] = actual_input
        entry["confirmed_author_input_binding"] = {"input_digest": actual_input, "receipts": receipts}


async def _bind_reviewed_stage_receipts(session, previous, operation, job, rows, calls, decision):
    """An authenticated agent review accepts only exact subjective VQA findings."""
    from services.generation_core.quality.advisory_record import record_matches, advisory_flagged_pages
    from services.provider_usage_instrumentation import ProviderUsageScope
    from utils.llm_provider import get_model
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    review = _validated_stage_review_input(decision)
    if (review["operationId"] != str(operation.id) or review["presentationId"] != str(operation.presentation_id)
        or str(get_current_owner_id()) != str(operation.owner_id) or previous["tenant_id"] != str(get_current_tenant_id())
        or review["model"] != get_model()):
        _reject("svg_timeout_repurchase_manifest_conflict")
    binding = await _rebind_inherited_stage_execution(session, previous, operation, job, rows)
    seed = next(row for row in rows if row.slide_index == 0)
    progress = seed.svg_checkpoint["trusted_stage_execution"]
    ordinal = review["stageOrdinal"]
    if ordinal != len(binding["accepted"]) or len(progress["stages"]) != ordinal + 1:
        _reject("svg_timeout_repurchase_manifest_conflict")
    stage = deepcopy(progress["stages"][ordinal])
    pages = [next(row.svg_checkpoint for row in rows if row.slide_index == index) for index in stage["indices"]]
    advisory = stage.get("advisory")
    raw_digests = [sha256_hex(page.get("svg_content", "").encode()) for page in pages]
    if (stage.get("status") != "review_pending" or not advisory
        or stage.get("page_digests") != [_digest({"svg": page.get("svg_content"), "png": page.get("png_sha256")}) for page in pages]
        or not record_matches(advisory, raw_digests) or advisory.get("vision", {}).get("model") != get_model()
        or any(page.get("quality", {}).get("passed") is not True for page in pages)):
        _reject("svg_timeout_repurchase_manifest_conflict")
    flags = advisory_flagged_pages(advisory)
    findings = review_coverage(advisory.get("vision")).findings()
    expected = []
    for local_index in flags:
        issues = next((item.get("issues") for item in findings if item.get("index") == local_index), None)
        if (not isinstance(local_index, int) or not 0 <= local_index < len(pages) or not issues
            or not set(issues).issubset({"illegible", "cramped"})):
            _reject("svg_timeout_repurchase_manifest_conflict")
        expected.append({"index": stage["indices"][local_index], "svgSha256": raw_digests[local_index],
                         "pngSha256": pages[local_index].get("png_sha256"), "issues": sorted(issues),
                         "decision": "accepted", "observedReadable": True, "majorClippingOrOverlapObserved": False})
    if not expected or sorted(review["pages"], key=lambda page: page["index"]) != sorted(expected, key=lambda page: page["index"]):
        _reject("svg_timeout_repurchase_manifest_conflict")
    call = next((call for call in calls if str(call.id) == review["vqaLogicalCallId"]), None)
    scope = ProviderUsageScope(job_id=job.id, operation="authored_generation", stage="vqa", attempt_number=job.attempt_number, round_ordinal=ordinal)
    key = scope.public_logical_call_key("authored.vision-advisory")
    fingerprint = sha256_hex(f"public-provider-call/v2:{operation.id}:vqa:-1:{call.logical_call_ordinal}:{sha256_hex(key.encode())}".encode()) if call is not None else None
    if (call is None or call.stage != "vqa" or call.state != "reported" or call.outcome != "succeeded"
        or call.idempotency_key_hash != fingerprint or call.provider_id != binding["provider"] or call.model_id != binding["model"]
        or len([call for call in calls if call.stage == "vqa"]) != 1):
        _reject("svg_timeout_repurchase_manifest_conflict")
    png_bindings = advisory.get("pageAdvisories") or []
    if len(png_bindings) != len(pages) or any(
        b.get("pngDigest") != p.get("png_sha256") for b, p in zip(png_bindings, pages, strict=False)
    ):
        _reject("svg_timeout_repurchase_manifest_conflict")
    accepted_review = {**deepcopy(review), "actorOwnerId": str(operation.owner_id), "tenantId": str(get_current_tenant_id()),
                       "frozenStyleDigest": previous["frozen_style_digest"], "frozenRequestDigest": previous["frozen_request_digest"],
                       "advisoryDigest": advisory["digest"], "sourceOperationId": str(operation.id), "sourceJobId": str(job.id)}
    accepted_review["digest"] = _digest(accepted_review)
    stage["status"] = "accepted"
    stage["reviewer_acceptance"] = accepted_review
    binding["accepted"].append({"ordinal": ordinal, "stage": stage, "logical_call": call.model_dump(mode="json"), "source_operation_id": str(operation.id)})
    binding["remaining_rounds"] = []
    binding["source_progress_digest"] = _digest(progress)
    binding["current_source_operation_id"] = str(operation.id)
    binding["current_source_job_id"] = str(job.id)
    return binding


def _validated_stage_review_input(decision):
    fields = {"schema", "reviewerKind", "reviewerRole", "model", "effort", "userVisualReviewConfirmed", "vqaFlagsRemoved", "hardGatesRelaxed",
              "operationId", "presentationId", "vqaLogicalCallId", "stageOrdinal", "evidenceDigest", "pages"}
    if (not isinstance(decision, dict) or set(decision) != fields or decision.get("schema") != "svg-stage-review-decision/v1"
        or decision.get("reviewerKind") != "agent" or decision.get("userVisualReviewConfirmed") is not False
        or decision.get("vqaFlagsRemoved") is not False or decision.get("hardGatesRelaxed") is not False
        or type(decision.get("stageOrdinal")) is not int or decision["stageOrdinal"] < 0
        or not isinstance(decision.get("pages"), list) or not decision["pages"]
        or not isinstance(decision.get("evidenceDigest"), str) or len(decision["evidenceDigest"]) != 64
        or any(c not in "0123456789abcdef" for c in decision["evidenceDigest"])):
        _reject("svg_timeout_repurchase_manifest_conflict")
    for page in decision["pages"]:
        if not isinstance(page, dict) or set(page) != {"index", "svgSha256", "pngSha256", "issues", "decision", "observedReadable", "majorClippingOrOverlapObserved"}:
            _reject("svg_timeout_repurchase_manifest_conflict")
        if (type(page.get("index")) is not int or page["index"] < 0 or page.get("decision") != "accepted"
            or page.get("observedReadable") is not True or page.get("majorClippingOrOverlapObserved") is not False
            or not isinstance(page.get("issues"), list) or not page["issues"] or not set(page["issues"]).issubset({"illegible", "cramped"})):
            _reject("svg_timeout_repurchase_manifest_conflict")
    return deepcopy(decision)
