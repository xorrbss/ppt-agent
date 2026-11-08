"""Per-deck setup for SVG generation: admission, style pin, canonical asset
plan, and the source-image / role preflight that must finish before any page
starts spending a provider attempt.

Extracted out of ``svg_presentation_service.py`` (kept at/under the file-size
ratchet). ``_prepare_generation`` returns a :class:`GenerationContext` that
``svg_presentation_generate._generate`` unpacks before dispatching pages.

The facade names this module shadows onto locals (``require_svg_creation_admission``,
``pin_svg_style``, ``request_canvas_contract``, ``async_session_maker``, ``owned_job``,
``initialize_checkpoints``, ``_checkpoint``) are read from ``services.svg_presentation_service``
at call time rather than imported directly, so a test that monkeypatches an attribute on that
facade module still reaches this code -- exactly as it would if this were still one file.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re

from sqlalchemy import select

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from services.authored_checkpoint_errors import AuthoredStageError
from services.svg_presentation_legacy_assets import _legacy_source_image_manifest


def _restore_pinned_source_image_plan(manifest, candidates, planned):
    """Replay frozen page offers from the currently admitted image bindings."""
    from utils.authored_image_slots import source_slot_id_for_asset

    available = {
        source_slot_id_for_asset(candidate.asset_id): candidate
        for candidate in candidates
    }
    available.update({slot: candidate for page in planned for slot, candidate in page.items()})
    pages = {page.page_index: page.slot_ids for page in manifest.offered_pages}
    if set(pages) != set(range(len(planned))):
        raise ValueError("svg_asset_source_plan_conflict")
    try:
        restored = [
            {slot: available[slot] for slot in pages[index]}
            for index in range(len(planned))
        ]
    except KeyError:
        raise ValueError("svg_asset_source_plan_conflict") from None
    bound = {slot: candidate for page in restored for slot, candidate in page.items()}
    if {slot.slot_id: slot.source_sha256 for slot in manifest.slots} != {
        slot: candidate.digest.removeprefix("sha256:") for slot, candidate in bound.items()
    }:
        raise ValueError("svg_asset_source_plan_conflict")
    return restored


@dataclass
class GenerationContext:
    source_reflow: bool
    raster_reconstruction: bool
    raster_analysis: object
    raster_pin: object
    source_document: object
    source_units: object
    canonical_assets: bool
    canvas: object
    physical_kwargs: dict
    snapshot: dict
    source_font_profile: str | None
    count: int
    roles: list
    manifests: list
    planned: list
    source_manifest: object
    source_pointer: object
    asset_context: object
    asset_revision: object
    service: object
    semaphore: "asyncio.Semaphore"
    render_semaphore: "asyncio.Semaphore"
    runtime_profile: object = None
    asset_offer: object = None
    visual_intents: list | None = None
    recovery_cached_first: list[int] | None = None
    quality_recovery_source_evidence: list | None = None
    partial_recovery_manifest: dict | None = None
    header_pages: list | None = None
    provider_permits: object = None


def _preflight_svg_input_values(*, canvas, physical_kwargs, snapshot, source_document, source_units,
                                outline, roles, source_font_profile):
    """Exercise JSON input boundaries without changing typed render metadata."""
    from services.generation_core.drivers.svg.quality_recovery import _quality_json_value
    from services.svg_presentation_checkpoint import _digest
    from services.svg_trusted_fonts import read_trusted_pretendard_faces

    _digest(_quality_json_value(dict(canvas=canvas, physical=physical_kwargs, style=snapshot,
        source=source_document, units=source_units, contents=[slide.content for slide in outline.slides],
        roles=roles, font_profile=source_font_profile)))
    read_trusted_pretendard_faces()


async def _preflight_required_source_assets(session, service, manifest, context):
    """Resolve only assigned required model derivatives before author dispatch."""
    from services.svg_asset_manifest import read_derivative_bytes

    offered = {slot for page in manifest.offered_pages for slot in page.slot_ids}
    for slot in manifest.slots:
        if slot.slot_id in offered and slot.policy.required:
            await read_derivative_bytes(artifact_service=service, session=session,
                derivative=slot.model_derivative, owner_id=context.owner_id, tenant_id=context.tenant_id,
                max_bytes=1536 * 1024, max_pixels=16_777_216)


def _prepare_header_outline(snapshot, outline, canvas, *, mode, cover_exception):
    """Bind titles on a clone so a failed preparation cannot partly freeze input."""
    from services.svg_common_header import prepare_common_header
    from utils.outline_utils import explicit_outline_title
    from utils.hashing import canonical_json_digest

    stored_header = snapshot.get("common_header")
    mode = mode or ("uniform" if isinstance(stored_header, dict) and stored_header.get("version") else "free")
    prepared = outline.model_copy(deep=True)
    if mode == "uniform":
        for slide in prepared.slides:
            if slide.title is None:
                slide.title = explicit_outline_title(slide.content)
            if not isinstance(slide.title, str) or not slide.title.strip():
                raise ValueError("svg_common_header_title_unresolved")
    contract, bindings = prepare_common_header(snapshot, prepared, canvas,
        mode=mode, cover_exception=cover_exception)
    if contract is None:
        return snapshot, prepared
    if len(bindings) != len(prepared.slides):
        raise ValueError("svg_common_header_binding_incomplete")
    snapshot = {**snapshot, "common_header": contract}
    snapshot["digest"] = canonical_json_digest(
        {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True)
    for slide, binding in zip(prepared.slides, bindings):
        slide.common_header = binding
    return snapshot, prepared


def _verify_header_title_plan_outline(request, outline):
    from services.svg_common_header import common_header_source_digest
    from services.svg_common_header_title_budget import validate_header_title_plan
    from utils.hashing import canonical_json_digest

    plan = validate_header_title_plan(getattr(request, "_svg_header_title_plan", None))
    if plan is None:
        return
    sources = [{"page_index": index,
                "source_digest": common_header_source_digest(slide.content)}
               for index, slide in enumerate(outline.slides)]
    if (len(sources) != plan["page_count"]
            or canonical_json_digest(sources) != plan["source_digest"]
            or any(not outline.slides[index].title_source_digest
                   for index in plan["missing_indices"])):
        raise ValueError("svg_common_header_title_plan_conflict")


async def _verified_title_checkpoint_request_digest(session, job):
    """Recover the submitted fingerprint from sealed partial-recovery ancestry."""
    from models.sql.workspace import WorkspaceOperation
    from services.presentation_generation_job_service import request_sha256
    from services.svg_partial_recovery import (
        ARCHIVE_KEY, ARCHIVE_V2, LINEAGE_KEY, REVISION, _frozen_payload,
        _sha256_hex_valid, _verify_operation_references, digest,
    )
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    payload = job.request_payload or {}
    if LINEAGE_KEY not in payload:
        return job.request_sha256
    operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if operation is None or owner is None or not tenant:
        raise ValueError("svg_common_header_title_checkpoint_conflict")
    current_sha = job.request_sha256
    seen = set()
    while LINEAGE_KEY in payload:
        if operation.id in seen or not isinstance(payload, dict) or not _sha256_hex_valid(current_sha):
            raise ValueError("svg_common_header_title_checkpoint_conflict")
        seen.add(operation.id)
        operation_payload = operation.request_payload or {}
        lineage = payload.get(LINEAGE_KEY)
        manifest = operation_payload.get("svg_partial_recovery_manifest")
        archive = operation_payload.get(ARCHIVE_KEY)
        source_job = archive.get("job") if isinstance(archive, dict) else None
        source_payload = source_job.get("request_payload") if isinstance(source_job, dict) else None
        source_sha = source_job.get("request_sha256") if isinstance(source_job, dict) else None
        parent_id = operation.retry_of_operation_id
        if (not isinstance(lineage, dict) or not isinstance(manifest, dict)
                or not isinstance(archive, dict) or archive.get("schema") != ARCHIVE_V2
                or manifest.get("archive_schema") != ARCHIVE_V2
                or operation_payload.get("recovery_revision") != REVISION
                or operation_payload.get("generation_request") != payload
                or lineage != operation_payload.get("svg_partial_recovery_lineage")
                or lineage.get("schema") != "svg-partial-recovery-lineage/v1"
                or lineage.get("operation_id") != str(operation.id)
                or lineage.get("action_operation_id") != manifest.get("operation_id")
                or lineage.get("source_operation_id") != manifest.get("source_operation_id")
                or lineage.get("manifest_hash") != digest(manifest)
                or lineage.get("archive_digest") != manifest.get("archive_digest")
                or lineage.get("frozen_request_digest") != manifest.get("frozen_request_digest")
                or lineage.get("frozen_style_digest") != manifest.get("frozen_style_digest")
                or operation_payload.get("svg_partial_recovery_manifest_hash") != digest(manifest)
                or digest(archive) != manifest.get("archive_digest")
                or digest(_frozen_payload(payload)) != manifest.get("frozen_request_digest")
                or digest(job.svg_style_snapshot) != manifest.get("frozen_style_digest")
                or parent_id is None or manifest.get("operation_id") != str(parent_id)
                or operation.owner_id != owner or operation.kind != "presentation.generate"
                or operation.presentation_id != job.presentation_id
                or operation_payload.get("principal_tenant_id") != tenant
                or manifest.get("owner_id") != str(owner)
                or manifest.get("tenant_id") != tenant
                or manifest.get("presentation_id") != str(job.presentation_id)
                or manifest.get("job_id") != str(job.id)
                or lineage.get("job_id") != str(job.id)
                or request_sha256(payload) != current_sha
                or not isinstance(source_payload, dict) or not _sha256_hex_valid(source_sha)
                or source_job.get("id") != str(job.id)
                or source_job.get("presentation_id") != str(job.presentation_id)
                or source_job.get("workspace_operation_id") != str(parent_id)
                or source_job.get("svg_style_snapshot") != job.svg_style_snapshot
                or source_job.get("authored_outline") != job.authored_outline
                or digest(_frozen_payload(source_payload)) != manifest.get("frozen_request_digest")):
            raise ValueError("svg_common_header_title_checkpoint_conflict")
        await _verify_operation_references(
            session, operation, manifest, archive.get("operation_refs"))
        operation = await session.get(WorkspaceOperation, parent_id)
        if operation is None:
            raise ValueError("svg_common_header_title_checkpoint_conflict")
        payload, current_sha = source_payload, source_sha
    return current_sha


async def _verify_frozen_title_result(session, job, outline, plan):
    """A normal resume must reuse the sealed result that supplied its titles."""
    from uuid import UUID

    from models.sql.public_admission import AuthoredDeckPlanCheckpoint
    from models.sql.workspace import WorkspaceOperation
    from services.authored_deck_plan_checkpoint import canonical_checkpoint_digest
    from services.svg_common_header_titles import _restore_result
    from utils.hashing import canonical_json_digest

    lineage = (job.request_payload or {}).get("_svg_partial_recovery") or {}
    if lineage.get("schema") == "svg-partial-recovery-lineage/v2":
        from models.sql.presentation_generation_job import PresentationGenerationJob
        from services.svg_partial_recovery import verify_prepared_episode
        operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
        if operation is None:
            raise ValueError("svg_common_header_title_checkpoint_conflict")
        manifest = await verify_prepared_episode(session, operation, job)
        producer = await session.get(PresentationGenerationJob, UUID(manifest["job_id"]))
        if (producer is None or producer.authored_outline != outline.model_dump(mode="json")
                or producer.svg_style_snapshot != job.svg_style_snapshot):
            raise ValueError("svg_common_header_title_checkpoint_conflict")
        return await _verify_frozen_title_result(session, producer, outline, plan)

    row = await session.scalar(select(AuthoredDeckPlanCheckpoint).where(
        AuthoredDeckPlanCheckpoint.generation_job_id == job.id,
        AuthoredDeckPlanCheckpoint.stage == "header"))
    stored = row.payload if row is not None else None
    stored_identity = stored.get("identity") if isinstance(stored, dict) else None
    source_text = stored_identity.get("workspace_operation_id") if isinstance(stored_identity, dict) else None
    current_text = str(job.workspace_operation_id) if job.workspace_operation_id else None
    if source_text != current_text:
        try:
            source_id = UUID(source_text)
        except (TypeError, ValueError) as error:
            raise ValueError("svg_common_header_title_checkpoint_conflict") from error
        current = await session.get(WorkspaceOperation, job.workspace_operation_id)
        if current is None:
            raise ValueError("svg_common_header_title_checkpoint_conflict")
        seen = set()
        while current.id != source_id:
            if current.id in seen or current.retry_of_operation_id is None:
                raise ValueError("svg_common_header_title_checkpoint_conflict")
            seen.add(current.id)
            ancestor = await session.get(WorkspaceOperation, current.retry_of_operation_id)
            if (ancestor is None or ancestor.kind != "presentation.generate"
                    or ancestor.state not in {"failed", "cancelled", "dead_letter"}
                    or ancestor.fencing_token < 1
                    or ancestor.presentation_id != job.presentation_id
                    or ancestor.presentation_id != current.presentation_id
                    or ancestor.owner_id != current.owner_id
                    or ancestor.owner_scope != current.owner_scope
                    or ancestor.conversation_id != current.conversation_id):
                raise ValueError("svg_common_header_title_checkpoint_conflict")
            current = ancestor
    identity = {"job_id": str(job.id),
                "request_digest": await _verified_title_checkpoint_request_digest(session, job),
                "workspace_operation_id": source_text,
                "plan_digest": plan["plan_digest"]}
    if (row is None or row.input_digest != canonical_checkpoint_digest(identity)
            or not isinstance(stored, dict)
            or row.output_digest != canonical_checkpoint_digest(stored)
            or stored.get("version") != "svg-common-header-title-batch/v1"
            or stored.get("identity") != identity or stored.get("state") != "succeeded"
            or set(stored) != {"version", "identity", "state", "result"}):
        raise ValueError("svg_common_header_title_checkpoint_conflict")
    from services.svg_common_header import common_header_source_digest
    preparing_v2 = ((job.svg_style_snapshot or {}).get("common_header_design_plan", {}).get("schema")
                    == "svg-common-header-design-plan/v2"
                    and "common_header" not in (job.svg_style_snapshot or {}))
    pages = [{"page_index": index,
              "source_digest": (common_header_source_digest(slide.content) if preparing_v2
                                else slide.common_header["source_digest"]),
              "content": slide.content}
             for index, slide in enumerate(outline.slides)
             if index in plan["missing_indices"]]
    payload = {"source_digest": plan["source_digest"],
               "page_count": len(outline.slides), "pages": pages}
    result = _restore_result(stored["result"], payload)
    if (len(result["titles"]) != len(plan["missing_indices"])
            or any(outline.slides[item["page_index"]].title != item["title"]
                   or outline.slides[item["page_index"]].title_source_digest
                   != canonical_json_digest(item)
                   for item in result["titles"])):
        raise ValueError("svg_common_header_title_checkpoint_conflict")
    return UUID(source_text) if source_text is not None else None


async def _pin_owned_svg_style(request, job, *, pin_style, session_maker, owned_job,
                               outline=None, canvas=None):
    from services.svg_authoring_profile import validate_profile_agreement, validate_quality_recovery_agreement
    from services.svg_common_header import header_snapshot_base

    if outline is not None:
        _verify_header_title_plan_outline(request, outline)

    if job is None:
        async with session_maker() as authority_session:
            from services.svg_generation_admission import prepare_private_svg_recovery_authority

            await prepare_private_svg_recovery_authority(request, authority_session)
        snapshot = pin_style(request)
        if outline is not None:
            snapshot, prepared = _prepare_header_outline(snapshot, outline, canvas,
                mode=getattr(request, "common_header_mode", "free"),
                cover_exception=getattr(request, "common_header_cover_exception", False))
            outline.slides = prepared.slides
            request._svg_style_snapshot = snapshot
        return snapshot
    frozen_title_plan = None
    async with session_maker() as checkpoint_session:
        current = await owned_job(checkpoint_session, job.id, job.attempt_token,
                                  fencing_token=getattr(job, "_workspace_fencing_token", None))
        admitted = getattr(current, "request_payload", None) or {}
        frozen = current.svg_style_snapshot
        for key, value in (
            ("_internal_svg_source_binding", getattr(request, "_svg_source_binding", None)),
            ("_internal_svg_style_snapshot", getattr(request, "_svg_style_snapshot", None)),
        ):
            if key in admitted and admitted[key] != value:
                if (key == "_internal_svg_style_snapshot" and isinstance(frozen, dict)
                        and "common_header" in frozen and value == frozen
                        and isinstance(admitted[key], dict) and "common_header" not in admitted[key]
                        and admitted[key] == header_snapshot_base(frozen)):
                    continue
                raise RuntimeError("svg_initial_checkpoint_input_conflict")
        saved_outline = getattr(current, "authored_outline", None)
        if (outline is not None and getattr(request, "common_header_mode", None) == "uniform"
                and (frozen is not None or saved_outline is not None)
                and (frozen or {}).get("common_header_design_plan", {}).get("schema")
                    != "svg-common-header-design-plan/v2"
                and not (isinstance(frozen, dict) and "common_header" in frozen
                         and saved_outline is not None)):
            raise ValueError("svg_common_header_binding_incomplete")
        if frozen is not None:
            validate_profile_agreement(request, frozen)
            validate_quality_recovery_agreement(request, frozen)
            staged = getattr(request, "_svg_style_snapshot", None)
            if staged is not None and staged != frozen:
                base_matches = (isinstance(staged, dict) and "common_header" not in staged
                    and "common_header" in frozen
                    and staged == header_snapshot_base(frozen))
                if not base_matches:
                    raise RuntimeError("svg_style_snapshot_conflict")
            request._svg_style_snapshot = frozen
        from services.svg_generation_admission import prepare_private_svg_recovery_authority

        await prepare_private_svg_recovery_authority(request, checkpoint_session)
        snapshot = pin_style(request)
        validate_profile_agreement(request, snapshot)
        validate_quality_recovery_agreement(request, snapshot)
        if frozen is not None and frozen != snapshot:
            raise RuntimeError("svg_style_snapshot_conflict")
        if outline is not None:
            design_plan = snapshot.get("common_header_design_plan") or {}
            if (design_plan.get("schema") == "svg-common-header-design-plan/v2"
                    and "common_header" not in snapshot):
                # The v2 document design owns the later CAS that publishes all
                # bindings together. Never create a provisional v1 header.
                from uuid import UUID, uuid5
                from services.svg_common_header import common_header_source_digest
                from utils.outline_utils import explicit_outline_title

                prepared = outline.model_copy(deep=True)
                for index, slide in enumerate(prepared.slides):
                    if slide.title is None:
                        slide.title = explicit_outline_title(slide.content)
                    if not isinstance(slide.title, str) or not slide.title.strip():
                        raise ValueError("svg_common_header_title_unresolved")
                    if not slide.page_id:
                        if current.svg_header_design_checkpoint is not None:
                            raise ValueError("svg_common_header_checkpoint_page_identity_missing")
                        slide.page_id = str(uuid5(UUID(str(current.id)),
                            f"svg-common-header-page/v2:{index}:{common_header_source_digest(slide.content)}"))
                if len({slide.page_id for slide in prepared.slides}) != len(prepared.slides):
                    raise ValueError("svg_common_header_page_id_duplicate")
                outline.slides = prepared.slides
                current.authored_outline = outline.model_dump(mode="json")
                current.svg_style_snapshot = snapshot
                request._svg_style_snapshot = snapshot
                checkpoint_session.add(current)
                await checkpoint_session.commit()
                return snapshot
            if frozen is not None and "common_header" in frozen:
                from models.presentation_outline_model import PresentationOutlineModel
                from services.svg_common_header import prepare_common_header, validate_common_header_binding

                if saved_outline is None:
                    raise ValueError("svg_common_header_binding_incomplete")
                prepared = PresentationOutlineModel.model_validate(saved_outline)
                if prepared.model_dump(mode="json") != outline.model_dump(mode="json"):
                    raise ValueError("svg_common_header_outline_conflict")
                for index, slide in enumerate(prepared.slides):
                    if (slide.common_header is None or slide.common_header.get("page_index") != index
                            or slide.title != slide.common_header.get("title")):
                        raise ValueError("svg_common_header_binding_incomplete")
                    validate_common_header_binding(frozen["common_header"], slide.common_header,
                        index=index, count=len(prepared.slides),
                        content=slide.content, title=slide.title)
                    if (slide.title_source_digest is not None
                            and slide.common_header.get("title_source_digest") != slide.title_source_digest):
                        raise ValueError("svg_common_header_binding_incomplete")
                if (getattr(request, "_svg_header_title_plan", None) is not None
                        and not (current.request_payload or {}).get("_svg_partial_recovery")):
                    from services.svg_common_header_title_budget import validate_header_title_plan

                    frozen_title_plan = validate_header_title_plan(request._svg_header_title_plan)
                prepare_common_header(frozen, prepared, canvas,
                    mode=getattr(request, "common_header_mode", None) or "uniform",
                    cover_exception=getattr(request, "common_header_cover_exception", False))
                outline.slides = prepared.slides
            elif frozen is None and saved_outline is None:
                snapshot, prepared = _prepare_header_outline(snapshot, outline, canvas,
                    mode=getattr(request, "common_header_mode", "free"),
                    cover_exception=getattr(request, "common_header_cover_exception", False))
                if "common_header" in snapshot:
                    # The owned_job check above guards this single transaction:
                    # no body dispatch can observe style without the page titles.
                    current.authored_outline = prepared.model_dump(mode="json")
                outline.slides = prepared.slides
            elif "common_header" in snapshot or any(
                    slide.common_header is not None for slide in outline.slides):
                # A historical absence is free; a partly persisted header is
                # incomplete and must never be repaired with new defaults.
                raise ValueError("svg_common_header_binding_incomplete")
            request._svg_style_snapshot = snapshot
        current.svg_style_snapshot = snapshot
        checkpoint_session.add(current)
        await checkpoint_session.commit()
    if frozen_title_plan is not None:
        from models.sql.presentation_generation_job import PresentationGenerationJob

        # Historical verification must not retain the authority CAS row locks.
        async with session_maker() as title_session:
            current = await title_session.get(PresentationGenerationJob, job.id)
            if (current is None or current.workspace_operation_id != job.workspace_operation_id
                    or current.svg_style_snapshot != snapshot
                    or current.authored_outline != prepared.model_dump(mode="json")):
                raise ValueError("svg_common_header_title_checkpoint_conflict")
            await _verify_frozen_title_result(title_session, current, prepared, frozen_title_plan)
        async with session_maker() as authority_session:
            await owned_job(authority_session, job.id, job.attempt_token,
                fencing_token=getattr(job, "_workspace_fencing_token", None))
            await authority_session.commit()
    return snapshot


async def _verify_owned_partial_recovery(request, job, count, *, session_maker, owned_job):
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.workspace import WorkspaceOperation
    from services.svg_partial_recovery import verify_prepared_episode, digest
    from services.authored_canvas import request_canvas_metadata
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    async with session_maker() as authority_session:
        await owned_job(authority_session, job.id, job.attempt_token,
            fencing_token=getattr(job, "_workspace_fencing_token", None))
        await authority_session.commit()
    # The initial historical verification can be slow. Reload its inputs in a
    # separate read transaction so operation heartbeats can acquire their locks.
    async with session_maker() as recovery_session:
        current = await recovery_session.get(PresentationGenerationJob, job.id)
        operation = await recovery_session.get(WorkspaceOperation, job.workspace_operation_id)
        if (current is None or operation is None
                or current.workspace_operation_id != operation.id
                or current.attempt_token != job.attempt_token
                or operation.attempt_token != job.attempt_token
                or operation.fencing_token != getattr(job, "_workspace_fencing_token", None)
                or current.state not in {"running", "staging"}
                or operation.state != "running" or operation.cancel_requested_at is not None):
            raise ValueError("svg_partial_recovery_episode_unbound")
        manifest = await verify_prepared_episode(recovery_session, operation, current)
        if (manifest.get("owner_id") != str(get_current_owner_id())
                or manifest.get("tenant_id") != str(get_current_tenant_id())
                or manifest.get("expected_page_count") != count
                or manifest.get("canvas_digest") != digest(request_canvas_metadata(request))):
            raise ValueError("svg_partial_recovery_episode_unbound")
    async with session_maker() as authority_session:
        await owned_job(authority_session, job.id, job.attempt_token,
            fencing_token=getattr(job, "_workspace_fencing_token", None))
        await authority_session.commit()
    return manifest


async def _prepare_generation(request, presentation_id, outline, session, job) -> GenerationContext:
    from services.authored_source_images import collect_source_image_candidates, plan_slide_source_images
    from services.svg_generation_assets import slot_writer_enabled
    from utils.authored_styles import resolve_authored_style
    from services.generation_core.authoring.author_slide_svg import preflight_svg_style_images

    from services import svg_presentation_service as _facade
    require_svg_creation_admission = _facade.require_svg_creation_admission
    pin_svg_style = _facade.pin_svg_style
    request_canvas_contract = _facade.request_canvas_contract
    async_session_maker = _facade.async_session_maker
    owned_job = _facade.owned_job
    initialize_checkpoints = _facade.initialize_checkpoints
    _checkpoint = _facade._checkpoint

    require_svg_creation_admission(request)
    if job is not None and getattr(job, "presentation_id", presentation_id) != presentation_id:
        raise RuntimeError("svg_initial_checkpoint_input_conflict")
    source_reflow = bool(getattr(request, "_source_content_svg_reflow", False))
    source_preservation_required = source_reflow or bool(request._source_content_authored_reflow) or bool(re.search(
        r"(?:이미지|사진|도표|그림).{0,40}(?:모두|전부|필수|반드시|보존|빠짐없이)"
        r"|(?:모든|전체|필수|반드시).{0,20}(?:이미지|사진|도표|그림)"
        r"|(?:preserve|retain|keep|must include).{0,40}(?:image|picture|chart)"
        r"|(?:all|every)\s+(?:source\s+)?(?:image|picture|chart)",
        request.instructions or "", re.IGNORECASE))
    raster_reconstruction = bool(getattr(request, "_raster_reconstruction", False))
    raster_analysis = getattr(request, "_raster_analysis", None)
    raster_pin = getattr(request, "_raster_approval_pin", None)
    if raster_reconstruction:
        from services.source_content.raster_contracts import (
            RasterAnalysis, RasterApprovalPin, require_analysis_digest,
        )

        raster_analysis = RasterAnalysis.model_validate(raster_analysis)
        raster_pin = RasterApprovalPin.model_validate(raster_pin)
        require_analysis_digest(raster_analysis)
        if raster_analysis.digest != raster_pin.analysis_digest or source_reflow:
            raise ValueError("raster_generation_analysis_pin_mismatch")
        if job is None or job.workspace_operation_id is None:
            raise ValueError("raster_generation_durable_job_required")
    source_document = getattr(request, "_source_content_manifest", None)
    source_units = None
    from services.generation_core.admission.strategy import resolve_generation_strategy

    canonical_assets = not raster_reconstruction and slot_writer_enabled(
        strategy=resolve_generation_strategy(request),
        durable=job is not None and job.workspace_operation_id is not None)
    canvas = request_canvas_contract(request)
    physical_kwargs = ({"physical_canvas": request._document_canvas}
                       if getattr(request, "_document_canvas", None) is not None else {})
    snapshot = await _pin_owned_svg_style(
        request, job, pin_style=pin_svg_style, session_maker=async_session_maker, owned_job=owned_job,
        outline=outline, canvas=canvas,
    )
    if (snapshot.get("common_header_design_plan") or {}).get("schema") == "svg-common-header-design-plan/v2":
        from utils.llm_calls.svg_common_header_design import preflight_header_selector
        preflight_header_selector()
    if source_reflow:
        from services.svg_source_layout import source_pages
        source_units = source_pages(source_document, chart_contract=snapshot.get("source_chart_contract"))
        if len(source_units) != len(outline.slides):
            raise ValueError("svg_source_page_count_mismatch")
    from services.svg_authoring_profile import validate_profile_agreement

    validate_profile_agreement(request, snapshot)
    source_font_profile = None
    if source_reflow:
        from utils.svg_template_fonts import source_font_profile as resolved_font_profile
        source_font_profile = resolved_font_profile(snapshot)
    count = len(outline.slides)
    if raster_reconstruction and raster_analysis.source.page_count != count:
        raise ValueError("raster_generation_page_count_mismatch")
    if not count:
        raise ValueError("svg_outline_empty")
    partial_recovery_manifest = None
    if job is not None and (getattr(job, "request_payload", None) or {}).get("_svg_partial_recovery"):
        partial_recovery_manifest = await _verify_owned_partial_recovery(
            request, job, count, session_maker=async_session_maker, owned_job=owned_job)
    existing = None
    if job is not None:
        # Existing inline checkpoints must finish with their original contract.
        # A takeover must never mix inline pages and canonical asset pages.
        async with async_session_maker() as checkpoint_session:
            existing = await checkpoint_session.scalar(select(AuthoredSlideCheckpoint.svg_checkpoint).where(
                AuthoredSlideCheckpoint.job_id == job.id,
                AuthoredSlideCheckpoint.svg_checkpoint.is_not(None)).limit(1))
            from services.svg_authoring_profile import read_quality_recovery_policy
            if read_quality_recovery_policy(snapshot):
                saved_pages = list(await checkpoint_session.scalars(select(AuthoredSlideCheckpoint.svg_checkpoint).where(
                    AuthoredSlideCheckpoint.job_id == job.id,
                    AuthoredSlideCheckpoint.svg_checkpoint.is_not(None))))
                from services.generation_core.drivers.svg.collect import _unknown_provider_checkpoint
                def review_unknown(value):
                    if isinstance(value, dict):
                        return (value.get("status") in {"review_inflight", "review_unknown"}
                                or any(review_unknown(item) for item in value.values()))
                    return any(review_unknown(item) for item in value) if isinstance(value, list) else False
                if any(_unknown_provider_checkpoint(saved) or review_unknown(saved) for saved in saved_pages):
                    raise AuthoredStageError(slide_index=None, stage="author",
                        code="public_provider_result_unknown", retryable=False)
        if existing and existing.get("trusted_stage_execution") and getattr(request, "_svg_stage_plan", None) is None:
            raise AuthoredStageError(slide_index=None, stage="author",
                                    code="svg_stage_plan_missing", retryable=False)
        if existing and any(key in existing for key in (
            "schema_version", "source_manifest_ref", "svg_content", "input_digest", "attempts"
        )):
            canonical_assets = not raster_reconstruction and (
                existing.get("schema_version") == "svg-slide-checkpoint/v2"
                or bool(existing.get("source_manifest_ref")))
    if source_reflow and not canonical_assets:
        raise ValueError("svg_source_durable_assets_required")
    source_identity_presentation_id = None
    if partial_recovery_manifest:
        from services.artifact_store import configured_public_artifact_service
        from services.svg_asset_persistence import load_source_manifest_pointer
        from services.svg_asset_manifest import ArtifactPointer
        from services.svg_generation_assets import publication_context, _verify_inherited_source_manifest

        async with async_session_maker() as asset_session:
            current_context, _ = await publication_context(asset_session, job)
            binding = partial_recovery_manifest["source_asset_binding"]
            pointer = ArtifactPointer.model_validate(binding["pointer"])
            reference = await configured_public_artifact_service().resolve_ref(asset_session,
                artifact_id=pointer.ref, owner_id=current_context.owner_id,
                tenant_id=current_context.tenant_id, expected_digest=pointer.sha256)
            inherited = await load_source_manifest_pointer(asset_session, configured_public_artifact_service(),
                pointer=binding["pointer"],
                owner_id=current_context.owner_id, tenant_id=current_context.tenant_id)
            if (inherited.digest != binding["digest"] or str(inherited.presentation_id) != binding["presentation_id"]
                    or inherited.source_revision != binding["source_revision"]
                    or str(reference.created_by_operation_id) != binding["origin_operation_id"]):
                raise ValueError("svg_partial_recovery_asset_binding_changed")
            await _verify_inherited_source_manifest(asset_session, inherited, binding["pointer"],
                context=current_context, presentation_id=presentation_id, operation_id=job.workspace_operation_id)
            await _preflight_required_source_assets(asset_session, configured_public_artifact_service(), inherited,
                current_context)
            from services.svg_source_input_snapshot import record_source_input_evidence
            # This attempt's fresh proof; its publication may persist svg-source-input-snapshot/v1.
            record_source_input_evidence(request, job=job, context=current_context,
                partial_manifest=partial_recovery_manifest, inherited=inherited, pointer=binding["pointer"])
            if source_reflow:
                source_identity_presentation_id = inherited.presentation_id
    source_image_registration = []
    candidates = await collect_source_image_candidates(
        request, resolve_authored_style(request.authored_style), presentation_id, session,
        include_document_images=True, registration_report=source_image_registration,
        **({"source_identity_presentation_id": source_identity_presentation_id}
           if source_identity_presentation_id is not None else {}))
    use_content_group_assignment = not source_preservation_required and not existing
    source_image_assignment = []
    planned = plan_slide_source_images(
        [item.content for item in outline.slides], candidates,
        source_slide_locked=source_reflow,
        content_group_assignment=use_content_group_assignment,
        assignment_report=source_image_assignment,
    )
    if candidates and planned is None:
        raise RuntimeError("svg_source_image_plan_failed")
    manifests = []
    # Complete every page's local image checks before any page can reserve a
    # paid attempt. A later page's invalid input must not buy earlier pages.
    planned = planned or [{} for _ in outline.slides]
    for index, offered in enumerate(planned if not canonical_assets else []):
        try:
            manifests.append(_legacy_source_image_manifest(offered))
        except (ValueError, KeyError, OSError) as error:
            raise AuthoredStageError(slide_index=index, stage="author",
                                    code="preflight_failed", retryable=False) from error
    roles = ["cover" if i == 0 and request.include_title_slide else "content" for i in range(count)]
    try:
        preflight_svg_style_images(snapshot, roles)
    except (ValueError, KeyError, OSError) as error:
        raise AuthoredStageError(slide_index=0, stage="author",
                                code="preflight_failed", retryable=False) from error
    _preflight_svg_input_values(canvas=canvas, physical_kwargs=physical_kwargs, snapshot=snapshot,
        source_document=source_document, source_units=source_units, outline=outline,
        roles=roles, source_font_profile=source_font_profile)
    if job is not None:
        await initialize_checkpoints(job_id=job.id, attempt_token=job.attempt_token, roles=roles,
                                     fencing_token=getattr(job, "_workspace_fencing_token", None))
    from services.svg_visual_intent_checkpoint import freeze_visual_intents, intent_contents
    from services.svg_authoring_profile import read_quality_recovery_policy

    visual_intents = await freeze_visual_intents(
        contents=intent_contents(outline, source_units=source_units,
                                raster_analysis=raster_analysis, raster_pin=raster_pin),
        roles=roles, job=job, historical_payload=existing, checkpoint=_checkpoint,
            trusted_plan=getattr(request, "_svg_stage_plan", None),
        quality_policy=read_quality_recovery_policy(snapshot),
    )
    asset_offer = None
    if not source_reflow and not raster_reconstruction:
        asset_offer = await _offer_assets(request, presentation_id, outline, roles, job, planned,
            manifests, canonical_assets=canonical_assets, style_snapshot=snapshot,
            **({"source_assets_present": bool(candidates)} if use_content_group_assignment else {}))
    source_manifest = source_pointer = asset_context = asset_revision = None
    service = None
    if canonical_assets:
        from services.artifact_store import configured_public_artifact_service
        from services.svg_generation_assets import publication_context, prepare_source_manifest
        service = configured_public_artifact_service()
        seed = await _checkpoint(job, 0) or {}
        if partial_recovery_manifest:
            original_pointer = partial_recovery_manifest["source_asset_binding"]["pointer"]
            if seed.get("source_manifest_ref") not in (None, original_pointer):
                raise ValueError("svg_partial_recovery_asset_binding_changed")
            seed["source_manifest_ref"] = original_pointer
        if use_content_group_assignment:
            seed["source_image_assignment"] = {
                "version": "source-image-assignment/v1", "groups": source_image_assignment,
                "registration": [
                    {
                        **{key: item[key] for key in (
                            "code", "status", "coverage", "digest", "asset_count", "occurrence_count",
                            "block_index",
                        ) if key in item},
                        **({"diagnostics": [{"code": value["code"]}
                            for value in item["diagnostics"]
                            if isinstance(value, dict) and isinstance(value.get("code"), str)]}
                           if isinstance(item.get("diagnostics"), list) else {}),
                    }
                    for item in source_image_registration if isinstance(item, dict)
                ],
            }
        if source_reflow:
            if seed.get("source_font_profile", source_font_profile) != source_font_profile:
                raise ValueError("svg_source_font_binding_mismatch")
            seed["source_font_profile"] = source_font_profile
        async with async_session_maker() as asset_session:
            asset_context, asset_revision = await publication_context(asset_session, job)
            if seed.get("source_manifest_ref") is not None:
                from services.svg_asset_persistence import load_source_manifest_pointer

                pinned_manifest = await load_source_manifest_pointer(
                    asset_session, service, pointer=seed["source_manifest_ref"],
                    owner_id=asset_context.owner_id, tenant_id=asset_context.tenant_id)
                planned = _restore_pinned_source_image_plan(pinned_manifest, candidates, planned)
            source_manifest, source_pointer = await prepare_source_manifest(asset_session, service,
                context=asset_context, revision=asset_revision, presentation_id=presentation_id,
                planned=planned, saved_pointer=seed.get("source_manifest_ref"),
                source_preservation_required=source_preservation_required,
                inherited_operation_id=(job.workspace_operation_id if job is not None
                        and ((getattr(job, "request_payload", None) or {}).get("_svg_recovery_lineage")
                             or partial_recovery_manifest) else None),
                )
            if not partial_recovery_manifest:
                await _preflight_required_source_assets(asset_session, service, source_manifest, asset_context)
            await asset_session.commit()
        seed["source_manifest_ref"] = source_pointer
        await _checkpoint(job, 0, payload=seed)
        manifests = [{slot.slot_id: slot.model_dump(mode="json", by_alias=True)
                      for slot in source_manifest.slots if slot.slot_id in offered} for offered in planned]
    # Provider/render workers share the job-level gate with HTML; each page owns
    # separate transactions so concurrent calls never share an AsyncSession.
    from services.generation_core.runtime.profile import resolve_svg_runtime_profile
    runtime_profile = resolve_svg_runtime_profile(source_reflow=source_reflow,
                                                  raster_reconstruction=raster_reconstruction)
    from services.svg_timeout_repurchase import sample_completion_runtime_profile
    runtime_profile, recovery_cached_first = await sample_completion_runtime_profile(
        session, job, runtime_profile)
    # A fresh attempt pipelines its pages: up to author + render pages are in
    # flight, author attempts take a provider permit (capacity = the configured
    # author concurrency) and renders keep their own gate, so a page renders
    # while another authors. A timeout repurchase pins a cached-first serial
    # profile and keeps the single page gate exactly as before.
    from services.generation_core.runtime.profile import ProviderPermits
    pipelined = recovery_cached_first is None
    semaphore = asyncio.Semaphore(runtime_profile.author_concurrency
                                  + (runtime_profile.render_concurrency if pipelined else 0))
    render_semaphore = asyncio.Semaphore(runtime_profile.render_concurrency)
    provider_permits = ProviderPermits(runtime_profile.author_concurrency) if pipelined else None
    return GenerationContext(
        source_reflow=source_reflow, raster_reconstruction=raster_reconstruction,
        raster_analysis=raster_analysis, raster_pin=raster_pin, source_document=source_document,
        source_units=source_units, canonical_assets=canonical_assets, canvas=canvas,
        physical_kwargs=physical_kwargs, snapshot=snapshot, source_font_profile=source_font_profile,
        count=count, roles=roles, manifests=manifests, planned=planned, source_manifest=source_manifest,
        source_pointer=source_pointer, asset_context=asset_context, asset_revision=asset_revision,
        service=service, semaphore=semaphore, render_semaphore=render_semaphore,
        runtime_profile=runtime_profile, provider_permits=provider_permits,
        recovery_cached_first=recovery_cached_first,
        asset_offer=asset_offer,
        visual_intents=visual_intents,
        quality_recovery_source_evidence=getattr(request, "_svg_recovery_source_evidence", None),
        partial_recovery_manifest=partial_recovery_manifest,
        header_pages=([slide.common_header for slide in outline.slides]
                      if "common_header" in snapshot else None),
    )


async def _offer_assets(request, presentation_id, outline, roles, job, planned, manifests, *,
                        canonical_assets, style_snapshot=None, source_assets_present=None):
    """Add the deck's planned illustrations/icons to every page's image offer.

    Raster slots join ``planned`` (and the inline manifests on the
    non-canonical path) exactly like source images; vector icons stay on the
    returned projection for the author step to flatten in place.
    """
    from services import svg_presentation_service as _facade
    from services.generation_core.drivers.svg.asset_slots import (
        SvgAssetOffer, legacy_manifest, project_offer, resolve_offer,
    )
    from services.generation_core.runtime.profile import resolve_svg_runtime_profile
    from services.generation_core.drivers.svg.collect import stage_runtime_profile

    offer = await resolve_offer(request, outline, roles, job,
                                profile=stage_runtime_profile(request, resolve_svg_runtime_profile(source_reflow=False)),
                                checkpoint=_facade._checkpoint, durable=canonical_assets,
                                style_snapshot=style_snapshot,
                                has_source_assets=(any(planned) if source_assets_present is None
                                                   else source_assets_present))
    if not offer.slots:
        return SvgAssetOffer(raster=[{} for _ in planned], vector=[{} for _ in planned],
                             slots=[{} for _ in planned], receipt=dict(offer.receipt))
    async with _facade.async_session_maker() as offer_session:
        projected = await project_offer(offer_session, offer, presentation_id=presentation_id,
                                        count=len(planned))
        await offer_session.commit()
    for index, page in enumerate(projected.raster):
        if not page:
            continue
        planned[index] = {**planned[index], **page}
        if not canonical_assets:
            manifests[index].update(legacy_manifest(page))
    return projected
