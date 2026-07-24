"""Content-free metrics for the durable Template V2 PPTX queue."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from typing import Literal


LOGGER = logging.getLogger(__name__)

QueueOperation = Literal["dispatch", "recover"]
QueueOutcome = Literal["completed", "failed"]

_OPERATIONS = {"dispatch", "recover"}
_OUTCOMES = {"completed", "failed"}
_MAX_COUNT = 1_000_000


@dataclass(frozen=True, slots=True)
class PptxQueueObservation:
    operation: QueueOperation
    outcome: QueueOutcome
    count: int

    def as_event(self) -> dict[str, object]:
        return asdict(self)


def build_pptx_queue_observation(
    *,
    operation: QueueOperation,
    outcome: QueueOutcome,
    count: int = 0,
) -> dict[str, object]:
    """Build a bounded queue metric without job or tenant identifiers."""

    if not isinstance(operation, str) or operation not in _OPERATIONS:
        raise ValueError("PPTX queue operation is not in the safe allowlist")
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise ValueError("PPTX queue outcome is not in the safe allowlist")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or count > _MAX_COUNT
    ):
        raise ValueError("PPTX queue count must be a bounded integer")
    return PptxQueueObservation(
        operation=operation,
        outcome=outcome,
        count=count,
    ).as_event()


def log_pptx_queue_observation(
    *,
    operation: QueueOperation,
    outcome: QueueOutcome,
    count: int = 0,
    logger: logging.Logger = LOGGER,
) -> dict[str, object]:
    event = build_pptx_queue_observation(
        operation=operation,
        outcome=outcome,
        count=count,
    )
    logger.info(
        "template_v2_pptx_queue %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )
    return event
