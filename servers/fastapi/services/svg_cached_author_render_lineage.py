"""Retired cached-author-render recovery: its stored markers and the lineage it created.

The zero-provider cached-author-render recovery (``svg-cached-author-render/v1``
to ``/v3``) bound every approval to ``APPROVED_CONVERTER_CODE_DIGEST``
(``52b0a07b...0ad0``), the digest of twelve converter source files on 2026-10-07.
No converter source state matched it after 2026-10-08 (``306c3ea9f``), so every
new approval, every replay and every marker projection was rejected. The
admission, manifest builder and replay were deleted on 2026-11-05 (owner
decision, ``docs/explicit-verifier-revisions-design-20261105.md``).

What remains is what stored records still need, unchanged:

* the revision and marker constants, so stored operations and checkpoints are
  still recognised;
* ``verify_action`` and ``approved_sidecars``, which reject exactly as the
  stale approval did, with the same codes and in the same order;
* ``verify_child_lineage`` and the witnesses it rebuilds, moved verbatim: a
  child that one of these recoveries created and completed while the approval
  matched still proves its inherited source manifest through it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json

from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
from models.sql.slide import SlideModel
from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation, WorkspaceArtifact
from services.svg_recovery_witness import author_witness, digest, unknown_provider as _unknown_provider
from services.svg_timeout_repurchase import _live
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import sha256_hex


REVISION = "svg-cached-author-render/v1"
REVISION_V2 = "svg-cached-author-render/v2"
REVISION_V3 = "svg-cached-author-render/v3"
REVISIONS = frozenset({REVISION, REVISION_V2, REVISION_V3})
ACTION_PREFIX = "svg-cached-author-render:"
ACTION_PREFIX_V2 = "svg-cached-author-render-v2:"
ACTION_PREFIX_V3 = "svg-cached-author-render-v3:"
MARKER_SCHEMA = "svg-cached-author-render/v1"
MARKER_SCHEMA_V2 = "svg-cached-author-render/v2"
MARKER_SCHEMA_V3 = "svg-cached-author-render/v3"


def marker_schema(revision: str) -> str:
    if revision == REVISION:
        return MARKER_SCHEMA
    if revision == REVISION_V2:
        return MARKER_SCHEMA_V2
    if revision == REVISION_V3:
        return MARKER_SCHEMA_V3
    _reject("svg_cached_author_render_revision_invalid")


class CachedAuthorRenderRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.retryable = False


def _reject(code: str = "svg_cached_author_render_not_safe") -> None:
    raise CachedAuthorRenderRejected(code)


def operation_request_hash(payload: dict) -> str:
    """Match the workspace operation's durable request hash contract."""
    return sha256_hex(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                 sort_keys=True, separators=(",", ":"),
                                 default=str).encode("utf-8"))


def source_receipt_witness(payload: dict) -> str:
    receipt = payload.get("source_preservation") or {}
    return digest({key: value for key, value in receipt.items()
                   if key != "rendering_verified"})


def cached_svg(payload: dict) -> str:
    """Exactly one archived author SVG may be used as the replay source."""
    sources = (payload.get("svg_content"), payload.get("last_valid_svg"))
    candidates = [value for value in sources if isinstance(value, str) and value]
    if not candidates or any(value not in (None, "") and value != candidates[0]
                                   for value in sources):
        _reject("svg_cached_author_render_svg_witness_changed")
    return candidates[0]


def approved_sidecars(payload: dict) -> dict[str, dict]:
    """Fixed-axis sidecars that a cached-author-render marker approves: none any more.

    A page without a marker has none. A page with one could only be reproved
    against the stale approved converter digest, so it was rejected here, and it
    still is, with the same code.
    """
    marker = payload.get("cached_author_render") or {}
    if not marker:
        return {}
    _reject("svg_cached_author_render_sidecar_untrusted")


