"""Geometry-only SVG composition of approved raster content, without layout patterns."""
from __future__ import annotations

from dataclasses import asdict
import json
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from services.svg_source_layout import SvgLayoutPlan
from services.svg_contract import SVG_NS, RASTER_REDESIGN_ROOT_ID, source_digest, validate_svg
from services.source_content.raster_redesign_header import element_tag
from services.source_content.raster_redesign_plan_items import build_items
from services.source_content.raster_redesign_table import layout_table, render_table
from services.source_content.raster_structure import redesign_content_ledger
from services.source_content.raster_target import raster_target_font_floor, require_raster_physical_readability
from services.svg_source_template_layout import wrap_source_text, text_metrics
from utils.svg_template_fonts import measure_font
from utils.svg_template_rules import reference_template_snapshot


def free_raster_items(analysis, pin, index):
    from services.source_content.raster_svg_layout import _approved_regions
    page = _approved_regions(analysis, index + 1)
    if (pin.analysis_id != analysis.analysis_id or pin.analysis_digest != analysis.digest
            or pin.analysis_revision != analysis.revision or pin.source_sha256 != analysis.source.source_sha256):
        raise ValueError("raster_free_composition_approval_mismatch")
    if page.render_mode != "redesign":
        raise ValueError("raster_free_composition_requires_redesign")
    if set(pin.approved_region_ids) != {region.region_id for source_page in analysis.pages
            for region in ((source_page.full_page_region,) if source_page.render_mode == "full_page_preserve"
                           else source_page.regions) if region is not None}:
        raise ValueError("raster_approved_region_set_mismatch")
    ledger = redesign_content_ledger(analysis, index + 1)
    if not ledger.lossless:
        raise ValueError("raster_free_composition_ledger_invalid")
    title, logos, items = build_items(page)
    title_items = list(title.items) if title is not None and hasattr(title, "items") else []
    if title is not None and not hasattr(title, "items"):
        from services.source_content.raster_redesign_tree import Item
        title_items = [Item("text", title)]
    ordered = [*title_items, *logos, *items]
    if len({item.region.region_id for item in ordered}) != len(ordered):
        raise ValueError("raster_free_composition_duplicate_region")
    return page, ordered, ledger


def raster_composition_context(analysis, pin, index, snapshot):
    from services.raster_source_assets import raster_slot_id
    page, items, ledger = free_raster_items(analysis, pin, index)
    width, height = analysis.target_canvas.width_units, analysis.target_canvas.height_units
    template = reference_template_snapshot(snapshot, width, height)
    identity_policy = template.get("identity_policy") == "svg-source-identity/v2"
    source_regions = {region.region_id: region for region in page.regions}
    blocks = []
    characters = set()
    for item in items:
        region = item.region
        block = dict(block_id=region.region_id, kind=item.kind, role=region.role,
                     reading_order=region.reading_order, group_id=region.group_id)
        if identity_policy:
            block["source_evidence"] = _source_evidence(region)
        if item.kind == "text":
            block["text"] = region.text or ""
            characters.update(block["text"])
        elif item.kind == "table":
            block["rows"] = item.rows
            block["pixel_cells"] = [dict(row=cell.row, column=cell.col,
                slot=raster_slot_id(index + 1, cell.region_id)) for cell in item.pixel_cells]
            if identity_policy:
                for cell, descriptor in zip(item.pixel_cells, block["pixel_cells"], strict=True):
                    descriptor.update(region_id=cell.region_id, content_role="immutable-table-cell-bitmap",
                                      source_evidence=_source_evidence(source_regions[cell.region_id]))
            characters.update("".join(cell for row in item.rows for cell in row))
        else:
            block.update(slot=raster_slot_id(index + 1, region.region_id),
                         aspect_ratio=1 / item.aspect, source_pixel_bbox=region.source_pixel_bbox)
            if identity_policy:
                block["image_treatment"] = {
                    "content_role": "immutable-text-bitmap" if item.inline else
                        "immutable-table-bitmap" if region.role == "table" else "source-image",
                    "inline_with_text": item.inline, "minimum_scale": item.min_scale,
                    "maximum_scale": item.max_scale, "source_glyph_height_px": item.text_px,
                    "guidance": "Keep the crop intact and readable; align text crops with related text and retain table crops as evidence.",
                }
        blocks.append(block)
    font = measure_font(template["fonts"]["body"]["family"], 400, 20)
    digest = source_digest(json.dumps({"analysis": analysis.digest, "page": page.model_dump(mode="json")},
                                     sort_keys=True, ensure_ascii=False))
    return dict(source_unit_id=f"raster-page-{index + 1}", page_digest=digest,
                width=width, height=height, minimum_font_size_px=raster_target_font_floor(analysis.target_canvas),
                source_template=template, blocks=blocks, schema=SvgLayoutPlan.model_json_schema(),
                measurements={"font_size_px": 20, "glyph_widths": {
                    c: round(font.getlength(c), 3) for c in sorted(characters) if c not in "\r\n\t"}}), items, ledger


