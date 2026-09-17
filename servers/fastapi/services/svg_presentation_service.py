"""SVG generation with existing durable jobs and fenced per-page checkpoints."""
from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
import json
from pathlib import Path
import re
import uuid

from sqlalchemy import delete, select

from models.presentation_and_path import PresentationPathAndEditPath
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation import PresentationModel, PRESENTATION_LIFECYCLE_PUBLISHED
from models.sql.slide import SlideModel
from services.authored_checkpoint_artifacts import atomic_write, checkpoint_root
from services.authored_checkpoint_fence import owned_job
from services.authored_checkpoint_service import initialize_checkpoints, begin_publish, complete_publish
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_canvas import request_canvas_contract, request_canvas_metadata, merge_presentation_canvas
from services.authored_presentation_artifacts import _save_slide_pngs, authored_title
from services.database import async_session_maker
from services.provider_usage_instrumentation import provider_usage_scope
from services.svg_generation_admission import require_svg_creation_admission, pin_svg_style
from utils.datetime_utils import get_current_utc_datetime


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


_NUMERIC_CLAIM = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{2,})(?:\s?(?:%|년|월|일|억|만|개|명|건|원|배|점|GB|MB|KB))?"
    r"|(?<![\w.])\d+(?:\s?(?:%|년|월|일|억|만|개|명|건|원|배|점|GB|MB|KB))"
)


def _required_numeric_claims(content: str) -> list[str]:
    """Protect concrete figures without requiring verbatim prose or bullet labels."""
    return list(dict.fromkeys(match.group(0).strip() for match in _NUMERIC_CLAIM.finditer(content)))


async def _checkpoint(job, index, *, payload=None):
    if job is None:
        return None
    async with async_session_maker() as session:
        active = await owned_job(session, job.id, job.attempt_token,
                                 fencing_token=getattr(job, "_workspace_fencing_token", None))
        row = await session.scalar(select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job.id,
            AuthoredSlideCheckpoint.slide_index == index,
        ))
        if row is None:
            raise RuntimeError("svg_checkpoint_missing")
        if payload is not None:
            row.svg_checkpoint = payload
            row.attempt_token = job.attempt_token
            row.author_status = "completed" if payload.get("svg_content") else "running"
            row.illustrate_status = "skipped"
            row.render_status = "completed" if payload.get("png_sha256") else "pending"
            row.author_completed_at = get_current_utc_datetime() if payload.get("svg_content") else None
            row.render_completed_at = get_current_utc_datetime() if payload.get("png_sha256") else None
            row.failed_stage = None
            row.error = None
            session.add(row)
            await session.flush()
            states = await session.scalars(select(AuthoredSlideCheckpoint.render_status).where(
                AuthoredSlideCheckpoint.job_id == job.id))
            active.completed_slides = sum(state == "completed" for state in states)
            active.current_stage = "render" if payload.get("svg_content") else "author"
            session.add(active)
        result = dict(row.svg_checkpoint or {})
        await session.commit()
        return result


def _write_png(png: bytes) -> dict:
    digest = sha256(png).hexdigest()
    relative = Path("svg") / digest[:2] / f"{digest}.png"
    atomic_write(checkpoint_root() / relative, png)
    return {"png_sha256": digest, "png_path": relative.as_posix()}


