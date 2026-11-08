"""SVG export admission using the existing fenced worker and artifact authority."""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
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
from services.svg_export_bundle import (ExportIoCounters, build_svg_export_bundle, digest as _digest,
                                        digest_embedding, encode_sealed_manifest_async)
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


def _verified_apart_from_design_review(slide):
    """The page's classification without a saved review's design verdict.

    The header authority proves the header operation's own accepted bytes; a
    later review's verdict on them is a page flag, not a revocation.
    """
    from services.svg_page_publication import REVIEW_FLAG_KEY, classify_svg_page_quality, without_design_reasons
    receipt = (slide.content or {}).get("svgReceipt") or {}
    quality = receipt.get("quality") or {}
    if receipt.get(REVIEW_FLAG_KEY) is None:
        return quality.get("verified") is True
    classified = classify_svg_page_quality(svg_content=slide.svg_content or "", quality=quality,
        index=getattr(slide, "index", 0) or 0, png_path=receipt.get("png_path"), receipt=receipt,
        review_flag=without_design_reasons(receipt[REVIEW_FLAG_KEY]))
    return classified.get("verified") is True


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
        from services.workspace_svg_header_body_remeasurement import require_body_remeasurement_witness
        require_body_remeasurement_witness(final, owner=operation, index=index, parent=parent)
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
                or not _verified_apart_from_design_review(classified[index]) or not page_measurement_current(slide)
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
    from services.generation_core.drivers.svg.native_requirements import (
        REQUIREMENTS_VERSION_V4, requirements_revision,
    )
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
            pt_per_px=scale, source_native_objects=source_objects, source_native_summary=source_summary,
            **({"revision": REQUIREMENTS_VERSION_V4}
               if requirements_revision(snapshot) == REQUIREMENTS_VERSION_V4 else {}))
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


def svg_export_admission_snapshot(presentation, slides, *, header_revision_authority=None, require_passed=False):
    """Bind an edited SVG revision to its published canonical page evidence.

    Every page's measurement must describe its exported bytes (integrity). A
    flagged page exports interactively -- the receipt reports it not passed --
    and only ``require_passed`` (release-v1) still refuses one.
    """
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
                or not slide.svg_content
                or require_passed and (quality.get("passed") is not True or quality.get("verified") is not True)
                or quality.get("source_digest") != source_digest(slide.svg_content)):
            raise ValueError("svg_export_published_source_required")
        pages.append({"id": str(slide.id), "index": slide.index, "svg": slide.svg_content,
                      "content": content, "notes": slide.speaker_note or ""})
    _quality_recovery_export_gate(presentation, slides, header_revision_authority=header_revision_authority,
                                  require_passed=require_passed)
    return _digest({"presentationId": str(presentation.id), "ownerId": str(presentation.owner_id),
                    "revision": presentation.revision, "theme": presentation.theme, "pages": pages})


def _quality_recovery_export_gate(presentation, slides, *, header_revision_authority=None, require_passed=False):
    """The deck's current design-review publication, or None when none binds it.

    Integrity always refuses: a publication record that is malformed or does
    not match its own digest, claims a revision the deck never had, or (at the
    deck's current revision) is not bound to a current, intact gate for exactly
    these pages, snapshot, owner, job and operation. What the review concluded
    is a page flag: an interactive export proceeds whatever the gate status,
    and with no current review at all (no publication, or one a later edit
    superseded); its receipt reports the visual review not passed.
    ``require_passed`` (release-v1) still requires a current, passed, fully
    covered review.
    """
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
    publication = meta.get("qualityRecoveryPublication")
    if publication is None and not require_passed:
        return None
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
    revision = publication.get("sourceRevision")
    if (publication.get("schema") not in {"svg-quality-recovery-publication/v1", "svg-quality-recovery-publication/v2"}
            or publication.get("digest") != content_digest(body)
            or type(revision) is not int or revision > presentation.revision):
        raise ValueError("svg_quality_recovery_export_review_required")
    if revision < presentation.revision:
        # An edit published after this review: the record is history, and
        # nothing current reviewed the deck being exported.
        if require_passed:
            raise ValueError("svg_quality_recovery_export_review_required")
        return None
    payloads = [({**((slide.content or {}).get("svgReceipt") or {}),
                  "svg_content": slide.svg_content}, None) for slide in slides]
    gate = _current_gate(meta.get("advisory"), payloads)
    if (gate is None or gate.get("policy") != policy
            or gate.get("snapshotDigest") != content_digest(snapshot)
            or publication.get("snapshotDigest") != content_digest(snapshot)
            or publication.get("gateDigest") != gate.get("digest")
            or publication.get("sourceContentDigests") != [content_digest((slide.content or {}).get("source")) for slide in slides]
            or publication.get("inputDigests") != [payload.get("input_digest") for payload, _ in payloads]
            or (gate.get("binding") or {}).get("presentationId") != str(presentation.id)
            or (gate.get("binding") or {}).get("ownerId") != str(presentation.owner_id)
            or (gate.get("binding") or {}).get("operationId") != publication.get("reviewOperationId", publication["parentOperationId"])
            or (gate.get("binding") or {}).get("jobId") != publication["parentJobId"]):
        raise ValueError("svg_quality_recovery_export_review_required")
    if require_passed and (gate.get("status") != "passed" or gate.get("pageCoverageComplete") is not True):
        raise ValueError("svg_quality_recovery_export_review_required")
    return publication


