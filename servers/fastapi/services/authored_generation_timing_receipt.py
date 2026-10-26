"""Immutable authored generation timings bound to QualityReceipt v2."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic

from services.authored_generation_timing_receipt_e4 import (
    deck_instruments_field,
    fit_summary_entries,
    flagged_slide_entries,
    merged_stage_wall_millis,
    normalization_entries,
    optional_top_level_fields,
    provider_rate_profile as _e4_provider_rate_profile,
    quality_loop_variant_field,
    STAGE_COOLDOWN_WAIT_KEY,
    stage_cooldown_wait_seconds,
    stage_optional_keys,
    stage_wall_millis,
    vision_advisory_field,
)
from services.authored_generation_observability_metrics import (
    # Redundant alias: this module stays the published import surface for the
    # metric vocabulary, so the name is deliberately re-exported.
    GENERATION_OBSERVABILITY_METRICS as GENERATION_OBSERVABILITY_METRICS,
    _observability_metrics,
)
from services.authored_generation_timing_values import (
    MAX_GENERATION_STAGE_TIMING_MS,
    _bounded_timing_ms,
    _required_sha256,
)
from services.provider_call_timing import provider_call_timing_field
from services.provider_operation_usage import STAGE_COUNTER_KEYS
from services.authored_quality_receipt_v2 import (
    QualityReceiptV2,
    _manifest_digest,
    _manifest_lock,
    _receipt_digest as _quality_receipt_digest,
    public_attempt_id,
    quality_receipt_manifest_path,
)
from utils.hashing import canonical_json_digest
from services.canvas_bench_speed import speed_provenance
from services.generation_core.drivers.svg.runtime_provenance import runtime_provenance_field


GENERATION_TIMING_STAGES = (
    "outline",
    "author",
    "illustration",
    "render",
    "quality",
    "repair",
    "checkpoint",
    "export",
    "publication",
)
LEGACY_GENERATION_TIMING_STAGES = (
    "outline", "author", "repair", "checkpoint", "export", "publication"
)
GENERATION_ATTEMPT_OUTCOMES = frozenset(
    {
        "succeeded",
        "failed",
        "budget_exhausted",
        "cancelled",
        "dead_letter",
        "worker_crash",
    }
)
GENERATION_ATTEMPT_TIMING_STAGES = frozenset(
    (*GENERATION_TIMING_STAGES, "provider")
)
PUBLIC_GENERATION_TIMING_STAGES = (
    "outline",
    "provider",
    "authoring",
    "repair",
    "chromiumReview",
    "publication",
)
OPTIONAL_GENERATION_OBSERVATIONS = frozenset({
    "setup.title", "setup.plan_llm", "setup.scenes", "setup.icon_sheets",
    "setup.header_design", "setup.header_representative", "setup.header_selection",
    "setup.prepare_local", "render.prepare", "render.wait", "render.work", "render.post",
    "render.checkpoint", "quality.vqa",
})
MAX_RENDER_ATTEMPT_OBSERVATIONS = 800
MAX_RENDER_STAGE_WINDOWS = 32


def _optional_observation_name(name: str) -> bool:
    return name in OPTIONAL_GENERATION_OBSERVATIONS or bool(
        re.fullmatch(r"publication\.[a-z][a-z0-9_]{0,63}", name)
    )


def _optional_observations(value: object) -> dict[str, dict[str, int]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("generation_optional_observations_invalid")
    result = {}
    for name, observation in value.items():
        if not isinstance(name, str) or not _optional_observation_name(name) or not isinstance(observation, Mapping):
            raise ValueError("generation_optional_observations_invalid")
        entry = {key: _bounded_timing_ms(observation.get(key), name)
                 for key in ("startMillis", "endMillis", "durationMillis")}
        if entry["endMillis"] < entry["startMillis"]:
            raise ValueError("generation_optional_observations_invalid")
        result[name] = entry
    return result


def _render_attempt_observations(value: object) -> tuple[dict[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_RENDER_ATTEMPT_OBSERVATIONS:
        raise ValueError("generation_render_observations_invalid")
    result = []
    for attempt in value:
        if (not isinstance(attempt, Mapping) or attempt.get("schema") not in {
                "svg-render-attempt-timing/v1", "svg-render-attempt-timing/v2"}
                or type(attempt.get("pageIndex")) is not int or attempt["pageIndex"] < 0
                or type(attempt.get("attemptOrdinal")) is not int or attempt["attemptOrdinal"] < 1
                or not isinstance(attempt.get("stages"), list)
                or len(attempt["stages"]) > MAX_RENDER_STAGE_WINDOWS):
            raise ValueError("generation_render_observations_invalid")
        stages = []
        legacy = attempt["schema"] == "svg-render-attempt-timing/v1"
        first = attempt["stages"][0] if attempt["stages"] else {}
        origin = first.get("startMonotonic", 0) if legacy and isinstance(first, Mapping) else 0
        for stage in attempt["stages"]:
            if not isinstance(stage, Mapping) or stage.get("name") not in {"prepare", "wait", "work", "post", "checkpoint"}:
                raise ValueError("generation_render_observations_invalid")
            clocks = [stage.get(key) for key in (
                ("startMonotonic", "endMonotonic") if legacy else ("startMillis", "endMillis"))]
            if any(type(clock) not in (int, float) or not 0 <= clock < float("inf") for clock in clocks):
                raise ValueError("generation_render_observations_invalid")
            if clocks[1] < clocks[0]:
                raise ValueError("generation_render_observations_invalid")
            offsets = [(clock - origin) * 1000 if legacy else clock for clock in clocks]
            stages.append({"name": stage["name"], "startMillis": _bounded_timing_ms(offsets[0], "render_start"),
                           "endMillis": _bounded_timing_ms(offsets[1], "render_end"),
                           "durationMillis": _bounded_timing_ms(stage.get("durationMillis"), "render")})
        dropped = _bounded_timing_ms(attempt.get("stageWindowsDropped", 0), "render_dropped")
        result.append({"schema": "svg-render-attempt-timing/v2", "pageIndex": attempt["pageIndex"],
                       "attemptOrdinal": attempt["attemptOrdinal"], "stages": stages,
                       "eventLoopLagMillis": _bounded_timing_ms(attempt.get("eventLoopLagMillis"), "loop_lag"),
                       **({"stageWindowsDropped": dropped} if dropped else {})})
    return tuple(result)
#: The writer is ``services/provider_operation_usage.py``; taking its tuple
#: rather than restating it keeps the validator from drifting away from the
#: snapshot it validates (the check below is an exact set match).
_PROVIDER_USAGE_COUNTERS = STAGE_COUNTER_KEYS
_ATTEMPT_CAUSE_KEYS = ("queueReason", "stageReason", "retryReason")


def _attempt_causes(value: object | None) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping) or set(value) != set(_ATTEMPT_CAUSE_KEYS):
        raise ValueError("generation_attempt_causes_invalid")
    output: list[tuple[str, str]] = []
    for key in _ATTEMPT_CAUSE_KEYS:
        reason = str(value.get(key) or "").strip().lower()
        if (
            not reason
            or len(reason) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in reason
            )
        ):
            raise ValueError("generation_attempt_causes_invalid")
        output.append((key, reason))
    return tuple(output)


def generation_timing_receipt_path(job_id: object) -> Path:
    """Return the job-scoped timing sidecar beside its QualityReceipt ledger."""

    manifest = quality_receipt_manifest_path(job_id)
    return manifest.with_name(f"{manifest.stem}-timing.json")


def generation_attempt_timing_receipt_path(job_id: object) -> Path:
    """Return the append-only attempt ledger used before quality succeeds."""

    manifest = quality_receipt_manifest_path(job_id)
    return manifest.with_name(f"{manifest.stem}-attempt-timing.json")


def _stage_timings(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise ValueError("generation_timing_stages_invalid")
    stages = (
        GENERATION_TIMING_STAGES
        if set(value) == set(GENERATION_TIMING_STAGES)
        else LEGACY_GENERATION_TIMING_STAGES
        if set(value) == set(LEGACY_GENERATION_TIMING_STAGES)
        else None
    )
    if stages is None:
        raise ValueError("generation_timing_stages_invalid")
    return tuple(
        (stage, _bounded_timing_ms(value[stage], stage))
        for stage in stages
    )


def _partial_stage_timings(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("generation_attempt_timing_stages_invalid")
    normalized: dict[str, int] = {}
    for raw_stage, raw_value in value.items():
        stage = str(raw_stage).strip().lower()
        if stage not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stages_invalid")
        normalized[stage] = _bounded_timing_ms(raw_value, stage)
    return tuple(
        (stage, normalized[stage])
        for stage in (*GENERATION_TIMING_STAGES, "provider")
        if stage in normalized
    )


def _provider_usage_counters(value: object, field_name: str) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(_PROVIDER_USAGE_COUNTERS):
        raise ValueError(f"generation_attempt_{field_name}_invalid")
    return {
        name: _bounded_timing_ms(value[name], f"{field_name}_{name}")
        for name in _PROVIDER_USAGE_COUNTERS
    }


def _provider_usage(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"totals", "stages"}:
        raise ValueError("generation_attempt_provider_usage_invalid")
    raw_stages = value.get("stages")
    if not isinstance(raw_stages, Mapping) or len(raw_stages) > 64:
        raise ValueError("generation_attempt_provider_usage_invalid")
    stages: dict[str, dict[str, int]] = {}
    for raw_stage, counters in sorted(raw_stages.items(), key=lambda item: str(item[0])):
        stage = str(raw_stage).strip().lower()
        if (
            not stage
            or len(stage) > 64
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in stage
            )
        ):
            raise ValueError("generation_attempt_provider_usage_invalid")
        stages[stage] = _provider_usage_counters(counters, f"provider_stage_{stage}")
    return {
        "totals": _provider_usage_counters(value.get("totals"), "provider_totals"),
        "stages": stages,
    }


def _checkpoint_signals(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > 64:
        raise ValueError("generation_attempt_checkpoint_signals_invalid")
    output: list[dict[str, object]] = []
    required = {
        "stage",
        "enabled",
        "reused",
        "outcome",
        "lookupMs",
        "resolveMs",
        "commitMs",
        "totalMs",
    }
    for item in value:
        if not isinstance(item, Mapping) or set(item) != required:
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        stage = str(item.get("stage") or "").strip().lower()
        enabled = item.get("enabled")
        reused = item.get("reused")
        outcome = item.get("outcome")
        if stage not in {"deck_plan", "chapter_plan", "header", "illustration", "content_brief"}:
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        if (
            not isinstance(enabled, bool)
            or not isinstance(reused, bool)
            or outcome not in {"succeeded", "failed"}
        ):
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        if reused and not enabled:
            raise ValueError("generation_attempt_checkpoint_signals_invalid")
        output.append(
            {
                "stage": stage,
                "enabled": enabled,
                "reused": reused,
                "outcome": outcome,
                "lookupMs": _bounded_timing_ms(item.get("lookupMs"), "checkpoint_lookup"),
                "resolveMs": _bounded_timing_ms(item.get("resolveMs"), "checkpoint_resolve"),
                "commitMs": _bounded_timing_ms(item.get("commitMs"), "checkpoint_commit"),
                "totalMs": _bounded_timing_ms(item.get("totalMs"), "checkpoint_total"),
            }
        )
    return tuple(output)


@dataclass(frozen=True, slots=True)
class GenerationTimingReceipt:
    schema_version: str
    attempt_id: str
    attempt_number: int
    revision: int
    quality_receipt_digest: str
    requested_release_policy_digest: str
    pre_export_evaluation_policy_digest: str
    stage_timings_ms: tuple[tuple[str, int], ...]
    total_ms: int
    observability_metrics: tuple[tuple[str, dict[str, object]], ...] = ()
    timing_receipt_digest: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = {
            "schemaVersion": self.schema_version,
            "attemptId": self.attempt_id,
            "attemptNumber": self.attempt_number,
            "revision": self.revision,
            "qualityReceiptDigest": self.quality_receipt_digest,
            "requestedReleasePolicyDigest": self.requested_release_policy_digest,
            "preExportEvaluationPolicyDigest": self.pre_export_evaluation_policy_digest,
            "stagesMs": dict(self.stage_timings_ms),
            "totalMs": self.total_ms,
            "timingReceiptDigest": self.timing_receipt_digest,
        }
        if self.observability_metrics:
            payload["observability"] = {
                "schemaVersion": "generation-observability/v1",
                "metrics": dict(self.observability_metrics),
            }
        return payload


def _receipt_digest(receipt: GenerationTimingReceipt) -> str:
    payload = receipt.to_dict()
    payload.pop("timingReceiptDigest", None)
    return canonical_json_digest(payload, allow_nan=True)


def _from_dict(value: object) -> GenerationTimingReceipt:
    if not isinstance(value, Mapping):
        raise ValueError("generation_timing_receipt_invalid")
    attempt_number = value.get("attemptNumber")
    revision = value.get("revision")
    if (
        value.get("schemaVersion") != "generation-timing-receipt/v1"
        or isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        raise ValueError("generation_timing_receipt_invalid")
    observability = value.get("observability")
    observability_metrics = ()
    if observability is not None:
        if not isinstance(observability, Mapping) or observability.get("schemaVersion") != "generation-observability/v1":
            raise ValueError("generation_observability_receipt_invalid")
        observability_metrics = _observability_metrics(observability.get("metrics"))
    receipt = GenerationTimingReceipt(
        schema_version="generation-timing-receipt/v1",
        attempt_id=public_attempt_id(value.get("attemptId")),
        attempt_number=attempt_number,
        revision=revision,
        quality_receipt_digest=_required_sha256(
            value.get("qualityReceiptDigest"), "quality_receipt_digest"
        ),
        requested_release_policy_digest=_required_sha256(
            value.get("requestedReleasePolicyDigest"),
            "requested_release_policy_digest",
        ),
        pre_export_evaluation_policy_digest=_required_sha256(
            value.get("preExportEvaluationPolicyDigest"),
            "pre_export_evaluation_policy_digest",
        ),
        stage_timings_ms=_stage_timings(value.get("stagesMs")),
        total_ms=_bounded_timing_ms(value.get("totalMs"), "total"),
        observability_metrics=observability_metrics,
        timing_receipt_digest=_required_sha256(
            value.get("timingReceiptDigest"), "timing_receipt_digest"
        ),
    )
    if receipt.timing_receipt_digest != _receipt_digest(receipt):
        raise ValueError("generation_timing_receipt_digest_mismatch")
    return receipt


def _load_records(path: Path) -> dict[str, GenerationTimingReceipt]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("generation_timing_manifest_corrupt") from error
    records = payload.get("receipts") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != "generation-timing-manifest/v1"
        or not isinstance(records, Mapping)
        or payload.get("manifestDigest") != _manifest_digest(records)
    ):
        raise ValueError("generation_timing_manifest_corrupt")
    parsed: dict[str, GenerationTimingReceipt] = {}
    for key, item in records.items():
        receipt = _from_dict(item)
        if key != f"{receipt.attempt_id}#{receipt.revision}":
            raise ValueError("generation_timing_manifest_key_invalid")
        parsed[key] = receipt
    return parsed


def _persist_records(
    path: Path, records: Mapping[str, GenerationTimingReceipt]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {key: receipt.to_dict() for key, receipt in sorted(records.items())}
    envelope = {
        "schemaVersion": "generation-timing-manifest/v1",
        "receipts": serialized,
        "manifestDigest": _manifest_digest(serialized),
    }
    encoded = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (AttributeError, OSError):
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def create_generation_timing_receipt(
    *,
    job_id: object,
    quality_receipt: QualityReceiptV2,
    attempt_number: int,
    stage_timings_ms: Mapping[str, object],
    total_ms: object,
    observability_metrics: Mapping[str, object] | None = None,
) -> GenerationTimingReceipt:
    """Atomically create one digest-bound timing receipt for a successful attempt."""

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
    ):
        raise ValueError("generation_timing_attempt_number_invalid")
    if quality_receipt.receipt_digest != _quality_receipt_digest(quality_receipt):
        raise ValueError("generation_timing_quality_receipt_digest_mismatch")
    receipt = GenerationTimingReceipt(
        schema_version="generation-timing-receipt/v1",
        attempt_id=quality_receipt.attempt_id,
        attempt_number=attempt_number,
        revision=quality_receipt.revision,
        quality_receipt_digest=quality_receipt.receipt_digest,
        requested_release_policy_digest=quality_receipt.requested_release_policy_digest,
        pre_export_evaluation_policy_digest=quality_receipt.pre_export_evaluation_policy_digest,
        stage_timings_ms=_stage_timings(stage_timings_ms),
        total_ms=_bounded_timing_ms(total_ms, "total"),
        observability_metrics=(
            _observability_metrics(observability_metrics)
            if observability_metrics is not None
            else ()
        ),
    )
    receipt = replace(receipt, timing_receipt_digest=_receipt_digest(receipt))
    path = generation_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_records(path)
        key = f"{receipt.attempt_id}#{receipt.revision}"
        existing = records.get(key)
        if existing is not None:
            if existing != receipt:
                raise ValueError("generation_timing_receipt_binding_changed")
            return existing
        records[key] = receipt
        _persist_records(path, records)
    return receipt


def load_generation_timing_receipt(job_id: object) -> GenerationTimingReceipt | None:
    path = generation_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_records(path)
    if not records:
        return None
    return max(
        records.values(),
        key=lambda receipt: (
            receipt.attempt_number,
            receipt.revision,
            receipt.attempt_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class GenerationAttemptTimingReceipt:
    """Immutable terminal timing for one operation attempt.

    This receipt intentionally does not require a QualityReceipt. It can
    therefore be sealed from an exception/finally path while preserving only
    opaque identity digests, bounded timings and scalar usage counters.
    """

    schema_version: str
    attempt_id: str
    attempt_number: int
    revision: int
    outcome: str
    failure_stage: str | None
    stage_timings_ms: tuple[tuple[str, int], ...]
    total_ms: int
    quality_receipt_digest: str | None = None
    provider_usage: dict[str, object] | None = None
    checkpoint_signals: tuple[dict[str, object], ...] = ()
    causes: tuple[tuple[str, str], ...] = ()
    receipt_digest: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": self.schema_version,
            "attemptId": self.attempt_id,
            "attemptNumber": self.attempt_number,
            "revision": self.revision,
            "outcome": self.outcome,
            "failureStage": self.failure_stage,
            "stagesMs": dict(self.stage_timings_ms),
            "totalMs": self.total_ms,
            "receiptDigest": self.receipt_digest,
        }
        if self.quality_receipt_digest is not None:
            payload["qualityReceiptDigest"] = self.quality_receipt_digest
        if self.provider_usage is not None:
            payload["providerUsage"] = self.provider_usage
        if self.checkpoint_signals:
            payload["checkpointSignals"] = list(self.checkpoint_signals)
        if self.causes:
            payload["causes"] = dict(self.causes)
        return payload


@dataclass(frozen=True, slots=True)
class GenerationAttemptTimingReceiptV2:
    """Content-free terminal attempt receipt with explicit stage validity."""

    schema_version: str
    attempt_id: str
    attempt_number: int
    revision: int
    outcome: str
    stages: tuple[tuple[str, dict[str, object]], ...]
    queue_wait_ms: int
    wall_ms: int
    logical_calls: int
    retry_reason: str | None
    token_availability: str
    cost_availability: str
    worker: str
    terminal_error: str | None
    validity: str
    invalid_reasons: tuple[str, ...] = ()
    provider_usage: dict[str, object] | None = None
    #: E4 additions (``authored_generation_timing_receipt_e4.py`` module
    #: docstring has the full field-by-field description and the digest
    #: backward-compatibility rationale for their conditional serialization).
    provider_rate_profile: str | None = None
    normalizations: tuple[dict[str, object], ...] = ()
    quality_loop_variant: str | None = None
    flagged_slides: tuple[dict[str, object], ...] = ()
    #: M1: per-slide fit magnitude for the slides that failed the composition
    #: safety gate. Empty on a succeeded attempt, and then absent from the
    #: serialized form for the same digest reason as the fields above.
    fit_summaries: tuple[dict[str, object], ...] = ()
    #: SPEED-12: per-stage admission/executor/provider split of every provider
    #: call (``services/provider_call_timing.py``); absent when no call ran.
    provider_call_timing: dict[str, object] | None = None
    #: H4: deck-level geometry/colour instruments (``utils/authored_deck_
    #: instruments.py``); absent when the knob was off.
    deck_instruments: dict[str, object] | None = None
    #: H6: the one advisory vision pass over the finished deck (``services/
    #: authored_vision_advisory.py``); absent when the knob was off, otherwise
    #: always present -- a failed call is still recorded as its skip reason.
    vision_advisory: dict[str, object] | None = None
    provenance: dict[str, object] | None = None
    receipt_digest: str = ""
    observations: dict[str, dict[str, int]] = field(default_factory=dict)
    render_attempts: tuple[dict[str, object], ...] = ()
    render_attempts_dropped: int = 0
    runtime_provenance: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "attemptId": self.attempt_id,
            "attemptNumber": self.attempt_number,
            "revision": self.revision,
            "outcome": self.outcome,
            "stages": dict(self.stages),
            "queueWaitMillis": self.queue_wait_ms,
            "wallMillis": self.wall_ms,
            "logicalCalls": self.logical_calls,
            "retryReason": self.retry_reason,
            "tokenAvailability": self.token_availability,
            "costAvailability": self.cost_availability,
            "worker": self.worker,
            "terminalError": self.terminal_error,
            "validity": self.validity,
            "invalidReasons": list(self.invalid_reasons),
            "providerUsage": self.provider_usage,
            **optional_top_level_fields(
                provider_rate_profile=self.provider_rate_profile,
                normalizations=self.normalizations,
                quality_loop_variant=self.quality_loop_variant,
                flagged_slides=self.flagged_slides,
                fit_summaries=self.fit_summaries,
                provider_call_timing=self.provider_call_timing,
                deck_instruments=self.deck_instruments,
                vision_advisory=self.vision_advisory,
            ),
            "receiptDigest": self.receipt_digest,
            **({"provenance": self.provenance} if self.provenance is not None else {}),
            **({"observations": self.observations} if self.observations else {}),
            **({"renderAttempts": list(self.render_attempts)} if self.render_attempts else {}),
            **({"renderAttemptsDropped": self.render_attempts_dropped} if self.render_attempts_dropped else {}),
            **({"runtimeProvenance": self.runtime_provenance} if self.runtime_provenance is not None else {}),
        }


def _attempt_receipt_digest(receipt: GenerationAttemptTimingReceipt) -> str:
    payload = receipt.to_dict()
    payload.pop("receiptDigest", None)
    return canonical_json_digest(payload, allow_nan=True)


def _attempt_receipt_v2_digest(receipt: GenerationAttemptTimingReceiptV2) -> str:
    payload = receipt.to_dict()
    payload.pop("receiptDigest", None)
    # Diagnostics never participate in reuse or attestation identity.
    payload.pop("observations", None)
    payload.pop("renderAttempts", None)
    payload.pop("renderAttemptsDropped", None)
    payload.pop("runtimeProvenance", None)
    return canonical_json_digest(payload, allow_nan=True)


def _authoring_cooldown_wait(value: float | int) -> float | int:
    """Validate a writer-supplied cooldown wait with the reader's own rule."""

    return stage_cooldown_wait_seconds(
        {STAGE_COOLDOWN_WAIT_KEY: value}, stage_name="authoring"
    )


