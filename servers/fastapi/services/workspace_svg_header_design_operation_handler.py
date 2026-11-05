"""Operation-owned free SVG header revisions with immutable candidate evidence."""
from __future__ import annotations

import asyncio
from copy import copy, deepcopy
from xml.etree import ElementTree as ET
import uuid

from sqlalchemy import select

from models.common_header_design import CommonHeaderDesignInput
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation
from services.database import async_session_maker
from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError, WorkspaceOperationResult
from utils.hashing import canonical_json_digest


def header_command(context):
    return isinstance(context, dict) and context.get("target") == "common_header"


def validate_header_command(request):
    context = request.context
    if request.command_type != "design.apply" or not header_command(context):
        raise ValueError("svg_common_header_operation_invalid")
    if set(context) - {"target", "common_header_design", "tenant_id"}:
        raise ValueError("svg_common_header_operation_context_invalid")
    if type(request.expected_revision) is not int or request.expected_revision < 0:
        raise ValueError("workspace_expected_revision_required")
    if not isinstance(context.get("common_header_design"), dict):
        raise ValueError("svg_common_header_design_required")
    return CommonHeaderDesignInput.model_validate(context["common_header_design"])


async def _rows(session, presentation_id):
    return list(await session.scalars(select(SlideModel).where(
        SlideModel.presentation == presentation_id).order_by(SlideModel.index, SlideModel.id)))


def _admitted_design(raw, snapshot):
    design = deepcopy(raw)
    reference_id = (snapshot.get("design_pack_template") or {}).get("provenance", {}).get("snapshot_id")
    if "reference_snapshot_ids" not in design and reference_id:
        design["reference_snapshot_ids"] = [reference_id]
    return CommonHeaderDesignInput.model_validate(design).model_dump(mode="json")


async def _source_job(session, presentation):
    job = await session.scalar(select(PresentationGenerationJob).join(WorkspaceOperation,
        WorkspaceOperation.id == PresentationGenerationJob.workspace_operation_id).where(
        PresentationGenerationJob.presentation_id == presentation.id,
        WorkspaceOperation.presentation_id == presentation.id, WorkspaceOperation.owner_id == presentation.owner_id,
        WorkspaceOperation.kind == "presentation.generate", WorkspaceOperation.state == "succeeded"
        ).order_by(WorkspaceOperation.result_revision.desc(), PresentationGenerationJob.created_at.desc()).limit(1))
    if job is None:
        raise ValueError("svg_common_header_source_inputs_required")
    return job


def _request(job, presentation, design):
    from services.presentation_generation_staging import _request_from_durable_payload

    request = _request_from_durable_payload(deepcopy(job.request_payload))
    request.common_header_mode = "uniform"
    request.common_header_design = CommonHeaderDesignInput.model_validate(design)
    request.n_slides = presentation.n_slides
    request.instructions = presentation.instructions
    request._svg_header_design_plan = None
    request._svg_style_snapshot = None
    from services.authored_canvas import presentation_canvas_metadata
    from services.document_canvas import parse_canvas_metadata, DocumentCanvasMetadata
    physical = parse_canvas_metadata(presentation_canvas_metadata(presentation.theme))
    request._authored_canvas = physical.canvas_contract()
    request._document_canvas = physical if isinstance(physical, DocumentCanvasMetadata) else None
    frozen = ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot") or {}
    request.common_header_cover_exception = bool((frozen.get("common_header") or {}).get("cover_exception", False))
    reference = frozen.get("design_pack_template")
    reference_id = (reference or {}).get("provenance", {}).get("snapshot_id")
    if reference_id:
        request.svg_design_reference_snapshot_id = uuid.UUID(reference_id)
        request._svg_uploaded_design_reference = deepcopy(reference)
    return request


