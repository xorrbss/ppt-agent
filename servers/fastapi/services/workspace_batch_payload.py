"""Bounded result-payload projection for batch page remake operations.

The runner's generic sanitizer drops sequences, so the per-slide outcome list
and the index lists this card renders would otherwise vanish silently.  Every
value admitted here is re-derived from scratch: no caller-supplied key, string
or nesting survives.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

BATCH_KIND = "slide.regenerate.batch"

BATCH_RESULT_KEYS = frozenset(
    {
        "kind",
        "status",
        "requested_count",
        "published_count",
        "failed_count",
        "published_slide_indices",
        "failed_slide_indices",
        "fixed_slide_indices",
        "slide_results",
        "canvas_refresh_required",
        "rollback_available",
    }
)

_INDEX_LIST_KEYS = frozenset(
    {
        "published_slide_indices",
        "failed_slide_indices",
        "fixed_slide_indices",
    }
)
_SLIDE_RESULT_STATUSES = frozenset({"published", "failed", "skipped"})
_MAX_ENTRIES = 200
_MAX_PREVIEW_TASK_ID_LENGTH = 160
_MAX_REASON_CODE_LENGTH = 128


def _slide_indices(value: Sequence[Any]) -> list[int]:
    return [
        item
        for item in value
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0
    ][:_MAX_ENTRIES]


def _slide_results(value: Sequence[Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in value:
        if len(results) >= _MAX_ENTRIES:
            break
        if not isinstance(item, Mapping):
            continue
        slide_index = item.get("slide_index")
        status = item.get("status")
        if (
            isinstance(slide_index, bool)
            or not isinstance(slide_index, int)
            or slide_index < 0
            or status not in _SLIDE_RESULT_STATUSES
        ):
            continue
        entry: dict[str, Any] = {"slide_index": slide_index, "status": status}
        preview_task_id = item.get("preview_task_id")
        if isinstance(preview_task_id, str):
            entry["preview_task_id"] = preview_task_id[:_MAX_PREVIEW_TASK_ID_LENGTH]
        reason_code = item.get("reason_code")
        if isinstance(reason_code, str):
            entry["reason_code"] = reason_code[:_MAX_REASON_CODE_LENGTH]
        results.append(entry)
    return results


def sanitize_batch_value(key: str, value: object) -> list:
    """Return the bounded list form of one batch result key, or an empty list."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    if key in _INDEX_LIST_KEYS:
        return _slide_indices(value)
    if key == "slide_results":
        return _slide_results(value)
    return []