def _public_stage_receipts(
    value: object,
) -> tuple[tuple[str, dict[str, object]], ...]:
    if not isinstance(value, Mapping) or set(value) != set(PUBLIC_GENERATION_TIMING_STAGES):
        raise ValueError("generation_attempt_v2_stages_invalid")
    output: list[tuple[str, dict[str, object]]] = []
    for name in PUBLIC_GENERATION_TIMING_STAGES:
        raw = value.get(name)
        if not isinstance(raw, Mapping):
            raise ValueError("generation_attempt_v2_stages_invalid")
        state = raw.get("state")
        if state == "observed":
            # ``wallMillis`` (D13/E4) and the authoring-only
            # ``cooldownWaitSeconds`` (authored_generation_timing_receipt_e4.py)
            # are optional so a receipt written before them still loads.
            required_keys = {"state", "startMillis", "endMillis", "durationMillis"}
            if set(raw) != required_keys | stage_optional_keys(raw, stage_name=name):
                raise ValueError("generation_attempt_v2_stages_invalid")
            start = _bounded_timing_ms(raw.get("startMillis"), f"{name}_start")
            end = _bounded_timing_ms(raw.get("endMillis"), f"{name}_end")
            duration = _bounded_timing_ms(raw.get("durationMillis"), name)
            if end < start:
                raise ValueError("generation_attempt_v2_stages_invalid")
            normalized = {
                "state": "observed",
                "startMillis": start,
                "endMillis": end,
                "durationMillis": duration,
            }
            wall = stage_wall_millis(
                raw, stage_name=name, bounded_timing_ms=_bounded_timing_ms
            )
            if wall is not None:
                normalized["wallMillis"] = wall
            cooldown_wait = stage_cooldown_wait_seconds(raw, stage_name=name)
            if cooldown_wait is not None:
                normalized["cooldownWaitSeconds"] = cooldown_wait
        elif state == "not_started" and set(raw) == {"state"}:
            normalized = {"state": "not_started"}
        elif (
            state == "invalid"
            and set(raw) == {"state", "reason"}
            and raw.get("reason") == "executed_duration_missing"
        ):
            normalized = {
                "state": "invalid",
                "reason": "executed_duration_missing",
            }
        else:
            raise ValueError("generation_attempt_v2_stages_invalid")
        output.append((name, normalized))
    return tuple(output)


