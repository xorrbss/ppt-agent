"""Layout-only plans and deterministic assembly of immutable source content.

The receipt proves structural preservation; Chromium owns actual glyph/visibility
verification. Unsupported source appearance is rejected before provider dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from services.source_content.contracts import SourceContentManifest
from services.source_content.digests import digest_json
from services.source_content.pptx_custom_geometry import validate_custom_geometry, flatten_custom_geometry
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


def source_pages(manifest: SourceContentManifest):
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
                       for row in b.payload.get("rows", []) for c in row)) for b in page.blocks):
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
                raise ValueError("svg_source_chart_unsupported")
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
                "preset") not in {"rect", "roundRect", "ellipse", "line", "straightConnector1", "parallelogram"}:
                if block.payload.get("preset") is not None or not block.payload.get("customGeometry"):
                    raise ValueError(f"svg_source_shape_unsupported:page={index + 1}:block={block.source_block_id}")
                validate_custom_geometry(block.payload["customGeometry"])
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
            node = _node(parent if parent is not None else root, "text", id=element_id, x=x, y=y + placement.font_size * (ascent + advance * line_index),
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
            elif preset == "line" or block.semantic_role == "connector":
                geometry = block.payload.get("geometry") or {}
                x1, x2 = (p.x + p.width, p.x) if geometry.get("flipH") else (p.x, p.x + p.width)
                y1, y2 = (p.y + p.height, p.y) if geometry.get("flipV") else (p.y, p.y + p.height)
                _node(root, "line", id=element_id, x1=x1, y1=y1, x2=x2, y2=y2, stroke=p.stroke, stroke_width=1)
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


def verify_source_receipt(svg, receipt, manifest, page_index, *, _validated_pages=None):
    pages = source_pages(manifest) if _validated_pages is None else _validated_pages
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
    furniture_ids = furniture.get("element_ids", [])
    if furniture_ids:
        group = elements.get("svg-system-master-furniture")
        if (group is None or not furniture.get("digest")
                or len(furniture_ids) != len(set(furniture_ids))
                or set(furniture_ids) != {node.get("id") for node in group.iter() if node.get("id")}
                or any(not node_id.startswith("svg-system-") for node_id in furniture_ids)
                or set(furniture_ids).intersection(bound_ids)):
            raise ValueError("svg_source_furniture_receipt_mismatch")
    if len(set(bound_ids)) != len(bound_ids) or set(elements) != set(bound_ids) | set(furniture_ids) | table_group_ids:
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
    if visible_ids - set(furniture_ids) != expected_visible:
        raise ValueError("svg_source_receipt_unbound_content")
    return True