def derived_child_request_valid(job, manifest: dict, *,
                                operation_id, presentation_id,
                                original_parent_request: dict | None = None) -> bool:
    from services.presentation_generation_job_service import request_sha256

    if job.request_sha256 != request_sha256(job.request_payload):
        return False
    lineage = (job.request_payload or {}).get("_svg_recovery_lineage") or {}
    revision = manifest.get("recovery_revision")
    expected_lineage = {
        "schema": ("svg-cached-author-render-lineage/v3" if revision == REVISION_V3
                   else "svg-cached-author-render-lineage/v2" if revision == REVISION_V2
                   else "svg-cached-author-render-lineage/v1"),
        "parent_operation_id": manifest.get("operation_id"),
        "parent_job_id": manifest.get("job_id"),
        "parent_presentation_id": manifest.get("presentation_id"),
        "child_operation_id": str(operation_id),
        "child_presentation_id": str(presentation_id),
        "approval_manifest_hash": digest(manifest),
        "child_job_id": str(job.id),
    }
    if revision in {REVISION_V2, REVISION_V3}:
        expected_lineage["action_operation_id"] = manifest.get("action_operation_id")
    if revision not in REVISIONS or lineage != expected_lineage:
        return False
    derived = deepcopy(job.request_payload)
    derived.pop("_svg_recovery_lineage", None)
    if derived.get("presentation_id") != str(presentation_id):
        return False
    slot_fields = (
        "job_request_presentation_id_present" in manifest,
        "job_request_presentation_id" in manifest,
    )
    if slot_fields == (False, False) and revision == REVISION:
        # Early v1 approvals predate the explicit slot witness. Reconstruct it
        # only from the locked, digest-matched original job request.
        if (not isinstance(original_parent_request, dict)
                or digest(original_parent_request) != manifest.get("job_request_digest")):
            return False
        original_slot_present = "presentation_id" in original_parent_request
        original_slot_value = original_parent_request.get("presentation_id")
    elif slot_fields == (True, True):
        original_slot_present = manifest["job_request_presentation_id_present"]
        original_slot_value = manifest["job_request_presentation_id"]
        if (type(original_slot_present) is not bool
                or (not original_slot_present and original_slot_value is not None)
                or (original_parent_request is not None
                    and not isinstance(original_parent_request, dict))):
            return False
        if original_parent_request is not None and (
                digest(original_parent_request) != manifest.get("job_request_digest")
                or original_slot_present != ("presentation_id" in original_parent_request)
                or original_slot_value != original_parent_request.get("presentation_id")):
            return False
    else:
        return False
    if original_slot_present:
        derived["presentation_id"] = original_slot_value
    else:
        derived.pop("presentation_id", None)
    return (digest(derived) == manifest.get("job_request_digest")
            and str(job.presentation_id) == str(presentation_id)
            and str(job.workspace_operation_id) == str(operation_id))


