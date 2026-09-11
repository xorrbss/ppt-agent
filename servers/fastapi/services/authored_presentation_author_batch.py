"""Pre-author batch implementation extracted from the pipeline orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import inspect
import time
from typing import Any

from services.source_content.browser_readback import BrowserSourceReadbackError
from services.authored_admission_producers import author_html

async def persist_batch_success(index: int, html: str, *, context, runtime) -> str:
    if runtime.is_fallback_slide_html(html):
        raise runtime.AuthoredStageError(
            slide_index=index,
            stage="author",
            code="authored_slide_author_failed",
        )
    try:
        admitted = author_html(
            html, index=index, producer="author_batch",
            bindings=context["source_block_bindings"][index] if context["source_block_bindings"] is not None else None,
            validate=runtime.validate_authored_slide_html, stamp=runtime.stamp_source_blocks,
        )
    except runtime.RendererPolicyError as exc:
        raise runtime.AuthoredStageError(
            slide_index=index,
            stage="author",
            code="authored_slide_author_failed",
            cause=exc,
            retryable=False,
        ) from exc
    context["preauthored_htmls"][index] = admitted
    if context["generation_job"] is not None:
        await runtime._complete_stage_after_external_success(
            job_id=context["generation_job"].id,
            slide_index=index,
            stage="author",
            attempt_token=context["attempt_token"],
            fencing_token=context["fencing_token"],
            html=admitted,
        )
    return admitted


async def run_author_batch(
    indexes: Sequence[int],
    *,
    recovery: bool,
    prior_failures: Mapping[int, Any],
    style_anchor: Any | None = None,
    context,
    runtime,
    persist_batch_success,
):
    selected = tuple(indexes)
    attempt_observers: dict[int, Any] = {}
    attempt_reservers: dict[int, Any] = {}

    for index in selected:
        if context["generation_job"] is not None:
            checkpoint = await runtime.get_checkpoint(
                context["generation_job"].id,
                index,
                attempt_token=context["attempt_token"],
                fencing_token=context["fencing_token"],
            )
            if checkpoint and runtime.should_inject_failure(
                checkpoint, slide_index=index, stage="author"
            ):
                raise runtime.AuthoredStageError(
                    slide_index=index,
                    stage="author",
                    code="authored_injected_failure",
                )
            await runtime.start_stage(
                job_id=context["generation_job"].id,
                slide_index=index,
                stage="author",
                attempt_token=context["attempt_token"],
                fencing_token=context["fencing_token"],
            )

        def observe(ordinal: int, *, slide_index: int = index) -> None:
            context["preauthored_attempt_counts"][slide_index] = max(
                context["preauthored_attempt_counts"].get(slide_index, 0), int(ordinal)
            )

        attempt_observers[index] = observe
        if context["generation_job"] is not None:
            async def reserve(
                ordinal: int, *, slide_index: int = index
            ) -> None:
                await runtime.reserve_provider_attempt(
                    job_id=context["generation_job"].id,
                    slide_index=slide_index,
                    attempt_token=context["attempt_token"],
                    fencing_token=context["fencing_token"],
                    ordinal=ordinal,
                )

            attempt_reservers[index] = reserve

    sources_by_index = {
        index: tuple(
            (context["slide_source_images"][index] or {}).values()
            if context["slide_source_images"] is not None
            else ()
        )
        for index in selected
    }
    started_batch = time.monotonic()
    try:
        with runtime.provider_usage_scope(
            job_id=context["generation_job"].id if context["generation_job"] is not None else None,
            operation="authored_generation",
            stage="author",
            attempt_number=(context["generation_job"].attempt_number if context["generation_job"] else 0),
            slide_index=None,
        ):
            batch_kwargs: dict[str, Any] = {}
            if style_anchor is not None and runtime.accepts_style_anchor(
                runtime.author_planned_batch
            ):
                batch_kwargs["style_anchor"] = style_anchor
            if "normalization_receipts_by_index" in inspect.signature(
                runtime.author_planned_batch
            ).parameters:
                batch_kwargs["normalization_receipts_by_index"] = {
                    index: context["preauthored_normalization_receipts"].setdefault(index, [])
                    for index in selected
                }
            result = await runtime.author_planned_batch(
                context["plan"],
                selected,
                source_images_by_index=sources_by_index,
                require_all_source_images=context["require_all_source_images"],
                recovery=recovery,
                prior_failures=prior_failures,
                raise_on_failure=context["generation_job"] is not None,
                attempt_observers=attempt_observers,
                attempt_reservers=attempt_reservers,
                **batch_kwargs,
            )
        persisted_htmls: dict[int, str] = {}
        persisted_failures = dict(result.failures)
        for index, html in result.htmls.items():
            try:
                persisted_htmls[index] = await persist_batch_success(index, html)
            except BrowserSourceReadbackError as error:
                # The candidate is invalid, not a failed checkpoint write:
                # keep its remaining author call available in this attempt.
                persisted_failures[index] = runtime.AuthoredBatchFailure(
                    error, error.detail, html
                )
            except Exception as error:
                # The provider output itself succeeded. Never spend the
                # slide's recovery call to compensate for a checkpoint I/O
                # failure; fail the attempt with the HTML retained in memory.
                context["preauthored_attempt_counts"][index] = 2
                persisted_failures[index] = runtime.AuthoredBatchFailure(
                    runtime.AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_persist_failed",
                        cause=error,
                        retryable=runtime.is_retryable_exception(error),
                        provider_attempt_count=1 if not recovery else 2,
                    ),
                    "Do not regenerate this accepted provider output.",
                    html,
                )
        return runtime.AuthoredPlannedBatchResult(
            htmls=persisted_htmls,
            failures=persisted_failures,
        )
    finally:
        elapsed = time.monotonic() - started_batch
        # Count a shared provider wait once in deck totals, never once per
        # returned slide.
        if selected:
            context["preauthored_author_work"][selected[0]] = (
                context["preauthored_author_work"].get(selected[0], 0.0) + elapsed
            )
        await runtime.record_local_stage(latency_ms=elapsed * 1000)
