# ruff: noqa: F401
"""Detached one-page re-author preview and explicit CAS publication.

This service deliberately has no deck/subset request shape. Quality review may
surface this action, but it cannot invoke it: one explicit path-level slide index
is required for both preview and publication.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from constants.user_messages import (
    UserMessageKey,
    UserMessageLike,
    set_record_message,
    user_message,
)
from models.presentation_from_template import AuthoredPageRemakePreviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import presentation_version_service as version_service
from services.authored_exact_provenance import (
    ExactProvenanceError,
    verify_exact_provenance,
)
from services.authored_exact_source_snapshot import (
    ExactSourceSnapshotError,
    materialize_exact_source_style,
)
from services.authored_quality_review_service import (
    _ANALYZE_AND_FIX_LOCKS,
    _render_review_html,
    _saved_brand,
    _saved_role,
)
from services.authored_deck_chapter_plan import load_chapter_plan
from services.authored_deck_illustration_plan import load_illustration_plan
from services.authored_deck_infographic_plan import load_infographic_plan
from services.authored_infographic_assets import (
    build_infographic_asset_context,
    deck_has_picture_source,
)
from services.authored_header_contract_service import (
    load_header_contract,
    page_number_mode_for,
)
from services.authored_presentation_generation import _fit_content_revision
from services.authored_local_fit_policy import html_digest
from services.authored_canvas import presentation_canvas_contract
from services.authored_review_asset_store import publish_reviewed_png
from services.authored_semantic_candidate import (
    compile_authored_semantic_source,
    compile_semantic_components,
)
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.remake import (
    republish_authored_semantic_bundle_graph_for_remake,
)
from services.image_generation_service import ImageGenerationService
from services.database import async_session_maker
from services.workspace_authored_async_admission import admit_workspace_authored_task
from services.slidespec_artifact_store import ACTIVE_POINTER_KEY, _json_cas_predicate
from services.svg_page_remake import preview_svg, publish_svg_candidate
from utils.authored_dom_quality import inspect_authored_dom
from utils.asset_directory_utils import get_images_directory
from utils.authored_styles import (
    AuthoredExactReconstructionUnavailableError,
    resolve_authored_style,
)
from utils.datetime_utils import get_current_utc_datetime
from services.generation_core.authoring.author_deck import prepare_authored_deck
from services.generation_core.authoring.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    authored_semantic_dual_output_mode,
    normalize_reference_mode,
)
from services.generation_core.authoring.author_vision_qa import revise_authored_deck
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
from utils.hashing import sha256_hex, sha256_prefixed

LOGGER = logging.getLogger(__name__)
PREVIEW_KIND = "authored_page_remake_preview"


class AuthoredPageRemakeError(RuntimeError):
    """Stable fail-closed error suitable for a task or operation result."""

    def __init__(self, code: str, *, stage: str, retryable: bool = False) -> None:
        self.code = code
        self.stage = stage
        self.retryable = retryable
        super().__init__(code)


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256_prefixed(encoded)


def authored_page_source_digest(slide: SlideModel) -> str:
    """Bind a preview to every persisted field replaced by publication."""

    return _canonical_digest(
        {
            "slide_id": str(slide.id),
            "index": slide.index,
            "html": slide.html_content,
            **({"svg": slide.svg_content, "source_format": "svg"} if slide.is_svg() else {}),
            "content": slide.content,
            "properties": slide.properties,
        }
    )


async def _set_task(
    session: AsyncSession,
    task: AsyncPresentationGenerationTaskModel,
    *,
    status: str,
    message: UserMessageLike,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    task.status = status
    set_record_message(task, message)
    task.data = data
    task.error = error
    task.updated_at = get_current_utc_datetime()
    session.add(task)
    await session.commit()


async def _load_deck(
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int | None = None,
) -> tuple[PresentationModel, list[SlideModel]]:
    """Load the deck and refuse a target this service cannot re-author.

    The requirement is on the page being remade, not on its neighbours.  A deck
    that carries one blank placeholder is still a deck of authored pages, and
    demanding that every slide be authored let a single blank disable remake for
    every other page.  Everything downstream is index-addressed - the plan role,
    the outline alignment, the per-slide reference images - so a non-authored
    sibling does not affect the candidate for ``slide_index``.

    ``slide_index`` is validated only when it is in range; callers keep raising
    their own out-of-range errors so the reported codes stay unchanged.
    """

    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise AuthoredPageRemakeError(
            "presentation_not_found", stage="source_validation"
        )
    rows = await session.execute(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )
    slides = list(rows.scalars().all())
    unsupported_target = (
        slide_index is not None
        and 0 <= slide_index < len(slides)
        and not (slides[slide_index].is_authored() or slides[slide_index].is_svg())
    )
    if not slides or not (presentation.is_authored() or presentation.is_svg()) or unsupported_target:
        raise AuthoredPageRemakeError(
            "authored_page_remake_unsupported_presentation",
            stage="source_validation",
        )
    return presentation, slides


async def _authoring_context(
    session: AsyncSession,
    presentation: PresentationModel,
    slides: list[SlideModel],
) -> tuple[Any, Any, str, tuple[bytes, ...] | None, Any]:
    outline = presentation.get_presentation_outline()
    if outline is None or not outline.slides or len(outline.slides) != len(slides):
        raise AuthoredPageRemakeError(
            "authored_page_remake_outline_missing", stage="source_validation"
        )

    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    canvas = presentation_canvas_contract(theme)
    style_id = str(theme.get("style") or "default")
    provenance = await session.get(AuthoredExactProvenance, presentation.id)
    exact_source_images: tuple[bytes, ...] | None = None
    try:
        if provenance is not None:
            provenance_payload = (
                provenance.provenance if isinstance(provenance.provenance, dict) else {}
            )
            private_style_id = provenance_payload.get("style_id")
            if not isinstance(private_style_id, str) or private_style_id != style_id:
                raise ExactProvenanceError("style identity changed")
            style = resolve_authored_style(private_style_id)
            snapshot_id = provenance_payload.get("source_snapshot_id")
            if snapshot_id is not None:
                if not isinstance(snapshot_id, str):
                    raise ExactProvenanceError("source snapshot is invalid")
                style = await materialize_exact_source_style(
                    style,
                    snapshot_id,
                    expected_slide_count=len(slides),
                )
            exact_source_images = verify_exact_provenance(
                provenance_payload,
                style,
                expected_slide_count=len(slides),
            )
            saved_mode = EXACT_RECONSTRUCTION
        else:
            legacy_mode = theme.get("reference_mode")
            if (
                isinstance(legacy_mode, str)
                and normalize_reference_mode(legacy_mode) == EXACT_RECONSTRUCTION
            ):
                raise ExactProvenanceError("exact provenance is missing")
            style = resolve_authored_style(style_id)
            if (
                normalize_reference_mode(
                    getattr(style, "reference_mode", STYLE_REFERENCE)
                )
                == EXACT_RECONSTRUCTION
            ):
                raise ExactProvenanceError("exact provenance is missing")
            saved_mode = STYLE_REFERENCE
    except (
        AuthoredExactReconstructionUnavailableError,
        ExactProvenanceError,
        ExactSourceSnapshotError,
    ) as error:
        raise AuthoredPageRemakeError(
            "authored_page_remake_exact_source_unavailable",
            stage="source_validation",
        ) from error

    # Remaking one page must reproduce the deck's own frozen chrome. Rebuilding
    # the plan from defaults instead would give the remade slide a header the
    # rest of the deck does not have, and a page number it deliberately dropped.
    header_contract = load_header_contract(presentation.theme)
    plan = prepare_authored_deck(
        outline,
        _saved_brand(presentation),
        style,
        page_number_mode=page_number_mode_for(header_contract),
        header_contract=header_contract,
        # The chapter plan is indexed against the persisted outline, which is
        # the one loaded above, so the remade page is handed the same eyebrow
        # prefix and the same role it was authored with.
        chapter_plan=load_chapter_plan(presentation.theme),
        illustration_plan=load_illustration_plan(presentation.theme),
        infographic_plan=load_infographic_plan(presentation.theme),
        canvas=canvas,
    )
    plan_mode = normalize_reference_mode(getattr(plan, "reference_mode", saved_mode))
    if plan_mode != saved_mode:
        raise AuthoredPageRemakeError(
            "authored_page_remake_reference_mode_mismatch",
            stage="source_validation",
        )
    return plan, style, plan_mode, exact_source_images, canvas


def _issue_summary(report: Any) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in report.issues]


def _local_fit_policy_revision(theme: object) -> str | None:
    if not isinstance(theme, Mapping):
        return None
    quality = theme.get("authoredQualityGate")
    local_fit = quality.get("localFit") if isinstance(quality, Mapping) else None
    revision = local_fit.get("policyRevision") if isinstance(local_fit, Mapping) else None
    return revision if isinstance(revision, str) and revision else None


def _theme_with_remake_local_fit_receipt(
    *,
    theme: object,
    slides: list[SlideModel],
    slide_index: int,
    candidate_html: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace one renewable local-fit receipt without relabeling generation evidence."""

    if not isinstance(theme, Mapping):
        raise AuthoredPageRemakeError(
            "authored_page_remake_local_fit_evidence_missing", stage="post_verify"
        )
    quality = theme.get("authoredQualityGate")
    local_fit = quality.get("localFit") if isinstance(quality, Mapping) else None
    raw_receipts = local_fit.get("slides") if isinstance(local_fit, Mapping) else None
    policy_revision = (
        local_fit.get("policyRevision") if isinstance(local_fit, Mapping) else None
    )
    ordered = sorted(slides, key=lambda item: (item.index, item.id))
    if (
        not isinstance(quality, Mapping)
        or not isinstance(local_fit, Mapping)
        or not isinstance(raw_receipts, list)
        or len(raw_receipts) != len(ordered)
        or receipt.get("slideIndex") != slide_index
        or receipt.get("policyRevision") != policy_revision
        or receipt.get("finalHtmlDigest") != html_digest(candidate_html)
    ):
        raise AuthoredPageRemakeError(
            "authored_page_remake_local_fit_evidence_invalid", stage="post_verify"
        )

    next_receipts: list[dict[str, Any]] = []
    for index, (current, slide) in enumerate(zip(raw_receipts, ordered, strict=True)):
        if (
            not isinstance(current, Mapping)
            or current.get("slideIndex") != index
        ):
            raise AuthoredPageRemakeError(
                "authored_page_remake_local_fit_evidence_invalid",
                stage="post_verify",
            )
        if index != slide_index and (
            not isinstance(slide.html_content, str)
            or current.get("finalHtmlDigest") != html_digest(slide.html_content)
        ):
            raise AuthoredPageRemakeError(
                "authored_page_remake_local_fit_evidence_stale",
                stage="post_verify",
            )
        next_receipts.append(dict(receipt if index == slide_index else current))

    next_local_fit = {**dict(local_fit), "slides": next_receipts}
    next_quality = {**dict(quality), "localFit": next_local_fit}
    return {**dict(theme), "authoredQualityGate": next_quality}


