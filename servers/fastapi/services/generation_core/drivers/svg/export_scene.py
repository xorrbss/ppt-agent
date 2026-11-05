"""Native tables/charts for every svg deck's PPTX export (one path for all).

Right before the vendored converter runs, each page's native-object entries
-- its ``NativeObjects`` sidecar from the export bundle plus, on raster
redesign pages, the entries read back from their validated table grids --
are projected into converter payloads and annotated on the ``<g>`` whose id
they name (``data-pptx-native`` + ``data-pptx-json``). Each annotation is
pre-validated with the converter's own marker validator; an object that
cannot be native keeps its drawn group and is recorded with a reason. If the
converter still fails with native objects, the caller restores every page
(``fall_back_all``) and converts the drawn groups instead, again on record.
Nothing is ever dropped silently: the receipt accounts for every offer and
``verify_readback`` checks the produced PPTX holds exactly the native ones.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from pptx.enum.shapes import MSO_SHAPE_TYPE

from services.generation_core.drivers.svg.export_native_wire import PageNativeObjects
from services.generation_core.drivers.svg.export_projection import converter_payload
from services.generation_core.native_objects import NativeObject
from services.source_content.raster_redesign_pptx import (
    is_redesign_svg,
    redesign_native_objects,
    table_cell_overlays,
)
from services.svg_contract import SVG_NS
import math
import re

_MAX_REASON = 200
_READBACK_MISMATCH = "svg_native_objects_readback_mismatch"


@dataclass
class NativeOffer:
    page: int
    id: str | None
    kind: str | None
    raster: bool = False
    native: bool = False
    reason: str | None = None


@dataclass
class SvgPptxInputs:
    redesign_active: bool = False
    raster_tables_skipped: int = 0
    # (page, table group id) -> pixel-cell images drawn over that raster table.
    raster_cell_overlays: dict[tuple[int, str], int] = field(default_factory=dict)
    offers: list[NativeOffer] = field(default_factory=list)
    original_sources: dict[Path, str] = field(default_factory=dict)
    required_source_charts: set[tuple[int, str]] = field(default_factory=set)

    @property
    def native_requested(self) -> bool:
        return any(offer.native for offer in self.offers)

    def fall_back_all(self, reason: str) -> None:
        """Rewrite every annotated page to its source; every native offer falls back."""
        if self.required_source_charts:
            raise ValueError("svg_source_native_chart_unprojectable")
        for path, source in self.original_sources.items():
            path.write_text(source, encoding="utf-8")
        for offer in self.offers:
            if offer.native:
                offer.native, offer.reason = False, reason[:_MAX_REASON]

    def receipt(self) -> dict:
        native = sum(offer.native for offer in self.offers)
        return {
            "offered": len(self.offers), "native": native, "fellBack": len(self.offers) - native,
            "reasons": [{"page": o.page, "id": o.id, "kind": o.kind, "reason": o.reason}
                        for o in self.offers if not o.native],
        }

    def raster_evidence(self) -> dict:
        """The raster-redesign block, key for key as before native objects existed."""
        raster = [offer for offer in self.offers if offer.raster]
        delivered = sum(offer.native for offer in raster)
        return {
            "rasterRedesign": self.redesign_active,
            "nativeTablesRequested": len(raster),
            "nativeTablesDelivered": delivered,
            "tablesKeptAsGrid": self.raster_tables_skipped + len(raster) - delivered,
            "nativeTableFallbackReason": next((offer.reason for offer in raster if not offer.native), None),
            # Unverified cells shown as source pixels over delivered native tables.
            "tableCellOverlays": sum(self.raster_cell_overlays.get((offer.page, offer.id), 0)
                                     for offer in raster if offer.native),
        }

    def verify_readback(self, deck) -> None:
        """Require each slide's native table/chart frames to equal its native offers."""
        for index, slide in enumerate(deck.slides):
            shapes = list(_iter_shapes(slide.shapes))
            counted = (sum(shape.has_table for shape in shapes), sum(shape.has_chart for shape in shapes))
            expected = tuple(sum(o.native and o.page == index and o.kind == kind for o in self.offers)
                             for kind in ("table", "chart"))
            if counted != expected:
                raise ValueError(_READBACK_MISMATCH)