async def require_quality_recovery_export_parent(session, presentation, slides, *, header_revision_authority=None,
                                                 require_passed=False):
    """The closed generation remains the parent; export has its own admission."""
    publication = _quality_recovery_export_gate(presentation, slides, header_revision_authority=header_revision_authority,
                                                require_passed=require_passed)
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


def _export_source_digest(svgs, notes, source_assets, source_preservation, source_manifest, native_objects,
                          manifest_encoding=None):
    """The export's ``source_snapshot_sha256``; the asset-less base omits ``assets``."""
    base = {"svgs": svgs, "notes": notes}
    if any(asset is not None for asset in source_assets):
        base["assets"] = source_assets
    source_digest = _digest(base)
    if source_preservation is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest,
            "sourcePreservation": source_preservation})
    if source_manifest is not None:
        source_digest = digest_embedding({"svgSourceSnapshot": source_digest},
            "sealedSourceManifest", source_manifest, manifest_encoding)
    if native_objects is not None:
        source_digest = _digest({"svgSourceSnapshot": source_digest, "nativeObjects": native_objects})
    return source_digest


def _native_object_entries(slides, *, chart_paint_fallbacks=None):
    """Each slide's NativeObjects sidecar as a bundle entry; ``None`` when no
    slide has one (identity digests then stay what they were without them).
    An invalid stored sidecar does not fail the export: its objects are
    recorded as fallbacks by the worker's export scene. ``chart_paint_fallbacks``
    (v5 decks only, one list per slide) names charts that stay drawn because
    their post-publication paint evidence is missing, stale or failed."""
    from services.generation_core.drivers.svg.export_native_wire import bundle_entry
    entries = [bundle_entry(slide.content, **({"chart_paint_fallbacks": chart_paint_fallbacks[index]}
                                              if chart_paint_fallbacks and chart_paint_fallbacks[index] else {}))
               for index, slide in enumerate(slides)]
    return entries if any(entry is not None for entry in entries) else None


class _StageClock:
    """Private export timing. ``lap`` records sequential, non-overlapping top-level
    segments; ``add`` accumulates spans nested inside a top-level segment, which are
    inclusive (never sum them with the top-level values). Never raises."""

    __slots__ = ("_last", "top", "inner", "counts")

    def __init__(self):
        self._last = time.perf_counter()
        self.top = {}
        self.inner = {}
        self.counts = {}

    def lap(self, name):
        try:
            now = time.perf_counter()
            self.top[name] = self.top.get(name, 0.0) + (now - self._last)
            self._last = now
        except Exception:  # pragma: no cover - timing must never alter export behaviour
            pass

    def add(self, name, started):
        try:
            self.inner[name] = self.inner.get(name, 0.0) + (time.perf_counter() - started)
            self.counts[name] = self.counts.get(name, 0) + 1
        except Exception:  # pragma: no cover
            pass

    def log(self, operation_id, outcome):
        try:
            LOGGER.info(
                "svg_export_stage_seconds operation=%s outcome=%s top_level_sequential=%s "
                "nested_inclusive=%s calls=%s",
                operation_id, outcome,
                {key: round(value, 3) for key, value in self.top.items()},
                {key: round(value, 3) for key, value in self.inner.items()},
                dict(self.counts))
        except Exception:  # pragma: no cover
            pass