async def require_header_runtime_admission(session, *, operation, admission,
                                         expected_attempt_token=None, expected_fencing_token=None):
    """Join the current operation, exact public request and its server-sealed budget."""
    from types import SimpleNamespace
    from services.public_operation_admission import _require_phase2_provider_contract
    from services.svg_common_header_design_plan import require_header_design_plan, validate_header_design_plan
    from services.workspace_svg_header_retry import header_operation_request_hash, require_header_retry_lineage
    from utils.datetime_utils import as_utc, get_current_utc_datetime
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    from models.sql.workspace import get_workspace_owner_scope

    now, row = get_current_utc_datetime(), admission
    if (row is None or operation is None or operation.kind != "design.apply" or row.operation_id != operation.id
            or operation.owner_id != row.owner_id or operation.owner_id != get_current_owner_id()
            or operation.owner_scope != get_workspace_owner_scope() or row.tenant_id != get_current_tenant_id()
            or row.operation_kind != "generation" or row.state != "active" or operation.state != "running"
            or operation.cancel_requested_at is not None
            or operation.contract_version != "public-operation/v2" or operation.execution_profile_version != "public-generation-export/v2"
            or operation.attempt_number < 1 or operation.attempt_number > operation.max_attempts
            or not operation.attempt_token or operation.lease_expires_at is None or as_utc(operation.lease_expires_at) <= now
            or operation.expected_revision is None or operation.deadline_at is None or row.deadline_at is None
            or as_utc(operation.deadline_at) != as_utc(row.deadline_at) or as_utc(operation.deadline_at) <= now
            or row.fence != operation.fencing_token or row.fence < 1 or not row.policy_digest
            or (expected_attempt_token is not None and operation.attempt_token != expected_attempt_token)
            or (expected_fencing_token is not None and operation.fencing_token != expected_fencing_token)):
        raise RuntimeError("svg_common_header_runtime_admission_invalid")
    payload = operation.request_payload
    validate_header_command(SimpleNamespace(command_type=operation.kind,
        context=payload.get("context"), expected_revision=operation.expected_revision))
    if payload.get("principal_tenant_id") != row.tenant_id:
        raise RuntimeError("svg_common_header_runtime_admission_invalid")
    hashed = {key: payload[key] for key in ("presentation_id", "command", "command_type", "context", "expected_revision", "asset_ids")}
    if (hashed["presentation_id"] != str(operation.presentation_id) or hashed["command_type"] != operation.kind
            or hashed["expected_revision"] != operation.expected_revision or header_operation_request_hash(operation) != operation.request_hash):
        raise RuntimeError("svg_common_header_runtime_request_conflict")
    try:
        retry = await require_header_retry_lineage(session, operation, row)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    revision = payload.get("svg_header_revision") or {}
    plan = validate_header_design_plan(revision.get("plan"))
    envelope = operation.svg_header_design_checkpoint or {}
    document = envelope.get("document") or {}
    if (plan is None or revision.get("version") != "svg-common-header-revision/v2"
            or envelope.get("version") != "svg-common-header-design-checkpoint/v2"
            or envelope.get("identity") != {"owner_id": str(operation.id), "owner_type": "operation", "request_digest": operation.request_hash}
            or document.get("plan_digest") != plan["plan_digest"] or document.get("base_digest") != revision.get("base_digest")
            or document.get("selector_capability") != revision.get("selector_capability")):
        raise RuntimeError("svg_common_header_runtime_checkpoint_conflict")
    count = row.slide_count
    if (type(count) is not int or count < 1 or revision.get("body_initial_limit") != count + 1
            or revision.get("body_retry_limit") != count + 1
            or (retry is None and (row.planned_request_limit != 2 + count + 1 or row.retry_request_limit != 2 + count + 1))
            or row.planned_request_limit > count + 3 or row.retry_request_limit > count + 3):
        raise RuntimeError("svg_common_header_runtime_budget_conflict")
    for limit, consumed, reserved in ((row.planned_request_limit, row.initial_request_count, 0),
            (row.retry_request_limit, row.retry_request_count, 0),
            (row.estimated_cost_limit_microunits, row.estimated_consumed_microunits, row.estimated_reserved_microunits),
            (row.actual_cost_limit_microunits, row.actual_consumed_microunits, row.actual_reserved_microunits)):
        if any(type(value) is not int or value < 0 for value in (limit, consumed, reserved)) or consumed + reserved > limit:
            raise RuntimeError("svg_common_header_runtime_budget_conflict")
    presentation = await session.get(PresentationModel, operation.presentation_id)
    if presentation is None or presentation.owner_id != operation.owner_id or presentation.revision != operation.expected_revision:
        raise RuntimeError("svg_common_header_runtime_source_conflict")
    trusted_metadata = {"id": str(presentation.id), "title": presentation.title, "revision": presentation.revision}
    if revision.get("presentation_metadata", trusted_metadata) != trusted_metadata:
        raise RuntimeError("svg_common_header_runtime_source_conflict")
    snapshot = ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot") or {}
    if _admitted_design(payload["context"]["common_header_design"], snapshot) != revision.get("design"):
        raise RuntimeError("svg_common_header_runtime_request_conflict")
    rows = await _rows(session, presentation.id)
    if len(rows) != count or _base_digest(presentation, rows) != revision["base_digest"]:
        raise RuntimeError("svg_common_header_runtime_source_conflict")
    job = await session.get(PresentationGenerationJob, uuid.UUID(revision["source_job_id"]))
    if job is None or job.presentation_id != presentation.id:
        raise RuntimeError("svg_common_header_runtime_source_conflict")
    request = _request(job, presentation, revision["design"])
    request._svg_header_design_plan = plan
    require_header_design_plan(request)
    await _require_phase2_provider_contract(session, row, expected_attempt_token=expected_attempt_token,
        expected_fencing_token=expected_fencing_token)
    return row


