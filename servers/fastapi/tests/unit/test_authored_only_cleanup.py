from __future__ import annotations

import pytest

from services.compatibility_migration.cleanup import (
    PhysicalDeletionEvidence,
    ReleaseRegressionEvidence,
    evaluate_physical_deletion_readiness,
)
from services.compatibility_migration.rollout import AvailabilitySample


def _release(release_id: str, regressions: int = 0):
    return ReleaseRegressionEvidence(release_id, True, regressions)


def _evidence(**changes) -> PhysicalDeletionEvidence:
    values = {
        "global_scope": True,
        "legacy_reference_count": 0,
        "deletion_candidate_count": 10,
        "archived_candidate_count": 10,
        "archive_checksum_verified_count": 10,
        "archive_checksum_failures": 0,
        "active_job_count": 0,
        "unresolved_quarantine_count": 0,
        "releases": (_release("2026.08.1"), _release("2026.08.2")),
        "read": AvailabilitySample(10_000, 9_990, 100),
        "export": AvailabilitySample(10_000, 9_990, 100),
    }
    values.update(changes)
    return PhysicalDeletionEvidence(**values)


def test_complete_global_evidence_is_ready_but_performs_no_deletion():
    evidence = _evidence()
    before = repr(evidence)

    result = evaluate_physical_deletion_readiness(evidence)

    assert result.ready is True
    assert result.reason_codes == ()
    assert result.archive_ratio == 1.0
    assert result.read_success_ratio == pytest.approx(0.999)
    assert result.export_success_ratio == pytest.approx(0.999)
    assert repr(evidence) == before
    result.require_ready()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"global_scope": False}, "global_evidence_required"),
        ({"legacy_reference_count": 1}, "legacy_references_remain"),
        ({"active_job_count": 1}, "active_jobs_not_drained"),
        ({"unresolved_quarantine_count": 1}, "quarantine_not_resolved"),
        (
            {"archived_candidate_count": 9, "archive_checksum_verified_count": 9},
            "archive_coverage_incomplete",
        ),
        (
            {"archive_checksum_verified_count": 9},
            "archive_checksum_verification_incomplete",
        ),
        (
            {"archive_checksum_failures": 1},
            "archive_checksum_verification_incomplete",
        ),
        (
            {"releases": (_release("2026.08.1"),)},
            "two_release_regression_window_incomplete",
        ),
        (
            {"releases": (_release("2026.08.1"), _release("2026.08.2", 1))},
            "compatibility_regression_detected",
        ),
        (
            {"read": AvailabilitySample(999, 999, 100)},
            "read_availability_sample_insufficient",
        ),
        (
            {"export": AvailabilitySample(10_000, 9_989, 100)},
            "export_availability_below_threshold",
        ),
    ],
)
def test_each_physical_deletion_precondition_fails_closed(changes, reason):
    result = evaluate_physical_deletion_readiness(_evidence(**changes))
    assert result.ready is False
    assert reason in result.reason_codes
    with pytest.raises(RuntimeError, match="physical_deletion_not_ready"):
        result.require_ready()


def test_empty_candidate_set_still_requires_all_other_global_evidence():
    result = evaluate_physical_deletion_readiness(
        _evidence(
            deletion_candidate_count=0,
            archived_candidate_count=0,
            archive_checksum_verified_count=0,
        )
    )
    assert result.ready is True
    assert result.archive_ratio == 1.0


def test_evidence_validation_rejects_impossible_counts_and_duplicate_releases():
    with pytest.raises(ValueError, match="archive_count_exceeds_candidates"):
        _evidence(deletion_candidate_count=1, archived_candidate_count=2)
    with pytest.raises(ValueError, match="release_evidence_duplicate"):
        _evidence(releases=(_release("same"), _release("same")))