async def _prior_child_witnesses(session, parent, *, now, lock: bool):
    """Require the one existing v1 child to be terminal and entirely provider-free."""
    from uuid import UUID
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from models.sql.presentation import PRESENTATION_LIFECYCLE_STAGING

    def locked(statement):
        return statement.with_for_update() if lock else statement

    children = list(await session.scalars(locked(select(WorkspaceOperation).where(
        WorkspaceOperation.retry_of_operation_id == parent.id,
        WorkspaceOperation.owner_scope == parent.owner_scope,
    ).order_by(WorkspaceOperation.id))))
    if len(children) != 1:
        _reject("svg_cached_author_render_prior_child_unproven")
    witnesses = []
    for child in children:
        child_manifest = child.request_payload.get("svg_cached_author_render_manifest") or {}
        if (child.state != "failed" or child.kind != "presentation.generate"
                or child.owner_id != parent.owner_id
                or child.result_revision is not None or _live(child.lease_expires_at, now)
                or child.request_hash != operation_request_hash(child.request_payload)
                or child.request_payload.get("recovery_revision") != REVISION
                or child.request_payload.get("retry_of_operation_id") != str(parent.id)
                or child.request_payload.get("svg_cached_author_render_manifest_hash")
                != digest(child_manifest)
                or child_manifest.get("recovery_revision") != REVISION
                or child_manifest.get("operation_id") != str(parent.id)
                or child_manifest.get("operation_digest") != digest(parent.model_dump(mode="json"))
                or child.request_payload.get("principal_tenant_id")
                != child_manifest.get("tenant_id")
                or not isinstance(child.error_detail, dict)
                or child.error_detail.get("retryable") is not False
                or child.error_code not in {
                    "public_provider_request_limit_exceeded",
                    "svg_cached_author_render_lineage_invalid",
                    "svg_cached_author_render_source_mismatch",
                    "svg_cached_author_render_invalid",
                    "svg_cached_author_render_projection_unproven",
                }):
            _reject("svg_cached_author_render_prior_child_unproven")
        presentation = await session.scalar(locked(select(PresentationModel).where(
            PresentationModel.id == child.presentation_id,
            PresentationModel.owner_id == parent.owner_id)))
        job = await session.scalar(locked(select(PresentationGenerationJob).where(
            PresentationGenerationJob.workspace_operation_id == child.id,
            PresentationGenerationJob.presentation_id == child.presentation_id)))
        if (presentation is None or presentation.revision != 0
                or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_STAGING
                or job is None or job.state != "failed" or job.completed_slides != 0
                or _live(job.lease_expires_at, now)
                or str(child.request_payload.get("job_id")) != str(job.id)
                or child.request_payload.get("generation_request") != job.request_payload
                or child.request_payload.get("svg_recovery_lineage")
                != job.request_payload.get("_svg_recovery_lineage")):
            _reject("svg_cached_author_render_prior_child_unproven")
        task = await session.scalar(locked(select(AsyncPresentationGenerationTaskModel).where(
            AsyncPresentationGenerationTaskModel.id == job.task_id)))
        admission = await session.scalar(locked(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == child.id)))
        if (task is None or task.status != "error" or admission is None
                or admission.owner_id != parent.owner_id
                or admission.tenant_id != child_manifest.get("tenant_id")
                or admission.operation_kind != "generation"
                or admission.state not in {"failed", "budget_exhausted"}
                or admission.terminal_at is None
                or any(getattr(admission, name) != 0 for name in (
                    "planned_request_limit", "retry_request_limit",
                    "estimated_cost_limit_microunits", "actual_cost_limit_microunits",
                    "initial_request_count", "retry_request_count",
                    "estimated_reserved_microunits", "estimated_consumed_microunits",
                    "actual_reserved_microunits", "actual_consumed_microunits",
                    "actual_cost_unavailable_count"))):
            _reject("svg_cached_author_render_prior_provider_unresolved")
        original_manifest, original_job = await verify_child_lineage(session, child)
        if (original_manifest != child_manifest
                or str(original_job.id) != str(job.id)):
            _reject("svg_cached_author_render_prior_child_unproven")
        parent_rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == UUID(child_manifest["job_id"])
        ).order_by(AuthoredSlideCheckpoint.slide_index))))
        rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job.id
        ).order_by(AuthoredSlideCheckpoint.slide_index))))
        if ([row.slide_index for row in rows] != list(range(job.total_slides))
                or job.total_slides != child_manifest.get("expected_page_count")
                or len(parent_rows) != job.total_slides):
            _reject("svg_cached_author_render_prior_checkpoint_unproven")
        recovery = CachedAuthorRenderRecovery(parent_rows, child_manifest)
        for row, original in zip(rows, parent_rows, strict=False):
            if (row.role != original.role
                    or row.author_status != "completed"
                    or row.illustrate_status != "pending"
                    or row.render_status != "pending"
                    or row.persist_status != "pending"
                    or row.failed_stage is not None or row.error is not None
                    or row.author_html is not None or row.illustrated_html is not None
                    or row.render_path is not None or row.render_sha256 is not None
                    or row.artifact_refs is not None
                    or row.render_started_at is not None
                    or row.render_completed_at is not None
                    or row.persist_started_at is not None
                    or row.persist_completed_at is not None
                    or row.svg_checkpoint != child_checkpoint_payload(
                        original, recovery, child_operation_id=child.id)):
                _reject("svg_cached_author_render_prior_checkpoint_unproven")
        calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission.id))))
        usage = list(await session.scalars(locked(select(ProviderUsageReceipt).where(
            ProviderUsageReceipt.job_id == job.id))))
        leases = list(await session.scalars(locked(select(PublicResourceLease).where(
            PublicResourceLease.admission_id == admission.id
        ).order_by(PublicResourceLease.id))))
        budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
            ProviderRuntimeBudget.job_id == job.id
        ).order_by(ProviderRuntimeBudget.id))))
        reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
            ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id,
        ).where(ProviderRuntimeBudget.job_id == job.id)
            .order_by(ProviderRuntimeReservation.id))))
        slide = await session.scalar(locked(select(SlideModel.id).where(
            SlideModel.presentation == child.presentation_id)))
        artifact = await session.scalar(locked(select(WorkspaceArtifact.id).where(
            WorkspaceArtifact.operation_id == child.id)))
        if (calls or usage or reservations or slide is not None or artifact is not None
                or any(item.state not in {"released", "cancelled", "expired"}
                       for item in leases)
                or any(item.request_count or item.estimated_consumed
                       or item.estimated_reserved or item.actual_consumed
                       or item.estimated_limit not in (None, 0)
                       or item.actual_limit not in (None, 0) for item in budgets)):
            _reject("svg_cached_author_render_prior_provider_unresolved")
        witnesses.append({
            "operation_id": str(child.id),
            "operation_digest": digest(child.model_dump(mode="json")),
            "operation_version": child.version,
            "presentation_id": str(child.presentation_id),
            "presentation_revision": presentation.revision,
            "presentation_digest": digest(presentation.model_dump(mode="json")),
            "job_digest": digest(job.model_dump(mode="json")),
            "task_digest": digest(task.model_dump(mode="json")),
            "admission_digest": digest(admission.model_dump(mode="json")),
            "checkpoint_digests": [digest(row.model_dump(mode="json")) for row in rows],
            "resource_leases_digest": digest([item.model_dump(mode="json") for item in leases]),
            "runtime_budgets_digest": digest([item.model_dump(mode="json") for item in budgets]),
            "v1_manifest_hash": digest(child_manifest),
        })
    return witnesses


