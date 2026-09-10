"""Receipt-only terminal handling for verbatim source-reflow slides."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def flagged_entry(
    *,
    index: int,
    reason: str,
    attempted: bool,
    outcome: str,
    repeated: bool | None = None,
    remake_count: int = 0,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "index": int(index),
        "reason": str(reason)[:160],
        "remakeAttempted": bool(attempted),
        "remakeOutcome": str(outcome)[:64],
    }
    if remake_count > 1:
        entry["remakeCount"] = int(remake_count)
    if repeated is not None:
        entry["repeated"] = bool(repeated)
    return entry


def terminal_reason(defects: Sequence[Any]) -> str:
    categories: list[str] = []
    for issue in defects:
        category = str(getattr(issue, "category", "") or "").strip()
        if category and category not in categories:
            categories.append(category)
    return "composition_safety:" + (",".join(categories) or "unknown")


def verbatim_flagged_slides(
    terminal: Sequence[tuple[int, Sequence[Any], Mapping[str, object]]],
) -> tuple[dict[str, object], ...]:
    """Flag terminal verbatim slides without consuming remake budget."""

    return tuple(
        flagged_entry(
            index=index,
            reason=str(declaration.get("reason") or terminal_reason(defects)),
            attempted=False,
            outcome="verbatim_flag_only",
            repeated=(
                bool(declaration["repeated"])
                if isinstance(declaration.get("repeated"), bool)
                else None
            ),
        )
        for index, defects, declaration in terminal
    )


__all__ = ["flagged_entry", "terminal_reason", "verbatim_flagged_slides"]