def _availability(value: object, field_name: str) -> str:
    if value not in {"available", "partial", "unavailable", "not_started"}:
        raise ValueError(f"generation_attempt_v2_{field_name}_invalid")
    return str(value)


def _optional_reason(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
        raise ValueError(f"generation_attempt_v2_{field_name}_invalid")
    return value.strip()


def _nested(value: object, key: str) -> object:
    """Read ``value[key]`` for the ``{"instruments": {"deck": ...}}`` /
    ``{"advisory": {"vision": ...}}`` wrapper shapes, or ``None`` when the
    outer key is absent or not a mapping."""
    return value.get(key) if isinstance(value, Mapping) else None


def _attempt_receipt_v2_from_dict(
    value: object,
) -> GenerationAttemptTimingReceiptV2:
    if not isinstance(value, Mapping):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    attempt_number = value.get("attemptNumber")
    revision = value.get("revision")
    outcome = value.get("outcome")
    validity = value.get("validity")
    invalid_reasons = value.get("invalidReasons")
    if (
        value.get("schemaVersion") != "generation-attempt-timing-receipt/v2"
        or isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
        or validity not in {"valid", "rc_invalid"}
        or not isinstance(invalid_reasons, Sequence)
        or isinstance(invalid_reasons, (str, bytes))
        or any(not isinstance(item, str) or not item for item in invalid_reasons)
    ):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    stages = _public_stage_receipts(value.get("stages"))
    derived_reasons = tuple(
        f"{name}:executed_duration_missing"
        for name, stage in stages
        if stage["state"] == "invalid"
    )
    reasons = tuple(str(item) for item in invalid_reasons)
    if reasons != derived_reasons or (validity == "valid") != (not reasons):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    terminal_error = _optional_reason(value.get("terminalError"), "terminal_error")
    if (outcome == "succeeded") != (terminal_error is None):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    worker = _optional_reason(value.get("worker"), "worker")
    if worker is None:
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    # E4 fields (providerRateProfile/normalizations/qualityLoop). Absent on a
    # receipt written before E4 landed -- ``None``/empty then, not invented.
    receipt = GenerationAttemptTimingReceiptV2(
        schema_version="generation-attempt-timing-receipt/v2",
        attempt_id=public_attempt_id(value.get("attemptId")),
        attempt_number=attempt_number,
        revision=revision,
        outcome=str(outcome),
        stages=stages,
        queue_wait_ms=_bounded_timing_ms(value.get("queueWaitMillis"), "queue_wait"),
        wall_ms=_bounded_timing_ms(value.get("wallMillis"), "wall"),
        logical_calls=_bounded_timing_ms(value.get("logicalCalls"), "logical_calls"),
        retry_reason=_optional_reason(value.get("retryReason"), "retry_reason"),
        token_availability=_availability(value.get("tokenAvailability"), "tokens"),
        cost_availability=_availability(value.get("costAvailability"), "cost"),
        worker=worker,
        terminal_error=terminal_error,
        validity=str(validity),
        invalid_reasons=reasons,
        provider_usage=_provider_usage(value.get("providerUsage")),
        provider_rate_profile=_e4_provider_rate_profile(
            value.get("providerRateProfile")
        ),
        normalizations=normalization_entries(value.get("normalizations")),
        quality_loop_variant=quality_loop_variant_field(
            value.get("qualityLoop"), optional_reason=_optional_reason
        ),
        flagged_slides=flagged_slide_entries(value.get("flaggedSlides")),
        fit_summaries=fit_summary_entries(value.get("fitSummary")),
        provider_call_timing=provider_call_timing_field(value.get("providerCallTiming")),
        deck_instruments=deck_instruments_field(_nested(value.get("instruments"), "deck")),
        vision_advisory=vision_advisory_field(_nested(value.get("advisory"), "vision")),
        provenance=speed_provenance(value.get("provenance")),
        runtime_provenance=runtime_provenance_field(value.get("runtimeProvenance")),
        observations=_optional_observations(value.get("observations")),
        render_attempts=_render_attempt_observations(value.get("renderAttempts")),
        render_attempts_dropped=_bounded_timing_ms(value.get("renderAttemptsDropped", 0), "render_dropped"),
        receipt_digest=_required_sha256(value.get("receiptDigest"), "receipt_digest"),
    )
    if receipt.receipt_digest != _attempt_receipt_v2_digest(receipt):
        raise ValueError("generation_attempt_timing_receipt_v2_digest_mismatch")
    return receipt


def _attempt_receipt_from_dict(value: object) -> GenerationAttemptTimingReceipt:
    if not isinstance(value, Mapping):
        raise ValueError("generation_attempt_timing_receipt_invalid")
    attempt_number = value.get("attemptNumber")
    revision = value.get("revision")
    outcome = value.get("outcome")
    failure_stage = value.get("failureStage")
    if (
        value.get("schemaVersion") != "generation-attempt-timing-receipt/v1"
        or isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
        or (
            failure_stage is not None
            and failure_stage not in GENERATION_ATTEMPT_TIMING_STAGES
        )
        or (outcome == "succeeded" and failure_stage is not None)
    ):
        raise ValueError("generation_attempt_timing_receipt_invalid")
    quality_digest = value.get("qualityReceiptDigest")
    receipt = GenerationAttemptTimingReceipt(
        schema_version="generation-attempt-timing-receipt/v1",
        attempt_id=public_attempt_id(value.get("attemptId")),
        attempt_number=attempt_number,
        revision=revision,
        outcome=str(outcome),
        failure_stage=failure_stage,
        stage_timings_ms=_partial_stage_timings(value.get("stagesMs")),
        total_ms=_bounded_timing_ms(value.get("totalMs"), "attempt_total"),
        quality_receipt_digest=(
            _required_sha256(quality_digest, "quality_receipt_digest")
            if quality_digest is not None
            else None
        ),
        provider_usage=_provider_usage(value.get("providerUsage")),
        checkpoint_signals=_checkpoint_signals(value.get("checkpointSignals", ())),
        causes=_attempt_causes(value.get("causes")),
        receipt_digest=_required_sha256(value.get("receiptDigest"), "receipt_digest"),
    )
    if receipt.receipt_digest != _attempt_receipt_digest(receipt):
        raise ValueError("generation_attempt_timing_receipt_digest_mismatch")
    return receipt


GenerationAttemptTimingReceiptAny = (
    GenerationAttemptTimingReceipt | GenerationAttemptTimingReceiptV2
)


def _load_attempt_records(path: Path) -> dict[str, GenerationAttemptTimingReceiptAny]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("generation_attempt_timing_manifest_corrupt") from error
    records = payload.get("receipts") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schemaVersion") != "generation-attempt-timing-manifest/v1"
        or not isinstance(records, Mapping)
        or payload.get("manifestDigest") != _manifest_digest(records)
    ):
        raise ValueError("generation_attempt_timing_manifest_corrupt")
    parsed: dict[str, GenerationAttemptTimingReceiptAny] = {}
    for key, item in records.items():
        receipt = (
            _attempt_receipt_v2_from_dict(item)
            if isinstance(item, Mapping)
            and item.get("schemaVersion") == "generation-attempt-timing-receipt/v2"
            else _attempt_receipt_from_dict(item)
        )
        expected = f"{receipt.attempt_id}#{receipt.attempt_number}#{receipt.revision}"
        if isinstance(receipt, GenerationAttemptTimingReceiptV2):
            expected += "#v2"
        if key != expected:
            raise ValueError("generation_attempt_timing_manifest_key_invalid")
        parsed[key] = receipt
    return parsed


