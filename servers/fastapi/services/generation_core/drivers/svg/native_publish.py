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
import math
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS, chart_object
from services.generation_core.drivers.svg.native_declarations import bound_declarations
from services.generation_core.drivers.svg.fixed_axis_migration import (
    migrate_verified_generated_fixed_axis,
)
from services.generation_core.drivers.svg.native_geometry import (
    NativeRejection, collect_primitives, find_group, paint,
)
from services.generation_core.drivers.svg.native_paint import background_layers
from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, table_object
from services.generation_core.native_objects import NativeObject, NativeObjects, NativeObjectsError

_EMU_PER_POINT = 12700


def _sibling_canvas_texts(root: ET.Element, native_group_ids: frozenset[str]) -> tuple:
    """Collect visible sibling text, excluding all bound native groups."""
    canvas = ET.fromstring(ET.tostring(root))
    def visible(node: ET.Element) -> bool:
        if any(key in node.attrib for key in ("style", "class", "clip-path", "mask", "filter")):
            return False
        if (node.get("display") not in (None, "inline")
                or node.get("visibility") not in (None, "visible")):
            return False
        if paint(node.get("fill", "black")) is None:
            return False
        try:
            return all(math.isfinite(float(node.get(key, "1"))) and float(node.get(key, "1")) > 0
                       for key in ("opacity", "fill-opacity"))
        except ValueError:
            return False
    def prune(parent: ET.Element) -> None:
        for child in list(parent):
            if (child.get("id") in native_group_ids
                    or child.get("data-native-chart") is not None
                    or child.get("data-native-table") is not None
                    or child.tag.rsplit("}", 1)[-1] not in {"g", "text", "tspan"}
                    or not visible(child)):
                parent.remove(child)
            else:
                prune(child)
    if not visible(canvas):
        return ()
    prune(canvas)
    return collect_primitives(canvas, (0, 0), {}, CHART_ELEMENTS).texts


def canvas_point_per_px(canvas_metadata: object) -> float:
    """Points per canvas px of the deck's canvas (0.75 for the 9525-EMU px)."""
    from services.document_canvas import canvas_physical_size, parse_canvas_metadata

    width_emu, _ = canvas_physical_size(canvas_metadata)
    return width_emu / parse_canvas_metadata(canvas_metadata).width_px / _EMU_PER_POINT


def _entry(root: ET.Element, declaration: dict, *, width: float, height: float,
           pt_per_px: float, svg: str,
           source_preservation: dict | None, native_group_ids: frozenset[str],
           native_group_ids_complete: bool, binding_digest: str,
           verified_fixed_axis_migration: dict | None = None,
           revision: str | None = None, trusted: bool = False) -> NativeObject:
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
    if kind == "chart" and verified_fixed_axis_migration is not None:
        raw = migrate_verified_generated_fixed_axis(
            raw, svg, verified_fixed_axis_migration, source_preservation,
        )
    if kind == "table":
        anchor, payload = table_object(raw, prims, pt_per_px=pt_per_px,
                                       layers=background_layers(root, group))
    else:
        canvas_texts = ()
        if raw.get("axisContract") == "native-chart-fixed-value-axis/v1":
            if (source_preservation is not None
                    and (not isinstance(source_preservation, dict)
                         or source_preservation.get("source_native_charts") != [])):
                raise NativeRejection("native_chart_fixed_axis_source_conflict")
            if not native_group_ids_complete:
                raise NativeRejection("native_chart_sibling_ownership_unproven")
            canvas_texts = _sibling_canvas_texts(root, native_group_ids)
        try:
            anchor, payload, appearance = chart_object(
                raw, prims, canvas_texts=canvas_texts, revision=revision,
            )
        except NativeRejection:
            if not trusted:
                raise
            # A trusted server chart is accepted by construction: its payload comes from
            # the declaration its drawing was replayed from, not from a drawing proof.
            anchor, payload, appearance = _trusted_chart_object(declaration)
        from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V5
        if revision == REQUIREMENTS_VERSION_V5 and payload["chartType"] == "donut":
            from services.generation_core.drivers.svg.native_chart import _model_chart
            from services.generation_core.drivers.svg.native_chart_evidence import _v5_scene
            from services.generation_core.drivers.svg.chart_content_polar import prove_polar
            # Match V5's content reader: the canonical source declaration and
            # scene prove this ratio, independently of export-enriched payloads.
            records, primitive_type = _v5_scene(root)
            local = [record for record in records if group in record["ancestors"]]
            canonical_prims = primitive_type(
                tuple(record["shape"] for record in local if "shape" in record),
                tuple(record["text"] for record in local if "text" in record), prims.tags,
            )
            geometry = prove_polar(raw, _model_chart(raw), canonical_prims)
            if geometry.get("passed"):
                appearance = {**(appearance or {}), "holeRatio": geometry["holeRatio"]}
        if payload.get("retainedAxisTitle"):
            payload["retainedAxisTitle"].update(
                svgDigest=binding_digest,
                nativeGroupIds=sorted(native_group_ids),
            )
    entry = NativeObject.build(id=group_id, kind=kind, anchor=anchor, payload=payload,
                               svgAppearance=appearance if kind == "chart" else None)
    if not entry.anchor.within(width, height):
        raise NativeRejection("native_anchor_outside_canvas")
    return entry


