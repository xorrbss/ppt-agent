"""SVG export admission using the existing fenced worker and artifact authority."""
from __future__ import annotations
import json
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from dataclasses import dataclass
from sqlalchemy import select
from models.sql.slide import SlideModel
from services.artifact_store import configured_public_artifact_service
from services.artifact_store import configured_worker_artifact_cache
from services.authored_canvas import presentation_canvas_metadata
from services.document_canvas import parse_canvas_metadata
from services.public_export_operation import (
    PublicWorkerArtifactResult,
    _public_worker_output,
    _worker_ref,
    execute_isolated_export_operation,
)
from services.public_export_deadline import load_public_export_deadline_grant
from services.public_export_errors import PublicExportOperationError
from services.svg_contract import validate_svg
from services.svg_export_bundle import build_svg_export_bundle, digest as _digest
from services.svg_export_source_preservation import (
    source_preservation_receipts as _source_preservation_receipts,
    sealed_whole_page_manifest as _sealed_whole_page_manifest,
)
from workers.public_svg_export_worker import (
    SVG_EXPORT_SCHEMA,
    SVG_EXPORT_SCHEMA_V2,
    SVG_EXPORT_SCHEMA_V3,
    SVG_EXPORT_SCHEMA_V4,
    CONVERTER_VERSION,
)
from utils.hashing import sha256_hex

LOGGER = logging.getLogger(__name__)

_HEADER_REVISION_AUTHORITY_SEAL = object()


@dataclass(frozen=True)
class _HeaderRevisionAuthority:
    operation_id: str
    scene_digest: str
    digest: str
    seal: object
    parent_operation_id: str
    parent_job_id: str
    generation_revision: int


def _header_authority_current(authority, presentation, slides):
    if not isinstance(authority, _HeaderRevisionAuthority) or authority.seal is not _HEADER_REVISION_AUTHORITY_SEAL:
        return False
    return authority.scene_digest == _header_scene_digest(presentation, slides)


def _header_scene_digest(presentation, slides):
    from services.workspace_svg_header_design_operation_handler import _base_digest
    from utils.hashing import canonical_json_digest
    return canonical_json_digest({"presentationId": str(presentation.id), "ownerId": str(presentation.owner_id),
                                 "scene": _base_digest(presentation, _classified_header_slides(slides))})


def _classified_header_slides(slides):
    """Project only the old missing-classification case using the normal classifier."""
    from copy import deepcopy
    from types import SimpleNamespace
    from services.generation_core.drivers.svg.quality import page_outcome
    from services.generation_core.quality.page_outcome import PASSED, classify_page_outcomes
    result = []
    for index, slide in enumerate(slides):
        content = deepcopy(slide.content or {})
        receipt = content.get("svgReceipt") or {}
        quality = receipt.get("quality") or {}
        if (quality.get("verified") is False and "classification" not in quality
                and quality.get("passed") is True and not quality.get("failures")
                and not receipt.get("terminal") and not receipt.get("flag") and not receipt.get("inflight")):
            outcome = classify_page_outcomes([page_outcome(index, {**receipt, "svg_content": slide.svg_content})])[0]
            if outcome.classification != PASSED or outcome.verified is not True or outcome.flag is not None:
                raise ValueError("svg_header_revision_authority_pages_invalid")
            quality.update(classification=outcome.classification, verified=outcome.verified)
        attributes = {name: getattr(slide, name) for name in (
            "id", "owner_id", "index", "presentation", "source_format", "speaker_note", "properties") if hasattr(slide, name)}
        result.append(SimpleNamespace(**attributes, svg_content=slide.svg_content, content=content))
    return result


def header_revision_review_slides(authority, presentation, slides):
    if not _header_authority_current(authority, presentation, slides):
        raise ValueError("svg_header_revision_authority_invalid")
    return _classified_header_slides(slides)


def header_revision_source_parent(authority, presentation, slides):
    if not _header_authority_current(authority, presentation, slides):
        raise ValueError("svg_header_revision_authority_invalid")
    from services.generation_core.quality.advisory_record import content_digest
    # This is only the closed generation's identity, without its old QA,
    # snapshot, gate, or any claim to have evaluated the edited revision.
    parent = dict(parentOperationId=authority.parent_operation_id, parentJobId=authority.parent_job_id,
        sourceGenerationRevision=authority.generation_revision, sourceRevision=authority.generation_revision)
    return {**parent, "digest": content_digest(parent)}