def native_fallbacks_recorded(receipt) -> bool:
    """Whether a native-objects receipt accounts for every object that stayed drawn.

    An object that cannot be projected keeps its drawn shapes and the export
    goes on; what it must not do is vanish: each fell-back offer needs its
    own recorded reason.
    """
    if not isinstance(receipt, dict):
        return False
    offered, native, fell_back, reasons = (receipt.get(key) for key in ("offered", "native", "fellBack", "reasons"))
    return (all(type(value) is int and value >= 0 for value in (offered, native, fell_back))
            and offered == native + fell_back and isinstance(reasons, list) and len(reasons) == fell_back
            and all(isinstance(item, dict) and isinstance(item.get("reason"), str) and item["reason"]
                    for item in reasons))


def _iter_shapes(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)


def _page_entries(index: int, page: PageNativeObjects, inputs: SvgPptxInputs) -> list[NativeObject]:
    if page.invalid_reason is not None:
        reason = f"sidecar_invalid:{page.invalid_reason}"[:_MAX_REASON]
        for object_id, kind in page.invalid_objects or ((None, None),):
            inputs.offers.append(NativeOffer(index, object_id, kind, reason=reason))
        return []
    return list(page.sidecar.objects) if page.sidecar else []


def _source_table_vertical_padding(root, item, projected, *, pt_per_px):
    """Reserve only the exact vertical cell insets proved by the source grid."""
    from services.generation_core.drivers.svg.native_geometry import Box, NativeRejection, collect_primitives, find_group
    from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, _grid, _place

    try:
        found = find_group(root, item.id)
        if found is None:
            return
        prims = collect_primitives(*found, allowed=TABLE_ELEMENTS)
        anchor = item.anchor
        frame = Box(anchor.x, anchor.y, anchor.x + anchor.width, anchor.y + anchor.height)
        rows = [[cell.get("text", "") if isinstance(cell, dict) else cell for cell in row]
                for row in item.payload["rows"]]
        xs, ys = _grid(prims, frame, len(rows), len(rows[0]))
        placed = _place(prims.texts, xs, ys, rows)
        updates = []
        for (row, column), text in placed.items():
            box = text.box()
            if not text.uniform or not (xs[column] <= box.x0 <= box.x1 <= xs[column + 1]
                    and ys[row] <= box.y0 <= box.y1 <= ys[row + 1]):
                return
            cell = projected["rows"][row][column]
            existing = cell.get("padding", {})
            # Unspecified OOXML horizontal cell margins are 0.1 inch.
            padding = {"left": existing.get("left", 7.2 / pt_per_px),
                       "right": existing.get("right", 7.2 / pt_per_px),
                       "top": box.y0 - ys[row], "bottom": ys[row + 1] - box.y1}
            updates.append((cell, padding))
        for cell, padding in updates:
            cell["padding"] = padding
    except (NativeRejection, KeyError, TypeError, ValueError):
        return


def _retained_value_axis_title_proven(root: ET.Element, item: NativeObject,
                                     entry_ids: frozenset[str], svg_digest: str | None) -> bool:
    """Recheck a retained sibling title against the actual export SVG."""
    from services.generation_core.drivers.svg.native_chart import (
        CHART_ELEMENTS, _prove_external_value_axis_title,
    )
    from services.generation_core.drivers.svg.native_geometry import (
        NativeRejection, collect_primitives, find_group,
    )
    from services.generation_core.drivers.svg.native_publish import _sibling_canvas_texts

    retained = item.payload.get("retainedAxisTitle")
    if retained is None:
        return True
    native_ids = frozenset(retained["nativeGroupIds"])
    if (item.kind != "chart" or item.payload.get("axisContract") != "native-chart-fixed-value-axis/v1"
            or item.payload.get("valueAxis", {}).get("title") is not None
            or retained["svgDigest"] != svg_digest or item.id not in native_ids
            or not entry_ids <= native_ids):
        return False
    if any(node.tag == f"{{{SVG_NS}}}g" and node.get("id", "").startswith(
            ("native-chart-", "native-table-")) and node.get("id") not in native_ids
           for node in root.iter()):
        return False
    try:
        found = find_group(root, item.id)
        if found is None:
            return False
        prims = collect_primitives(*found, allowed=CHART_ELEMENTS)
        canvas_texts = _sibling_canvas_texts(root, native_ids)
        value_axis = {**item.payload["valueAxis"], "title": retained["text"]}
        payload = {**item.payload, "valueAxis": value_axis}
        _prove_external_value_axis_title(payload, prims, canvas_texts)
    except (NativeRejection, KeyError, TypeError, ValueError):
        return False
    return True


