"""Static check of the figure contract on a model-authored slide document.

The contract is exact because the skeleton the model was handed is exact: one
figure root with the planned archetype and size class, the planned labels in
order, only library classes inside the figure, no styling of its own, no text
inside SVG. A violation is reported as one ``InfographicContractError`` whose
message doubles as the correction hint of the retry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

from models.authored_infographic_plan import AuthoredSlideInfographic

from utils.authored_infographic.library import library_classes
from utils.authored_infographic.skeleton import (
    DEFAULT_NODE_CLASS,
    HUB_ARCHETYPES,
    NODE_CLASS,
    ROOT_CLASS,
    TABLE_ARCHETYPES,
    UNORDERED_ARCHETYPES,
    density_class,
    node_count,
)

CONTRACT_ERROR_PREFIX = "authored_infographic_contract:"

#: Elements inside a figure that may carry an inline ``style`` attribute.
_INLINE_STYLE_CLASSES = frozenset({"ig-co", "ig-pin"})
_INLINE_STYLE_PROPERTIES = frozenset({"left", "top", "width", "height"})
_LIBRARY_SELECTOR = re.compile(r"\.ig(?:-[\w-]+)?\b")
_VOID = frozenset({"img", "br", "hr", "meta", "link", "input", "col"})
#: Content the exporter cannot carry into a DrawingML table cell.
_TABLE_REPLACED_TAGS = frozenset({"img", "svg", "canvas", "video", "picture", "iframe"})
_WS = re.compile(r"\s+")


class InfographicContractError(ValueError):
    """The authored slide does not reproduce the planned figure."""

    def __init__(self, violations: list[str], *, rejected_html: str) -> None:
        self.violations = tuple(violations)
        self.rejected_html = rejected_html
        super().__init__(CONTRACT_ERROR_PREFIX + " " + "; ".join(violations))


@dataclass
class _Node:
    tag: str
    attrs: dict[str, Optional[str]]
    children: list["_Node"] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def own_text(self) -> str:
        return _WS.sub(" ", "".join(self.text)).strip()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self.stack = [self.root]
        self.styles: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        node = _Node(tag.lower(), {k.lower(): v for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag.lower() == "style":
            self._in_style = True
            self.styles.append("")
        if tag.lower() not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag.lower(), {k.lower(): v for k, v in attrs}))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "style":
            self._in_style = False
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        if self._in_style:
            self.styles[-1] += data
            return
        self.stack[-1].text.append(data)


def _parse(html: str) -> _TreeParser:
    parser = _TreeParser()
    parser.feed(html or "")
    parser.close()
    return parser


def _norm(text: str) -> str:
    return _WS.sub("", text or "")


def _label_texts(root: _Node, slide: AuthoredSlideInfographic) -> list[str]:
    """The figure's label slots in document order, per archetype."""
    if slide.archetype == "comparison":
        head = next((n for n in root.walk() if n.tag == "thead"), None)
        cells = [n for n in head.walk() if n.tag == "th"] if head else []
        return [_first_text(n) for n in cells[1:]]
    if slide.archetype == "before_after":
        body = next((n for n in root.walk() if n.tag == "tbody"), None)
        return [_first_text(n) for n in body.walk() if n.tag == "th"] if body else []
    texts = []
    for node in root.walk():
        if "ig-hub-title" in node.classes and slide.archetype in HUB_ARCHETYPES:
            texts.append(node.own_text())
        elif "ig-label" in node.classes:
            texts.append(node.own_text())
    return texts


def _first_text(node: _Node) -> str:
    """A cell's own text before any nested slot such as ``.ig-th-sub``."""
    return node.own_text()


def _check_native_table(root: _Node) -> list[str]:
    """Hold a table archetype to the exporter's native-table contract.

    The exporter rebuilds ``<table data-native="table">`` as one editable
    DrawingML table, and refuses it when the table owns declared children,
    carries replaced content, or is not a rectangular grid. Those are the
    three ways a model can silently turn a figure back into a screenshot.
    """
    violations: list[str] = []
    tables = [n for n in root.walk() if n.tag == "table"]
    if len(tables) != 1:
        return [f"a table figure must contain exactly one <table>, found {len(tables)}"]
    table = tables[0]
    if (table.attrs.get("data-native") or "").strip().lower() != "table":
        violations.append('the <table> must carry data-native="table"')
    inside = [n for n in table.walk() if n is not table]
    if any(n.attrs.get("data-qid") for n in inside):
        violations.append(
            "no data-qid inside the table: only the <table> itself may be a declared "
            "element, its rows and cells carry data-qid-ignore"
        )
    replaced = sorted({n.tag for n in inside if n.tag in _TABLE_REPLACED_TAGS})
    if replaced:
        violations.append(
            "a table cell may only contain text, not " + ", ".join(f"<{t}>" for t in replaced)
        )
    widths = set()
    for row in (n for n in table.walk() if n.tag == "tr"):
        cells = [c for c in row.children if c.tag in ("td", "th")]
        widths.add(len(cells))
        if any(c.attrs.get("colspan") or c.attrs.get("rowspan") for c in cells):
            violations.append("a table cell may not span rows or columns")
            break
    if len(widths) > 1:
        violations.append(
            "every table row must have the same number of cells, found " + str(sorted(widths))
        )
    return violations


