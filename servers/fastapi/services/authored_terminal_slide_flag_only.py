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


def source_visibility_flag_entries(
    *, outline: Any, block_ids: Sequence[str], flagged_indices: Sequence[int]
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    """Bind visibility-only failures to slides already accepted as flagged."""

    dump = getattr(outline, "model_dump", None)
    value = dump(mode="json", by_alias=True) if callable(dump) else outline
    slides = value.get("slides", ()) if isinstance(value, Mapping) else ()
    allowed = {int(index) for index in flagged_indices}
    remaining = set(block_ids)
    entries: list[dict[str, object]] = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, Mapping) or index not in allowed:
            continue
        reading_order = slide.get("readingOrder", slide.get("reading_order", ()))
        matched = [block_id for block_id in block_ids if block_id in reading_order]
        if not matched:
            continue
        remaining.difference_update(matched)
        entry = flagged_entry(
            index=index, reason="source_visibility", attempted=False,
            outcome="verbatim_flag_only",
        )
        entry["sourceVisibilityBlockIds"] = matched
        entries.append(entry)
    return tuple(entries), tuple(block_id for block_id in block_ids if block_id in remaining)


__all__ = [
    "flagged_entry", "source_visibility_flag_entries", "terminal_reason",
    "verbatim_flagged_slides",
]
