"""Bounded server layout and font resolution within a selected template.

Source geometry is consulted only to associate furniture with content. It never
supplies output coordinates. All emitted characters come from the source page.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
import math
import statistics
import time

from services.svg_source_layout import (SvgLayoutPlan, SvgResolvedLayoutPlan,
    SvgSourcePlacement, _cells, _ranges, assemble_source_svg, page_digest, source_pages)
from services.svg_contract import source_digest


SOLVER_VERSION = "svg-template-layout/v2"
_LOCAL_LIMIT = ContextVar("svg_source_local_limit", default=None)
_LOCAL_CANCEL = ContextVar("svg_source_local_cancel", default=None)


def _check_local_budget():
    callback = _LOCAL_CANCEL.get()
    if callback is not None:
        callback()
    limit = _LOCAL_LIMIT.get()
    if limit is not None and time.monotonic() >= limit:
        raise ValueError("svg_source_layout_search_budget_exhausted")


def selected_template(style_snapshot):
    template = style_snapshot.get("source_template")
    if not isinstance(template, dict) or not template.get("digest") or not template.get("layouts"):
        raise ValueError("svg_source_template_layout_required")
    return template


def validate_page_font_coverage(manifest, page_index, template):
    from utils.svg_template_fonts import require_font_coverage
    roles = page_roles(manifest, page_index)
    for block in manifest.units[page_index].blocks:
        if block.kind == "text":
            require_font_coverage(block.payload["text"], template["fonts"][_role(block, roles)]["family"])
        elif block.kind == "table":
            for _, _, _, _, cell in _cells(block)[1]:
                require_font_coverage(cell["text"], template["fonts"]["table"]["family"])


def _font(placement):
    from utils.svg_template_fonts import measure_font
    return measure_font(placement.font_family, placement.font_weight, 20)


def text_metrics(font):
    ascent, descent = font.getmetrics()
    return ascent / 20, (ascent + descent) / 20 + .1


def measured_text_height(lines, size, font):
    _, advance = text_metrics(font)
    return size * advance * lines


def wrap_source_text(text, width, size, font):
    """Keep every character, including newline/space, in an exclusive range."""
    if width <= 0:
        raise ValueError("svg_source_text_box_unfit")
    ends, start = [], 0
    while start < len(text):
        _check_local_budget()
        newline = text.find("\n", start)
        limit = len(text) if newline < 0 else newline + 1
        low, high, best = start + 1, limit, start
        while low <= high:
            _check_local_budget()
            middle = (low + high) // 2
            fragment = text[start:middle].rstrip("\r\n")
            bounds = font.getbbox(fragment)
            measured = max(font.getlength(fragment), bounds[2] - min(0, bounds[0])) * size / 20
            if measured <= width * .97:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        if best == start:
            raise ValueError("svg_source_text_box_unfit")
        ends.append(best)
        start = best
    return tuple(ends or [0])


def _text_boxes(block, placement, template=None):
    if block.kind == "text":
        return [(block.source_block_id, block.payload["text"], placement.width, placement.height)]
    rows, cells = _cells(block)
    inset = template["table"]["cell_padding"] if template else None
    return [(cell.get("sourceCellId", f"{block.source_block_id}:row:{r}:column:{c}"), cell["text"],
        placement.width * cs / len(rows[0]) - (2 * inset if inset is not None else 6),
        placement.height * rs / len(rows) - (2 * inset if inset is not None else 4))
        for r, c, rs, cs, cell in cells]


def resolve_source_plan(plan, manifest, page_index, minimum_px, template=None):
    """Resolve untrusted geometry into measured character ranges, never vice versa."""
    minimum_px = math.ceil(minimum_px * 100) / 100
    blocks = {b.source_block_id: b for b in manifest.units[page_index].blocks if b.kind != "source_note"}
    ids = [p.block_id for p in plan.placements]
    if len(set(ids)) != len(ids) or set(ids) != set(blocks):
        raise ValueError("svg_source_plan_coverage_mismatch")
    resolved, adjustments = [], 0
    for placement in plan.placements:
        _check_local_budget()
        block = blocks[placement.block_id]
        values = placement.model_dump(exclude={"line_ends", "cell_line_ends"})
        if block.kind not in {"text", "table"}:
            resolved.append(SvgSourcePlacement(**values))
            continue
        font = _font(placement)
        boxes = _text_boxes(block, placement, template)
        header_cells = {cell.get("sourceCellId", f"{block.source_block_id}:row:{r}:column:{c}")
            for r, c, rs, cs, cell in _cells(block)[1] if r == 0} if block.kind == "table" and template else set()
        header_font = _font(placement.model_copy(update={"font_weight": str(template["table"]["header_weight"])})) if header_cells else font
        def fit(size):
            ranges = {sid: wrap_source_text(text, width, size, header_font if sid in header_cells else font) for sid, text, width, height in boxes}
            return ranges, all(measured_text_height(len(ranges[sid]),size,header_font if sid in header_cells else font) <= height + .000001
                for sid, text, width, height in boxes)
        size = max(placement.font_size, minimum_px)
        try:
            ranges, fits = fit(size)
        except ValueError as error:
            if "text_box_unfit" not in str(error):
                raise
            ranges, fits = {}, False
        if not fits:
            ranges, fits = fit(minimum_px)
            if not fits:
                raise ValueError(f"svg_source_text_box_unfit:{placement.block_id}")
            low, high, size = minimum_px, size, minimum_px
            for _ in range(10):
                middle = (low + high) / 2
                try:
                    candidate, fits = fit(middle)
                except ValueError as error:
                    if "text_box_unfit" not in str(error):
                        raise
                    fits = False
                if fits:
                    low, size, ranges = middle, middle, candidate
                else:
                    high = middle
        size = max(minimum_px, math.floor(size * 100) / 100)
        ranges, _ = fit(size)
        values["font_size"] = size
        values["line_ends" if block.kind == "text" else "cell_line_ends"] = (
            ranges[block.source_block_id] if block.kind == "text" else ranges)
        resolved.append(SvgSourcePlacement(**values))
        adjustments += 1
    values = plan.model_dump(exclude={"placements"})
    return SvgResolvedLayoutPlan(**values, placements=tuple(resolved)), adjustments


def repair_template_coordinates(plan, manifest, page_index, template):
    """Move only colliding later content, at most two passes within its slot."""
    blocks = {b.source_block_id: b for b in manifest.units[page_index].blocks}
    roles = page_roles(manifest, page_index)
    layout = next(x for x in template["layouts"] if x["id"] == plan.layout_id)
    membership = source_group_membership(manifest,page_index,parallel_only=layout.get("allowed_variations",{}).get("requires_same_source_parallel_group",False))
    placements = list(plan.placements)
    count = 0
    for _ in range(2):
        changed = False
        candidate = plan.model_copy(update={"placements": tuple(placements)})
        ink = occupied_boxes(candidate, manifest, page_index)
        for i, p in enumerate(placements):
            if blocks[p.block_id].kind == "shape":
                continue
            earlier = {x.block_id for x in placements[:i] if blocks[x.block_id].kind != "shape"}
            shift = 0
            for a in (x for x in ink if x.block_id == p.block_id):
                for b in (x for x in ink if x.block_id in earlier):
                    gap = required_content_gap(a.block_id,b.block_id,layout,membership)
                    horizontal_gap = max(a.x,b.x)-min(a.x+a.width,b.x+b.width)
                    if horizontal_gap < gap and a.y < b.y+b.height+gap and a.y+a.height > b.y:
                        shift = max(shift, b.y+b.height+gap-a.y)
            if shift:
                moved = p.model_copy(update={"y": p.y + shift})
                slot = layout["title_box"] if _role(blocks[p.block_id], roles) == "heading" else layout["body_box"]
                if not _inside(moved, slot):
                    raise ValueError(f"svg_source_content_boxes_overlap:{p.block_id}")
                placements[i], changed = moved, True
                count += 1
                candidate = plan.model_copy(update={"placements": tuple(placements)})
                ink = occupied_boxes(candidate, manifest, page_index)
        if not changed:
            break
    return plan.model_copy(update={"placements": tuple(placements)}), count


@dataclass(frozen=True)
class OccupiedBox:
    block_id: str
    x: float
    y: float
    width: float
    height: float


def occupied_boxes(plan, manifest, page_index):
    """Ink boxes distinguish unused text margins from actual content collisions.

    These measured boxes are a preflight check; Chromium still verifies final
    shaping, loaded fonts, visibility and clipping before publication.
    """
    blocks = {b.source_block_id: b for b in manifest.units[page_index].blocks}
    boxes = []
    for p in plan.placements:
        block = blocks[p.block_id]
        if block.kind == "shape":
            continue
        if block.kind != "text":
            boxes.append(OccupiedBox(p.block_id, p.x, p.y, p.width, p.height))
            continue
        font, scale = _font(p), p.font_size / 20
        ascent, advance = text_metrics(font)
        for line, (start, end) in enumerate(_ranges(block.payload["text"], p.line_ends)):
            fragment = block.payload["text"][start:end].rstrip("\r\n")
            if not fragment.strip():
                continue
            left, top, right, bottom = font.getbbox(fragment, anchor="ls")
            boxes.append(OccupiedBox(p.block_id, p.x + left * scale,
                p.y + p.font_size * (ascent + advance * line) + top * scale,
                (right - left) * scale, (bottom - top) * scale))
    return boxes


def _inside(p, box):
    return (p.x >= box["x"] - .001 and p.y >= box["y"] - .001
        and p.x + p.width <= box["x"] + box["width"] + .001
        and p.y + p.height <= box["y"] + box["height"] + .001)


def _role(block, roles=None):
    if roles is not None and block.source_block_id in roles:
        return roles[block.source_block_id]
    return "heading" if block.kind == "text" and block.semantic_role in {"title", "heading", "subtitle"} else (
        "table" if block.kind == "table" else "body")


def page_roles(manifest, page_index):
    """Use explicit roles, or one clearly prominent source title candidate.

    Source type size establishes hierarchy only; output type comes from the
    selected template. Ambiguous source pages retain their declared roles.
    """
    blocks = manifest.units[page_index].blocks
    roles = {b.source_block_id: _role(b) for b in blocks}
    if "heading" not in roles.values():
        candidates = [(b, b.payload.get("dominantSizePt")) for b in blocks if b.kind == "text"]
        candidates = [(b, float(size)) for b, size in candidates if isinstance(size, (int, float)) and size > 0]
        if candidates:
            typical = statistics.median(size for _,size in candidates)
            block,size = max(candidates,key=lambda item:(item[1],-item[0].order))
            if size >= typical*1.4 and len(block.payload["text"]) <= 200:
                roles[block.source_block_id] = "heading"
                anchor = block.payload.get("geometry", {})
                for neighbor, neighbor_size in candidates:
                    geom = neighbor.payload.get("geometry", {})
                    if neighbor is block or neighbor_size < size*.8 or len(neighbor.payload["text"]) > 200:
                        continue
                    if all(k in anchor and k in geom for k in ("x","y","cx","cy")):
                        separation = max(anchor["y"],geom["y"])-min(anchor["y"]+anchor["cy"],geom["y"]+geom["cy"])
                        if abs(anchor["x"]-geom["x"]) <= max(anchor["cx"],geom["cx"])*.1 and separation <= max(anchor["cy"],geom["cy"])*.2:
                            roles[neighbor.source_block_id] = "heading"
    return roles


def parallel_source_groups(manifest, page_index):
    blocks = manifest.units[page_index].blocks
    candidates = []
    for block in blocks:
        geom = block.payload.get("geometry", {})
        if block.kind != "shape" or not all(k in geom for k in ("x","y","cx","cy")) or min(geom["cx"],geom["cy"]) <= 0:
            continue
        members = []
        for child in blocks:
            other = child.payload.get("geometry", {})
            if child.kind != "text" or not all(k in other for k in ("x","y","cx","cy")):
                continue
            if geom["x"] <= other["x"]+other["cx"]/2 <= geom["x"]+geom["cx"] and geom["y"] <= other["y"]+other["cy"]/2 <= geom["y"]+geom["cy"]:
                members.append(child.source_block_id)
        if len(members) >= 3:
            candidates.append((block,geom,members))
    for anchor, geom, _ in candidates:
        peers = [(b,g,m) for b,g,m in candidates if abs(g["y"]-geom["y"]) <= geom["cy"]*.04
            and abs(g["cx"]-geom["cx"]) <= geom["cx"]*.05 and abs(g["cy"]-geom["cy"]) <= geom["cy"]*.05]
        peers.sort(key=lambda item:item[1]["x"])
        if len(peers) >= 3 and all(a[1]["x"]+a[1]["cx"] <= b[1]["x"] for a,b in zip(peers,peers[1:])):
            return [dict(container_id=b.source_block_id,member_ids=m) for b,g,m in peers]
    return []


def applicable_layouts(template, manifest, page_index):
    groups = parallel_source_groups(manifest,page_index)
    layouts = [layout for layout in template["layouts"] if not layout.get("applicability",{}).get("requires_source_parallel_groups")
        or len(groups) >= layout["applicability"].get("minimum_group_count",3)]
    if groups:
        layouts.sort(key=lambda layout:(-layout["columns"],layout["id"]))
    return layouts


def source_group_membership(manifest,page_index,*,parallel_only=False):
    blocks = manifest.units[page_index].blocks
    content = [b for b in blocks if b.kind in {"text","image","table"}]
    membership = {b.source_block_id:set() for b in content}
    allowed = {g["container_id"] for g in parallel_source_groups(manifest,page_index)} if parallel_only else None
    for container in (b for b in blocks if b.kind == "shape"):
        if allowed is not None and container.source_block_id not in allowed:
            continue
        geom = container.payload.get("geometry",{})
        if not all(k in geom for k in ("x","y","cx","cy")):
            continue
        members = []
        for block in content:
            item = block.payload.get("geometry",{})
            if all(k in item for k in ("x","y","cx","cy")) and geom["x"] <= item["x"]+item["cx"]/2 <= geom["x"]+geom["cx"] and geom["y"] <= item["y"]+item["cy"]/2 <= geom["y"]+geom["cy"]:
                members.append(block.source_block_id)
        # Whole-page backdrops do not create a semantic content group.
        if 2 <= len(members) < len(content)*.7:
            for sid in members:
                membership[sid].add(container.source_block_id)
    return membership


def required_content_gap(first_id,second_id,layout,membership):
    if membership.get(first_id,set()).intersection(membership.get(second_id,set())):
        return layout.get("intra_group_gap",layout["min_gap"])
    return layout["min_gap"]


def validate_template_plan(plan, manifest, page_index, template):
    if plan.schema_version != "svg-source-layout/v2" or plan.template_digest != template["digest"]:
        raise ValueError("svg_source_template_identity_mismatch")
    layout = next((x for x in applicable_layouts(template,manifest,page_index) if x["id"] == plan.layout_id), None)
    if layout is None:
        raise ValueError("svg_source_template_layout_unknown")
    tokens = template["tokens"]
    if plan.background != tokens["background"]:
        raise ValueError("svg_source_template_background_mismatch")
    blocks = {b.source_block_id: b for b in manifest.units[page_index].blocks}
    roles = page_roles(manifest, page_index)
    evidence = []
    for p in plan.placements:
        block = blocks.get(p.block_id)
        if block is None:
            raise ValueError("svg_source_plan_coverage_mismatch")
        role = _role(block, roles)
        box = layout.get("title_box") if role == "heading" else layout["body_box"]
        inside = box and _inside(p, box)
        if block.kind == "shape" and layout.get("title_box"):
            inside = inside or _inside(p, layout["title_box"])
        if not inside:
            raise ValueError(f"svg_source_template_slot_violation:{p.block_id}")
        if block.kind in {"text", "table"}:
            spec = template["fonts"][role]
            expected_fill = tokens.get("heading", tokens["text"]) if role == "heading" else tokens["text"]
            if (p.font_family != spec["family"] or str(p.font_weight) != str(spec["weight"])
                    or p.fill != expected_fill or p.font_size > spec["size"] + .001):
                raise ValueError(f"svg_source_template_typography_violation:{p.block_id}")
            if block.kind == "table" and (p.background != template["table"]["body_fill"] or p.stroke != template["table"]["border"]):
                raise ValueError("svg_source_template_table_violation")
            evidence.append(dict(block_id=p.block_id, role=role, font_digest=spec["digest"],
                family=p.font_family, size=p.font_size, fill=p.fill))
        elif block.kind == "shape" and (p.background != template["shape"]["fill"] or p.stroke != template["shape"]["border"]):
            raise ValueError("svg_source_template_shape_violation")
    headings = [p.font_size for p in plan.placements if _role(blocks[p.block_id], roles) == "heading"]
    bodies = [p.font_size for p in plan.placements if blocks[p.block_id].kind in {"text", "table"} and _role(blocks[p.block_id], roles) != "heading"]
    if headings and bodies and min(headings) < max(bodies):
        raise ValueError("svg_source_template_title_hierarchy_violation")
    return dict(template_digest=template["digest"], layout_id=plan.layout_id,
        rule_version=template["rule_version"], solver_version=SOLVER_VERSION,
        typography=evidence, decoration_count=len(template.get("decorations", [])),
        source_parallel_groups=parallel_source_groups(manifest,page_index) if layout.get("applicability",{}).get("requires_source_parallel_groups") else [],
        role_classification=[dict(block_id=b.source_block_id, source_role=b.semantic_role,
            layout_role=roles[b.source_block_id], basis="source_typography_prominence" if roles[b.source_block_id] != _role(b) else "source_role")
            for b in blocks.values() if b.kind in {"text", "table"}],
        constraints_verified=True, passed=True,
        checks=dict(identity=True, slots=True, typography=True, background=True,
            table_style=True, shape_style=True, decorations=True, title_hierarchy=True), visual_review_verified=False)


def _placement(block, template, *, role=None, **geometry):
    role = role or _role(block)
    font = template["fonts"][role]
    tokens = template["tokens"]
    return SvgSourcePlacement(block_id=block.source_block_id, **geometry,
        font_family=font["family"], font_weight=str(font["weight"]), font_size=font["size"],
        fill=tokens.get("heading", tokens["text"]) if role == "heading" else tokens["text"],
        background=template["table"]["body_fill"] if block.kind == "table" else template.get("shape", {}).get("fill", tokens["background"]),
        stroke=template["table"]["border"] if block.kind == "table" else template.get("shape", {}).get("border", tokens.get("accent", tokens["text"])))


def _needed_height(block, p, size, template):
    if block.kind == "image":
        geometry = block.payload.get("geometry") or {}
        ratio = float(geometry.get("cy", geometry.get("height", 1))) / max(1, float(geometry.get("cx", geometry.get("width", 1))))
        image_fraction = template.get("image", {}).get("max_height_fraction")
        image_cap = (p.height * image_fraction if image_fraction is not None
                     else 42 if ratio < .3 else p.height * .35)
        return max(1, math.floor(min(image_cap, p.width * ratio)))
    font = _font(p)
    if block.kind == "table":
        rows, cells = _cells(block)
        inset = template["table"]["cell_padding"]
        header_font = _font(p.model_copy(update={"font_weight": str(template["table"]["header_weight"])}))
        row_height = max((measured_text_height(len(wrap_source_text(cell["text"], p.width * cs / len(rows[0]) - 2 * inset, size, header_font if r == 0 else font)),size,header_font if r == 0 else font) + 2 * inset) / rs
            for r, c, rs, cs, cell in cells)
        return row_height * len(rows)
    return measured_text_height(len(wrap_source_text(block.payload["text"], p.width, size, font)),size,font)


def _candidate(manifest, page_index, template, layout, width, height, minimum_px, pass_index):
    blocks = [b for b in manifest.units[page_index].blocks if b.kind != "source_note"]
    membership = source_group_membership(manifest,page_index,parallel_only=layout.get("allowed_variations",{}).get("requires_same_source_parallel_group",False))
    roles = page_roles(manifest, page_index)
    headings = [b for b in blocks if _role(b, roles) == "heading"]
    content = [b for b in blocks if b.kind != "shape" and b not in headings]
    gap = layout["gap"] if pass_index == 0 else layout["min_gap"]
    body = layout["body_box"]
    columns = int(layout["columns"])
    col_width = (body["width"] - gap * (columns - 1)) / columns
    placed = []
    title = layout.get("title_box")
    if headings and not title:
        raise ValueError("svg_source_text_box_unfit:title")
    for i, block in enumerate(headings):
        h = (title["height"] - gap * (len(headings) - 1)) / len(headings)
        placed.append(_placement(block, template, role=roles[block.source_block_id], x=title["x"], y=title["y"] + i * (h + gap), width=title["width"], height=h))
    groups = []
    for block in sorted(content, key=lambda b: b.order):
        previous = groups[-1][-1] if groups else None
        a = previous.payload.get("geometry", {}) if previous is not None else {}
        b = block.payload.get("geometry", {})
        same_row = False
        if pass_index == 1 and previous is not None and previous.kind == block.kind == "text" and len(groups[-1]) < 4 and all(k in a and k in b for k in ("x", "y", "cx", "cy")):
            overlap = min(a["y"]+a["cy"],b["y"]+b["cy"])-max(a["y"],b["y"])
            same_row = overlap >= .6 * min(a["cy"],b["cy"]) and b["x"] >= a["x"] + a["cx"] * .8
        if same_row:
            groups[-1].append(block)
        else:
            groups.append([block])
    column, y, previous_group = 0, body["y"], None
    for group in groups:
        _check_local_budget()
        if previous_group is not None and pass_index > 0:
            y -= gap-required_content_gap(previous_group[-1].source_block_id,group[0].source_block_id,layout,membership)
        weights = []
        for block in group:
            probe = _placement(block, template, x=0,y=0,width=col_width,height=body["height"])
            weights.append(max(1,max((_font(probe).getlength(line) for line in block.payload.get("text", "").splitlines()), default=1)))
        available_width = col_width - gap*(len(group)-1)
        items, offset = [], 0
        for block, weight in zip(group, weights):
            w = available_width * weight / sum(weights)
            p = _placement(block, template, x=body["x"]+column*(col_width+gap)+offset,y=y,
                width=math.floor(w) if block.kind == "image" else w,height=body["height"])
            size = minimum_px if pass_index == 2 else p.font_size
            h = _needed_height(block,p,size,template)
            items.append((p,h,size,offset))
            offset += w+gap
        h = max(item[1] for item in items)
        if y + h > body["y"] + body["height"] + .001:
            column += 1
            y = body["y"]
        if column >= columns or h > body["height"]:
            raise ValueError(f"svg_source_text_box_unfit:{group[0].source_block_id}")
        for p,item_height,size,offset in items:
            values = dict(x=body["x"]+column*(col_width+gap)+offset,y=y,height=item_height,font_size=size)
            original = next(b for b in group if b.source_block_id == p.block_id)
            if original.kind == "image":
                geom = original.payload.get("geometry", {})
                ratio = float(geom.get("cy",1))/max(1,float(geom.get("cx",1)))
                values["width"] = max(1,min(p.width,math.floor(item_height/max(.001,ratio))))
            placed.append(p.model_copy(update=values))
        y += h + gap
        previous_group = group
    # Source containers follow their contained content after template flow. They
    # never reserve a grid cell or restore original page coordinates.
    originals = {b.source_block_id: b for b in blocks}
    for block in (b for b in blocks if b.kind == "shape"):
        _check_local_budget()
        geom = block.payload.get("geometry") or {}
        related = []
        for p in placed:
            if originals[p.block_id].kind == "shape":
                continue
            other = originals[p.block_id].payload.get("geometry") or {}
            if all(k in geom and k in other for k in ("x", "y", "cx", "cy")):
                cx, cy = other["x"] + other["cx"] / 2, other["y"] + other["cy"] / 2
                if geom["x"] <= cx <= geom["x"] + geom["cx"] and geom["y"] <= cy <= geom["y"] + geom["cy"]:
                    related.append(p)
        if not related:
            choices = [p for p in placed if originals[p.block_id].kind != "shape"]
            if not choices:
                raise ValueError(f"svg_source_layout_candidate_unavailable:{block.source_block_id}")
            def distance(p):
                other = originals[p.block_id].payload.get("geometry") or {}
                if all(k in geom and k in other for k in ("x", "y", "cx", "cy")):
                    return ((geom["x"]+geom["cx"]/2-other["x"]-other["cx"]/2)**2
                        + (geom["y"]+geom["cy"]/2-other["y"]-other["cy"]/2)**2, originals[p.block_id].order)
                return (abs(block.order-originals[p.block_id].order), originals[p.block_id].order)
            related = [min(choices, key=distance)]
        ink_related = []
        for p in related:
            original = originals[p.block_id]
            if original.kind == "text":
                font = _font(p)
                ends = wrap_source_text(original.payload["text"],p.width,p.font_size,font)
                widths = [font.getbbox(original.payload["text"][start:end].rstrip("\r\n"))[2]*p.font_size/20
                    for start,end in _ranges(original.payload["text"],ends)]
                p = p.model_copy(update={"width":max(1,max(widths,default=1))})
            ink_related.append(p)
        x, sy = min(p.x for p in ink_related), min(p.y for p in ink_related)
        right, bottom = max(p.x+p.width for p in ink_related), max(p.y+p.height for p in ink_related)
        preset = block.payload.get("preset")
        if preset in {"ellipse","parallelogram"} or (geom.get("cx",0) <= 750000 and geom.get("cy",0) <= 750000):
            extent = min(18, bottom-sy, right-x)
            right,bottom = x+max(1,extent),sy+max(1,extent)
        elif block.semantic_role == "connector" or geom.get("cx",0) > 8*max(1,geom.get("cy",0)):
            right,bottom = x+min(80,right-x),sy+.5
        placed.append(_placement(block, template, x=x, y=sy, width=right-x, height=bottom-sy))
    return SvgLayoutPlan(source_unit_id=manifest.units[page_index].source_unit_id,
        page_digest=page_digest(manifest, page_index), width=width, height=height,
        background=template["tokens"]["background"], template_digest=template["digest"], layout_id=layout["id"],
        placements=tuple(p.model_dump(exclude={"line_ends", "cell_line_ends"}) for p in placed))


def _try_template_source_layout(manifest, page_index, style_snapshot, *, source_images,
        image_slots, min_font_size_px, width, height, max_layout_candidates=3,
        local_layout_budget_ms=1500, deadline=None, check_cancelled=None, _validated_pages=None):
    from utils.llm_calls.author_slide_svg import SvgAuthorResult
    fit_minimum_px = math.ceil(min_font_size_px * 100) / 100
    analysis_started = time.monotonic()
    if _validated_pages is not None and _validated_pages is not manifest.units:
        raise ValueError("svg_source_validated_pages_identity_mismatch")
    validated_pages = source_pages(manifest) if _validated_pages is None else _validated_pages
    template = selected_template(style_snapshot)
    validate_page_font_coverage(manifest, page_index, template)
    from utils.svg_template_fonts import measure_font
    for spec in template["fonts"].values():
        measure_font(spec["family"], str(spec["weight"]), 20)
    measure_font(template["fonts"]["table"]["family"], str(template["table"]["header_weight"]), 20)
    analysis_ms = (time.monotonic()-analysis_started)*1000
    if max_layout_candidates < 1 or local_layout_budget_ms <= 0:
        raise ValueError("svg_source_layout_budget_invalid")
    started = time.monotonic()
    limit = min(started + local_layout_budget_ms / 1000, deadline or float("inf"))
    _LOCAL_LIMIT.set(limit)
    _LOCAL_CANCEL.set(check_cancelled)
    attempts, violations, repairs = [], [], 0
    reason = "layout_candidate_unavailable"
    for layout in applicable_layouts(template,manifest,page_index)[:max_layout_candidates]:
        if check_cancelled is not None:
            check_cancelled()
        if time.monotonic() >= limit:
            reason = "layout_search_budget_exhausted"
            break
        attempts.append(layout["id"])
        for pass_index in range(3):
            if time.monotonic() >= limit:
                reason = "layout_search_budget_exhausted"
                break
            repairs += pass_index > 0
            try:
                plan = _candidate(manifest, page_index, template, layout, width, height, fit_minimum_px, pass_index)
                if pass_index == 2:
                    low, high = fit_minimum_px, min(template["fonts"]["body"]["size"],template["fonts"]["table"]["size"])
                    for _ in range(4):
                        middle = (low+high)/2
                        try:
                            larger = _candidate(manifest,page_index,template,layout,width,height,middle,pass_index)
                        except ValueError as error:
                            if "text_box_unfit" not in str(error):
                                raise
                            high = middle
                        else:
                            plan,low = larger,middle
                resolved, adjusted = resolve_source_plan(plan, manifest, page_index, min_font_size_px, template)
                _check_local_budget()
                assembled = assemble_source_svg(manifest, page_index, resolved, source_images=source_images,
                    image_slots=image_slots, min_font_size_px=min_font_size_px,
                    style_digest=style_snapshot["digest"], template_snapshot=template, _validated_pages=validated_pages)
                _check_local_budget()
                receipt = dict(contract_version="svg-static/v1", style_digest=style_snapshot["digest"],
                    source_digest=source_digest(assembled.svg_content), validation_error=None,
                    source_layout_version="svg-source-layout/v2", source_preservation=assembled.receipt,
                    authoring_path="server_template_layout", model_calls=0,
                    attempted_layout_ids=attempts, repair_count=repairs,
                    deterministic_repaired_placements=adjusted, analysis_ms=analysis_ms, elapsed_ms=(time.monotonic()-started)*1000)
                return SvgAuthorResult(assembled.svg_content, "", receipt)
            except ValueError as error:
                code = str(error)
                if code == "svg_source_layout_search_budget_exhausted":
                    reason = "layout_search_budget_exhausted"
                    break
                if not any(x in code for x in ("text_box_unfit", "boxes_overlap", "layout_candidate_unavailable")):
                    raise
                violations.append(code)
                reason = "layout_collision_unresolved" if "overlap" in code else (
                    "layout_candidate_unavailable" if "candidate_unavailable" in code else "layout_overflow_unresolved")
    return dict(escalation_reason=reason, attempted_layout_ids=attempts, repair_count=repairs,
        violations=violations[-6:], analysis_ms=analysis_ms, elapsed_ms=(time.monotonic()-started)*1000, model_calls=0,
        model_layout_allowed=any(x.get("allowed_variations", {}).get("model_layout", False) for x in template["layouts"]))


def try_template_source_layout(manifest, page_index, style_snapshot, **kwargs):
    """Isolate cancellation/deadline context, including when a candidate fails."""
    limit_token = _LOCAL_LIMIT.set(None)
    cancel_token = _LOCAL_CANCEL.set(None)
    try:
        return _try_template_source_layout(manifest, page_index, style_snapshot, **kwargs)
    finally:
        _LOCAL_LIMIT.reset(limit_token)
        _LOCAL_CANCEL.reset(cancel_token)
