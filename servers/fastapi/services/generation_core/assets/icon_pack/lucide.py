"""The vendored Lucide icon set, composed the way the bundled library composes it.

``assets/lucide`` holds ``icon-nodes.json`` from ``lucide-static`` (see its
``manifest.json`` for the version, source and digest) and the ISC licence.
A deck's icons are chosen from this set by name (``utils/llm_calls/
select_deck_icons.py``); this module only turns a chosen name, or a main name
plus a small corner badge, into a monochrome 128-unit SVG with exactly the
geometry, stroke and layout of ``assets/infographics/monochrome-v1``: one
symbol at ``translate(12 12) scale(4.333333)``, or a main symbol at
``translate(8 8) scale(3.25)`` with its badge at ``translate(78 78)
scale(1.75)``, stroke 1.75 with round caps and joins. The result is an
ordinary icon file for the existing offer and flattening path.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import quoteattr

from utils.hashing import sha256_hex

LUCIDE_DIRECTORY = Path(__file__).resolve().parents[4] / "assets" / "lucide"
_ELEMENTS = frozenset({"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"})
_ATTRIBUTES = frozenset({"d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height",
                         "x1", "y1", "x2", "y2", "points", "fill"})
_SINGLE = "translate(12 12) scale(4.333333)"
_MAIN_WITH_BADGE = "translate(8 8) scale(3.25)"
_BADGE = "translate(78 78) scale(1.75)"


@lru_cache(maxsize=1)
def _load() -> tuple[str, dict[str, tuple[tuple[str, dict[str, str]], ...]]]:
    manifest = json.loads((LUCIDE_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    payload = (LUCIDE_DIRECTORY / manifest["file"]).read_bytes()
    if sha256_hex(payload) != manifest["sha256"]:
        raise ValueError("lucide_icon_nodes_digest_mismatch")
    nodes = {}
    for name, elements in json.loads(payload).items():
        children = []
        for tag, attributes in elements:
            if tag not in _ELEMENTS or not set(attributes) <= _ATTRIBUTES:
                raise ValueError("lucide_icon_node_unsupported:" + name)
            if attributes.get("fill", "currentColor") not in {"currentColor", "none"}:
                raise ValueError("lucide_icon_node_unsupported:" + name)
            children.append((tag, dict(attributes)))
        nodes[name] = tuple(children)
    if len(nodes) != manifest["icons"]:
        raise ValueError("lucide_icon_count_mismatch")
    return str(manifest["version"]), nodes


def lucide_version() -> str:
    return _load()[0]


def icon_names() -> tuple[str, ...]:
    """Every available icon name, sorted."""
    return tuple(sorted(_load()[1]))


def has_icon(name: object) -> bool:
    return isinstance(name, str) and name in _load()[1]


def _symbol(name: str, color: str) -> str:
    parts = []
    for tag, attributes in _load()[1][name]:
        attrs = dict(attributes)
        if attrs.get("fill") == "currentColor":
            attrs["fill"] = color
        parts.append(f"<{tag} " + " ".join(f"{key}={quoteattr(value)}" for key, value in attrs.items()) + "/>")
    return "".join(parts)


def compose_icon_svg(main: str, badge: Optional[str] = None, *, color: str = "#000000") -> str:
    """One monochrome icon in the library's 128-unit layout; raises for an unknown name."""
    for name in (main, badge):
        if name is not None and not has_icon(name):
            raise ValueError("lucide_icon_unknown")
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" '
            f'fill="none" stroke="{color}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">')
    if badge is None or badge == main:
        return f'{head}<g transform="{_SINGLE}">{_symbol(main, color)}</g></svg>'
    return (f'{head}<g transform="{_MAIN_WITH_BADGE}">{_symbol(main, color)}</g>'
            f'<g transform="{_BADGE}">{_symbol(badge, color)}</g></svg>')


def write_icon(main: str, badge: Optional[str], *, color: str, directory: Path) -> Path:
    """Write the composed icon to a content-addressed file under ``directory``."""
    svg = compose_icon_svg(main, badge, color=color)
    payload = svg.encode("utf-8")
    target = Path(directory) / f"{sha256_hex(payload)}.svg"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return target


__all__ = ["LUCIDE_DIRECTORY", "compose_icon_svg", "has_icon", "icon_names", "lucide_version", "write_icon"]