async def require_current_header_revision_authority(session, presentation, slides, *, publication_operation_id=None):
    """Authorize bytes from a completed header edit; this grants no visual verdict."""
    from types import SimpleNamespace
    from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
    from models.sql.public_admission import PublicOperationAdmission
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from services.workspace_svg_header_design_operation_handler import (
        _admitted_design, _base_digest, _request, validate_header_command)
    from services.workspace_svg_header_retry import header_operation_request_hash, require_header_retry_lineage
    from services.svg_common_header_design_plan import require_header_design_plan, validate_header_design_plan
    from services.svg_common_header_design_checkpoint import _candidate_key
    from services.svg_export_page_evidence import _common_header_current, _geometry_ok
    from services.svg_export_visual_review import page_measurement_current
    from services.svg_common_header import validate_common_header_outline, header_snapshot_base
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    from utils.hashing import canonical_json_digest

    statement = select(WorkspaceOperation).where(
        WorkspaceOperation.presentation_id == presentation.id,
        WorkspaceOperation.owner_id == presentation.owner_id,
        WorkspaceOperation.kind == "design.apply", WorkspaceOperation.state == "succeeded",
        WorkspaceOperation.result_revision == presentation.revision)
    if publication_operation_id is not None:
        statement = statement.where(WorkspaceOperation.id == publication_operation_id)
    operations = list((await session.scalars(statement)).all())
    if not operations:
        return None
    if len(operations) != 1:
        raise ValueError("svg_header_revision_authority_invalid")
    operation = operations[0]
    payload = operation.request_payload or {}
    if (payload.get("context") or {}).get("target") != "common_header":
        return None
    row = await session.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == operation.id))
    count = len(slides)
    if (operation.owner_id != get_current_owner_id() or operation.owner_scope != get_workspace_owner_scope()
            or type(operation.expected_revision) is not int or operation.expected_revision + 1 != presentation.revision
            or operation.result_revision != presentation.revision
            or operation.cancel_requested_at is not None or operation.completed_at is None or operation.attempt_token is not None
            or operation.fencing_token < 1
            or operation.contract_version != "public-operation/v2" or operation.execution_profile_version != "public-generation-export/v2"
            or row is None or row.operation_id != operation.id or row.owner_id != presentation.owner_id
            or row.tenant_id != get_current_tenant_id() or row.operation_kind != "generation" or row.state != "succeeded"
            or row.fence != operation.fencing_token or not row.policy_digest
            or payload.get("principal_tenant_id") != row.tenant_id
            or row.slide_count != count or presentation.n_slides != count or not count
            or (operation.retry_of_operation_id is None
                and (row.planned_request_limit != count + 3 or row.retry_request_limit != count + 3))
            or row.planned_request_limit > count + 3 or row.retry_request_limit > count + 3):
        raise ValueError("svg_header_revision_authority_invalid")
    await require_header_retry_lineage(session, operation, row)
    for limit, consumed, reserved in ((row.planned_request_limit, row.initial_request_count, 0),
            (row.retry_request_limit, row.retry_request_count, 0),
            (row.estimated_cost_limit_microunits, row.estimated_consumed_microunits, row.estimated_reserved_microunits),
            (row.actual_cost_limit_microunits, row.actual_consumed_microunits, row.actual_reserved_microunits)):
        if any(type(value) is not int or value < 0 for value in (limit, consumed, reserved)) or consumed + reserved > limit:
            raise ValueError("svg_header_revision_authority_admission_invalid")
    validate_header_command(SimpleNamespace(command_type=operation.kind,
        context=payload.get("context"), expected_revision=operation.expected_revision))
    hashed = {key: payload.get(key) for key in ("presentation_id", "command", "command_type", "context", "expected_revision", "asset_ids")}
    if (hashed["presentation_id"] != str(presentation.id) or hashed["command_type"] != operation.kind
            or hashed["expected_revision"] != operation.expected_revision
            or header_operation_request_hash(operation) != operation.request_hash):
        raise ValueError("svg_header_revision_authority_request_invalid")
    revision = payload.get("svg_header_revision") or {}
    plan = validate_header_design_plan(revision.get("plan"))
    envelope = operation.svg_header_design_checkpoint or {}
    document = envelope.get("document") or {}
    parent = document.get("parent_publication") or {}
    snapshot = ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot") or {}
    selection = snapshot.get("common_header_selection") or {}
    header = snapshot.get("common_header") or {}
    if (plan is None or revision.get("version") != "svg-common-header-revision/v2"
            or envelope.get("version") != "svg-common-header-design-checkpoint/v2"
            or envelope.get("identity") != {"owner_id": str(operation.id), "owner_type": "operation", "request_digest": operation.request_hash}
            or document.get("status") != "selected" or document.get("plan_digest") != plan["plan_digest"]
            or document.get("base_digest") != revision.get("base_digest")
            or document.get("selector_capability") != revision.get("selector_capability")
            or parent.get("revision") != operation.expected_revision
            or snapshot.get("common_header_design_plan") != plan
            or header.get("version") != "svg-common-header/v2"
            or document.get("selection") != selection or selection.get("plan_digest") != plan["plan_digest"]
            or document.get("selected_candidate_id") != header.get("candidate_id")
            or selection.get("selected_candidate_id") != header.get("candidate_id")
            or selection.get("selected_revision") != header.get("revision")
            or document.get("selection_input_digest") != selection.get("selection_input_digest")
            or snapshot.get("digest") != canonical_json_digest({key: value for key, value in snapshot.items() if key != "digest"})
            or revision.get("body_initial_limit") != count + 1 or revision.get("body_retry_limit") != count + 1):
        raise ValueError("svg_header_revision_authority_checkpoint_invalid")
    parent_rows = [SimpleNamespace(id=item["id"], svg_content=item["svg_content"], content=item["content"])
                   for item in parent.get("pages", [])]
    parent_deck = SimpleNamespace(id=presentation.id, revision=operation.expected_revision,
        title=presentation.title, instructions=presentation.instructions, n_slides=count,
        theme=parent.get("theme"), outlines=parent.get("outline"))
    if (len(parent_rows) != count or _base_digest(parent_deck, parent_rows) != revision.get("base_digest")
            or revision.get("presentation_metadata") != {"id": str(presentation.id), "title": presentation.title, "revision": operation.expected_revision}
            or _admitted_design(payload["context"]["common_header_design"], (parent_deck.theme or {}).get("svg", {}).get("styleSnapshot") or {}) != revision.get("design")):
        raise ValueError("svg_header_revision_authority_parent_invalid")
    def style_base(value):
        return {key: item for key, item in value.items()
                if key not in {"digest", "common_header", "common_header_selection", "common_header_design_plan"}}
    if style_base(snapshot) != style_base((parent_deck.theme or {}).get("svg", {}).get("styleSnapshot") or {}):
        raise ValueError("svg_header_revision_authority_style_invalid")
    selection_step = (document.get("steps") or {}).get(selection.get("provider_step")) or {}
    selection_result = (selection_step.get("response") or {}).get("result") or {}
    if (selection_step.get("status") != "succeeded"
            or selection_result.get("candidate_selection") != selection.get("candidate_selection")
            or selection_result.get("representative_visual_review") != selection.get("representative_visual_review")
            or (selection.get("representative_visual_review") or {}).get("passed") is not True
            or (operation.result or {}).get("selection_input_digest") != selection.get("selection_input_digest")
            or document.get("page_values_digest") != canonical_json_digest(document.get("page_values"))):
        raise ValueError("svg_header_revision_authority_selection_invalid")
    job = await session.get(PresentationGenerationJob, uuid.UUID(revision["source_job_id"]))
    producer = await session.get(WorkspaceOperation, job.workspace_operation_id) if job is not None else None
    producer_admission = (await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == producer.id))) if producer is not None else None
    if (job is None or producer is None or producer_admission is None or job.state != "published"
            or job.presentation_id != presentation.id or producer.presentation_id != presentation.id
            or producer.owner_id != presentation.owner_id or producer.owner_scope != operation.owner_scope
            or producer.kind != "presentation.generate" or producer.state != "succeeded"
            or type(producer.result_revision) is not int or producer.result_revision > operation.expected_revision
            or producer_admission.owner_id != presentation.owner_id or producer_admission.tenant_id != row.tenant_id):
        raise ValueError("svg_header_revision_authority_source_invalid")
    admitted_snapshot = (job.request_payload or {}).get("_internal_svg_style_snapshot")
    if admitted_snapshot != job.svg_style_snapshot:
        if admitted_snapshot != header_snapshot_base(job.svg_style_snapshot or {}):
            raise ValueError("svg_header_revision_authority_source_invalid")
        validate_common_header_outline(job.svg_style_snapshot, job.authored_outline)
    request = _request(job, parent_deck, revision["design"])
    request._svg_header_design_plan = plan
    require_header_design_plan(request)
    validate_common_header_outline(snapshot, presentation.outlines)
    outlines = presentation.outlines["slides"]
    if len(outlines) != count or [slide.index for slide in slides] != list(range(count)):
        raise ValueError("svg_header_revision_authority_pages_invalid")
    source_pages = {page.get("page_id"): page for page in (job.authored_outline or {}).get("slides", [])}
    for page in outlines:
        original = source_pages.get(page.get("page_id"))
        if original is None or any(page.get(key) != original.get(key) for key in ("content", "title", "title_source_digest")):
            raise ValueError("svg_header_revision_authority_source_invalid")
    final_pages = []
    classified = _classified_header_slides(slides)
    for index, (slide, outline) in enumerate(zip(slides, outlines, strict=True)):
        key = _candidate_key(index, {"candidate_revision": str(header["revision"]),
            "candidate_id": "final-" + header["candidate_id"], "page_id": outline["page_id"]})
        final = (envelope.get("candidate_pages") or {}).get(key) or {}
        receipt = (slide.content or {}).get("svgReceipt") or {}
        quality = receipt.get("quality") or {}
        generated = (receipt.get("system_furniture") or {}).get("generated_common_header") or {}
        if (str(slide.id) != str(parent_rows[index].id) or slide.owner_id != presentation.owner_id
                or slide.source_format != "svg" or final.get("svg_content") != slide.svg_content
                or not final or final.get("terminal") or final.get("inflight")
                or any(final.get(name) != receipt.get(name) for name in (
                    "input_digest", "system_furniture", "source_preservation", "png_sha256"))
                or generated.get("contract") != header or generated.get("page") != outline.get("common_header")
                or generated.get("selection") != selection or quality.get("passed") is not True or quality.get("failures")
                or classified[index].content["svgReceipt"]["quality"].get("verified") is not True or not page_measurement_current(slide)
                or not _geometry_ok(slide, quality) or not _common_header_current(slide, quality)):
            raise ValueError("svg_header_revision_authority_pages_invalid")
        from services.generation_core.drivers.svg.native_declarations import bound_declarations
        declaration, bound = bound_declarations(final, slide.svg_content)
        if declaration is not None and not bound:
            raise ValueError("svg_header_revision_authority_native_invalid")
        final_pages.append(final)
    canvas = parse_canvas_metadata(presentation_canvas_metadata(presentation.theme))
    receipts = _source_preservation_receipts(presentation, slides, canvas)
    sealed = await _sealed_whole_page_manifest(session, claim=SimpleNamespace(owner_id=operation.owner_id,
        owner_scope=operation.owner_scope), presentation=presentation, slides=slides, receipts=receipts)
    from services.generation_core.drivers.svg.publish import svg_slide_content
    from services.generation_core.drivers.svg.native_publish import canvas_point_per_px
    scale = canvas_point_per_px(presentation_canvas_metadata(presentation.theme))
    manifest = None
    if sealed is not None:
        from services.source_content.contracts import SourceContentManifest
        manifest = SourceContentManifest.model_validate(sealed["manifest"])
    for index, (slide, final) in enumerate(zip(slides, final_pages, strict=True)):
        source_objects = source_summary = None
        if (final.get("source_preservation") or {}).get("native_tables"):
            if manifest is None:
                raise ValueError("svg_header_revision_authority_source_invalid")
            from services.svg_source_native_tables import source_table_native_objects
            source_objects, source_summary = source_table_native_objects(slide.svg_content,
                final["source_preservation"], manifest, index, pt_per_px=scale)
        expected = svg_slide_content(final, role=(slide.content or {}).get("role"), source=outlines[index]["content"],
            pt_per_px=scale, source_native_objects=source_objects, source_native_summary=source_summary)
        expected["svgReceipt"]["source_digest"] = sha256_hex(slide.svg_content.encode("utf-8"))
        from services.svg_quality_recovery_review import saved_page_render_input
        expected_page = SimpleNamespace(svg_content=slide.svg_content, content=expected)
        if (saved_page_render_input(_classified_header_slides([expected_page])[0])
                != saved_page_render_input(_classified_header_slides([slide])[0])):
            raise ValueError("svg_header_revision_authority_pages_invalid")
        if (expected.get("nativeObjects") != (slide.content or {}).get("nativeObjects")
                or (expected.get("svgReceipt") or {}).get("nativeObjects") != ((slide.content or {}).get("svgReceipt") or {}).get("nativeObjects")
                or expected.get("svgAssetManifest") != (slide.content or {}).get("svgAssetManifest")
                or (slide.content or {}).get("source") != outlines[index]["content"]):
            raise ValueError("svg_header_revision_authority_native_invalid")
    scene = _header_scene_digest(presentation, slides)
    proof = dict(operationId=str(operation.id), requestHash=operation.request_hash,
        admissionId=str(row.id), policyDigest=row.policy_digest, fence=row.fence,
        checkpointDigest=canonical_json_digest(envelope), sceneDigest=scene)
    return _HeaderRevisionAuthority(str(operation.id), scene, canonical_json_digest(proof), _HEADER_REVISION_AUTHORITY_SEAL,
        str(producer.id), str(job.id), producer.result_revision)


