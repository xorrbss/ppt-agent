"""Select a repair candidate only when every validation gate passes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from services.slidespec_repair import _get, sort_candidates

@dataclass(frozen=True)
class CandidateSelection:
    outcome: Literal["accepted", "rejected", "inconclusive"]
    selected: object | None
    evaluated: tuple[object, ...]
    reason_codes: tuple[str, ...]


def _outcome(result: object) -> str:
    if isinstance(result, str):
        return result
    return str(_get(result, "outcome", "status", default=""))


def _reasons(result: object) -> tuple[str, ...]:
    raw = _get(result, "reasonCodes", "reason_codes", default=())
    return tuple(str(value) for value in raw) if isinstance(raw, Sequence) else ()


def select_candidate(
    candidates: Sequence[object],
    *,
    cheap_gate: Callable[[object], object],
    acceptance_gate: Callable[[object], object],
) -> CandidateSelection:
    """Apply cheap gates, then expensive acceptance in minimum-change order.

    A candidate-specific rejection may advance to the next candidate.  Any
    inconclusive result stops immediately and preserves the original revision.
    """

    survivors: list[object] = []
    evaluated: list[object] = []
    rejection_codes: list[str] = []
    for candidate in candidates:
        result = cheap_gate(candidate)
        outcome = _outcome(result)
        if outcome == "inconclusive":
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), _reasons(result) or ("acceptance.inconclusive",)
            )
        if outcome in {"passed", "accepted"}:
            survivors.append(candidate)
        elif outcome in {"failed", "rejected"}:
            rejection_codes.extend(_reasons(result))
        else:
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), ("acceptance.inconclusive",)
            )

    for candidate in sort_candidates(survivors):
        evaluated.append(candidate)
        result = acceptance_gate(candidate)
        outcome = _outcome(result)
        if outcome == "accepted":
            return CandidateSelection("accepted", candidate, tuple(evaluated), _reasons(result))
        if outcome == "inconclusive":
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), _reasons(result) or ("acceptance.inconclusive",)
            )
        if outcome != "rejected":
            return CandidateSelection(
                "inconclusive", None, tuple(evaluated), ("acceptance.inconclusive",)
            )
        rejection_codes.extend(_reasons(result))
    return CandidateSelection(
        "rejected",
        None,
        tuple(evaluated),
        tuple(dict.fromkeys(rejection_codes)) or ("acceptance.required_gate_failed",),
    )


__all__ = ["CandidateSelection", "select_candidate"]
