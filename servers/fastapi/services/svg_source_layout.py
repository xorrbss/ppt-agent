"""Layout-only plans and deterministic assembly of immutable source content.

The receipt proves structural preservation; Chromium owns actual glyph/visibility
verification. Unsupported source appearance is rejected before provider dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from collections import Counter
import math
import re
from threading import RLock
import weakref
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from services.source_content.contracts import SourceContentManifest
from services.source_content.digests import digest_json
from services.source_content.pptx_custom_geometry import validate_custom_geometry, flatten_custom_geometry
from services.svg_source_preset_shapes import supports_source_preset, source_preset_paths
from services.svg_contract import SVG_NS, source_digest, validate_svg
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX


class _PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SvgLayoutPlacement(_PlanModel):
    block_id: str
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    font_size: float = Field(default=20, gt=0)
    font_family: str = "Pretendard"
    font_weight: str = "normal"
    fill: str = "#172033"
    background: str = "#FFFFFF"
    stroke: str = "#CBD5E1"


class SvgSourcePlacement(SvgLayoutPlacement):
    """Internal resolved placement; never exposed as a provider schema."""
    line_ends: tuple[int, ...] = ()
    cell_line_ends: dict[str, tuple[int, ...]] = Field(default_factory=dict)


class SvgLayoutPlan(_PlanModel):
    schema_version: str = "svg-source-layout/v2"
    source_unit_id: str
    page_digest: str
    width: int = Field(default=DEFAULT_CANVAS_WIDTH_PX, gt=0)
    height: int = Field(default=DEFAULT_CANVAS_HEIGHT_PX, gt=0)
    background: str = "#FFFFFF"
    template_digest: str = ""
    layout_id: str = ""
    placements: tuple[SvgLayoutPlacement, ...]


class SvgResolvedLayoutPlan(SvgLayoutPlan):
    schema_version: str = "svg-source-layout/v1"
    placements: tuple[SvgSourcePlacement, ...]


@dataclass(frozen=True)
class SvgSourceAssembly:
    svg_content: str
    receipt: dict
    plan: SvgLayoutPlan


def page_digest(manifest, page_index):
    return digest_json(dict(manifest_digest=manifest.manifest_digest,
        page=manifest.units[page_index].model_dump(mode="json", by_alias=True)))


def _cells(block):
    rows = block.payload.get("rows")
    if not isinstance(rows, list) or not rows or not rows[0]:
        raise ValueError("svg_source_table_structure_unsupported")
    columns = len(rows[0])
    covered = set()
    result = []
    for r, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != columns:
            raise ValueError("svg_source_table_structure_unsupported")
        for c, cell in enumerate(row):
            if not isinstance(cell, dict) or not isinstance(cell.get("text"), str):
                raise ValueError("svg_source_table_cell_invalid")
            if (r, c) in covered:
                if cell["text"]:
                    raise ValueError("svg_source_merged_cell_content_unsupported")
                continue
            if cell.get("mergeCovered") or cell.get("hMerge") or cell.get("vMerge"):
                raise ValueError("svg_source_table_merge_invalid")
            rs, cs = cell.get("rowSpan", 1), cell.get("gridSpan", 1)
            if type(rs) is not int or type(cs) is not int or min(rs, cs) < 1 or r + rs > len(rows) or c + cs > columns:
                raise ValueError("svg_source_table_merge_invalid")
            positions = {(rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)}
            if covered.intersection(positions):
                raise ValueError("svg_source_table_merge_invalid")
            covered.update(positions - {(r, c)})
            result.append((r, c, rs, cs, cell))
    return rows, result


class _FrozenSourceDict(dict):
    def _reject(self, *args, **kwargs):
        raise TypeError("svg_source_manifest_immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _reject

    def __copy__(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return deepcopy(dict(self), memo)

    def __reduce__(self):
        return dict, (dict(self),)


class _FrozenSourceList(list):
    def _reject(self, *args, **kwargs):
        raise TypeError("svg_source_manifest_immutable")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = _reject
    __iadd__ = __imul__ = _reject

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        return deepcopy(list(self), memo)

    def __reduce__(self):
        return list, (list(self),)


def _freeze_source_value(value):
    if isinstance(value, (_FrozenSourceDict, _FrozenSourceList)):
        return value
    if isinstance(value, dict):
        return _FrozenSourceDict((key, _freeze_source_value(item)) for key, item in value.items())
    if isinstance(value, list):
        return _FrozenSourceList(_freeze_source_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_source_value(item) for item in value)
    return value


_SOURCE_PAGES_MEMO = {}
_SOURCE_PAGES_LOCK = RLock()


def _evict_source_pages(reference, identity):
    with _SOURCE_PAGES_LOCK:
        entry = _SOURCE_PAGES_MEMO.get(identity)
        if entry is not None and entry[0] is reference:
            del _SOURCE_PAGES_MEMO[identity]


def source_pages(manifest: SourceContentManifest, *, chart_contract=None):
    """Memoize validation per live manifest identity and exact chart contract.

    Pydantic's frozen source models are shallow: payload/projection dicts and
    lists remain mutable. After successful validation, detach and seal those
    containers before memoizing. JSON serialization and isinstance checks stay
    compatible; deepcopy yields editable copies. A stored digest alone cannot
    detect in-place payload edits. The weak reference prevents identity reuse
    and releases entries even when a caller retains the returned page tuple.
    """
    key = _exact_json_key(chart_contract)
    with _SOURCE_PAGES_LOCK:
        identity = id(manifest)
        entry = _SOURCE_PAGES_MEMO.get(identity)
        if key is not None and entry is not None and entry[0]() is manifest and key in entry[1]:
            return entry[1][key]
    # Whole-manifest validation can take seconds. Unrelated identities must
    # never wait on that work under the global memo lock.
    pages = _validate_source_pages(manifest, chart_contract=chart_contract)
    if key is None:
        return pages
    with _SOURCE_PAGES_LOCK:
        entry = _SOURCE_PAGES_MEMO.get(identity)
        if entry is not None and entry[0]() is manifest and key in entry[1]:
            return entry[1][key]
        for page in pages:
            for block in page.blocks:
                object.__setattr__(block, "payload", _freeze_source_value(block.payload))
                object.__setattr__(block, "comparison_projection", _freeze_source_value(block.comparison_projection))
        if entry is None or entry[0]() is not manifest:
            reference = weakref.ref(manifest, lambda reference: _evict_source_pages(reference, identity))
            entry = (reference, {})
            _SOURCE_PAGES_MEMO[identity] = entry
        entry[1][key] = pages
        return pages


def _validate_source_pages(manifest: SourceContentManifest, *, chart_contract=None):
    from services.svg_source_charts import validate_source_chart_contract, source_chart_payload
    validate_source_chart_contract(chart_contract)
    sealed = manifest.model_dump(mode="json", by_alias=True)
    sealed.pop("manifestDigest", None)
    if digest_json(sealed) != manifest.manifest_digest:
        raise ValueError("svg_source_manifest_digest_mismatch")
    inventory = manifest.object_inventory
    if (inventory.unsupported_content_object_ids or inventory.unclassified_object_ids
            or inventory.discovered_object_count != inventory.classified_object_count
            or any(item.content_bearing and not item.supported for item in manifest.inventory_objects)):
        raise ValueError("svg_source_unsupported_inventory")
    if not manifest.units or manifest.counts.source_units != len(manifest.units):
        raise ValueError("svg_source_page_count_mismatch")
    if manifest.counts.picture_bullet_count:
        raise ValueError("svg_source_picture_bullet_unsupported")
    ids, pages = set(), set()
    for index, page in enumerate(manifest.units):
        if page.source_index != index or page.source_unit_id in pages:
            raise ValueError("svg_source_page_order_mismatch")
        pages.add(page.source_unit_id)
        if not any((b.kind == "text" and str(b.payload.get("text", "")).strip()) or
                   (b.kind == "table" and any(str(c.get("text", "")).strip()
                       for row in b.payload.get("rows", []) for c in row)) or
                   (b.kind == "chart" and chart_contract is not None) for b in page.blocks):
            raise ValueError(f"svg_source_image_only_page_not_supported:page={index + 1}")
        for block in page.blocks:
            if block.source_block_id in ids:
                raise ValueError("svg_source_duplicate_block_id")
            ids.add(block.source_block_id)
            if digest_json(block.payload) != block.content_digest:
                raise ValueError("svg_source_block_digest_mismatch")
            if block.kind == "source_note":
                continue
            if block.kind == "chart":
                source_chart_payload(block, chart_contract)
            if block.kind == "text" and not isinstance(block.payload.get("text"), str):
                raise ValueError("svg_source_text_invalid")
            if block.kind == "table":
                _cells(block)
            if block.kind == "shape" and block.semantic_role == "connector" and block.payload.get("preset") not in {
                                                                                                  "line",
                                                                                                      "straightConnector1"}:
                raise ValueError(
                    f"svg_source_connector_geometry_unsupported:page={index + 1}:block={block.source_block_id}")
            if block.kind == "shape" and block.payload.get(
                "preset") not in {"rect", "roundRect", "ellipse", "line", "straightConnector1",
                                  "parallelogram", "diamond"}:
                preset = block.payload.get("preset")
                if preset is not None:
                    if not supports_source_preset(preset):
                        raise ValueError(f"svg_source_shape_unsupported:page={index + 1}:block={block.source_block_id}")
                elif block.payload.get("customGeometry"):
                    validate_custom_geometry(block.payload["customGeometry"])
                else:
                    raise ValueError(f"svg_source_shape_unsupported:page={index + 1}:block={block.source_block_id}")
            geometry = block.payload.get("geometry") or {}
            transform_keys = (
    "rotationDegrees",
    ) if block.kind == "shape" and block.semantic_role == "connector" else (
        "rotationDegrees",
        "flipH",
         "flipV")
            if block.kind in {"image", "shape"} and any(geometry.get(key) for key in transform_keys):
                raise ValueError(f"svg_source_transform_unsupported:page={index + 1}:block={block.source_block_id}")
            if block.kind == "image" and (any((block.payload.get("crop") or {}).values())
                                          or block.payload.get("colorTransforms")):
                raise ValueError("svg_source_image_appearance_unsupported")
    return manifest.units


def _exact_json_key(value):
    """Order- and type-exact JSON text of a pure JSON value; None when not pure JSON."""
    import json
    import math
    pending = [value]
    while pending:
        item = pending.pop()
        kind = type(item)
        if kind is dict:
            if any(type(key) is not str for key in item):
                return None
            pending.extend(item.values())
        elif kind is list:
            pending.extend(item)
        elif kind is float:
            if not math.isfinite(item):
                return None
        elif kind not in {str, int, bool, type(None)}:
            return None
    try:
        return json.dumps(value, sort_keys=False, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None


class ValidatedSourcePages:
    """Compatibility delegate to the process-wide, weak-identity source memo."""

    def __init__(self, manifest):
        self._manifest = manifest

    def pages_for(self, chart_contract):
        return source_pages(self._manifest, chart_contract=chart_contract)

    def verify_receipt(self, svg, receipt, page_index):
        return verify_source_receipt(svg, receipt, self._manifest, page_index,
                                     _validated_pages=self._receipt_pages(receipt))

    def _receipt_pages(self, receipt):
        # The contract the leaf verification would use: a composed receipt verifies its
        # body receipt. Anything malformed keeps the unmemoized path and its own errors.
        if not isinstance(receipt, dict):
            return None
        leaf = receipt
        if receipt.get("common_header_composition"):
            envelope = receipt["common_header_composition"]
            leaf = envelope.get("body_receipt") if isinstance(envelope, dict) else None
            if not isinstance(leaf, dict):
                return None
        return self.pages_for(leaf.get("source_chart_contract"))


def _number(value):
    return f"{value:.6f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def _node(parent, name, **attrs):
    return ET.SubElement(parent, f"{{{SVG_NS}}}{name}", {k.replace("_", "-"): _number(v) for k, v in attrs.items()})


def _ranges(text, ends):
    # Newline characters stay in the bound source range. SVG whitespace is a
    # display concern only; no normalization is applied to preservation data.
    if not ends:
        offset = 0
        ends = []
        for line in text.splitlines(keepends=True):
            offset += len(line)
            ends.append(offset)
        if not ends:
            ends = [len(text)]
    cursor = 0
    result = []
    for end in ends:
        if type(end) is not int or not cursor <= end <= len(text) or (end == cursor and text):
            raise ValueError("svg_source_text_range_invalid")
        result.append((cursor, end))
        cursor = end
    if cursor != len(text):
        raise ValueError("svg_source_text_range_incomplete")
    return result


def assemble_source_svg(manifest, page_index, plan, *, source_images, image_slots,
                        min_font_size_px, style_digest, template_snapshot=None, _validated_pages=None):
    pages = source_pages(manifest) if _validated_pages is None else _validated_pages
    if type(page_index) is not int or not 0 <= page_index < len(pages):
        raise ValueError("svg_source_page_index_invalid")
    page = pages[page_index]
    plan = SvgResolvedLayoutPlan.model_validate(plan)
    if (plan.schema_version not in {"svg-source-layout/v1",
        "svg-source-layout/v2"} or plan.source_unit_id != page.source_unit_id
            or plan.page_digest != page_digest(manifest, page_index)):
        raise ValueError("svg_source_plan_input_mismatch")
    if not math.isfinite(min_font_size_px) or min_font_size_px <= 0:
        raise ValueError("svg_source_font_policy_required")
    blocks = {b.source_block_id: b for b in page.blocks if b.kind != "source_note"}
    placed = [p.block_id for p in plan.placements]
    if len(placed) != len(set(placed)) or set(placed) != set(blocks):
        raise ValueError("svg_source_plan_coverage_mismatch")
    image_ids = {key for key, block in blocks.items() if block.kind == "image"}
    if set(image_slots) != image_ids or len(set(image_slots.values())) != len(
        image_slots) or set(source_images) != set(image_slots.values()):
        raise ValueError("svg_source_image_occurrence_mismatch")
    template_evidence = None
    if template_snapshot is not None:
        from services.svg_source_template_layout import validate_template_plan
        template_evidence = validate_template_plan(plan, manifest, page_index, template_snapshot)
    elif plan.template_digest:
        raise ValueError("svg_source_template_snapshot_required")
    root = ET.Element(
    f"{{{SVG_NS}}}svg", dict(
        width=str(
            plan.width), height=str(
                plan.height), viewBox=f"0 0 {plan.width} {plan.height}"))
    _node(root, "rect", x=0, y=0, width=plan.width, height=plan.height, fill=plan.background)
    for decoration in (template_snapshot or {}).get("decorations", []):
        attrs = {k: v for k, v in decoration.items() if k not in {"id", "kind"}}
        active_layout = next((x for x in template_snapshot.get("layouts", []) if x["id"] == plan.layout_id), {})
        signature_y = active_layout.get("allowed_variations",{}).get("signature_rule_y")
        if decoration.get("id") == "template-editorial-rule" and signature_y is not None:
            attrs["y"] = signature_y
        if decoration["kind"] not in {"rect", "line"}:
            raise ValueError("svg_source_template_decoration_unsupported")
        _node(root, decoration["kind"], **attrs)
    bindings = []
    native_tables = []
    placements = {p.block_id: p for p in plan.placements}
    content_boxes = [p for p in plan.placements if blocks[p.block_id].kind != "shape"]
    if template_snapshot is not None:
        from services.svg_source_template_layout import occupied_boxes, source_group_membership, required_content_gap
        content_boxes = occupied_boxes(plan, manifest, page_index)
        layout_rule = (dict(min_gap=0) if template_snapshot.get("composition_mode") == "free-svg"
                       else next(x for x in template_snapshot["layouts"] if x["id"] == plan.layout_id))
        group_membership = source_group_membership(
    manifest,page_index,parallel_only=layout_rule.get(
        "allowed_variations",{}).get(
            "requires_same_source_parallel_group",False))
    for reservation in (template_snapshot or {}).get("system_furniture", {}).get("reservations", []):
        visibility = reservation.get("visibility")
        if (visibility == "hidden" or (visibility == "exceptCover" and page_index == 0)
                or (visibility == "coverOnly" and page_index != 0)):
            continue
        x, y, width, height = (reservation[key] for key in ("x", "y", "width", "height"))
        for content in content_boxes:
            if (min(content.x + content.width, x + width) > max(content.x, x)
                    and min(content.y + content.height, y + height) > max(content.y, y)):
                raise ValueError("svg_source_master_furniture_collision")
    for i, a in enumerate(content_boxes):
        for b in content_boxes[i + 1:]:
            if template_snapshot is not None and a.block_id == b.block_id:
                continue
            if min(a.x + a.width, b.x + b.width) > max(a.x, b.x) \
                and min(a.y + a.height, b.y + b.height) > max(a.y, b.y):
                raise ValueError("svg_source_content_boxes_overlap")
            if template_snapshot is not None:
                x_overlap = min(a.x+a.width,b.x+b.width) > max(a.x,b.x)
                y_overlap = min(a.y+a.height,b.y+b.height) > max(a.y,b.y)
                distance = (max(a.y,b.y)-min(a.y+a.height,b.y+b.height)) if x_overlap else (
                    max(a.x,b.x)-min(a.x+a.width,b.x+b.width) if y_overlap else float("inf"))
                if distance + .001 < required_content_gap(a.block_id,b.block_id,layout_rule,group_membership):
                    raise ValueError("svg_source_content_boxes_overlap:template_min_gap")

    def text_nodes(text, placement, source_id, kind, *, x, y, width, height, ends=(), metadata=None, parent=None):
        ranges = _ranges(text, ends)
        ascent, advance = 1, 1.2
        required_height = placement.font_size * (1.2 * len(ranges) + .2)
        if template_snapshot is not None:
            from services.svg_source_template_layout import _font, text_metrics, measured_text_height
            font = _font(placement)
            ascent, advance = text_metrics(font)
            required_height = measured_text_height(len(ranges),placement.font_size,font)
        if placement.font_size < min_font_size_px or required_height > height + .000001:
            raise ValueError("svg_source_text_box_unfit")
        for line_index, (start, end) in enumerate(ranges):
            element_id = f"source-element-{len(bindings)}"
            node = _node(parent if parent is not None else root, "text", id=element_id, x=x,
                y=y + placement.font_size * (ascent + advance * line_index),
                         font_family=placement.font_family, font_size=placement.font_size,
                         font_weight=placement.font_weight, fill=placement.fill)
            node.text = text[start:end]
            bindings.append(dict(source_id=source_id, kind=kind, element_id=element_id,
                text=text[start:end], start=start, end=end, **(metadata or {})))

    # Paint source furniture first; it cannot conceal later text or images.
    ordered = sorted(blocks.values(), key=lambda b: (b.kind != "shape", b.order))
    for block in ordered:
        p = placements[block.source_block_id]
        if p.x + p.width > plan.width or p.y + p.height > plan.height:
            raise ValueError("svg_source_placement_off_canvas")
        if p.fill in {"none", p.background, plan.background} and block.kind in {"text", "table"}:
            raise ValueError("svg_source_invisible_text")
        if block.kind != "table" and p.cell_line_ends:
            raise ValueError("svg_source_unknown_cell_layout")
        if block.kind not in {"text"} and p.line_ends:
            raise ValueError("svg_source_unknown_text_layout")
        if block.kind == "text":
            text_nodes(block.payload["text"], p, block.source_block_id, "text", x=p.x, y=p.y,
                width=p.width, height=p.height, ends=p.line_ends)
        elif block.kind == "table":
            rows, cells = _cells(block)
            table_parent = root
            if (template_snapshot or {}).get("composition_mode") == "free-svg":
                from services.svg_source_native_tables import source_table_group_id
                group_id = source_table_group_id(block.source_block_id)
                table_parent = _node(root, "g", id=group_id)
                native_tables.append(dict(block_id=block.source_block_id, element_id=group_id,
                    frame=dict(x=p.x, y=p.y, width=p.width, height=p.height)))
            known_cells = {cell.get("sourceCellId", f"{block.source_block_id}:row:{r}:column:{c}")
                                    for r, c, rs, cs, cell in cells}
            if set(p.cell_line_ends) - known_cells:
                raise ValueError("svg_source_unknown_cell_layout")
            cw, rh = p.width / len(rows[0]), p.height / len(rows)
            for r, c, rs, cs, cell in cells:
                cell_id = cell.get("sourceCellId", f"{block.source_block_id}:row:{r}:column:{c}")
                x, y, w, h = p.x + c * cw, p.y + r * rh, cs * cw, rs * rh
                cell_style = (template_snapshot or {}).get("table", {})
                header = r == 0 and bool(cell_style)
                cell_p = p.model_copy(
    update={
        "fill": cell_style["header_text"],
        "font_weight": str(
            cell_style["header_weight"])}) if header else p
                inset_x, inset_y = cell_style.get("cell_padding", 3), cell_style.get("cell_padding", 2)
                _node(
    table_parent,
    "rect",
    x=x,
    y=y,
    width=w,
    height=h,
    fill=cell_style["header_fill"] if header else p.background,
    stroke=p.stroke,
     stroke_width=1)
                text_nodes(cell["text"], cell_p, cell_id, "table_cell", x=x + inset_x,
                    y=y + inset_y, width=w - 2*inset_x, height=h - 2*inset_y,
                    ends=p.cell_line_ends.get(cell_id, ()), metadata=dict(block_id=
                        block.source_block_id, row=r, column=c, row_span=rs, column_span=cs), parent=table_parent)
        elif block.kind == "image":
            if not p.width.is_integer() or not p.height.is_integer():
                raise ValueError("svg_source_image_integer_size_required")
            element_id = f"source-element-{len(bindings)}"
            slot = image_slots[block.source_block_id]
            _node(
    root, "image", id=element_id, x=p.x, y=p.y, width=int(
        p.width), height=int(
            p.height), href=slot, preserveAspectRatio="xMidYMid meet")
            bindings.append(dict(source_id=block.source_block_id, kind="image", element_id=element_id,
                slot_id=slot, asset_digest=block.payload.get("contentDigest")))
        elif block.kind == "shape":
            element_id = f"source-element-{len(bindings)}"
            preset = block.payload.get("preset")
            custom = block.payload.get("customGeometry")
            if custom:
                path = flatten_custom_geometry(custom, x=p.x, y=p.y, width=p.width, height=p.height)
                _node(root, "path", id=element_id, d=path,
                    fill=p.background if custom["fill"] == "norm" else "none",
                    stroke=p.stroke if custom["stroke"] else "none")
            elif preset == "ellipse":
                _node(
    root,
    "ellipse",
    id=element_id,
    cx=p.x+p.width/2,
    cy=p.y+p.height/2,
    rx=p.width/2,
    ry=p.height/2,
    fill=p.background,
     stroke=p.stroke)
            elif preset == "parallelogram":
                skew = min(p.width / 4, p.height / 2)
                points = " ".join(f"{_number(x)},{_number(y)}" for x, y in (
                    (p.x + skew, p.y), (p.x + p.width, p.y),
                    (p.x + p.width - skew, p.y + p.height), (p.x, p.y + p.height)))
                _node(root, "polygon", id=element_id, points=points, fill=p.background, stroke=p.stroke)
            elif preset == "diamond":
                points = " ".join(f"{_number(x)},{_number(y)}" for x, y in (
                    (p.x + p.width / 2, p.y), (p.x + p.width, p.y + p.height / 2),
                    (p.x + p.width / 2, p.y + p.height), (p.x, p.y + p.height / 2)))
                _node(root, "polygon", id=element_id, points=points, fill=p.background, stroke=p.stroke)
            elif preset == "line" or block.semantic_role == "connector":
                geometry = block.payload.get("geometry") or {}
                x1, x2 = (p.x + p.width, p.x) if geometry.get("flipH") else (p.x, p.x + p.width)
                y1, y2 = (p.y + p.height, p.y) if geometry.get("flipV") else (p.y, p.y + p.height)
                _node(root, "line", id=element_id, x1=x1, y1=y1, x2=x2, y2=y2, stroke=p.stroke, stroke_width=1)
            elif preset not in {"rect", "roundRect", "straightConnector1"}:
                group = _node(root, "g", id=element_id)
                for path in source_preset_paths(preset, x=p.x, y=p.y, width=p.width, height=p.height,
                                               background=p.background, stroke=p.stroke):
                    _node(group, "path", **path)
            else:
                radius = (template_snapshot or {}).get("shape", {}).get("radius", 8) if preset == "roundRect" else 0
                _node(
    root,
    "rect",
    id=element_id,
    x=p.x,
    y=p.y,
    width=p.width,
    height=p.height,
    rx=radius,
    fill=p.background,
     stroke=p.stroke)
            bindings.append(dict(source_id=block.source_block_id, kind="shape", element_id=element_id,
                **({"geometry_digest": digest_json(custom)} if custom else {})))
    svg = ET.tostring(root, encoding="unicode")
    furniture_receipt = None
    furniture_images = None
    if (template_snapshot or {}).get("system_furniture"):
        from services.svg_master_furniture import stamp_master_furniture, furniture_image_manifest

        furniture_snapshot = template_snapshot
        svg = stamp_master_furniture(svg, furniture_snapshot, index=page_index, n=len(pages))
        stamped = ET.fromstring(svg)
        group = next((node for node in stamped if node.get("id") == "svg-system-master-furniture"), None)
        if group is not None:
            visible_images = {node.get("id") for node in group.iter() if node.tag == f"{{{SVG_NS}}}image"}
            furniture_images = {key: value for key, value in furniture_image_manifest(furniture_snapshot).items()
                                if key in visible_images}
            furniture_receipt = dict(digest=template_snapshot["system_furniture"]["digest"],
                element_ids=[node.get("id") for node in group.iter() if node.get("id")],
                images=furniture_images)
    validate_svg(
    svg,
    width=plan.width,
    height=plan.height,
    source_manifest=source_images,
     allow_system_page_number=False, trusted_furniture_images=furniture_images)
    receipt = dict(contract_version="svg-source-preservation/v1", manifest_digest=manifest.manifest_digest,
        source_digest=manifest.source_digest, source_page_id=page.source_unit_id, source_page_index=page_index,
        page_count=len(pages), page_digest=plan.page_digest, plan_digest=digest_json(plan.model_dump(mode="json")),
        svg_digest=source_digest(svg), style_digest=style_digest, canvas=dict(width=plan.width, height=plan.height),
        font_policy=dict(minimum_px=min_font_size_px, unit="CSS px", policy="existing-svg-generation-policy"),
        bindings=bindings, checks=dict(source_ids_exact=True, text_exact=True,
                                       table_structure_exact=True, image_occurrences_exact=True),
        rendering_verified=False)
    if furniture_receipt is not None:
        receipt["system_furniture"] = furniture_receipt
    if native_tables:
        receipt["native_tables"] = native_tables
    if template_evidence is not None:
        template_evidence["checks"]["measured_ink_collision"] = True
        template_evidence["checks"]["minimum_gap"] = True
        receipt.update(template_application=template_evidence,
            template_digest=plan.template_digest, layout_id=plan.layout_id,
            layout_schema_version=plan.schema_version)
    verify_source_receipt(svg, receipt, manifest, page_index, _validated_pages=pages)
    return SvgSourceAssembly(svg, receipt, plan)


def restore_source_linefeeds(svg, *, facts, manifest_digest, source_page_id, raw_output_digest):
    """Restore only provably omitted LF characters in independent source text owners."""
    if not isinstance(svg, str) or re.search(r"<!|<\?", svg):
        return svg, None
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg, None
    nodes = list(root.iter())
    counts = Counter(node.get("id") for node in nodes if node.get("id"))
    if any(value != 1 for value in counts.values()):
        return svg, None
    by_id = {node.get("id"): node for node in nodes if node.get("id")}
    required, records = [], []
    for fact in facts:
        required.append(fact["element_id"])
        if fact["kind"] == "text":
            records.append((fact["element_id"], fact["source_id"], fact["data"]["text"]))
        for cell in fact.get("cells", []):
            required.append(cell["element_id"])
            source_cell = fact["data"]["rows"][cell["row"]][cell["column"]]
            source_id = source_cell.get("sourceCellId",
                f"{fact['source_id']}:row:{cell['row']}:column:{cell['column']}")
            records.append((cell["element_id"], source_id, cell["text"]))
    if len(required) != len(set(required)) or any(identity not in by_id for identity in required):
        return svg, None
    chart_children = {id(node) for fact in facts if fact["kind"] == "chart"
                      for node in by_id[fact["element_id"]].iter()}
    owners = {identity for identity, _source, _text in records}
    if any(node.tag == f"{{{SVG_NS}}}text" and node.get("id") not in owners
           and id(node) not in chart_children for node in nodes):
        return svg, None

    def lf_positions(value):
        gaps, plain, offset = {}, [], 0
        for index, character in enumerate(value):
            if character == "\n":
                gaps.setdefault(offset, []).append(index)
            else:
                plain.append(character)
                offset += 1
        return "".join(plain), gaps

    pending, evidence = [], []
    for identity, source_id, expected in records:
        node = by_id[identity]
        if node.tag != f"{{{SVG_NS}}}text" or any(
                child.tag != f"{{{SVG_NS}}}tspan" for child in node.iter() if child is not node):
            return svg, None
        slots = [(node, "text")]
        for child in node:
            # Source text has immediate tspan children; overlapping owners are invalid.
            if len(child) or child.get("id") in owners:
                return svg, None
            slots.extend(((child, "text"), (child, "tail")))
        actual = "".join(getattr(element, field) or "" for element, field in slots)
        if actual == expected:
            continue
        plain, expected_gaps = lf_positions(expected)
        actual_plain, actual_gaps = lf_positions(actual)
        if plain != actual_plain or any(
                len(positions) > len(expected_gaps.get(gap, [])) for gap, positions in actual_gaps.items()):
            return svg, None
        non_lf_indices = [index for index, character in enumerate(actual) if character != "\n"]
        insertions, restored_positions = {}, []
        for gap, positions in expected_gaps.items():
            present = len(actual_gaps.get(gap, []))
            omitted = positions[present:]
            if omitted:
                boundary = non_lf_indices[gap] if gap < len(non_lf_indices) else len(actual)
                insertions[boundary] = len(omitted)
                restored_positions.extend(omitted)
        if not restored_positions:
            return svg, None
        cursor, consumed, updates = 0, set(), []
        for element, field in slots:
            value = getattr(element, field) or ""
            local = [(position - cursor, amount) for position, amount in insertions.items()
                     if cursor <= position <= cursor + len(value) and position not in consumed]
            for position, amount in sorted(local, reverse=True):
                value = value[:position] + "\n" * amount + value[position:]
                consumed.add(cursor + position)
            updates.append((element, field, value))
            cursor += len(getattr(element, field) or "")
        if consumed != set(insertions) or "".join(value for _node, _field, value in updates) != expected:
            return svg, None
        pending.extend(updates)
        evidence.append(dict(source_id=source_id, element_id=identity,
            source_text_digest=source_digest(expected), positions=sorted(restored_positions),
            restored_lf_count=len(restored_positions)))
    if not evidence:
        return svg, None
    if not isinstance(raw_output_digest, str) or not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", raw_output_digest):
        raise ValueError("svg_source_lf_provenance_missing")
    for node, field, value in pending:
        if (getattr(node, field) or "") != value:
            setattr(node, field, value)
    restored = ET.tostring(root, encoding="unicode")
    roundtrip = {node.get("id"): node for node in ET.fromstring(restored).iter() if node.get("id")}
    if any("".join(roundtrip[identity].itertext()) != expected for identity, _source, expected in records):
        return svg, None
    return restored, dict(schema="svg-source-linefeed-restoration/v1", manifest_digest=manifest_digest,
        source_page_id=source_page_id, raw_output_digest="sha256:" + raw_output_digest.removeprefix("sha256:"),
        input_svg_digest=source_digest(svg), restored_svg_digest=source_digest(restored),
        restored_lf_count=sum(item["restored_lf_count"] for item in evidence), bindings=evidence)


def restore_source_empty_table_texts(svg, *, page, facts, manifest_digest,
                                     source_page_digest, raw_output_digest):
    import hashlib
    from services.svg_source_native_tables import _authored_grid, source_table_group_id

    ns = "{" + SVG_NS + "}"
    if not isinstance(svg, str) or re.search(r"<!|<\?", svg):
        return svg, None
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg, None
    nodes = list(root.iter())
    counts = Counter(node.get("id") for node in nodes if node.get("id"))
    if any(count != 1 for count in counts.values()):
        return svg, None
    by_id = {node.get("id"): node for node in nodes if node.get("id")}
    fact_counts = Counter(fact["source_id"] for fact in facts)
    pending = []
    all_cell_ids = set()
    for block in page.blocks:
        if block.kind != "table":
            continue
        if fact_counts[block.source_block_id] != 1:
            return svg, None
        fact = next(fact for fact in facts if fact["source_id"] == block.source_block_id)
        group_id = source_table_group_id(block.source_block_id)
        if (fact.get("kind") != "table" or fact.get("element_id") != group_id
                or fact.get("data") != block.payload):
            return svg, None
        group = by_id.get(group_id)
        if (group is None or group not in list(root) or group.tag != ns + "g"
                or group.attrib != {"id": group_id}
                or (group.text or "").strip() or (group.tail or "").strip()
                or any(node.tag not in {ns + "rect", ns + "text"} for node in group)):
            return svg, None
        rows, cells = _cells(block)
        cell_facts = fact.get("cells", [])
        if len(cells) != len(cell_facts):
            return svg, None
        rects = [node for node in group if node.tag == ns + "rect"]
        try:
            xs, ys = _authored_grid(rows, cells, rects)
        except (ValueError, TypeError, KeyError):
            return svg, None
        expected_ids = set()
        table_pending = []
        for (row, column, rs, cs, cell), cell_fact in zip(cells, cell_facts, strict=True):
            source_id = cell.get("sourceCellId", f"{block.source_block_id}:row:{row}:column:{column}")
            identity = "source-fact-" + hashlib.sha256(source_id.encode()).hexdigest()[:20]
            if (identity in all_cell_ids or cell_fact != dict(row=row, column=column,
                    row_span=rs, column_span=cs, text=cell["text"], element_id=identity)):
                return svg, None
            all_cell_ids.add(identity)
            expected_ids.add(identity)
            node = by_id.get(identity)
            if node is not None:
                if (node not in list(group) or node.tag != ns + "text"
                        or "".join(node.itertext()) != cell["text"]):
                    return svg, None
                continue
            if cell["text"] != "":
                return svg, None
            style_keys = ("font-family", "font-size", "fill")
            style_node = next((candidate for candidate in group
                if candidate.tag == ns + "text" and candidate.get("id")
                and all(key in candidate.attrib for key in style_keys)
                and candidate.get("fill") != "none"), None)
            if style_node is None:
                return svg, None
            restored = ET.Element(ns + "text", {"id": identity,
                "x": format(xs[column], ".17g"), "y": format(ys[row], ".17g"),
                **{key: style_node.get(key) for key in style_keys}})
            table_pending.append((group, restored, dict(block_id=block.source_block_id,
                source_id=source_id, element_id=identity, row=row, column=column,
                row_span=rs, column_span=cs, exact_empty=True,
                style_source_element_id=style_node.get("id"))))
        actual_ids = [node.get("id") for node in group if node.get("id")]
        pending_ids = {node.get("id") for _group, node, _record in table_pending}
        if (set(actual_ids) != expected_ids - pending_ids
                or any(node.tag != ns + "text" for node in group if node.get("id"))):
            return svg, None
        pending.extend(table_pending)
    if not pending:
        return svg, None
    if not all(isinstance(value, str) and value for value in
               (manifest_digest, source_page_digest, raw_output_digest)):
        raise ValueError("svg_source_empty_table_text_provenance_missing")
    original_tree = ET.tostring(root, encoding="unicode")
    for group, node, _record in pending:
        group.append(node)
    canonical = ET.tostring(root, encoding="unicode")
    # Prove every existing node, attribute, character and sibling order survived.
    for group, node, _record in pending:
        group.remove(node)
    if ET.tostring(root, encoding="unicode") != original_tree:
        raise ValueError("svg_source_empty_table_text_existing_paint_changed")
    return canonical, dict(schema="svg-source-empty-table-text-restoration/v1",
        manifest_digest=manifest_digest, source_page_digest=source_page_digest,
        raw_output_digest="sha256:" + raw_output_digest.removeprefix("sha256:"),
        input_svg_digest=source_digest(svg), restored_svg_digest=source_digest(canonical),
        original_tree_digest=source_digest(original_tree),
        empty_texts=[record for _group, _node, record in pending],
        existing_tree_preserved=True, added_visible_characters=0)


def bind_whole_page_source_svg(svg, manifest, page_index, *, facts, width, height,
                               style_snapshot, minimum_font_px, _validated_pages=None,
                               native_declarations=None, restore_source_lf=False, raw_output_digest=None):
    """Bind a freely authored page to the existing exact source verifier."""
    chart_contract = style_snapshot.get("source_chart_contract")
    pages = source_pages(manifest, chart_contract=chart_contract) if _validated_pages is None else _validated_pages
    page = pages[page_index]
    restoration = empty_restoration = None
    if restore_source_lf:
        svg, empty_restoration = restore_source_empty_table_texts(
            svg, page=page, facts=facts, manifest_digest=manifest.manifest_digest,
            source_page_digest=page_digest(manifest, page_index), raw_output_digest=raw_output_digest)
        svg, restoration = restore_source_linefeeds(svg, facts=facts, manifest_digest=manifest.manifest_digest,
            source_page_id=page.source_unit_id, raw_output_digest=raw_output_digest)
    root = ET.fromstring(svg)
    elements = {node.get("id"): node for node in root.iter() if node.get("id")}
    bindings, native_tables, source_native_charts, required_ids = [], [], [], set()
    source_chart_ids = {fact["element_id"] for fact in facts if fact["kind"] == "chart"}
    native_groups = [{"element_id": item["id"], "kind": "chart"}
                     for item in (native_declarations or {}).get("objects", [])
                     if item.get("kind") == "chart" and item["id"] not in source_chart_ids]
    required_ids.update(item["element_id"] for item in native_groups)
    by_source = {fact["source_id"]: fact for fact in facts}
    for block in page.blocks:
        if block.kind == "source_note":
            continue
        fact = by_source[block.source_block_id]
        if block.kind == "chart":
            from services.svg_source_charts import source_chart_record
            declarations = [item for item in (native_declarations or {}).get("objects", [])
                            if item.get("id") == fact["element_id"]]
            if len(declarations) != 1:
                raise ValueError("svg_source_chart_declaration_missing")
            source_native_charts.append(source_chart_record(
                svg, block, fact["element_id"], declarations[0], chart_contract))
            group = elements.get(fact["element_id"])
            for ordinal, node in enumerate(group.iter()):
                if node is not group:
                    node.set("id", f"{fact['element_id']}-part-{ordinal}")
                required_ids.add(node.get("id"))
        if block.kind == "table":
            group_id = fact["element_id"]
            group = elements.get(group_id)
            if group is None or group not in list(root):
                raise ValueError("svg_source_native_table_group_invalid")
            rects = [node for node in group if node.tag == f"{{{SVG_NS}}}rect"]
            try:
                bounds = [(float(node.get("x")), float(node.get("y")),
                           float(node.get("width")), float(node.get("height"))) for node in rects]
                x, y = min(item[0] for item in bounds), min(item[1] for item in bounds)
                frame = dict(x=x, y=y, width=max(a + c for a, b, c, d in bounds) - x,
                             height=max(b + d for a, b, c, d in bounds) - y)
            except (ValueError, TypeError) as error:
                raise ValueError("svg_source_native_table_frame_invalid") from error
            native_tables.append(dict(block_id=block.source_block_id, element_id=group_id, frame=frame))
            required_ids.add(group_id)
            for (r, c, rs, cs, cell), cell_fact in zip(_cells(block)[1], fact["cells"], strict=True):
                cell_id = cell.get("sourceCellId", f"{block.source_block_id}:row:{r}:column:{c}")
                bindings.append(dict(source_id=cell_id, kind="table_cell", element_id=cell_fact["element_id"],
                    text=cell["text"], start=0, end=len(cell["text"]), block_id=block.source_block_id,
                    row=r, column=c, row_span=rs, column_span=cs))
        else:
            binding = dict(source_id=block.source_block_id, kind=block.kind, element_id=fact["element_id"])
            if block.kind == "text":
                text = block.payload["text"]
                binding.update(text=text, start=0, end=len(text))
            elif block.kind == "image":
                binding.update(slot_id=fact["slot_id"], asset_digest=block.payload.get("contentDigest"))
            elif block.kind == "shape" and block.payload.get("customGeometry"):
                binding["geometry_digest"] = digest_json(block.payload["customGeometry"])
            bindings.append(binding)
    required_ids.update(binding["element_id"] for binding in bindings)
    parents = {child: parent for parent in root.iter() for child in parent}
    for node in root.iter():
        # Non-content IDs and formatting whitespace are not source facts. Text
        # and tspan characters, including whitespace, remain byte-for-byte.
        if node.get("id") and node.get("id") not in required_ids:
            node.attrib.pop("id")
        if node.tag not in {f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}tspan"} and node.text and not node.text.strip():
            node.text = None
        parent = parents.get(node)
        if ((parent is None or parent.tag not in {f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}tspan"})
                and node.tail and not node.tail.strip()):
            node.tail = None
    svg = ET.tostring(root, encoding="unicode")
    template = style_snapshot.get("source_template") or {}
    furniture_receipt = None
    if template.get("system_furniture"):
        from services.svg_master_furniture import stamp_master_furniture, furniture_image_manifest

        svg = stamp_master_furniture(svg, template, index=page_index, n=len(pages))
        group = next((node for node in ET.fromstring(svg) if node.get("id") == "svg-system-master-furniture"), None)
        if group is not None:
            image_ids = {node.get("id") for node in group.iter() if node.tag == f"{{{SVG_NS}}}image"}
            furniture_receipt = dict(digest=template["system_furniture"]["digest"],
                element_ids=[node.get("id") for node in group.iter() if node.get("id")],
                images={key: value for key, value in furniture_image_manifest(template).items() if key in image_ids})
    receipt = dict(contract_version="svg-source-preservation/v1", manifest_digest=manifest.manifest_digest,
        source_digest=manifest.source_digest, source_page_id=page.source_unit_id, source_page_index=page_index,
        page_count=len(pages), page_digest=page_digest(manifest, page_index), svg_digest=source_digest(svg),
        style_digest=style_snapshot["digest"], canvas=dict(width=width, height=height), bindings=bindings,
        native_tables=native_tables, source_authoring_contract=style_snapshot["source_authoring_contract"],
        whole_page_native_groups=native_groups,
        **({"source_chart_contract": chart_contract, "source_native_charts": source_native_charts}
           if chart_contract is not None else {}),
        rendering_verified=False,
        font_policy=dict(minimum_px=minimum_font_px, policy="svg-physical-readability/v1", unit="SVG units"),
        **({"system_furniture": furniture_receipt} if furniture_receipt else {}))
    # Child identity stamping is paint-neutral but is part of the exact proof.
    if source_native_charts:
        from services.svg_source_charts import source_chart_record
        receipt["source_native_charts"] = [source_chart_record(svg,
            next(b for b in page.blocks if b.source_block_id == record["source_id"]),
            record["element_id"], record["declaration"], chart_contract) for record in source_native_charts]
    if restoration is not None:
        receipt["source_lf_restoration"] = {**restoration, "canonical_svg_digest": source_digest(svg)}
    if empty_restoration is not None:
        receipt["source_empty_table_text_restoration"] = {
            **empty_restoration, "canonical_svg_digest": source_digest(svg)}
    verify_source_receipt(svg, receipt, manifest, page_index, _validated_pages=pages)
    return svg, receipt


def compose_source_common_header(svg, receipt, manifest, page_index, *, snapshot, binding, _validated_pages=None):
    """Keep the original source witness and prove a separate editable header layer."""
    from services.svg_common_header import GROUP_ID, render_common_header, validate_common_header_binding
    verify_source_receipt(svg, receipt, manifest, page_index, _validated_pages=_validated_pages)
    if receipt.get("common_header_composition"):
        original = receipt["common_header_composition"]
        svg, receipt = original["body_svg"], original["body_receipt"]
    contract = snapshot.get("common_header")
    validate_common_header_binding(contract, binding, index=page_index, count=receipt["page_count"])
    if not isinstance(contract, dict) or contract.get("version") != "svg-common-header/v2":
        raise ValueError("svg_source_header_contract_invalid")
    group = render_common_header(contract, binding, index=page_index, count=receipt["page_count"])
    if group is None:
        return svg, deepcopy(receipt)
    root = ET.fromstring(svg)
    if any(node.get("id", "").startswith(GROUP_ID) for node in root.iter()):
        raise ValueError("svg_source_header_body_ownership_conflict")
    root.append(group)
    final = ET.tostring(root, encoding="unicode")
    composed = {**deepcopy(receipt), "svg_digest": source_digest(final), "rendering_verified": False,
        "common_header_composition": {"version": "svg-source-common-header-composition/v2",
            "body_svg": svg, "body_receipt": deepcopy(receipt), "body_svg_digest": source_digest(svg),
            "contract": deepcopy(contract), "page": deepcopy(binding),
            "header_digest": contract["digest"], "page_header_digest": binding["digest"]}}
    verify_source_receipt(final, composed, manifest, page_index, _validated_pages=_validated_pages)
    return final, composed


def _verify_source_header_composition(svg, receipt, manifest, page_index, *, _validated_pages=None):
    from services.svg_common_header import GROUP_ID, render_common_header
    from services.svg_master_furniture import _structure
    envelope = receipt.get("common_header_composition")
    if (not isinstance(envelope, dict) or envelope.get("version") != "svg-source-common-header-composition/v2"
            or not isinstance(envelope.get("body_svg"), str) or not isinstance(envelope.get("body_receipt"), dict)
            or envelope["body_receipt"].get("common_header_composition")
            or envelope.get("body_svg_digest") != source_digest(envelope["body_svg"])
            or receipt.get("svg_digest") != source_digest(svg)):
        raise ValueError("svg_source_header_composition_invalid")
    contract, binding = envelope.get("contract"), envelope.get("page")
    expected = render_common_header(contract, binding, index=page_index, count=receipt["page_count"])
    if (expected is None or contract.get("version") != "svg-common-header/v2"
            or envelope.get("header_digest") != contract["digest"]
            or envelope.get("page_header_digest") != binding["digest"]):
        raise ValueError("svg_source_header_composition_invalid")
    root = ET.fromstring(svg)
    groups = [node for node in root.iter() if node.get("id") == GROUP_ID]
    if len(groups) != 1 or groups[0] not in list(root) or _structure(groups[0]) != _structure(expected):
        raise ValueError("svg_source_header_composition_invalid")
    root.remove(groups[0])
    if _structure(root) != _structure(ET.fromstring(envelope["body_svg"])):
        raise ValueError("svg_source_header_body_changed")
    original = envelope["body_receipt"]
    if any(receipt.get(key) != value for key, value in original.items()
           if key not in {"svg_digest", "rendering_verified"}):
        raise ValueError("svg_source_header_receipt_changed")
    verify_source_receipt(envelope["body_svg"], original, manifest, page_index,
                          _validated_pages=_validated_pages)
    return True


def verify_source_receipt(svg, receipt, manifest, page_index, *, _validated_pages=None):
    if isinstance(receipt, dict) and receipt.get("common_header_composition"):
        return _verify_source_header_composition(svg, receipt, manifest, page_index,
            _validated_pages=_validated_pages)
    pages = (source_pages(manifest, chart_contract=(receipt or {}).get("source_chart_contract"))
             if _validated_pages is None else _validated_pages)
    if type(page_index) is not int or not 0 <= page_index < len(pages):
        raise ValueError("svg_source_page_index_invalid")
    page = pages[page_index]
    expected = dict(contract_version="svg-source-preservation/v1", manifest_digest=manifest.manifest_digest,
        source_digest=manifest.source_digest, source_page_id=page.source_unit_id, source_page_index=page_index,
        page_count=len(pages), page_digest=page_digest(manifest, page_index), svg_digest=source_digest(svg))
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items()):
        raise ValueError("svg_source_receipt_input_mismatch")
    bindings = receipt.get("bindings", [])
    slots = {b["slot_id"]: {"required": True} for b in bindings if b.get("kind") == "image"}
    canvas = receipt["canvas"]
    furniture = receipt.get("system_furniture") or {}
    validate_svg(
    svg,
    width=canvas["width"],
    height=canvas["height"],
    source_manifest=slots,
     allow_system_page_number=False, trusted_furniture_images=furniture.get("images"))
    root = ET.fromstring(svg)
    elements = {e.get("id"): e for e in root.iter() if e.get("id")}
    bound_ids = [b["element_id"] for b in bindings]
    from services.svg_source_native_tables import verify_source_table_groups
    table_group_ids = verify_source_table_groups(root, receipt, page)
    from services.svg_source_charts import verify_source_charts
    chart_owned_ids, chart_visible_ids = verify_source_charts(svg, receipt, page)
    extra_groups = receipt.get("whole_page_native_groups") or []
    extra_ids = set()
    if extra_groups:
        if (receipt.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}
                or not isinstance(extra_groups, list) or len(extra_groups) > 32):
            raise ValueError("svg_source_native_group_invalid")
        text_ids = {binding["element_id"] for binding in bindings if binding["kind"] in {"text", "table_cell"}}
        for record in extra_groups:
            if not isinstance(record, dict) or set(record) != {"element_id", "kind"} or record["kind"] != "chart":
                raise ValueError("svg_source_native_group_invalid")
            group_id = record["element_id"]
            group = elements.get(group_id)
            if (group_id in extra_ids or group_id in bound_ids or group_id in table_group_ids
                    or group is None or group.tag != f"{{{SVG_NS}}}g"
                    or not {node.get("id") for node in group.iter()}.intersection(text_ids)):
                raise ValueError("svg_source_native_group_invalid")
            extra_ids.add(group_id)
    furniture_ids = furniture.get("element_ids", [])
    if furniture_ids:
        group = elements.get("svg-system-master-furniture")
        if (group is None or not furniture.get("digest")
                or len(furniture_ids) != len(set(furniture_ids))
                or set(furniture_ids) != {node.get("id") for node in group.iter() if node.get("id")}
                or any(not node_id.startswith("svg-system-") for node_id in furniture_ids)
                or set(furniture_ids).intersection(bound_ids)):
            raise ValueError("svg_source_furniture_receipt_mismatch")
    if (len(set(bound_ids)) != len(bound_ids)
            or set(elements) != set(bound_ids) | set(furniture_ids) | table_group_ids | extra_ids | chart_owned_ids):
        raise ValueError("svg_source_receipt_binding_mismatch")
    expected_sources = {}
    for block in page.blocks:
        if block.kind == "source_note":
            continue
        if block.kind == "table":
            for r, c, rs, cs, cell in _cells(block)[1]:
                sid = cell.get("sourceCellId", f"{block.source_block_id}:row:{r}:column:{c}")
                expected_sources[sid] = (
    "table_cell",
    cell["text"],
    dict(
        block_id=block.source_block_id,
        row=r,
        column=c,
        row_span=rs,
         column_span=cs))
        else:
            metadata = {"asset_digest": block.payload.get("contentDigest")} if block.kind == "image" else {}
            if block.kind == "shape" and block.payload.get("customGeometry"):
                metadata = {"geometry_digest": digest_json(block.payload["customGeometry"])}
            expected_sources[block.source_block_id] = (block.kind, block.payload.get("text"), metadata)
    if {b["source_id"] for b in bindings} != set(expected_sources):
        raise ValueError("svg_source_receipt_coverage_mismatch")
    for sid, (kind, text, metadata) in expected_sources.items():
        entries = [b for b in bindings if b["source_id"] == sid]
        cursor = 0
        for b in entries:
            node = elements[b["element_id"]]
            if b["kind"] != kind or any(b.get(k) != v for k, v in metadata.items()):
                raise ValueError("svg_source_receipt_content_mismatch")
            if kind in {"text", "table_cell"}:
                end = b.get("end")
                if b.get("start") != cursor or type(end) is not int or not cursor <= end <= len(text):
                    raise ValueError("svg_source_receipt_range_mismatch")
                if node.tag != f"{{{SVG_NS}}}text" or "".join(
                    node.itertext()) != text[cursor:end] or b.get("text") != text[cursor:end]:
                    raise ValueError("svg_source_receipt_text_mismatch")
                cursor = end
            elif len(entries) != 1:
                raise ValueError("svg_source_receipt_duplicate_occurrence")
            elif kind == "image" and (node.tag != f"{{{SVG_NS}}}image" or node.get("href") != b.get("slot_id")):
                raise ValueError("svg_source_receipt_image_mismatch")
        if kind in {"text", "table_cell"} and cursor != len(text):
            raise ValueError("svg_source_receipt_range_mismatch")
    visible_ids = {node.get("id") for node in root.iter() if node.tag in {f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}image"}}
    expected_visible = {b["element_id"] for b in bindings if b["kind"] in {"text", "table_cell", "image"}}
    if visible_ids - set(furniture_ids) - chart_visible_ids != expected_visible - chart_visible_ids:
        raise ValueError("svg_source_receipt_unbound_content")
    return True
