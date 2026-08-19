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
from typing import Any, Optional

from services.authored_checkpoint_service import (
    AuthoredStageError,
    is_retryable_exception,
    should_inject_failure,
    start_stage,
)
from services.authored_presentation_artifacts import (
    _complete_stage_after_external_success,
)
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
) -> tuple[str, Any, float]:
    """Return this slide's finalized HTML, its checkpoint, and time spent.

    ``illustration_allowed`` is the deck plan's answer for this slide, already
    defaulted to True by the caller when the deck has no plan — which is the
    behaviour this fork had before plans existed.
    """
    illustrated_html = (
        checkpoint.illustrated_html
        if checkpoint
        and checkpoint.illustrate_status in ("completed", "skipped")
        and checkpoint.illustrated_html
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
        )
    illustration_work = 0.0
    # A slide the deck excluded loses its slot through the same removal every
    # generation failure already uses, so it can never leave a src-less <img>
    # box behind, and no provider call is made for it.
    illustrated_html = html if illustration_allowed else strip_illustration_slots(html)
    generating = image_service is not None and illustration_allowed
    skipped = not generating
    if generating:
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
            try:
                # The slide's outline markdown is the authoritative source for
                # any label a text-capable image model is allowed to draw — it
                # must never invent one.
                illustrated_html = await apply_authored_illustration(
                    html,
                    style,
                    image_service,
                    slide_index=index,
                    slide_content=slide_content,
                    **({} if generation_job is None else {"raise_on_failure": True}),
                )
            except Exception as exc:
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

    illustrated_html = _finalize(illustrated_html, index)
    if generation_job is not None:
        checkpoint = await _complete_stage_after_external_success(
            job_id=generation_job.id,
            slide_index=index,
            stage="illustrate",
            attempt_token=attempt_token,
            html=illustrated_html,
            skipped=skipped,
        )
    return illustrated_html, checkpoint, illustration_work


__all__ = ["illustrate_slide"]