async def render_and_export_svgs(claim, *, tenant_id, svgs, notes, export_format, canvas, session_factory,
                                 asset_bindings=None, source_preservation=None, deadline_grant=None,
                                 font_embedding=False, native_objects=None, slide_refs=None,
                                 source_manifest=None, source_manifest_encoding=None, io_memo=None,
                                 _stages=None, font_manifest=None, system_furniture=None):
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
    from services.svg_export_assets import ExportIoMemo
    _t_prepare = time.perf_counter()
    async with session_factory() as session:
        refs = []
        asset_refs = []
        assets = []
        # Verified manifest reads are shared with the build phase (one operation); consumer refs
        # registered below belong to this transaction only, hence a fresh dict body.
        asset_memo = io_memo.for_session() if io_memo is not None else ExportIoMemo(ExportIoCounters())
        for index, source in enumerate(svgs):
            _require_deadline_budget(grant)
            binding = asset_bindings[index] if asset_bindings is not None else None
            if binding is not None:
                from services.svg_export_assets import prepare_export_assets
                receipt_args = ({"source_preservation": source_preservation[index]}
                                if source_preservation is not None else {})
                wire, bound = await prepare_export_assets(binding, svg=source, service=service,
                    session=session, claim=claim, tenant_id=tenant_id, export_format=export_format,
                    width=resolved_canvas.width_px, height=resolved_canvas.height_px, memo=asset_memo,
                    **receipt_args)
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
            source_manifest=source_manifest, source_manifest_encoding=source_manifest_encoding,
            font_manifest=font_manifest, system_furniture=system_furniture)
        bundle = await service.persist_bytes(
            session, owner_id=claim.owner_id, tenant_id=tenant_id, kind="export-bundle",
            payload=json.dumps(payload).encode(), media_type="application/vnd.presenton.export-bundle+json",
            created_by_operation_id=claim.operation_id, retention_class="ephemeral")
        await session.commit()
    if _stages is not None:
        _stages.add("render_prepare_persist_bundle_incl", _t_prepare)
    if asset_memo.counters is not None:
        asset_memo.counters.log(LOGGER, "prepare", claim.operation_id)
    # The worker receives only digest-verified transport bytes, never database
    # credentials or an assumption that the authority is a local filesystem.
    _require_deadline_budget(grant)
    _t_stage = time.perf_counter()
    cache = configured_worker_artifact_cache()
    await cache.maintain()
    # One eviction pass for the whole staging batch instead of one full cache
    # walk per object (services/worker_artifact_cache_maintenance.py).
    async with cache.maintenance_batch():
        # The deck-level source manifest consumer ref is shared by every slide; stage each exact ref once.
        for reference in dict.fromkeys([*refs, *asset_refs, bundle]):
            _require_deadline_budget(grant)
            await service.stream_ref_to_object_store(reference, cache)
    if _stages is not None:
        _stages.add("worker_cache_staging", _t_stage)
    _t_worker = time.perf_counter()
    try:
        exported = await execute_isolated_export_operation(
            claim, input_artifact=_worker_ref(bundle),
            deadline_seconds=grant.export_worker_seconds() if grant else None)
    finally:
        # Includes launch, transport and output verification unless converter metrics separate them.
        if _stages is not None:
            _stages.add("worker_invocation_incl", _t_worker)
    if not exported.ok:
        raise PublicExportOperationError(exported.error_code or "svg_export_worker_failed")
    ref = _public_worker_output(dict(exported.output_artifact or {}), retention_class="published")
    if (ref.owner_id != claim.owner_id or ref.tenant_id != tenant_id
            or ref.created_by_operation_id != claim.operation_id or ref.kind != f"presentation-{export_format}"):
        raise PublicExportOperationError("svg_export_output_binding_invalid")
    _t_register = time.perf_counter()
    async with session_factory() as session:
        registered = await service.register_ref(session, ref)
        await session.commit()
    if _stages is not None:
        _stages.add("output_ref_register", _t_register)
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
        if _quality_recovery_export_gate(presentation, slides) is None:
            # Superseded by a later edit: no producer reviewed these bytes.
            return source_review
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


