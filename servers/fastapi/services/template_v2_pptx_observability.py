"""Content-free metrics for the deterministic Template V2 PPTX analyzer.

This module is deliberately independent from the existing rollout observation
contract. It accepts only bounded aggregate values and has no identifier,
filename, source hash, exception, or arbitrary-metadata entry point.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from math import isfinite
from typing import Literal


LOGGER = logging.getLogger(__name__)

PptxAnalysisProvider = Literal["deterministic-ooxml-static"]
PptxAnalysisStatus = Literal["completed", "failed"]

_PROVIDERS = {"deterministic-ooxml-static"}
_STATUSES = {"completed", "failed"}
_MAX_DURATION_MS = 86_400_000.0
_MAX_COUNT = 1_000_000


@dataclass(frozen=True, slots=True)
class PptxAnalysisObservation:
    """The complete event schema; arbitrary metadata has no entry point."""

    provider: PptxAnalysisProvider
    status: PptxAnalysisStatus
    duration_ms: float
    count: int

    def as_event(self) -> dict[str, object]:
        return asdict(self)


def build_pptx_analysis_observation(
    *,
    provider: PptxAnalysisProvider,
    status: PptxAnalysisStatus,
    duration_ms: float = 0,
    count: int = 1,
) -> dict[str, object]:
    """Build one bounded analyzer metric without content or identifiers."""

    if not isinstance(provider, str) or provider not in _PROVIDERS:
        raise ValueError("PPTX analysis provider is not in the safe allowlist")
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError("PPTX analysis status is not in the safe allowlist")
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, (int, float))
        or not isfinite(duration_ms)
        or duration_ms < 0
        or duration_ms > _MAX_DURATION_MS
    ):
        raise ValueError(
            "PPTX analysis duration_ms must be a bounded millisecond value"
        )
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or count > _MAX_COUNT
    ):
        raise ValueError("PPTX analysis count must be a bounded integer")

    return PptxAnalysisObservation(
        provider=provider,
        status=status,
        duration_ms=float(duration_ms),
        count=count,
    ).as_event()


def log_pptx_analysis_observation(
    *,
    provider: PptxAnalysisProvider,
    status: PptxAnalysisStatus,
    duration_ms: float = 0,
    count: int = 1,
    logger: logging.Logger = LOGGER,
) -> dict[str, object]:
    """Log and return a validated metric with exactly four payload fields."""

    event = build_pptx_analysis_observation(
        provider=provider,
        status=status,
        duration_ms=duration_ms,
        count=count,
    )
    logger.info(
        "template_v2_pptx_analysis %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )
    return event
