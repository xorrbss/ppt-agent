"""Candidate: locked, zero-provider rerender of known authored SVG pages.

This staged module is deliberately disabled until a reviewed fixed-axis code
digest is set below. It is not wired into the running product.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall, PublicResourceLease
from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
from models.sql.slide import SlideModel
from models.sql.presentation import PresentationModel
from models.sql.source_content import SourceContentManifestRecord
from models.sql.workspace import WorkspaceOperation, WorkspaceArtifact
from services.svg_timeout_repurchase import _live
from utils.datetime_utils import get_current_utc_datetime
from utils.hashing import canonical_json_sha256, sha256_hex


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

# Filled only after the fixed-axis owner's exact production patch is reviewed.
# The digest covers every parser/projection file that changes that conversion.
APPROVED_CONVERTER_CODE_DIGEST = "52b0a07b7d21957896d327f49be977a90523555b2c3c95401323a5455d590ad0"
CONVERTER_FILES = (
    "services/generation_core/drivers/svg/native_chart.py",
    "services/generation_core/drivers/svg/native_declarations.py",
    "services/generation_core/drivers/svg/native_publish.py",
    "services/generation_core/drivers/svg/fixed_axis_migration.py",
    "services/generation_core/drivers/svg/export_projection.py",
    "services/generation_core/drivers/svg/export_scene.py",
    "services/generation_core/native_objects/payload.py",
    "services/generation_core/drivers/svg/export_chart_style.py",
    "vendor/slide_master/svg_to_pptx/native_objects/chart_data.py",
    "vendor/slide_master/svg_to_pptx/native_objects/chart_xml.py",
    "vendor/slide_master/MANIFEST.json",
    "workers/public_svg_pptx_convert.py",
)


class CachedAuthorRenderRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.retryable = False


def _reject(code: str = "svg_cached_author_render_not_safe") -> None:
    raise CachedAuthorRenderRejected(code)


def digest(value: object) -> str:
    return canonical_json_sha256(value, allow_nan=True)


def operation_request_hash(payload: dict) -> str:
    """Match the workspace operation's durable request hash contract."""
    return sha256_hex(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                 sort_keys=True, separators=(",", ":"),
                                 default=str).encode("utf-8"))


def author_witness(payload: dict) -> str:
    from services.svg_classification_remeasurement import author_witness_digest

    return author_witness_digest(payload)


def source_receipt_witness(payload: dict) -> str:
    receipt = payload.get("source_preservation") or {}
    return digest({key: value for key, value in receipt.items()
                   if key != "rendering_verified"})


def _unknown_provider(value: object) -> bool:
    if isinstance(value, dict):
        return any(_unknown_provider(item) for item in value.values())
    if isinstance(value, list):
        return any(_unknown_provider(item) for item in value)
    return value in {
        "public_provider_result_unknown", "public_provider_timeout",
        "provider_outcome:public_provider_result_unknown",
    } if isinstance(value, str) else False


