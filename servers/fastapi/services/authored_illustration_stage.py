"""The authored pipeline's illustrate stage: one slide's picture, checkpointed.

Extracted from ``authored_presentation_pipeline`` so the deck-scoped
illustration plan could be honoured here without growing that module past the
repository's file-size limit. The stage owns three things and nothing else:
whether this slide is allowed a picture at all, generating it, and recording the
durable checkpoint that lets a resumed job skip the provider call.

The allowance is the part worth explaining. Authored slides are written
independently and in parallel, so a slide's own prompt cannot see how many
siblings already claimed a picture; steering the count from inside that prompt
swung between 4/77, 73/77 and 1/77 illustrated slides on one real deck. The deck
now decides once, before authoring, and a slide that was not selected has its
slot withdrawn here even if the authoring model wrote one anyway — because the
prompt is a request and this is the enforcement.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from services.authored_checkpoint_service import (
    AuthoredStageError,
    is_retryable_exception,
    should_inject_failure,
    start_stage,
)
from services.authored_presentation_artifacts import (
    _complete_stage_after_external_success,
)
from models.authored_infographic_plan import AuthoredSlideInfographic
from services.authored_infographic_assets import (
    InfographicAssetContext,
    fill_scene_cutout,
    substitute_figure_icons,
)
from services.authored_checkpoint_html import checkpoint_stage_html
from services.source_images.models import SourceImageCandidate
from services.provider_usage_instrumentation import (
    provider_usage_scope,
    record_local_stage,
)
from utils.authored_illustrations import (
    apply_authored_illustration,
    strip_illustration_slots,
)
from utils.llm_calls.author_slide import validate_authored_slide_html
from utils.renderer_isolation import RendererPolicyError


def _finalize(html: str, index: int) -> str:
    """Admit the finished slide, mapping a policy rejection onto the stage."""
    try:
        return validate_authored_slide_html(html, finalized=True)
    except RendererPolicyError as exc:
        raise AuthoredStageError(
            slide_index=index,
            stage="illustrate",
            code="authored_slide_illustrate_failed",
            cause=exc,
            retryable=False,
        ) from exc


async def illustrate_slide(
    *,
    html: str,
    checkpoint: Any,
    index: int,
    style: Any,
    image_service: Any,
    slide_content: str,
    illustration_allowed: bool,
    generation_job: Any,
    attempt_token: Optional[str],
    source_images: Optional[Mapping[str, SourceImageCandidate]] = None,
    # The slide's planned figure and the deck's icon-pack context. Both default
    # to absent: a deck without a plan, or one that cannot spend a transparent
    # PNG, runs the stage exactly as it did before figures existed.
    infographic: Optional[AuthoredSlideInfographic] = None,
    infographic_assets: Optional[InfographicAssetContext] = None,
) -> tuple[str, Any, float]:
    """Return this slide's finalized HTML, its checkpoint, and time spent.

    ``illustration_allowed`` is the deck plan's answer for this slide, already
    defaulted to True by the caller when the deck has no plan — which is the
    behaviour this fork had before plans existed.

    ``source_images`` is the exact candidate set the authoring call was offered
    for this slide, keyed by asset id; ``None`` means source-image reuse is not
    in play and ``data-asset-id`` stays inert. The plan does not veto it: the
    plan exists to budget provider calls, and placing a picture the user already
    uploaded costs none.
    """
    illustrated_html = (
        checkpoint_stage_html(checkpoint, stage="illustrate")
        if checkpoint and checkpoint.illustrate_status in ("completed", "skipped")
        else None
    )
    if illustrated_html is not None:
        return _finalize(illustrated_html, index), checkpoint, 0.0

    if checkpoint and should_inject_failure(
        checkpoint, slide_index=index, stage="illustrate"
    ):
        raise AuthoredStageError(
            slide_index=index,
            stage="illustrate",
            code="authored_injected_failure",
        )
    if generation_job is not None:
        await start_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="illustrate",
            attempt_token=attempt_token,
            fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
        )
    illustration_work = 0.0
    # A slide the deck excluded loses its slot through the same removal every
    # generation failure already uses, so it can never leave a src-less <img>
    # box behind, and no provider call is made for it.
    image_generation_disabled = bool(
        image_service is not None
        and getattr(image_service, "is_image_generation_disabled", False)
    )
    illustrated_html = (
        html
        if illustration_allowed
        and (not image_generation_disabled or source_images is not None)
        else strip_illustration_slots(html)
    )
    generating = (
        image_service is not None
        and illustration_allowed
        and not image_generation_disabled
    )
    # A slide with an offered picture still has an `<img data-asset-id>` to
    # resolve even when this deck generates no illustrations at all, so the
    # stage runs for either reason. Resolution is also what drops an id the
    # slide was never offered, which is the feature's security boundary.
    substituting = source_images is not None
    # A scene's object cutout is tried first, on the same withdrawn-slot copy:
    # when it lands, the ordinary stage below finds no slot left to fill; when
    # it does not, the slot is still there and the ordinary fill-or-strip rule
    # applies unchanged.
    if generating and infographic_assets is not None:
        illustrated_html = await fill_scene_cutout(
            illustrated_html,
            slide=infographic,
            context=infographic_assets,
            slide_index=index,
        )
        if not (getattr(style, "illustration_prompt", None) or "").strip():
            # The cutout was this deck's only fill: without a style prompt the
            # ordinary stage has nothing to generate, so a slot the cutout did
            # not take is withdrawn the same way a failed generation is.
            illustrated_html = strip_illustration_slots(illustrated_html)
    skipped = not (generating or substituting)
    if generating or substituting:
        illustration_started = time.monotonic()
        with provider_usage_scope(
            job_id=generation_job.id if generation_job is not None else None,
            operation="authored_generation",
            stage="illustrate",
            attempt_number=(
                generation_job.attempt_number if generation_job else 0
            ),
            slide_index=index,
        ):
            illustration_kwargs: dict[str, Any] = {}
            if generation_job is not None:
                illustration_kwargs["raise_on_failure"] = True
            # Omitted rather than passed as None so a deck without the feature
            # reaches substitution byte-identically to before it.
            if source_images is not None:
                illustration_kwargs["source_images"] = source_images
            if not generating:
                # Source-image substitution remains available, but a missing
                # or invalid source cannot fall through to a disabled/absent
                # provider and turn an otherwise valid deck into retries.
                illustration_kwargs["generate_fallback"] = False
            try:
                # The slide's outline markdown is the authoritative source for
                # any label a text-capable image model is allowed to draw — it
                # must never invent one.
                illustrated_html = await apply_authored_illustration(
                    # The withdrawn-slot copy, so a slide the deck excluded
                    # cannot have its slot generated back here.
                    illustrated_html,
                    style,
                    image_service,
                    slide_index=index,
                    slide_content=slide_content,
                    **illustration_kwargs,
                )
            except Exception as exc:
                if generating and not substituting:
                    # Generated artwork is an optional enhancement. A provider
                    # outage must not discard an otherwise valid authored
                    # slide; remove the unresolved slot and publish the
                    # text/diagram composition. Source-image substitution is
                    # intentionally excluded because its validation is part of
                    # the caller-owned source contract and must stay fail-closed.
                    illustrated_html = strip_illustration_slots(html)
                    skipped = True
                else:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="illustrate",
                        code="authored_slide_illustrate_failed",
                        cause=exc,
                        retryable=is_retryable_exception(exc),
                    ) from exc
            finally:
                illustration_work = time.monotonic() - illustration_started
                await record_local_stage(latency_ms=illustration_work * 1000)

    # Figure icons are not budgeted by the illustration plan — they replace
    # the library's built-in SVG, never add a picture — so they are placed
    # whenever the deck may spend a transparent PNG at all. This runs after
    # the contract check the author stage already passed, on purpose: the
    # checker must keep seeing the library's own markup.
    if infographic_assets is not None and infographic is not None:
        icons_started = time.monotonic()
        illustrated_html = await substitute_figure_icons(
            illustrated_html, slide=infographic, context=infographic_assets
        )
        illustration_work += time.monotonic() - icons_started

    illustrated_html = _finalize(illustrated_html, index)
    if generation_job is not None:
        checkpoint = await _complete_stage_after_external_success(
            job_id=generation_job.id,
            slide_index=index,
            stage="illustrate",
            attempt_token=attempt_token,
            fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
            html=illustrated_html,
            skipped=skipped,
        )
    return illustrated_html, checkpoint, illustration_work


__all__ = ["illustrate_slide"]
