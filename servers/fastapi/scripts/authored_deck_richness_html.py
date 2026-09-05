"""Static (no-Chromium) HTML structural metrics for the deck-richness CLI.

Every function here operates on the raw slide HTML string alone. They are the
fallback path used when a browser render is unavailable (``render_error`` was
set) and the source data for a handful of metrics that never need a browser at
all (``uses_library``, ``image_count``, ``inline_image_bytes``,
``css_rule_count``). ``authored_deck_richness_geometry`` prefers the richer,
browser-measured structure (real bounding boxes, real DOM parent/child edges)
when it is available and falls back to the parser in this module otherwise.

Detection rules, spelled out so the numbers are auditable:

* ``has_chart`` / ``chart_evidence``: a slide is scored as containing a real
  chart when ANY of the following holds, and every rule that fires is listed
  in ``chart_evidence`` (so "why did this count" is always answerable):
    - ``svg_chart``: an ``<svg>`` subtree contains at least one of
      ``<path>``/``<rect>``/``<line>`` AND at least two ``<text>`` elements.
      Two text nodes is the threshold because a single label is common on
      purely decorative SVG icons/dividers; axis + value (or two data labels)
      is the minimum signature of an actual plotted chart.
    - ``canvas``: the slide contains a ``<canvas>`` element (charting
      libraries that paint to canvas leave no other DOM trace).
    - ``keyword:<word>``: any element's ``class`` or ``id`` attribute
      contains a whole token matching ``chart|graph|axis|legend`` (word
      boundaries, so "photograph"/"infographic" do not match, but
      "chart-title" or "bar_graph" do).
  A plain ``<div>`` styled to look like a bar (colored block, no matching
  class/id keyword, no svg/canvas) deliberately does NOT count: that is
  exactly the "bar-shaped div is not a chart" case the caller asked to
  exclude.
* ``has_table``: a ``<table>`` element that contains a ``<thead>`` OR at
  least two ``<tr>`` elements (a single-row ``<table>`` used purely for CSS
  layout is not scored as a real data table).
* ``css_rule_count``: every ``{`` inside ``<style>`` blocks (HTML comments
  and CSS comments stripped first) counts as one rule, EXCEPT the opening
  brace of a container at-rule (``@media``, ``@supports``, ``@keyframes``,
  ``@document``, ``@layer`` and vendor-prefixed keyframes) is subtracted,
  because that brace groups nested rules rather than declaring one itself;
  the nested rules' own braces are still counted normally. This is a regex
  heuristic, not a real CSS parser: a ``{`` inside a string literal or
  malformed CSS would miscount.
* ``distinct_block_count`` / ``max_nesting_depth`` (fallback only): computed
  from a start/end-tag stack over the whole document. ``distinct_block_count``
  counts opening tags whose name is in ``BLOCK_TAGS`` (structural/content
  tags; ``html``/``head``/``body``/``script``/``style``/``link``/``meta``/
  ``title``/``template``/``noscript`` are excluded as non-visual).
  ``max_nesting_depth`` is the deepest point reached by a stack of all
  non-void open tags (void tags per ``VOID_TAGS`` never increase depth).
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from utils.authored_infographic.library import LIBRARY_MARKER

BLOCK_TAGS = frozenset(
    {
        "div", "section", "article", "aside", "header", "footer", "nav", "main",
        "figure", "figcaption", "ul", "ol", "li", "table", "thead", "tbody",
        "tfoot", "tr", "td", "th", "p", "span", "h1", "h2", "h3", "h4", "h5",
        "h6", "svg", "canvas", "img",
    }
)
VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)
_SVG_SHAPE_TAGS = frozenset({"path", "rect", "line"})
_KEYWORD_RE = re.compile(r"\b(chart|graph|axis|legend)\b", re.IGNORECASE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_AT_RULE_CONTAINER_RE = re.compile(
    r"@(?:-webkit-|-moz-|-o-|-ms-)?"
    r"(?:media|supports|keyframes|document|layer)\b[^{]*\{",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class HtmlStructuralMetrics:
    html_bytes: int
    uses_library: bool
    image_count: int
    inline_image_bytes: int
    has_chart: bool
    chart_evidence: tuple[str, ...]
    has_table: bool
    css_rule_count: int
    distinct_block_count: int
    max_nesting_depth: int


class _SVGFrame:
    __slots__ = ("shape_count", "text_count")

    def __init__(self) -> None:
        self.shape_count = 0
        self.text_count = 0


class _TableFrame:
    __slots__ = ("has_thead", "tr_count")

    def __init__(self) -> None:
        self.has_thead = False
        self.tr_count = 0


@dataclass
class _ParseState:
    stack: list[str] = field(default_factory=list)
    max_depth: int = 0
    block_count: int = 0
    svg_stack: list[_SVGFrame] = field(default_factory=list)
    table_stack: list[_TableFrame] = field(default_factory=list)
    chart_evidence: list[str] = field(default_factory=list)
    has_table: bool = False
    image_count: int = 0
    inline_image_bytes: int = 0
    in_style: bool = False
    style_chunks: list[str] = field(default_factory=list)


class _StructuralParser(HTMLParser):
    """One pass over the slide HTML collecting every static metric above."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.state = _ParseState()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)

    def _start(
        self, raw_tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool
    ) -> None:
        tag = raw_tag.lower()
        state = self.state
        attr_map = {name.lower(): (value or "") for name, value in attrs}

        if _matches_keyword(attr_map.get("class")) or _matches_keyword(attr_map.get("id")):
            for keyword in _matched_keywords(attr_map.get("class"), attr_map.get("id")):
                state.chart_evidence.append(f"keyword:{keyword}")

        if tag in BLOCK_TAGS:
            state.block_count += 1
        if tag == "canvas":
            state.chart_evidence.append("canvas")
        if tag == "img":
            state.image_count += 1
            src = attr_map.get("src", "")
            decoded = _decode_data_image_bytes(src)
            if decoded is not None:
                state.inline_image_bytes += decoded
        if tag == "style":
            state.in_style = True

        if state.svg_stack and tag in _SVG_SHAPE_TAGS:
            state.svg_stack[-1].shape_count += 1
        if state.svg_stack and tag == "text":
            state.svg_stack[-1].text_count += 1
        if state.table_stack and tag == "thead":
            state.table_stack[-1].has_thead = True
        if state.table_stack and tag == "tr":
            state.table_stack[-1].tr_count += 1

        void = self_closing or tag in VOID_TAGS
        if tag == "svg" and not void:
            state.svg_stack.append(_SVGFrame())
        if tag == "table" and not void:
            state.table_stack.append(_TableFrame())
        if not void:
            state.stack.append(tag)
            state.max_depth = max(state.max_depth, len(state.stack))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        state = self.state
        if tag == "svg" and state.svg_stack:
            frame = state.svg_stack.pop()
            if frame.shape_count >= 1 and frame.text_count >= 2:
                state.chart_evidence.append("svg_chart")
        if tag == "table" and state.table_stack:
            frame = state.table_stack.pop()
            if frame.has_thead or frame.tr_count >= 2:
                state.has_table = True
        if tag == "style":
            state.in_style = False
        for index in range(len(state.stack) - 1, -1, -1):
            if state.stack[index] == tag:
                del state.stack[index]
                break

    def handle_data(self, data: str) -> None:
        if self.state.in_style:
            self.state.style_chunks.append(data)