async def _rendered_v2_child_witness(session, parent, prior_v1: dict, *, now, lock: bool):
    """Seal the terminal v2 action child without reusing any rendered output."""
    from uuid import UUID
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from models.sql.presentation import PRESENTATION_LIFECYCLE_STAGING

    def locked(statement):
        return statement.with_for_update() if lock else statement

    children = list(await session.scalars(locked(select(WorkspaceOperation).where(
        WorkspaceOperation.retry_of_operation_id == UUID(prior_v1["operation_id"]),
        WorkspaceOperation.owner_scope == parent.owner_scope,
    ).order_by(WorkspaceOperation.id))))
    if len(children) != 1:
        _reject("svg_cached_author_render_prior_child_unproven")
    child = children[0]
    prior_manifest = child.request_payload.get("svg_cached_author_render_manifest") or {}
    if (child.kind != "presentation.generate" or child.state != "failed"
            or child.owner_id != parent.owner_id or child.result_revision is not None
            or _live(child.lease_expires_at, now)
            or _unknown_provider(child.request_payload)
            or _unknown_provider(child.error_detail)
            or child.request_hash != operation_request_hash(child.request_payload)
            or child.error_code not in {"public_provider_request_limit_exceeded",
                                        "svg_source_publication_binding_invalid"}
            or not isinstance(child.error_detail, dict)
            or child.error_detail.get("retryable") is not False
            or child.request_payload.get("recovery_revision") != REVISION_V2
            or child.request_payload.get("retry_of_operation_id") != prior_v1["operation_id"]
            or child.request_payload.get("svg_cached_author_render_manifest_hash")
            != digest(prior_manifest)
            or prior_manifest.get("recovery_revision") != REVISION_V2
            or prior_manifest.get("max_additional_provider_calls") != 0
            or prior_manifest.get("append_only_recovery") is not True
            or prior_manifest.get("operation_id") != str(parent.id)
            or prior_manifest.get("action_operation_id") != prior_v1["operation_id"]
            or prior_manifest.get("prior_children") != [prior_v1]
            or child.request_payload.get("principal_tenant_id")
            != prior_manifest.get("tenant_id")):
        _reject("svg_cached_author_render_prior_child_unproven")
    presentation = await session.scalar(locked(select(PresentationModel).where(
        PresentationModel.id == child.presentation_id,
        PresentationModel.owner_id == parent.owner_id)))
    job = await session.scalar(locked(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == child.id,
        PresentationGenerationJob.presentation_id == child.presentation_id)))
    if (presentation is None or presentation.revision != 0
            or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_STAGING
            or job is None or job.state != "failed"
            or job.total_slides != prior_manifest.get("expected_page_count")
            or job.completed_slides != job.total_slides
            or _unknown_provider(job.request_payload)
            or _unknown_provider(getattr(job, "last_error", None))
            or _live(job.lease_expires_at, now)
            or str(child.request_payload.get("job_id")) != str(job.id)
            or child.request_payload.get("generation_request") != job.request_payload
            or child.request_payload.get("svg_recovery_lineage")
            != job.request_payload.get("_svg_recovery_lineage")):
        _reject("svg_cached_author_render_prior_child_unproven")
    task = await session.scalar(locked(select(AsyncPresentationGenerationTaskModel).where(
        AsyncPresentationGenerationTaskModel.id == job.task_id)))
    admission = await session.scalar(locked(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == child.id)))
    if (task is None or task.status != "error"
            or _unknown_provider(task.error) or admission is None
            or admission.owner_id != parent.owner_id
            or admission.tenant_id != prior_manifest.get("tenant_id")
            or admission.operation_kind != "generation"
            or admission.state not in {"failed", "budget_exhausted"}
            or admission.terminal_at is None
            or any(getattr(admission, name) != 0 for name in (
                "planned_request_limit", "retry_request_limit",
                "estimated_cost_limit_microunits", "actual_cost_limit_microunits",
                "initial_request_count", "retry_request_count",
                "estimated_reserved_microunits", "estimated_consumed_microunits",
                "actual_reserved_microunits", "actual_consumed_microunits",
                "actual_cost_unavailable_count"))):
        _reject("svg_cached_author_render_prior_provider_unresolved")
    verified_manifest, verified_job = await verify_child_lineage(session, child)
    if verified_manifest != prior_manifest or str(verified_job.id) != str(job.id):
        _reject("svg_cached_author_render_prior_child_unproven")
    rows = list(await session.scalars(locked(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id,
    ).order_by(AuthoredSlideCheckpoint.slide_index))))
    if [row.slide_index for row in rows] != list(range(job.total_slides)):
        _reject("svg_cached_author_render_prior_checkpoint_unproven")
    for row, page in zip(rows, prior_manifest.get("pages") or [], strict=False):
        payload = row.svg_checkpoint or {}
        marker = payload.get("cached_author_render") or {}
        archive = payload.get("cached_author_render_archive") or {}
        original = (archive.get("checkpoint_row") or {}).get("svg_checkpoint") or {}
        if (row.author_status != "completed" or row.render_status != "completed"
                or row.persist_status != "pending" or row.failed_stage is not None
                or row.error is not None or row.role != (archive.get("checkpoint_row") or {}).get("role")
                or _unknown_provider(payload)
                or page.get("slide_index") != row.slide_index
                or marker.get("schema") != MARKER_SCHEMA_V2
                or marker.get("revision") != REVISION_V2
                or marker.get("operation_id") != str(child.id)
                or marker.get("source_operation_id") != str(parent.id)
                or marker.get("slide_index") != row.slide_index
                or marker.get("status") != "completed"
                or marker.get("approval_manifest_hash") != digest(prior_manifest)
                or archive.get("approval_manifest_hash") != digest(prior_manifest)
                or archive.get("approval_manifest") != prior_manifest
                or digest(archive.get("checkpoint_row")) != page.get("row_digest")
                or marker.get("svg_sha256") != page.get("svg_sha256")
                or marker.get("raw_sha256") != page.get("raw_sha256")
                or marker.get("input_digest") != page.get("input_digest")
                or payload.get("input_digest") != page.get("input_digest")
                or (payload.get("quality") or {}).get("passed") is not True
                or (payload.get("quality") or {}).get("verified") is not True
                or (payload.get("quality") or {}).get("measurement_complete") is not True
                or (payload.get("source_preservation") or {}).get("rendering_verified") is not True
                or author_witness(payload) != page.get("author_receipt_digest")
                or source_receipt_witness(payload) != page.get("source_receipt_digest")
                or payload.get("nativeDeclarations") != original.get("nativeDeclarations")
                or payload.get("source_manifest_ref") != original.get("source_manifest_ref")
                or payload.get("source_manifest_digest") != original.get("source_manifest_digest")
                or marker.get("verified_fixed_axis_migrations")
                != page.get("verified_fixed_axis_migrations")):
            _reject("svg_cached_author_render_prior_checkpoint_unproven")
        if sha256_hex(cached_svg(payload).encode()) != page["svg_sha256"]:
            _reject("svg_cached_author_render_prior_checkpoint_unproven")
    calls = list(await session.scalars(locked(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id))))
    usage = list(await session.scalars(locked(select(ProviderUsageReceipt).where(
        ProviderUsageReceipt.job_id == job.id))))
    leases = list(await session.scalars(locked(select(PublicResourceLease).where(
        PublicResourceLease.admission_id == admission.id
    ).order_by(PublicResourceLease.id))))
    budgets = list(await session.scalars(locked(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id
    ).order_by(ProviderRuntimeBudget.id))))
    reservations = list(await session.scalars(locked(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id,
    ).where(ProviderRuntimeBudget.job_id == job.id)
        .order_by(ProviderRuntimeReservation.id))))
    slide = await session.scalar(locked(select(SlideModel.id).where(
        SlideModel.presentation == child.presentation_id)))
    artifact = await session.scalar(locked(select(WorkspaceArtifact.id).where(
        WorkspaceArtifact.operation_id == child.id)))
    if (calls or usage or reservations or slide is not None or artifact is not None
            or any(item.state not in {"released", "cancelled", "expired"} for item in leases)
            or any(item.request_count or item.estimated_consumed
                   or item.estimated_reserved or item.actual_consumed
                   or item.estimated_limit not in (None, 0)
                   or item.actual_limit not in (None, 0) for item in budgets)):
        _reject("svg_cached_author_render_prior_provider_unresolved")
    return {
        "operation_id": str(child.id),
        "operation_version": child.version,
        "operation_digest": digest(child.model_dump(mode="json")),
        "presentation_id": str(child.presentation_id),
        "presentation_revision": presentation.revision,
        "presentation_digest": digest(presentation.model_dump(mode="json")),
        "job_digest": digest(job.model_dump(mode="json")),
        "task_digest": digest(task.model_dump(mode="json")),
        "admission_digest": digest(admission.model_dump(mode="json")),
        "checkpoint_digests": [digest(row.model_dump(mode="json")) for row in rows],
        "resource_leases_digest": digest([item.model_dump(mode="json") for item in leases]),
        "runtime_budgets_digest": digest([item.model_dump(mode="json") for item in budgets]),
        "v2_manifest_hash": digest(prior_manifest),
        "source_pin_digest": prior_manifest.get("source_pin_digest"),
        "source_manifest_digest": prior_manifest.get("source_manifest_digest"),
        "style_digest": prior_manifest.get("style_digest"),
        "job_request_digest": prior_manifest.get("job_request_digest"),
        "source_page_witnesses": [{key: page.get(key) for key in (
            "slide_index", "row_digest", "raw_sha256", "svg_sha256",
            "input_digest", "author_receipt_digest", "source_receipt_digest"
        )} for page in prior_manifest.get("pages") or []],
    }