async def _seed_body(owner, ctx, request, outline, index, identity, body_payload, *, session_factory):
    from services.svg_common_header_design_checkpoint import candidate_page_checkpoint
    from services.svg_common_header_generation import _page_input_digest
    saved = await candidate_page_checkpoint(owner, index, identity=identity, session_maker=session_factory)
    if saved:
        return
    payload = {key: deepcopy(value) for key, value in body_payload.items()
               if key in {"author_body_svg", "svg_content", "nativeDeclarations", "nativeRequirements",
                          "source_preservation", "readability_policy", "rasterRenderAdvisories", "rasterContentLedger", "assetOffer",
                          "body_reuse_remeasurement"}}
    body = payload.get("author_body_svg") or payload.get("svg_content")
    composition = (payload.get("source_preservation") or {}).get("common_header_composition")
    if composition:
        body = composition["body_svg"]
        payload["source_preservation"] = deepcopy(composition["body_receipt"])
    if not isinstance(body, str) or not body:
        raise ValueError("svg_common_header_body_reuse_missing")
    payload.update(svg_content=body, author_body_svg=body,
        schema_version="svg-slide-checkpoint/v2" if ctx.canonical_assets else "svg-slide-checkpoint/v1", source_format="svg",
        input_digest=_page_input_digest(ctx, request, outline, request.language, index),
        attempts=[{"kind": "local", "receipt": {"version": "svg-common-header-body-reuse/v2",
            "parent_input_digest": body_payload.get("input_digest"),
            "parent_svg_digest": canonical_json_digest(body_payload.get("svg_content")),
            "body_digest": canonical_json_digest(body),
            **({"body_remeasurement_digest": payload["body_reuse_remeasurement"]["digest"]}
               if payload.get("body_reuse_remeasurement") else {})}}], body_reuse_parent=deepcopy(body_payload))
    await candidate_page_checkpoint(owner, index, identity=identity, payload=payload, session_maker=session_factory)


