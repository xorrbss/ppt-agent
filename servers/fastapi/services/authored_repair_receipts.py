"""Provider receipts for the authored repair ladder (design M0, 2026-09-08).

Until this module existed, the remake-first round
(``services/authored_repair_round_order.py``) and the terminal remake round
(``services/authored_terminal_slide_fallback.py``) ran outside any
``provider_usage_scope``: ``record_provider_call`` returns early without an
active scope, so every remake authoring call was invisible to
``provider_usage_receipts`` (bench run 48: 25 ``author`` receipts, none for
the 7 remake calls that ran for 2,600 s). Those calls also skipped provider
admission for the same reason. The patch round did carry a scope, but under
the same name as the deck-wide ``repair`` span receipt.

Receipt stages this module emits, one deck-level scope per round:

``repair_remake``
    Full re-author calls of the remake-first round (``round_ordinal`` 1) and
    of the terminal round behind the patch round (``round_ordinal`` 2).
    Provider calls carry ``retry_class='recovery'``, the author model, latency
    and tokens; the round's wall clock is a span receipt in the same stage
    with ``provider='local', model='none'``.
``repair_patch``
    The bounded structured patch proposal (``utils/llm_calls/author_slide_patch.py``)
    plus its span receipt. It makes no provider call when preflight or the
    deterministic candidate leaves nothing to propose, so a deck may have the
    span and no call.

No ``repair_vision`` stage exists: both rounds start from the gate's own
critique with ``max_cycles=1``, so no vision critique runs inside the ladder
(vision QA, when enabled, is receipted under ``vqa``).

The receipt key of a call is derived from job, stage, attempt, slide and the
round, so a slide remade in both rounds gets two receipts, while a replay of
one round reproduces the same keys and is deduplicated by the receipt store.

Per-stage provider seconds and tokens for one deck::

    SELECT stage,
           COUNT(*) FILTER (WHERE provider <> 'local')       AS calls,
           SUM(latency_ms) FILTER (WHERE provider <> 'local') AS provider_ms,
           SUM(input_tokens) AS input_tokens,
           SUM(output_tokens) AS output_tokens,
           SUM(cached_tokens) AS cached_tokens,
           MAX(latency_ms) FILTER (WHERE provider = 'local')  AS span_ms
    FROM provider_usage_receipts
    WHERE job_id = :job_id
    GROUP BY stage ORDER BY stage;
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any

from services.provider_usage_instrumentation import (
    provider_usage_scope,
    record_local_stage,
)

OPERATION = "authored_generation"


@dataclass(frozen=True, slots=True)
class RepairRound:
    """One receipted round of the repair ladder: its stage and round ordinal."""

    stage: str
    round_ordinal: int | None


REMAKE_FIRST_ROUND = RepairRound("repair_remake", 1)
TERMINAL_ROUND = RepairRound("repair_remake", 2)
#: The patch round calls each slide's slot once, so its keys need no round.
PATCH_ROUND = RepairRound("repair_patch", None)


@asynccontextmanager
async def repair_round_receipts(
    generation_job: Any, *, repair_round: RepairRound
) -> AsyncIterator[None]:
    """Receipt scope for one repair round, then its span receipt.

    Deck-level on purpose: several slides are remade under one scope, so
    ``author_slide_html`` puts the slide into the semantic slot (the same
    shape the ``vqa`` stage uses). A generation without a durable job opens no
    scope and records nothing, like every other stage.
    """

    job_id = getattr(generation_job, "id", None) if generation_job is not None else None
    attempt_number = (
        int(getattr(generation_job, "attempt_number", 0) or 0)
        if generation_job is not None
        else 0
    )
    started = monotonic()
    with provider_usage_scope(
        job_id=job_id,
        operation=OPERATION,
        stage=repair_round.stage,
        attempt_number=attempt_number,
        round_ordinal=repair_round.round_ordinal,
    ):
        try:
            yield
        finally:
            if job_id is not None:
                await record_local_stage(latency_ms=(monotonic() - started) * 1000)


__all__ = [
    "OPERATION",
    "PATCH_ROUND",
    "REMAKE_FIRST_ROUND",
    "RepairRound",
    "TERMINAL_ROUND",
    "repair_round_receipts",
]