def _png_matches_content_addressed_reference(reference: str, png: bytes) -> bool:
    filename = reference.replace("\\", "/").rsplit("/", 1)[-1]
    return filename.endswith(f"_{sha256_hex(png)}.png")


async def run_authored_page_remake_preview(
    presentation_id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePreviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    """Generate and validate one detached candidate; never mutate a live slide."""

    started = time.perf_counter()
    # Deliberately unlocked, so a batch can preview four pages at once. A
    # process-local lock could not exclude publication anyway — the workspace
    # worker has its own OS process — so a deck that moves underneath is caught
    # by the candidate's source digest at publish time, not by exclusion here.
    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise AuthoredPageRemakeError(
            "authored_page_remake_slide_out_of_range", stage="source_validation"
        )
    target = slides[slide_index]
    if target.is_svg():

        return await preview_svg(presentation, slides, slide_index, request, task, session)
    if not target.html_content:
        raise AuthoredPageRemakeError(
            "authored_page_remake_source_html_missing", stage="source_validation"
        )
    outline = presentation.get_presentation_outline()
    if outline is None:
        raise AuthoredPageRemakeError(
            "authored_page_remake_outline_missing", stage="source_validation"
        )
    plan, style, reference_mode, exact_sources, canvas = await _authoring_context(
        session, presentation, slides
    )

    await _set_task(
        session,
        task,
        status="processing",
        message=user_message(UserMessageKey.REMAKE_PAGE_RUNNING, page=slide_index + 1),
    )
    source_html = str(target.html_content)
    source_digest = authored_page_source_digest(target)
    try:
        baseline_png, baseline_metrics = await _render_review_html(
            source_html, canvas=canvas
        )
    except Exception as error:
        raise AuthoredPageRemakeError(
            "authored_page_remake_source_render_failed",
            stage="source_render",
            retryable=True,
        ) from error
    baseline_report = inspect_authored_dom(
        source_html,
        baseline_metrics,
        reference_mode=reference_mode,
        viewport_width=canvas.width_px,
        viewport_height=canvas.height_px,
    )
    issue_details = "; ".join(issue.detail for issue in baseline_report.issues[:4])
    instruction = (request.instruction or "").strip()
    detail = (
        "Rebuild this entire slide as a fresh composition while preserving every "
        "source fact, label, number, and semantic relationship. Resolve overlap, "
        "occlusion, clipping, and unsafe spacing."
    )
    if issue_details:
        detail += f" Detected visual evidence: {issue_details}"
    if instruction:
        detail += f" User note: {instruction}"
    critique = SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type=(
                    "overlap"
                    if any(
                        issue.category == "overlap"
                        for issue in baseline_report.issues
                    )
                    else "other"
                ),
                severity="high",
                detail=detail[:200],
            )
        ],
    )

    role = _saved_role(target, plan.roles[slide_index])
    reference_kwargs: dict[str, Any] = {}
    plan_prefixes = getattr(plan, "chapter_prefixes", ())
    if plan_prefixes:
        # Remaking one page re-authors it, and the deck's other 63 pages already
        # open their eyebrow with a chapter. Handing over the same prefix is what
        # keeps this one from being the page that disagrees.
        reference_kwargs["chapter_prefixes"] = [plan_prefixes[slide_index]]
    if reference_mode == EXACT_RECONSTRUCTION:
        planned = getattr(plan, "reference_images", None)
        if planned is None or len(planned) != len(slides):
            raise AuthoredPageRemakeError(
                "authored_page_remake_exact_source_unavailable",
                stage="source_validation",
            )
        reference_kwargs["reference_images_by_slide"] = [planned[slide_index]]
        if exact_sources is None or len(exact_sources) != len(slides):
            raise AuthoredPageRemakeError(
                "authored_page_remake_exact_source_unavailable",
                stage="source_validation",
            )
    else:
        # A one-page remake receives the original deck decision at the target's
        # persisted index.  An absent plan intentionally leaves this keyword
        # absent, preserving legacy per-slide illustration behavior.
        illustration_slots = getattr(plan, "illustration_slots", ())
        if illustration_slots:
            reference_kwargs["illustration_slots"] = [
                illustration_slots[slide_index]
            ]
        infographic_briefs = getattr(plan, "infographic_briefs", ())
        if infographic_briefs:
            reference_kwargs["infographic_briefs"] = [
                infographic_briefs[slide_index]
            ]
            # The remade page gets the same pack icons and scene cutout its
            # siblings carry, from the same per-style pack.
            reference_kwargs["infographic_assets"] = build_infographic_asset_context(
                style=style, brand=plan.brand
            )
        if deck_has_picture_source(style):
            reference_kwargs["image_service"] = ImageGenerationService(
                get_images_directory()
            )

    try:
        result = await revise_authored_deck(
            [source_html],
            [baseline_png],
            [outline.slides[slide_index].content],
            [role],
            plan.brand,
            plan.design_system,
            max_cycles=1,
            style=style,
            slide_indices=[slide_index],
            total_slides=len(slides),
            initial_critiques=[critique],
            return_details=True,
            force_full_reauthor_indices=[0],
            **reference_kwargs,
        )
    except Exception as error:
        raise AuthoredPageRemakeError(
            "authored_page_remake_generation_failed",
            stage="generation",
            retryable=True,
        ) from error
    if len(result) != 4:
        raise AuthoredPageRemakeError(
            "authored_page_remake_candidate_invalid", stage="generation"
        )
    htmls, pngs, changed, details = result
    detail_payload = details[0] if details and isinstance(details[0], dict) else {}
    if not htmls or not pngs:
        raise AuthoredPageRemakeError(
            "authored_page_remake_candidate_invalid", stage="generation"
        )
    # The reviser swallows model and transport failures and hands the source
    # back unchanged, so "no new page was produced" must not be read as "the
    # page is bad".  A failed attempt is transient and worth retrying; a page
    # that simply came back identical is a real result the user can judge.
    attempt_failed = changed != [0] or (
        bool(detail_payload.get("attempted"))
        and not detail_payload.get("succeeded")
    )
    if attempt_failed:
        LOGGER.warning(
            "Authored page remake generation attempt failed "
            "(slide_index=%s, path=%s, detail=%s)",
            slide_index,
            detail_payload.get("path"),
            detail_payload.get("failure_detail"),
        )
        raise AuthoredPageRemakeError(
            "authored_page_remake_generation_failed",
            stage="generation",
            retryable=True,
        )
    candidate_html = str(htmls[0])
    candidate_png = bytes(pngs[0])
    policy_revision = _local_fit_policy_revision(presentation.theme)
    if policy_revision is not None:
        try:
            fitted = await _fit_content_revision(
                candidate_html,
                slide_index=slide_index,
                policy_revision=policy_revision,
                canvas=canvas,
            )
        except Exception as error:
            raise AuthoredPageRemakeError(
                "authored_page_remake_candidate_local_fit_failed",
                stage="candidate_render",
                retryable=True,
            ) from error
        candidate_html = fitted.html
        candidate_png = fitted.png
    semantic_source = None
    if authored_semantic_dual_output_mode() == "declared":
        try:
            semantic_source = compile_authored_semantic_source(
                candidate_html,
                slide_index=slide_index,
            )
        except (TypeError, ValueError) as error:
            raise AuthoredPageRemakeError(
                "authored_page_remake_semantic_validation_failed",
                stage="semantic_validation",
                retryable=True,
            ) from error
    # An identical re-author is a legitimate outcome, not a defect: it happens
    # naturally when the page was already remade.  Report it so the user can
    # decide whether to keep it or retry, rather than hiding the result.
    candidate_change = (
        "unchanged" if candidate_html == source_html else "changed"
    )
    if policy_revision is not None:
        verified_png, candidate_metrics = candidate_png, fitted.metrics
    else:
        try:
            verified_png, candidate_metrics = await _render_review_html(
                candidate_html, canvas=canvas
            )
        except Exception as error:
            raise AuthoredPageRemakeError(
                "authored_page_remake_candidate_render_failed",
                stage="candidate_render",
                retryable=True,
            ) from error
    candidate_report = inspect_authored_dom(
        candidate_html,
        candidate_metrics,
        reference_mode=reference_mode,
        viewport_width=canvas.width_px,
        viewport_height=canvas.height_px,
    )
    # A candidate that generated and rendered is always offered to the user.
    # Deterministic DOM findings are advisory evidence for that human decision;
    # hiding a renderable candidate behind a local sensor removes the only way
    # to judge it.  Telemetry coverage travels with the verdict so a warning
    # without issues (missing/partial browser probe) stays explainable.
    candidate_dom_status = (
        "warning" if candidate_report.needs_attention else "passed"
    )
    if candidate_dom_status == "warning":
        LOGGER.info(
            "Authored page remake candidate needs attention "
            "(slide_index=%s, telemetry=%s, issues=%d)",
            slide_index,
            candidate_report.telemetry_status,
            len(candidate_report.issues),
        )
    image_reference = publish_reviewed_png(
        presentation_id, slide_index, verified_png or candidate_png
    )
    candidate_digest = _canonical_digest(
        {"html": candidate_html, "image": image_reference, "semanticSource": semantic_source}
    )
    await _set_task(
        session,
        task,
        status="completed",
        message=user_message(UserMessageKey.REMAKE_PAGE_READY, page=slide_index + 1),
        data={
            "kind": PREVIEW_KIND,
            "phase": "preview_ready",
            "presentation_id": str(presentation_id),
            "slide_id": str(target.id),
            "slide_index": slide_index,
            "source_digest": source_digest,
            "candidate_digest": candidate_digest,
            "candidate_html": candidate_html,
            "candidate_image": image_reference,
            **({"semantic_source": semantic_source} if semantic_source is not None else {}),
            "baseline_issues": _issue_summary(baseline_report),
            "candidate_issues": _issue_summary(candidate_report),
            "validation": {
                "source_render": "passed",
                "candidate_render": "passed",
                "candidate_dom": candidate_dom_status,
                "candidate_dom_telemetry": candidate_report.telemetry_status,
                "candidate_change": candidate_change,
                "reference_mode": reference_mode,
            },
            "generation": {
                "path": detail_payload.get("path", "full_fallback"),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "llm_scope": "single_page",
            },
            "publish": {
                "status": "not_started",
                "rollback_available": False,
            },
        },
    )


