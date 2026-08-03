"""Dependency-free, low-cardinality workspace metrics and SLO evaluation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import os
from threading import RLock
from typing import Mapping

from models.workspace_operation_kinds import WORKSPACE_OPERATION_KINDS

_LAG_BUCKETS_MS = (100, 500, 1_000, 5_000, 15_000, 60_000)
_OPERATION_NAMES = frozenset(
    {
        "accepted",
        "claimed",
        "running",
        "retry_scheduled",
        "cancel_requested",
        "cancelled",
        "recovered",
        "succeeded",
        "failed",
        "dead_letter",
        "blocked",
        "lease_lost",
        "released",
        "runner_fault",
    }
)
_OPERATION_STATUSES = frozenset(
    {
        "queued",
        "waiting_for_assets",
        "blocked",
        "approval_required",
        "awaiting_approval",
        "running",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
        "dead_letter",
    }
)
_COMMAND_TYPES = frozenset(WORKSPACE_OPERATION_KINDS)
_ROLLOUT_REASONS = frozenset(
    {
        "none",
        "chat_first_feature_disabled",
        "chat_first_runtime_unsupported",
        "chat_first_runtime_profile_required",
        "chat_first_runtime_profile_invalid",
        "chat_first_runtime_profile_mismatch",
        "chat_first_kill_switch_enabled",
        "chat_first_cohort_excluded",
    }
)
_CLEANUP_OUTCOMES = frozenset(
    {
        "claimed",
        "duplicate",
        "succeeded",
        "retry_wait",
        "dead_letter",
        "stale",
    }
)
_OUTBOX_OUTCOMES = frozenset(
    {
        "dispatched",
        "delivered",
        "event_delivered",
        "retry_wait",
        "dead_letter",
        "lease_lost",
    }
)
_OBSERVABILITY_SIGNALS = frozenset(
    {
        "event_lag",
        "operation_observation",
        "shadow_parity",
    }
)


def _threshold(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class WorkspaceSLOStatus:
    healthy: bool
    silent_loss_total: int
    shadow_parity_ratio: float
    error_ratio: float
    dead_letter_total: int
    dead_letter_ratio: float
    lease_lost_total: int
    lease_lost_ratio: float
    revision_conflict_total: int
    revision_conflict_ratio: float
    cleanup_dead_letter_total: int
    cleanup_dead_letter_ratio: float
    outbox_dead_letter_total: int
    outbox_dead_letter_ratio: float
    outbox_lease_lost_total: int
    outbox_lease_lost_ratio: float
    event_lag_max_ms: int
    violations: tuple[str, ...]


class WorkspaceMetrics:
    """Small in-process exporter; metrics failures never affect workflow state."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._admissions: dict[str, int] = defaultdict(int)
        self._operations: dict[tuple[str, str, str], int] = defaultdict(int)
        self._cleanup: dict[str, int] = defaultdict(int)
        self._outbox: dict[str, int] = defaultdict(int)
        self._rollout: dict[tuple[str, str], int] = defaultdict(int)
        self._lag_count = 0
        self._lag_sum_ms = 0
        self._lag_max_ms = 0
        self._lag_buckets: dict[int, int] = defaultdict(int)
        self._shadow_total = 0
        self._shadow_matches = 0
        self._silent_loss = 0
        self._signal_failures: dict[str, int] = defaultdict(int)
        self._dead_letter_total = 0
        self._lease_lost_total = 0
        self._revision_conflict_total = 0

    def reset(self) -> None:
        with self._lock:
            self._admissions.clear()
            self._operations.clear()
            self._cleanup.clear()
            self._outbox.clear()
            self._rollout.clear()
            self._lag_count = 0
            self._lag_sum_ms = 0
            self._lag_max_ms = 0
            self._lag_buckets.clear()
            self._shadow_total = 0
            self._shadow_matches = 0
            self._silent_loss = 0
            self._signal_failures.clear()
            self._dead_letter_total = 0
            self._lease_lost_total = 0
            self._revision_conflict_total = 0

    def record_operation_admission(self, command_type: str) -> None:
        """Count one newly persisted, non-duplicate operation admission."""

        command = command_type if command_type in _COMMAND_TYPES else "other"
        with self._lock:
            self._admissions[command] += 1

    def record_revision_conflict(self) -> None:
        """Count a stale-revision rejection that occurs before an operation exists."""

        with self._lock:
            self._revision_conflict_total += 1

    def record_cleanup_outcome(self, outcome: str) -> None:
        """Count one durable cleanup-journal transition using a closed label set."""

        normalized = outcome if outcome in _CLEANUP_OUTCOMES else "other"
        with self._lock:
            self._cleanup[normalized] += 1

    def record_outbox_outcome(self, outcome: str) -> None:
        """Count one durable outbox delivery outcome using a closed label set."""

        normalized = outcome if outcome in _OUTBOX_OUTCOMES else "other"
        with self._lock:
            self._outbox[normalized] += 1

    def record_event_lag_ms(self, lag_ms: int) -> None:
        """Observe durable operation-event creation to successful delivery lag."""

        if not isinstance(lag_ms, int) or isinstance(lag_ms, bool) or lag_ms < 0:
            return
        with self._lock:
            self._lag_count += 1
            self._lag_sum_ms += lag_ms
            self._lag_max_ms = max(self._lag_max_ms, lag_ms)
            for boundary in _LAG_BUCKETS_MS:
                if lag_ms <= boundary:
                    self._lag_buckets[boundary] += 1

    def observe_operation(self, event: Mapping[str, object]) -> None:
        raw_name = str(event.get("name") or "other")
        raw_status = str(event.get("result_status") or "none")
        raw_command = str(event.get("command_type") or "none")
        name = raw_name if raw_name in _OPERATION_NAMES else "other"
        status = (
            raw_status
            if raw_status == "none" or raw_status in _OPERATION_STATUSES
            else "other"
        )
        command = (
            raw_command
            if raw_command == "none" or raw_command in _COMMAND_TYPES
            else "other"
        )
        error_code = str(event.get("error_code") or "")
        fence_outcome = str(event.get("fence_outcome") or "")
        is_dead_letter = name == "dead_letter" or status == "dead_letter"
        is_lease_lost = (
            name == "lease_lost"
            or error_code
            in {"operation_lease_lost", "workspace_operation_lease_lost"}
            or fence_outcome == "lease_lost"
        )
        is_revision_conflict = (
            event.get("revision_conflict") is True
            or error_code in {"revision_conflict", "stale_presentation_revision"}
        )
        with self._lock:
            self._operations[(name, status, command)] += 1
            self._dead_letter_total += int(is_dead_letter)
            self._lease_lost_total += int(is_lease_lost)
            self._revision_conflict_total += int(is_revision_conflict)
        lag = event.get("event_lag_ms")
        if isinstance(lag, int) and not isinstance(lag, bool) and lag >= 0:
            self.record_event_lag_ms(lag)

    def record_rollout(self, *, enabled: bool, reason: str | None) -> None:
        # Both dimensions are finite values originating in rollout policy.
        outcome = "enabled" if enabled else "disabled"
        candidate = reason or "none"
        normalized_reason = (
            candidate if candidate in _ROLLOUT_REASONS else "other"
        )
        with self._lock:
            self._rollout[(outcome, normalized_reason)] += 1

    def record_shadow_parity(self, *, matched: bool, silent_loss: bool = False) -> None:
        with self._lock:
            self._shadow_total += 1
            self._shadow_matches += int(matched)
            self._silent_loss += int(silent_loss)

    def record_signal_failure(self, signal: str) -> None:
        """Count a best-effort telemetry failure with a closed signal label."""

        normalized = signal if signal in _OBSERVABILITY_SIGNALS else "other"
        with self._lock:
            self._signal_failures[normalized] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "admissions": dict(self._admissions),
                "operations": dict(self._operations),
                "cleanup": dict(self._cleanup),
                "outbox": dict(self._outbox),
                "rollout": dict(self._rollout),
                "event_lag_count": self._lag_count,
                "event_lag_sum_ms": self._lag_sum_ms,
                "event_lag_max_ms": self._lag_max_ms,
                "event_lag_buckets": dict(self._lag_buckets),
                "shadow_total": self._shadow_total,
                "shadow_matches": self._shadow_matches,
                "silent_loss_total": self._silent_loss,
                "signal_failures": dict(self._signal_failures),
                "dead_letter_total": self._dead_letter_total,
                "lease_lost_total": self._lease_lost_total,
                "revision_conflict_total": self._revision_conflict_total,
            }

    def evaluate_slo(self) -> WorkspaceSLOStatus:
        snapshot = self.snapshot()
        # Operation observations include claim, retry, recovery and terminal
        # transitions, so they are not a stable request denominator.  Use only
        # newly persisted non-duplicate admissions for all operation ratios.
        total = sum(snapshot["admissions"].values())
        errors = sum(
            count
            for (name, status, _), count in snapshot["operations"].items()
            if name in {"failed", "dead_letter", "runner_fault"}
            or status in {"failed", "dead_letter"}
        )
        error_ratio = min(errors / total, 1.0) if total else 0.0
        dead_letter_total = int(snapshot["dead_letter_total"])
        lease_lost_total = int(snapshot["lease_lost_total"])
        revision_conflict_total = int(snapshot["revision_conflict_total"])
        dead_letter_ratio = min(dead_letter_total / total, 1.0) if total else 0.0
        lease_lost_ratio = min(lease_lost_total / total, 1.0) if total else 0.0
        revision_conflict_ratio = (
            min(revision_conflict_total / total, 1.0) if total else 0.0
        )
        cleanup_claimed_total = int(snapshot["cleanup"].get("claimed", 0))
        cleanup_dead_letter_total = int(
            snapshot["cleanup"].get("dead_letter", 0)
        )
        cleanup_dead_letter_ratio = (
            min(cleanup_dead_letter_total / cleanup_claimed_total, 1.0)
            if cleanup_claimed_total
            else 0.0
        )
        outbox_dispatched_total = int(snapshot["outbox"].get("dispatched", 0))
        outbox_dead_letter_total = int(
            snapshot["outbox"].get("dead_letter", 0)
        )
        outbox_lease_lost_total = int(
            snapshot["outbox"].get("lease_lost", 0)
        )
        outbox_dead_letter_ratio = (
            min(outbox_dead_letter_total / outbox_dispatched_total, 1.0)
            if outbox_dispatched_total
            else 0.0
        )
        outbox_lease_lost_ratio = (
            min(outbox_lease_lost_total / outbox_dispatched_total, 1.0)
            if outbox_dispatched_total
            else 0.0
        )
        shadow_total = int(snapshot["shadow_total"])
        parity = (
            int(snapshot["shadow_matches"]) / shadow_total
            if shadow_total
            else 1.0
        )
        violations: list[str] = []
        if int(snapshot["silent_loss_total"]) > 0:
            violations.append("workspace_silent_loss")
        if parity < _threshold("CHAT_FIRST_WORKSPACE_SLO_SHADOW_PARITY", 0.999):
            violations.append("workspace_shadow_parity")
        if error_ratio > _threshold("CHAT_FIRST_WORKSPACE_SLO_ERROR_RATIO", 0.01):
            violations.append("workspace_error_ratio")
        if dead_letter_ratio > _threshold(
            "CHAT_FIRST_WORKSPACE_SLO_DEAD_LETTER_RATIO", 0.001
        ):
            violations.append("workspace_dead_letter")
        if lease_lost_ratio > _threshold(
            "CHAT_FIRST_WORKSPACE_SLO_LEASE_LOST_RATIO", 0.001
        ):
            violations.append("workspace_lease_lost")
        if revision_conflict_ratio > _threshold(
            "CHAT_FIRST_WORKSPACE_SLO_REVISION_CONFLICT_RATIO", 0.05
        ):
            violations.append("workspace_revision_conflict")
        if cleanup_dead_letter_ratio > _threshold(
            "CHAT_FIRST_WORKSPACE_SLO_CLEANUP_DEAD_LETTER_RATIO", 0.001
        ):
            violations.append("workspace_cleanup_dead_letter")
        if outbox_dead_letter_ratio > _threshold(
            "CHAT_FIRST_WORKSPACE_SLO_OUTBOX_DEAD_LETTER_RATIO", 0.001
        ):
            violations.append("workspace_outbox_dead_letter")
        if outbox_lease_lost_ratio > _threshold(
            "CHAT_FIRST_WORKSPACE_SLO_OUTBOX_LEASE_LOST_RATIO", 0.001
        ):
            violations.append("workspace_outbox_lease_lost")
        if int(snapshot["event_lag_max_ms"]) > int(
            _threshold("CHAT_FIRST_WORKSPACE_SLO_EVENT_LAG_MS", 15_000)
        ):
            violations.append("workspace_event_lag")
        return WorkspaceSLOStatus(
            healthy=not violations,
            silent_loss_total=int(snapshot["silent_loss_total"]),
            shadow_parity_ratio=parity,
            error_ratio=error_ratio,
            dead_letter_total=dead_letter_total,
            dead_letter_ratio=dead_letter_ratio,
            lease_lost_total=lease_lost_total,
            lease_lost_ratio=lease_lost_ratio,
            revision_conflict_total=revision_conflict_total,
            revision_conflict_ratio=revision_conflict_ratio,
            cleanup_dead_letter_total=cleanup_dead_letter_total,
            cleanup_dead_letter_ratio=cleanup_dead_letter_ratio,
            outbox_dead_letter_total=outbox_dead_letter_total,
            outbox_dead_letter_ratio=outbox_dead_letter_ratio,
            outbox_lease_lost_total=outbox_lease_lost_total,
            outbox_lease_lost_ratio=outbox_lease_lost_ratio,
            event_lag_max_ms=int(snapshot["event_lag_max_ms"]),
            violations=tuple(violations),
        )

    def prometheus_text(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "# HELP workspace_operation_admission_total Newly persisted non-duplicate workspace operations.",
            "# TYPE workspace_operation_admission_total counter",
        ]
        for command, count in sorted(snapshot["admissions"].items()):
            lines.append(
                'workspace_operation_admission_total{command="%s"} %d'
                % (command, count)
            )
        lines.extend([
            "# HELP workspace_operation_total Durable workspace operation observations.",
            "# TYPE workspace_operation_total counter",
        ])
        for (name, status, command), count in sorted(snapshot["operations"].items()):
            lines.append(
                'workspace_operation_total{name="%s",status="%s",command="%s"} %d'
                % (name, status, command, count)
            )
        lines.extend(
            (
                "# HELP workspace_event_lag_ms Workspace event publication lag.",
                "# TYPE workspace_event_lag_ms histogram",
            )
        )
        for boundary in _LAG_BUCKETS_MS:
            lines.append(
                f'workspace_event_lag_ms_bucket{{le="{boundary}"}} '
                f'{snapshot["event_lag_buckets"].get(boundary, 0)}'
            )
        lines.append(f'workspace_event_lag_ms_bucket{{le="+Inf"}} {snapshot["event_lag_count"]}')
        lines.append(f'workspace_event_lag_ms_sum {snapshot["event_lag_sum_ms"]}')
        lines.append(f'workspace_event_lag_ms_count {snapshot["event_lag_count"]}')
        lines.extend((
            "# HELP workspace_rollout_decision_total Workspace write admission decisions.",
            "# TYPE workspace_rollout_decision_total counter",
        ))
        for (outcome, reason), count in sorted(snapshot["rollout"].items()):
            lines.append(
                'workspace_rollout_decision_total{outcome="%s",reason="%s"} %d'
                % (outcome, reason, count)
            )
        lines.append(f'workspace_shadow_parity_total {snapshot["shadow_total"]}')
        lines.append(f'workspace_shadow_parity_match_total {snapshot["shadow_matches"]}')
        lines.append(f'workspace_silent_loss_total {snapshot["silent_loss_total"]}')
        lines.extend((
            "# HELP workspace_observability_signal_failure_total Best-effort telemetry signal failures.",
            "# TYPE workspace_observability_signal_failure_total counter",
        ))
        for signal, count in sorted(snapshot["signal_failures"].items()):
            lines.append(
                'workspace_observability_signal_failure_total{signal="%s"} %d'
                % (signal, count)
            )
        lines.extend((
            "# HELP workspace_cleanup_total Durable cleanup-journal outcomes.",
            "# TYPE workspace_cleanup_total counter",
        ))
        for outcome, count in sorted(snapshot["cleanup"].items()):
            lines.append(
                'workspace_cleanup_total{outcome="%s"} %d' % (outcome, count)
            )
        lines.extend((
            "# HELP workspace_cleanup_dead_letter_total Cleanup journal entries observed in dead letter state.",
            "# TYPE workspace_cleanup_dead_letter_total counter",
            "workspace_cleanup_dead_letter_total %d"
            % snapshot["cleanup"].get("dead_letter", 0),
        ))
        lines.extend((
            "# HELP workspace_outbox_total Durable workspace outbox delivery outcomes.",
            "# TYPE workspace_outbox_total counter",
        ))
        for outcome, count in sorted(snapshot["outbox"].items()):
            lines.append(
                'workspace_outbox_total{outcome="%s"} %d' % (outcome, count)
            )
        lines.extend(
            (
                "# HELP workspace_dead_letter_total Workspace operations observed in dead letter state.",
                "# TYPE workspace_dead_letter_total counter",
                f'workspace_dead_letter_total {snapshot["dead_letter_total"]}',
                "# HELP workspace_lease_lost_total Workspace operation lease-loss observations.",
                "# TYPE workspace_lease_lost_total counter",
                f'workspace_lease_lost_total {snapshot["lease_lost_total"]}',
                "# HELP workspace_revision_conflict_total Workspace revision-conflict observations.",
                "# TYPE workspace_revision_conflict_total counter",
                f'workspace_revision_conflict_total {snapshot["revision_conflict_total"]}',
            )
        )
        return "\n".join(lines) + "\n"


WORKSPACE_METRICS = WorkspaceMetrics()
