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


async def _pin_owned_svg_style(request, job, *, pin_style, session_maker, owned_job):
    from services.svg_authoring_profile import validate_profile_agreement, validate_quality_recovery_agreement

    if job is None:
        async with session_maker() as authority_session:
            from services.svg_generation_admission import prepare_private_svg_recovery_authority

            await prepare_private_svg_recovery_authority(request, authority_session)
        return pin_style(request)
    async with session_maker() as checkpoint_session:
        current = await owned_job(checkpoint_session, job.id, job.attempt_token,
                                  fencing_token=getattr(job, "_workspace_fencing_token", None))
        admitted = getattr(current, "request_payload", None) or {}
        for key, value in (
            ("_internal_svg_source_binding", getattr(request, "_svg_source_binding", None)),
            ("_internal_svg_style_snapshot", getattr(request, "_svg_style_snapshot", None)),
        ):
            if key in admitted and admitted[key] != value:
                raise RuntimeError("svg_initial_checkpoint_input_conflict")
        frozen = current.svg_style_snapshot
        if frozen is not None:
            validate_profile_agreement(request, frozen)
            validate_quality_recovery_agreement(request, frozen)
            staged = getattr(request, "_svg_style_snapshot", None)
            if staged is not None and staged != frozen:
                raise RuntimeError("svg_style_snapshot_conflict")
            request._svg_style_snapshot = frozen
        from services.svg_generation_admission import prepare_private_svg_recovery_authority

        await prepare_private_svg_recovery_authority(request, checkpoint_session)
        snapshot = pin_style(request)
        validate_profile_agreement(request, snapshot)
        validate_quality_recovery_agreement(request, snapshot)
        if frozen is not None and frozen != snapshot:
            raise RuntimeError("svg_style_snapshot_conflict")
        current.svg_style_snapshot = snapshot
        checkpoint_session.add(current)
        await checkpoint_session.commit()
        return snapshot


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
    )
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
    source_image_registration = []
    candidates = await collect_source_image_candidates(
        request, resolve_authored_style(request.authored_style), presentation_id, session,
        include_document_images=True, registration_report=source_image_registration)
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
    if job is not None:
        await initialize_checkpoints(job_id=job.id, attempt_token=job.attempt_token, roles=roles,
                                     fencing_token=getattr(job, "_workspace_fencing_token", None))
    from services.svg_visual_intent_checkpoint import freeze_visual_intents, intent_contents

    visual_intents = await freeze_visual_intents(
        contents=intent_contents(outline, source_units=source_units,
                                raster_analysis=raster_analysis, raster_pin=raster_pin),
        roles=roles, job=job, historical_payload=existing, checkpoint=_checkpoint,
            trusted_plan=getattr(request, "_svg_stage_plan", None),
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
                        and (getattr(job, "request_payload", None) or {}).get("_svg_recovery_lineage") else None),
                )
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
    semaphore = asyncio.Semaphore(runtime_profile.author_concurrency)
    render_semaphore = asyncio.Semaphore(runtime_profile.render_concurrency)
    return GenerationContext(
        source_reflow=source_reflow, raster_reconstruction=raster_reconstruction,
        raster_analysis=raster_analysis, raster_pin=raster_pin, source_document=source_document,
        source_units=source_units, canonical_assets=canonical_assets, canvas=canvas,
        physical_kwargs=physical_kwargs, snapshot=snapshot, source_font_profile=source_font_profile,
        count=count, roles=roles, manifests=manifests, planned=planned, source_manifest=source_manifest,
        source_pointer=source_pointer, asset_context=asset_context, asset_revision=asset_revision,
        service=service, semaphore=semaphore, render_semaphore=render_semaphore,
        runtime_profile=runtime_profile,
        recovery_cached_first=recovery_cached_first,
        asset_offer=asset_offer,
        visual_intents=visual_intents,
        quality_recovery_source_evidence=getattr(request, "_svg_recovery_source_evidence", None),
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