async def require_header_revision_lineage_authority(session, presentation, slides, *, publication_operations):
    """Check consecutive header revisions against the next sealed parent scene."""
    from types import SimpleNamespace
    headers = [operation for operation in publication_operations if operation.kind == "design.apply"]
    if len({((operation.request_payload or {}).get("svg_header_revision") or {}).get("source_job_id")
            for operation in headers}) > 1:
        raise ValueError("svg_header_revision_authority_lineage_invalid")
    current = None
    for index, operation in enumerate(headers):
        if operation.result_revision == presentation.revision:
            current = await require_current_header_revision_authority(session, presentation, slides,
                publication_operation_id=operation.id)
            if current is None:
                raise ValueError("svg_header_revision_authority_invalid")
            continue
        if index + 1 == len(headers) or headers[index + 1].expected_revision != operation.result_revision:
            raise ValueError("svg_header_revision_authority_lineage_invalid")
        successor = headers[index + 1]
        frozen = ((successor.svg_header_design_checkpoint or {}).get("document") or {}).get("parent_publication") or {}
        if frozen.get("revision") != operation.result_revision:
            raise ValueError("svg_header_revision_authority_lineage_invalid")
        metadata = ((operation.request_payload or {}).get("svg_header_revision") or {}).get("presentation_metadata") or {}
        historical = SimpleNamespace(id=presentation.id, owner_id=presentation.owner_id,
            title=metadata.get("title"), instructions=presentation.instructions, revision=operation.result_revision,
            n_slides=len(frozen.get("pages", [])), theme=frozen.get("theme"), outlines=frozen.get("outline"))
        historical_slides = [SimpleNamespace(id=uuid.UUID(item["id"]), owner_id=presentation.owner_id,
            index=page_index, source_format="svg", svg_content=item["svg_content"], content=item["content"])
            for page_index, item in enumerate(frozen.get("pages", []))]
        authority = await require_current_header_revision_authority(session, historical, historical_slides,
            publication_operation_id=operation.id)
        if authority is None:
            raise ValueError("svg_header_revision_authority_lineage_invalid")
    return current


def svg_export_admission_snapshot(presentation, slides, *, header_revision_authority=None):
    """Bind an edited SVG revision to its published canonical page evidence."""
    from services.svg_contract import source_digest
    if _header_authority_current(header_revision_authority, presentation, slides):
        slides = header_revision_review_slides(header_revision_authority, presentation, slides)
    if (not slides or len(slides) != presentation.n_slides
            or [slide.index for slide in slides] != list(range(len(slides)))):
        raise ValueError("svg_export_source_snapshot_invalid")
    pages = []
    for slide in slides:
        content = slide.content or {}
        quality = (content.get("svgReceipt") or {}).get("quality") or {}
        if (slide.owner_id != presentation.owner_id or slide.source_format != "svg"
                or not slide.svg_content or quality.get("passed") is not True
                or quality.get("verified") is not True
                or quality.get("source_digest") != source_digest(slide.svg_content)):
            raise ValueError("svg_export_published_source_required")
        pages.append({"id": str(slide.id), "index": slide.index, "svg": slide.svg_content,
                      "content": content, "notes": slide.speaker_note or ""})
    _quality_recovery_export_gate(presentation, slides, header_revision_authority=header_revision_authority)
    return _digest({"presentationId": str(presentation.id), "ownerId": str(presentation.owner_id),
                    "revision": presentation.revision, "theme": presentation.theme, "pages": pages})