async def _design_revision(context, owner, request, outline, snapshot, rows, inputs, admitted, session_factory):
    from services.svg_common_header import header_snapshot_base
    from services.svg_common_header_design_checkpoint import candidate_page_checkpoint, read_update_header_design
    from services.svg_common_header_generation import (_design_input, _design_reference_images, _paid_step,
        _checked_candidates, _selection_input, _validated_selection, _snapshot, _representative_index,
        run_header_candidate_page)
    from services.svg_common_header_v2 import prepare_common_header_v2
    from services.svg_common_header_values import prepare_header_page_values
    from services.svg_presentation_checkpoint import _read_png
    from services.workspace_svg_header_revision_inputs import raw_body, reusable_body, merge_header_revision_values
    from services.workspace_svg_header_retry import require_retry_page_context

    plan, ctx = admitted["plan"], inputs["ctx"]
    ctx.header_page_values = prepare_header_page_values(request, outline,
        presentation=inputs["trusted_presentation"], source_manifest=ctx.source_document,
        expected_revision=context.claim.expected_revision)
    ctx.header_page_values, public_values, value_lineage = merge_header_revision_values(
        context.claim.request_payload["context"]["common_header_design"], outline, ctx.header_page_values,
        parent_revision=context.claim.expected_revision)
    inputs["header_design_public"] = {**deepcopy(admitted["design"]), **public_values}
    page_values_digest = canonical_json_digest(ctx.header_page_values)
    from services.svg_common_header_design_calls import preflight_header_selector
    if preflight_header_selector() != admitted["selector_capability"]:
        raise ValueError("svg_common_header_selector_model_changed")
    document = await read_update_header_design(owner, session_maker=session_factory)
    if not document:
        document = {"version": "svg-common-header-design-checkpoint/v2", "steps": {}, "status": "preparing",
            "plan_digest": plan["plan_digest"], "base_digest": admitted["base_digest"],
            "selector_capability": admitted["selector_capability"], "parent_publication": {
                "revision": context.claim.expected_revision, "theme": deepcopy(inputs["parent_theme"]),
                "outline": outline.model_dump(mode="json"), "pages": [{"id": str(row.id),
                    "svg_content": row.svg_content, "content": deepcopy(row.content), "properties": deepcopy(row.properties)} for row in rows]}}
    if document.get("plan_digest") != plan["plan_digest"] or document.get("base_digest") != admitted["base_digest"]:
        raise ValueError("svg_common_header_checkpoint_input_conflict")
    if document.get("page_values_digest", page_values_digest) != page_values_digest:
        raise ValueError("svg_common_header_checkpoint_input_conflict")
    document.update(page_values=deepcopy(ctx.header_page_values), page_values_digest=page_values_digest,
                    inherited_value_lineage=deepcopy(value_lineage))
    async def save():
        await context.raise_if_cancelled()
        await read_update_header_design(owner, payload=deepcopy(document), session_maker=session_factory)
    remeasured_bodies = {}

    async def reuse_body(page_index):
        row = rows[page_index]
        try:
            body, preservation = raw_body(row)
            return body, preservation, None
        except ValueError as error:
            if str(error) != "svg_common_header_body_reuse_unverified":
                raise
        if page_index not in remeasured_bodies:
            from services.workspace_svg_header_body_remeasurement import remeasure_header_body
            remeasured_bodies[page_index] = asyncio.create_task(remeasure_header_body(row, owner=owner,
                ctx=ctx, request=request, outline=outline, index=page_index,
                parent=document["parent_publication"], session_factory=session_factory,
                source_payload=inputs["reuse_payloads"].get(page_index)))
        return await remeasured_bodies[page_index]

    async def render_candidate(active, page_outline, page_index, identity):
        saved = await candidate_page_checkpoint(owner, page_index, identity=identity, session_maker=session_factory)
        from services.workspace_svg_header_body_remeasurement import require_body_remeasurement_witness
        require_body_remeasurement_witness(saved, owner=owner, index=page_index, parent=document["parent_publication"])
        if saved.get("body_reuse_remeasurement"):
            from services.workspace_svg_header_body_remeasurement import render_remeasured_body_candidate
            return await render_remeasured_body_candidate(active, request, page_outline, owner, page_index,
                identity=identity, saved=saved, parent=document["parent_publication"],
                session_factory=session_factory)
        return await run_header_candidate_page(active, request, context.claim.presentation_id,
            page_outline, request.language, owner, page_index, identity, async_session_maker=session_factory)
    index = document.setdefault("representative_index", _representative_index(ctx, outline, request.common_header_cover_exception, None, plan))
    base = header_snapshot_base(ctx.snapshot)
    base = {**base, "common_header_design_plan": plan}
    candidate_contexts, defects = {}, []
    await save()
    for revision in (1, 2):
        design_input = _design_input(ctx, request, outline, plan, index, defects or None)
        design_input.update(candidate_revision=revision, modification_target="common_header",
            existing_header=snapshot.get("common_header"),
            reuse_policy={"preserve_body_box_and_shared_style_to_reuse_existing_body": True,
                         "existing_body_box": (snapshot.get("common_header") or {}).get("body_box"),
                         "existing_shared_style": (snapshot.get("common_header") or {}).get("shared_style", {})})
        images = _design_reference_images(ctx, index) if request.common_header_design.reference_snapshot_ids else []
        response = await _paid_step(document, key=f"design-{revision}", stage="design", ordinal=revision,
            payload=design_input, images=images, plan=plan, job=owner, save=save)
        try:
            candidates = _checked_candidates(response, plan, revision)
        except (ValueError, ET.ParseError) as error:
            defects = [{"error": str(error)}]
            if revision == 2:
                raise
            continue
        defects = []
        for candidate in candidates:
            try:
                contract, bindings = prepare_common_header_v2(candidate, outline, ctx.canvas, snapshot=base,
                    cover_exception=request.common_header_cover_exception, page_values=ctx.header_page_values)
                candidate_ctx = copy(ctx)
                candidate_ctx.snapshot = _snapshot(base, contract)
                candidate_ctx.header_pages = bindings
                candidate_contexts[candidate["candidate_id"]] = candidate_ctx
            except (ValueError, ET.ParseError) as error:
                defects.append({"candidate_id": candidate["candidate_id"], "error": str(error)})
        if candidate_contexts:
            break
        if revision == 2:
            raise ValueError("svg_common_header_candidates_ineligible")
    async def run(candidate):
        candidate_id = candidate["candidate_id"]
        active = candidate_contexts.get(candidate_id)
        if active is None:
            return candidate_id, {"eligible": False, "error": next(item["error"] for item in defects
                if item["candidate_id"] == candidate_id)}
        identity = {"candidate_revision": str(revision), "candidate_id": candidate_id,
                    "page_id": outline.slides[index].page_id}
        saved = await candidate_page_checkpoint(owner, index, identity=identity, session_maker=session_factory)
        require_retry_page_context(owner, active, request, outline, index, identity, saved)
        if not saved and reusable_body(snapshot.get("common_header"), active.snapshot["common_header"], rows[index]):
            prior = deepcopy(inputs["reuse_payloads"].get(index) or {})
            body, preservation, remeasurement = await reuse_body(index)
            prior.update(svg_content=body, author_body_svg=body, source_preservation=preservation)
            if remeasurement:
                prior["body_reuse_remeasurement"] = remeasurement
                for name, source_name in (("nativeDeclarations", "source_native_declarations"),
                                          ("nativeRequirements", "source_native_requirements")):
                    if remeasurement.get(source_name) is not None:
                        prior[name] = deepcopy(remeasurement[source_name])
            if not (rows[index].content or {}).get("nativeObjects") or prior.get("nativeDeclarations") or ctx.source_reflow:
                await _seed_body(owner, active, request, outline, index, identity, prior, session_factory=session_factory)
        try:
            payload, _ = await render_candidate(active, outline, index, identity)
            return candidate_id, {"eligible": payload.get("quality", {}).get("passed") is True,
                "payload": payload, "header_digest": active.snapshot["common_header"]["digest"]}
        except Exception as error:
            saved = await candidate_page_checkpoint(owner, index, identity=identity, session_maker=session_factory)
            code = str(getattr(error, "code", error))
            if (saved.get("inflight") or not saved.get("attempts")
                    or any(marker in code.lower() for marker in ("unknown", "uncertain", "fence_lost", "lease_lost", "authority_expired"))):
                raise
            return candidate_id, {"eligible": False, "payload": saved, "error": code,
                "header_digest": active.snapshot["common_header"]["digest"]}
    results = await asyncio.gather(*(run(candidate) for candidate in candidates), return_exceptions=True)
    pages = {}
    for result in results:
        if isinstance(result, BaseException):
            raise result
        pages[result[0]] = result[1]
    if not any(page.get("eligible") for page in pages.values()):
        raise ValueError("svg_common_header_representative_pages_ineligible")
    selection_input, images = _selection_input(ctx, request, outline, plan, document, candidates, pages, index, _read_png)
    await save()
    for ordinal in (1, 2):
        response = await _paid_step(document, key=f"selection-{ordinal}", stage="selection", ordinal=ordinal,
            payload=selection_input, images=images, plan=plan, job=owner, save=save)
        try:
            selected_id, selection = _validated_selection(response, selection_input, document["display_mapping"], pages, candidates)
            if selected_id is None:
                raise ValueError("svg_common_header_all_candidates_unsuitable")
            break
        except ValueError as error:
            if ordinal == 2:
                raise
            selection_input = {**selection_input, "known_selection_defect": str(error)}
            selection_input["selection_input_digest"] = canonical_json_digest({key: value for key, value in selection_input.items()
                if key != "selection_input_digest"})
    active = candidate_contexts[selected_id]
    metadata = {"version": "svg-common-header-selection/v2", "selection_input_digest": selection_input["selection_input_digest"],
        "plan_digest": plan["plan_digest"], "selected_candidate_id": selected_id, "selected_revision": revision,
        "representative_page_id": outline.slides[index].page_id, "display_mapping": document["display_mapping"],
        "candidate_selection": selection["candidate_selection"], "representative_visual_review": selection["representative_visual_review"],
        "provider_step": f"selection-{ordinal}"}
    active.snapshot["common_header_selection"] = metadata
    active.snapshot["digest"] = canonical_json_digest({key: value for key, value in active.snapshot.items() if key != "digest"})
    selected_outline = outline.model_copy(deep=True)
    for page, binding in zip(selected_outline.slides, active.header_pages, strict=True):
        page.common_header = binding
    document.update(status="selected", selected_candidate_id=selected_id,
                    selection_input_digest=metadata["selection_input_digest"], selection=metadata)
    await save()
    final_pages = []
    for page_index, row in enumerate(rows):
        await context.raise_if_cancelled()
        await context.progress(stage="applying_common_header", current=page_index + 1, total=len(rows), percent=60)
        identity = {"candidate_revision": str(revision), "candidate_id": "final-" + selected_id,
                    "page_id": outline.slides[page_index].page_id}
        if page_index == index:
            await _seed_body(owner, active, request, outline, page_index, identity, pages[selected_id]["payload"], session_factory=session_factory)
        elif reusable_body(snapshot.get("common_header"), active.snapshot["common_header"], row):
            body, preservation, remeasurement = await reuse_body(page_index)
            prior = {**deepcopy(inputs["reuse_payloads"].get(page_index) or {}),
                     "svg_content": body, "author_body_svg": body, "source_preservation": preservation}
            if remeasurement:
                prior["body_reuse_remeasurement"] = remeasurement
                for name, source_name in (("nativeDeclarations", "source_native_declarations"),
                                          ("nativeRequirements", "source_native_requirements")):
                    if remeasurement.get(source_name) is not None:
                        prior[name] = deepcopy(remeasurement[source_name])
            await _seed_body(owner, active, request, outline, page_index, identity, prior, session_factory=session_factory)
        if owner.retry_of_operation_id is not None:
            saved = await candidate_page_checkpoint(owner, page_index, identity=identity, session_maker=session_factory)
            require_retry_page_context(owner, active, request, selected_outline, page_index, identity, saved)
        payload, png = await render_candidate(active, selected_outline, page_index, identity)
        if payload.get("quality", {}).get("passed") is not True:
            raise ValueError("svg_common_header_final_page_invalid")
        final_pages.append((payload, png))
    from services.generation_core.drivers.svg.quality import classify_pages
    async def final_checkpoint(active_owner, page_index, *, payload=None):
        identity = {"candidate_revision": str(revision), "candidate_id": "final-" + selected_id,
                    "page_id": selected_outline.slides[page_index].page_id}
        return await candidate_page_checkpoint(active_owner, page_index, identity=identity, payload=payload,
            session_maker=session_factory)
    await classify_pages(final_pages, job=owner, checkpoint=final_checkpoint, snapshot=active.snapshot)
    return active.snapshot, selected_outline, final_pages