def check_infographic_contract(html: str, slide: AuthoredSlideInfographic) -> list[str]:
    """Every violation of the figure contract, empty when the slide conforms."""
    if not slide.has_figure:
        return []
    parser = _parse(html)
    roots = [n for n in parser.root.walk() if "ig" in n.classes and n.tag != "#document"]
    violations: list[str] = []
    for i, css in enumerate(parser.styles):
        if _LIBRARY_SELECTOR.search(css):
            violations.append(
                f"style block {i + 1} targets library classes (.ig-*); the library is injected "
                "by the server and must not be restyled"
            )
    if len(roots) != 1:
        violations.append(
            f'expected exactly one figure root <section class="ig ..."> but found {len(roots)}'
        )
        return violations
    root = roots[0]
    want_root = ROOT_CLASS[slide.archetype]
    if want_root not in root.classes:
        violations.append(f"figure root must carry class {want_root}")
    want_size = density_class(slide)
    if want_size not in root.classes:
        violations.append(f"figure root must carry size class {want_size}")
    allowed = library_classes() | {"ig"}
    unknown = sorted(
        {c for n in root.walk() for c in n.classes if c not in allowed}
    )
    if unknown:
        violations.append("classes outside the library inside the figure: " + ", ".join(unknown[:8]))
    for node in root.walk():
        if node.tag == "style":
            violations.append("a <style> element inside the figure")
        style = node.attrs.get("style")
        if style is not None and node is not root:
            if not (
                node.tag == "img"
                or _INLINE_STYLE_CLASSES.intersection(node.classes)
            ):
                violations.append(f"inline style on <{node.tag} class=\"{' '.join(node.classes)}\">")
            elif node.tag != "img":
                props = {p.split(":", 1)[0].strip().lower() for p in style.split(";") if ":" in p}
                if not props <= _INLINE_STYLE_PROPERTIES:
                    violations.append(
                        "callout inline style may only set left/top/width/height"
                    )
        if node.tag == "svg" and any(n.own_text() for n in node.walk()):
            violations.append("text inside an <svg> in the figure")
    if slide.archetype in TABLE_ARCHETYPES:
        violations.extend(_check_native_table(root))
    else:
        node_class, bg_class = NODE_CLASS.get(slide.archetype, DEFAULT_NODE_CLASS)
        nodes = [n for n in root.walk() if node_class in n.classes]
        if len(nodes) != node_count(slide):
            violations.append(
                f"expected {node_count(slide)} .{node_class} elements, found {len(nodes)}"
            )
        for n in nodes:
            kinds = {c for child in n.walk() for c in child.classes}
            if bg_class not in kinds:
                violations.append(f"a .{node_class} is missing its .{bg_class} sibling")
                break
            if "ig-label" not in kinds:
                violations.append(f"a .{node_class} is missing its .ig-label")
                break
    want = [_norm(node.label) for node in slide.nodes]
    if slide.archetype == "venn":
        want = want[:4]
    got = [_norm(t) for t in _label_texts(root, slide)]
    if slide.archetype in UNORDERED_ARCHETYPES:
        want, got = sorted(want), sorted(got)
    if got != want:
        violations.append(
            "labels must be the planned labels verbatim and in order: "
            + " | ".join(node.label for node in slide.nodes)
        )
    return violations


def enforce_infographic_contract(html: str, slide: AuthoredSlideInfographic | None) -> None:
    """Raise ``InfographicContractError`` when a figure slide breaks its contract."""
    if slide is None or not slide.has_figure:
        return
    violations = check_infographic_contract(html, slide)
    if violations:
        raise InfographicContractError(violations, rejected_html=html)


__all__ = [
    "CONTRACT_ERROR_PREFIX",
    "InfographicContractError",
    "check_infographic_contract",
    "enforce_infographic_contract",
]