def _quality_recovery_export_gate(presentation, slides, *, header_revision_authority=None):
    if _header_authority_current(header_revision_authority, presentation, slides):
        return None
    from services.svg_authoring_profile import read_quality_recovery_policy
    meta = (presentation.theme or {}).get("svg", {})
    snapshot = meta.get("styleSnapshot") or {}
    policy = read_quality_recovery_policy(snapshot)
    if policy is None:
        return None
    from services.generation_core.drivers.svg.quality import _current_gate
    from services.generation_core.quality.advisory_record import content_digest
    from uuid import UUID
    payloads = [({**((slide.content or {}).get("svgReceipt") or {}),
                  "svg_content": slide.svg_content}, None) for slide in slides]
    gate = _current_gate(meta.get("advisory"), payloads)
    publication = meta.get("qualityRecoveryPublication")
    if not isinstance(publication, dict):
        raise ValueError("svg_quality_recovery_export_review_required")
    body = {key: value for key, value in publication.items() if key != "digest"}
    try:
        UUID(publication.get("parentOperationId", ""))
        UUID(publication.get("parentJobId", ""))
        if publication.get("schema") == "svg-quality-recovery-publication/v2":
            UUID(publication.get("reviewOperationId", ""))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("svg_quality_recovery_export_parent_invalid") from error
    if (gate is None or gate.get("status") != "passed" or gate.get("policy") != policy
            or gate.get("pageCoverageComplete") is not True
            or gate.get("snapshotDigest") != content_digest(snapshot)
            or publication.get("schema") not in {"svg-quality-recovery-publication/v1", "svg-quality-recovery-publication/v2"}
            or publication.get("digest") != content_digest(body)
            or publication.get("gateDigest") != gate.get("digest")
            or publication.get("sourceRevision") != presentation.revision
            or publication.get("snapshotDigest") != content_digest(snapshot)
            or publication.get("sourceContentDigests") != [content_digest((slide.content or {}).get("source")) for slide in slides]
            or publication.get("inputDigests") != [payload.get("input_digest") for payload, _ in payloads]
            or (gate.get("binding") or {}).get("presentationId") != str(presentation.id)
            or (gate.get("binding") or {}).get("ownerId") != str(presentation.owner_id)
            or (gate.get("binding") or {}).get("operationId") != publication.get("reviewOperationId", publication["parentOperationId"])
            or (gate.get("binding") or {}).get("jobId") != publication["parentJobId"]):
        raise ValueError("svg_quality_recovery_export_review_required")
    return publication


async def require_quality_recovery_export_parent(session, presentation, slides, *, header_revision_authority=None):
    """The closed generation remains the parent; export has its own admission."""
    publication = _quality_recovery_export_gate(presentation, slides, header_revision_authority=header_revision_authority)
    if publication is None:
        return
    await require_quality_recovery_source_parent(session, presentation, publication)
    if publication.get("schema") == "svg-quality-recovery-publication/v2":
        from uuid import UUID
        from models.sql.workspace import WorkspaceOperation
        from models.sql.public_admission import PublicOperationAdmission
        from utils.owner_context import get_current_tenant_id
        review = await session.get(WorkspaceOperation, UUID(publication["reviewOperationId"]))
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == UUID(publication["reviewOperationId"])))
        context = ((review.request_payload or {}).get("context") or {}) if review is not None else {}
        if (review is None or admission is None or review.state != "succeeded"
                or review.kind != "quality.review" or review.owner_id != presentation.owner_id
                or review.presentation_id != presentation.id or review.owner_scope != publication.get("ownerScope")
                or review.expected_revision != publication["sourceRevision"]
                or review.result_revision != publication["sourceRevision"] or review.max_attempts != 1
                or admission.owner_id != presentation.owner_id or admission.tenant_id != str(get_current_tenant_id())
                or admission.planned_request_limit != 1 or admission.retry_request_limit != 0
                or context.get("mode", "analyze_only") != "analyze_only"
                or context.get("target", "source_svg") != "source_svg"):
            raise ValueError("svg_quality_recovery_export_review_operation_invalid")


async def require_quality_recovery_source_parent(session, presentation, publication, *, header_revision_authority=None, slides=None):
    """Validate immutable generation lineage even when an authorized edit advanced the deck."""
    from uuid import UUID
    from models.sql.workspace import WorkspaceOperation
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.public_admission import PublicOperationAdmission
    from utils.owner_context import get_current_tenant_id
    if (slides is not None and _header_authority_current(header_revision_authority, presentation, slides)):
        authority = header_revision_authority
        if (publication.get("parentOperationId") != authority.parent_operation_id
                or publication.get("parentJobId") != authority.parent_job_id
                or publication.get("sourceGenerationRevision", publication.get("sourceRevision")) != authority.generation_revision):
            raise ValueError("svg_quality_recovery_export_parent_invalid")
        return
    parent = await session.get(WorkspaceOperation, UUID(publication["parentOperationId"]))
    job = await session.get(PresentationGenerationJob, UUID(publication["parentJobId"]))
    admission = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == UUID(publication["parentOperationId"])))
    admitted_snapshot = (job.request_payload or {}).get("_internal_svg_style_snapshot") if job is not None else None
    snapshot_matches = job is not None and admitted_snapshot == job.svg_style_snapshot
    if (not snapshot_matches and job is not None and isinstance(admitted_snapshot, dict)
            and "common_header" not in admitted_snapshot and isinstance(job.svg_style_snapshot, dict)
            and isinstance(job.svg_style_snapshot.get("common_header"), dict)):
        from services.svg_common_header import header_snapshot_base, validate_common_header_outline

        if admitted_snapshot == header_snapshot_base(job.svg_style_snapshot):
            try:
                validate_common_header_outline(job.svg_style_snapshot, job.authored_outline)
            except (ValueError, KeyError, TypeError):
                pass
            else:
                snapshot_matches = job.authored_outline == presentation.outlines
    if (parent is None or job is None or admission is None or parent.state != "succeeded"
            or parent.kind != "presentation.generate" or job.state != "published"
            or parent.owner_id != presentation.owner_id or admission.owner_id != presentation.owner_id
            or admission.tenant_id != str(get_current_tenant_id())
            or parent.presentation_id != presentation.id or job.presentation_id != presentation.id
            or job.workspace_operation_id != parent.id
            or parent.result_revision != publication.get("sourceGenerationRevision", publication["sourceRevision"])
            or type(parent.result_revision) is not int or parent.result_revision > presentation.revision
            or job.svg_style_snapshot != presentation.theme["svg"]["styleSnapshot"]
            or not snapshot_matches):
        raise ValueError("svg_quality_recovery_export_parent_invalid")


def _require_deadline_budget(grant):
    """Fail fast once the pinned admission deadline has no worker budget left.

    Checked before and between the expensive preparation phases (asset
    derivatives, bundle persist, worker cache staging), so an operation whose
    deadline already passed stops in milliseconds instead of spending minutes
    preparing inputs for a worker grant that can no longer be issued.  The
    floor and the error code are the grant's own
    (``public_export_admission_deadline_exhausted``).
    """
    if grant is not None:
        grant.export_worker_seconds()


def _native_object_entries(slides):
    """Each slide's NativeObjects sidecar as a bundle entry; ``None`` when no
    slide has one (identity digests then stay what they were without them).
    An invalid stored sidecar does not fail the export: its objects are
    recorded as fallbacks by the worker's export scene."""
    from services.generation_core.drivers.svg.export_native_wire import bundle_entry
    entries = [bundle_entry(slide.content) for slide in slides]
    return entries if any(entry is not None for entry in entries) else None