def _failed_preview_breadcrumb(
    presentation_id: uuid.UUID, slide_index: int
) -> dict[str, Any]:
    """Identify a failed attempt without implying a usable candidate.

    History is listed by reading task payloads, so an attempt that stores nothing
    is invisible afterwards.  The phase is deliberately not ``preview_ready``:
    publication validates that field and must keep rejecting this task.
    """

    return {
        "kind": PREVIEW_KIND,
        "phase": "failed",
        "presentation_id": str(presentation_id),
        "slide_index": slide_index,
    }


async def _run_preview_background(
    presentation_id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePreviewRequest,
    task_id: str,
) -> None:
    async with async_session_maker() as session:
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            return
        try:
            await run_authored_page_remake_preview(
                presentation_id, slide_index, request, task, session
            )
        except AuthoredPageRemakeError as error:
            await session.rollback()
            await _set_task(
                session,
                task,
                status="error",
                message=user_message(UserMessageKey.REMAKE_PAGE_FAILED),
                data=_failed_preview_breadcrumb(presentation_id, slide_index),
                error={
                    "detail": error.code,
                    "stage": error.stage,
                    "retryable": error.retryable,
                },
            )
        except Exception as error:  # noqa: BLE001 - background task boundary
            LOGGER.warning(
                "Authored page remake preview failed (%s)",
                type(error).__name__,
            )
            await session.rollback()
            await _set_task(
                session,
                task,
                status="error",
                message=user_message(UserMessageKey.REMAKE_PAGE_FAILED),
                data=_failed_preview_breadcrumb(presentation_id, slide_index),
                error={
                    "detail": "authored_page_remake_failed",
                    "stage": "unknown",
                    "retryable": True,
                },
            )