def _persist_attempt_records(
    path: Path, records: Mapping[str, GenerationAttemptTimingReceiptAny]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {key: receipt.to_dict() for key, receipt in sorted(records.items())}
    envelope = {
        "schemaVersion": "generation-attempt-timing-manifest/v1",
        "receipts": serialized,
        "manifestDigest": _manifest_digest(serialized),
    }
    encoded = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def create_generation_attempt_timing_receipt_v2(
    *,
    job_id: object,
    attempt_id: object,
    attempt_number: int,
    revision: int,
    outcome: str,
    stage_observations: Mapping[str, Mapping[str, object]],
    executed_stages: Sequence[str],
    queue_wait_ms: object,
    wall_ms: object,
    provider_usage: Mapping[str, object],
    retry_reason: str | None,
    worker: str,
    terminal_error: str | None,
    provider_rate_profile: str | None = None,
    normalizations: Sequence[Mapping[str, object]] = (),
    quality_loop_variant: str | None = None,
    flagged_slides: Sequence[Mapping[str, object]] = (),
    fit_summaries: Sequence[Mapping[str, object]] | Mapping[object, object] = (),
    provider_call_timing: Mapping[str, object] | None = None,
    authoring_cooldown_wait_seconds: float | int | None = None,
    deck_instruments: Mapping[str, object] | None = None,
    vision_advisory: Mapping[str, object] | None = None,
    provenance: Mapping[str, object] | None = None,
    observations: Mapping[str, Mapping[str, object]] | None = None,
    render_attempts: Sequence[Mapping[str, object]] = (),
    render_attempts_dropped: int = 0,
    runtime_provenance: Mapping[str, object] | None = None,
) -> GenerationAttemptTimingReceiptV2:
    """Seal one v2 terminal receipt without prompt, content, or provider traces.

    ``provider_rate_profile``, ``normalizations`` and ``quality_loop_variant``
    are E4 additions (section 8 of the design doc) and all default to the
    absent/empty state a caller that does not supply them yet already
    produces: this stays backward compatible for a caller that has not been
    updated.
    """

    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or outcome not in GENERATION_ATTEMPT_OUTCOMES
    ):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    executed = frozenset(executed_stages)
    if not executed.issubset(PUBLIC_GENERATION_TIMING_STAGES):
        raise ValueError("generation_attempt_v2_executed_stages_invalid")
    normalized_observations: dict[str, dict[str, int]] = {}
    for raw_name, raw_observation in stage_observations.items():
        name = str(raw_name)
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_v2_stage_observation_invalid")
        if not isinstance(raw_observation, Mapping):
            raise ValueError("generation_attempt_v2_stage_observation_invalid")
        start = _bounded_timing_ms(raw_observation.get("startMillis"), f"{name}_start")
        end = _bounded_timing_ms(raw_observation.get("endMillis"), f"{name}_end")
        duration = _bounded_timing_ms(raw_observation.get("durationMillis"), name)
        if end < start:
            raise ValueError("generation_attempt_v2_stage_observation_invalid")
        normalized_observations[name] = {
            "startMillis": start,
            "endMillis": end,
            "durationMillis": duration,
        }

    public_sources = {
        "outline": ("outline",),
        "provider": ("provider",),
        "authoring": ("author",),
        "repair": ("repair",),
        "chromiumReview": ("render", "quality"),
        "publication": ("publication",),
    }
    public_stages: dict[str, dict[str, object]] = {}
    invalid_reasons: list[str] = []
    for public_name in PUBLIC_GENERATION_TIMING_STAGES:
        source_observations = [
            normalized_observations[source_name]
            for source_name in public_sources[public_name]
            if source_name in normalized_observations
        ]
        if source_observations:
            merged_start = min(item["startMillis"] for item in source_observations)
            merged_end = max(item["endMillis"] for item in source_observations)
            public_stages[public_name] = {
                "state": "observed",
                "startMillis": merged_start,
                "endMillis": merged_end,
                "durationMillis": sum(item["durationMillis"] for item in source_observations),
                # D13: the merged window's real wall span, next to the summed
                # per-source work above (authored_generation_timing_receipt_e4.py).
                "wallMillis": merged_stage_wall_millis(merged_start, merged_end),
            }
            # F3: the authoring stage alone carries the cooldown the wave
            # scheduler slept out, so a slow attempt can be read as "waiting on
            # the provider" rather than "slow to author".
            if (
                public_name == "authoring"
                and authoring_cooldown_wait_seconds is not None
            ):
                public_stages[public_name][STAGE_COOLDOWN_WAIT_KEY] = (
                    _authoring_cooldown_wait(authoring_cooldown_wait_seconds)
                )
        elif public_name in executed:
            public_stages[public_name] = {
                "state": "invalid",
                "reason": "executed_duration_missing",
            }
            invalid_reasons.append(f"{public_name}:executed_duration_missing")
        else:
            public_stages[public_name] = {"state": "not_started"}

    safe_usage = _provider_usage(provider_usage)
    if safe_usage is None:
        raise ValueError("generation_attempt_provider_usage_invalid")
    totals = safe_usage["totals"]
    assert isinstance(totals, Mapping)
    logical_calls = int(totals["logicalCalls"])
    reported_calls = int(totals["reportedUsageCalls"])
    unavailable_calls = int(totals["unavailableUsageCalls"])
    priced_calls = int(totals["pricedCalls"])
    estimated_cost = int(totals["estimatedCostMicrounits"])
    if logical_calls == 0:
        token_availability = "not_started"
        cost_availability = "not_started"
    else:
        token_availability = (
            "available"
            if reported_calls >= logical_calls and unavailable_calls == 0
            else "partial"
            if reported_calls > 0
            else "unavailable"
        )
        cost_availability = (
            "available"
            if priced_calls >= logical_calls
            else "partial"
            if priced_calls > 0 or estimated_cost > 0
            else "unavailable"
        )

    receipt = GenerationAttemptTimingReceiptV2(
        schema_version="generation-attempt-timing-receipt/v2",
        attempt_id=public_attempt_id(attempt_id),
        attempt_number=attempt_number,
        revision=revision,
        outcome=outcome,
        stages=_public_stage_receipts(public_stages),
        queue_wait_ms=_bounded_timing_ms(queue_wait_ms, "queue_wait"),
        wall_ms=_bounded_timing_ms(wall_ms, "wall"),
        logical_calls=logical_calls,
        retry_reason=_optional_reason(retry_reason, "retry_reason"),
        token_availability=token_availability,
        cost_availability=cost_availability,
        worker=_optional_reason(worker, "worker") or "workspace-generation-worker",
        terminal_error=_optional_reason(terminal_error, "terminal_error"),
        validity="rc_invalid" if invalid_reasons else "valid",
        invalid_reasons=tuple(invalid_reasons),
        provider_usage=safe_usage,
        provider_rate_profile=_e4_provider_rate_profile(provider_rate_profile),
        normalizations=normalization_entries(normalizations),
        quality_loop_variant=_optional_reason(
            quality_loop_variant, "quality_loop_variant"
        ),
        flagged_slides=flagged_slide_entries(flagged_slides),
        fit_summaries=fit_summary_entries(fit_summaries),
        provider_call_timing=provider_call_timing_field(provider_call_timing),
        deck_instruments=deck_instruments_field(deck_instruments),
        vision_advisory=vision_advisory_field(vision_advisory),
        provenance=speed_provenance(provenance),
        runtime_provenance=runtime_provenance_field(runtime_provenance),
        observations=_optional_observations(observations),
        render_attempts=_render_attempt_observations(render_attempts),
        render_attempts_dropped=_bounded_timing_ms(render_attempts_dropped, "render_dropped"),
    )
    if (outcome == "succeeded") != (receipt.terminal_error is None):
        raise ValueError("generation_attempt_timing_receipt_v2_invalid")
    receipt = replace(receipt, receipt_digest=_attempt_receipt_v2_digest(receipt))
    path = generation_attempt_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_attempt_records(path)
        key = f"{receipt.attempt_id}#{receipt.attempt_number}#{receipt.revision}#v2"
        existing = records.get(key)
        if existing is not None:
            if existing != receipt:
                raise ValueError("generation_attempt_timing_receipt_binding_changed")
            assert isinstance(existing, GenerationAttemptTimingReceiptV2)
            return existing
        records[key] = receipt
        _persist_attempt_records(path, records)
    return receipt