async def verify_child_lineage(session, operation):
    """Prove an inherited source pointer belongs to the immutable parent."""
    from uuid import UUID
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.provider_usage_receipt import ProviderUsageReceipt

    manifest = operation.request_payload.get("svg_cached_author_render_manifest") or {}
    revision = manifest.get("recovery_revision")
    action_operation_id = (manifest.get("action_operation_id")
                           if revision in {REVISION_V2, REVISION_V3}
                           else manifest.get("operation_id"))
    if (manifest.get("append_only_recovery") is not True
            or revision not in REVISIONS
            or operation.request_payload.get("recovery_revision") != revision
            or operation.request_payload.get("svg_cached_author_render_manifest_hash")
            != digest(manifest)
            or str(operation.retry_of_operation_id) != action_operation_id
            or str(operation.owner_id) != manifest.get("owner_id")
            or str(operation.request_payload.get("principal_tenant_id"))
            != manifest.get("tenant_id")):
        _reject("svg_cached_author_render_lineage_invalid")
    parent = await session.get(WorkspaceOperation, UUID(manifest["operation_id"]))
    parent_presentation = await session.get(
        PresentationModel, UUID(manifest["presentation_id"]))
    legacy_request_slot = (
        manifest.get("recovery_revision") == REVISION
        and "job_request_presentation_id_present" not in manifest
        and "job_request_presentation_id" not in manifest
    )
    parent_job = (await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.id == UUID(manifest["job_id"])
    ).with_for_update().execution_options(populate_existing=True))
        if legacy_request_slot else await session.get(
        PresentationGenerationJob, UUID(manifest["job_id"])))
    parent_task = await session.get(AsyncPresentationGenerationTaskModel, parent_job.task_id) if parent_job else None
    job = await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id,
    ))
    if (parent is None or parent_presentation is None
            or parent_job is None or parent_task is None or job is None
            or operation.request_hash != operation_request_hash(operation.request_payload)
            or operation.request_payload.get("generation_request") != job.request_payload
            or operation.request_payload.get("svg_recovery_lineage")
            != job.request_payload.get("_svg_recovery_lineage")
            or digest(parent.model_dump(mode="json")) != manifest.get("operation_digest")
            or digest(parent_presentation.model_dump(mode="json"))
            != manifest.get("presentation_digest")
            or digest(parent_job.model_dump(mode="json")) != manifest.get("job_digest")
            or digest(parent_task.model_dump(mode="json")) != manifest.get("parent_task_digest")
            or not derived_child_request_valid(job, manifest,
                operation_id=operation.id, presentation_id=operation.presentation_id,
                original_parent_request=parent_job.request_payload)
            or digest(job.svg_style_snapshot) != manifest.get("style_digest")
            or str(job.id) != str(operation.request_payload.get("job_id"))):
        _reject("svg_cached_author_render_lineage_invalid")
    rows = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == parent_job.id,
    ).order_by(AuthoredSlideCheckpoint.slide_index)))
    if ([row.slide_index for row in rows] != list(range(manifest.get("expected_page_count", -1)))
            or [digest(row.model_dump(mode="json")) for row in rows]
            != [page.get("row_digest") for page in manifest.get("pages", [])]):
        _reject("svg_cached_author_render_lineage_invalid")
    admission = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == parent.id))
    if admission is None or digest(admission.model_dump(mode="json")) != manifest.get("old_admission_digest"):
        _reject("svg_cached_author_render_lineage_invalid")
    calls = list(await session.scalars(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id,
    ).order_by(PublicProviderLogicalCall.slide_index)))
    usage = list(await session.scalars(select(ProviderUsageReceipt).where(
        ProviderUsageReceipt.job_id == parent_job.id,
    ).order_by(ProviderUsageReceipt.id)))
    leases = list(await session.scalars(select(PublicResourceLease).where(
        PublicResourceLease.admission_id == admission.id,
    ).order_by(PublicResourceLease.id)))
    budgets = list(await session.scalars(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == parent_job.id,
    ).order_by(ProviderRuntimeBudget.id)))
    reservations = list(await session.scalars(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id,
    ).where(ProviderRuntimeBudget.job_id == parent_job.id)
        .order_by(ProviderRuntimeReservation.id)))
    if any(digest([item.model_dump(mode="json") for item in records]) != manifest.get(key)
           for key, records in (
               ("old_logical_calls_digest", calls),
               ("old_usage_digest", usage),
               ("old_resource_leases_digest", leases),
               ("old_runtime_budgets_digest", budgets),
               ("old_runtime_reservations_digest", reservations),
           )):
        _reject("svg_cached_author_render_lineage_invalid")
    if revision in {REVISION_V2, REVISION_V3}:
        direct_prior = await _prior_child_witnesses(
            session, parent, now=get_current_utc_datetime(), lock=False)
        prior = list(direct_prior)
        if revision == REVISION_V3:
            prior.append(await _rendered_v2_child_witness(
                session, parent, direct_prior[0],
                now=get_current_utc_datetime(), lock=False))
        if (prior != manifest.get("prior_children")
                or len(prior) != (2 if revision == REVISION_V3 else 1)
                or prior[-1]["operation_id"] != action_operation_id
                or prior[-1]["operation_version"] != manifest.get("action_operation_version")
                or prior[-1]["presentation_id"] != manifest.get("action_presentation_id")
                or prior[-1]["presentation_revision"] != manifest.get("action_presentation_revision")):
            _reject("svg_cached_author_render_lineage_invalid")
    return manifest, job


