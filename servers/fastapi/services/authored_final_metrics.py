"""F4: resolve the deck tail's browser metrics without re-rendering the deck.

``docs/authored-quality-speed-programme-design-20260905.md`` sections 1.1 and
4, F4. The browser-metrics LRU is process-local and used to hold 32 entries, so
a long deck evicted its own early slides and the tail re-rendered them purely
to collect telemetry it had already collected once (counter
``metrics_recollection_requests``). Two things fix that: the LRU is now sized
from the registered knobs (``utils/slide_capture.py``), and the deck tail asks
for the metrics it is already holding before it asks Chromium for them again.

The one rule that governs every source here is IDENTITY, not provenance: a
readback is used only when the metrics carry the ``source_digest`` of the exact
HTML about to be gated and published. ``utils/slide_capture._cache_browser_metrics``
stamps that digest on every payload it caches, so it is available for the
sealed attempt evidence and for the repair rounds' own metrics alike.

Bench run 24 is why. Four slides were remade after their first capture, and the
sealed ledger was trusted for all 24 slides on length alone (``source=ledger``),
so a stale entry -- or a deliberate gap -- would have been gated as if it
described the published bytes. Order of preference, per slide:

1. the sealed attempt evidence, when its digest matches this slide's HTML;
2. metrics carried by the repair rounds, under the same digest rule;
3. ``ensure_browser_metrics``, which is a cache hit whenever the slide was
   already measured and a re-render only when it genuinely was not.

A slide Chromium still cannot measure comes back as ``None`` from step 3, so
the unified gate keeps blocking on missing telemetry exactly as before.
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
    by_index = overrides or {}
    resolved: list[Any] = [None] * len(htmls)
    from_ledger: list[int] = []
    from_round: list[int] = []
    missing: list[int] = []
    for index, html in enumerate(htmls):
        ledger_metrics = sealed[index] if sealed is not None else None
        round_metrics = by_index.get(index)
        if metrics_match_html(html, ledger_metrics):
            resolved[index] = ledger_metrics
            from_ledger.append(index)
        elif metrics_match_html(html, round_metrics):
            resolved[index] = round_metrics
            from_round.append(index)
        else:
            missing.append(index)
    LOGGER.info(
        "[authored] metrics_readback ledger=%d repair_round=%d gathered=%d stale=%s",
        len(from_ledger),
        len(from_round),
        len(missing),
        missing[:8],
    )
    if missing:
        from services.authored_admission_capture import captured_metrics_or_existing
        from utils.slide_capture import ensure_browser_metrics

        gathered = await asyncio.gather(
            *(
                captured_metrics_or_existing(
                    htmls[index], index=index, ensure=ensure_browser_metrics
                )
                for index in missing
            )
        )
        for index, metrics in zip(missing, gathered, strict=True):
            resolved[index] = metrics
    return resolved


__all__ = [
    "checkpoint_browser_metrics",
    "metrics_match_html",
    "resolve_final_browser_metrics",
]