def _annotate(root: ET.Element, entries: list[tuple[NativeObject, bool]], index: int,
              inputs: SvgPptxInputs, *, width: float, height: float, pt_per_px: float = 0.75,
              source_svg_digest: str | None = None) -> int:
    from svg_to_pptx.native_objects import validate_native_object_marker
    from services.generation_core.drivers.svg.native_declarations import native_binding_digest

    svg_digest = source_svg_digest or native_binding_digest(ET.tostring(root, encoding="unicode"))
    entry_ids = frozenset(item.id for item, _ in entries)
    parents = {child: parent for parent in root.iter() for child in parent}
    elements = {element.get("id"): element for element in root.iter() if element.get("id")}
    seen: set[str] = set()
    marked = 0
    for item, raster in entries:
        offer = NativeOffer(index, item.id, item.kind, raster=raster)
        inputs.offers.append(offer)
        element = elements.get(item.id)
        if item.id in seen:
            offer.reason = "id_duplicate"
        elif element is None:
            offer.reason = "group_missing"
        elif element.tag != f"{{{SVG_NS}}}g":
            offer.reason = "element_not_group"
        elif not item.anchor.within(width, height):
            offer.reason = "anchor_outside_canvas"
        seen.add(item.id)
        if offer.reason is not None:
            continue
        if (item.kind == "chart" and (index, item.id) in inputs.required_source_charts
                and item.payload.get("axisContract") == "native-chart-fixed-value-axis/v1"):
            offer.reason = "source_native_chart_fixed_axis_conflict"
            continue
        if not _retained_value_axis_title_proven(root, item, entry_ids, svg_digest):
            offer.reason = "retained_axis_title_unproven"
            continue
        element.set("data-pptx-native", item.kind)
        projected = converter_payload(item, pt_per_px=pt_per_px)
        if item.kind == "table":
            from services.generation_core.drivers.svg.native_table import apply_drawn_table_style

            apply_drawn_table_style(projected, item, root)
            _source_table_vertical_padding(root, item, projected, pt_per_px=pt_per_px)
        source_legend = None
        source_underlays = []
        if item.kind == "chart":
            from services.generation_core.drivers.svg.export_chart_style import apply_drawn_chart_style
            from services.generation_core.drivers.svg.native_geometry import NativeRejection

            try:
                source_legend = apply_drawn_chart_style(projected, item, root,
                                                       source_underlays=source_underlays)
            except NativeRejection as error:
                del element.attrib["data-pptx-native"]
                offer.reason = str(error)[:_MAX_REASON]
                continue
            if (index, item.id) in inputs.required_source_charts:
                from services.svg_source_charts import apply_original_chart_axes
                apply_original_chart_axes(projected)
        element.set("data-pptx-json", json.dumps(projected, ensure_ascii=False, separators=(",", ":")))
        ancestors, node = [], parents.get(element)
        while node is not None:
            ancestors.insert(0, node)
            node = parents.get(node)
        try:
            validate_native_object_marker(element, ancestors=tuple(ancestors))
        except (RuntimeError, ValueError) as error:
            # Per-object fallback: this group converts as drawn; the others stay native.
            del element.attrib["data-pptx-native"], element.attrib["data-pptx-json"]
            offer.reason = f"converter_rejected:{error}"[:_MAX_REASON]
            continue
        offer.native = True
        for underlay in source_underlays:
            parent = parents[element]
            parent.insert(list(parent).index(element), underlay)
        if source_legend is not None:
            root.append(source_legend)
        marked += 1
    return marked



