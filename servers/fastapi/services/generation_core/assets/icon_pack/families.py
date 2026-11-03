"""Vendored icon families, composed the way the bundled library composes icons.

Each family lives in ``assets/icon_families/<id>/``: ``nodes.json`` (icon name
to ``[tag, attributes]`` drawing nodes), ``manifest.json`` (package, version,
source, digest, grid, stroke, style) and the upstream licence. A deck's icons
are chosen from one family by name (``utils/llm_calls/select_deck_icons.py``);
this module turns a chosen name, or a main name plus a small corner badge, into
a monochrome 128-unit SVG with the layout of ``assets/infographics/
monochrome-v1``: one symbol filling the box at offset 12, or a main symbol at
offset 8 with its badge at offset 78, scaled from the family's grid. Outline
families draw with the manifest stroke and round caps and joins. The result
is an ordinary icon file for the existing offer and flattening path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import quoteattr

from utils.hashing import sha256_hex

FAMILIES_DIRECTORY = Path(__file__).resolve().parents[4] / "assets" / "icon_families"
DEFAULT_FAMILY = "lucide"
_ELEMENTS = frozenset({"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"})
_ATTRIBUTES = frozenset({"d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height",
                         "x1", "y1", "x2", "y2", "points", "fill", "stroke", "opacity"})
_PAINT = frozenset({"currentColor", "none"})
#: The library's 128-unit layout for a 24-unit symbol: (offset, scale).
_SINGLE = (12.0, 104.0 / 24)
_MAIN_WITH_BADGE = (8.0, 78.0 / 24)
_BADGE = (78.0, 42.0 / 24)


@dataclass(frozen=True)
class IconFamily:
    id: str
    title: str
    style: str
    version: str
    grid: float
    stroke_width: Optional[float]
    nodes: dict[str, tuple[tuple[str, dict[str, str]], ...]]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.nodes))


def available_families() -> tuple[str, ...]:
    return tuple(sorted(path.parent.name for path in FAMILIES_DIRECTORY.glob("*/manifest.json")))


@lru_cache(maxsize=8)
def load_family(family_id: str = DEFAULT_FAMILY) -> IconFamily:
    """The vendored family, digest-checked; raises for an unknown or altered family."""
    if family_id not in available_families():
        raise ValueError("icon_family_unknown")
    directory = FAMILIES_DIRECTORY / family_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload = (directory / manifest["file"]).read_bytes()
    if manifest.get("id") != family_id or sha256_hex(payload) != manifest["sha256"]:
        raise ValueError("icon_family_digest_mismatch")
    nodes = {}
    for name, elements in json.loads(payload).items():
        children = []
        for tag, attributes in elements:
            if (tag not in _ELEMENTS or not set(attributes) <= _ATTRIBUTES
                    or any(attributes.get(key, "none") not in _PAINT for key in ("fill", "stroke"))
                    or not _opacity_ok(attributes.get("opacity", "1"))):
                raise ValueError("icon_family_node_unsupported:" + name)
            children.append((tag, dict(attributes)))
        nodes[name] = tuple(children)
    if len(nodes) != manifest["icons"]:
        raise ValueError("icon_family_count_mismatch")
    stroke = manifest.get("strokeWidth")
    return IconFamily(id=family_id, title=str(manifest["title"]), style=str(manifest["style"]),
                      version=str(manifest["version"]), grid=float(manifest["grid"]),
                      stroke_width=float(stroke) if stroke is not None else None, nodes=nodes)


def _opacity_ok(value: str) -> bool:
    try:
        return 0.0 <= float(value) <= 1.0
    except ValueError:
        return False


def has_icon(name: object, family_id: str = DEFAULT_FAMILY) -> bool:
    return isinstance(name, str) and name in load_family(family_id).nodes


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _symbol(family: IconFamily, name: str, color: str) -> str:
    parts = []
    for tag, attributes in family.nodes[name]:
        attrs = {key: (color if value == "currentColor" else value) for key, value in attributes.items()}
        parts.append(f"<{tag} " + " ".join(f"{key}={quoteattr(value)}" for key, value in attrs.items()) + "/>")
    return "".join(parts)


def _group(family: IconFamily, name: str, color: str, placement: tuple[float, float]) -> str:
    offset, scale = placement
    return (f'<g transform="translate({_number(offset)} {_number(offset)}) '
            f'scale({_number(scale * 24 / family.grid)})">{_symbol(family, name, color)}</g>')


def compose_icon_svg(main: str, badge: Optional[str] = None, *, color: str = "#000000",
                     family_id: str = DEFAULT_FAMILY) -> str:
    """One monochrome icon in the library's 128-unit layout; raises for an unknown name."""
    family = load_family(family_id)
    for name in (main, badge):
        if name is not None and name not in family.nodes:
            raise ValueError("icon_family_icon_unknown")
    if family.stroke_width is not None:
        paint = (f'fill="none" stroke="{color}" stroke-width="{_number(family.stroke_width * family.grid / 24)}" '
                 'stroke-linecap="round" stroke-linejoin="round"')
    else:
        paint = f'fill="{color}" stroke="none"'
    head = f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" {paint}>'
    if badge is None or badge == main:
        return f"{head}{_group(family, main, color, _SINGLE)}</svg>"
    return (f"{head}{_group(family, main, color, _MAIN_WITH_BADGE)}"
            f"{_group(family, badge, color, _BADGE)}</svg>")