def _base_digest(presentation, rows):
    return canonical_json_digest({"revision": presentation.revision, "theme": presentation.theme,
        "outlines": presentation.outlines, "pages": [{"id": str(row.id), "svg": row.svg_content,
                                                     "content": row.content} for row in rows]})


async def admit_svg_header_design_operation(session, *, operation, presentation, retry_source_operation=None):
    from services.authored_canvas import presentation_canvas_contract
    from services.public_operation_admission import (ProviderRequestPlan, admit_public_operation_compatible_v2,
        current_principal_context, plan_weighted_resources)
    from services.svg_common_header_design_plan import seal_new_header_design_plan, require_header_design_plan
    from services.svg_generation_admission import _common_header_preservation_conflict
    from services.svg_common_header_design_calls import preflight_header_selector

    if not presentation.is_svg() or operation.expected_revision != presentation.revision:
        raise ValueError("svg_common_header_svg_required")
    principal = current_principal_context()
    if principal.owner_id != presentation.owner_id or principal.owner_id != operation.owner_id:
        raise ValueError("svg_common_header_owner_invalid")
    rows = await _rows(session, presentation.id)
    if not rows or any(not row.is_svg() or row.index != index or row.owner_id != principal.owner_id
                       for index, row in enumerate(rows)):
        raise ValueError("svg_common_header_page_inputs_invalid")
    job = await _source_job(session, presentation)
    snapshot = ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot") or {}
    if (snapshot.get("svg_template") or {}).get("mode") == "template-redesign":
        raise ValueError("svg_template_frame_change_requires_new_revision")
    if not snapshot or not snapshot.get("digest"):
        raise ValueError("svg_common_header_style_snapshot_required")
    design = _admitted_design(operation.request_payload["context"]["common_header_design"], snapshot)
    request = _request(job, presentation, design)
    if _common_header_preservation_conflict(request):
        raise ValueError("svg_common_header_source_preservation_conflict")
    if any(((row.content or {}).get("svgReceipt") or {}).get("rasterSourcePageNumber") in
           (((row.content or {}).get("svgReceipt") or {}).get("rasterApprovalPin") or {}).get("full_page_preserve_pages", [])
           for row in rows):
        raise ValueError("svg_common_header_source_preservation_conflict")
    outline = PresentationOutlineModel.model_validate(presentation.outlines or {})
    if len(outline.slides) != len(rows) or any(not page.title or not page.page_id for page in outline.slides):
        raise ValueError("svg_common_header_frozen_titles_required")
    retry = None
    if retry_source_operation is not None:
        from services.workspace_svg_header_retry import require_header_retry_lineage, seed_header_retry_checkpoint
        seed_header_retry_checkpoint(operation, retry_source_operation)
        retry = await require_header_retry_lineage(session, operation)
        request._svg_header_design_plan = operation.request_payload["svg_header_revision"]["plan"]
        plan = require_header_design_plan(request)
    else:
        plan = seal_new_header_design_plan(request, explicit_revision=True)
    capability = preflight_header_selector()
    class RevisionPlan(ProviderRequestPlan):
        @property
        def initial_request_limit(self):
            return 2 + len(rows) + 1
        @property
        def retry_request_limit(self):
            return 2 + len(rows) + 1
    canvas = presentation_canvas_contract(presentation.theme)
    admission = await admit_public_operation_compatible_v2(session, principal=principal,
        operation_id=operation.id, operation_kind="generation",
        request_plan=RevisionPlan(slide_count=len(rows), image_budget=0, repair_budget=0, vqa_budget=0),
        resource_plan=plan_weighted_resources(pixel_count=canvas.width_px * canvas.height_px * len(rows),
            device_pixel_ratio_milli=1000, dom_node_count=0, font_count=0, svg_complexity=0,
            estimated_memory_mib=128, fidelity_mode="standard"),
        **({"policy_version": retry["policy_version"],
            "estimated_cost_limit_microunits": retry["limits"]["estimated_cost_limit_microunits"],
            "actual_cost_limit_microunits": retry["limits"]["actual_cost_limit_microunits"]} if retry else {}))
    admission.retry_request_limit = 2 + len(rows) + 1
    revision = {
        "version": "svg-common-header-revision/v2", "base_digest": _base_digest(presentation, rows),
        "source_job_id": str(job.id), "design": design, "plan": plan, "selector_capability": capability,
        "presentation_metadata": {"id": str(presentation.id), "title": presentation.title, "revision": presentation.revision},
        "body_initial_limit": len(rows) + 1, "body_retry_limit": len(rows) + 1}
    if retry is not None:
        if operation.request_payload["svg_header_revision"] != revision:
            raise ValueError("svg_common_header_retry_source_changed")
        for name, cap in retry["limits"].items():
            setattr(admission, name, min(getattr(admission, name), cap))
        await require_header_retry_lineage(session, operation, admission)
    else:
        operation.request_payload = {**operation.request_payload, "svg_header_revision": revision}
    await session.flush()


