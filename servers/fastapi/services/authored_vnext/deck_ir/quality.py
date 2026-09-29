"""Fail-closed Deck IR quality gates and bounded critic recovery.

The module intentionally accepts mapping-like contract objects.  WP1 owns the
strict wire models; this layer owns the quality decision and therefore does not
silently coerce missing evidence into a pass.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from utils.hashing import canonical_json_digest


_MISSING = object()

STATIC_REQUIRED_CHECKS = frozenset(
    {
        "schema",
        "qid",
        "source",
        "asset",
        "font",
        "capacity",
        "semanticOrder",
        "accessibility",
        "domAllowlist",
        "deterministicDigest",
    }
)
BROWSER_REQUIRED_CHECKS = frozenset(
    {
        "viewportContainment",
        "overflow",
        "clipping",
        "overlap",
        "ghosting",
        "fontLoad",
        "imageDecode",
        "minimumFont",
        "contrast",
        "connectorClearance",
        "quiescence",
        "pngObservationBinding",
    }
)
DECK_REQUIRED_CHECKS = frozenset(
    {
        "slideOrder",
        "storyProgression",
        "furnitureConsistency",
        "paletteConsistency",
        "typeScaleConsistency",
        "densityOutliers",
        "repeatedContent",
        "openingCompleteness",
        "closingCompleteness",
    }
)

CRITIC_PATCH_ALLOWLIST = frozenset(
    {
        "replaceLayoutVariant",
        "setDensity",
        "setEmphasis",
        "setComponentVariant",
        "setAssetCropIntent",
        "setReadingOrderVariant",
        "setFocalStrength",
        "setNegativeSpaceIntent",
        "setAsymmetryIntent",
        "setImageTreatment",
        "setTypographyRhythm",
    }
)

_CRITIC_FORBIDDEN_KEYS = frozenset(
    {
        "html",
        "css",
        "javascript",
        "script",
        "selector",
        "x",
        "y",
        "width",
        "height",
        "fontSize",
        "fontFamily",
        "color",
        "title",
        "claim",
        "evidence",
        "sourceBindingId",
        "sourceBindingIds",
        "requirementIds",
        "qid",
        "elementKey",
        "contentPriority",
        "hardRelation",
        "overlapPolicy",
        "textOcclusion",
        "sourceOcclusion",
        "capability",
        "readiness",
        "fallback",
    }
)


def _plain(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python", by_alias=True)
    return value


def _read(value: object, *names: str, default: object = _MISSING) -> object:
    value = _plain(value)
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    if default is _MISSING:
        raise ValueError(f"missing required field: {names[0]}")
    return default


def _canonical_digest(payload: object) -> str:
    """Use the WP1 canonical helper when available, with a deterministic fallback."""

    try:
        from .canonical import canonical_digest as contract_canonical_digest

        digest = contract_canonical_digest(payload)
        if isinstance(digest, str) and digest:
            return digest
    except (ImportError, TypeError, ValueError):
        pass
    return canonical_json_digest(_plain(payload))


@dataclass(frozen=True, slots=True)
class QualityIssue:
    severity: Literal["P0", "P1", "P2"]
    code: str
    stage: Literal["static", "browser", "deck"]
    path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "stage": self.stage,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class StageQualityDecision:
    stage: Literal["static", "browser", "deck"]
    passed: bool
    checks: Mapping[str, bool]
    issues: tuple[QualityIssue, ...]
    receipt_digest: str

    @property
    def p0_count(self) -> int:
        return sum(issue.severity == "P0" for issue in self.issues)

    @property
    def p1_count(self) -> int:
        return sum(issue.severity == "P1" for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "checks": dict(self.checks),
            "issues": [issue.to_dict() for issue in self.issues],
            "p0Count": self.p0_count,
            "p1Count": self.p1_count,
            "receiptDigest": self.receipt_digest,
        }


@dataclass(frozen=True, slots=True)
class DeckQualityGateReceipt:
    schema_version: str
    static: StageQualityDecision
    browser: StageQualityDecision
    deck: StageQualityDecision
    p0_count: int
    p1_count: int
    p2_count: int
    passed: bool
    receipt_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "static": self.static.to_dict(),
            "browser": self.browser.to_dict(),
            "deck": self.deck.to_dict(),
            "p0Count": self.p0_count,
            "p1Count": self.p1_count,
            "p2Count": self.p2_count,
            "passed": self.passed,
            "receiptDigest": self.receipt_digest,
        }


def _quality_issue(
    raw: object,
    *,
    stage: Literal["static", "browser", "deck"],
    index: int,
) -> QualityIssue:
    severity = _read(raw, "severity", default=None)
    code = _read(raw, "code", default=None)
    path = _read(raw, "path", default=f"{stage}.issues[{index}]")
    if severity not in {"P0", "P1", "P2"}:
        raise ValueError(f"{stage} issue severity must be P0, P1, or P2")
    if not isinstance(code, str) or not code:
        raise ValueError(f"{stage} issue code must be non-empty")
    if not isinstance(path, str) or not path:
        raise ValueError(f"{stage} issue path must be non-empty")
    return QualityIssue(severity=severity, code=code, stage=stage, path=path)


def evaluate_stage_gate(
    evidence: object,
    *,
    stage: Literal["static", "browser", "deck"],
    required_checks: Iterable[str],
) -> StageQualityDecision:
    """Evaluate one quality boundary without accepting unobserved checks."""

    raw_checks = _read(evidence, "checks", default=None)
    if not isinstance(raw_checks, Mapping):
        raise ValueError(f"{stage}.checks must be a mapping")
    checks = {str(name): value is True for name, value in raw_checks.items()}
    required = frozenset(required_checks)
    missing = sorted(required.difference(checks))
    issues_raw = _read(evidence, "issues", default=())
    if isinstance(issues_raw, (str, bytes)) or not isinstance(issues_raw, Sequence):
        raise ValueError(f"{stage}.issues must be a sequence")
    issues = [
        _quality_issue(raw, stage=stage, index=index)
        for index, raw in enumerate(issues_raw)
    ]
    for name in missing:
        issues.append(
            QualityIssue(
                severity="P1",
                code=f"deck_ir_{stage}_evidence_missing",
                stage=stage,
                path=f"{stage}.checks.{name}",
            )
        )
        checks[name] = False
    for name in sorted(required):
        if checks[name] is not True and not any(
            issue.path == f"{stage}.checks.{name}" for issue in issues
        ):
            issues.append(
                QualityIssue(
                    severity="P1",
                    code=f"deck_ir_{stage}_gate_failed",
                    stage=stage,
                    path=f"{stage}.checks.{name}",
                )
            )

    p0_or_p1 = any(issue.severity in {"P0", "P1"} for issue in issues)
    required_passed = all(checks[name] for name in required)
    passed = required_passed and not p0_or_p1
    body = {
        "stage": stage,
        "passed": passed,
        "checks": {name: checks[name] for name in sorted(checks)},
        "issues": [issue.to_dict() for issue in issues],
    }
    return StageQualityDecision(
        stage=stage,
        passed=passed,
        checks=MappingProxyType(dict(sorted(checks.items()))),
        issues=tuple(issues),
        receipt_digest=_canonical_digest(body),
    )


def evaluate_deck_quality(
    *,
    static_evidence: object,
    browser_evidence: object,
    deck_evidence: object,
) -> DeckQualityGateReceipt:
    """Require static, browser, and deck evidence with zero P0/P1 defects."""

    static = evaluate_stage_gate(
        static_evidence,
        stage="static",
        required_checks=STATIC_REQUIRED_CHECKS,
    )
    browser = evaluate_stage_gate(
        browser_evidence,
        stage="browser",
        required_checks=BROWSER_REQUIRED_CHECKS,
    )
    deck = evaluate_stage_gate(
        deck_evidence,
        stage="deck",
        required_checks=DECK_REQUIRED_CHECKS,
    )
    issues = static.issues + browser.issues + deck.issues
    p0_count = sum(issue.severity == "P0" for issue in issues)
    p1_count = sum(issue.severity == "P1" for issue in issues)
    p2_count = sum(issue.severity == "P2" for issue in issues)
    passed = static.passed and browser.passed and deck.passed and p0_count == p1_count == 0
    body = {
        "schemaVersion": "deck-ir-quality-gate-receipt/v1",
        "stageReceiptDigests": [
            static.receipt_digest,
            browser.receipt_digest,
            deck.receipt_digest,
        ],
        "p0Count": p0_count,
        "p1Count": p1_count,
        "p2Count": p2_count,
        "passed": passed,
    }
    return DeckQualityGateReceipt(
        schema_version="deck-ir-quality-gate-receipt/v1",
        static=static,
        browser=browser,
        deck=deck,
        p0_count=p0_count,
        p1_count=p1_count,
        p2_count=p2_count,
        passed=passed,
        receipt_digest=_canonical_digest(body),
    )


@dataclass(frozen=True, slots=True)
class CriticPatchDecision:
    valid: bool
    errors: tuple[str, ...]
    patch_digest: str | None


def _find_forbidden_keys(value: object, path: str = "patch") -> list[str]:
    value = _plain(value)
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            if key_text in _CRITIC_FORBIDDEN_KEYS:
                errors.append(nested_path)
            errors.extend(_find_forbidden_keys(nested, nested_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, nested in enumerate(value):
            errors.extend(_find_forbidden_keys(nested, f"{path}[{index}]"))
    return errors


def validate_bounded_critic_patch(
    patch: object,
    *,
    expected_deck_ir_digest: str,
    expected_observation_digest: str,
    blocking_slide_keys: Iterable[str],
) -> CriticPatchDecision:
    """Validate the visual-only patch allowlist and immutable input bindings."""

    errors: list[str] = []
    if _read(patch, "schemaVersion", "schema_version", default=None) != "deck-critic-patch/v1":
        errors.append("deck_critic_patch_schema_invalid")
    if _read(patch, "deckIrDigest", "deck_ir_digest", default=None) != expected_deck_ir_digest:
        errors.append("deck_critic_patch_deck_digest_mismatch")
    if _read(patch, "observationDigest", "observation_digest", default=None) != expected_observation_digest:
        errors.append("deck_critic_patch_observation_digest_mismatch")

    forbidden_paths = _find_forbidden_keys(patch)
    if forbidden_paths:
        errors.append("deck_critic_patch_forbidden_field")
    allowed_slides = frozenset(blocking_slide_keys)
    targets = _read(patch, "targets", default=None)
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence) or not targets:
        errors.append("deck_critic_patch_targets_invalid")
        targets = ()
    seen_slides: set[str] = set()
    for target in targets:
        slide_key = _read(target, "slideKey", "slide_key", default=None)
        if not isinstance(slide_key, str) or slide_key not in allowed_slides or slide_key in seen_slides:
            errors.append("deck_critic_patch_target_scope_invalid")
        elif isinstance(slide_key, str):
            seen_slides.add(slide_key)
        operations = _read(target, "allowedOperations", "allowed_operations", default=None)
        if isinstance(operations, (str, bytes)) or not isinstance(operations, Sequence) or not operations:
            errors.append("deck_critic_patch_operations_invalid")
            continue
        for operation in operations:
            op = _read(operation, "op", default=None)
            if op not in CRITIC_PATCH_ALLOWLIST:
                errors.append("deck_critic_patch_operation_not_allowed")
            plain_operation = _plain(operation)
            has_value = (
                "value" in plain_operation
                if isinstance(plain_operation, Mapping)
                else hasattr(operation, "value")
            )
            if not has_value:
                errors.append("deck_critic_patch_value_missing")

    # WP1 performs strict schema validation too.  Keep this call additive and
    # never use it to turn a local rejection into a pass.
    try:
        from .contracts import DeckCriticPatchV1
        from .validation import parse_contract

        parse_contract(DeckCriticPatchV1, patch)
    except ImportError:
        pass
    except (TypeError, ValueError):
        errors.append("deck_critic_patch_contract_invalid")

    unique_errors = tuple(dict.fromkeys(errors))
    return CriticPatchDecision(
        valid=not unique_errors,
        errors=unique_errors,
        patch_digest=_canonical_digest(patch) if not unique_errors else None,
    )


@dataclass(frozen=True, slots=True)
class SharedRecoveryBudget:
    maximum_actions: int = 1
    consumed_action: Literal["critic", "director-correction"] | None = None


@dataclass(frozen=True, slots=True)
class RecoveryBudgetDecision:
    accepted: bool
    reason: str
    next_budget: SharedRecoveryBudget


def consume_shared_recovery_budget(
    budget: SharedRecoveryBudget,
    action: Literal["critic", "director-correction"],
) -> RecoveryBudgetDecision:
    """Reserve the single attempt-wide recovery action without mutating state."""

    if budget.maximum_actions != 1:
        raise ValueError("Deck IR shared recovery budget must be exactly one")
    if budget.consumed_action is not None:
        return RecoveryBudgetDecision(False, "shared_recovery_budget_exhausted", budget)
    return RecoveryBudgetDecision(
        True,
        "shared_recovery_budget_reserved",
        SharedRecoveryBudget(maximum_actions=1, consumed_action=action),
    )


__all__ = [
    "BROWSER_REQUIRED_CHECKS",
    "CRITIC_PATCH_ALLOWLIST",
    "DECK_REQUIRED_CHECKS",
    "STATIC_REQUIRED_CHECKS",
    "CriticPatchDecision",
    "DeckQualityGateReceipt",
    "QualityIssue",
    "RecoveryBudgetDecision",
    "SharedRecoveryBudget",
    "StageQualityDecision",
    "consume_shared_recovery_budget",
    "evaluate_deck_quality",
    "evaluate_stage_gate",
    "validate_bounded_critic_patch",
]