async def render_and_export_svgs(claim, *, tenant_id, svgs, notes, export_format, canvas, session_factory,
                                 asset_bindings=None, source_preservation=None, deadline_grant=None,
                                 font_embedding=False, native_objects=None, slide_refs=None,
                                 source_manifest=None):
    """Run the svg worker; ``slide_refs`` are pre-persisted slide svg checkpoints
    (``services.svg_export_identity``), otherwise the sources persist ephemeral."""
    if (claim.owner_id is None or claim.kind != f"export.{export_format}"
            or not 1 <= len(svgs) <= 100 or len(notes) != len(svgs)):
        raise PublicExportOperationError("svg_export_input_invalid")
    if asset_bindings is not None and len(asset_bindings) != len(svgs):
        raise PublicExportOperationError("svg_export_asset_count_invalid")
    if source_preservation is not None and len(source_preservation) != len(svgs):
        raise PublicExportOperationError("svg_source_export_receipt_count_invalid")
    if source_preservation is not None and export_format != "pptx":
        raise PublicExportOperationError("svg_source_export_format_unsupported")
    if font_embedding and export_format != "pptx":
        raise PublicExportOperationError("svg_font_embedding_pptx_only")
    if native_objects is not None and len(native_objects) != len(svgs):
        raise PublicExportOperationError("svg_export_native_objects_count_invalid")
    if slide_refs is not None and (len(slide_refs) != len(svgs) or any(
            ref.kind != "slide-svg" or ref.sha256 != sha256_hex(source.encode())
            or ref.owner_id != claim.owner_id or ref.tenant_id != tenant_id
            for ref, source in zip(slide_refs, svgs, strict=True))):
        raise PublicExportOperationError("svg_export_slide_checkpoint_binding_invalid")
    resolved_canvas = parse_canvas_metadata(canvas)
    canvas = resolved_canvas.model_dump(mode="json", by_alias=True)
    for index, source in enumerate(svgs):
        if asset_bindings is None or asset_bindings[index] is None:
            validate_svg(source,width=resolved_canvas.width_px,height=resolved_canvas.height_px)
    grant = deadline_grant or await load_public_export_deadline_grant(
        session_factory, operation_id=claim.operation_id)
    _require_deadline_budget(grant)
    service = configured_public_artifact_service()
    async with session_factory() as session:
        refs = []
        asset_refs = []
        assets = []
        for index, source in enumerate(svgs):
            _require_deadline_budget(grant)
            binding = asset_bindings[index] if asset_bindings is not None else None
            if binding is not None:
                from services.svg_export_assets import prepare_export_assets
                receipt_args = ({"source_preservation": source_preservation[index]}
                                if source_preservation is not None else {})
                wire, bound = await prepare_export_assets(binding, svg=source, service=service,
                    session=session, claim=claim, tenant_id=tenant_id, export_format=export_format,
                    width=resolved_canvas.width_px, height=resolved_canvas.height_px, **receipt_args)
                assets.append(wire)
                asset_refs.extend(bound)
            else:
                assets.append(None)
            refs.append(slide_refs[index] if slide_refs is not None else await service.persist_bytes(
                session, owner_id=claim.owner_id, tenant_id=tenant_id, kind="slide-svg",
                payload=source.encode(), media_type="image/svg+xml",
                created_by_operation_id=claim.operation_id, retention_class="ephemeral"))
        payload = build_svg_export_bundle(
            claim=claim, slide_refs=[_worker_ref(ref).to_wire() for ref in refs],
            source_digests=[ref.sha256 for ref in refs], notes=notes, canvas=canvas,
            export_format=export_format, font_embedding=font_embedding, assets=assets,
            source_preservation=source_preservation, native_objects=native_objects,
            source_manifest=source_manifest)
        bundle = await service.persist_bytes(
            session, owner_id=claim.owner_id, tenant_id=tenant_id, kind="export-bundle",
            payload=json.dumps(payload).encode(), media_type="application/vnd.presenton.export-bundle+json",
            created_by_operation_id=claim.operation_id, retention_class="ephemeral")
        await session.commit()
    # The worker receives only digest-verified transport bytes, never database
    # credentials or an assumption that the authority is a local filesystem.
    _require_deadline_budget(grant)
    cache = configured_worker_artifact_cache()
    await cache.maintain()
    # One eviction pass for the whole staging batch instead of one full cache
    # walk per object (services/worker_artifact_cache_maintenance.py).
    async with cache.maintenance_batch():
        for reference in [*refs, *asset_refs, bundle]:
            _require_deadline_budget(grant)
            await service.stream_ref_to_object_store(reference, cache)
    exported = await execute_isolated_export_operation(
        claim, input_artifact=_worker_ref(bundle),
        deadline_seconds=grant.export_worker_seconds() if grant else None)
    if not exported.ok:
        raise PublicExportOperationError(exported.error_code or "svg_export_worker_failed")
    ref = _public_worker_output(dict(exported.output_artifact or {}), retention_class="published")
    if (ref.owner_id != claim.owner_id or ref.tenant_id != tenant_id
            or ref.created_by_operation_id != claim.operation_id or ref.kind != f"presentation-{export_format}"):
        raise PublicExportOperationError("svg_export_output_binding_invalid")
    async with session_factory() as session:
        registered = await service.register_ref(session, ref)
        await session.commit()
    return PublicWorkerArtifactResult(artifact=registered, resource_metrics={
        "role": exported.role, **exported.resource_metrics,
        "public_export_worker_evidence": dict(exported.execution_evidence)})

