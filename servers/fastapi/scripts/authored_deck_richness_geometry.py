"""Browser-metrics-derived richness metrics: ink fill, dead space, defects.

These metrics need the ``browser_metrics`` payload returned by one Chromium
render (``utils.slide_capture.render_html_to_png_with_metrics``); when a
render failed or returned no telemetry, callers get ``None``/empty results
back and should fall back to ``authored_deck_richness_html`` for structure.

Reused rather than reimplemented, per the task brief:

* ``ink_fill`` is exactly ``DomQualityReport.fit_summary["content_bbox_fill"]``
  from ``utils.authored_dom_quality.inspect_authored_dom`` -- the union area
  of owned-content bounding boxes (clipped to the 1280x720 canvas) divided by
  canvas area.
* ``geometry_defects`` counts ``inspect_authored_dom`` issues in the geometry
  categories the composition gate blocks on, read from
  ``utils.authored_dom_categories`` rather than restated here so a detector
  added later is measured too, broken down by category and by severity.

``bottom_dead_band`` and the browser-metrics ``distinct_block_count`` /
``max_nesting_depth`` are not exposed anywhere upstream, so they are computed
here directly from the normalized element list:

* ``bottom_dead_band`` = ``(720 - lowest_owned_content_bottom) / 720``,
  clipped to ``[0, 1]``. An element counts as "owned content" using the same
  rule ``utils.authored_dom_quality`` uses internally (that helper is a
  leading-underscore private function, so the rule is duplicated here on
  purpose instead of reaching into it): an element with an explicit
  ``has_owned_text`` flag counts only when that flag is true or its tag is
  ``img``/``picture``/``svg``/``video``; an element from an older payload
  that carries no such flag at all counts unconditionally (conservative,
  matching the "legacy payload" branch upstream). A slide with no owned
  content at all reports ``bottom_dead_band = 1.0`` (the whole canvas is
  dead).
* the browser-metrics structure count treats each distinct ``qid`` in
  ``browser_metrics["elements"]`` as one block, and depth as the longest
  ``parent_qid`` chain (cycle-guarded). It is only used when the element list
  is non-empty; an empty/absent list means "no structure available", and the
  caller falls back to the HTML parser instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from utils.authored_dom_categories import (
    BLOCKING_SAFETY_CATEGORIES,
    DOM_QUALITY_CATEGORIES,
)
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    inspect_authored_dom,
    normalize_browser_metrics,
)

_GEOMETRY_CATEGORIES = tuple(
    category
    for category in DOM_QUALITY_CATEGORIES
    if category in BLOCKING_SAFETY_CATEGORIES
)
_OWNED_CONTENT_TAGS = frozenset({"img", "picture", "svg", "video"})


@dataclass(frozen=True, slots=True)
class GeometryMetrics:
    ink_fill: float
    bottom_dead_band: float
    geometry_defects: dict[str, Any]
    telemetry_status: str
    block_count_from_metrics: int | None
    nesting_depth_from_metrics: int | None


def _is_owned_content(item: Mapping[str, Any]) -> bool:
    if "has_owned_text" not in item:
        return True
    if bool(item.get("has_owned_text")):
        return True
    return str(item.get("tag", "")).lower() in _OWNED_CONTENT_TAGS


def _summarize_defects(issues: tuple[DomQualityIssue, ...]) -> dict[str, Any]:
    by_category = {category: 0 for category in _GEOMETRY_CATEGORIES}
    by_severity = {"high": 0, "medium": 0, "low": 0}
    total = 0
    for issue in issues:
        if issue.category not in _GEOMETRY_CATEGORIES:
            continue
        total += 1
        by_category[issue.category] += 1
        by_severity[issue.severity] += 1
    return {"total": total, "by_category": by_category, "by_severity": by_severity}


def compute_bottom_dead_band(
    browser_metrics: Mapping[str, Any] | None, *, canvas_width: float = 1280.0, canvas_height: float = 720.0
) -> float:
    """Fraction of the canvas height below the lowest owned-content box."""

    normalized = normalize_browser_metrics(browser_metrics)
    width = normalized["viewport"]["width"] or canvas_width
    height = normalized["viewport"]["height"] or canvas_height
    lowest_bottom = 0.0
    found = False
    for item in normalized["elements"]:
        if not _is_owned_content(item):
            continue
        bbox = item.get("bbox") or {}
        x = float(bbox.get("x", 0.0) or 0.0)
        y = float(bbox.get("y", 0.0) or 0.0)
        w = float(bbox.get("width", 0.0) or 0.0)
        h = float(bbox.get("height", 0.0) or 0.0)
        top = max(0.0, y)
        bottom = min(height, y + h)
        left = max(0.0, x)
        right = min(width, x + w)
        if right <= left or bottom <= top:
            continue
        found = True
        lowest_bottom = max(lowest_bottom, bottom)
    if not found or height <= 0:
        return 1.0
    return round(max(0.0, min(1.0, (height - lowest_bottom) / height)), 4)


def structure_from_browser_metrics(
    browser_metrics: Mapping[str, Any] | None,
) -> tuple[int | None, int | None]:
    """(distinct_block_count, max_nesting_depth) from the measured element tree.

    Returns ``(None, None)`` when the payload carries no element list, so the
    caller knows to fall back to the HTML parser instead of trusting a bogus
    zero.
    """

    normalized = normalize_browser_metrics(browser_metrics)
    elements = normalized["elements"]
    if not elements:
        return None, None
    qids = {str(item.get("qid")) for item in elements if str(item.get("qid") or "")}
    if not qids:
        return None, None
    parent_by_qid = {
        str(item.get("qid")): str(item.get("parent_qid") or "") for item in elements
    }

    def depth(qid: str) -> int:
        seen: set[str] = {qid}
        current = parent_by_qid.get(qid, "")
        count = 1
        while current and current not in seen:
            seen.add(current)
            count += 1
            current = parent_by_qid.get(current, "")
        return count

    return len(qids), max(depth(qid) for qid in qids)


def compute_geometry(
    html: str, browser_metrics: Mapping[str, Any] | None
) -> GeometryMetrics:
    report = inspect_authored_dom(
        html, browser_metrics, reference_mode=STYLE_REFERENCE
    )
    fit_summary = report.fit_summary or {}
    ink_fill = float(fit_summary.get("content_bbox_fill", 0.0) or 0.0)
    block_count, nesting_depth = structure_from_browser_metrics(browser_metrics)
    return GeometryMetrics(
        ink_fill=ink_fill,
        bottom_dead_band=compute_bottom_dead_band(browser_metrics),
        geometry_defects=_summarize_defects(report.issues),
        telemetry_status=report.telemetry_status,
        block_count_from_metrics=block_count,
        nesting_depth_from_metrics=nesting_depth,
    )
