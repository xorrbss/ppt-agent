"""Evidence-only gate for eventual legacy compatibility data deletion.

This module never removes rows or files.  It turns measured global evidence
into a fail-closed readiness decision that a separately authorized maintenance
workflow may consume after human review.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.compatibility_migration.rollout import AvailabilitySample


@dataclass(frozen=True, slots=True)
class ReleaseRegressionEvidence:
    release_id: str
    completed: bool
    compatibility_regressions: int

    def __post_init__(self) -> None:
        if not self.release_id.strip():
            raise ValueError("authored_only_release_id_required")
        if self.compatibility_regressions < 0:
            raise ValueError("authored_only_release_regression_count_invalid")


@dataclass(frozen=True, slots=True)
class PhysicalDeletionEvidence:
    """Complete aggregate evidence required before physical deletion review."""

    global_scope: bool
    legacy_reference_count: int
    deletion_candidate_count: int
    archived_candidate_count: int
    archive_checksum_verified_count: int
    archive_checksum_failures: int
    active_job_count: int
    unresolved_quarantine_count: int
    releases: tuple[ReleaseRegressionEvidence, ...]
    read: AvailabilitySample
    export: AvailabilitySample

    def __post_init__(self) -> None:
        counters = (
            self.legacy_reference_count,
            self.deletion_candidate_count,
            self.archived_candidate_count,
            self.archive_checksum_verified_count,
            self.archive_checksum_failures,
            self.active_job_count,
            self.unresolved_quarantine_count,
        )
        if any(value < 0 for value in counters):
            raise ValueError("authored_only_cleanup_count_invalid")
        if self.archived_candidate_count > self.deletion_candidate_count:
            raise ValueError("authored_only_archive_count_exceeds_candidates")
        if self.archive_checksum_verified_count > self.archived_candidate_count:
            raise ValueError("authored_only_archive_verified_count_invalid")
        release_ids = [release.release_id for release in self.releases]
        if len(set(release_ids)) != len(release_ids):
            raise ValueError("authored_only_release_evidence_duplicate")

    @property
    def archive_ratio(self) -> float:
        if self.deletion_candidate_count == 0:
            return 1.0
        return self.archive_checksum_verified_count / self.deletion_candidate_count


@dataclass(frozen=True, slots=True)
class PhysicalDeletionReadiness:
    ready: bool
    reason_codes: tuple[str, ...]
    archive_ratio: float
    read_success_ratio: float | None
    export_success_ratio: float | None

    def require_ready(self) -> None:
        if not self.ready:
            joined = ",".join(self.reason_codes)
            raise RuntimeError(f"authored_only_physical_deletion_not_ready:{joined}")


def evaluate_physical_deletion_readiness(
    evidence: PhysicalDeletionEvidence,
    *,
    minimum_availability_ratio: float = 0.999,
    minimum_availability_attempts: int = 1_000,
) -> PhysicalDeletionReadiness:
    """Evaluate the non-destructive, global physical-deletion gate.

    Read and export each need enough observations and at least 99.9% success by
    default.  The final two supplied release records are interpreted in
    chronological order and must both be complete and regression-free.
    """

    if not 0 < minimum_availability_ratio <= 1:
        raise ValueError("authored_only_cleanup_availability_ratio_invalid")
    if minimum_availability_attempts < 1:
        raise ValueError("authored_only_cleanup_availability_sample_invalid")

    reasons: list[str] = []
    if not evidence.global_scope:
        reasons.append("global_evidence_required")
    if evidence.legacy_reference_count:
        reasons.append("legacy_references_remain")
    if evidence.active_job_count:
        reasons.append("active_jobs_not_drained")
    if evidence.unresolved_quarantine_count:
        reasons.append("quarantine_not_resolved")

    if evidence.deletion_candidate_count != evidence.archived_candidate_count:
        reasons.append("archive_coverage_incomplete")
    if (
        evidence.archive_checksum_verified_count
        != evidence.deletion_candidate_count
        or evidence.archive_checksum_failures
    ):
        reasons.append("archive_checksum_verification_incomplete")

    if len(evidence.releases) < 2:
        reasons.append("two_release_regression_window_incomplete")
    else:
        final_two = evidence.releases[-2:]
        if not all(release.completed for release in final_two):
            reasons.append("two_release_regression_window_incomplete")
        if any(release.compatibility_regressions for release in final_two):
            reasons.append("compatibility_regression_detected")

    for name, sample in (("read", evidence.read), ("export", evidence.export)):
        if sample.attempts < minimum_availability_attempts:
            reasons.append(f"{name}_availability_sample_insufficient")
            continue
        ratio = sample.success_ratio
        if ratio is None or ratio < minimum_availability_ratio:
            reasons.append(f"{name}_availability_below_threshold")

    return PhysicalDeletionReadiness(
        ready=not reasons,
        reason_codes=tuple(reasons),
        archive_ratio=evidence.archive_ratio,
        read_success_ratio=evidence.read.success_ratio,
        export_success_ratio=evidence.export.success_ratio,
    )


__all__ = [
    "PhysicalDeletionEvidence",
    "PhysicalDeletionReadiness",
    "ReleaseRegressionEvidence",
    "evaluate_physical_deletion_readiness",
]