async def svg_source_review_context(session, presentation, slides=None):
    svg_meta = (presentation.theme or {}).get("svg", {})
    source_review = dict(snapshot=svg_meta.get('styleSnapshot'), publication=svg_meta.get('qualityRecoveryPublication'), producer=None)
    publication = source_review['publication']
    if isinstance(publication, Mapping) and publication.get("schema") == "svg-quality-recovery-publication/v2":
        from models.sql.workspace import WorkspaceOperation
        from models.sql.public_admission import PublicOperationAdmission
        from utils.owner_context import get_current_tenant_id
        if slides is None:
            slides = list(await session.scalars(select(SlideModel).where(
                SlideModel.presentation == presentation.id).order_by(SlideModel.index, SlideModel.id)))
        authority = await require_current_header_revision_authority(session, presentation, slides)
        if authority is not None:
            slides = header_revision_review_slides(authority, presentation, slides)
        # Validate the newly evaluated gate itself, independently of the
        # header authority that permits artifact creation before visual QA.
        _quality_recovery_export_gate(presentation, slides)
        await require_quality_recovery_source_parent(session, presentation, publication,
            header_revision_authority=authority, slides=slides)
        review = await session.get(WorkspaceOperation, uuid.UUID(publication["reviewOperationId"]))
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == uuid.UUID(publication["reviewOperationId"])))
        # Timeline deliberately defers operation payloads with raiseload. Read
        # this one owned, admitted source-review payload without lazy loading.
        review_payload = await session.scalar(select(WorkspaceOperation.request_payload).join(
            PublicOperationAdmission, PublicOperationAdmission.operation_id == WorkspaceOperation.id).where(
                WorkspaceOperation.id == uuid.UUID(publication["reviewOperationId"]),
                WorkspaceOperation.owner_id == presentation.owner_id,
                WorkspaceOperation.presentation_id == presentation.id,
                WorkspaceOperation.kind == "quality.review", WorkspaceOperation.state == "succeeded",
                PublicOperationAdmission.owner_id == presentation.owner_id,
                PublicOperationAdmission.tenant_id == str(get_current_tenant_id()),
                PublicOperationAdmission.operation_kind == "generation", PublicOperationAdmission.state == "succeeded"))
        context = (review_payload or {}).get("context") or {}
        if (review is None or admission is None or admission.operation_id != review.id
                or review.state != "succeeded" or review.kind != "quality.review"
                or review.owner_id != presentation.owner_id or review.presentation_id != presentation.id
                or review.owner_scope != publication.get("ownerScope")
                or review.expected_revision != presentation.revision or review.result_revision != presentation.revision
                or review.max_attempts != 1 or admission.owner_id != presentation.owner_id
                or admission.tenant_id != str(get_current_tenant_id()) or admission.operation_kind != "generation"
                or admission.state != "succeeded" or admission.planned_request_limit != 1 or admission.retry_request_limit != 0
                or (review_payload or {}).get("principal_tenant_id") != admission.tenant_id
                or context.get("mode", "analyze_only") != "analyze_only" or context.get("target", "source_svg") != "source_svg"):
            raise ValueError("svg_quality_recovery_export_review_operation_invalid")
        source_review["currentTenantId"] = str(get_current_tenant_id())
        source_review["producer"] = dict(operationId=str(review.id), presentationId=str(review.presentation_id),
            ownerId=str(review.owner_id), ownerScope=str(review.owner_scope), tenantId=admission.tenant_id,
            sourceRevision=review.result_revision, expectedRevision=review.expected_revision,
            maxAttempts=review.max_attempts, mode=context.get("mode", "analyze_only"), target=context.get("target", "source_svg"),
            plannedRequestLimit=admission.planned_request_limit, retryRequestLimit=admission.retry_request_limit,
            state=review.state, kind=review.kind, publicationDigest=publication["digest"],
            parentOperationId=publication["parentOperationId"], parentJobId=publication["parentJobId"],
            parentSourceRevision=publication["sourceGenerationRevision"])
        return source_review
    if isinstance(publication, Mapping):
        try:
            producer_id = uuid.UUID(str(publication.get('parentOperationId')))
        except (ValueError, TypeError):
            producer_id = None
        if producer_id is not None:
            from models.sql.workspace import WorkspaceOperation
            producer = await session.get(WorkspaceOperation, producer_id)
            if producer is not None:
                source_review['producer'] = dict(operationId=str(producer.id), presentationId=str(producer.presentation_id), ownerId=str(producer.owner_id), ownerScope=str(producer.owner_scope), sourceRevision=producer.result_revision, state=producer.state, kind=producer.kind)
    return source_review


async def current_svg_artifact_fidelity(session, presentation, review_operation):
    from copy import deepcopy
    from sqlalchemy import select
    from models.sql.public_artifact import PublicArtifact
    from models.sql.workspace import WorkspaceArtifact, WorkspaceOperation
    from models.sql.slide import SlideModel
    from services.svg_export_visual_review import svg_visual_review
    from services.svg_final_pptx_review import final_pptx_visual_domain
    from utils.hashing import sha256_hex
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    meta = (presentation.theme or {}).get("svg") or {}
    reviews = meta.get("finalPptxReviews") or {}
    entries = [entry for entry in reviews.values() if isinstance(entry, dict) and entry.get("operationId") == str(review_operation.id)]
    if not entries:
        return None
    review = entries[0]
    view = dict(artifactId=review.get("artifactId"), artifactDigest=review.get("artifactDigest"),
                sourceRevision=review.get("sourceRevision"), exportOperationId=review.get("parentExportOperationId"),
                reviewOperationId=str(review_operation.id), reviewDigest=review.get("digest"))

    def unavailable(code):
        return dict(view, status="failed", reasonCode=code)

    if (len(entries) != 1 or review_operation.kind != "quality.review" or review_operation.state != "succeeded"
            or review_operation.owner_id != presentation.owner_id or get_current_owner_id() != presentation.owner_id
            or review_operation.presentation_id != presentation.id or review_operation.result_revision != presentation.revision
            or review.get("sourceRevision") != presentation.revision or review.get("ownerId") != str(presentation.owner_id)
            or review.get("ownerScope") != str(review_operation.owner_scope)):
        return unavailable("svg_final_pptx_visual_review_owner_stale")
    try:
        public_id = uuid.UUID(str(review.get("artifactId")))
        export_id = uuid.UUID(str(review.get("parentExportOperationId")))
    except (ValueError, TypeError):
        return unavailable("svg_final_pptx_artifact_binding_invalid")
    from models.svg_quality_review import SvgQualityReviewRequest
    request_payload = await session.scalar(select(WorkspaceOperation.request_payload).where(
        WorkspaceOperation.id == review_operation.id, WorkspaceOperation.owner_id == presentation.owner_id))
    try:
        request = SvgQualityReviewRequest.model_validate((request_payload or {}).get("context") or {})
    except ValueError:
        return unavailable("svg_final_pptx_visual_review_stale")
    if request.target != "final_pptx" or request.artifact_id != public_id or request.scope != "all":
        return unavailable("svg_final_pptx_visual_review_stale")
    if (request_payload or {}).get("principal_tenant_id") != get_current_tenant_id():
        return unavailable("svg_final_pptx_visual_review_owner_stale")
    public = await session.get(PublicArtifact, public_id)
    binding = await session.scalar(select(WorkspaceArtifact).where(WorkspaceArtifact.public_artifact_id == public_id, WorkspaceArtifact.presentation_id == presentation.id, WorkspaceArtifact.owner_id == presentation.owner_id, WorkspaceArtifact.source_revision == presentation.revision, WorkspaceArtifact.kind == 'export.pptx'))
    if (public is None or binding is None or public.kind != "presentation-pptx"
            or public.owner_id != presentation.owner_id or public.tenant_id != get_current_tenant_id()
            or binding.public_artifact_id != public_id or binding.presentation_id != presentation.id
            or binding.owner_id != presentation.owner_id or binding.source_revision != presentation.revision
            or binding.operation_id != export_id or review.get("artifactDigest") != "sha256:" + public.sha256
            or review.get("tenantId") != public.tenant_id):
        return unavailable("svg_final_pptx_artifact_binding_invalid")
    export = await session.get(WorkspaceOperation, export_id)
    if (export is None or export.kind != "export.pptx" or export.state != "succeeded"
            or export.owner_id != presentation.owner_id or export.presentation_id != presentation.id
            or export.expected_revision != presentation.revision or export.result_revision != presentation.revision
            or str(export.owner_scope) != str(review_operation.owner_scope)):
        return unavailable("svg_final_pptx_export_not_succeeded")
    export_payload = await session.scalar(select(WorkspaceOperation.request_payload).where(
        WorkspaceOperation.id == export.id, WorkspaceOperation.owner_id == presentation.owner_id))
    if (export_payload or {}).get("principal_tenant_id") != get_current_tenant_id():
        return unavailable("svg_final_pptx_visual_review_owner_stale")
    slides = list((await session.scalars(select(SlideModel).where(SlideModel.presentation == presentation.id).order_by(SlideModel.index))).all())
    try:
        header_authority = await require_current_header_revision_authority(session, presentation, slides)
        svg_export_admission_snapshot(presentation, slides, header_revision_authority=header_authority)
    except ValueError as error:
        code = str(error)
        allowed = {"svg_export_published_source_required", "svg_export_source_snapshot_invalid",
                   "svg_quality_recovery_export_parent_invalid", "svg_quality_recovery_export_review_required",
                   "svg_authoring_profile_invalid", "svg_authoring_profile_snapshot_digest_mismatch", "svg_quality_recovery_profile_invalid"}
        return unavailable(code if code in allowed else "svg_export_source_snapshot_invalid")
    if header_authority is not None:
        slides = header_revision_review_slides(header_authority, presentation, slides)
    identity = dict(presentationId=str(presentation.id), ownerId=str(presentation.owner_id),
                    ownerScope=str(review_operation.owner_scope), sourceRevision=presentation.revision)
    source_context = await svg_source_review_context(session, presentation, slides)
    source = svg_visual_review(slides=slides, advisory=meta.get("advisory"),
                               expected_binding=dict(identity, _generationSourceReview=source_context))
    metrics = deepcopy(export.result or {})
    metrics["final_pptx_visual_review"] = deepcopy(review)
    domain = final_pptx_visual_domain(source, worker_metrics=metrics, artifact_digest=review["artifactDigest"],
                                     operation_id=str(export.id), presentation_id=str(presentation.id),
                                     source_revision=presentation.revision,
                                     source_digests=[sha256_hex((slide.svg_content or "").encode()) for slide in slides],
                                     expected_binding=dict(identity, tenantId=public.tenant_id))
    return dict(view, status=domain["status"], reasonCode=domain.get("reasonCode"))


