"""Shared primitives for the deterministic Composer classifier.

Geometry, page normalization, formatted-run access and placeholder lookup are
used by every ``classify_*`` module. Nothing here calls a model or touches the
filesystem: every function is a pure mapping from manifest data. Manifests are
stored both snake- and camel-cased, and the run enrichment (``sizePt``,
``bold``, ``dominantSizePt``, ``placeholderType``) may be absent on stored
manifests, so every reader here degrades to ``None`` instead of failing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
import re
from typing import Any

from services.composer.plan import SourceRect

#: Glyphs that join the steps of a flow written as text (``A → B → C``).
ARROW_GLYPHS = "→⇒➜➔⟶↦↓"

_HEX_COLOR = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_WHITESPACE = re.compile(r"\s+")

#: Manifest roles that imply a placeholder type on manifests without
#: ``placeholderType`` (``services.source_content.pptx._semantic_role``).
_ROLE_PLACEHOLDERS = {"title": "title", "subtitle": "subTitle", "source_note": "ftr"}


def field(mapping: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    """Return the first present key of ``names``."""

    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def squash(value: object) -> str:
    """Drop whitespace entirely, matching the readback's character comparison."""

    return _WHITESPACE.sub("", str(value or ""))


def positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def normalize_color(value: Any) -> str | None:
    match = _HEX_COLOR.match(str(value or "").strip())
    return f"#{match.group(1).upper()}" if match else None


@dataclass(frozen=True, slots=True)
class Geometry:
    """A shape rectangle on the source page, in EMU."""

    x: float
    y: float
    cx: float
    cy: float

    @property
    def right(self) -> float:
        return self.x + self.cx

    @property
    def bottom(self) -> float:
        return self.y + self.cy

    @property
    def center_y(self) -> float:
        return self.y + self.cy / 2.0


@dataclass(frozen=True, slots=True)
class PageBox:
    """The source page size the rectangles are normalized against, in EMU.

    ``captured`` is false when the size is only the unit's own extents: the
    lowest block then always touches the bottom, so edge rules must not fire.
    """

    width: float
    height: float
    captured: bool = True


def parse_geometry(payload: Mapping[str, Any]) -> Geometry | None:
    """Read ``payload.geometry``; a connector may be zero-height, a point is nothing."""

    raw = payload.get("geometry")
    if not isinstance(raw, Mapping):
        return None
    try:
        values = [float(raw.get(key, 0) or 0) for key in ("x", "y", "cx", "cy")]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    if values[2] < 0 or values[3] < 0 or (values[2] == 0 and values[3] == 0):
        return None
    return Geometry(*values)


def page_box(deck_context: Mapping[str, Any], geometries: Iterable[Geometry]) -> PageBox | None:
    """The captured page size when the deck context carries it, else the unit's extents."""

    raw = deck_context.get("source_page_emu")
    if is_sequence(raw) and len(raw) == 2:
        width, height = positive_float(raw[0]), positive_float(raw[1])
        if width and height:
            return PageBox(width, height)
    boxes = list(geometries)
    if not boxes:
        return None
    width = max(box.right for box in boxes)
    height = max(box.bottom for box in boxes)
    if width <= 0 or height <= 0:
        return None
    return PageBox(width, height, captured=False)


def normalized_rect(geometry: Geometry | None, page: PageBox | None) -> SourceRect | None:
    """Normalize a rectangle to the page; ``None`` when either side is unknown."""

    if geometry is None or page is None or geometry.cx <= 0 or geometry.cy <= 0:
        return None
    return SourceRect(
        x=min(max(geometry.x / page.width, -0.5), 1.5),
        y=min(max(geometry.y / page.height, -0.5), 1.5),
        width=min(geometry.cx / page.width, 2.0),
        height=min(geometry.cy / page.height, 2.0),
    )


def union_geometry(geometries: Iterable[Geometry | None]) -> Geometry | None:
    boxes = [box for box in geometries if box is not None]
    if not boxes:
        return None
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.right for box in boxes)
    bottom = max(box.bottom for box in boxes)
    return Geometry(left, top, right - left, bottom - top)


