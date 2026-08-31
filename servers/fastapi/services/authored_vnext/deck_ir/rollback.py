"""Version-aware kill switch and abstract atomic admission rollback.

No SQL or concrete database mutation lives here.  The controller derives a
sealed decision; a repository implementation owns the single compare-and-swap
that changes admission for *new* operations only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .migration import RolloutPhase, parse_rollout_phase
from .quality import _canonical_digest, _read


@dataclass(frozen=True, slots=True)
class AdmissionRollbackDecision:
    schema_version: str
    action: Literal[
        "route-new-to-legacy",
        "rollback-new-to-previous-qualified",
        "pause-new-admission",
        "operator-authorized-legacy-recovery",
    ]
    phase: RolloutPhase
    target_strategy: Literal["deck-ir", "legacy-authored", "reject"]
    target_rc_digest: str | None
    target_revision_digests: tuple[tuple[str, str], ...]
    affects_in_flight_operations: bool
    valid: bool
    reason: str
    receipt_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "action": self.action,
            "rolloutPhase": self.phase.label,
            "targetStrategy": self.target_strategy,
            "targetRcDigest": self.target_rc_digest,
            "targetRevisionDigests": dict(self.target_revision_digests),
            "affectsInFlightOperations": False,
            "valid": self.valid,
            "reason": self.reason,
            "receiptDigest": self.receipt_digest,
        }


def _decision(
    *,
    phase: RolloutPhase,
    action: Literal[
        "route-new-to-legacy",
        "rollback-new-to-previous-qualified",
        "pause-new-admission",
        "operator-authorized-legacy-recovery",
    ],
    target_strategy: Literal["deck-ir", "legacy-authored", "reject"],
    target_rc_digest: str | None,
    target_revision_digests: dict[str, str],
    valid: bool,
    reason: str,
) -> AdmissionRollbackDecision:
    body = {
        "schemaVersion": "deck-ir-admission-rollback-decision/v1",
        "action": action,
        "rolloutPhase": phase.label,
        "targetStrategy": target_strategy,
        "targetRcDigest": target_rc_digest,
        "targetRevisionDigests": dict(sorted(target_revision_digests.items())),
        "affectsInFlightOperations": False,
        "valid": valid,
        "reason": reason,
    }
    return AdmissionRollbackDecision(
        schema_version="deck-ir-admission-rollback-decision/v1",
        action=action,
        phase=phase,
        target_strategy=target_strategy,
        target_rc_digest=target_rc_digest,
        target_revision_digests=tuple(sorted(target_revision_digests.items())),
        affects_in_flight_operations=False,
        valid=valid,
        reason=reason,
        receipt_digest=_canonical_digest(body),
    )


def derive_kill_switch_decision(
    *,
    phase: RolloutPhase | int | str,
    previous_qualified_revision: object | None,
    pre_d6_legacy_available: bool,
) -> AdmissionRollbackDecision:
    """Route new work according to the D0-D5 versus post-D6 rollback contract."""

    parsed = parse_rollout_phase(phase)
    if parsed < RolloutPhase.D6_LEGACY_RETIREMENT:
        if pre_d6_legacy_available:
            return _decision(
                phase=parsed,
                action="route-new-to-legacy",
                target_strategy="legacy-authored",
                target_rc_digest=None,
                target_revision_digests={},
                valid=True,
                reason="pre-d6-kill-switch-whole-operation-legacy",
            )
        return _decision(
            phase=parsed,
            action="pause-new-admission",
            target_strategy="reject",
            target_rc_digest=None,
            target_revision_digests={},
            valid=True,
            reason="pre-d6-legacy-unavailable-admission-paused",
        )

    if previous_qualified_revision is None:
        return _decision(
            phase=parsed,
            action="pause-new-admission",
            target_strategy="reject",
            target_rc_digest=None,
            target_revision_digests={},
            valid=False,
            reason="previous-qualified-revision-missing",
        )
    rc_digest = _read(
        previous_qualified_revision,
        "rcDigest",
        "rc_digest",
        default=None,
    )
    qualified = _read(previous_qualified_revision, "qualified", default=None) is True
    immutable = _read(previous_qualified_revision, "immutable", default=None) is True
    revisions = {
        field: value
        for field, names in {
            "grammar": ("grammarDigest", "grammar_digest"),
            "compiler": ("compilerDigest", "compiler_digest"),
            "modelFingerprint": ("modelFingerprint", "model_fingerprint"),
            "candidatePolicy": ("candidatePolicyDigest", "candidate_policy_digest"),
        }.items()
        if isinstance((value := _read(previous_qualified_revision, *names, default=None)), str)
        and value
    }
    valid = (
        isinstance(rc_digest, str)
        and rc_digest.startswith("sha256:")
        and qualified
        and immutable
        and len(revisions) == 4
    )
    if not valid:
        return _decision(
            phase=parsed,
            action="pause-new-admission",
            target_strategy="reject",
            target_rc_digest=rc_digest if isinstance(rc_digest, str) else None,
            target_revision_digests=revisions,
            valid=False,
            reason="previous-qualified-revision-invalid",
        )
    return _decision(
        phase=parsed,
        action="rollback-new-to-previous-qualified",
        target_strategy="deck-ir",
        target_rc_digest=rc_digest,
        target_revision_digests=revisions,
        valid=True,
        reason="post-d6-version-rollback",
    )


def derive_operator_legacy_recovery_decision(
    *,
    phase: RolloutPhase | int | str,
    recovery_package: object,
    operator_authorization_receipt: object | None,
) -> AdmissionRollbackDecision:
    """Admit dormant legacy only with explicit, bounded post-D6 authority."""

    parsed = parse_rollout_phase(phase)
    package_valid = (
        parsed >= RolloutPhase.D6_LEGACY_RETIREMENT
        and _read(recovery_package, "securityPreflightPassed", default=None) is True
        and _read(recovery_package, "compatibilityPreflightPassed", default=None) is True
        and _read(recovery_package, "expired", default=None) is False
        and isinstance(_read(recovery_package, "sourceDigest", default=None), str)
        and isinstance(_read(recovery_package, "runtimeDigest", default=None), str)
    )
    authorized = (
        operator_authorization_receipt is not None
        and _read(operator_authorization_receipt, "authorized", default=None) is True
        and _read(operator_authorization_receipt, "scope", default=None)
        == "whole-operation-legacy-recovery"
    )
    if package_valid and authorized:
        return _decision(
            phase=parsed,
            action="operator-authorized-legacy-recovery",
            target_strategy="legacy-authored",
            target_rc_digest=None,
            target_revision_digests={
                "legacyPackage": _read(recovery_package, "sourceDigest"),
                "legacyRuntime": _read(recovery_package, "runtimeDigest"),
            },
            valid=True,
            reason="time-bounded-dormant-legacy-authorized",
        )
    return _decision(
        phase=parsed,
        action="pause-new-admission",
        target_strategy="reject",
        target_rc_digest=None,
        target_revision_digests={},
        valid=False,
        reason="legacy-recovery-package-or-authorization-invalid",
    )


@dataclass(frozen=True, slots=True)
class AtomicAdmissionResult:
    committed: bool
    revision: int
    active_strategy: Literal["deck-ir", "legacy-authored", "reject"]
    active_rc_digest: str | None
    reason: str


@runtime_checkable
class AdmissionDecisionRepository(Protocol):
    """Repository boundary for one atomic admission compare-and-swap."""

    def compare_and_swap_admission(
        self,
        *,
        expected_revision: int,
        decision: AdmissionRollbackDecision,
    ) -> AtomicAdmissionResult: ...


def apply_atomic_admission_decision(
    repository: AdmissionDecisionRepository,
    *,
    expected_revision: int,
    decision: AdmissionRollbackDecision,
) -> AtomicAdmissionResult:
    if not decision.valid:
        return AtomicAdmissionResult(
            committed=False,
            revision=expected_revision,
            active_strategy="reject",
            active_rc_digest=None,
            reason="invalid-admission-decision-not-applied",
        )
    return repository.compare_and_swap_admission(
        expected_revision=expected_revision,
        decision=decision,
    )


__all__ = [
    "AdmissionDecisionRepository",
    "AdmissionRollbackDecision",
    "AtomicAdmissionResult",
    "apply_atomic_admission_decision",
    "derive_kill_switch_decision",
    "derive_operator_legacy_recovery_decision",
]
