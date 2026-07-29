from __future__ import annotations

from copy import deepcopy
from typing import Any


def resolve_repeat_suggestion_decisions(
    suggestions: list[dict[str, Any]],
    accepted_ids: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return accepted suggestions and a complete applied/unapplied audit list."""
    if len(accepted_ids) != len(set(accepted_ids)):
        raise ValueError("duplicate_repeat_suggestion_id")

    copied = deepcopy(suggestions)
    by_id = {
        suggestion.get("id"): suggestion
        for suggestion in copied
        if isinstance(suggestion, dict) and isinstance(suggestion.get("id"), str)
    }
    if set(accepted_ids) - set(by_id):
        raise ValueError("unknown_repeat_suggestion_id")

    accepted_set = set(accepted_ids)
    decisions = [
        {
            **suggestion,
            "status": (
                "applied"
                if suggestion.get("id") in accepted_set
                else "unapplied"
            ),
        }
        for suggestion in copied
    ]
    accepted = [
        decision for decision in decisions if decision["status"] == "applied"
    ]
    return accepted, decisions
