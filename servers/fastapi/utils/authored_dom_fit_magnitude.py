"""Fit magnitude recorded beside the composition gate's unchanged verdicts.

M1 of ``docs/authored-fit-magnitude-measurement-design-20260904.md``.  The gate
(``utils/authored_dom_quality.py``) already holds every number this module
returns at the moment it decides: the browser probe carries
``scroll_width/height``, ``client_width/height`` and a viewport-relative
bounding box for every qid-bearing element.  Until now all of that was reduced
to a category, a binary severity and a fixed sentence, so "how far did this
slide miss?" was thrown away.  These helpers record the miss.

Nothing here participates in a verdict.  The gate calls the functions below
*after* it has decided that an issue exists and attaches the result to the
issue's optional ``magnitude`` field; a report's ``fit_summary`` is likewise a
pure projection of issues that were already raised.  Keeping the arithmetic in
its own module also keeps the gate file (an audited file-size exception) from
growing.

The module deliberately takes plain numbers and mappings rather than the gate's
``BoundingBox``: the gate imports this module, so the dependency stays one-way.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from utils.coerce import finite_float
from utils.authored_dom_categories import RATIO_MAGNITUDE_CATEGORIES
from utils.authored_dom_footer_zone import FOOTER_ZONE_CATEGORY
from utils.authored_dom_text_escape import TEXT_ESCAPE_CATEGORY

#: The infographic library's figure-root marker.  ``utils/authored_infographic``
#: emits exactly one ``<section class="ig ...">`` per figure and the archetype
#: checker identifies the root the same way (``checker.py``: ``"ig" in classes``).
FIGURE_ROOT_CLASS = "ig"

#: Element kinds, as section 3.2 of the design names them.
ELEMENT_KINDS = ("text", "figure", "figure_part", "container", "image")

#: Categories that carry a magnitude.  Every other category is unchanged.
#: ``text_escapes_card`` (``utils/authored_dom_text_escape``) and
#: ``text_intrudes_footer_zone`` (``utils/authored_dom_footer_zone``) both
#: report their miss in the same ratio shape as ``overflow`` and are projected
#: the same way, so the A4 text-scale rung can bound a shrink from either.
MAGNITUDE_CATEGORIES = (
    "overflow",
    "off_canvas",
    "clipping",
    TEXT_ESCAPE_CATEGORY,
    FOOTER_ZONE_CATEGORY,
)

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "canvas",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
        "video",
    }
)

# Kept local rather than imported from the gate so the dependency stays one-way.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_RATIO_DIGITS = 4
_PIXEL_DIGITS = 1
_FRACTION_DIGITS = 3


@dataclass(frozen=True)
class ElementKindRecord:
    """Static markup facts that decide one element's kind."""

    tag: str
    is_figure_root: bool = False
    inside_figure: bool = False
    has_block_child: bool = False
    own_text: str = ""


@dataclass
class _Frame:
    tag: str
    qid: str
    is_figure_root: bool
    inside_figure: bool
    has_block_child: bool = False
    text: list[str] = field(default_factory=list)