def _read_png(payload: dict) -> bytes | None:
    digest = payload.get("png_sha256", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return None
    path = checkpoint_root() / "svg" / digest[:2] / f"{digest}.png"
    if not path.is_file():
        return None
    png = path.read_bytes()
    return png if sha256(png).hexdigest() == digest else None


def _legacy_source_image_manifest(offered: dict) -> dict:
    """Build and validate the existing inline format without provider state."""
    from services.svg_asset_manifest import decide_preservation_policy
    from services.svg_image_derivative import build_model_preview
    from utils.llm_calls.author_slide_svg import preflight_svg_source_images

    manifest = {}
    for slot, candidate in offered.items():
        if candidate.path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("svg_asset_source_byte_budget_exceeded")
        data = candidate.path.read_bytes()
        if sha256(data).hexdigest() != candidate.digest.removeprefix("sha256:"):
            raise ValueError("svg_source_image_digest_mismatch")
        # Apply the old raster limit to a bounded derivative, never directly
        # to a valid original. The source file and source digest stay intact.
        preview = build_model_preview(data, max_bytes=min(1536 * 1024, (2 * 1024 * 1024) // max(1, len(offered))),
                                      max_edge=4096)
        data, mime = preview.payload, preview.media_type
        # Preserve the legacy required-image behavior through the central
        # policy. The future slot writer will supply occurrence decisions.
        policy = decide_preservation_policy(selected=True, preservation_class="supporting",
                                            source_preservation_required=True)
        manifest[slot] = {"data_url": f"data:{mime};base64," + base64.b64encode(data).decode(),
                          "description": candidate.semantic_description or candidate.source_heading or "Source image",
                          "required": policy.required, "allowOmit": policy.allow_omit,
                          "allowExcludeOnFailure": policy.allow_exclude_on_failure,
                          "policyVersion": policy.policy_version, "asset_id": candidate.asset_id,
                          "source_digest": candidate.digest,
                          "model_preview": preview.metadata(),
                          "digest": "sha256:" + sha256(data).hexdigest()}
    return preflight_svg_source_images(manifest)


async def generate_svg_presentation(request, presentation_id, outline, language,
                                    sql_session, generation_job=None):
    from services.authored_presentation_service import _AUTHORED_JOB_SEMAPHORE
    async with _AUTHORED_JOB_SEMAPHORE:
        return await _generate(request, presentation_id, outline, language, sql_session, generation_job)


async def _generate(request, presentation_id, outline, language, session, job):
    from services.svg_contract import finalize_svg
    from services.svg_renderer import render_svg_with_metrics
    from utils.llm_calls.author_slide_svg import author_slide_svg, preflight_svg_style_images
    from services.authored_source_images import collect_source_image_candidates, plan_slide_source_images
    from utils.authored_styles import resolve_authored_style
    from services.svg_generation_assets import slot_writer_enabled

    require_svg_creation_admission(request)
    canonical_assets = slot_writer_enabled(
        strategy=getattr(request, "strategy", None),
        durable=job is not None and job.workspace_operation_id is not None)
    canvas = request_canvas_contract(request)
    snapshot = pin_svg_style(request)
    if job is not None:
        async with async_session_maker() as checkpoint_session:
            current = await owned_job(checkpoint_session, job.id, job.attempt_token,
                                      fencing_token=getattr(job, "_workspace_fencing_token", None))
            if current.svg_style_snapshot is not None and current.svg_style_snapshot != snapshot:
                raise RuntimeError("svg_style_snapshot_conflict")
            current.svg_style_snapshot = snapshot
            checkpoint_session.add(current)
            await checkpoint_session.commit()
    count = len(outline.slides)
    if not count:
        raise ValueError("svg_outline_empty")
    if job is not None:
        # Existing inline checkpoints must finish with their original contract.
        # A takeover must never mix inline pages and canonical asset pages.
        async with async_session_maker() as checkpoint_session:
            existing = await checkpoint_session.scalar(select(AuthoredSlideCheckpoint.svg_checkpoint).where(
                AuthoredSlideCheckpoint.job_id == job.id,
                AuthoredSlideCheckpoint.svg_checkpoint.is_not(None)).limit(1))
        if existing:
            canonical_assets = (existing.get("schema_version") == "svg-slide-checkpoint/v2"
                                or bool(existing.get("source_manifest_ref")))
    candidates = await collect_source_image_candidates(
        request, resolve_authored_style(request.authored_style), presentation_id, session,
        include_document_images=True)
    planned = plan_slide_source_images([item.content for item in outline.slides], candidates)
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
    source_manifest = source_pointer = asset_context = asset_revision = None
    if canonical_assets:
        from services.artifact_store import configured_public_artifact_service
        from services.svg_generation_assets import publication_context, prepare_source_manifest
        service = configured_public_artifact_service()
        seed = await _checkpoint(job, 0) or {}
        async with async_session_maker() as asset_session:
            asset_context, asset_revision = await publication_context(asset_session, job)
            source_manifest, source_pointer = await prepare_source_manifest(asset_session, service,
                context=asset_context, revision=asset_revision, presentation_id=presentation_id,
                planned=planned, saved_pointer=seed.get("source_manifest_ref"),
                source_preservation_required=bool(request._source_content_authored_reflow)
                    or bool(re.search(
                        r"(?:이미지|사진|도표|그림).{0,40}(?:모두|전부|필수|반드시|보존|빠짐없이)"
                        r"|(?:모든|전체|필수|반드시).{0,20}(?:이미지|사진|도표|그림)"
                        r"|(?:preserve|retain|keep|must include).{0,40}(?:image|picture|chart)"
                        r"|(?:all|every)\s+(?:source\s+)?(?:image|picture|chart)",
                        request.instructions or "", re.IGNORECASE)))
            await asset_session.commit()
        seed["source_manifest_ref"] = source_pointer
        await _checkpoint(job, 0, payload=seed)
        manifests = [{slot.slot_id: slot.model_dump(mode="json", by_alias=True)
                      for slot in source_manifest.slots if slot.slot_id in offered} for offered in planned]
    # Provider/render workers share the job-level gate with HTML; each page owns
    # separate transactions so concurrent calls never share an AsyncSession.
    semaphore = asyncio.Semaphore(3)
    fatal_error = None

    async def page(index):
        async with semaphore:
            if fatal_error is not None:
                raise fatal_error
            content = outline.slides[index].content
            input_digest = _digest({"content": content, "style": snapshot, "index": index,
                                    "count": count, "instructions": request.instructions,
                                    "language": language, "preset": request.codex_generation_preset})
            input_digest = _digest({"page": input_digest, "assets": manifests[index]})
            saved = await _checkpoint(job, index) or {}
            if saved.get("input_digest") and saved.get("input_digest") != input_digest:
                raise RuntimeError("svg_checkpoint_input_conflict")
            png = _read_png(saved)
            if png is not None and saved.get("svg_content") and saved.get("quality"):
                return saved, png
            payload = saved or {"schema_version": "svg-slide-checkpoint/v1", "source_format": "svg",
                                "input_digest": input_digest, "attempts": []}
            payload.setdefault("attempts", [])
            payload.setdefault("input_digest", input_digest)
            if canonical_assets:
                payload.update(schema_version="svg-slide-checkpoint/v2", source_format="svg",
                               source_manifest_ref=source_pointer, source_manifest_digest=source_manifest.digest)
            svg = payload.get("svg_content")
            # At most one model correction. Persist the ordinal before purchase;
            # an uncertain provider attempt is not silently bought again on resume.
            if svg is None and payload.get("inflight"):
                raise AuthoredStageError(slide_index=index, stage="author",
                                        code="svg_provider_attempt_uncertain", retryable=False)
            hint = None
            while svg is not None or len(payload["attempts"]) < 2:
                ordinal = len(payload["attempts"]) if svg is None else len(payload["attempts"]) - 1
                if svg is None:
                    provider_ordinal = ordinal + len(payload.get("provider_failures", [])) + 1
                    offered_images = manifests[index]
                    if canonical_assets:
                        from services.svg_generation_assets import author_images
                        async with async_session_maker() as asset_session:
                            offered_images = await author_images(asset_session, service, context=asset_context,
                                manifest=source_manifest, page_index=index)
                    async def before_dispatch():
                        # A checkpoint is a recovery witness, not a separate
                        # purchase ledger. Admission still owns reservation.
                        payload["inflight"] = provider_ordinal
                        await _checkpoint(job, index, payload=payload)
                    with provider_usage_scope(job_id=job.id if job else None,
                                              operation="authored_generation",
                                              stage="author" if ordinal == 0 else "repair",
                                              slide_index=index,
                                              attempt_number=job.attempt_number if job else 0):
                        from utils.provider_overload import ProviderOutcomeError
                        try:
                            authored = await author_slide_svg(
                                content=content, style_snapshot=snapshot, role=roles[index],
                                index=index, n=count, instructions=request.instructions,
                                correction_hint=hint,
                                receipt_attempt_ordinal=provider_ordinal,
                                source_images=offered_images,
                                before_dispatch=before_dispatch,
                                canonical_source_images=canonical_assets,
                            )
                        except ProviderOutcomeError as error:
                            if error.code != "public_provider_overload" or not error.recovery_allowed:
                                raise
                            failures = payload.setdefault("provider_failures", [])
                            failures.append({"ordinal": provider_ordinal, "code": error.code})
                            payload["inflight"] = None
                            if payload.get("last_valid_svg"):
                                svg = payload["svg_content"] = payload["last_valid_svg"]
                                payload["repair_unavailable"] = True
                                await _checkpoint(job, index, payload=payload)
                                continue
                            await _checkpoint(job, index, payload=payload)
                            if len(failures) >= 2:
                                raise
                            await asyncio.sleep(min(60., max(30., error.cooldown_seconds or 30.)))
                            continue
                    svg = authored.svg_content
                    payload["attempts"].append({"raw_response": authored.raw_response,
                                                "receipt": authored.receipt})
                    payload["inflight"] = None
                    payload["svg_content"] = svg
                    await _checkpoint(job, index, payload=payload)
                try:
                    if payload["attempts"][-1]["receipt"].get("validation_error"):
                        raise ValueError(payload["attempts"][-1]["receipt"]["validation_error"])
                    final = finalize_svg(svg, index=index, n=count, width=canvas.width_px,
                                         height=canvas.height_px, style_snapshot=snapshot,
                                         source_manifest={slot: {"required": item["policy"]["required"],
                                             "allowOmit": item["policy"]["allowOmit"]}
                                             for slot, item in manifests[index].items()} if canonical_assets else None)
                    materialized = None
                    if canonical_assets:
                        from services.svg_generation_assets import screen_placement
                        # Candidate SVG is durable before image rendering. A
                        # derivative failure resumes here without rebuying it.
                        payload["svg_content"] = final
                        await _checkpoint(job, index, payload=payload)
                        try:
                            async with async_session_maker() as asset_session:
                                envelope, materialized = await screen_placement(asset_session, service,
                                    context=asset_context, revision=asset_revision, manifest=source_manifest,
                                    source_pointer=source_pointer, svg=final, page_index=index,
                                    candidates=planned[index], width=canvas.width_px, height=canvas.height_px,
                                    saved_envelope=payload.get("svgAssetManifest"))
                                await asset_session.commit()
                            payload["svgAssetManifest"] = envelope
                            await _checkpoint(job, index, payload=payload)
                        except (ValueError, OSError) as error:
                            raise AuthoredStageError(slide_index=index, stage="render",
                                code="svg_asset_screen_derivative_failed", retryable=False) from error
                    try:
                        rendered = await render_svg_with_metrics(final, width=canvas.width_px,
                                                                 height=canvas.height_px, instructions=request.instructions,
                                                                 required_text=_required_numeric_claims(content),
                                                                 materialized_svg=materialized)
                    except ValueError as error:
                        if canonical_assets:
                            raise AuthoredStageError(slide_index=index, stage="render",
                                code="svg_asset_capture_failed", retryable=False) from error
                        raise
                except ValueError as error:
                    hint = str(error)[:600]
                    payload["attempts"][-1]["validation_error"] = hint
                    if ordinal == 0:
                        payload["initial_quality"] = {
                            "stage": "first_response", "passed": False, "verified": False,
                            "failures": [{"kind": "validation_failed", "detail": hint}],
                        }
                    payload["svg_content"] = None
                    svg = None
                    await _checkpoint(job, index, payload=payload)
                    if ordinal == 1:
                        raise AuthoredStageError(slide_index=index, stage="render",
                                                code="svg_validation_failed", retryable=False) from error
                    continue
                payload["attempts"][-1]["quality"] = rendered.quality
                if ordinal == 0:
                    payload["initial_quality"] = {
                        **rendered.quality, "stage": "first_response",
                    }
                payload["last_valid_svg"] = final
                if rendered.quality.get("passed") is False and ordinal == 0 and not payload.get("repair_unavailable"):
                    hint = json.dumps(rendered.quality, ensure_ascii=False)[:3000]
                    payload["svg_content"] = None
                    svg = None
                    await _checkpoint(job, index, payload=payload)
                    continue
                if payload.get("repair_unavailable"):
                    rendered.quality["passed"] = False
                    rendered.quality.setdefault("failures", []).append({"kind": "provider_repair_unavailable"})
                if canonical_assets:
                    import xml.etree.ElementTree as ET
                    used = {node.get("href") for node in ET.fromstring(final).iter()
                            if node.tag.rsplit("}", 1)[-1] == "image"}
                    omitted = sorted(set(manifests[index]) - used)
                    rendered.quality["sourceImages"] = {
                        "offered": sorted(manifests[index]), "used": sorted(used), "omitted": omitted,
                    }
                    if omitted:
                        rendered.quality["passed"] = False
                        rendered.quality.setdefault("failures", []).append({
                            "kind": "source_image_omitted", "slotIds": omitted,
                        })
                payload.update(svg_content=final, source_digest=sha256(final.encode()).hexdigest(),
                               quality=rendered.quality, **_write_png(rendered.png))
                await _checkpoint(job, index, payload=payload)
                return payload, rendered.png
            raise AuthoredStageError(slide_index=index, stage="author",
                                    code="svg_repair_budget_exhausted", retryable=False)

    async def tracked_page(index):
        nonlocal fatal_error
        try:
            return await page(index)
        except AuthoredStageError as error:
            if fatal_error is error:
                raise
            fatal_error = fatal_error or error
            if job is not None and error.code != "authored_attempt_lost":
                from services.authored_checkpoint_service import fail_stage
                await fail_stage(job_id=job.id, slide_index=index, stage=error.stage,
                    attempt_token=job.attempt_token, error=error,
                    request_id=getattr(request, "_request_id", None),
                    fencing_token=getattr(job, "_workspace_fencing_token", None))
            raise
        except Exception as error:
            fatal_error = fatal_error or error
            raise

    tasks = [asyncio.create_task(tracked_page(i)) for i in range(count)]
    try:
        pages = await asyncio.gather(*tasks, return_exceptions=True)
        if fatal_error is not None:
            raise fatal_error
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return await _publish(request, presentation_id, outline, language, session, job, snapshot, roles, pages)


async def _publish(request, presentation_id, outline, language, session, job, snapshot, roles, pages):
    from services.account_deletion_service import require_active_account_generation
    from services.compatibility_migration.admission import stamp_new_svg_presentation
    from services.presentation_publication_service import publish_presentation_revision
    from utils.owner_context import get_current_owner_id

    owner = get_current_owner_id()
    if owner is None:
        raise RuntimeError("svg_owner_required")
    job_id = job.id if job else None
    attempt_token = job.attempt_token if job else None
    operation_id = job.workspace_operation_id if job else None
    fencing_token = getattr(job, "_workspace_fencing_token", None) if job else None
    await session.rollback()  # release read transaction before ordered publication locks
    await require_active_account_generation(session, user_id=owner)
    if job:
        await begin_publish(session, job_id=job_id, attempt_token=attempt_token,
                            fencing_token=fencing_token)
    presentation = await session.scalar(select(PresentationModel).where(
        PresentationModel.id == presentation_id, PresentationModel.owner_id == owner).with_for_update())
    if presentation is None:
        presentation = PresentationModel(id=presentation_id, owner_id=owner, content=request.content,
                                         n_slides=len(pages), language=language or "ko")
        session.add(presentation)
        await session.flush()
    expected_revision = presentation.revision
    if presentation.mode not in (None, "authored", "svg") or (expected_revision > 0 and not presentation.is_svg()):
        raise RuntimeError("svg_document_mode_conflict")
    image_refs = await asyncio.to_thread(_save_slide_pngs, presentation_id,
                                         [png for _, png in pages], attempt_token)
    stamp_new_svg_presentation(presentation)
    presentation.title = authored_title(request, outline)
    presentation.n_slides = len(pages)
    presentation.outlines = outline.model_dump(mode="json")
    presentation.content = request.content
    presentation.instructions = request.instructions
    presentation.language = language or "ko"
    presentation.layout = None
    presentation.structure = None
    presentation.reference_snapshot_ids = [str(x) for x in request.reference_snapshot_ids] or None
    quality = [payload["quality"] for payload, _ in pages]
    initial_quality = [payload.get("initial_quality", payload["quality"]) for payload, _ in pages]
    review = [i for i, item in enumerate(quality)
              if item.get("verified") is not True or item.get("passed") is not True]
    presentation.theme = merge_presentation_canvas({
        **(presentation.theme or {}), "mode": "svg", "style": request.authored_style,
        "svg": {"schemaVersion": "svg-document/v1", "styleSnapshot": snapshot,
                "qualityStatus": "review_required" if review else "passed",
                "reviewPages": review, "quality": quality,
                "initialQuality": initial_quality,
                "codexGenerationPreset": request.codex_generation_preset},
    }, request_canvas_metadata(request))
    presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
    await session.execute(delete(SlideModel).where(SlideModel.presentation == presentation_id))
    for index, (payload, _) in enumerate(pages):
        session.add(SlideModel(presentation=presentation_id, owner_id=owner,
                              layout_group="svg", layout="svg:content", index=index,
                              content={"__svg__": True, "role": roles[index], "source": outline.slides[index].content,
                                       **({"svgAssetManifest": payload["svgAssetManifest"]} if payload.get("svgAssetManifest") else {}),
                                       "svgReceipt": {key: value for key, value in payload.items() if key not in {"svg_content", "attempts", "svgAssetManifest"}}},
                              svg_content=payload["svg_content"], source_format="svg",
                              properties={"image": image_refs[index]}, speaker_note=outline.slides[index].content))
    session.add(presentation)
    await session.flush()
    # complete_publish still requires the running operation; terminalize the
    # public operation only after its linked job transition in this transaction.
    if job:
        await complete_publish(session, job_id=job_id, attempt_token=attempt_token,
                               fencing_token=fencing_token)
    result = {"presentation_id": str(presentation_id), "source_format": "svg",
              "quality_status": "review_required" if review else "passed",
              "edit_path": f"/presentation?id={presentation_id}", "path": ""}
    await publish_presentation_revision(session, presentation_id=presentation_id,
                                       expected_revision=expected_revision, operation_id=operation_id,
                                       attempt_token=attempt_token, result=result,
                                       operation_kind="presentation.generate")
    await session.commit()
    return PresentationPathAndEditPath(presentation_id=presentation_id, path="",
                                       edit_path=f"/presentation?id={presentation_id}")
