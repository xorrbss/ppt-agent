"""Publication step for model-declared native tables/charts of one SVG page.

:func:`page_native_objects` is the only place a declaration becomes a
``NativeObjects`` entry. It honours a page's declarations only when they are
bound to the page's final SVG (``native_declarations.bound_declarations``),
checks each declared group against the final drawing (``native_table`` /
``native_chart``) and builds the entry with ``NativeObject.build``. A rejected
declaration drops only its own native annotation -- the drawn primitives stay
in the SVG and the page is never failed -- and the reason is recorded.

Receipt (``svgReceipt["nativeObjects"]``, present whenever the page recorded
declarations)::

    {"declared": n, "accepted": k, "dropped": [{"id", "kind", "reason"}]}

Reason codes: extraction (``native_declaration_*``, ``native_chart_not_offered``),
binding (``native_declaration_stale_svg``), group (``native_group_missing``,
``native_group_element_unsupported``, ``native_group_transform_unsupported``,
``native_group_shape_empty``), table (``native_table_*``), chart
(``native_chart_*``), canvas (``native_anchor_outside_canvas``) and sidecar
shape (``native_object_*`` from ``NativeObject.build``).
"""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS, chart_object
from services.generation_core.drivers.svg.native_declarations import bound_declarations
from services.generation_core.drivers.svg.native_geometry import (
    NativeRejection, collect_primitives, find_group,
)
from services.generation_core.drivers.svg.native_paint import background_layers
from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, table_object
from services.generation_core.native_objects import NativeObject, NativeObjects, NativeObjectsError

_EMU_PER_POINT = 12700


def canvas_point_per_px(canvas_metadata: object) -> float:
    """Points per canvas px of the deck's canvas (0.75 for the 9525-EMU px)."""
    from services.document_canvas import canvas_physical_size, parse_canvas_metadata

    width_emu, _ = canvas_physical_size(canvas_metadata)
    return width_emu / parse_canvas_metadata(canvas_metadata).width_px / _EMU_PER_POINT


def _entry(root: ET.Element, declaration: dict, *, width: float, height: float,
           pt_per_px: float) -> NativeObject:
    kind, group_id = declaration.get("kind"), declaration.get("id")
    if kind not in ("table", "chart") or not isinstance(group_id, str):
        raise NativeRejection("native_declaration_invalid")
    found = find_group(root, group_id)
    if found is None:
        raise NativeRejection("native_group_missing")
    group, offset, inherited = found
    allowed = TABLE_ELEMENTS if kind == "table" else CHART_ELEMENTS
    prims = collect_primitives(group, offset, inherited, allowed)
    raw = declaration.get("raw")
    if not isinstance(raw, dict):
        raise NativeRejection("native_declaration_json_invalid")
    if kind == "table":
        anchor, payload = table_object(raw, prims, pt_per_px=pt_per_px,
                                       layers=background_layers(root, group))
    else:
        anchor, payload, appearance = chart_object(raw, prims)
    entry = NativeObject.build(id=group_id, kind=kind, anchor=anchor, payload=payload,
                               svgAppearance=appearance if kind == "chart" else None)
    if not entry.anchor.within(width, height):
        raise NativeRejection("native_anchor_outside_canvas")
    return entry


def _canvas_size(root: ET.Element) -> tuple[float, float]:
    numbers = [float(n) for n in re.findall(r"[-+]?\d*\.?\d+", root.get("viewBox", ""))]
    if len(numbers) == 4:
        return numbers[2], numbers[3]
    return float(root.get("width", 0)), float(root.get("height", 0))


def page_native_objects(payload: dict, *, pt_per_px: float) -> tuple[dict | None, dict | None]:
    """``(nativeObjects wire or None, receipt summary or None)`` for one page payload."""
    svg = payload.get("svg_content")
    record, current = bound_declarations(payload, svg)
    if record is None:
        return None, None
    objects = [item for item in record.get("objects") or [] if isinstance(item, dict)]
    dropped = [dict(item) for item in record.get("dropped") or [] if isinstance(item, dict)]
    declared = len(objects) + len(dropped)
    if not current:
        dropped += [{"id": item.get("id"), "kind": item.get("kind"),
                     "reason": "native_declaration_stale_svg"} for item in objects]
        return None, {"declared": declared, "accepted": 0, "dropped": dropped}
    root = ET.fromstring(svg)
    width, height = _canvas_size(root)
    accepted: list[NativeObject] = []
    for item in objects:
        try:
            entry = _entry(root, item, width=width, height=height, pt_per_px=pt_per_px)
        except (NativeRejection, NativeObjectsError) as error:
            dropped.append({"id": item.get("id"), "kind": item.get("kind"), "reason": str(error)})
            continue
        except (TypeError, ValueError):
            dropped.append({"id": item.get("id"), "kind": item.get("kind"),
                            "reason": "native_declaration_invalid"})
            continue
        if any(existing.id == entry.id for existing in accepted):
            dropped.append({"id": entry.id, "kind": entry.kind, "reason": "native_object_id_duplicate"})
            continue
        accepted.append(entry)
    summary = {"declared": declared, "accepted": len(accepted), "dropped": dropped}
    return (NativeObjects(tuple(accepted)).to_wire() if accepted else None), summary


__all__ = ["canvas_point_per_px", "page_native_objects"]