def _trusted_chart_object(declaration: dict) -> tuple[dict, dict, dict | None]:
    """``(anchor, payload, appearance)`` of a trusted server chart, from its declaration alone."""
    from services.generation_core.drivers.svg.native_chart import _model_chart

    raw = declaration["raw"]
    payload = _model_chart(raw)
    for item, declared in zip(payload["series"], raw["series"], strict=True):
        colors = declared.get("pointColors")
        if payload["chartType"] not in ("pie", "donut") and isinstance(colors, list) and len(set(colors)) == 1:
            item["color"] = colors[0]
    style = declaration.get("style")
    if isinstance(style, dict) and isinstance(style.get("font"), str):
        payload["fontFamily"] = style["font"]
    appearance = ({key: raw[key] for key in ("categoryOrder", "valueLabels", "markers")}
                  if {"categoryOrder", "valueLabels", "markers"} <= set(raw) else None)
    x, y, width, height = (float(value) for value in declaration["box"])
    return {"x": x, "y": y, "width": width, "height": height}, payload, appearance


def _canvas_size(root: ET.Element) -> tuple[float, float]:
    numbers = [float(n) for n in re.findall(r"[-+]?\d*\.?\d+", root.get("viewBox", ""))]
    if len(numbers) == 4:
        return numbers[2], numbers[3]
    return float(root.get("width", 0)), float(root.get("height", 0))


def page_native_objects(payload: dict, *, pt_per_px: float,
                        verified_fixed_axis_migrations: dict[str, dict] | None = None,
                        revision: str | None = None
                        ) -> tuple[dict | None, dict | None]:
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
    bound_items = [*objects, *dropped]
    native_group_ids = frozenset(
        item["id"] for item in bound_items
        if isinstance(item.get("id"), str) and item["id"]
    )
    native_group_ids_complete = len(native_group_ids) == len(bound_items)
    from services.generation_core.drivers.svg.server_chart import trusted_server_chart_ids
    trusted = trusted_server_chart_ids(record, svg)
    accepted: list[NativeObject] = []
    for item in objects:
        try:
            verified = ((verified_fixed_axis_migrations or {}).get(item.get("id"))
                        if item.get("kind") == "chart" else None)
            entry = _entry(root, item, width=width, height=height, pt_per_px=pt_per_px,
                           svg=svg, source_preservation=payload.get("source_preservation"),
                           native_group_ids=native_group_ids,
                           native_group_ids_complete=native_group_ids_complete,
                           binding_digest=record["svgDigest"],
                           verified_fixed_axis_migration=verified,
                           revision=revision, trusted=item.get("id") in trusted)
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