_NATIVE_MATRIX_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_NATIVE_MATRIX_ARGS = re.compile(
    r"\s*(" + _NATIVE_MATRIX_NUMBER + r")"
    + (r"(?:\s*,\s*|\s+)(" + _NATIVE_MATRIX_NUMBER + r")") * 5
    + r"\s*"
)
_NATIVE_MATRIX_OPERATION = re.compile(r"([A-Za-z]+)\s*\(([^()]*)\)")


def _canonicalize_native_uniform_matrix_transforms(
    root: ET.Element, entries: list[tuple[NativeObject, bool]],
) -> None:
    native_ids = {item.id for item, _ in entries}
    parents = {child: parent for parent in root.iter() for child in parent}
    targets = set()
    for element in root.iter():
        if (element.get("id") not in native_ids
                and element.get("data-pptx-native") is None
                and element.get("data-native-table") is None
                and element.get("data-native-chart") is None):
            continue
        node = element
        while node is not None:
            targets.add(node)
            node = parents.get(node)

    def equivalent(match: re.Match[str]) -> str:
        if match.group(1).lower() != "matrix":
            return match.group(0)
        arguments = _NATIVE_MATRIX_ARGS.fullmatch(match.group(2))
        if arguments is None:
            return match.group(0)
        a, b, c, d, e, f = (float(value) for value in arguments.groups())
        if not all(math.isfinite(value) for value in (a, b, c, d, e, f)):
            return match.group(0)
        if b != 0 or c != 0 or a != d or a <= 0:
            return match.group(0)
        return f"translate({e:.17g},{f:.17g}) scale({a:.17g})"

    for element in targets:
        transform = element.get("transform")
        if transform:
            canonical = _NATIVE_MATRIX_OPERATION.sub(equivalent, transform)
            if canonical != transform:
                element.set("transform", canonical)


def prepare_svg_pptx_inputs(paths: list[Path], pages: list[PageNativeObjects] | None, *,
                            width: float, height: float, pt_per_px: float = 0.75) -> SvgPptxInputs:
    """Annotate native objects on the page files in place (converter on sys.path).

    ``pages`` is one entry per path (``None``: no sidecars); ``width``/
    ``height`` are the canvas size the anchors must lie within.
    """
    if pages is not None and len(pages) != len(paths):
        raise ValueError("svg_native_objects_page_count_mismatch")
    inputs = SvgPptxInputs()
    for index, path in enumerate(paths):
        source = path.read_text(encoding="utf-8")
        root = ET.fromstring(source)
        page = pages[index] if pages else PageNativeObjects()
        from services.svg_source_charts import validate_source_chart_export_policy
        required = validate_source_chart_export_policy(page.source_chart_policy, page.sidecar, source)
        inputs.required_source_charts.update((index, chart_id) for chart_id in required)
        entries = [(item, False) for item in _page_entries(index, pages[index] if pages else PageNativeObjects(),
                                                            inputs)]
        if is_redesign_svg(root):
            inputs.redesign_active = True
            raster, skipped = redesign_native_objects(root)
            inputs.raster_tables_skipped += skipped
            for table_id, count in table_cell_overlays(root).items():
                inputs.raster_cell_overlays[(index, table_id)] = count
            entries.extend((item, True) for item in raster)
        from services.generation_core.drivers.svg.native_declarations import native_binding_digest
        source_svg_digest = native_binding_digest(source)
        _canonicalize_native_uniform_matrix_transforms(root, entries)
        if entries and _annotate(root, entries, index, inputs, width=width, height=height,
                                 pt_per_px=pt_per_px, source_svg_digest=source_svg_digest):
            inputs.original_sources[path] = source
            path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
        if not required.issubset({offer.id for offer in inputs.offers if offer.page == index and offer.native}):
            raise ValueError("svg_source_native_chart_unprojectable")
    return inputs


__all__ = ["NativeOffer", "SvgPptxInputs", "native_fallbacks_recorded", "prepare_svg_pptx_inputs"]