class WorkspaceSvgHeaderDesignOperationHandler:
    def __init__(self, *, session_factory=async_session_maker):
        self._session_factory = session_factory

    async def execute(self, context):
        try:
            return await self._execute(context)
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(str(error)) from error

    async def _execute(self, context):
        claim = context.claim
        await context.raise_if_cancelled()
        async with self._session_factory() as session:
            owner = await session.get(WorkspaceOperation, claim.operation_id)
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if (owner is None or presentation is None or not presentation.is_svg()
                    or owner.owner_id != claim.owner_id or presentation.owner_id != claim.owner_id
                    or owner.owner_scope != claim.owner_scope or owner.attempt_token != claim.attempt_token
                    or owner.fencing_token != claim.fencing_token or owner.cancel_requested_at is not None
                    or presentation.revision != claim.expected_revision):
                raise ValueError("svg_header_design_operation_fence_lost")
            frozen_snapshot = ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot") or {}
            if (frozen_snapshot.get("svg_template") or {}).get("mode") == "template-redesign":
                raise ValueError("svg_template_frame_change_requires_new_revision")
            rows = await _rows(session, presentation.id)
            admitted = claim.request_payload.get("svg_header_revision") or {}
            if admitted.get("base_digest") != _base_digest(presentation, rows):
                raise ValueError("svg_common_header_source_changed")
            job = await session.get(PresentationGenerationJob, uuid.UUID(admitted["source_job_id"]))
            request = _request(job, presentation, admitted["design"])
            request._svg_header_design_plan = admitted["plan"]
            outline = PresentationOutlineModel.model_validate(presentation.outlines)
            snapshot = deepcopy(presentation.theme["svg"]["styleSnapshot"])
            from services.workspace_svg_header_revision_inputs import revision_inputs as build_revision_inputs
            revision_inputs = await build_revision_inputs(session, claim, presentation, rows, snapshot, admitted)
            revision_inputs["parent_theme"] = deepcopy(presentation.theme)
            from types import SimpleNamespace
            trusted_metadata = {"id": str(presentation.id), "title": presentation.title, "revision": presentation.revision}
            if admitted.get("presentation_metadata", trusted_metadata) != trusted_metadata:
                raise ValueError("svg_common_header_source_changed")
            revision_inputs["trusted_presentation"] = SimpleNamespace(**trusted_metadata)
            request._source_content_svg_reflow = revision_inputs["ctx"].source_reflow
            request._source_content_manifest = revision_inputs["ctx"].source_document
            request._raster_reconstruction = revision_inputs["ctx"].raster_reconstruction
            request._raster_analysis = revision_inputs["ctx"].raster_analysis
            request._raster_approval_pin = revision_inputs["ctx"].raster_pin
            from services.workspace_artifact_publication_service import prepare_workspace_chat_artifact_staging
            revision_inputs["staging"] = await prepare_workspace_chat_artifact_staging(session,
                operation_id=claim.operation_id, presentation_id=claim.presentation_id,
                source_revision=claim.expected_revision + 1, attempt_token=claim.attempt_token,
                fencing_token=claim.fencing_token)
        await context.progress(stage="designing_common_header", percent=10)
        from services.provider_usage_instrumentation import public_operation_scope, provider_operation_usage_scope
        try:
            with provider_operation_usage_scope(), public_operation_scope(claim.operation_id,
                    attempt_token=claim.attempt_token, fencing_token=claim.fencing_token):
                selected, selected_outline, pages = await _design_revision(context, owner, request, outline,
                    snapshot, rows, revision_inputs, admitted, self._session_factory)
            await context.raise_if_cancelled()
            return await _publish_revision(context, selected, selected_outline, pages, revision_inputs,
                                           self._session_factory)
        except BaseException:
            from services.workspace_artifact_publication_service import (compensate_workspace_chat_artifact_staging,
                workspace_chat_artifacts_were_published)
            async with self._session_factory() as cleanup:
                promoted = revision_inputs.get("promoted")
                if promoted is not None and await workspace_chat_artifacts_were_published(cleanup,
                        staging=revision_inputs["staging"], artifacts=promoted.artifacts):
                    published = await cleanup.get(WorkspaceOperation, claim.operation_id)
                    return WorkspaceOperationResult(payload=published.result or {}, result_revision=published.result_revision)
                await compensate_workspace_chat_artifact_staging(cleanup, revision_inputs["staging"])
            raise