class _ElementKindParser(HTMLParser):
    """Collect tag, figure ancestry, block children and own text per qid."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_Frame] = []
        self.records: dict[str, ElementKindRecord] = {}

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> _Frame:
        attr_map = {
            str(name).lower(): "" if value is None else str(value)
            for name, value in attrs
        }
        classes = set(attr_map.get("class", "").split())
        parent = self._stack[-1] if self._stack else None
        if parent is not None and tag in _BLOCK_TAGS:
            parent.has_block_child = True
        return _Frame(
            tag=tag,
            qid=attr_map.get("data-qid", "").strip(),
            is_figure_root=FIGURE_ROOT_CLASS in classes,
            inside_figure=bool(
                parent is not None and (parent.is_figure_root or parent.inside_figure)
            ),
        )

    def _close(self, frame: _Frame) -> None:
        if not frame.qid or frame.qid in self.records:
            return
        self.records[frame.qid] = ElementKindRecord(
            tag=frame.tag,
            is_figure_root=frame.is_figure_root,
            inside_figure=frame.inside_figure,
            has_block_child=frame.has_block_child,
            own_text=" ".join(frame.text).strip()[:2000],
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        frame = self._open(tag.lower(), attrs)
        # A void element never receives an end tag; sealing it immediately keeps
        # following text out of a phantom stack frame (the same reason the gate's
        # own text parser refuses to push one).
        if frame.tag in _VOID_TAGS:
            self._close(frame)
            return
        self._stack.append(frame)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._close(self._open(tag.lower(), attrs))

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == name:
                for frame in reversed(self._stack[index:]):
                    self._close(frame)
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._stack and data.strip():
            self._stack[-1].text.append(data.strip())

    def close(self) -> None:
        super().close()
        for frame in reversed(self._stack):
            self._close(frame)
        self._stack.clear()


def build_element_kind_index(enriched_html: str) -> dict[str, ElementKindRecord]:
    """Index one slide's already qid-enriched HTML by qid.

    The caller passes the output of ``ensure_authored_qids`` because the gate
    has it in hand; parsing raw HTML here would index qids that do not exist.
    """

    parser = _ElementKindParser()
    parser.feed(enriched_html or "")
    parser.close()
    return parser.records


def _own_text_is_present(
    item: Mapping[str, Any] | None, record: ElementKindRecord | None
) -> bool:
    if item is not None and "has_owned_text" in item:
        # The v2/v3 Chrome probe distinguishes owned glyphs from descendant
        # paint; where it spoke, it is the authority.
        return bool(item.get("has_owned_text"))
    if record is not None and record.own_text:
        return True
    if item is None:
        return False
    return any(
        str(item.get(key, "") or "").strip()
        for key in ("rendered_text", "text", "text_content")
    )


def element_kind(
    qid: str,
    item: Mapping[str, Any] | None,
    index: Mapping[str, ElementKindRecord],
) -> str:
    """Classify one element per section 3.2 of the design.

    Resolution order is the design's: a figure root and its parts answer first,
    because a smaller font fixes neither; then images; then own-text leaves;
    everything else is a container.  A qid the static map does not know (an
    external probe payload measuring an element the HTML no longer carries)
    degrades to the browser's own tag and text rather than being dropped.
    """

    record = index.get(qid)
    if record is not None:
        if record.is_figure_root:
            return "figure"
        if record.inside_figure:
            return "figure_part"
    tag = (
        record.tag
        if record is not None
        else str((item or {}).get("tag", "") or "").lower()
    )
    if tag == "img":
        return "image"
    if _own_text_is_present(item, record) and not (
        record.has_block_child if record is not None else False
    ):
        return "text"
    return "container"


def _number(value: object, default: float = 0.0) -> float:
    return finite_float(value, default)


def _axis_ratio(scroll: float, client: float) -> float:
    # 1.0 means "this axis fits"; a zero client box carries no ratio at all.
    if client <= 0 or scroll <= client:
        return 1.0
    return round(scroll / client, _RATIO_DIGITS)


def overflow_magnitude(item: Mapping[str, Any], *, kind: str) -> dict[str, float | str]:
    """How far an element's scroll box exceeds its client box, per axis."""

    scroll_x = _number(item.get("scroll_width"))
    scroll_y = _number(item.get("scroll_height"))
    client_x = _number(item.get("client_width"))
    client_y = _number(item.get("client_height"))
    return {
        "ratio_x": _axis_ratio(scroll_x, client_x),
        "ratio_y": _axis_ratio(scroll_y, client_y),
        "excess_px_x": round(max(0.0, scroll_x - client_x), _PIXEL_DIGITS),
        "excess_px_y": round(max(0.0, scroll_y - client_y), _PIXEL_DIGITS),
        "element_kind": kind,
    }


def off_canvas_magnitude(
    box: Sequence[float] | Mapping[str, float],
    *,
    viewport_width: float,
    viewport_height: float,
    kind: str,
) -> dict[str, float | str]:
    """Pixels beyond each canvas edge; 0 on an edge the element stays inside."""

    left, top, right, bottom = _edges(box)
    return {
        "overshoot_px_left": round(max(0.0, -left), _PIXEL_DIGITS),
        "overshoot_px_top": round(max(0.0, -top), _PIXEL_DIGITS),
        "overshoot_px_right": round(max(0.0, right - viewport_width), _PIXEL_DIGITS),
        "overshoot_px_bottom": round(max(0.0, bottom - viewport_height), _PIXEL_DIGITS),
        "element_kind": kind,
    }


