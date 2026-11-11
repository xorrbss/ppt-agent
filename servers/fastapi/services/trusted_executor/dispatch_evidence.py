"""Private, content-free observations; not external worker/image attestation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any
import uuid

from utils.hashing import canonical_json_bytes, sha256_hex
from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob


DISPATCH_EVIDENCE_KEY = "_supervised_dispatch_evidence"
PRIVATE_DISPATCH_EVIDENCE_KEY = "_worker_dispatch_evidence"
PRIVATE_DISPATCH_GAPS_KEY = "_worker_dispatch_evidence_gaps"
_SCHEMA = "presenton.local-supervised-dispatch-evidence/v1"
_RUNTIME_SCHEMA = "presenton.observed-launch-identity/v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_NUMERIC_METRICS = frozenset({
    "peakPrivateBytes", "peakRssBytes", "peakHandleCount", "peakProcessCount",
    "peakCpuMillis", "totalProcessCount", "terminatedProcessCount", "finalProcessCount",
})
_BOOLEAN_METRICS = frozenset({
    "resourceLimitsEnforced", "recycledWithinBaseline", "deadlineExceeded", "memoryLimitExceeded",
})
_LIMITS = frozenset({
    "memoryBytes", "pidCount", "wallClockSeconds", "cpuRatePercent", "temporaryBytes",
})
_KEYS = frozenset({
    "schemaVersion", "evidenceScope", "dispatchId", "operationId", "role",
    "attemptTokenSha256", "fencingToken", "inputDigest", "inputBytes", "outputDigest",
    "outputBytes", "invocationStartedAtUtc", "treeZeroConfirmedAtUtc", "durationMillis",
    "utcElapsedMillis", "utcClockRegressed", "outcome", "workerRuntimeIdentity",
    "resourceMetrics", "resourceMetricGaps", "externalAttestationStatus",
    "productionEligible", "evidenceDigest",
})


def _canonical(value: object) -> bytes:
    # ASCII-escaped on purpose: the evidence digest is persisted in that form.
    return canonical_json_bytes(value, ensure_ascii=True)


def _digest(value: object) -> str:
    return sha256_hex(_canonical(value))


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("worker_dispatch_evidence_invalid")


def _sha(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _unsigned(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**63


def _observed_resources(raw: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in _NUMERIC_METRICS:
        if key in raw:
            _require(_unsigned(raw[key]))
            result[key] = raw[key]
    for key in _BOOLEAN_METRICS:
        if key in raw:
            _require(isinstance(raw[key], bool))
            result[key] = raw[key]
    if "limits" in raw:
        limits = raw["limits"]
        _require(isinstance(limits, Mapping))
        selected = {key: limits[key] for key in _LIMITS if key in limits}
        _require(all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 < value <= 3600
            if key == "wallClockSeconds" else _unsigned(value)
            for key, value in selected.items()
        ))
        result["limits"] = selected
    return result


@dataclass(frozen=True, slots=True)
class SupervisedInvocationObservation:
    dispatch_id: str
    started_at: datetime
    monotonic_started_ns: int
    runtime_identity: Mapping[str, object]


def begin_supervised_invocation(command: Sequence[str]) -> SupervisedInvocationObservation:
    """Run in the launch thread, immediately before invoking the supervisor.

    The executable hash is an observation before launch, not a loader/image
    attestation. Failure to read it remains an explicit gap, not a made-up pin.
    No argv, environment, file path, or individual path hash is retained.
    """

    executable_digest = None
    try:
        with Path(command[0]).open("rb") as stream:
            executable_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        pass
    runtime: dict[str, object] = {
        "schemaVersion": _RUNTIME_SCHEMA,
        "identityScope": "observed-executable-and-argv",
        "executableSha256": executable_digest,
        "argvSha256": _digest(list(command)),
        "unavailable": [] if executable_digest is not None else ["executableSha256"],
    }
    runtime["identityDigest"] = _digest(runtime)
    return SupervisedInvocationObservation(
        dispatch_id=str(uuid.uuid4()), started_at=datetime.now(timezone.utc),
        monotonic_started_ns=time.perf_counter_ns(), runtime_identity=runtime,
    )


def seal_supervised_dispatch_evidence(
    *,
    observation: SupervisedInvocationObservation,
    tree_zero_at: datetime,
    tree_zero_monotonic_ns: int,
    job: IsolatedWorkerJob,
    output: ArtifactRef,
    resource_metrics: Mapping[str, object],
) -> dict[str, object]:
    """Seal only verified successful output after supervised tree-zero proof.

    Failed/cancelled invocations and process-loss durability require a separate
    journal; this receipt does not claim to cover those missing observations.
    """

    resources = _observed_resources(resource_metrics)
    elapsed_utc = (tree_zero_at - observation.started_at).total_seconds() * 1000
    payload: dict[str, object] = {
        "schemaVersion": _SCHEMA,
        "evidenceScope": "local-supervisor-invocation-to-tree-zero",
        "dispatchId": observation.dispatch_id,
        "operationId": str(job.operation_id),
        "role": job.role,
        "attemptTokenSha256": sha256_hex(job.attempt_token.encode()),
        "fencingToken": job.fencing_token,
        "inputDigest": job.input_digest,
        "inputBytes": job.input_artifact.size_bytes,
        "outputDigest": output.sha256,
        "outputBytes": output.size_bytes,
        "invocationStartedAtUtc": observation.started_at.isoformat(),
        "treeZeroConfirmedAtUtc": tree_zero_at.isoformat(),
        "durationMillis": (tree_zero_monotonic_ns - observation.monotonic_started_ns) / 1_000_000,
        "utcElapsedMillis": elapsed_utc,
        "utcClockRegressed": elapsed_utc < 0,
        "outcome": "succeeded",
        "workerRuntimeIdentity": dict(observation.runtime_identity),
        "resourceMetrics": resources,
        "resourceMetricGaps": sorted((_NUMERIC_METRICS | _BOOLEAN_METRICS) - resources.keys()),
        "externalAttestationStatus": "not-collected",
        "productionEligible": False,
    }
    payload["evidenceDigest"] = _digest(payload)
    return validate_supervised_dispatch_evidence(payload)


def validate_supervised_dispatch_evidence(value: object) -> dict[str, object]:
    _require(isinstance(value, Mapping) and set(value) == _KEYS)
    _require(value["schemaVersion"] == _SCHEMA)
    _require(value["evidenceScope"] == "local-supervisor-invocation-to-tree-zero")
    _require(value["role"] == "export" and value["outcome"] == "succeeded")
    _require(value["externalAttestationStatus"] == "not-collected" and value["productionEligible"] is False)
    for key in ("dispatchId", "operationId"):
        try:
            _require(isinstance(value[key], str) and str(uuid.UUID(value[key])) == value[key])
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("worker_dispatch_evidence_invalid") from error
    for key in ("attemptTokenSha256", "inputDigest", "outputDigest", "evidenceDigest"):
        _require(_sha(value[key]))
    for key in ("fencingToken", "inputBytes", "outputBytes"):
        _require(_unsigned(value[key]))
    _require(value["fencingToken"] > 0)
    for key in ("durationMillis", "utcElapsedMillis"):
        _require(isinstance(value[key], (int, float)) and not isinstance(value[key], bool) and math.isfinite(value[key]))
    _require(value["durationMillis"] >= 0)
    try:
        start, finish = (datetime.fromisoformat(value[key]) for key in (
            "invocationStartedAtUtc", "treeZeroConfirmedAtUtc",
        ))
        _require(start.utcoffset() == finish.utcoffset() == timezone.utc.utcoffset(None))
        _require((finish - start).total_seconds() * 1000 == value["utcElapsedMillis"])
        _require(value["utcClockRegressed"] is (finish < start))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("worker_dispatch_evidence_invalid") from error
    runtime = value["workerRuntimeIdentity"]
    _require(isinstance(runtime, Mapping) and set(runtime) == {
        "schemaVersion", "identityScope", "executableSha256", "argvSha256", "unavailable", "identityDigest",
    })
    _require(runtime["schemaVersion"] == _RUNTIME_SCHEMA and runtime["identityScope"] == "observed-executable-and-argv")
    _require(runtime["executableSha256"] is None or _sha(runtime["executableSha256"]))
    _require(_sha(runtime["argvSha256"]) and _sha(runtime["identityDigest"]))
    _require(runtime["unavailable"] == ([] if runtime["executableSha256"] is not None else ["executableSha256"]))
    _require(_digest({key: item for key, item in runtime.items() if key != "identityDigest"}) == runtime["identityDigest"])
    resources = value["resourceMetrics"]
    _require(isinstance(resources, Mapping) and _observed_resources(resources) == resources)
    _require(value["resourceMetricGaps"] == sorted((_NUMERIC_METRICS | _BOOLEAN_METRICS) - resources.keys()))
    _require(_digest({key: item for key, item in value.items() if key != "evidenceDigest"}) == value["evidenceDigest"])
    return json.loads(_canonical(dict(value)))


def private_dispatch_evidence_summary(samples: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    receipts = []
    gaps = []
    dispatch_ids = set()
    for index, sample in enumerate(samples):
        if DISPATCH_EVIDENCE_KEY not in sample:
            gaps.append({"sampleIndex": index, "reason": "dispatch-evidence-not-collected"})
            continue
        receipt = validate_supervised_dispatch_evidence(sample[DISPATCH_EVIDENCE_KEY])
        _require(receipt["role"] == sample.get("role") and receipt["dispatchId"] not in dispatch_ids)
        dispatch_ids.add(receipt["dispatchId"])
        receipts.append(receipt)
    return {PRIVATE_DISPATCH_EVIDENCE_KEY: receipts, PRIVATE_DISPATCH_GAPS_KEY: gaps}
