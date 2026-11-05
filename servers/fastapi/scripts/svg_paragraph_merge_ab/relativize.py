"""Relative line positions for author-written multi-line text, before PPTX conversion.

``SVG_AUTHOR_RULES`` asks for immediate ``<tspan>`` children with explicit
``x``/``y`` per line. The vendored converter folds a multi-line ``<text>`` into
one reflowing text frame only when its lines are positioned by ``dy`` (it
rejects an absolute ``y`` on a line tspan), so such text exports as one
``wrap="none"`` frame per line. This pass rewrites the absolute baseline of each
line into the equivalent ``dy`` step. It changes no rendered position: the
first line keeps the text's own ``y`` and every later line sits exactly where
its ``y`` put it. Whether the lines then merge into one paragraph (same pitch,
same size, no list markers, no section-sized gap) is still decided by the
converter; text it does not merge converts as before.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from services.svg_contract import SVG_NS

_TSPAN = f"{{{SVG_NS}}}tspan"
_TEXT = f"{{{SVG_NS}}}text"
_NATIVE_MARK = "data-pptx-native"
_POSITION = ("x", "y", "dx", "dy")


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _format(value: float) -> str:
    return str(int(round(value))) if abs(value - round(value)) < 1e-6 else f"{value:.6f}".rstrip("0").rstrip(".")


def _has_positioned_descendant(tspan: ET.Element) -> bool:
    return any(item.tag == _TSPAN and (any(item.get(key) is not None for key in _POSITION)
                                       or _has_positioned_descendant(item)) for item in tspan)


def _line_steps(text: ET.Element) -> list[float] | None:
    """The ``dy`` of every line after the first, or ``None`` when the text is not such a block."""
    if (text.text or "").strip() or any(text.get(key) is not None for key in ("dx", "dy", "rotate")):
        return None
    lines = list(text)
    if len(lines) < 2 or any(line.tag != _TSPAN or (line.tail or "").strip() for line in lines):
        return None
    base_x, base_y = _number(text.get("x")), _number(text.get("y"))
    ys = []
    for line in lines:
        x, y = _number(line.get("x")), _number(line.get("y"))
        if (x is None or y is None or base_x is None or abs(x - base_x) > 1e-6
                or any(line.get(key) is not None for key in ("dx", "dy", "rotate"))
                or _has_positioned_descendant(line)):
            return None
        ys.append(y)
    if base_y is None or abs(ys[0] - base_y) > 1e-6:
        return None
    steps = [later - earlier for earlier, later in zip(ys, ys[1:], strict=False)]
    return steps if all(step > 0 for step in steps) else None


def relativize_line_positions(root: ET.Element) -> int:
    """Rewrite ``y`` to ``dy`` on every eligible multi-line text; return how many were rewritten."""
    skipped: set[int] = set()
    for group in root.iter():
        if group.get(_NATIVE_MARK) is not None:
            skipped.update(id(item) for item in group.iter())
    rewritten = 0
    for text in root.iter(_TEXT):
        if id(text) in skipped:
            continue
        steps = _line_steps(text)
        if steps is None:
            continue
        lines = list(text)
        del lines[0].attrib["y"]
        for line, step in zip(lines[1:], steps, strict=True):
            del line.attrib["y"]
            line.set("dy", _format(step))
        rewritten += 1
    return rewritten


__all__ = ["relativize_line_positions"]