@dataclass
class CachedAuthorRenderRecovery:
    rows: list[AuthoredSlideCheckpoint]
    manifest: dict

    @property
    def manifest_hash(self) -> str:
        return digest(self.manifest)


def action_id_for(operation, presentation_revision: int, *, revision: str = REVISION) -> str:
    if revision not in REVISIONS:
        _reject("svg_cached_author_render_revision_invalid")
    return ((ACTION_PREFIX_V3 if revision == REVISION_V3
             else ACTION_PREFIX_V2 if revision == REVISION_V2 else ACTION_PREFIX)
            + str(operation.id) + ":" + str(operation.version)
            + ":" + str(presentation_revision))


async def verify_action(session, operation, request, *, presentation_revision):
    """Reject a cached-author-render approval request exactly as the stale approval did.

    The request checks and the v2/v3 prior-child lookups (with their row locks)
    run in the same order as before, so each request fails with the same code;
    every request that passes them fails on the unapproved converter.
    """
    revision = request.recovery_revision
    if (request.action != "retry" or revision not in REVISIONS
            or request.expected_operation_version is None or request.expected_revision is None
            or operation.version != request.expected_operation_version
            or presentation_revision != request.expected_revision):
        _reject("svg_cached_author_render_approval_required")
    action_key = action_id_for(operation, presentation_revision, revision=revision)
    if request.action_id != action_key or request.idempotency_key != action_key:
        _reject("svg_cached_author_render_approval_required")
    if revision in {REVISION_V2, REVISION_V3}:
        action = operation
        if revision == REVISION_V3:
            action = await _locked_retry_parent(session, operation)
            if action.retry_of_operation_id is None:
                _reject("svg_cached_author_render_prior_child_unproven")
            await _locked_presentation(session, action)
        await _locked_presentation(session, await _locked_retry_parent(session, action))
    _reject("svg_cached_author_render_converter_unapproved")