def converter_code_hashes() -> list[dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    entries = []
    for relative in CONVERTER_FILES:
        path = root / relative
        if not path.is_file():
            _reject("svg_cached_author_render_converter_missing")
        entries.append({"path": relative, "sha256": sha256_hex(path.read_bytes())})
    return entries


def converter_code_digest() -> str:
    return digest(converter_code_hashes())


def cached_svg(payload: dict) -> str:
    """Exactly one archived author SVG may be used as the replay source."""
    sources = (payload.get("svg_content"), payload.get("last_valid_svg"))
    candidates = [value for value in sources if isinstance(value, str) and value]
    if not candidates or any(value not in (None, "") and value != candidates[0]
                                   for value in sources):
        _reject("svg_cached_author_render_svg_witness_changed")
    return candidates[0]


def verified_fixed_axis_migrations(payload: dict, svg: str, *,
                                   failed_drops: set[str]) -> dict[str, dict]:
    """Construct trusted sidecars from a bound raw declaration, never client data."""
    from services.generation_core.drivers.svg.native_declarations import bound_declarations
    from services.generation_core.drivers.svg.fixed_axis_migration import (
        MIGRATION_CONTRACT, COHERENT_GRID_V1, declaration_sha256,
    )

    record, current = bound_declarations({**payload, "svg_content": svg}, svg)
    if not current or not isinstance(record, dict):
        _reject("svg_cached_author_render_declaration_unbound")
    objects = record.get("objects") or []
    by_id = {item.get("id"): item for item in objects if isinstance(item, dict)}
    if len(by_id) != len(objects) or not failed_drops.issubset(by_id):
        _reject("svg_cached_author_render_declaration_unbound")
    sidecars = {}
    for element_id, item in by_id.items():
        raw = item.get("raw")
        if item.get("kind") != "chart" or not isinstance(raw, dict):
            continue
        if (raw.get("geometryContract") != COHERENT_GRID_V1
                or "axisContract" in raw):
            continue
        if (raw.get("chartType") not in {"bar", "column"}
                or raw.get("grouping") != "clustered"
                or payload.get("source_preservation", {}).get("source_native_charts") != []):
            _reject("svg_cached_author_render_declaration_unbound")
        sidecars[element_id] = {
            "contract": MIGRATION_CONTRACT,
            "rawSvgSha256": sha256_hex(svg.encode()),
            "rawDeclarationSha256": declaration_sha256(raw),
        }
    if not failed_drops.issubset(sidecars):
        _reject("svg_cached_author_render_declaration_unbound")
    return sidecars


def fixed_axis_failed_drops(payload: dict) -> set[str]:
    """Only the measured, exact converter defect can authorize migration."""
    initial = payload.get("initial_quality") or payload.get("quality") or {}
    failures = initial.get("failures") or []
    if not failures:
        return set()
    if len(failures) != 1 or failures[0].get("kind") != "source_native_chart_unprojectable":
        _reject("svg_cached_author_render_wrong_failure")
    dropped = (failures[0].get("native_summary") or {}).get("dropped") or []
    if (not dropped or failures[0].get("missing_count") != len(dropped)
            or any(item.get("kind") != "chart"
                   or item.get("reason") != "native_chart_json_invalid"
                   or not isinstance(item.get("id"), str) for item in dropped)):
        _reject("svg_cached_author_render_wrong_failure")
    ids = {item["id"] for item in dropped}
    if len(ids) != len(dropped):
        _reject("svg_cached_author_render_wrong_failure")
    return ids


def retry_fenced_axis_failure(row: AuthoredSlideCheckpoint, payload: dict) -> bool:
    """Recognize the measured render RETRY that cleared svg_content before budget exit."""
    if (row.author_status != "running" or row.render_status != "pending"
            or row.persist_status != "pending" or row.failed_stage is not None
            or row.error is not None or row.render_sha256 is not None
            or payload.get("svg_content") is not None
            or not isinstance(payload.get("last_valid_svg"), str)
            or not payload["last_valid_svg"]
            or payload.get("quality") not in (None, {})
            or payload.get("png_sha256") is not None
            or payload.get("png_path") is not None
            or payload.get("inflight") is not None):
        return False
    initial = payload.get("initial_quality")
    attempts = payload.get("attempts")
    repair = payload.get("local_readability_repair")
    if (not isinstance(initial, dict) or not isinstance(attempts, list)
            or len(attempts) != 1 or not isinstance(attempts[0], dict)
            or initial.get("stage") != "first_response"
            or initial.get("measurement_complete") is not True
            or initial.get("passed") is not False
            or attempts[0].get("quality") != {
                key: value for key, value in initial.items() if key != "stage"}
            or repair != {
                "status": "page_recomposition_required",
                "reason": "native_data_requires_recomposition",
                "provider_call_authorized": False,
            }):
        return False
    try:
        hint = json.loads(payload.get("repair_hint"))
        drops = fixed_axis_failed_drops(payload)
    except (TypeError, ValueError, CachedAuthorRenderRejected):
        return False
    return (isinstance(hint, dict)
            and hint.get("kind") == "svg_readability_repair"
            and hint.get("failures") == initial.get("failures")
            and bool(drops))


def approved_sidecars(payload: dict) -> dict[str, dict]:
    """Reprove a server-created locked marker at every native projection."""
    marker = payload.get("cached_author_render") or {}
    if not marker:
        return {}
    archive = payload.get("cached_author_render_archive") or {}
    manifest = archive.get("approval_manifest") or {}
    original_row = archive.get("checkpoint_row") or {}
    original = original_row.get("svg_checkpoint") or {}
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    index = marker.get("slide_index")
    pages = manifest.get("pages") or []
    witness = next((page for page in pages if page.get("slide_index") == index), None)
    revision = manifest.get("recovery_revision")
    if (revision not in REVISIONS or witness is None
            or marker.get("schema") != marker_schema(revision)
            or marker.get("revision") != revision
            or marker.get("approval_manifest_hash") != digest(manifest)
            or archive.get("approval_manifest_hash") != digest(manifest)
            or marker.get("source_operation_id") != manifest.get("operation_id")
            or str(get_current_owner_id()) != manifest.get("owner_id")
            or get_current_tenant_id() != manifest.get("tenant_id")
            or digest(original_row) != witness.get("row_digest")
            or not APPROVED_CONVERTER_CODE_DIGEST
            or converter_code_hashes() != manifest.get("converter_code_hashes")
            or converter_code_digest() != APPROVED_CONVERTER_CODE_DIGEST
            or manifest.get("converter_code_digest") != APPROVED_CONVERTER_CODE_DIGEST
            or payload.get("nativeDeclarations") != original.get("nativeDeclarations")
            or payload.get("source_preservation", {}).get("manifest_digest")
            != manifest.get("source_manifest_digest")
            or source_receipt_witness(payload) != witness.get("source_receipt_digest")
            or payload.get("source_manifest_ref") != original.get("source_manifest_ref")
            or payload.get("source_manifest_digest") != original.get("source_manifest_digest")):
        _reject("svg_cached_author_render_sidecar_untrusted")
    svg = cached_svg(original)
    if (sha256_hex(svg.encode()) != witness.get("svg_sha256")
            or payload.get("svg_content") != svg
            or marker.get("verified_fixed_axis_migrations")
            != witness.get("verified_fixed_axis_migrations")):
        _reject("svg_cached_author_render_sidecar_untrusted")
    expected = verified_fixed_axis_migrations(
        original, svg, failed_drops=set(witness.get("failed_fixed_axis_ids") or []))
    if expected != witness.get("verified_fixed_axis_migrations"):
        _reject("svg_cached_author_render_sidecar_untrusted")
    return expected


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
        for row, original in zip(rows, parent_rows):
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
    for row, page in zip(rows, prior_manifest.get("pages") or []):
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


async def build_manifest(session, operation, *, presentation_revision: int,
                         recovery_revision: str = REVISION, action_operation=None,
                         action_presentation_revision: int | None = None) -> CachedAuthorRenderRecovery:
    """Rebuild every witness under the workspace action's open DB transaction."""
    from services.presentation_generation_staging import _request_from_durable_payload
    from services.source_content.manifest_store import admit_source_content_manifest_pin
    from services.svg_source_layout import verify_source_receipt
    from services.svg_asset_persistence import load_source_manifest_pointer
    from services.artifact_store import configured_public_artifact_service
    from services.generation_core.drivers.svg.native_publish import page_native_objects, canvas_point_per_px
    from services.authored_canvas import request_canvas_metadata
    from services.document_canvas_generation import (
        svg_readability_policy, validate_svg_readability_policy,
    )
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    now = get_current_utc_datetime()
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if (recovery_revision not in REVISIONS
            or (recovery_revision == REVISION) != (action_operation is None)):
        _reject("svg_cached_author_render_revision_invalid")
    if not APPROVED_CONVERTER_CODE_DIGEST or converter_code_digest() != APPROVED_CONVERTER_CODE_DIGEST:
        _reject("svg_cached_author_render_converter_unapproved")
    if (owner is None or not tenant or operation.owner_id != owner
            or operation.kind != "presentation.generate" or operation.state != "failed"
            or operation.error_code != "public_provider_retry_limit_exceeded"
            or not isinstance(operation.error_detail, dict)
            or operation.error_detail.get("retryable") is not False
            or operation.result_revision is not None or _live(operation.lease_expires_at, now)):
        _reject()
    parent_presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == operation.presentation_id,
        PresentationModel.owner_id == owner,
    ).with_for_update())
    if parent_presentation is None or parent_presentation.revision != presentation_revision:
        _reject("svg_cached_author_render_presentation_changed")
    prior_children = []
    if recovery_revision == REVISION:
        existing_child = await session.scalar(select(WorkspaceOperation).where(
            WorkspaceOperation.retry_of_operation_id == operation.id,
            WorkspaceOperation.owner_scope == operation.owner_scope,
        ).with_for_update())
        if existing_child is not None:
            _reject("svg_cached_author_render_child_exists")
    else:
        action_presentation = await session.scalar(select(PresentationModel).where(
            PresentationModel.id == action_operation.presentation_id,
            PresentationModel.owner_id == owner,
        ).with_for_update())
        if (action_presentation is None
                or action_presentation.revision != action_presentation_revision
                or action_operation.owner_id != owner
                or action_operation.owner_scope != operation.owner_scope
                or action_operation.retry_of_operation_id != operation.id):
            _reject("svg_cached_author_render_prior_child_unproven")
        prior_children = await _prior_child_witnesses(
            session, operation, now=now, lock=True)
        if (len(prior_children) != 1
                or prior_children[0]["operation_id"] != str(action_operation.id)
                or prior_children[0]["operation_digest"]
                != digest(action_operation.model_dump(mode="json"))
                or prior_children[0]["operation_version"] != action_operation.version
                or prior_children[0]["presentation_digest"]
                != digest(action_presentation.model_dump(mode="json"))):
            _reject("svg_cached_author_render_prior_child_unproven")
    job = await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id,
    ).with_for_update())
    if (job is None or job.state != "failed" or job.resume_allowed is not False
            or not isinstance(job.total_slides, int) or job.total_slides < 1
            or job.completed_slides != 0
            or job.request_payload.get("strategy") != "svg"
            or not isinstance(job.request_sha256, str)
            or len(job.request_sha256) != 64
            or job.request_sha256 != operation.request_hash
            or operation.request_payload.get("generation_request") != job.request_payload
            or str(operation.request_payload.get("job_id")) != str(job.id)
            or str(operation.request_payload.get("task_id")) != str(job.task_id)
            or str(job.id) != str(operation.request_payload.get("job_id"))
            or _live(job.lease_expires_at, now)):
        _reject()
    parent_task = await session.scalar(select(AsyncPresentationGenerationTaskModel).where(
        AsyncPresentationGenerationTaskModel.id == job.task_id,
    ).with_for_update())
    if parent_task is None:
        _reject("svg_cached_author_render_parent_task_missing")
    admission = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id,
    ).with_for_update())
    if (admission is None or admission.state not in {"failed", "budget_exhausted"}
            or admission.terminal_at is None
            or admission.owner_id != owner or admission.tenant_id != tenant
            or admission.initial_request_count != job.total_slides
            or admission.retry_request_count != 0
            or admission.estimated_reserved_microunits or admission.actual_reserved_microunits):
        _reject()
    leases = list(await session.scalars(select(PublicResourceLease).where(
        PublicResourceLease.admission_id == admission.id,
    ).order_by(PublicResourceLease.id).with_for_update()))
    if any(item.state not in {"released", "cancelled", "expired"} for item in leases):
        _reject("svg_cached_author_render_live_lease")
    calls = list(await session.scalars(select(PublicProviderLogicalCall).where(
        PublicProviderLogicalCall.admission_id == admission.id,
    ).order_by(PublicProviderLogicalCall.slide_index).with_for_update()))
    if (len(calls) != job.total_slides
            or [item.slide_index for item in calls] != list(range(job.total_slides))
            or any(item.stage != "author"
                   or type(item.logical_call_ordinal) is not int
                   or item.logical_call_ordinal < 1
                   or item.state != "reported" or item.outcome != "succeeded"
                   or item.reconciled_at is None for item in calls)):
        _reject("svg_cached_author_render_provider_unresolved")
    usage = list(await session.scalars(select(ProviderUsageReceipt).where(
        ProviderUsageReceipt.job_id == job.id,
    ).order_by(ProviderUsageReceipt.id).with_for_update()))
    if (len(usage) != job.total_slides
            or any(item.stage != "author" or item.outcome != "succeeded"
                   or item.request_count != 1
                               for item in usage)):
        _reject("svg_cached_author_render_usage_unresolved")
    budgets = list(await session.scalars(select(ProviderRuntimeBudget).where(
        ProviderRuntimeBudget.job_id == job.id,
    ).order_by(ProviderRuntimeBudget.id).with_for_update()))
    reservations = list(await session.scalars(select(ProviderRuntimeReservation).join(
        ProviderRuntimeBudget, ProviderRuntimeBudget.id == ProviderRuntimeReservation.budget_id,
    ).where(ProviderRuntimeBudget.job_id == job.id)
        .order_by(ProviderRuntimeReservation.id).with_for_update()))
    if (any(item.estimated_reserved for item in budgets)
            or any(item.state in {"reserved", "uncertain"} for item in reservations)):
        _reject("svg_cached_author_render_provider_unresolved")
    published = await session.scalar(select(SlideModel.id).where(
        SlideModel.presentation == operation.presentation_id,
        SlideModel.svg_content.is_not(None),
        SlideModel.svg_content != "",
    ).with_for_update())
    if published is not None:
        _reject("svg_cached_author_render_already_published")

    request = _request_from_durable_payload(job.request_payload)
    pin = request.source_content_manifest_pin
    if (pin is None or request.vision_qa or request.web_search
            or request.n_slides != job.total_slides):
        _reject("svg_cached_author_render_source_plan_changed")
    source_record = await session.scalar(select(SourceContentManifestRecord).where(
        SourceContentManifestRecord.manifest_id == pin.manifest_id,
        SourceContentManifestRecord.owner_id == owner,
    ).with_for_update())
    if source_record is None:
        _reject("svg_cached_author_render_source_missing")
    try:
        source_manifest = await admit_source_content_manifest_pin(
            session, owner_id=owner, manifest_id=pin.manifest_id, digest=pin.digest,
        )
    except Exception as error:
        raise CachedAuthorRenderRejected("svg_cached_author_render_source_pin_invalid") from error
    if len(source_manifest.units) != job.total_slides:
        _reject("svg_cached_author_render_source_page_count_changed")
    recovery_policy = svg_readability_policy(
        request_canvas_metadata(request), source_restyle=True,
        instructions=request.instructions)
    if recovery_policy.get("quality_target_font_pt", 0) < 9.5:
        _reject("svg_cached_author_render_quality_policy_invalid")
    rows = list(await session.scalars(select(AuthoredSlideCheckpoint).where(
        AuthoredSlideCheckpoint.job_id == job.id,
    ).order_by(AuthoredSlideCheckpoint.slide_index).with_for_update()))
    if [row.slide_index for row in rows] != list(range(job.total_slides)):
        _reject("svg_cached_author_render_page_count_changed")
    pages = []
    source_asset_binding = None
    for row in rows:
        index = row.slide_index
        payload = row.svg_checkpoint or {}
        retry_fenced = (admission.state == "budget_exhausted"
                        and retry_fenced_axis_failure(row, payload))
        if (not (row.author_status == "completed" or retry_fenced)
                or row.render_status not in {"pending", "failed"}
                or row.persist_status != "pending"
                or (row.render_status == "failed" and row.failed_stage != "render")
                or (row.render_status == "pending" and (
                    row.failed_stage is not None or row.render_sha256 is not None
                    or (isinstance(payload.get("quality"), dict)
                        and payload["quality"].get("measurement_complete") is True)))):
            _reject("svg_cached_author_render_checkpoint_changed")
        attempts = payload.get("attempts") or []
        preservation = payload.get("source_preservation") or {}
        if (len(attempts) != 1 or attempts[0].get("kind") != "provider"
                or payload.get("inflight") is not None or payload.get("terminal")
                or _unknown_provider(payload)
                or payload.get("schema_version") != "svg-slide-checkpoint/v2"
                or payload.get("source_format") != "svg"
                or (payload.get("readability_policy") or {}).get("version")
                not in {"svg-physical-readability/v1", "svg-physical-readability/v2"}
                or preservation.get("contract_version") != "svg-source-preservation/v1"
                or (preservation.get("source_authoring_contract") or {}).get("version")
                != "svg-source-whole-page/v1"
                or (preservation.get("source_chart_contract") or {}).get("version")
                != "svg-source-native-chart/v1"
                or payload.get("cache_only_remeasurement")
                or payload.get("append_only_cache") or payload.get("recovery_revision")
                or payload.get("model_attempt_count") != 1
                or not isinstance(payload.get("input_digest"), str)):
            _reject("svg_cached_author_render_checkpoint_changed")
        try:
            historical_policy = validate_svg_readability_policy(payload.get("readability_policy"))
        except ValueError as error:
            raise CachedAuthorRenderRejected(
                "svg_cached_author_render_checkpoint_policy_invalid") from error
        if historical_policy != (job.svg_style_snapshot or {}).get("readability_policy"):
            _reject("svg_cached_author_render_checkpoint_policy_changed")
        receipt = attempts[0].get("receipt") or {}
        raw = attempts[0].get("raw_response")
        svg = cached_svg(payload)
        if (not isinstance(raw, str) or not raw or not isinstance(svg, str) or not svg
                or receipt.get("validation_error") is not None
                or str(receipt.get("raw_digest", "")).removeprefix("sha256:") != sha256_hex(raw.encode())
                or str(receipt.get("source_digest", "")).removeprefix("sha256:") != sha256_hex(svg.encode())
                or (payload.get("source_preservation") or {}).get("manifest_digest")
                != source_manifest.manifest_digest):
            _reject("svg_cached_author_render_svg_witness_changed")
        try:
            asset_manifest = await load_source_manifest_pointer(
                session, configured_public_artifact_service(),
                pointer=payload.get("source_manifest_ref"), owner_id=owner, tenant_id=tenant,
            )
            if asset_manifest.digest != payload.get("source_manifest_digest"):
                _reject("svg_cached_author_render_asset_pin_changed")
            binding = {
                "source_manifest_presentation_id": str(asset_manifest.presentation_id),
                "source_manifest_revision": asset_manifest.source_revision,
                "source_manifest_digest": asset_manifest.digest,
                "source_manifest_pointer_digest": digest(payload.get("source_manifest_ref")),
            }
            if source_asset_binding is None:
                source_asset_binding = binding
            elif binding != source_asset_binding:
                _reject("svg_cached_author_render_asset_pin_changed")
            verify_source_receipt(svg, payload.get("source_preservation"), source_manifest, index)
            measured_failure = row.render_status == "failed" or retry_fenced
            failed_drops = fixed_axis_failed_drops(payload) if measured_failure else set()
            if measured_failure and not failed_drops:
                _reject("svg_cached_author_render_wrong_failure")
            measured = payload.get("quality") or {}
            if (measured_failure and measured.get("failures")
                    and fixed_axis_failed_drops({"quality": measured}) != failed_drops):
                _reject("svg_cached_author_render_wrong_failure")
            sidecars = verified_fixed_axis_migrations(payload, svg, failed_drops=failed_drops)
            projected, summary = page_native_objects(
                {**payload, "svg_content": svg},
                pt_per_px=canvas_point_per_px(request_canvas_metadata(request)),
                verified_fixed_axis_migrations=sidecars)
        except Exception as error:
            raise CachedAuthorRenderRejected("svg_cached_author_render_projection_unproven") from error
        required = {item["element_id"] for item in
                    (payload.get("source_preservation") or {}).get("whole_page_native_groups", [])}
        accepted = {item["id"] for item in (projected or {}).get("objects", [])}
        if not required or not required.issubset(accepted) or summary.get("dropped"):
            _reject("svg_cached_author_render_projection_unproven")
        if failed_drops and not failed_drops.issubset(required):
            _reject("svg_cached_author_render_wrong_failure")
        pages.append({
            "slide_index": index,
            "row_id": str(row.id),
            "row_digest": digest(row.model_dump(mode="json")),
            "raw_sha256": sha256_hex(raw.encode()),
            "svg_sha256": sha256_hex(svg.encode()),
            "input_digest": payload["input_digest"],
            "author_receipt_digest": author_witness(payload),
            "source_receipt_digest": source_receipt_witness(payload),
            "failed_fixed_axis_ids": sorted(failed_drops),
            "verified_fixed_axis_migrations": sidecars,
            "retry_fenced_author": retry_fenced,
        })
    if not any(page["failed_fixed_axis_ids"] for page in pages):
        _reject("svg_cached_author_render_wrong_failure")
    manifest = {
        "recovery_revision": recovery_revision,
        "operation_id": str(operation.id),
        "operation_version": operation.version,
        "operation_request_digest": digest(operation.request_payload),
        "operation_digest": digest(operation.model_dump(mode="json")),
        "operation_error_digest": digest(operation.error_detail),
        "presentation_id": str(operation.presentation_id),
        "presentation_revision": presentation_revision,
        "presentation_digest": digest(parent_presentation.model_dump(mode="json")),
        "owner_id": str(owner), "tenant_id": tenant,
        "job_id": str(job.id), "job_request_digest": digest(job.request_payload),
        "job_request_presentation_id_present": "presentation_id" in job.request_payload,
        "job_request_presentation_id": job.request_payload.get("presentation_id"),
        "submitted_request_sha256": job.request_sha256,
        "job_digest": digest(job.model_dump(mode="json")),
        "parent_task_digest": digest(parent_task.model_dump(mode="json")),
        "style_digest": digest(job.svg_style_snapshot),
        "source_pin_digest": digest(pin.model_dump(mode="json")),
        "source_manifest_digest": source_manifest.manifest_digest,
        "source_manifest_asset_binding": source_asset_binding,
        "recovery_readability_policy": recovery_policy,
        "converter_code_hashes": converter_code_hashes(),
        "converter_code_digest": converter_code_digest(),
        "old_admission_digest": digest(admission.model_dump(mode="json")),
        "old_logical_calls_digest": digest([item.model_dump(mode="json") for item in calls]),
        "old_usage_digest": digest([item.model_dump(mode="json") for item in usage]),
        "old_resource_leases_digest": digest([item.model_dump(mode="json") for item in leases]),
        "old_runtime_budgets_digest": digest([item.model_dump(mode="json") for item in budgets]),
        "old_runtime_reservations_digest": digest([item.model_dump(mode="json") for item in reservations]),
        "pages": pages,
        "max_additional_provider_calls": 0,
        "expected_page_count": job.total_slides,
        "append_only_recovery": True,
    }
    if recovery_revision == REVISION_V2:
        manifest.update({
            "action_operation_id": str(action_operation.id),
            "action_operation_version": action_operation.version,
            "action_presentation_id": str(action_operation.presentation_id),
            "action_presentation_revision": action_presentation_revision,
            "prior_children": prior_children,
        })
    return CachedAuthorRenderRecovery(rows, manifest)


