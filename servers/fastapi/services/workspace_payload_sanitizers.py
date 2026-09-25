"""Bounded projections of nested operation payload values.

Extracted from ``sanitize_workspace_event_payload`` in
``services/workspace_operation_runner.py``: each function takes one nested
value the runner already accepted by key and returns only its public,
metadata-only shape.  Behaviour is unchanged by the move.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_DECK_IR_SCALAR_KEYS = (
    "strategy",
    "strategyReceiptDigest",
    "deckIrDigest",
    "detachedCheckpointDigest",
    "providerLogicalCalls",
    "providerOutputMode",
    "providerHtmlAuthoringCalls",
    "legacyProviderHtmlExecuted",
    "candidateSearchK",
    "candidateProviderCallsAdded",
    "grammarDigest",
    "styleProfileDigest",
    "retainedReferenceDigest",
    "compilerVersion",
    "candidateSelectionReceiptDigest",
    "executionEvidenceDigest",
    "slideCount",
)
_RESOURCE_LIMIT_KEYS = ("memory", "cpu", "pids", "openFiles", "outputBytes", "wallClock")


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def project_generation_deck_ir(value: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {
        key: value.get(key)
        for key in _DECK_IR_SCALAR_KEYS
        if value.get(key) is None or isinstance(value.get(key), (str, int, float, bool))
    }
    closure_digests = value.get("browserClosureReceiptDigests")
    if _is_sequence(closure_digests):
        projected["browserClosureReceiptDigests"] = [
            item for item in closure_digests[:30] if isinstance(item, str)
        ]
    compile_digests = value.get("compileReceiptDigests")
    if _is_sequence(compile_digests):
        projected["compileReceiptDigests"] = [
            item for item in compile_digests[:30] if isinstance(item, str)
        ]
    return projected


def sanitize_worker_resource_samples(value: Sequence[Any]) -> list[dict[str, Any]]:
    resource_samples: list[dict[str, Any]] = []
    for item in value[:100]:
        if not isinstance(item, Mapping):
            continue
        limits = item.get("resourceLimits")
        limits = limits if isinstance(limits, Mapping) else {}
        methods = item.get("controlEnforcementMethods")
        methods = methods if isinstance(methods, Mapping) else {}
        residual_risks = item.get("residualRisks")
        residual_risks = residual_risks if isinstance(residual_risks, Mapping) else {}
        resource_samples.append({
            "role": str(item.get("role", ""))[:16],
            "enforcementLayer": str(item.get("enforcementLayer", ""))[:64],
            "resourceLimitsEnforced": item.get("resourceLimitsEnforced") is True,
            "resourceLimits": {key: int(limits.get(key, 0)) for key in _RESOURCE_LIMIT_KEYS},
            "controlEnforcementMethods": {
                key: str(methods.get(key, ""))[:96] for key in _RESOURCE_LIMIT_KEYS
            },
            "residualRisks": {
                str(key)[:32]: str(detail)[:240] for key, detail in residual_risks.items()
            },
            "supervisorPollIntervalMillis": int(item.get("supervisorPollIntervalMillis", 0)),
            "peakRssBytes": int(item.get("peakRssBytes", 0)),
            "peakPrivateBytes": int(item.get("peakPrivateBytes", 0)),
            "peakHandleCount": int(item.get("peakHandleCount", 0)),
            "peakProcessCount": int(item.get("peakProcessCount", 0)),
            "peakCpuMillis": int(item.get("peakCpuMillis", 0)),
            "recycledWithinBaseline": item.get("recycledWithinBaseline") is True,
            "finalProcessCount": int(item.get("finalProcessCount", 0)),
        })
    return resource_samples


__all__ = ["project_generation_deck_ir", "sanitize_worker_resource_samples"]
