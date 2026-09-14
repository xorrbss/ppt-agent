"""Refresh export evidence after a verified Authored HTML edit."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping, Sequence
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_canvas import presentation_canvas_contract
from services.authored_local_fit_admission import (
    local_fit_failure_reason,
    preserve_original_capture,
)
from services.authored_local_fit_deadline import remaining_authored_local_fit_seconds
from services.authored_local_fit_evidence import AuthoredLocalFitRejected
from services.authored_local_fit_policy import (
    html_digest,
    local_fit_policy,
    slide_budget,
)
from services.authored_presentation_generation import _fit_content_revision
from services.authored_review_asset_store import publish_reviewed_png
from services.authored_semantic_candidate import compile_semantic_components
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.remake import (
    republish_authored_semantic_bundle_graph_for_remake,
)
from utils.slide_capture import render_html_to_png_with_metrics


class AuthoredPostEditEvidenceError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


async def _fit_or_preserve_content_revision(
    html: str,
    *,
    slide_index: int,
    policy_revision: str,
    canvas: object,
):
    try:
        return await _fit_content_revision(
            html,
            slide_index=slide_index,
            policy_revision=policy_revision,
            canvas=canvas,
        )
    except AuthoredLocalFitRejected as error:
        remaining = remaining_authored_local_fit_seconds(cap=120)
        if remaining <= 0:
            raise
        budget = slide_budget(60, remaining)
        width_px = int(getattr(canvas, "width_px", 1280))
        height_px = int(getattr(canvas, "height_px", 720))
        render = partial(
            render_html_to_png_with_metrics,
            **(
                {}
                if (width_px, height_px) == (1280, 720)
                else {"width_px": width_px, "height_px": height_px}
            ),
        )
        digest = html_digest(html)
        policy = local_fit_policy(
            author_revision=digest,
            original_html_digest=digest,
            export_width_pt=width_px * 0.75,
            export_height_pt=height_px * 0.75,
            viewport_width=width_px,
            viewport_height=height_px,
            slide_index=slide_index,
            policy_revision=policy_revision,
        )
        return await preserve_original_capture(
            original_html=html,
            policy=policy,
            reason=local_fit_failure_reason(error),
            render=render,
            render_gate=asyncio.Semaphore(1),
            slide_deadline=time.monotonic() + budget["total_seconds"],
            verify_seconds=budget["verify_seconds"],
            deck_scope=None,
            slide_index=slide_index,
        )


async def refresh_authored_post_edit_evidence(
    session: AsyncSession,
    *,
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
    slide_indices: Sequence[int],
    source_revision: int,
    target_revision: int,
    operation_id: uuid.UUID,
) -> None:
    """Bind local-fit and semantic export evidence to an accepted edit."""

    ordered = sorted(slides, key=lambda item: (item.index, item.id))
    selected = tuple(sorted(set(slide_indices)))
    if not selected or any(index < 0 or index >= len(ordered) for index in selected):
        raise AuthoredPostEditEvidenceError("authored_post_edit_slide_indices_invalid")
    if target_revision != source_revision + 1:
        raise AuthoredPostEditEvidenceError("authored_post_edit_revision_invalid")

    theme = presentation.theme
    quality = theme.get("authoredQualityGate") if isinstance(theme, Mapping) else None
    local_fit = quality.get("localFit") if isinstance(quality, Mapping) else None
    raw_receipts = local_fit.get("slides") if isinstance(local_fit, Mapping) else None
    policy_revision = (
        local_fit.get("policyRevision") if isinstance(local_fit, Mapping) else None
    )
    if raw_receipts is not None:
        if (
            not isinstance(theme, Mapping)
            or not isinstance(quality, Mapping)
            or not isinstance(local_fit, Mapping)
            or not isinstance(raw_receipts, list)
            or len(raw_receipts) != len(ordered)
            or not isinstance(policy_revision, str)
            or not policy_revision
        ):
            raise AuthoredPostEditEvidenceError(
                "authored_post_edit_local_fit_evidence_invalid"
            )
        replacements: dict[int, Mapping[str, object]] = {}
        canvas = presentation_canvas_contract(dict(theme))
        for index in selected:
            html = ordered[index].html_content
            if not isinstance(html, str) or not html:
                raise AuthoredPostEditEvidenceError(
                    "authored_post_edit_html_required"
                )
            try:
                fitted = await _fit_or_preserve_content_revision(
                    html,
                    slide_index=index,
                    policy_revision=policy_revision,
                    canvas=canvas,
                )
            except Exception as error:
                raise AuthoredPostEditEvidenceError(
                    "authored_post_edit_local_fit_failed", retryable=True
                ) from error
            if fitted.html != html or fitted.receipt.get("finalHtmlDigest") != html_digest(html):
                raise AuthoredPostEditEvidenceError(
                    "authored_post_edit_local_fit_changed_html", retryable=True
                )
            image_reference = await asyncio.to_thread(
                publish_reviewed_png,
                presentation.id,
                index,
                fitted.png,
            )
            slide = ordered[index]
            slide.content = {
                **(slide.content if isinstance(slide.content, dict) else {}),
                "__authored__": True,
                "image": image_reference,
            }
            slide.properties = {
                **(slide.properties if isinstance(slide.properties, dict) else {}),
                "image": image_reference,
            }
            replacements[index] = fitted.receipt

        next_receipts: list[dict[str, object]] = []
        for index, (receipt, slide) in enumerate(
            zip(raw_receipts, ordered, strict=True)
        ):
            selected_receipt = replacements.get(index, receipt)
            if (
                not isinstance(selected_receipt, Mapping)
                or selected_receipt.get("slideIndex") != index
                or not isinstance(slide.html_content, str)
                or selected_receipt.get("finalHtmlDigest")
                != html_digest(slide.html_content)
            ):
                raise AuthoredPostEditEvidenceError(
                    "authored_post_edit_local_fit_evidence_stale"
                )
            next_receipts.append(dict(selected_receipt))
        presentation.theme = {
            **dict(theme),
            "authoredQualityGate": {
                **dict(quality),
                "localFit": {**dict(local_fit), "slides": next_receipts},
            },
        }

    if presentation.owner_id is None:
        return
    active = await get_active_authored_emission(
        session,
        presentation_id=presentation.id,
        owner_id=presentation.owner_id,
    )
    if active.canonical_mode != "bundleGraph":
        return
    if active.presentation_revision != source_revision:
        raise AuthoredPostEditEvidenceError(
            "authored_post_edit_active_manifest_mismatch"
        )
    htmls = [str(slide.html_content or "") for slide in ordered]
    if any(not html for html in htmls):
        raise AuthoredPostEditEvidenceError("authored_post_edit_html_required")
    await republish_authored_semantic_bundle_graph_for_remake(
        session,
        presentation_id=presentation.id,
        owner_id=presentation.owner_id,
        expected_presentation_revision=source_revision,
        operation_id=operation_id,
        generation_attempt_id=(
            active.manifest.generation_attempt_id if active.manifest is not None else None
        ),
        ordered_slides=ordered,
        htmls=htmls,
        semantic_drafts=compile_semantic_components(htmls),
    )


__all__ = [
    "AuthoredPostEditEvidenceError",
    "refresh_authored_post_edit_evidence",
]
