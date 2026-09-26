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

    @property
    def native_requested(self) -> bool:
        return any(offer.native for offer in self.offers)

    def fall_back_all(self, reason: str) -> None:
        """Rewrite every annotated page to its source; every native offer falls back."""
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


def _annotate(root: ET.Element, entries: list[tuple[NativeObject, bool]], index: int,
              inputs: SvgPptxInputs, *, width: float, height: float) -> int:
    from svg_to_pptx.native_objects import validate_native_object_marker

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
        element.set("data-pptx-native", item.kind)
        element.set("data-pptx-json", json.dumps(converter_payload(item), ensure_ascii=False, separators=(",", ":")))
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
        marked += 1
    return marked


def prepare_svg_pptx_inputs(paths: list[Path], pages: list[PageNativeObjects] | None, *,
                            width: float, height: float) -> SvgPptxInputs:
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
        entries = [(item, False) for item in _page_entries(index, pages[index] if pages else PageNativeObjects(),
                                                            inputs)]
        if is_redesign_svg(root):
            inputs.redesign_active = True
            raster, skipped = redesign_native_objects(root)
            inputs.raster_tables_skipped += skipped
            for table_id, count in table_cell_overlays(root).items():
                inputs.raster_cell_overlays[(index, table_id)] = count
            entries.extend((item, True) for item in raster)
        if entries and _annotate(root, entries, index, inputs, width=width, height=height):
            inputs.original_sources[path] = source
            path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    return inputs


__all__ = ["NativeOffer", "SvgPptxInputs", "prepare_svg_pptx_inputs"]
