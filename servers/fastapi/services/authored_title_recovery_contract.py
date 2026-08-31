"""Title recovery guidance and lexical guards, without changing author budgets."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
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


def title_recovery_budget(
    measured: Mapping[str, int], limits: Mapping[str, int],
    eligible: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    counts = [(qid, len(text)) for qid, text in eligible]
    eligible_chars = sum(count for _qid, count in counts)
    fixed_chars = measured["owner_title"] - eligible_chars
    non_title = measured["owner_visible_text"] - measured["owner_title"]
    joint = min(limits["owner_title"], limits["owner_visible_text"] - non_title)
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
    return {
        "jointTitleBudget": joint, "fixedTitleChars": fixed_chars,
        "nonTitleVisibleChars": non_title, "availableEligibleChars": available,
        "requiredReduction": reduction, "feasible": feasible,
        "owners": [
            {"qid": qid, "currentChars": count, "suggestedTargetChars": targets[qid] if feasible else None}
            for qid, count in counts
        ],
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
        f"availableEligibleChars={plan['availableEligibleChars']}; requiredReduction={plan['requiredReduction']}. "
        f"Eligible owners [{owners}]. These nonempty per-owner targets are guidance, not new limits; "
        "you may redistribute while respecting the combined budget and every protected meaning. "
        f"Exact protected occurrences by owner and class [{protected}]. "
        "Keep every listed occurrence within its same QID; do not delete, replace, or add protected tokens. "
        + ("No nonempty allocation is possible: retain the text and fail closed. " if not plan["feasible"] else "")
    )


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