#: Person icons a pictograph repeats, first present in the family wins.
PICTOGRAPH_PERSON = ("user-round", "user", "person")
PICTOGRAPH_CELL = 128
PICTOGRAPH_MUTED_OPACITY = 0.25


def compose_pictograph_svg(*, total: int, highlighted: int, color: str, family_id: str = DEFAULT_FAMILY,
                           columns: int = 5) -> str:
    """``total`` person icons in rows of ``columns``; the first ``highlighted`` at full ink, the rest muted.

    Built from the family's own person icon in the library's 128-unit cell, so
    a pictograph matches the deck's icons. The count is exact: nothing here is
    drawn by a model.
    """
    family = load_family(family_id)
    name = next((candidate for candidate in PICTOGRAPH_PERSON if candidate in family.nodes), None)
    if name is None:
        raise ValueError("icon_family_has_no_person_icon")
    if not (1 <= total <= 20 and 0 <= highlighted <= total and 1 <= columns <= total):
        raise ValueError("pictograph_counts_invalid")
    rows = -(-total // columns)
    width, height = columns * PICTOGRAPH_CELL, rows * PICTOGRAPH_CELL
    if family.stroke_width is not None:
        paint = (f'fill="none" stroke="{color}" stroke-width="{_number(family.stroke_width * family.grid / 24)}" '
                 'stroke-linecap="round" stroke-linejoin="round"')
    else:
        paint = f'fill="{color}" stroke="none"'
    offset, scale = _SINGLE
    cells = []
    for index in range(total):
        x = (index % columns) * PICTOGRAPH_CELL + offset
        y = (index // columns) * PICTOGRAPH_CELL + offset
        muted = "" if index < highlighted else f' opacity="{PICTOGRAPH_MUTED_OPACITY}"'
        cells.append(f'<g transform="translate({_number(x)} {_number(y)}) scale({_number(scale * 24 / family.grid)})"'
                     f'{muted}>{_symbol(family, name, color)}</g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" {paint}>' + "".join(cells) + "</svg>")


def write_icon(main: str, badge: Optional[str], *, color: str, directory: Path,
               family_id: str = DEFAULT_FAMILY) -> Path:
    """Write the composed icon to a content-addressed file under ``directory``."""
    payload = compose_icon_svg(main, badge, color=color, family_id=family_id).encode("utf-8")
    target = Path(directory) / f"{sha256_hex(payload)}.svg"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return target


__all__ = ["DEFAULT_FAMILY", "FAMILIES_DIRECTORY", "IconFamily", "available_families", "compose_icon_svg",
           "compose_pictograph_svg",
           "has_icon", "load_family", "write_icon"]
