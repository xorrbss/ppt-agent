"""The closed set of corrections a design critic is allowed to ask for.

The critic never returns HTML. It returns at most a few of these operations,
and every one of them is applied here by rewriting a class attribute or by
replacing one label's text with a shorter piece of *its own* text. An operation
that cannot be proven safe is refused rather than approximated, so a critic
that misunderstands a slide can only leave it unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape, unescape
from typing import Any, Literal

from utils.authored_figure_variants.markup import (
    DENSITY_CLASSES,
    class_tags,
    find_figure,
    set_density_class,
    set_emphasis,
)

OperationKind = Literal["set_density", "move_emphasis", "shorten_label"]
#: The label element the ``shorten_label`` operation may rewrite.
LABEL_CLASS = "ig-label"
_TEXT_END = re.compile(r"</")


@dataclass(frozen=True)
class FigureOperation:
    """One closed correction; unused fields stay ``None``."""

    kind: OperationKind
    density: str | None = None
    node_index: int | None = None
    text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "density": self.density,
            "node_index": self.node_index,
            "text": self.text,
        }


class OperationRefused(ValueError):
    """Raised when an operation cannot be applied to this figure as stated."""


def _label_spans(html: str) -> tuple[tuple[int, int], ...]:
    """Content spans of every figure label, in node order.

    A label whose content is not plain text is excluded, which makes the index
    mapping unreliable; callers refuse the operation instead of guessing.
    """
    spans: list[tuple[int, int]] = []
    for tag in class_tags(html, LABEL_CLASS):
        end = _TEXT_END.search(html, tag.end)
        if end is None:
            return ()
        if "<" in html[tag.end : end.start()]:
            return ()
        spans.append((tag.end, end.start()))
    return tuple(spans)


def _apply_shorten_label(html: str, operation: FigureOperation) -> str:
    figure = find_figure(html)
    if figure is None:
        raise OperationRefused("slide carries no figure")
    index = operation.node_index
    replacement = (operation.text or "").strip()
    if index is None or not replacement:
        raise OperationRefused("shorten_label needs a node index and replacement text")
    spans = _label_spans(html)
    if len(spans) != figure.node_count or not 0 <= index < len(spans):
        raise OperationRefused("figure labels do not map one-to-one onto its nodes")
    start, end = spans[index]
    current = unescape(html[start:end]).strip()
    if len(replacement) >= len(current):
        raise OperationRefused("replacement label is not shorter than the current one")
    if replacement not in current:
        # Shortening must remove characters, never introduce wording the plan
        # did not copy verbatim from the slide content.
        raise OperationRefused("replacement label is not part of the current label")
    return html[:start] + escape(replacement, quote=False) + html[end:]


def apply_operation(html: str, operation: FigureOperation) -> str:
    """Apply one operation, raising ``OperationRefused`` when it does not hold."""
    if operation.kind == "set_density":
        if operation.density not in DENSITY_CLASSES:
            raise OperationRefused(f"unknown density class {operation.density!r}")
        if find_figure(html) is None:
            raise OperationRefused("slide carries no figure")
        return set_density_class(html, operation.density)
    if operation.kind == "move_emphasis":
        figure = find_figure(html)
        if figure is None:
            raise OperationRefused("slide carries no figure")
        index = operation.node_index
        if index is not None and not 0 <= index < figure.node_count:
            raise OperationRefused(f"figure has no node at index {index}")
        return set_emphasis(html, index)
    if operation.kind == "shorten_label":
        return _apply_shorten_label(html, operation)
    raise OperationRefused(f"unsupported operation {operation.kind!r}")


def apply_operations(
    html: str, operations: Sequence[FigureOperation]
) -> tuple[str, tuple[FigureOperation, ...], tuple[str, ...]]:
    """Apply every operation that holds; return the HTML, the applied set, refusals."""
    result = html
    applied: list[FigureOperation] = []
    refused: list[str] = []
    for operation in operations:
        try:
            candidate = apply_operation(result, operation)
        except OperationRefused as error:
            refused.append(f"{operation.kind}: {error}")
            continue
        if candidate == result:
            refused.append(f"{operation.kind}: no change")
            continue
        result = candidate
        applied.append(operation)
    return result, tuple(applied), tuple(refused)


__all__ = [
    "LABEL_CLASS",
    "FigureOperation",
    "OperationKind",
    "OperationRefused",
    "apply_operation",
    "apply_operations",
]