def _source_evidence(region):
    return {"confidence": region.confidence,
            "ocr_confidence": region.ocr.confidence if region.ocr else None,
            "review_reason": region.review_reason, "editable_fallback": region.editable_fallback,
            "content_policy": "Approved content and crop pixels are immutable; confidence guides visual treatment only."}


def assemble_free_raster_svg(analysis, index, plan, context, items):
    from services.raster_source_assets import raster_slot_id
    template, minimum = context["source_template"], context["minimum_font_size_px"]
    identity_policy = template.get("identity_policy") == "svg-source-identity/v2"
    if (plan.schema_version != "svg-source-layout/v2" or plan.source_unit_id != context["source_unit_id"] or plan.page_digest != context["page_digest"]
            or (plan.width, plan.height) != (context["width"], context["height"])
            or plan.template_digest != template["digest"] or plan.layout_id != "free-svg"):
        raise ValueError("raster_free_composition_input_mismatch")
    lookup = {item.region.region_id: item for item in items}
    ids = [p.block_id for p in plan.placements]
    if len(ids) != len(set(ids)) or set(ids) != set(lookup):
        raise ValueError("raster_free_composition_region_coverage_mismatch")
    if plan.background != template["tokens"]["background"]:
        raise ValueError("raster_free_composition_background_mismatch")
    root = ET.Element(f"{{{SVG_NS}}}svg", dict(id=RASTER_REDESIGN_ROOT_ID,
        width=str(plan.width), height=str(plan.height), viewBox=f"0 0 {plan.width} {plan.height}"))
    ET.SubElement(root, f"{{{SVG_NS}}}rect", dict(x="0", y="0", width=str(plan.width), height=str(plan.height), fill=plan.background))
    occupied, bindings, slots = [], [], set()
    placements = {p.block_id: p for p in plan.placements}
    for item in items:
        p = placements[item.region.region_id]
        if p.x + p.width > plan.width or p.y + p.height > plan.height:
            raise ValueError("raster_free_composition_outside_canvas")
        prefix = "rd-" + element_tag(p.block_id)
        element_ids = []
        if item.kind == "image":
            if not p.width.is_integer() or not p.height.is_integer():
                raise ValueError("raster_free_composition_image_integer_dimensions_required")
            pixel = item.region.source_pixel_bbox
            if pixel and min(p.width / pixel[2], p.height / pixel[3]) + .001 < item.min_scale:
                raise ValueError("raster_free_composition_crop_below_legibility")
            slot = raster_slot_id(index + 1, p.block_id)
            slots.add(slot)
            attrs = dict(id=prefix + "-image", x=str(p.x), y=str(p.y), width=str(int(p.width)),
                         height=str(int(p.height)), href=slot, preserveAspectRatio="xMidYMid meet")
            ET.SubElement(root, f"{{{SVG_NS}}}image", attrs)
            element_ids.append(attrs["id"])
            actual_width, actual_height = min(p.width, p.height / item.aspect), min(p.height, p.width * item.aspect)
        else:
            role = "heading" if item.region.role == "title" else "table" if item.kind == "table" else "body"
            spec = template["fonts"][role]
            if p.font_family != spec["family"] or str(p.font_weight) != str(spec["weight"]) or p.font_size < minimum:
                raise ValueError("raster_free_composition_typography_mismatch")
            colours = {value.lower() for value in template["tokens"].values() if isinstance(value, str)}
            colours.update(value.lower() for value in template["tokens"].get("palette", []) if isinstance(value, str))
            if item.kind == "text" and (p.fill.lower() not in colours or p.fill.lower() == plan.background.lower()):
                raise ValueError("raster_free_composition_text_colour_mismatch")
            if item.kind == "text":
                font = measure_font(p.font_family, p.font_weight, 20)
                ends = wrap_source_text(item.region.text or "", p.width, p.font_size, font)
                ascent, advance = text_metrics(font)
                actual_height = p.font_size * (ascent + advance * (len(ends) - 1) + .3)
                if actual_height > p.height + .001:
                    raise ValueError("raster_free_composition_text_overflow")
                start = 0
                for line, end in enumerate(ends):
                    attrs = {"id": prefix + f"-text-{line}", "x": str(p.x),
                             "y": str(p.y + p.font_size * (ascent + advance * line)),
                             "font-family": p.font_family, "font-weight": p.font_weight,
                             "font-size": str(p.font_size), "fill": p.fill}
                    ET.SubElement(root, f"{{{SVG_NS}}}text", attrs).text = (item.region.text or "")[start:end]
                    element_ids.append(attrs["id"])
                    start = end
                actual_width = p.width
            else:
                table = layout_table(item.rows, p.width, p.font_size, item.pixel_cells)
                if table is None or table.height > p.height + .001:
                    raise ValueError("raster_free_composition_table_overflow")
                fragments = render_table(table, p.x, p.y, id_prefix=prefix, family=p.font_family, escape=escape,
                    image_href=lambda region_id: raster_slot_id(index + 1, region_id))
                group = ET.fromstring(f'<g xmlns="{SVG_NS}">' + "".join(fragments) + "</g>")
                colours = {"#435D80": template["tokens"]["accent"], "#081D49": template["tokens"]["text"],
                           "#F4F8FD": template["table"]["body_fill"], "#CBDFF4": template["table"]["border"]}
                if identity_policy:
                    colours.update({"#FFFFFF": template["table"]["body_fill"], "#6F84A3": template["table"]["border"]})
                for node in group.iter():
                    if identity_policy:
                        if node.tag == f"{{{SVG_NS}}}text":
                            header = (node.get("id") or "").startswith(prefix + "-r0c")
                            node.set("fill", template["table"]["header_text"] if header else template["tokens"]["text"])
                            node.set("font-weight", str(template["table"]["header_weight"] if header else p.font_weight))
                    for attr in ("fill", "stroke"):
                        if node.get(attr) in colours and not (identity_policy and node.tag == f"{{{SVG_NS}}}text" and attr == "fill"):
                            node.set(attr, colours[node.get(attr)])
                    if node.get("id"):
                        element_ids.append(node.get("id"))
                    if node.tag == f"{{{SVG_NS}}}image":
                        slots.add(node.get("href"))
                root.extend(list(group))
                actual_width, actual_height = table.width, table.height
        box = (p.x + (p.width - actual_width) / 2 if item.kind == "image" else p.x,
               p.y + (p.height - actual_height) / 2 if item.kind == "image" else p.y,
               actual_width, actual_height)
        for other in occupied:
            if min(box[0] + box[2], other[0] + other[2]) > max(box[0], other[0]) and min(box[1] + box[3], other[1] + other[3]) > max(box[1], other[1]):
                raise ValueError("raster_free_composition_content_collision")
        occupied.append(box)
        bindings.append(dict(region_id=p.block_id, element_ids=element_ids, kind=item.kind))
    svg = ET.tostring(root, encoding="unicode")
    require_raster_physical_readability(svg, analysis.target_canvas)
    furniture_images = None
    furniture = template.get("system_furniture")
    if furniture:
        for reservation in furniture.get("reservations", []):
            visibility = reservation.get("visibility")
            if visibility == "hidden" or (visibility == "exceptCover" and index == 0) or (visibility == "coverOnly" and index != 0):
                continue
            x, y, width, height = (reservation[key] for key in ("x", "y", "width", "height"))
            for box in occupied:
                if min(x + width, box[0] + box[2]) > max(x, box[0]) and min(y + height, box[1] + box[3]) > max(y, box[1]):
                    raise ValueError("raster_free_composition_master_collision")
        from services.svg_master_furniture import stamp_master_furniture, furniture_image_manifest

        svg = stamp_master_furniture(svg, template, index, len(analysis.pages))
        images = {node.get("id") for node in ET.fromstring(svg).iter() if node.tag == f"{{{SVG_NS}}}image"}
        furniture_images = {key: value for key, value in furniture_image_manifest(template).items() if key in images}
    validate_svg(svg, width=plan.width, height=plan.height, source_manifest={slot: {"required": True} for slot in slots},
                 trusted_furniture_images=furniture_images)
    return svg, bindings


def raster_ledger_receipt(ledger):
    return dict(page_number=ledger.page_number, lossless=ledger.lossless,
                entries=[asdict(entry) for entry in ledger.entries], violations=list(ledger.violations))