async def execute_svg_workspace_export(context, *, presentation, admission, session_factory):
    from services.workspace_operation_handler_registry import (
        NonRetryableWorkspaceOperationError,
        WorkspaceOperationResult,
    )
    from services.presentation_export_attempt_service import (
        begin_export_attempt,
        terminalize_export_attempt_failed_if_current_workspace_claim,
    )
    from services.workspace_artifact_publication_service import (
        prepare_workspace_export_artifact_ref,
        publish_workspace_export_artifact,
    )
    claim = context.claim
    fmt = claim.kind.removeprefix("export.")
    if admission is None or admission.state != "active" or admission.owner_id != claim.owner_id:
        raise NonRetryableWorkspaceOperationError("svg_export_active_admission_required")
    if presentation.owner_id != claim.owner_id:
        raise NonRetryableWorkspaceOperationError("presentation_not_found")
    if presentation.revision != claim.expected_revision:
        raise NonRetryableWorkspaceOperationError("stale_presentation_revision")
    command_context = claim.request_payload.get("context") or {}
    quality_policy = command_context.get("quality_policy") or claim.request_payload.get("quality_policy")
    if quality_policy not in (None, "interactive-v1", "release-v1"):
        raise NonRetryableWorkspaceOperationError("svg_quality_policy_invalid")
    font_embedding = bool(
        command_context.get("font_embedding") is True
        or claim.request_payload.get("font_embedding") is True
    )
    if font_embedding and fmt != "pptx":
        raise NonRetryableWorkspaceOperationError("svg_font_embedding_pptx_only")
    async with session_factory() as session:
        slides = (await session.execute(
            select(SlideModel).where(SlideModel.presentation == claim.presentation_id)
            .order_by(SlideModel.index, SlideModel.id))).scalars().all()
    if not slides or any(slide.source_format != "svg" or not slide.svg_content for slide in slides):
        raise NonRetryableWorkspaceOperationError("svg_export_source_required")
    if any(slide.owner_id != claim.owner_id for slide in slides):
        raise NonRetryableWorkspaceOperationError("presentation_not_found")
    if (len(slides) != presentation.n_slides
            or [slide.index for slide in slides] != list(range(len(slides)))):
        raise NonRetryableWorkspaceOperationError("svg_export_incomplete_presentation")
    try:
        async with session_factory() as session:
            header_authority = await require_current_header_revision_authority(session, presentation, slides)
            await require_quality_recovery_export_parent(session, presentation, slides,
                header_revision_authority=header_authority)
    except ValueError as error:
        raise NonRetryableWorkspaceOperationError(str(error)) from error
    if header_authority is not None:
        slides = header_revision_review_slides(header_authority, presentation, slides)
    canvas = presentation_canvas_metadata(presentation.theme)
    revision_binding = claim.request_payload.get("svg_source_revision_binding")
    if revision_binding is not None:
        try:
            snapshot = svg_export_admission_snapshot(presentation, slides, header_revision_authority=header_authority)
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(str(error)) from error
        if (not isinstance(revision_binding, dict)
                or revision_binding.get("sourceRevision") != claim.expected_revision
                or revision_binding.get("snapshotSha256") != snapshot):
            raise NonRetryableWorkspaceOperationError("svg_export_source_snapshot_changed")
    measured_slides = slides
    try:
        measured_source_preservation = _source_preservation_receipts(
            presentation, slides, parse_canvas_metadata(canvas))
        source_manifest = None
        if measured_source_preservation is not None:
            async with session_factory() as session:
                source_manifest = await _sealed_whole_page_manifest(
                    session, claim=claim, presentation=presentation, slides=slides,
                    receipts=measured_source_preservation)
    except PublicExportOperationError as error:
        raise NonRetryableWorkspaceOperationError(str(error)) from error
    if fmt == "pptx" and measured_source_preservation is not None:
        from services.svg_source_export_projection import project_cached_source_tables
        async with session_factory() as session:
            slides = await project_cached_source_tables(session, presentation=presentation, slides=slides,
                receipts=measured_source_preservation, canvas=parse_canvas_metadata(canvas), owner_id=claim.owner_id)
    svgs = [slide.svg_content for slide in slides]
    notes = [slide.speaker_note or "" for slide in slides]
    source_digest = _digest({"svgs": svgs, "notes": notes})
    source_assets = [(slide.content or {}).get("svgAssetManifest") for slide in slides]
    source_preservation = ([(slide.content or {})["svgReceipt"]["source_preservation"] for slide in slides]
                           if measured_source_preservation is not None else None)
    if any(asset is not None for asset in source_assets):
        source_digest = _digest({"svgs": svgs, "notes": notes, "assets": source_assets})
    if source_preservation is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest,
            "sourcePreservation": source_preservation})
    if source_manifest is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest,
            "sealedSourceManifest": source_manifest})
    native_objects = _native_object_entries(slides) if fmt == "pptx" else None
    if native_objects is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest, "nativeObjects": native_objects})
    provenance = {
        "schema_version": 1,
        "adapter_chain": ["general"],
        "capability_evidence": {"selected_adapter": "general", "execution_profile": "svg-native",
                                "source_format": "svg", "visual_quality_verified": False},
        "fallback_reason": None,
        "asset_digests": {},
        "compiler_runtime": {"compiler": "slide-master", "compiler_version": CONVERTER_VERSION,
                             "runtime": "public-export-worker", "runtime_version": SVG_EXPORT_SCHEMA},
        "freshness": {"source_revision": claim.expected_revision, "source_snapshot_sha256": source_digest,
                      "asset_digests_match": True, "package_digest_match": False},
    }
    if any(asset is not None for asset in source_assets):
        provenance["compiler_runtime"]["runtime_version"] = SVG_EXPORT_SCHEMA_V2
    if source_preservation is not None:
        provenance["compiler_runtime"]["runtime_version"] = (
            SVG_EXPORT_SCHEMA_V4 if source_manifest is not None else SVG_EXPORT_SCHEMA_V3)
        provenance["capability_evidence"]["source_preservation"] = True
    async with session_factory() as session:
        from models.sql.workspace import WorkspaceOperation
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        attempt = await begin_export_attempt(
            session, presentation_id=claim.presentation_id, owner_id=claim.owner_id,
            owner_scope=claim.owner_scope, workspace_operation_id=claim.operation_id, export_format=fmt,
            source_revision=claim.expected_revision, source_snapshot_sha256=source_digest,
            request_hash=operation.request_hash, idempotency_key_hash=operation.idempotency_key_hash,
            requested_adapter="general", provenance=provenance)
    try:
        # Before any preparation: an expired admission must not spend minutes
        # deriving assets and staging a bundle on every retry.
        grant = await load_public_export_deadline_grant(session_factory, operation_id=claim.operation_id)
        _require_deadline_budget(grant)
        await context.progress(stage="export_rendering", percent=10)
        svg_meta = ((presentation.theme or {}).get("svg") or {})
        advisory = svg_meta.get("advisory")
        async with session_factory() as producer_session:
            source_review = await svg_source_review_context(producer_session, presentation, slides)

        async def run_worker(slide_refs):
            asset_bindings = None
            if any(asset is not None for asset in source_assets):
                from services.svg_export_assets import build_slide_export_assets
                service = configured_public_artifact_service()
                asset_bindings = []
                async with session_factory() as session:
                    for slide in slides:
                        await context.raise_if_cancelled()
                        _require_deadline_budget(grant)
                        asset_bindings.append(await build_slide_export_assets(
                            slide, service=service, session=session, claim=claim, tenant_id=admission.tenant_id,
                            export_format=fmt, canvas=canvas))
                    await session.commit()
            return await render_and_export_svgs(
                claim, tenant_id=admission.tenant_id, svgs=svgs, notes=notes, export_format=fmt, canvas=canvas,
                session_factory=session_factory, asset_bindings=asset_bindings,
                source_preservation=source_preservation, deadline_grant=grant, font_embedding=font_embedding,
                native_objects=native_objects, slide_refs=slide_refs,
                source_manifest=source_manifest)

        # One export per identity: a completed identity is reused without the
        # worker or asset derivation; only a claimed one runs ``run_worker``.
        from services.svg_export_identity import SvgExportRequest, export_svg_through_identity
        result, identity = await export_svg_through_identity(claim, tenant_id=admission.tenant_id,
            request=SvgExportRequest(
                export_format=fmt, svgs=svgs, notes=notes,
                canvas=parse_canvas_metadata(canvas).model_dump(mode="json", by_alias=True), slides=slides,
                source_assets=source_assets if any(asset is not None for asset in source_assets) else None,
                source_preservation=source_preservation, native_objects=native_objects,
                source_manifest=source_manifest,
                font_embedding=font_embedding, advisory=advisory, quality_policy=quality_policy),
            session_factory=session_factory, grant=grant, execute=run_worker)
        ref = result.artifact
        final_reviews = ((presentation.theme or {}).get("svg") or {}).get("finalPptxReviews") or {}
        if isinstance(final_reviews, dict) and isinstance(final_reviews.get(ref.sha256), dict):
            result.resource_metrics["final_pptx_visual_review"] = final_reviews[ref.sha256]
        receipt_args = dict(
            quality_receipt_id=f"quality:{claim.operation_id}:{ref.artifact_id}",
            operation_id=str(claim.operation_id), presentation_id=str(claim.presentation_id),
            artifact_digest=f"sha256:{ref.sha256}", source_revision=claim.expected_revision,
            scene_digest=f"sha256:{source_digest}", evaluated_at=datetime.now(timezone.utc))
        from services.svg_export_quality import svg_public_quality_receipt
        receipt = svg_public_quality_receipt(slides=measured_slides, advisory=advisory, source_preservation=source_preservation,
            source_preservation_parents=measured_source_preservation,
            worker_metrics=result.resource_metrics, exporter_version=CONVERTER_VERSION,
            require_native_coverage=fmt == "pptx",
            export_identity_digest=identity.canonical_digest, export_tenant_id=admission.tenant_id,
            review_binding=dict(presentationId=str(claim.presentation_id), ownerId=str(claim.owner_id),
                                ownerScope=str(claim.owner_scope), sourceRevision=claim.expected_revision,
                                _generationSourceReview=source_review),
            **receipt_args)
        # release-v1 stays a receipt-content gate; the identity join decides
        # only whether bytes are produced or reused, not whether they release.
        if quality_policy == "release-v1":
            from services.svg_export_release_gate import require_release_v1_receipt
            require_release_v1_receipt(receipt)
        # Post-export: the receipt's own digest is the publication quality
        # digest. ``evidenceDigests`` are the pre-export identity inputs.
        quality_digest = _digest(receipt)
        # Pre-export evidence stays in the immutable identity checkpoint. The
        # public receipt is a strict QualityReceiptV3; adding identity fields
        # here makes status projection discard the artifact-bound receipt.
        provenance["freshness"].update({"package_sha256": ref.sha256, "package_digest_match": True})
        await context.raise_if_cancelled()
        async with session_factory() as session:
            artifact = await prepare_workspace_export_artifact_ref(
                session, reference=ref, operation_id=claim.operation_id, presentation_id=claim.presentation_id,
                source_revision=claim.expected_revision, kind=claim.kind, export_attempt_id=attempt.id)
            revision = await publish_workspace_export_artifact(
                session, artifact=artifact, attempt_token=claim.attempt_token, fencing_token=claim.fencing_token,
                export_format=fmt, export_source_snapshot_sha256=source_digest, export_selected_adapter="general",
                export_provenance=provenance, worker_resource_metrics=result.resource_metrics,
                public_artifact_id=ref.artifact_id, public_tenant_id=admission.tenant_id,
                public_attempt_number=claim.attempt_number, public_quality_digest=quality_digest,
                public_quality_receipt=receipt)
        return WorkspaceOperationResult(
            payload={"status": "completed", "format": fmt, **result.resource_metrics}, result_revision=revision)
    except BaseException as error:
        try:
            async with session_factory() as session:
                await terminalize_export_attempt_failed_if_current_workspace_claim(
                    session,
                    attempt_id=attempt.id,
                    expected_owner_id=claim.owner_id,
                    expected_owner_scope=claim.owner_scope,
                    expected_workspace_operation_id=claim.operation_id,
                    expected_attempt_token=claim.attempt_token,
                    expected_fencing_token=claim.fencing_token,
                    failure_code=(getattr(error, "code", None) or str(error) or "presentation_export_failed"),
                    failure_path=getattr(error, "path", None),
                    selected_adapter="general",
                )
        except Exception:
            LOGGER.exception(
                "Could not terminalize SVG export attempt %s for operation %s",
                attempt.id,
                claim.operation_id,
            )
        raise
