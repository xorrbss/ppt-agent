"""Deterministic geometry metrics used to pick one rendered candidate.

Everything here is computed from the browser telemetry the render stage already
collects (``utils.slide_capture.render_html_to_png_with_metrics`` ->
``utils.authored_dom_quality.normalize_browser_metrics``). No pixels are read
and no model is called, so the same candidate always scores the same.

Four quantities are measured, in the order the quality brief names them:

``overflow_px``
    How much text a box cannot show (``scroll`` beyond ``client``), plus the
    part of any box pushed outside the viewport.
``text_overlap_area``
    Area shared by two text-owning boxes that are not each other's ancestor.
``whitespace_balance``
    1.0 when the painted content sits centred in the canvas, falling towards
    0.0 as one margin grows against the opposite one.
``ink_ratio``
    Union area of the painted boxes over the canvas area — the density the
    candidate actually achieved, as opposed to the density it was asked for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from utils.authored_dom_quality import BoundingBox, normalize_browser_metrics

#: The ink ratio band a slide is not penalised inside. Below it the page reads
#: as empty, above it as crowded; both ends were taken from the design brief's
#: "one figure, generous margins" rule rather than from a model's opinion.
INK_RATIO_BAND: tuple[float, float] = (0.28, 0.62)


@dataclass(frozen=True)
class CandidateMetrics:
    """One candidate's deterministic geometry, all in CSS pixels or ratios."""

    overflow_px: float
    off_canvas_px: float
    text_overlap_area: float
    whitespace_balance: float
    ink_ratio: float
    measured_elements: int
    viewport_width: float = 1280.0
    viewport_height: float = 720.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bbox(item: Mapping[str, Any]) -> BoundingBox:
    raw = item.get("bbox") or {}
    return BoundingBox(
        float(raw.get("x", 0.0) or 0.0),
        float(raw.get("y", 0.0) or 0.0),
        float(raw.get("width", 0.0) or 0.0),
        float(raw.get("height", 0.0) or 0.0),
    )


def _number(item: Mapping[str, Any], key: str) -> float:
    value = item.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _paints_text(item: Mapping[str, Any]) -> bool:
    if "has_owned_text" in item:
        return bool(item.get("has_owned_text"))
    return bool(str(item.get("text", "") or "").strip())


def _ancestors(elements: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {
        str(item.get("qid", "")): str(item.get("parent_qid", "") or "")
        for item in elements
    }


def _related(parents: Mapping[str, str], first: str, second: str) -> bool:
    for start, target in ((first, second), (second, first)):
        current = parents.get(start, "")
        seen: set[str] = set()
        while current and current not in seen:
            if current == target:
                return True
            seen.add(current)
            current = parents.get(current, "")
    return False


def _intersection_area(first: BoundingBox, second: BoundingBox) -> float:
    width = min(first.right, second.right) - max(first.x, second.x)
    height = min(first.bottom, second.bottom) - max(first.y, second.y)
    return width * height if width > 0 and height > 0 else 0.0


def _union_area(boxes: Sequence[BoundingBox]) -> float:
    """Exact union area of axis-aligned boxes by vertical-slab decomposition."""
    usable = [box for box in boxes if box.width > 0 and box.height > 0]
    if not usable:
        return 0.0
    edges = sorted({edge for box in usable for edge in (box.x, box.right)})
    total = 0.0
    for left, right in zip(edges, edges[1:], strict=False):
        span = right - left
        if span <= 0:
            continue
        intervals = sorted(
            (box.y, box.bottom) for box in usable if box.x < right and box.right > left
        )
        covered = 0.0
        current_start: float | None = None
        current_end = 0.0
        for start, end in intervals:
            if current_start is None:
                current_start, current_end = start, end
                continue
            if start > current_end:
                covered += current_end - current_start
                current_start, current_end = start, end
            else:
                current_end = max(current_end, end)
        if current_start is not None:
            covered += current_end - current_start
        total += span * covered
    return total


def measure_candidate(browser_metrics: Mapping[str, Any] | None) -> CandidateMetrics:
    """Reduce one render's browser telemetry to the four comparable numbers."""
    normalized = normalize_browser_metrics(browser_metrics)
    width = float(normalized["viewport"]["width"]) or 1280.0
    height = float(normalized["viewport"]["height"]) or 720.0
    elements = normalized["elements"]

    overflow = 0.0
    off_canvas = 0.0
    painted: list[BoundingBox] = []
    text_boxes: list[tuple[str, BoundingBox]] = []
    for item in elements:
        box = _bbox(item)
        client_width = _number(item, "client_width")
        client_height = _number(item, "client_height")
        if client_width > 0:
            overflow += max(0.0, _number(item, "scroll_width") - client_width)
        if client_height > 0:
            overflow += max(0.0, _number(item, "scroll_height") - client_height)
        if box.width <= 0 or box.height <= 0:
            continue
        off_canvas += (
            max(0.0, -box.x)
            + max(0.0, -box.y)
            + max(0.0, box.right - width)
            + max(0.0, box.bottom - height)
        )
        if _paints_text(item):
            painted.append(box)
            text_boxes.append((str(item.get("qid", "")), box))

    parents = _ancestors(elements)
    overlap = 0.0
    for index, (first_qid, first) in enumerate(text_boxes):
        for second_qid, second in text_boxes[index + 1 :]:
            if _related(parents, first_qid, second_qid):
                continue
            overlap += _intersection_area(first, second)

    if painted:
        left = min(box.x for box in painted)
        right = width - max(box.right for box in painted)
        top = min(box.y for box in painted)
        bottom = height - max(box.bottom for box in painted)
        imbalance = (abs(left - right) + abs(top - bottom)) / (width + height)
        balance = max(0.0, 1.0 - imbalance)
    else:
        balance = 0.0

    ink = _union_area(painted) / (width * height) if width and height else 0.0
    return CandidateMetrics(
        overflow_px=round(overflow, 3),
        off_canvas_px=round(off_canvas, 3),
        text_overlap_area=round(overlap, 3),
        whitespace_balance=round(balance, 4),
        ink_ratio=round(min(1.0, ink), 4),
        measured_elements=len(elements),
        viewport_width=width,
        viewport_height=height,
    )


def ink_ratio_penalty(ink_ratio: float) -> float:
    """Distance outside the accepted ink band; 0.0 anywhere inside it."""
    low, high = INK_RATIO_BAND
    if ink_ratio < low:
        return low - ink_ratio
    if ink_ratio > high:
        return ink_ratio - high
    return 0.0


__all__ = [
    "INK_RATIO_BAND",
    "CandidateMetrics",
    "ink_ratio_penalty",
    "measure_candidate",
]
