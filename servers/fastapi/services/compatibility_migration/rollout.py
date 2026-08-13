"""Fail-closed rollout controls for the authored-only migration.

The module is intentionally pure: it does not mutate flags, deploy code, or
query production telemetry.  Callers provide owner identity and aggregate
evidence, then apply the returned decision through their normal configuration
and change-management path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import hmac
from typing import Iterable, Literal


_BUCKET_COUNT = 10_000


class CanaryStage(str, Enum):
    INTERNAL = "internal"
    PERCENT_1 = "1"
    PERCENT_5 = "5"
    PERCENT_25 = "25"
    PERCENT_50 = "50"
    PERCENT_100 = "100"

    @property
    def basis_points(self) -> int:
        if self is CanaryStage.INTERNAL:
            return 0
        return int(self.value) * 100


CANARY_STAGES = tuple(CanaryStage)


def deterministic_owner_bucket(owner_id: object, *, salt: str) -> int:
    """Return a stable, non-identifying owner bucket in ``0..9999``.

    A deployment-specific, immutable salt makes the assignment reproducible
    across processes while avoiding emission of the owner identifier itself.
    Owner bucketing (rather than request bucketing) prevents one owner's decks
    from being split across compatibility runtimes.
    """

    normalized_owner = str(owner_id).strip()
    normalized_salt = salt.strip()
    if not normalized_owner:
        raise ValueError("authored_only_owner_id_required")
    if not normalized_salt:
        raise ValueError("authored_only_rollout_salt_required")
    digest = hmac.new(
        normalized_salt.encode("utf-8"),
        f"authored-only-owner-v1\0{normalized_owner}".encode("utf-8"),
        sha256,
    ).digest()
    return int.from_bytes(digest[:8], "big") % _BUCKET_COUNT


@dataclass(frozen=True, slots=True)
class AuthoredOnlyRolloutFlags:
    """A snapshot of authored-only rollout configuration."""

    stage: CanaryStage
    salt: str
    internal_owner_ids: frozenset[str] = frozenset()
    enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.stage, CanaryStage):
            raise ValueError("authored_only_canary_stage_invalid")
        if not self.salt.strip():
            raise ValueError("authored_only_rollout_salt_required")
        if any(not owner_id.strip() for owner_id in self.internal_owner_ids):
            raise ValueError("authored_only_internal_owner_id_invalid")

    def owner_enabled(self, owner_id: object) -> bool:
        """Resolve an owner without changing persisted state."""

        normalized_owner = str(owner_id).strip()
        if not normalized_owner:
            raise ValueError("authored_only_owner_id_required")
        if not self.enabled:
            return False
        if normalized_owner in self.internal_owner_ids:
            return True
        return deterministic_owner_bucket(
            normalized_owner,
            salt=self.salt,
        ) < self.stage.basis_points

    def capability_manifest(self, owner_id: object) -> dict[str, object]:
        """Return content-free UI capabilities for the owner."""

        admitted = self.owner_enabled(owner_id)
        return {
            "schema_version": 1,
            "authored_only_enabled": admitted,
            "canary_stage": self.stage.value,
            "legacy_create": False,
            "legacy_mutate": False,
            "legacy_open": True,
            "legacy_preview": True,
            "legacy_export": True,
            "legacy_download": True,
            "legacy_delete": True,
            "legacy_upgrade_fork": admitted,
        }


def next_canary_stage(stage: CanaryStage) -> CanaryStage | None:
    position = CANARY_STAGES.index(stage)
    if position == len(CANARY_STAGES) - 1:
        return None
    return CANARY_STAGES[position + 1]


def previous_canary_stage(stage: CanaryStage) -> CanaryStage:
    position = CANARY_STAGES.index(stage)
    if position == 0:
        return CanaryStage.INTERNAL
    return CANARY_STAGES[position - 1]


@dataclass(frozen=True, slots=True)
class AvailabilitySample:
    attempts: int
    successes: int
    p95_latency_ms: float

    def __post_init__(self) -> None:
        if self.attempts < 0 or self.successes < 0:
            raise ValueError("authored_only_slo_count_invalid")
        if self.successes > self.attempts:
            raise ValueError("authored_only_slo_success_count_invalid")
        if self.p95_latency_ms < 0:
            raise ValueError("authored_only_slo_latency_invalid")

    @property
    def success_ratio(self) -> float | None:
        if self.attempts == 0:
            return None
        return self.successes / self.attempts


@dataclass(frozen=True, slots=True)
class CanarySLO:
    minimum_attempts: int = 1_000
    read_success_ratio: float = 0.999
    export_success_ratio: float = 0.999
    hard_failure_ratio: float = 0.01
    read_p95_latency_ms: float = 2_000
    export_p95_latency_ms: float = 30_000

    def __post_init__(self) -> None:
        if self.minimum_attempts < 1:
            raise ValueError("authored_only_slo_minimum_attempts_invalid")
        for value in (
            self.read_success_ratio,
            self.export_success_ratio,
        ):
            if not 0 < value <= 1:
                raise ValueError("authored_only_slo_ratio_invalid")
        if not 0 <= self.hard_failure_ratio < 1:
            raise ValueError("authored_only_slo_hard_failure_ratio_invalid")
        if self.read_p95_latency_ms <= 0 or self.export_p95_latency_ms <= 0:
            raise ValueError("authored_only_slo_latency_threshold_invalid")


@dataclass(frozen=True, slots=True)
class CanaryWindow:
    read: AvailabilitySample
    export: AvailabilitySample
    owner_isolation_violations: int = 0
    source_or_archive_digest_mismatches: int = 0
    original_immutability_violations: int = 0
    legacy_admission_violations: int = 0
    rollback_probe_passed: bool = True

    def __post_init__(self) -> None:
        counters = (
            self.owner_isolation_violations,
            self.source_or_archive_digest_mismatches,
            self.original_immutability_violations,
            self.legacy_admission_violations,
        )
        if any(value < 0 for value in counters):
            raise ValueError("authored_only_canary_violation_count_invalid")


CanaryAction = Literal["advance", "hold", "kill", "rollback", "complete"]


@dataclass(frozen=True, slots=True)
class CanaryDecision:
    action: CanaryAction
    current_stage: CanaryStage
    recommended_stage: CanaryStage
    reason_codes: tuple[str, ...]

    @property
    def may_expand(self) -> bool:
        return self.action in {"advance", "complete"}

    def require_may_expand(self) -> None:
        if not self.may_expand:
            joined = ",".join(self.reason_codes)
            raise RuntimeError(f"authored_only_canary_expansion_blocked:{joined}")


def evaluate_canary(
    *,
    stage: CanaryStage,
    window: CanaryWindow,
    slo: CanarySLO | None = None,
) -> CanaryDecision:
    """Classify evidence into advance, hold, kill, or rollback.

    Integrity, isolation, immutability, and rollback-contract defects require a
    code/configuration rollback.  A legacy admission leak or a sampled hard
    availability breach activates the authored-only kill switch.  Insufficient
    traffic and ordinary SLO misses hold the cohort without expansion.
    """

    slo = slo or CanarySLO()
    rollback_reasons: list[str] = []
    if window.owner_isolation_violations:
        rollback_reasons.append("owner_isolation_violation")
    if window.source_or_archive_digest_mismatches:
        rollback_reasons.append("digest_mismatch")
    if window.original_immutability_violations:
        rollback_reasons.append("original_immutability_violation")
    if not window.rollback_probe_passed:
        rollback_reasons.append("rollback_probe_failed")
    if rollback_reasons:
        return CanaryDecision(
            "rollback",
            stage,
            previous_canary_stage(stage),
            tuple(rollback_reasons),
        )

    kill_reasons: list[str] = []
    if window.legacy_admission_violations:
        kill_reasons.append("legacy_admission_violation")
    for name, sample in (("read", window.read), ("export", window.export)):
        if sample.attempts < slo.minimum_attempts:
            continue
        ratio = sample.success_ratio
        if ratio is not None and 1 - ratio > slo.hard_failure_ratio:
            kill_reasons.append(f"{name}_hard_failure_ratio_breached")
    if kill_reasons:
        return CanaryDecision("kill", stage, stage, tuple(kill_reasons))

    hold_reasons: list[str] = []
    for name, sample, target_ratio, target_latency in (
        ("read", window.read, slo.read_success_ratio, slo.read_p95_latency_ms),
        (
            "export",
            window.export,
            slo.export_success_ratio,
            slo.export_p95_latency_ms,
        ),
    ):
        if sample.attempts < slo.minimum_attempts:
            hold_reasons.append(f"{name}_sample_insufficient")
            continue
        ratio = sample.success_ratio
        if ratio is None or ratio < target_ratio:
            hold_reasons.append(f"{name}_slo_breached")
        if sample.p95_latency_ms > target_latency:
            hold_reasons.append(f"{name}_latency_slo_breached")
    if hold_reasons:
        return CanaryDecision("hold", stage, stage, tuple(hold_reasons))

    upcoming = next_canary_stage(stage)
    if upcoming is None:
        return CanaryDecision("complete", stage, stage, ("canary_complete",))
    return CanaryDecision("advance", stage, upcoming, ("canary_slo_passed",))


RuntimeVersion = Literal["previous", "candidate"]
CompatibilityOperation = Literal[
    "legacy_open_preview",
    "legacy_export_download",
    "legacy_mutation_blocked",
    "upgrade_fork",
    "rollback_read_export",
]


@dataclass(frozen=True, slots=True, order=True)
class RollingDeployCell:
    caller_version: RuntimeVersion
    runtime_version: RuntimeVersion
    operation: CompatibilityOperation


REQUIRED_ROLLING_DEPLOY_MATRIX = frozenset(
    RollingDeployCell(caller_version, runtime_version, operation)
    for caller_version in ("previous", "candidate")
    for runtime_version in ("previous", "candidate")
    for operation in (
        "legacy_open_preview",
        "legacy_export_download",
        "legacy_mutation_blocked",
        "upgrade_fork",
        "rollback_read_export",
    )
)


@dataclass(frozen=True, slots=True)
class RollingDeployProbe:
    cell: RollingDeployCell
    passed: bool


@dataclass(frozen=True, slots=True)
class RollingDeployDecision:
    ready: bool
    missing: tuple[RollingDeployCell, ...]
    failed: tuple[RollingDeployCell, ...]

    def require_ready(self) -> None:
        if not self.ready:
            raise RuntimeError("authored_only_rolling_deploy_not_ready")


def evaluate_rolling_deploy(
    probes: Iterable[RollingDeployProbe],
) -> RollingDeployDecision:
    """Require every mixed-version compatibility cell to be present and green."""

    results: dict[RollingDeployCell, bool] = {}
    for probe in probes:
        # A duplicate failure cannot be hidden by a later successful retry.
        results[probe.cell] = results.get(probe.cell, True) and probe.passed
    missing = tuple(sorted(REQUIRED_ROLLING_DEPLOY_MATRIX - results.keys()))
    failed = tuple(
        sorted(
            cell
            for cell in REQUIRED_ROLLING_DEPLOY_MATRIX
            if cell in results and not results[cell]
        )
    )
    return RollingDeployDecision(not missing and not failed, missing, failed)


__all__ = [
    "AuthoredOnlyRolloutFlags",
    "AvailabilitySample",
    "CANARY_STAGES",
    "CanaryDecision",
    "CanarySLO",
    "CanaryStage",
    "CanaryWindow",
    "REQUIRED_ROLLING_DEPLOY_MATRIX",
    "RollingDeployCell",
    "RollingDeployDecision",
    "RollingDeployProbe",
    "deterministic_owner_bucket",
    "evaluate_canary",
    "evaluate_rolling_deploy",
    "next_canary_stage",
    "previous_canary_stage",
]