def overlap_ratio(a0: float, a1: float, b0: float, b1: float, *, by: str = "min") -> float:
    """Overlap of two ranges as a share of the shorter (``min``) or longer (``max``) one."""

    overlap = min(a1, b1) - max(a0, b0)
    if overlap <= 0:
        return 0.0
    lengths = (a1 - a0, b1 - b0)
    base = min(lengths) if by == "min" else max(lengths)
    return overlap / base if base > 0 else 0.0


@dataclass(frozen=True, slots=True)
class FormattedRun:
    """One ``a:r`` run as the manifest carried it; formatting is optional."""

    text: str
    bold: bool = False
    italic: bool = False
    size_pt: float | None = None
    color: str | None = None
    font_family: str | None = None


def formatted_runs(payload: Mapping[str, Any]) -> tuple[FormattedRun, ...]:
    """Flat runs of a text payload with the parallel ``runStyles`` when captured.

    ``runs[j]`` stays a string; the enrichment adds ``runStyles[j]`` aligned
    1:1 (``sizePt``, ``bold``, ``italic``, ``color``). A stored manifest has
    no styles, and a run may also be an object carrying its own style, which
    the readback tolerates as well.
    """

    raw = payload.get("runs")
    styles = payload.get("runStyles")
    if not is_sequence(raw):
        raw, styles = _runs_from_paragraphs(payload)
    if not is_sequence(styles):
        styles = ()
    result: list[FormattedRun] = []
    for index, item in enumerate(raw):
        style = dict(mapping_or_empty(styles[index])) if index < len(styles) else {}
        if isinstance(item, Mapping):
            style = {**item, **style}
            text = item.get("text")
        else:
            text = item
        result.append(
            FormattedRun(
                text=str(text or ""),
                bold=bool(style.get("bold")),
                italic=bool(style.get("italic")),
                size_pt=positive_float(field(style, "sizePt", "size_pt")),
                color=normalize_color(style.get("color")),
                font_family=field(style, "fontFamily", "font_family"),
            )
        )
    return tuple(result)


def _runs_from_paragraphs(payload: Mapping[str, Any]) -> tuple[list[Any], list[Any]]:
    """Flatten ``paragraphs[].runs`` (and ``runStyles``) with ``"\\n"`` between paragraphs."""

    paragraphs = payload.get("paragraphs")
    if not is_sequence(paragraphs):
        return [payload.get("text") or ""], []
    flat: list[Any] = []
    flat_styles: list[Any] = []
    for index, paragraph in enumerate(paragraphs):
        item = mapping_or_empty(paragraph)
        runs = item.get("runs")
        runs = list(runs) if is_sequence(runs) else [item.get("text") or ""]
        styles = item.get("runStyles")
        styles = list(styles) if is_sequence(styles) else []
        flat.extend(runs)
        flat_styles.extend(styles[position] if position < len(styles) else {} for position in range(len(runs)))
        if index + 1 < len(paragraphs):
            flat.append("\n")
            flat_styles.append(flat_styles[-1] if flat_styles else {})
    return flat, flat_styles


def weighted_size(pairs: Iterable[tuple[float | None, int]]) -> float | None:
    """The size carrying the most characters; ties go to the larger size."""

    weights: dict[float, int] = {}
    for size, chars in pairs:
        if size and chars > 0:
            weights[size] = weights.get(size, 0) + chars
    if not weights:
        return None
    return max(weights.items(), key=lambda item: (item[1], item[0]))[0]


def dominant_size_pt(payload: Mapping[str, Any], runs: Sequence[FormattedRun]) -> float | None:
    declared = positive_float(field(payload, "dominantSizePt", "dominant_size_pt"))
    if declared:
        return declared
    return weighted_size((run.size_pt, len(squash(run.text))) for run in runs)


def placeholder_type(block: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
    """``payload.placeholderType`` when captured, else what the manifest role implies."""

    declared = field(payload, "placeholderType", "placeholder_type")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    role = str(field(block, "semantic_role", "semanticRole", default="") or "")
    return _ROLE_PLACEHOLDERS.get(role)


__all__ = [
    "ARROW_GLYPHS",
    "FormattedRun",
    "Geometry",
    "PageBox",
    "dominant_size_pt",
    "field",
    "formatted_runs",
    "is_sequence",
    "mapping_or_empty",
    "normalize_color",
    "normalized_rect",
    "overlap_ratio",
    "page_box",
    "parse_geometry",
    "placeholder_type",
    "positive_float",
    "squash",
    "union_geometry",
    "weighted_size",
]