_FINAL_LOCK_TIMEOUT_MS = 10_000


async def _bound_final_lock_wait(session):
    """Bound the final admission/operation row-lock wait (PostgreSQL only)."""
    from sqlalchemy import text

    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text(f"SET LOCAL lock_timeout = '{_FINAL_LOCK_TIMEOUT_MS}ms'"))


async def _build_slide_asset_bindings(context, *, slides, service, session, tenant_id, export_format, canvas, grant,
                                      io_memo=None, _stages=None):
    """Share producer proof only inside this export preparation transaction.

    The long per-slide phase reads and writes only content-addressed artifacts;
    admission/operation rows are locked (and publication pointers promoted) only
    in the final section, so a cancel request or heartbeat never waits on it.
    """
    from models.sql.presentation import PresentationModel
    from services.artifact_store import _locked_current_operation
    from services.svg_asset_persistence import SvgPublicationContext, promote_deferred_publications_if_current
    from services.svg_document_mutations import _PublicationSourceVerification
    from services.svg_export_assets import build_slide_export_assets
    from utils.datetime_utils import as_utc, get_current_utc_datetime

    claim = context.claim
    def claim_binding():
        return (claim.operation_id, claim.presentation_id, claim.owner_id, claim.attempt_number,
                claim.attempt_token, claim.fencing_token, claim.expected_revision, claim.kind, tenant_id)
    expected_claim = claim_binding()
    presentation = await session.get(PresentationModel, claim.presentation_id, populate_existing=True)
    if (presentation is None or presentation.owner_id != claim.owner_id
            or presentation.revision != claim.expected_revision):
        raise ValueError("svg_export_source_snapshot_changed")
    verification = _PublicationSourceVerification(session, presentation, use_source_snapshot=True,
                                                   source_snapshot_slides=slides)

    async def verify_source(*args):
        _t_verify = time.perf_counter()
        try:
            if claim_binding() != expected_claim:
                raise ValueError("svg_export_asset_fence_lost")
            _require_deadline_budget(grant)
            return await verification.verify(*args)
        finally:
            # Nested inside the asset loop: inclusive, summed over callbacks.
            if _stages is not None:
                _stages.add("source_verify_wrapper_incl", _t_verify)

    bindings = []
    # Operation-scoped memo for verified source manifests, sealed assets and derivatives.
    # Verified manifest proofs are shared across the operation; per-slide entries belong to this transaction.
    export_memo = io_memo.for_session() if io_memo is not None else {}
    deferred_publications = []
    _t_loop = time.perf_counter()
    try:
        for slide in slides:
            await context.raise_if_cancelled()
            _require_deadline_budget(grant)
            bindings.append(await build_slide_export_assets(slide, service=service, session=session,
                claim=claim, tenant_id=tenant_id, export_format=export_format, canvas=canvas,
                _source_verifier=verify_source, _memo=export_memo, _deferred_publications=deferred_publications))
    finally:
        if _stages is not None:
            _stages.add("asset_loop_incl_source_verify", _t_loop)
    _t_revalidate = time.perf_counter()
    try:
        await verification.revalidate()
    finally:
        if _stages is not None:
            _stages.add("final_revalidate", _t_revalidate)
    await context.raise_if_cancelled()
    _require_deadline_budget(grant)
    if claim_binding() != expected_claim:
        raise ValueError("svg_export_asset_fence_lost")
    # Final section: the only admission -> operation row locks of this transaction.
    _t_final = time.perf_counter()
    await _bound_final_lock_wait(session)
    try:
        current = await _locked_current_operation(session, operation_id=claim.operation_id,
            owner_id=claim.owner_id, tenant_id=tenant_id, attempt_number=claim.attempt_number,
            attempt_token=claim.attempt_token, fencing_token=claim.fencing_token, operation_kind="export")
    except DBAPIError as error:
        await context.raise_if_cancelled()
        if (getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)) != "55P03":
            raise
        raise PublicExportOperationError("svg_export_asset_lock_timeout") from error
    if current is not None:
        await session.refresh(current)
        await session.refresh(presentation, attribute_names=["owner_id", "revision"])
    if (current is None or current.presentation_id != claim.presentation_id
            or current.expected_revision != claim.expected_revision
            or current.cancel_requested_at is not None or current.lease_expires_at is None
            or as_utc(current.lease_expires_at) <= get_current_utc_datetime()
            or presentation.owner_id != claim.owner_id or presentation.revision != claim.expected_revision):
        # A cancel committed during preparation surfaces as the runner's
        # cancellation, never as a retryable export failure.
        await context.raise_if_cancelled()
        raise ValueError("svg_export_asset_fence_lost")
    await promote_deferred_publications_if_current(session, SvgPublicationContext(
        operation_id=claim.operation_id, owner_id=claim.owner_id, tenant_id=tenant_id,
        attempt_number=claim.attempt_number, attempt_token=claim.attempt_token,
        fencing_token=claim.fencing_token, operation_kind="export", deferred_publications=deferred_publications))
    # One-time derived source snapshot for inherited sources this export proved the
    # legacy way; written under this export's live fence, never failing the export.
    await verification.bootstrap_source_snapshots(SvgPublicationContext(
        operation_id=claim.operation_id, owner_id=claim.owner_id, tenant_id=tenant_id,
        attempt_number=claim.attempt_number, attempt_token=claim.attempt_token,
        fencing_token=claim.fencing_token, operation_kind="export"))
    if _stages is not None:
        # Lock wait + checks + promotion only; excludes the caller's session.commit().
        _stages.add("final_section_pre_commit", _t_final)
    _require_deadline_budget(grant)
    return bindings


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
    stages = _StageClock()
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
    requested_embedding = (
        command_context.get("font_embedding") is True
        or claim.request_payload.get("font_embedding") is True
    )
    if requested_embedding and fmt != "pptx":
        raise NonRetryableWorkspaceOperationError("svg_font_embedding_pptx_only")
    # SVG text boxes are fixed-width and do not wrap, so a PowerPoint without
    # the bundled faces substitutes a wider system font and the text overflows.
    # Embedding (subset to the deck's glyphs) is therefore the PPTX default;
    # an explicit ``font_embedding: false`` still opts out.
    embedding_disabled = (
        command_context.get("font_embedding") is False
        or claim.request_payload.get("font_embedding") is False
    )
    font_embedding = fmt == "pptx" and (requested_embedding or not embedding_disabled)
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
    stages.lap("load_slides_validate")
    # Lineage authority, source preservation and the sealed manifest can keep the
    # loop busy before asset preparation too; widen the lease before them. A
    # missing or exhausted grant still fails at its own check below.
    try:
        early_lease_seconds = int((await load_public_export_deadline_grant(
            session_factory, operation_id=claim.operation_id)).export_worker_seconds())
    except PublicExportOperationError:
        early_lease_seconds = None
    if early_lease_seconds is not None:
        await context.extend_lease(seconds=early_lease_seconds)
    stages.lap("early_grant_lease")
    try:
        async with session_factory() as session:
            header_authority = await require_current_header_revision_authority(session, presentation, slides)
            await require_quality_recovery_export_parent(session, presentation, slides,
                header_revision_authority=header_authority, require_passed=quality_policy == "release-v1")
    except ValueError as error:
        raise NonRetryableWorkspaceOperationError(str(error)) from error
    stages.lap("preflight_authority_and_parent")
    if header_authority is not None:
        slides = header_revision_review_slides(header_authority, presentation, slides)
    canvas = presentation_canvas_metadata(presentation.theme)
    revision_binding = claim.request_payload.get("svg_source_revision_binding")
    if revision_binding is not None:
        try:
            snapshot = svg_export_admission_snapshot(presentation, slides, header_revision_authority=header_authority,
                                                     require_passed=quality_policy == "release-v1")
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
    stages.lap("snapshot_source_preservation_manifest")
    from services.svg_export_assets import ExportIoMemo
    io_counters = ExportIoCounters()
    io_memo = ExportIoMemo(io_counters)
    manifest_encoding = None
    if source_manifest is not None:
        # Deep-frozen value + canonical bytes + digest, made once; every later consumer in this
        # operation uses ``manifest_encoding.value`` so the digest cannot go stale.
        manifest_encoding = await encode_sealed_manifest_async(source_manifest, counters=io_counters)
        source_manifest = manifest_encoding.value
    if fmt == "pptx" and measured_source_preservation is not None:
        from services.svg_source_export_projection import project_cached_source_tables
        async with session_factory() as session:
            slides = await project_cached_source_tables(session, presentation=presentation, slides=slides,
                receipts=measured_source_preservation, canvas=parse_canvas_metadata(canvas), owner_id=claim.owner_id)
    stages.lap("manifest_encode_table_projection")
    svgs = [slide.svg_content for slide in slides]
    notes = [slide.speaker_note or "" for slide in slides]
    source_assets = [(slide.content or {}).get("svgAssetManifest") for slide in slides]
    # A failure placeholder carries no receipt (``None``); the bundle and the
    # worker export it as drawn (``_validate_source_preservation_entry``).
    source_preservation = ([((slide.content or {}).get("svgReceipt") or {}).get("source_preservation")
                            for slide in slides]
                           if measured_source_preservation is not None else None)
    chart_paint_fallbacks = None
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
    from services.svg_chart_paint_verification import deck_requirements_revision
    if fmt == "pptx" and deck_requirements_revision(presentation) == REQUIREMENTS_VERSION_V5:
        # svg-native-requirements/v5 only: the post-publication chart paint
        # verification. Every other revision skips it and its bundle is unchanged.
        from services.svg_chart_paint_verification import deck_chart_paint_fallbacks
        try:
            verification_grant = await load_public_export_deadline_grant(session_factory, operation_id=claim.operation_id)
        except PublicExportOperationError:
            verification_grant = None
        chart_paint_fallbacks = await deck_chart_paint_fallbacks(session_factory, presentation=presentation,
            slides=slides, remaining_seconds=verification_grant.remaining_seconds if verification_grant else None,
            between=context.raise_if_cancelled)
        stages.lap("chart_paint_verification")
    native_objects = (_native_object_entries(slides, **({"chart_paint_fallbacks": chart_paint_fallbacks}
                                                        if chart_paint_fallbacks is not None else {}))
                      if fmt == "pptx" else None)
    # Canonical JSON + sha256 over every page's source is pure CPU on plain data.
    source_digest = await asyncio.to_thread(_export_source_digest, svgs, notes, source_assets,
        source_preservation, source_manifest, native_objects, manifest_encoding)
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
    stages.lap("source_digest_begin_attempt")
    try:
        # Before any preparation: an expired admission must not spend minutes
        # deriving assets and staging a bundle on every retry.
        grant = await load_public_export_deadline_grant(session_factory, operation_id=claim.operation_id)
        _require_deadline_budget(grant)
        # Source verification of a deep recovery lineage keeps the event loop busy for
        # minutes; widen this attempt's lease to the admitted export window (as the
        # preview and analysis handlers do) so the heartbeat cannot lapse and cost the
        # attempt its fence at the end of preparation.
        await context.extend_lease(seconds=int(grant.export_worker_seconds()) if grant is not None else 1800)
        await context.progress(stage="export_rendering", percent=10)
        svg_meta = ((presentation.theme or {}).get("svg") or {})
        advisory = svg_meta.get("advisory")
        template_selection = (svg_meta.get("styleSnapshot") or {}).get("svg_template")
        font_manifest = None
        system_furniture = None
        if isinstance(template_selection, dict) and template_selection.get("mode") == "template-redesign":
            from services.svg_templates.selection_material import ensure_selection_material, template_package
            async with session_factory() as material_session:
                await ensure_selection_material(material_session, template_selection)
            package = template_package(template_selection)
            if not isinstance(package, dict):
                raise PublicExportOperationError("svg_template_font_manifest_missing")
            font_manifest = package.get("font_manifest")
            if font_manifest is None:
                raise PublicExportOperationError("svg_template_font_manifest_missing")
            if source_preservation is not None:
                raise PublicExportOperationError("svg_template_font_export_unsupported")
            from services.svg_templates.frame_attestation import template_furniture_receipt
            snapshot = svg_meta["styleSnapshot"]
            resolved = parse_canvas_metadata(canvas)
            system_furniture = []
            for index, (slide, source) in enumerate(zip(slides, svgs, strict=True)):
                receipt = (slide.content or {}).get("svgReceipt") or {}
                page = receipt.get("svg_template_page")
                expected = template_furniture_receipt(source, snapshot, width=resolved.width_px,
                    height=resolved.height_px, index=index, count=len(slides), page_context=page)
                if receipt.get("system_furniture") != expected:
                    raise PublicExportOperationError("svg_template_furniture_binding_mismatch")
                system_furniture.append(expected)
            if fmt == "pptx" and not font_embedding:
                explicitly_disabled = (command_context.get("font_embedding") is False
                    or claim.request_payload.get("font_embedding") is False)
                if explicitly_disabled:
                    raise PublicExportOperationError("svg_template_font_embedding_required")
                font_embedding = True
        async with session_factory() as producer_session:
            source_review = await svg_source_review_context(producer_session, presentation, slides)
        stages.lap("grant_lease_source_review")

        async def run_worker(slide_refs):
            asset_bindings = None
            if any(asset is not None for asset in source_assets):
                service = configured_public_artifact_service()
                _t_bindings = time.perf_counter()
                async with session_factory() as session:
                    try:
                        asset_bindings = await _build_slide_asset_bindings(context, slides=slides,
                            service=service, session=session, tenant_id=admission.tenant_id,
                            export_format=fmt, canvas=canvas, grant=grant, io_memo=io_memo, _stages=stages)
                        _t_commit = time.perf_counter()
                        await session.commit()
                        stages.add("bindings_commit", _t_commit)
                    finally:
                        stages.add("bindings_total_incl", _t_bindings)
                io_counters.log(LOGGER, "bindings", claim.operation_id)
            return await render_and_export_svgs(
                claim, tenant_id=admission.tenant_id, svgs=svgs, notes=notes, export_format=fmt, canvas=canvas,
                session_factory=session_factory, asset_bindings=asset_bindings,
                source_preservation=source_preservation, deadline_grant=grant, font_embedding=font_embedding,
                native_objects=native_objects, slide_refs=slide_refs,
                source_manifest=source_manifest, source_manifest_encoding=manifest_encoding,
                io_memo=io_memo, _stages=stages, font_manifest=font_manifest, system_furniture=system_furniture)

        # One export per identity: a completed identity is reused without the
        # worker or asset derivation; only a claimed one runs ``run_worker``.
        from services.svg_export_identity import SvgExportRequest, export_svg_through_identity
        result, identity = await export_svg_through_identity(claim, tenant_id=admission.tenant_id,
            request=SvgExportRequest(
                export_format=fmt, svgs=svgs, notes=notes,
                canvas=parse_canvas_metadata(canvas).model_dump(mode="json", by_alias=True), slides=slides,
                source_assets=source_assets if any(asset is not None for asset in source_assets) else None,
                source_preservation=source_preservation, native_objects=native_objects,
                source_manifest=source_manifest, source_manifest_encoding=manifest_encoding,
                font_embedding=font_embedding, font_manifest=font_manifest, system_furniture=system_furniture, advisory=advisory, quality_policy=quality_policy),
            session_factory=session_factory, grant=grant, execute=run_worker)
        # Inclusive of identity lookup/checkpoint persistence and run_worker (nested spans above).
        stages.lap("identity_export_incl_worker")
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
        stages.lap("quality_receipt_build")
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
        stages.lap("publish_artifact_sessions")
        stages.log(claim.operation_id, "completed")
        return WorkspaceOperationResult(
            payload={"status": "completed", "format": fmt, **result.resource_metrics}, result_revision=revision)
    except BaseException as error:
        stages.lap("failed_segment")
        stages.log(claim.operation_id, "failed")
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
