from __future__ import annotations

import pytest

from services.compatibility_migration.rollout import (
    AuthoredOnlyRolloutFlags,
    AvailabilitySample,
    CANARY_STAGES,
    CanarySLO,
    CanaryStage,
    CanaryWindow,
    REQUIRED_ROLLING_DEPLOY_MATRIX,
    RollingDeployProbe,
    deterministic_owner_bucket,
    evaluate_canary,
    evaluate_rolling_deploy,
)


def _healthy_sample() -> AvailabilitySample:
    return AvailabilitySample(attempts=10_000, successes=9_995, p95_latency_ms=50)


def _window(**changes) -> CanaryWindow:
    values = {"read": _healthy_sample(), "export": _healthy_sample()}
    values.update(changes)
    return CanaryWindow(**values)


def test_canary_sequence_is_fixed_and_monotonic():
    assert [stage.value for stage in CANARY_STAGES] == [
        "internal",
        "1",
        "5",
        "25",
        "50",
        "100",
    ]
    assert [stage.basis_points for stage in CANARY_STAGES] == [
        0,
        100,
        500,
        2_500,
        5_000,
        10_000,
    ]


def test_owner_bucket_is_stable_salted_and_does_not_expose_owner():
    owner = "owner-sensitive-123"
    first = deterministic_owner_bucket(owner, salt="release-stable-secret")

    assert first == deterministic_owner_bucket(owner, salt="release-stable-secret")
    assert 0 <= first < 10_000
    assert first != deterministic_owner_bucket(owner, salt="different-secret")
    assert owner not in str(first)
    with pytest.raises(ValueError, match="owner_id_required"):
        deterministic_owner_bucket(" ", salt="salt")
    with pytest.raises(ValueError, match="rollout_salt_required"):
        deterministic_owner_bucket(owner, salt=" ")


def test_flags_are_fail_closed_and_internal_stage_only_admits_allowlist():
    disabled = AuthoredOnlyRolloutFlags(
        CanaryStage.PERCENT_100,
        salt="secret",
        enabled=False,
        internal_owner_ids=frozenset({"employee"}),
    )
    assert disabled.owner_enabled("employee") is False

    internal = AuthoredOnlyRolloutFlags(
        CanaryStage.INTERNAL,
        salt="secret",
        enabled=True,
        internal_owner_ids=frozenset({"employee"}),
    )
    assert internal.owner_enabled("employee") is True
    assert internal.owner_enabled("customer") is False

    manifest = internal.capability_manifest("employee")
    assert manifest["legacy_create"] is False
    assert manifest["legacy_mutate"] is False
    assert manifest["legacy_open"] is True
    assert manifest["legacy_export"] is True
    assert manifest["legacy_upgrade_fork"] is True
    assert "employee" not in str(manifest)


def test_one_percent_stage_uses_strict_owner_bucket_threshold(monkeypatch):
    monkeypatch.setattr(
        "services.compatibility_migration.rollout.deterministic_owner_bucket",
        lambda *_args, **_kwargs: 99,
    )
    flags = AuthoredOnlyRolloutFlags(
        CanaryStage.PERCENT_1,
        salt="secret",
        enabled=True,
    )
    assert flags.owner_enabled("owner") is True

    monkeypatch.setattr(
        "services.compatibility_migration.rollout.deterministic_owner_bucket",
        lambda *_args, **_kwargs: 100,
    )
    assert flags.owner_enabled("owner") is False


def test_healthy_window_advances_one_stage_and_finishes_at_full_rollout():
    decision = evaluate_canary(stage=CanaryStage.PERCENT_5, window=_window())
    assert decision.action == "advance"
    assert decision.recommended_stage is CanaryStage.PERCENT_25
    decision.require_may_expand()

    complete = evaluate_canary(stage=CanaryStage.PERCENT_100, window=_window())
    assert complete.action == "complete"
    assert complete.recommended_stage is CanaryStage.PERCENT_100


def test_insufficient_sample_or_soft_slo_breach_holds_expansion():
    insufficient = evaluate_canary(
        stage=CanaryStage.PERCENT_1,
        window=_window(read=AvailabilitySample(999, 999, 25)),
    )
    assert insufficient.action == "hold"
    assert "read_sample_insufficient" in insufficient.reason_codes

    soft_failure = evaluate_canary(
        stage=CanaryStage.PERCENT_1,
        window=_window(read=AvailabilitySample(10_000, 9_985, 25)),
    )
    assert soft_failure.action == "hold"
    assert "read_slo_breached" in soft_failure.reason_codes
    with pytest.raises(RuntimeError, match="canary_expansion_blocked"):
        soft_failure.require_may_expand()


def test_hard_availability_or_admission_breach_activates_kill():
    hard_failure = evaluate_canary(
        stage=CanaryStage.PERCENT_25,
        window=_window(export=AvailabilitySample(10_000, 9_800, 25)),
    )
    assert hard_failure.action == "kill"
    assert hard_failure.recommended_stage is CanaryStage.PERCENT_25
    assert "export_hard_failure_ratio_breached" in hard_failure.reason_codes

    admission = evaluate_canary(
        stage=CanaryStage.PERCENT_5,
        window=_window(legacy_admission_violations=1),
    )
    assert admission.action == "kill"
    assert admission.reason_codes == ("legacy_admission_violation",)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"owner_isolation_violations": 1}, "owner_isolation_violation"),
        ({"source_or_archive_digest_mismatches": 1}, "digest_mismatch"),
        ({"original_immutability_violations": 1}, "original_immutability_violation"),
        ({"rollback_probe_passed": False}, "rollback_probe_failed"),
    ],
)
def test_integrity_and_rollback_contract_failures_roll_back(changes, reason):
    decision = evaluate_canary(
        stage=CanaryStage.PERCENT_25,
        window=_window(**changes),
    )
    assert decision.action == "rollback"
    assert decision.recommended_stage is CanaryStage.PERCENT_5
    assert reason in decision.reason_codes


def test_custom_slo_applies_latency_gate():
    decision = evaluate_canary(
        stage=CanaryStage.INTERNAL,
        window=_window(read=AvailabilitySample(100, 100, 101)),
        slo=CanarySLO(
            minimum_attempts=100,
            read_p95_latency_ms=100,
            export_p95_latency_ms=100,
        ),
    )
    assert decision.action == "hold"
    assert "read_latency_slo_breached" in decision.reason_codes


def test_rolling_deploy_requires_every_mixed_version_cell_green():
    assert len(REQUIRED_ROLLING_DEPLOY_MATRIX) == 20
    passing = [RollingDeployProbe(cell, True) for cell in REQUIRED_ROLLING_DEPLOY_MATRIX]
    assert evaluate_rolling_deploy(passing).ready is True

    missing = evaluate_rolling_deploy(passing[:-1])
    assert missing.ready is False
    assert len(missing.missing) == 1

    duplicate_failure = evaluate_rolling_deploy(
        passing + [RollingDeployProbe(passing[0].cell, False)]
    )
    assert duplicate_failure.ready is False
    assert duplicate_failure.failed == (passing[0].cell,)
    with pytest.raises(RuntimeError, match="rolling_deploy_not_ready"):
        duplicate_failure.require_ready()