async def queue_authored_page_remake_preview(
    presentation_id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePreviewRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> AsyncPresentationGenerationTaskModel:
    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise HTTPException(
            status_code=400, detail="Slide index is outside the presentation"
        )
    if not slides[slide_index].html_content and not slides[slide_index].is_svg():
        raise HTTPException(status_code=400, detail="Slide has no authored HTML")
    # The stored workspace request is the restart-safe source of truth.  The
    # legacy task remains the polling/read model consumed by the current UI.
    return await admit_workspace_authored_task(
        session,
        presentation_id=presentation_id,
        operation_kind="slide.regenerate.preview",
        expected_revision=int(presentation.revision),
        command=user_message(UserMessageKey.REMAKE_PAGE_COMMAND, page=slide_index + 1),
        context={
            "slide_index": slide_index,
            "instruction": request.instruction,
        },
        task_id_prefix="workspace-page-preview",
        pending_message=user_message(
            UserMessageKey.REMAKE_PAGE_QUEUED, page=slide_index + 1
        ),
    )


def _validated_preview_data(
    task: AsyncPresentationGenerationTaskModel,
    *,
    presentation: PresentationModel,
    slide: SlideModel,
    slide_index: int,
) -> dict[str, Any]:
    data = task.data if isinstance(task.data, dict) else None
    if task.status != "completed" or data is None:
        raise AuthoredPageRemakeError(
            "authored_page_remake_preview_incomplete", stage="publish_validation"
        )
    if (
        data.get("kind") != PREVIEW_KIND
        or data.get("phase") != "preview_ready"
        or data.get("presentation_id") != str(presentation.id)
        or data.get("slide_id") != str(slide.id)
        or data.get("slide_index") != slide_index
        or task.owner_id != presentation.owner_id
    ):
        raise AuthoredPageRemakeError(
            "authored_page_remake_preview_target_mismatch",
            stage="publish_validation",
        )
    if data.get("source_digest") != authored_page_source_digest(slide):
        raise AuthoredPageRemakeError(
            "authored_page_remake_source_changed", stage="publish_validation"
        )
    if not isinstance(data.get("candidate_html"), str) or not isinstance(
        data.get("candidate_image"), str
    ):
        raise AuthoredPageRemakeError(
            "authored_page_remake_candidate_invalid", stage="publish_validation"
        )
    expected_candidate_digest = _canonical_digest(
        {
            "html": data["candidate_html"],
            "image": data["candidate_image"],
            "semanticSource": data.get("semantic_source"),
        }
    )
    if data.get("candidate_digest") != expected_candidate_digest:
        raise AuthoredPageRemakeError(
            "authored_page_remake_candidate_digest_mismatch",
            stage="publish_validation",
        )
    if authored_semantic_dual_output_mode() == "declared":
        semantic_source = data.get("semantic_source")
        if not isinstance(semantic_source, dict):
            raise AuthoredPageRemakeError(
                "authored_page_remake_semantic_missing",
                stage="publish_validation",
            )
        try:
            rebuilt = compile_authored_semantic_source(
                str(data["candidate_html"]),
                slide_index=slide_index,
            )
        except (TypeError, ValueError) as error:
            raise AuthoredPageRemakeError(
                "authored_page_remake_semantic_validation_failed",
                stage="publish_validation",
            ) from error
        if rebuilt != semantic_source:
            raise AuthoredPageRemakeError(
                "authored_page_remake_semantic_mismatch",
                stage="publish_validation",
            )
    return data


class _MasterOwnedBodyTextParser(HTMLParser):
    """Extract text only from the canonical writable body section."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.matches = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        classes = str(attributes.get("class") or "").split()
        if self.depth:
            self.depth += 1
        elif (
            tag == "section"
            and "body" in classes
            and str(attributes.get("data-body-slot") or "").startswith("slot:")
        ):
            self.matches += 1
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and data.strip():
            self.parts.append(data.strip())


async def validated_authored_page_remake_candidate_body(
    *,
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int,
    preview_task_id: str,
) -> str:
    """Validate the existing preview CAS and expose only model-owned body text."""

    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise AuthoredPageRemakeError(
            "authored_page_remake_slide_out_of_range", stage="publish_validation"
        )
    task = await session.get(AsyncPresentationGenerationTaskModel, preview_task_id)
    if task is None:
        raise AuthoredPageRemakeError(
            "authored_page_remake_preview_not_found", stage="publish_validation"
        )
    data = _validated_preview_data(
        task,
        presentation=presentation,
        slide=slides[slide_index],
        slide_index=slide_index,
    )
    parser = _MasterOwnedBodyTextParser()
    parser.feed(str(data["candidate_html"]))
    body = "\n".join(parser.parts).strip()
    if parser.matches != 1 or not body:
        raise AuthoredPageRemakeError(
            "authored_vnext_regenerate_body_contract_invalid",
            stage="publish_validation",
        )
    return body


# ---- Folded back from authored_page_remake_publish_impl.py (bind_runtime split) ----

async def publish_authored_page_remake(
    *,
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int,
    preview_task_id: str,
    snapshot: bool = True,
    operation_id: uuid.UUID | None = None,
    attempt_token: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Stage one preview publication in the caller-owned transaction."""

    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise AuthoredPageRemakeError(
            "authored_page_remake_slide_out_of_range", stage="publish_validation"
        )
    slide = slides[slide_index]
    task = await session.get(AsyncPresentationGenerationTaskModel, preview_task_id)
    if task is None:
        raise AuthoredPageRemakeError(
            "authored_page_remake_preview_not_found", stage="publish_validation"
        )
    if slide.is_svg():

        return await publish_svg_candidate(session, presentation, slides, slide_index, task, snapshot=snapshot,
            operation_id=operation_id, attempt_token=attempt_token, expected_revision=expected_revision)
    data = _validated_preview_data(
        task,
        presentation=presentation,
        slide=slide,
        slide_index=slide_index,
    )
    candidate_html = str(data["candidate_html"])
    candidate_image = str(data["candidate_image"])

    expected_theme = presentation.theme
    policy_revision = _local_fit_policy_revision(expected_theme)
    next_theme: dict[str, Any] | None = None
    # Re-render immediately before mutation. A candidate that can no longer be
    # rendered is never published; deterministic DOM findings stay advisory so an
    # explicitly approved preview remains applicable. Policy-backed decks pass
    # through the same bounded local-fit evidence producer used by generation.
    try:
        canvas = presentation_canvas_contract(
            expected_theme if isinstance(expected_theme, dict) else {}
        )
        if policy_revision is not None:
            fitted = await _fit_content_revision(
                candidate_html,
                slide_index=slide_index,
                policy_revision=policy_revision,
                canvas=canvas,
            )
            if fitted.html != candidate_html:
                raise AuthoredPageRemakeError(
                    "authored_page_remake_candidate_changed_after_preview",
                    stage="post_verify",
                    retryable=True,
                )
            if not _png_matches_content_addressed_reference(
                candidate_image, fitted.png
            ):
                raise AuthoredPageRemakeError(
                    "authored_page_remake_candidate_capture_changed",
                    stage="post_verify",
                    retryable=True,
                )
            _png, metrics = fitted.png, fitted.metrics
            next_theme = _theme_with_remake_local_fit_receipt(
                theme=expected_theme,
                slides=slides,
                slide_index=slide_index,
                candidate_html=candidate_html,
                receipt=fitted.receipt,
            )
        else:
            _png, metrics = await _render_review_html(
                candidate_html, canvas=canvas
            )
    except AuthoredPageRemakeError:
        raise
    except Exception as error:
        raise AuthoredPageRemakeError(
            "authored_page_remake_post_verify_failed",
            stage="post_verify",
            retryable=True,
        ) from error
    plan, _style, reference_mode, _exact_sources, _canvas = await _authoring_context(
        session, presentation, slides
    )
    del plan
    post_report = inspect_authored_dom(
        candidate_html,
        metrics,
        reference_mode=reference_mode,
        viewport_width=canvas.width_px,
        viewport_height=canvas.height_px,
    )
    post_verify_status = "warning" if post_report.needs_attention else "passed"
    if post_verify_status == "warning":
        LOGGER.info(
            "Authored page remake publication carries DOM warnings "
            "(slide_index=%s, telemetry=%s, issues=%d)",
            slide_index,
            post_report.telemetry_status,
            len(post_report.issues),
        )

    expected_html = slide.html_content
    expected_content = slide.content
    expected_properties = slide.properties
    next_properties = (
        dict(expected_properties) if isinstance(expected_properties, dict) else {}
    )
    next_properties.pop(ACTIVE_POINTER_KEY, None)
    next_properties["image"] = candidate_image
    next_content = dict(expected_content) if isinstance(expected_content, dict) else {}
    next_content.update(
        {
            "__authored__": True,
            "image": candidate_image,
        }
    )

    if snapshot:
        # A batch takes one snapshot for the whole transaction instead: per-page
        # snapshots there would evict the deck's real undo history.
        await version_service.snapshot_slides(
            session,
            presentation_id,
            [item.model_dump(mode="json") for item in slides],
            label=user_message(UserMessageKey.VERSION_LABEL_PAGE_REMAKE, page=slide_index + 1),
            force=True,
        )
    result = await session.execute(
        update(SlideModel)
        .where(
            SlideModel.id == slide.id,
            SlideModel.html_content == expected_html,
            _json_cas_predicate(session, SlideModel.content, expected_content),
            _json_cas_predicate(session, SlideModel.properties, expected_properties),
        )
        .values(
            html_content=candidate_html,
            content=next_content,
            properties=next_properties,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise AuthoredPageRemakeError(
            "authored_page_remake_publish_cas_conflict", stage="publish"
        )
    if next_theme is not None:
        theme_result = await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == presentation.id,
                _json_cas_predicate(session, PresentationModel.theme, expected_theme),
            )
            .values(theme=next_theme)
            .execution_options(synchronize_session=False)
        )
        if theme_result.rowcount != 1:
            raise AuthoredPageRemakeError(
                "authored_page_remake_publish_cas_conflict", stage="publish"
            )
    await session.flush()
    readback = await session.execute(
        select(
            SlideModel.html_content, SlideModel.content, SlideModel.properties
        ).where(SlideModel.id == slide.id)
    )
    row = readback.one_or_none()
    if (
        row is None
        or row.html_content != candidate_html
        or row.content != next_content
        or row.properties != next_properties
    ):
        raise AuthoredPageRemakeError(
            "authored_page_remake_publish_readback_mismatch", stage="publish"
        )
    if next_theme is not None:
        theme_readback = await session.execute(
            select(PresentationModel.theme).where(
                PresentationModel.id == presentation.id
            )
        )
        if theme_readback.scalar_one_or_none() != next_theme:
            raise AuthoredPageRemakeError(
                "authored_page_remake_publish_readback_mismatch", stage="publish"
            )
        await session.refresh(presentation, attribute_names=["theme"])

    next_data = dict(data)
    next_data["phase"] = "published"
    next_data["publish"] = {
        "status": "published",
        "post_verify": post_verify_status,
        "post_verify_telemetry": post_report.telemetry_status,
        "post_verify_issues": _issue_summary(post_report),
        "rollback_available": True,
    }
    task.data = next_data
    set_record_message(
        task, user_message(UserMessageKey.REMAKE_PAGE_APPLIED, page=slide_index + 1)
    )
    task.updated_at = get_current_utc_datetime()
    session.add(task)
    await session.flush()
    return {
        "kind": "authored_page_remake",
        "status": "published",
        "presentation_id": str(presentation_id),
        "slide_id": str(slide.id),
        "slide_index": slide_index,
        "preview_task_id": preview_task_id,
        "source_digest": data["source_digest"],
        "candidate_digest": data["candidate_digest"],
        "post_verify_status": post_verify_status,
        "rollback_available": True,
        "canvas_refresh_required": True,
        "canvas_refresh_slide_index": slide_index,
    }


async def publish_authored_page_remake_legacy(
    *,
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int,
    preview_task_id: str,
) -> dict[str, Any]:
    lock = _ANALYZE_AND_FIX_LOCKS.setdefault(presentation_id, asyncio.Lock())
    async with lock:
        try:
            result = await publish_authored_page_remake(
                session=session,
                presentation_id=presentation_id,
                slide_index=slide_index,
                preview_task_id=preview_task_id,
            )
            presentation, slides = await _load_deck(
                session, presentation_id, slide_index
            )
            for item in slides:
                await session.refresh(item)
            if presentation.owner_id is not None:
                active_semantic = await get_active_authored_emission(
                    session,
                    presentation_id=presentation_id,
                    owner_id=presentation.owner_id,
                )
                if active_semantic.canonical_mode == "bundleGraph":
                    expected_revision = presentation.revision
                    operation_id = uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "presenton:authored-remake:"
                        f"{presentation_id}:{preview_task_id}:{expected_revision + 1}",
                    )
                    htmls = [str(item.html_content or "") for item in slides]
                    await republish_authored_semantic_bundle_graph_for_remake(
                        session,
                        presentation_id=presentation_id,
                        owner_id=presentation.owner_id,
                        expected_presentation_revision=expected_revision,
                        operation_id=operation_id,
                        generation_attempt_id=None,
                        ordered_slides=slides,
                        htmls=htmls,
                        semantic_drafts=compile_semantic_components(htmls),
                    )
                    revision_result = await session.execute(
                        update(PresentationModel)
                        .where(
                            PresentationModel.id == presentation_id,
                            PresentationModel.revision == expected_revision,
                        )
                        .values(
                            revision=PresentationModel.revision + 1,
                            updated_at=get_current_utc_datetime(),
                        )
                    )
                    if revision_result.rowcount != 1:
                        raise AuthoredPageRemakeError(
                            "authored_page_remake_publish_cas_conflict",
                            stage="publish",
                        )
                    result["presentation_revision"] = expected_revision + 1
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


def remake_http_error(error: AuthoredPageRemakeError) -> HTTPException:
    if error.code in {
        "presentation_not_found",
        "authored_page_remake_preview_not_found",
    }:
        status = 404
    elif error.code in {
        "authored_page_remake_source_changed",
        "authored_page_remake_publish_cas_conflict",
    }:
        status = 409
    else:
        status = 400
    return HTTPException(
        status_code=status,
        detail={"code": error.code, "stage": error.stage},
    )
