"""Closed title-capacity accounting and deterministic recovery contracts."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
import secrets
from typing import Any, Mapping, Sequence

_NAMED_WORD = r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*(?![A-Za-z0-9])"
_LEXICAL_WORD = r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*(?![A-Za-z0-9])"
_PROTECTED_PATTERNS = {
    "number_unit": r"[+-]?\d[\d,]*(?:\.\d+)?(?:\s*(?:%|퍼센트|억원|조원|만원|원|배|명|건|개|년|월|일|시간|분|초|bp|USD|KRW))?",
    "english_qualifier": r"(?i:\b(?:not|no|never|without|only|all|some|may|must|aim|target|estimated|expected|approximately|at least|at most|up to)\b)",
    "korean_qualifier": r"않|없|못|불가|금지|미만|이상|이하|초과|최소|최대|일부|모든|조건|경우|예상|추정|가능|목표",
}
_TOKEN_CLASSES = frozenset({"named_word", *_PROTECTED_PATTERNS})
_TOKEN_SCHEMA = "authored-title-token-diagnostic/v1"
_MAX_TOKEN_DIFFERENCES = 8
TITLE_RECOVERY_CONTRACT_SCHEMA = "authored-title-recovery-contract/v1"
TITLE_RECOVERY_RECEIPT_SCHEMA = "authored-title-recovery-receipt/v1"
TITLE_CAPACITY_UNRESOLVABLE = "authored_title_capacity_unresolvable"

_ENGLISH_ABBREVIATIONS = (
    (r"\binformation\b", "info"),
    (r"\btechnology\b", "tech"),
    (r"\barchitecture\b", "arch"),
    (r"\bimplementation\b", "impl"),
    (r"\bmanagement\b", "mgmt"),
    (r"\bdevelopment\b", "dev"),
    (r"\boperations\b", "ops"),
    (r"\bperformance\b", "perf"),
    (r"\brequirements\b", "reqs"),
    (r"\bprocessing\b", "proc"),
    (r"\boptimization\b", "opt"),
    (r"\bin order to\b", "to"),
    (r"\bwith regard to\b", "on"),
    (r"\band\b", "&"),
)
_KOREAN_ABBREVIATIONS = (
    ("할 수 있는", "가능한"),
    ("할 수 있도록", "가능하게"),
    ("에 대한", "의"),
    ("을 위한", "용"),
    ("를 위한", "용"),
    ("하기 위한", "용"),
    ("위하여", "위해"),
    ("통하여", "통해"),
    ("프로세스", "절차"),
)


class AuthoredTitleCapacityUnresolvable(ValueError):
    """Bounded terminal result; callers must not relax gates or call a provider."""

    code = TITLE_CAPACITY_UNRESOLVABLE

    def __init__(self, receipt: Mapping[str, Any]) -> None:
        super().__init__(self.code)
        self.receipt = dict(receipt)


def _token_key(token: str) -> str:
    return token.casefold().replace(" ", "")


def _counts(pattern: str, text: str) -> Counter[str]:
    return Counter(_token_key(token) for token in re.findall(pattern, text))


def _protected_title_occurrences(text: str, names: set[str] | None = None) -> dict[str, list[str]]:
    """Shared literal occurrences; normalization affects counts, not prompt spelling."""

    names = set(_counts(_NAMED_WORD, text)) if names is None else names
    return {
        "named_word": [word for word in re.findall(_LEXICAL_WORD, text) if _token_key(word) in names],
        **{token_class: re.findall(pattern, text) for token_class, pattern in _PROTECTED_PATTERNS.items()},
    }


def protected_title_token_differences(before: str, after: str) -> list[tuple[str, str, int, int]]:
    """Internal lexical differences; raw tokens must never enter error metadata."""

    # Capitalization selects protected words, but never decides whether an
    # occurrence exists. A case-only change is not a deletion or a new entity.
    names = set(_counts(_NAMED_WORD, before)) | set(_counts(_NAMED_WORD, after))
    before_tokens, after_tokens = _protected_title_occurrences(before, names), _protected_title_occurrences(after, names)
    differences = []
    for token_class in before_tokens:
        original = Counter(map(_token_key, before_tokens[token_class]))
        candidate = Counter(map(_token_key, after_tokens[token_class]))
        differences.extend(
            (token_class, token, original[token], candidate[token])
            for token in sorted(original.keys() | candidate.keys())
            if original[token] != candidate[token]
        )
    return differences


def _protected_ledger(qid: str, text: str) -> dict[str, Any]:
    occurrences = _protected_title_occurrences(text)
    return {
        "qid": qid,
        "currentChars": len(text),
        "protectedOccurrences": [
            {"tokenClass": token_class, "token": token, "count": count}
            for token_class, tokens in occurrences.items()
            for token, count in sorted(Counter(tokens).items(), key=lambda item: (_token_key(item[0]), item[0]))
        ],
    }


def _validate_budget_inputs(
    measured: Mapping[str, int], limits: Mapping[str, int], eligible: Sequence[tuple[str, str]],
) -> None:
    for source, keys in (
        (measured, ("owner_title", "owner_visible_text")),
        (limits, ("owner_title", "owner_visible_text")),
    ):
        if any(type(source.get(key)) is not int or source[key] < 0 for key in keys):
            raise ValueError("title recovery accounting requires non-negative integer owner metrics")
    qids = [qid for qid, _text in eligible]
    if any(not isinstance(qid, str) or not qid or not isinstance(text, str) for qid, text in eligible):
        raise ValueError("title recovery owners require non-empty QIDs and string text")
    if len(qids) != len(set(qids)):
        raise ValueError("title recovery owner QIDs must be unique")


def title_recovery_budget(
    measured: Mapping[str, int], limits: Mapping[str, int],
    eligible: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    _validate_budget_inputs(measured, limits, eligible)
    counts = [(qid, len(text)) for qid, text in eligible]
    eligible_chars = sum(count for _qid, count in counts)
    fixed_chars = measured["owner_title"] - eligible_chars
    non_title = measured["owner_visible_text"] - measured["owner_title"]
    if fixed_chars < 0 or non_title < 0:
        raise ValueError("title recovery measurements are internally inconsistent")
    joint = max(0, min(limits["owner_title"], limits["owner_visible_text"] - non_title))
    available = joint - fixed_chars
    reduction = max(0, eligible_chars - available)
    targets = dict(counts)
    remaining = reduction
    # Guidance only: preserve short topic/brand labels where possible. The
    # admission authority remains the joint caps, not these per-QID targets.
    feasible = available >= len(counts) and all(count > 0 for _qid, count in counts)
    if feasible:
        for qid, count in sorted(counts, key=lambda value: -value[1]):
            cut = min(remaining, count - 1)
            targets[qid] -= cut
            remaining -= cut
        feasible = remaining == 0
    ledger = [_protected_ledger(qid, text) for qid, text in eligible]
    return {
        "jointTitleBudget": joint, "fixedTitleChars": fixed_chars,
        "nonTitleVisibleChars": non_title, "availableEligibleChars": available,
        "eligibleChars": eligible_chars,
        "remainingTitleBudget": available - eligible_chars,
        "requiredReduction": reduction, "feasible": feasible,
        "gateRelaxationAllowed": False, "additionalProbabilisticCallsAllowed": 0,
        "qidLedger": ledger,
        "owners": [
            {
                "qid": qid, "currentChars": count,
                "capacityChars": targets[qid] if feasible else None,
                "suggestedTargetChars": targets[qid] if feasible else None,
            }
            for qid, count in counts
        ],
    }


def build_title_recovery_contract(
    measured: Mapping[str, int], limits: Mapping[str, int],
    eligible: Sequence[tuple[str, str]], *, layout_id: str,
) -> dict[str, Any]:
    """Freeze title accounting before the first provider call.

    The returned author-facing contract carries exact residual budget and a
    per-QID protected-token ledger. It never authorizes a retry or a softer gate.
    """

    if not isinstance(layout_id, str) or not layout_id.strip():
        raise ValueError("title recovery contract requires a layout ID")
    plan = title_recovery_budget(measured, limits, eligible)
    return {
        "schemaVersion": TITLE_RECOVERY_CONTRACT_SCHEMA,
        "layoutId": layout_id,
        **plan,
    }


def title_budget_prompt(plan: Mapping[str, Any], eligible: Sequence[tuple[str, str]]) -> str:
    def occurrence_list(tokens: list[str]) -> str:
        counts = Counter(map(_token_key, tokens))
        labels = {_token_key(token): token for token in reversed(tokens)}
        return ", ".join(f"{labels[key]} x{count}" for key, count in sorted(counts.items()))

    owners = "; ".join(
        f"{owner['qid']}: currentChars={owner['currentChars']}, suggestedTargetChars={owner['suggestedTargetChars']}"
        for owner in plan["owners"]
    )
    protected = "; ".join(
        f"{qid}: " + "; ".join(
            f"{token_class} [{occurrence_list(tokens)}]"
            for token_class, tokens in _protected_title_occurrences(text).items()
        )
        for qid, text in eligible
    )
    return (
        f"Exact frozen accounting: jointTitleBudget={plan['jointTitleBudget']}; "
        f"fixedTitleChars={plan['fixedTitleChars']}; nonTitleVisibleChars={plan['nonTitleVisibleChars']}; "
        f"availableEligibleChars={plan['availableEligibleChars']}; "
        f"remainingTitleBudget={plan['remainingTitleBudget']}; requiredReduction={plan['requiredReduction']}. "
        f"Eligible owners [{owners}]. These nonempty per-owner targets are guidance, not new limits; "
        "you may redistribute while respecting the combined budget and every protected meaning. "
        f"Exact protected occurrences by owner and class [{protected}]. "
        "Keep every listed occurrence within its same QID; do not delete, replace, or add protected tokens. "
        "Gate relaxation is forbidden and no additional probabilistic title call is allowed. "
        + ("No nonempty allocation is possible: retain the text and fail closed. " if not plan["feasible"] else "")
    )


def deterministic_title_abbreviation(text: str, capacity_chars: int) -> str | None:
    """Shorten only a closed Korean/English phrase table, never protected tokens."""

    if type(capacity_chars) is not int or capacity_chars < 1:
        return None
    if len(text) <= capacity_chars:
        return text
    candidate = text
    for pattern, replacement in _ENGLISH_ABBREVIATIONS:
        shortened = re.sub(pattern, replacement, candidate, flags=re.IGNORECASE)
        if len(shortened) < len(candidate) and not protected_title_token_differences(text, shortened):
            candidate = shortened
            if len(candidate) <= capacity_chars:
                return candidate
    for phrase, replacement in _KOREAN_ABBREVIATIONS:
        shortened = candidate.replace(phrase, replacement)
        if len(shortened) < len(candidate) and not protected_title_token_differences(text, shortened):
            candidate = shortened
            if len(candidate) <= capacity_chars:
                return candidate
    return candidate if len(candidate) <= capacity_chars else None


def _layout_value(layout: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in layout:
            return layout[key]
    return None


def _layout_budget(layout: Mapping[str, Any], non_title: int) -> tuple[str, int] | None:
    layout_id = _layout_value(layout, "layoutId", "layout_id", "id")
    title = _layout_value(layout, "titleMaxChars", "title_max_chars", "owner_title")
    visible = _layout_value(layout, "maxVisibleTextChars", "max_visible_text_chars", "owner_visible_text")
    if (
        not isinstance(layout_id, str) or not layout_id
        or type(title) is not int or title < 0
        or type(visible) is not int or visible < 0
    ):
        return None
    return layout_id, max(0, min(title, visible - non_title))


def select_larger_compatible_layout(
    contract: Mapping[str, Any], compatible_layouts: Sequence[Mapping[str, Any]],
) -> tuple[str, int]:
    """Choose a deterministic larger compatible layout before lexical recovery."""

    current_id = contract["layoutId"]
    current_budget = contract["jointTitleBudget"]
    required = contract["fixedTitleChars"] + contract["eligibleChars"]
    candidates: list[tuple[str, int]] = []
    for layout in compatible_layouts:
        if _layout_value(layout, "compatible", "isCompatible", "is_compatible") is not True:
            continue
        parsed = _layout_budget(layout, contract["nonTitleVisibleChars"])
        if parsed is not None and parsed[0] != current_id and parsed[1] > current_budget:
            candidates.append(parsed)
    sufficient = sorted((item for item in candidates if item[1] >= required), key=lambda item: (item[1], item[0]))
    if sufficient:
        return sufficient[0]
    return max(candidates, key=lambda item: (item[1], item[0])) if candidates else (current_id, current_budget)


def _json_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _title_recovery_receipt(
    contract: Mapping[str, Any], *, outcome: str, selected_layout_id: str,
    joint_after: int, titles_before: Mapping[str, str], titles_after: Mapping[str, str],
    abbreviated: bool,
) -> dict[str, Any]:
    ledger = contract["qidLedger"]
    protected = [
        {"qid": item["qid"], "protectedOccurrences": item["protectedOccurrences"]}
        for item in ledger
    ]
    terminal = TITLE_CAPACITY_UNRESOLVABLE if outcome == "terminal" else None
    after_chars = sum(len(value) for value in titles_after.values())
    return {
        "schemaVersion": TITLE_RECOVERY_RECEIPT_SCHEMA,
        "outcome": outcome,
        "terminalCode": terminal,
        "originalLayoutId": contract["layoutId"],
        "selectedLayoutId": selected_layout_id,
        "jointTitleBudgetBefore": contract["jointTitleBudget"],
        "jointTitleBudgetAfter": joint_after,
        "fixedTitleChars": contract["fixedTitleChars"],
        "nonTitleVisibleChars": contract["nonTitleVisibleChars"],
        "eligibleCharsBefore": sum(len(value) for value in titles_before.values()),
        "eligibleCharsAfter": after_chars,
        "remainingTitleBudgetAfter": joint_after - contract["fixedTitleChars"] - after_chars,
        "qidCount": len(titles_before),
        "qidLedgerDigest": _json_digest(ledger),
        "protectedLedgerDigest": _json_digest(protected),
        "layoutSubstituted": selected_layout_id != contract["layoutId"],
        "deterministicAbbreviationApplied": abbreviated,
        "gateRelaxed": False,
        "probabilisticCallCount": 0,
    }


def resolve_title_capacity_recovery(
    contract: Mapping[str, Any], titles: Mapping[str, str], *,
    compatible_layouts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Resolve a failed title envelope without another probabilistic call.

    Recovery order is fixed: substitute one larger compatible layout, then use
    only the closed lexical abbreviation table. Failure is a bounded terminal.
    """

    if contract.get("schemaVersion") != TITLE_RECOVERY_CONTRACT_SCHEMA:
        raise ValueError("authored title recovery contract schema invalid")
    ledger_qids = [item.get("qid") for item in contract.get("qidLedger", ()) if isinstance(item, Mapping)]
    if ledger_qids != list(titles) or any(not isinstance(value, str) or not value for value in titles.values()):
        raise ValueError("authored title recovery ledger does not match title owners")
    for item in contract["qidLedger"]:
        qid = item["qid"]
        original_occurrences = item["protectedOccurrences"]
        if original_occurrences != _protected_ledger(qid, titles[qid])["protectedOccurrences"]:
            raise ValueError("authored title recovery protected ledger mismatch")

    selected_layout_id, joint_after = select_larger_compatible_layout(contract, compatible_layouts)
    available = joint_after - contract["fixedTitleChars"]
    before = dict(titles)
    if sum(map(len, before.values())) <= available:
        outcome = "layout_substitution" if selected_layout_id != contract["layoutId"] else "unchanged"
        receipt = _title_recovery_receipt(
            contract, outcome=outcome, selected_layout_id=selected_layout_id,
            joint_after=joint_after, titles_before=before, titles_after=before, abbreviated=False,
        )
        return {"status": "resolved", "titles": before, "layoutId": selected_layout_id, "receipt": receipt}

    measured = {
        "owner_title": contract["fixedTitleChars"] + sum(map(len, before.values())),
        "owner_visible_text": contract["nonTitleVisibleChars"] + contract["fixedTitleChars"] + sum(map(len, before.values())),
    }
    plan = title_recovery_budget(measured, {
        "owner_title": joint_after,
        "owner_visible_text": joint_after + contract["nonTitleVisibleChars"],
    }, tuple(before.items()))
    recovered: dict[str, str] = {}
    if plan["feasible"]:
        capacities = {item["qid"]: item["capacityChars"] for item in plan["owners"]}
        for qid, text in before.items():
            shortened = deterministic_title_abbreviation(text, capacities[qid])
            if shortened is None or protected_title_token_differences(text, shortened):
                recovered = {}
                break
            recovered[qid] = shortened
    if recovered and sum(map(len, recovered.values())) <= available:
        receipt = _title_recovery_receipt(
            contract, outcome="deterministic_abbreviation", selected_layout_id=selected_layout_id,
            joint_after=joint_after, titles_before=before, titles_after=recovered, abbreviated=True,
        )
        return {"status": "resolved", "titles": recovered, "layoutId": selected_layout_id, "receipt": receipt}

    receipt = _title_recovery_receipt(
        contract, outcome="terminal", selected_layout_id=selected_layout_id,
        joint_after=joint_after, titles_before=before, titles_after=before, abbreviated=False,
    )
    return {
        "status": "terminal", "code": TITLE_CAPACITY_UNRESOLVABLE,
        "titles": before, "layoutId": selected_layout_id, "receipt": receipt,
    }