async def _publish_revision(context, snapshot, outline, pages, inputs, session_factory):
    from services.generation_core.drivers.svg.publish import svg_slide_content
    from services.generation_core.drivers.svg.native_publish import canvas_point_per_px
    from services.authored_canvas import presentation_canvas_metadata
    from services.presentation_publication_service import publish_presentation_revision
    from services.svg_page_publication import set_svg_page
    from services.svg_export_page_evidence import _common_header_current
    from types import SimpleNamespace
    from services.svg_presentation_checkpoint import _read_png
    from services.workspace_artifact_publication_service import (promote_workspace_chat_image_artifacts,
        stage_workspace_chat_artifacts_for_publication)
    from utils.datetime_utils import get_current_utc_datetime

    claim = context.claim
    await context.progress(stage="publishing_revision", percent=90)
    async with session_factory() as session:
        from services.workspace_operation_runner import _public_admission_for_operation
        await _public_admission_for_operation(session, claim.operation_id)
        now = get_current_utc_datetime()
        current = await session.scalar(select(WorkspaceOperation).where(
            WorkspaceOperation.id == claim.operation_id, WorkspaceOperation.owner_id == claim.owner_id,
            WorkspaceOperation.owner_scope == claim.owner_scope, WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_token == claim.attempt_token, WorkspaceOperation.fencing_token == claim.fencing_token,
            WorkspaceOperation.expected_revision == claim.expected_revision, WorkspaceOperation.cancel_requested_at.is_(None),
            WorkspaceOperation.lease_expires_at > now, WorkspaceOperation.deadline_at > now).with_for_update())
        presentation = await session.scalar(select(PresentationModel).where(
            PresentationModel.id == claim.presentation_id, PresentationModel.owner_id == claim.owner_id,
            PresentationModel.revision == claim.expected_revision).with_for_update())
        if current is None or presentation is None:
            raise ValueError("svg_header_design_operation_fence_lost")
        rows = await _rows(session, presentation.id)
        if _base_digest(presentation, rows) != claim.request_payload["svg_header_revision"]["base_digest"]:
            raise ValueError("svg_common_header_source_changed")
        if presentation.title != inputs["trusted_presentation"].title:
            raise ValueError("svg_common_header_source_changed")
        if len(pages) != len(rows) or len(outline.slides) != len(rows):
            raise ValueError("svg_common_header_final_page_count_changed")
        pngs = [_read_png(payload) for payload, _ in pages]
        if any(png is None for png in pngs):
            raise ValueError("svg_common_header_selection_render_missing")
        staged_images = []
        for index, png in enumerate(pngs):
            target = inputs["staging"].staging_directory / f"header-page-{index + 1}.png"
            target.write_bytes(png)
            staged_images.append(target)
        promoted = await promote_workspace_chat_image_artifacts(session, inputs["staging"])
        inputs["promoted"] = promoted
        images = [next(artifact.public_path for artifact in promoted.artifacts
                       if str(artifact.absolute_path) == promoted.replacements[str(target)]) for target in staged_images]
        scale = canvas_point_per_px(presentation_canvas_metadata(presentation.theme))
        for index, (row, (payload, _)) in enumerate(zip(rows, pages, strict=True)):
            quality = payload.get("quality") or {}
            if quality.get("passed") is not True or payload.get("terminal"):
                raise ValueError("svg_common_header_final_page_invalid")
            source_objects = source_summary = None
            receipt = payload.get("source_preservation")
            if receipt and inputs["source_document"] is not None:
                from services.svg_source_layout import verify_source_receipt
                verify_source_receipt(payload["svg_content"], receipt, inputs["source_document"], index)
                if receipt.get("rendering_verified") is not True:
                    raise ValueError("svg_source_quality_unverified")
                if receipt.get("native_tables"):
                    from services.svg_source_native_tables import source_table_native_objects
                    source_objects, source_summary = source_table_native_objects(payload["svg_content"], receipt,
                        inputs["source_document"], index, pt_per_px=scale)
            content = svg_slide_content(payload, role=inputs["ctx"].roles[index], source=outline.slides[index].content,
                pt_per_px=scale, source_native_objects=source_objects, source_native_summary=source_summary)
            if not _common_header_current(SimpleNamespace(svg_content=payload["svg_content"], content=content), quality):
                raise ValueError("svg_common_header_evidence_stale")
            set_svg_page(row, svg_content=payload["svg_content"], content=content, quality=quality, png=pngs[index])
            row.properties = {**(row.properties or {}), "image": images[index]}
            session.add(row)
        metadata = {**presentation.theme.get("svg", {}), "styleSnapshot": snapshot,
            "quality": [payload["quality"] for payload, _ in pages], "reviewPages": list(range(len(rows))),
            "qualityStatus": "review_required", "commonHeaderSelection": snapshot["common_header_selection"]}
        # Previous visual/export receipts remain historical and cannot certify the new revision.
        for key in ("advisory", "visualReview", "finalPptxReview", "qualityRecoveryPublication"):
            metadata.pop(key, None)
        presentation.theme = {**presentation.theme, "svg": metadata,
                              "commonHeaderDesign": deepcopy(inputs["header_design_public"])}
        presentation.outlines = outline.model_dump(mode="json")
        session.add(presentation)
        result = {"kind": "design.apply", "target": "common_header", "presentation_id": str(presentation.id),
                  "page_count": len(rows), "selection_input_digest": snapshot["common_header_selection"]["selection_input_digest"]}
        revision = await publish_presentation_revision(session, presentation_id=presentation.id,
            expected_revision=claim.expected_revision, operation_id=claim.operation_id, attempt_token=claim.attempt_token,
            result=result, source_manifest=inputs["source_document"])
        await stage_workspace_chat_artifacts_for_publication(session, staging=inputs["staging"], artifacts=promoted.artifacts)
        await session.commit()
        return WorkspaceOperationResult(payload=result, result_revision=revision)
