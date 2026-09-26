"""Source geometry and text metrics for template layout."""

from __future__ import annotations

from dataclasses import dataclass
import statistics

from services.svg_source_layout import _ranges


def _font(placement):
    from utils.svg_template_fonts import measure_font
    return measure_font(placement.font_family, placement.font_weight, 20)


def text_metrics(font):
    ascent, descent = font.getmetrics()
    return ascent / 20, (ascent + descent) / 20 + .1


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
                        if abs(anchor["x"]-geom["x"]) <= max(anchor["cx"],geom["cx"])* \
                            .1 and separation <= max(anchor["cy"],geom["cy"])*.2:
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
            if geom["x"] <= other["x"]+other["cx"]/2 <= geom["x"]+geom["cx"] \
                and geom["y"] <= other["y"]+other["cy"]/2 <= geom["y"]+geom["cy"]:
                members.append(child.source_block_id)
        if len(members) >= 3:
            candidates.append((block,geom,members))
    for _anchor, geom, _ in candidates:
        peers = [(b,g,m) for b,g,m in candidates if abs(g["y"]-geom["y"]) <= geom["cy"]*.04
            and abs(g["cx"]-geom["cx"]) <= geom["cx"]*.05 and abs(g["cy"]-geom["cy"]) <= geom["cy"]*.05]
        peers.sort(key=lambda item:item[1]["x"])
        if len(peers) >= 3 and all(a[1]["x"]+a[1]["cx"] <= b[1]["x"] for a,b in zip(peers,peers[1:], strict=False)):
            return [dict(container_id=b.source_block_id,member_ids=m) for b,g,m in peers]
    return []
def applicable_layouts(template, manifest, page_index):
    groups = parallel_source_groups(manifest,page_index)
    layouts = [layout for layout in template["layouts"] if not layout.get("applicability",
        {}).get("requires_source_parallel_groups")
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
            if all(k in item for k in ("x","y","cx","cy")) and geom["x"] <= item["x"]+item["cx"]/ \
                2 <= geom["x"]+geom["cx"] and geom["y"] <= item["y"]+item["cy"]/2 <= geom["y"]+geom["cy"]:
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