def require_title_capacity_resolution(result: Mapping[str, Any]) -> Mapping[str, Any]:
    if result.get("status") == "terminal":
        raise AuthoredTitleCapacityUnresolvable(result["receipt"])
    return result


def safe_title_recovery_receipt(value: object) -> dict[str, Any] | None:
    """Validate the exact content-free receipt schema before persistence."""

    keys = {
        "schemaVersion", "outcome", "terminalCode", "originalLayoutId", "selectedLayoutId",
        "jointTitleBudgetBefore", "jointTitleBudgetAfter", "fixedTitleChars", "nonTitleVisibleChars",
        "eligibleCharsBefore", "eligibleCharsAfter", "remainingTitleBudgetAfter", "qidCount",
        "qidLedgerDigest", "protectedLedgerDigest", "layoutSubstituted",
        "deterministicAbbreviationApplied", "gateRelaxed", "probabilisticCallCount",
    }
    if not isinstance(value, dict) or set(value) != keys or value.get("schemaVersion") != TITLE_RECOVERY_RECEIPT_SCHEMA:
        return None
    outcome = value.get("outcome")
    if outcome not in {"unchanged", "layout_substitution", "deterministic_abbreviation", "terminal"}:
        return None
    if value.get("terminalCode") != (TITLE_CAPACITY_UNRESOLVABLE if outcome == "terminal" else None):
        return None
    if any(not isinstance(value.get(key), str) or not value[key] for key in ("originalLayoutId", "selectedLayoutId")):
        return None
    non_negative = (
        "jointTitleBudgetBefore", "jointTitleBudgetAfter", "fixedTitleChars", "nonTitleVisibleChars",
        "eligibleCharsBefore", "eligibleCharsAfter", "qidCount", "probabilisticCallCount",
    )
    if any(type(value.get(key)) is not int or value[key] < 0 for key in non_negative):
        return None
    if type(value.get("remainingTitleBudgetAfter")) is not int:
        return None
    if any(not isinstance(value.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None for key in ("qidLedgerDigest", "protectedLedgerDigest")):
        return None
    if any(type(value.get(key)) is not bool for key in ("layoutSubstituted", "deterministicAbbreviationApplied", "gateRelaxed")):
        return None
    if value["gateRelaxed"] or value["probabilisticCallCount"] != 0:
        return None
    if value["layoutSubstituted"] != (value["originalLayoutId"] != value["selectedLayoutId"]):
        return None
    if value["remainingTitleBudgetAfter"] != value["jointTitleBudgetAfter"] - value["fixedTitleChars"] - value["eligibleCharsAfter"]:
        return None
    return dict(value)


def title_token_diagnostic(
    before_html: str, after_html: str,
    before: Mapping[str, str], after: Mapping[str, str],
) -> dict[str, Any]:
    # A non-persisted salt makes low-entropy words/numbers non-dictionaryable;
    # token digests identify differences only within this diagnostic.
    salt = secrets.token_bytes(32)
    differences = []
    total = 0
    for qid, text in before.items():
        for token_class, token, old_count, new_count in protected_title_token_differences(text, after.get(qid, "")):
            total += 1
            if len(differences) < _MAX_TOKEN_DIFFERENCES:
                differences.append({
                    "qidDigest": sha256(qid.encode()).hexdigest(),
                    "tokenClass": token_class,
                    "tokenDigest": sha256(salt + token.encode()).hexdigest(),
                    "beforeCount": old_count, "afterCount": new_count,
                })
    return {
        "schemaVersion": _TOKEN_SCHEMA,
        "beforeHtmlDigest": sha256(before_html.encode()).hexdigest(),
        "afterHtmlDigest": sha256(after_html.encode()).hexdigest(),
        "differenceCount": total, "truncated": total > _MAX_TOKEN_DIFFERENCES,
        "differences": differences,
    }


def safe_title_token_diagnostic(value: object) -> dict[str, Any] | None:
    def count(item: object) -> bool:
        return type(item) is int and 0 <= item <= 2**31 - 1

    def digest(item: object) -> bool:
        return isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) is not None

    if not isinstance(value, dict) or set(value) != {
        "schemaVersion", "beforeHtmlDigest", "afterHtmlDigest", "differenceCount", "truncated", "differences",
    } or value["schemaVersion"] != _TOKEN_SCHEMA:
        return None
    if not digest(value["beforeHtmlDigest"]) or not digest(value["afterHtmlDigest"]) or not count(value["differenceCount"]):
        return None
    if type(value["truncated"]) is not bool or value["truncated"] != (value["differenceCount"] > _MAX_TOKEN_DIFFERENCES):
        return None
    differences = value["differences"]
    if not isinstance(differences, list) or len(differences) != min(value["differenceCount"], _MAX_TOKEN_DIFFERENCES):
        return None
    for item in differences:
        if not isinstance(item, dict) or set(item) != {"qidDigest", "tokenClass", "tokenDigest", "beforeCount", "afterCount"}:
            return None
        if (
            not digest(item["qidDigest"]) or not digest(item["tokenDigest"])
            or not isinstance(item["tokenClass"], str) or item["tokenClass"] not in _TOKEN_CLASSES
            or not count(item["beforeCount"]) or not count(item["afterCount"])
            or item["beforeCount"] == item["afterCount"]
        ):
            return None
    return {**value, "differences": [dict(item) for item in differences]}