def action_id_for(operation, presentation_revision: int, *, revision: str = REVISION) -> str:
    if revision not in REVISIONS:
        _reject("svg_cached_author_render_revision_invalid")
    return ((ACTION_PREFIX_V3 if revision == REVISION_V3
             else ACTION_PREFIX_V2 if revision == REVISION_V2 else ACTION_PREFIX)
            + str(operation.id) + ":" + str(operation.version)
            + ":" + str(presentation_revision))


async def build_manifest_v2(session, action_operation, *, presentation_revision: int):
    """Reprove the failed zero-call action child and the original cache parent."""
    if action_operation.retry_of_operation_id is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    parent = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == action_operation.retry_of_operation_id,
        WorkspaceOperation.owner_scope == action_operation.owner_scope,
    ).with_for_update())
    if parent is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    parent_presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == parent.presentation_id,
        PresentationModel.owner_id == parent.owner_id,
    ).with_for_update())
    if parent_presentation is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    return await build_manifest(
        session, parent, presentation_revision=parent_presentation.revision,
        recovery_revision=REVISION_V2, action_operation=action_operation,
        action_presentation_revision=presentation_revision)


async def build_manifest_v3(session, action_operation, *, presentation_revision: int):
    """Rebuild original cached pages and bind both failed zero-call children."""
    from uuid import UUID

    if action_operation.retry_of_operation_id is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    v1_child = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == action_operation.retry_of_operation_id,
        WorkspaceOperation.owner_scope == action_operation.owner_scope,
    ).with_for_update())
    if v1_child is None or v1_child.retry_of_operation_id is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    v1_presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == v1_child.presentation_id,
        PresentationModel.owner_id == v1_child.owner_id,
    ).with_for_update())
    if v1_presentation is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    base = await build_manifest_v2(
        session, v1_child, presentation_revision=v1_presentation.revision)
    if (base.manifest.get("operation_id") != str(v1_child.retry_of_operation_id)
            or len(base.manifest.get("prior_children") or []) != 1):
        _reject("svg_cached_author_render_prior_child_unproven")
    parent = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == UUID(base.manifest["operation_id"]),
        WorkspaceOperation.owner_scope == action_operation.owner_scope,
    ).with_for_update())
    action_presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == action_operation.presentation_id,
        PresentationModel.owner_id == action_operation.owner_id,
    ).with_for_update())
    if parent is None or action_presentation is None:
        _reject("svg_cached_author_render_prior_child_unproven")
    witness = await _rendered_v2_child_witness(
        session, parent, base.manifest["prior_children"][0],
        now=get_current_utc_datetime(), lock=True)
    source_fields = ("source_pin_digest", "source_manifest_digest",
                     "style_digest", "job_request_digest")
    page_keys = ("slide_index", "row_digest", "raw_sha256", "svg_sha256",
                 "input_digest", "author_receipt_digest", "source_receipt_digest")
    if (witness["operation_id"] != str(action_operation.id)
            or witness["operation_digest"] != digest(action_operation.model_dump(mode="json"))
            or witness["operation_version"] != action_operation.version
            or witness["presentation_id"] != str(action_operation.presentation_id)
            or witness["presentation_revision"] != presentation_revision
            or witness["presentation_digest"]
            != digest(action_presentation.model_dump(mode="json"))
            or any(witness.get(key) != base.manifest.get(key) for key in source_fields)
            or witness["source_page_witnesses"] != [
                {key: page.get(key) for key in page_keys}
                for page in base.manifest["pages"]]):
        _reject("svg_cached_author_render_prior_child_unproven")
    manifest = deepcopy(base.manifest)
    manifest.update({
        "recovery_revision": REVISION_V3,
        "action_operation_id": str(action_operation.id),
        "action_operation_version": action_operation.version,
        "action_presentation_id": str(action_operation.presentation_id),
        "action_presentation_revision": presentation_revision,
        "prior_children": [base.manifest["prior_children"][0], witness],
    })
    return CachedAuthorRenderRecovery(base.rows, manifest)


async def verify_action(session, operation, request, *, presentation_revision):
    revision = request.recovery_revision
    if (request.action != "retry" or revision not in REVISIONS
            or request.expected_operation_version is None or request.expected_revision is None
            or operation.version != request.expected_operation_version
            or presentation_revision != request.expected_revision):
        _reject("svg_cached_author_render_approval_required")
    action_key = action_id_for(operation, presentation_revision, revision=revision)
    if request.action_id != action_key or request.idempotency_key != action_key:
        _reject("svg_cached_author_render_approval_required")
    if revision == REVISION_V3:
        recovery = await build_manifest_v3(
            session, operation, presentation_revision=presentation_revision)
    elif revision == REVISION_V2:
        recovery = await build_manifest_v2(
            session, operation, presentation_revision=presentation_revision)
    else:
        recovery = await build_manifest(session, operation,
                                        presentation_revision=presentation_revision)
    return recovery


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