def _matched_keywords(*values: str | None) -> list[str]:
    matched: list[str] = []
    for value in values:
        if not value:
            continue
        matched.extend(match.group(1).lower() for match in _KEYWORD_RE.finditer(value))
    return matched


def _matches_keyword(value: str | None) -> bool:
    return bool(value) and bool(_KEYWORD_RE.search(value))


def _decode_data_image_bytes(src: str) -> int | None:
    if not src.lower().startswith("data:"):
        return None
    header, _, payload = src.partition(",")
    if ";base64" not in header.lower() or not payload:
        return None
    try:
        return len(base64.b64decode(payload, validate=False))
    except (binascii.Error, ValueError):
        return None


def count_css_rules(style_text: str) -> int:
    """See module docstring's ``css_rule_count`` rule."""

    stripped = _CSS_COMMENT_RE.sub("", style_text)
    total_braces = stripped.count("{")
    container_at_rules = len(_AT_RULE_CONTAINER_RE.findall(stripped))
    return max(0, total_braces - container_at_rules)


def parse_structural_metrics(html: str) -> HtmlStructuralMetrics:
    """Pure, browser-free structural scan of one slide's HTML."""

    parser = _StructuralParser()
    parser.feed(html or "")
    parser.close()
    state = parser.state
    css_rule_count = count_css_rules("".join(state.style_chunks))
    # Dedupe while preserving first-seen order so repeated keyword hits (many
    # elements sharing a class) do not spam the evidence list.
    seen: set[str] = set()
    evidence: list[str] = []
    for item in state.chart_evidence:
        if item not in seen:
            seen.add(item)
            evidence.append(item)
    return HtmlStructuralMetrics(
        html_bytes=len((html or "").encode("utf-8")),
        uses_library=LIBRARY_MARKER in (html or ""),
        image_count=state.image_count,
        inline_image_bytes=state.inline_image_bytes,
        has_chart=bool(evidence),
        chart_evidence=tuple(evidence),
        has_table=state.has_table,
        css_rule_count=css_rule_count,
        distinct_block_count=state.block_count,
        max_nesting_depth=state.max_depth,
    )
