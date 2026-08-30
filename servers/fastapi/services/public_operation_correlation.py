"""Safe, low-cardinality correlation signals for public operation boundaries.

``operation_id`` is retained only in structured logs, where it joins API,
provider, worker, render, upload, and publication activity.  Prometheus
receives only closed component/action/outcome labels; putting an operation id
in a metric label would create unbounded cardinality and leak a public handle.
"""

from __future__ import annotations

import json
import logging
from typing import Literal
import uuid


LOGGER = logging.getLogger(__name__)

CorrelationComponent = Literal[
    "api", "provider", "worker", "render", "upload", "publication"
]
CorrelationAction = Literal[
    "accepted",
    "status_read",
    "events_read",
    "cancel_requested",
    "download_granted",
    "call_reserved",
    "started",
    "completed",
    "artifact_registered",
    "artifact_uploaded",
    "publication_prepared",
]
CorrelationOutcome = Literal["succeeded", "failed", "replayed"]

_COMPONENTS = frozenset(
    {"api", "provider", "worker", "render", "upload", "publication"}
)
_ACTIONS = frozenset(
    {
        "accepted",
        "status_read",
        "events_read",
        "cancel_requested",
        "download_granted",
        "call_reserved",
        "started",
        "completed",
        "artifact_registered",
        "artifact_uploaded",
        "publication_prepared",
    }
)
_OUTCOMES = frozenset({"succeeded", "failed", "replayed"})


def _operation_id(value: uuid.UUID | str) -> str:
    """Canonicalize a durable UUID and never log an untrusted path value."""

    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("public_operation_id_invalid") from error


def build_public_operation_correlation(
    *,
    operation_id: uuid.UUID | str,
    component: CorrelationComponent,
    action: CorrelationAction,
    outcome: CorrelationOutcome,
) -> dict[str, str | int]:
    """Build a closed metadata-only signal without credentials or payloads."""

    if component not in _COMPONENTS:
        raise ValueError("public_operation_component_invalid")
    if action not in _ACTIONS:
        raise ValueError("public_operation_action_invalid")
    if outcome not in _OUTCOMES:
        raise ValueError("public_operation_outcome_invalid")
    return {
        "schema_version": 1,
        "pipeline": "public_operation_correlation",
        "operation_id": _operation_id(operation_id),
        "component": component,
        "action": action,
        "outcome": outcome,
    }


def log_public_operation_correlation(
    *,
    operation_id: uuid.UUID | str,
    component: CorrelationComponent,
    action: CorrelationAction,
    outcome: CorrelationOutcome,
    logger: logging.Logger = LOGGER,
) -> dict[str, str | int] | None:
    """Emit best-effort structured logs and aggregate-only metric dimensions."""

    try:
        event = build_public_operation_correlation(
            operation_id=operation_id,
            component=component,
            action=action,
            outcome=outcome,
        )
        try:
            logger.info(
                "public_operation_correlation %s",
                json.dumps(event, sort_keys=True, separators=(",", ":")),
            )
        finally:
            # A logging sink is not an authority.  Count the same safe closed
            # dimensions even when the collector is temporarily unavailable.
            from services.workspace_metrics import WORKSPACE_METRICS

            WORKSPACE_METRICS.record_public_operation_correlation(
                component=component,
                action=action,
                outcome=outcome,
            )
        return event
    except Exception:
        # Correlation cannot change control flow, publication, or a provider
        # reservation.  Do not include exception text: it may contain secrets.
        return None


__all__ = [
    "build_public_operation_correlation",
    "log_public_operation_correlation",
]
