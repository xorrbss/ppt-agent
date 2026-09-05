"""F4: resolve the deck tail's browser metrics without re-rendering the deck.

``docs/authored-quality-speed-programme-design-20260905.md`` sections 1.1 and
4, F4. The browser-metrics LRU is process-local and used to hold 32 entries, so
a long deck evicted its own early slides and the tail re-rendered them purely
to collect telemetry it had already collected once (counter
``metrics_recollection_requests``). Two things fix that: the LRU is now sized
from the registered knobs (``utils/slide_capture.py``), and the deck tail asks
for the metrics it is already holding before it asks Chromium for them again.

The order of preference here is deliberate:

1. the sealed attempt evidence, when there is a ledger. It is authoritative
   INCLUDING its gaps: ``_finalize_attempt_evidence`` deliberately retains a
   missing entry rather than re-rendering an unchanged slide, and the unified
   gate is meant to block on that, so this module must not quietly repair it.
2. metrics carried by the repair rounds, but only for a slide whose metrics
   still carry the digest of the exact HTML about to be published. A digest
   mismatch is treated as no metrics at all.
3. ``ensure_browser_metrics``, unchanged, for whatever is left.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping, Sequence
from typing import Any

LOGGER = logging.getLogger(__name__)


def _source_digest(html: str) -> str:
    return "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()


def metrics_match_html(html: str, metrics: object) -> bool:
    """Whether these metrics were measured from exactly this HTML."""

    if not isinstance(metrics, Mapping):
        return False
    return str(metrics.get("source_digest") or "") == _source_digest(html)


def checkpoint_browser_metrics(
    htmls: Sequence[str], retained: Sequence[Any] | None
) -> list[Any] | None:
    """The sealed attempt evidence for this deck, or ``None`` when there is none."""

    if not retained:
        return None
    metrics = list(retained)
    if len(metrics) != len(htmls):
        return None
    return metrics


async def resolve_final_browser_metrics(
    htmls: Sequence[str],
    *,
    retained: Sequence[Any] | None = None,
    overrides: Mapping[int, Any] | None = None,
) -> list[Any]:
    """Browser metrics for every final slide, re-rendering only what is missing."""

    sealed = checkpoint_browser_metrics(htmls, retained)
    if sealed is not None:
        LOGGER.info(
            "[authored] metrics_readback_from_checkpoint=%d source=ledger", len(sealed)
        )
        return sealed

    from utils.slide_capture import ensure_browser_metrics

    resolved: list[Any] = [None] * len(htmls)
    missing: list[int] = []
    for index, html in enumerate(htmls):
        candidate = (overrides or {}).get(index)
        if metrics_match_html(html, candidate):
            resolved[index] = candidate
        else:
            missing.append(index)
    LOGGER.info(
        "[authored] metrics_readback_from_checkpoint=%d gathered=%d source=repair_round",
        len(htmls) - len(missing),
        len(missing),
    )
    if missing:
        gathered = await asyncio.gather(
            *(ensure_browser_metrics(htmls[index]) for index in missing)
        )
        for index, metrics in zip(missing, gathered, strict=True):
            resolved[index] = metrics
    return resolved


__all__ = [
    "checkpoint_browser_metrics",
    "metrics_match_html",
    "resolve_final_browser_metrics",
]
