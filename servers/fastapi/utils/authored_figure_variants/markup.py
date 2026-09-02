"""Locate the archetype figure inside an authored slide and rewrite its knobs.

Only two things in the figure markup are treated as knobs here, and both are
pure class edits on already-authored HTML:

* the ``n3``..``n6`` density class on the figure root, which the library uses
  to scale type, padding and icon size, and
* the ``is-key`` emphasis class, which at most one ``.ig-node`` carries.

Nothing else is touched, so a variant differs from its baseline by class
attributes alone: identical elements, identical text, identical ``data-qid``
values. That is what makes a variant renderable without another model call and
comparable against the baseline by the ordinary DOM gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The figure root's density classes, tightest last.
DENSITY_CLASSES: tuple[str, ...] = ("n3", "n4", "n5", "n6")
#: The class that marks a document region as the archetype figure root.
ROOT_CLASS = "ig"
#: The per-node element that may carry emphasis.
NODE_CLASS = "ig-node"
#: The emphasis class itself.
EMPHASIS_CLASS = "is-key"

_START_TAG = re.compile(r"<(?P<name>[A-Za-z][\w:-]*)(?P<attrs>(?:\"[^\"]*\"|'[^']*'|[^>\"'])*)>")
_CLASS_ATTR = re.compile(
    r"(?P<head>\bclass\s*=\s*)(?P<quote>[\"'])(?P<value>[^\"']*)(?P=quote)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StartTag:
    """One start tag and where its class attribute lives in the document."""

    start: int
    end: int
    classes: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class FigureMarkup:
    """Where the figure's knobs are in one authored slide document."""

    root: StartTag
    nodes: tuple[StartTag, ...]
    density_class: str
    emphasis_index: int | None

    @property
    def node_count(self) -> int:
        return len(self.nodes)


def _classes(raw_attrs: str) -> tuple[str, ...]:
    match = _CLASS_ATTR.search(raw_attrs)
    if match is None:
        return ()
    return tuple(token for token in match.group("value").split() if token)


def _tags(html: str) -> list[StartTag]:
    tags: list[StartTag] = []
    for match in _START_TAG.finditer(html or ""):
        raw = match.group(0)
        tags.append(
            StartTag(
                start=match.start(),
                end=match.end(),
                classes=_classes(match.group("attrs")),
                raw=raw,
            )
        )
    return tags


def _replace_classes(raw_tag: str, classes: tuple[str, ...]) -> str:
    value = " ".join(classes)
    match = _CLASS_ATTR.search(raw_tag)
    if match is None:
        raise ValueError("figure markup element has no class attribute to rewrite")
    quote = match.group("quote")
    rewritten = f"{match.group('head')}{quote}{value}{quote}"
    return raw_tag[: match.start()] + rewritten + raw_tag[match.end() :]


def find_figure(html: str) -> FigureMarkup | None:
    """Return the first archetype figure of a slide, or ``None`` when it has none.

    The figure root is the element carrying both the ``ig`` root class and a
    density class; per the authoring contract a slide holds at most one figure,
    so the nodes are simply the ``.ig-node`` elements that follow the root.
    """
    tags = _tags(html)
    root: StartTag | None = None
    for tag in tags:
        if ROOT_CLASS in tag.classes and any(name in tag.classes for name in DENSITY_CLASSES):
            root = tag
            break
    if root is None:
        return None
    density = next(name for name in DENSITY_CLASSES if name in root.classes)
    nodes = tuple(
        tag for tag in tags if tag.start > root.start and NODE_CLASS in tag.classes
    )
    emphasis = next(
        (index for index, tag in enumerate(nodes) if EMPHASIS_CLASS in tag.classes),
        None,
    )
    return FigureMarkup(root=root, nodes=nodes, density_class=density, emphasis_index=emphasis)


def has_figure_markup(html: str) -> bool:
    return find_figure(html) is not None


def class_tags(html: str, class_name: str) -> tuple[StartTag, ...]:
    """Every start tag carrying ``class_name``, in document order."""
    return tuple(tag for tag in _tags(html) if class_name in tag.classes)


def _rewrite(html: str, edits: list[tuple[StartTag, tuple[str, ...]]]) -> str:
    """Apply class rewrites back-to-front so earlier offsets stay valid."""
    result = html
    for tag, classes in sorted(edits, key=lambda item: item[0].start, reverse=True):
        result = result[: tag.start] + _replace_classes(tag.raw, classes) + result[tag.end :]
    return result


def set_density_class(html: str, density: str) -> str:
    """Replace the figure root's ``n3``..``n6`` class; other classes are kept."""
    if density not in DENSITY_CLASSES:
        raise ValueError(f"unknown figure density class: {density!r}")
    figure = find_figure(html)
    if figure is None or figure.density_class == density:
        return html
    classes = tuple(
        density if name == figure.density_class else name for name in figure.root.classes
    )
    return _rewrite(html, [(figure.root, classes)])


def set_emphasis(html: str, node_index: int | None) -> str:
    """Move ``is-key`` to one node, or drop it entirely when ``node_index`` is None."""
    figure = find_figure(html)
    if figure is None:
        return html
    if node_index is not None and not 0 <= node_index < figure.node_count:
        raise ValueError(f"figure has no node at index {node_index}")
    if figure.emphasis_index == node_index:
        return html
    edits: list[tuple[StartTag, tuple[str, ...]]] = []
    for index, tag in enumerate(figure.nodes):
        wanted = index == node_index
        marked = EMPHASIS_CLASS in tag.classes
        if wanted == marked:
            continue
        classes = (
            tag.classes + (EMPHASIS_CLASS,)
            if wanted
            else tuple(name for name in tag.classes if name != EMPHASIS_CLASS)
        )
        edits.append((tag, classes))
    return _rewrite(html, edits)


def canonical_figure_html(html: str) -> str:
    """The document with its figure knobs neutralised.

    Two documents with the same canonical form differ only in the knobs
    best-of-N varies, so a resumed attempt can recognise its own earlier
    winner instead of re-rendering the whole candidate set.
    """
    figure = find_figure(html)
    if figure is None:
        return html
    return set_emphasis(set_density_class(html, DENSITY_CLASSES[0]), None)


def same_figure_document(first: str, second: str) -> bool:
    """True when two documents differ only by density/emphasis classes."""
    return canonical_figure_html(first) == canonical_figure_html(second)


def shift_density(density: str, step: int) -> str:
    """Return the density class ``step`` positions tighter (positive) or looser."""
    index = DENSITY_CLASSES.index(density)
    return DENSITY_CLASSES[max(0, min(len(DENSITY_CLASSES) - 1, index + step))]


__all__ = [
    "DENSITY_CLASSES",
    "StartTag",
    "canonical_figure_html",
    "class_tags",
    "EMPHASIS_CLASS",
    "FigureMarkup",
    "NODE_CLASS",
    "ROOT_CLASS",
    "find_figure",
    "has_figure_markup",
    "same_figure_document",
    "set_density_class",
    "set_emphasis",
    "shift_density",
]