def load_generation_attempt_timing_receipts(
    job_id: object,
) -> tuple[GenerationAttemptTimingReceiptAny, ...]:
    """Load every verified attempt receipt in deterministic attempt order."""

    path = generation_attempt_timing_receipt_path(job_id)
    with _manifest_lock(path):
        records = _load_attempt_records(path)
    return tuple(
        sorted(
            records.values(),
            key=lambda receipt: (
                receipt.attempt_number,
                receipt.revision,
                receipt.attempt_id,
            ),
        )
    )


@dataclass(slots=True)
class GenerationTimingRecorder:
    """Monotonic, exception-safe stage collector for a final attempt receipt."""

    started_at: float = field(default_factory=monotonic)
    _stage_timings_ms: dict[str, int] = field(default_factory=dict)
    _stage_observations: dict[str, dict[str, int]] = field(default_factory=dict)
    _checkpoint_signals: list[dict[str, object]] = field(default_factory=list)
    _observations: dict[str, dict[str, int]] = field(default_factory=dict)
    _render_attempts: list[dict[str, object]] = field(default_factory=list)
    render_attempts_dropped: int = 0
    _render_attempts_evicted: int = 0
    _runtime_provenance: dict[str, object] | None = None

    def record_runtime_provenance(self, value: object) -> None:
        if self._runtime_provenance is None:
            self._runtime_provenance = runtime_provenance_field(value)

    def runtime_provenance(self) -> dict[str, object] | None:
        return runtime_provenance_field(self._runtime_provenance)

    def record_render_attempt(self, receipt: dict[str, object]) -> None:
        """Keep bounded recent detail while all windows enter aggregate observations."""
        if len(self._render_attempts) == MAX_RENDER_ATTEMPT_OBSERVATIONS:
            self._render_attempts.pop(0)
            self._render_attempts_evicted += 1
            self.render_attempts_dropped += 1
        self._render_attempts.append(receipt)

    def render_attempts(self) -> tuple[dict[str, object], ...]:
        """Snapshot closed, valid diagnostics; unfinished work cannot fail a receipt.

        Recompute the rejected count so repeated snapshots neither double count
        open attempts nor prevent them from appearing after they finish.
        """
        result = []
        dropped = self._render_attempts_evicted
        for attempt in self._render_attempts:
            try:
                if not isinstance(attempt, Mapping) or not attempt.get("closed"):
                    raise ValueError("generation_render_attempt_open")
                result.extend(_render_attempt_observations([attempt]))
            except (TypeError, ValueError, OverflowError):
                dropped += 1
        self.render_attempts_dropped = dropped
        return tuple(result)

    @contextmanager
    def observe(self, name: str) -> Iterator[None]:
        if not _optional_observation_name(name):
            raise ValueError("generation_optional_observations_invalid")
        started = monotonic()
        try:
            yield
        finally:
            self.record_observation(name, started, monotonic())

    def record_observation(self, name: str, started: float, ended: float,
                           *, duration_ms: float | None = None) -> None:
        entry = _optional_observations({name: {
            "startMillis": max(0.0, (started - self.started_at) * 1000),
            "endMillis": max(0.0, (ended - self.started_at) * 1000),
            "durationMillis": max(0.0, (ended - started) * 1000 if duration_ms is None else duration_ms),
        }})[name]
        previous = self._observations.get(name)
        if previous is not None:
            entry["startMillis"] = min(entry["startMillis"], previous["startMillis"])
            entry["endMillis"] = max(entry["endMillis"], previous["endMillis"])
            entry["durationMillis"] = min(MAX_GENERATION_STAGE_TIMING_MS,
                entry["durationMillis"] + previous["durationMillis"])
        self._observations[name] = entry

    def observations(self) -> dict[str, dict[str, int]]:
        return {name: dict(value) for name, value in self._observations.items()}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stage_invalid")
        started = monotonic()
        start_ms = _bounded_timing_ms(
            (started - self.started_at) * 1000,
            f"{name}_start",
        )
        try:
            yield
        finally:
            ended = monotonic()
            elapsed = _bounded_timing_ms(
                (ended - started) * 1000,
                name,
            )
            self._stage_timings_ms[name] = min(
                MAX_GENERATION_STAGE_TIMING_MS,
                self._stage_timings_ms.get(name, 0) + elapsed,
            )
            previous = self._stage_observations.get(name)
            self._stage_observations[name] = {
                "startMillis": min(
                    start_ms,
                    previous["startMillis"] if previous is not None else start_ms,
                ),
                "endMillis": _bounded_timing_ms(
                    (ended - self.started_at) * 1000,
                    f"{name}_end",
                ),
                "durationMillis": self._stage_timings_ms[name],
            }

    def record_stage_ms(self, name: str, value: object) -> None:
        if name not in GENERATION_ATTEMPT_TIMING_STAGES:
            raise ValueError("generation_attempt_timing_stage_invalid")
        duration = _bounded_timing_ms(value, name)
        end_ms = self.elapsed_ms
        self._stage_timings_ms[name] = duration
        self._stage_observations[name] = {
            "startMillis": max(0, end_ms - duration),
            "endMillis": end_ms,
            "durationMillis": duration,
        }

    def record_checkpoint_signal(self, signal: Mapping[str, object]) -> None:
        self._checkpoint_signals.append(dict(_checkpoint_signals((signal,))[0]))

    def snapshot_stage_timings_ms(self) -> dict[str, int]:
        return dict(self._stage_timings_ms)

    def stage_observations(self) -> dict[str, dict[str, int]]:
        return {
            name: dict(observation)
            for name, observation in self._stage_observations.items()
        }

    def checkpoint_signals(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(signal) for signal in self._checkpoint_signals)

    @property
    def elapsed_ms(self) -> int:
        return _bounded_timing_ms(
            (monotonic() - self.started_at) * 1000,
            "attempt_total",
        )


__all__ = [
    "GENERATION_ATTEMPT_OUTCOMES",
    "GENERATION_ATTEMPT_TIMING_STAGES",
    "GenerationAttemptTimingReceipt",
    "GenerationAttemptTimingReceiptV2",
    "GenerationTimingRecorder",
    "GenerationTimingReceipt",
    "create_generation_attempt_timing_receipt_v2",
    "create_generation_timing_receipt",
    "generation_attempt_timing_receipt_path",
    "generation_timing_receipt_path",
    "load_generation_attempt_timing_receipts",
    "load_generation_timing_receipt",
]