async def _locked_retry_parent(session, operation):
    if operation.retry_of_operation_id is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    parent = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation.retry_of_operation_id,
        WorkspaceOperation.owner_scope == operation.owner_scope,
    ).with_for_update())
    if parent is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    return parent


async def _locked_presentation(session, operation):
    presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == operation.presentation_id,
        PresentationModel.owner_id == operation.owner_id,
    ).with_for_update())
    if presentation is None:
        _reject("svg_cached_author_render_prior_child_unproven")


def child_checkpoint_payload(row, recovery, *, child_operation_id):
    """Copy one locked parent witness into a new child row; never edit parent."""
    pages = {page["slide_index"]: page for page in recovery.manifest["pages"]}
    original = row.model_dump(mode="json")
    payload = deepcopy(row.svg_checkpoint)
    page = pages.get(row.slide_index)
    if page is None or digest(original) != page["row_digest"]:
        _reject("svg_cached_author_render_checkpoint_changed")
    payload["cached_author_render_archive"] = {
        "checkpoint_row": original,
        "approval_manifest": deepcopy(recovery.manifest),
        "approval_manifest_hash": recovery.manifest_hash,
    }
    payload["cached_author_render"] = {
        "schema": marker_schema(recovery.manifest["recovery_revision"]),
        "revision": recovery.manifest["recovery_revision"],
        "operation_id": str(child_operation_id),
        "source_operation_id": recovery.manifest["operation_id"],
        "approval_manifest_hash": recovery.manifest_hash,
        "slide_index": row.slide_index,
        "status": "pending",
        "verified_fixed_axis_migrations": deepcopy(
            page["verified_fixed_axis_migrations"]),
        **{key: page[key] for key in (
            "raw_sha256", "svg_sha256", "input_digest", "author_receipt_digest", "source_receipt_digest"
        )},
    }
    return payload