def clipping_magnitude(
    box: Sequence[float] | Mapping[str, float],
    clip_box: Sequence[float] | Mapping[str, float] | None,
    *,
    kind: str,
) -> dict[str, float | str]:
    """Share of the element's box lying outside the box that clips it.

    The gate raises ``clipping`` from the browser's own ``clipped`` boolean and
    never names the clipping ancestor, so no recorded ancestor box exists to
    compare against.  The caller therefore resolves the nearest measured
    ancestor whose computed overflow is ``hidden``/``clip`` and passes its box;
    when no such ancestor was measured it passes the viewport, which is the
    outermost clipper every slide has.  ``hidden_fraction`` is 0.0 for a
    zero-area element: an element with no box hides nothing measurable.
    """

    left, top, right, bottom = _edges(box)
    area = max(0.0, right - left) * max(0.0, bottom - top)
    if area <= 0 or clip_box is None:
        return {"hidden_fraction": 0.0, "element_kind": kind}
    clip_left, clip_top, clip_right, clip_bottom = _edges(clip_box)
    visible = max(0.0, min(right, clip_right) - max(left, clip_left)) * max(
        0.0, min(bottom, clip_bottom) - max(top, clip_top)
    )
    return {
        "hidden_fraction": round(max(0.0, 1.0 - visible / area), _FRACTION_DIGITS),
        "element_kind": kind,
    }


def _edges(
    box: Sequence[float] | Mapping[str, float],
) -> tuple[float, float, float, float]:
    if isinstance(box, Mapping):
        left = _number(box.get("x", box.get("left")))
        top = _number(box.get("y", box.get("top")))
        return (
            left,
            top,
            left + _number(box.get("width")),
            top + _number(box.get("height")),
        )
    values = [_number(value) for value in box]
    if len(values) != 4:
        raise ValueError("authored_fit_magnitude_box_invalid")
    return (values[0], values[1], values[0] + values[2], values[1] + values[3])


def build_fit_summary(
    issues: Iterable[Any],
    *,
    content_area: float,
    viewport_area: float,
) -> dict[str, Any]:
    """Project one slide's raised issues into the design's fit summary (3.3)."""

    worst_ratio = 1.0
    worst_off_canvas = 0.0
    kinds: dict[str, int] = {}
    for issue in issues:
        magnitude = getattr(issue, "magnitude", None)
        category = getattr(issue, "category", "")
        if not isinstance(magnitude, Mapping) or category not in MAGNITUDE_CATEGORIES:
            continue
        kind = str(magnitude.get("element_kind", "container"))
        kinds[kind] = kinds.get(kind, 0) + 1
        if category in RATIO_MAGNITUDE_CATEGORIES:
            worst_ratio = max(
                worst_ratio,
                _number(magnitude.get("ratio_x"), 1.0),
                _number(magnitude.get("ratio_y"), 1.0),
            )
        elif category == "off_canvas":
            worst_off_canvas = max(
                worst_off_canvas,
                *(
                    _number(magnitude.get(key))
                    for key in (
                        "overshoot_px_left",
                        "overshoot_px_top",
                        "overshoot_px_right",
                        "overshoot_px_bottom",
                    )
                ),
            )
    return {
        "worst_overflow_ratio": round(worst_ratio, _RATIO_DIGITS),
        "worst_off_canvas_px": round(worst_off_canvas, _PIXEL_DIGITS),
        "overflow_kinds": dict(sorted(kinds.items())),
        "content_bbox_fill": (
            round(content_area / viewport_area, _FRACTION_DIGITS)
            if viewport_area > 0
            else 0.0
        ),
    }


__all__ = [
    "ELEMENT_KINDS",
    "FIGURE_ROOT_CLASS",
    "MAGNITUDE_CATEGORIES",
    "ElementKindRecord",
    "build_element_kind_index",
    "build_fit_summary",
    "clipping_magnitude",
    "element_kind",
    "off_canvas_magnitude",
    "overflow_magnitude",
]
