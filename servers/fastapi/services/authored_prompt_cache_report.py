"""C2: per-deck prompt-cache hit-rate summary for the authoring stage.

``docs/authored-sol-quality-first-design-20260908.md`` (amendment C2). Bench
run 48 showed only 5 of 25 authoring calls reporting a nonzero
``cached_tokens``, while the deck-invariant prefix every call sends is meant
to be ~9.2k tokens. Nothing before this module made that hit rate visible per
deck; it was only found by reading the run's receipts by hand.

This module adds no new table: the author-stage receipts already carry
``cached_tokens`` and ``input_tokens`` per call
(``models.sql.provider_usage_receipt.ProviderUsageReceipt``), written by
``services.provider_usage_instrumentation.record_provider_call``. The
summary is a pure aggregation over that per-call data, split from the DB
read so it is testable with plain fixtures.

The log line has a fixed, greppable shape so a bench run can pull it
straight out of the backend log:

    [authored] prompt_cache calls=N hits=H cached_tokens=T input_tokens=I

``calls`` is every receipted author-stage call for the job (initial and
recovery attempts alike); ``hits`` is how many of those reported
``cached_tokens > 0``; ``cached_tokens``/``input_tokens`` are deck totals.

Wiring note: the end of the author stage is inside
``services/authored_presentation_pipeline.py``, which this workstream does
not own. The call-out needed there (after the initial-authoring and recovery
passes, while ``generation_job`` is still in scope) is:

    await log_author_stage_prompt_cache_summary(generation_job.id)

See ``utils.llm_calls.author_prompt_split`` for the investigation into why
hits are sporadic, and ``services.authored_wave_scheduler`` /
``utils.llm_calls.author_prompt_split.invariant_prefix_source`` for the
``AUTHORED_PROMPT_CACHE_WARMUP_ENABLED`` fix this measurement is meant to
validate on a future bench run.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PromptCacheCallUsage:
    """The two receipt fields this summary needs from one authoring call."""

    cached_tokens: int | None
    input_tokens: int | None


@dataclass(frozen=True, slots=True)
class PromptCacheSummary:
    """One deck's author-stage prompt-cache accounting."""

    calls: int
    hits: int
    cached_tokens: int
    input_tokens: int


def summarize_prompt_cache_usage(
    calls: Iterable[PromptCacheCallUsage],
) -> PromptCacheSummary:
    """Aggregate per-call receipt usage into one deck-level summary.

    A call with ``cached_tokens`` absent or zero counts toward ``calls`` but
    not ``hits``; absent counters contribute zero to the sums rather than
    being dropped, so a mix of reported and unavailable usage still yields a
    meaningful ``calls`` total.
    """

    materialized = tuple(calls)
    hits = sum(1 for call in materialized if (call.cached_tokens or 0) > 0)
    cached_tokens = sum(call.cached_tokens or 0 for call in materialized)
    input_tokens = sum(call.input_tokens or 0 for call in materialized)
    return PromptCacheSummary(
        calls=len(materialized),
        hits=hits,
        cached_tokens=cached_tokens,
        input_tokens=input_tokens,
    )


def format_prompt_cache_summary(summary: PromptCacheSummary) -> str:
    """Render the fixed, greppable log line for one deck's summary."""

    return (
        f"[authored] prompt_cache calls={summary.calls} hits={summary.hits} "
        f"cached_tokens={summary.cached_tokens} input_tokens={summary.input_tokens}"
    )


def log_prompt_cache_summary(
    calls: Iterable[PromptCacheCallUsage],
    *,
    log: logging.Logger | None = None,
) -> PromptCacheSummary:
    """Summarize and log one deck's author-stage prompt-cache usage.

    Returns the summary so a caller (or a test) can assert on the numbers
    without re-parsing the log line.
    """

    summary = summarize_prompt_cache_usage(calls)
    (log or logger).info(format_prompt_cache_summary(summary))
    return summary


async def author_stage_prompt_cache_usage(
    job_id: uuid.UUID,
) -> tuple[PromptCacheCallUsage, ...]:
    """Fetch one job's author-stage receipts as prompt-cache call usage.

    Scoped to ``operation="authored_generation"`` and ``stage="author"``,
    which is exactly the first-pass authoring calls: repair/remake calls are
    not receipted under this stage today (M0 tracks that gap separately).
    """

    from sqlmodel import select

    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from services.database import async_session_maker

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(
                    ProviderUsageReceipt.cached_tokens,
                    ProviderUsageReceipt.input_tokens,
                ).where(
                    ProviderUsageReceipt.job_id == job_id,
                    ProviderUsageReceipt.operation == "authored_generation",
                    ProviderUsageReceipt.stage == "author",
                )
            )
        ).all()
    return tuple(
        PromptCacheCallUsage(cached_tokens=cached_tokens, input_tokens=input_tokens)
        for cached_tokens, input_tokens in rows
    )


async def log_author_stage_prompt_cache_summary(
    job_id: uuid.UUID,
) -> PromptCacheSummary:
    """Fetch, summarize and log one job's author-stage prompt-cache usage.

    This is the one call the author stage's end should make; see the module
    docstring for the exact call-out and why it lives here rather than in the
    pipeline module that would otherwise call it.
    """

    calls = await author_stage_prompt_cache_usage(job_id)
    return log_prompt_cache_summary(calls)


async def log_author_stage_prompt_cache_summary_safely(generation_job: object) -> None:
    """The author stage's call-out: log the summary, never fail the deck for it."""

    job_id = getattr(generation_job, "id", None)
    if job_id is None:
        return
    try:
        await log_author_stage_prompt_cache_summary(job_id)
    except Exception:  # noqa: BLE001 -- a reporting failure must not touch generation
        logger.warning("prompt_cache summary unavailable for job %s", job_id, exc_info=True)


__all__ = [
    "PromptCacheCallUsage",
    "PromptCacheSummary",
    "author_stage_prompt_cache_usage",
    "format_prompt_cache_summary",
    "log_author_stage_prompt_cache_summary",
    "log_author_stage_prompt_cache_summary_safely",
    "log_prompt_cache_summary",
    "summarize_prompt_cache_usage",
]
