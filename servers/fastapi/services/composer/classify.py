"""Manifest unit -> ``ComposerSlidePlan``. Deterministic; no model call.

``classify_unit`` satisfies ``services.composer.contracts.Classifier``. It is a
thin facade: bindings live in ``classify_bindings``, furniture rules in
``classify_furniture``, grouping rules in ``classify_groups``, title selection
in ``classify_title``, plan summaries in ``classify_summary`` and shared
geometry helpers in ``classify_common``. This module only orders the rules:

1. every text block is projected and bound exactly once (``classify_bindings``);
2. furniture leaves the content pool first;
3. title and subtitle are chosen by placeholder, then by type size among
   blocks that carry words, then by position when the manifest carries no
   sizes; a large wordless or KPI-shaped block that would otherwise have won
   by size becomes a ``kpi`` component instead;
4. captions, then a cross-block process (numbered steps, arrow shapes), then
   a comparison are lifted out of the pool -- explicit numbering and arrows
   are the author's intent, column geometry alone is ambiguous, so the
   process check runs first;
5. what remains becomes list, process or paragraph components in source
   order, next to tables and captions; images are placed by occurrence.

Every rule works on a stored manifest that predates the run enrichment
(sizes, placeholder types, page size) and gets sharper when it is present.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from services.composer.classify_bindings import (
    TextBlock,
    bind_paragraphs,
    build_text_block,
    paragraph_for_span,
    table_rows,
    text_runs_for_span,
)
from services.composer.classify_common import (
    Geometry,
    PageBox,
    field,
    is_sequence,
    mapping_or_empty,
    normalized_rect,
    page_box,
    parse_geometry,
    union_geometry,
    weighted_size,
)
from services.composer.classify_furniture import classify_furniture
from services.composer.classify_groups import (
    detect_captions,
    detect_comparison,
    detect_process,
    flow_step_groups,
    group_header,
    is_arrow_flow,
    list_kind,
)
from services.composer.classify_summary import archetype_of, component_chars, is_cover, mark_supporting
from services.composer.classify_title import select_title
from services.composer.plan import ComposerComponent, ComposerSlidePlan, FurnitureKind, Paragraph


@dataclass
class _Collected:
    unit_id: str
    page: PageBox | None
    text_blocks: list[TextBlock] = dataclass_field(default_factory=list)
    tables: list[tuple[str, int, Mapping[str, Any]]] = dataclass_field(default_factory=list)
    images: list[tuple[str, int, Mapping[str, Any]]] = dataclass_field(default_factory=list)
    shapes: list[Mapping[str, Any]] = dataclass_field(default_factory=list)
    notes: list[str] = dataclass_field(default_factory=list)


def classify_unit(
    *, unit: Mapping[str, Any], slide_index: int, deck_context: Mapping[str, Any]
) -> ComposerSlidePlan:
    """Plan one slide from one manifest unit (see the module docstring for the order)."""

    collected = _collect(unit, deck_context)
    page = collected.page
    furniture: list[ComposerComponent] = []
    body: list[TextBlock] = []
    for block in collected.text_blocks:
        kind = classify_furniture(block, page, deck_context)
        if kind is None:
            body.append(block)
        else:
            furniture.append(_text_component(block, "furniture", "furniture", page, furniture_kind=kind))

    title_block, subtitle_block, kpi_ids = select_title(body, page)
    remaining = [block for block in body if block is not title_block and block is not subtitle_block]
    kpi_blocks = [block for block in remaining if block.block_id in kpi_ids]
    remaining = [block for block in remaining if block.block_id not in kpi_ids]
    image_boxes = [box for box in (parse_geometry(payload) for _, _, payload in collected.images) if box]
    caption_ids = detect_captions(remaining, image_boxes, page)
    captions = [block for block in remaining if block.block_id in caption_ids]
    remaining = [block for block in remaining if block.block_id not in caption_ids]

    placed: list[tuple[int, int, ComposerComponent]] = []
    process = detect_process(remaining, collected.shapes, page)
    if process is not None:
        groups, remaining = process
        placed.append(_grouped_component(collected.unit_id, "process", groups, page))
    else:
        columns = detect_comparison(remaining, page)
        if columns is not None:
            placed.append(_grouped_component(collected.unit_id, "comparison", columns, page))
            used = {block.block_id for column in columns for block in column}
            remaining = [block for block in remaining if block.block_id not in used]
    for block in remaining:
        placed.append((block.order, len(placed), _block_component(block, page)))
    for block in captions:
        placed.append((block.order, len(placed), _text_component(block, "caption", "caption", page)))
    for block in kpi_blocks:
        placed.append((block.order, len(placed), _text_component(block, "kpi", "body", page, emphasis="accent")))
    for block_id, order, payload in collected.tables:
        component = _table_component(block_id, payload, page, collected.notes)
        if component is not None:
            placed.append((order, len(placed), component))
    components = [component for _, _, component in sorted(placed, key=lambda item: (item[0], item[1]))]
    mark_supporting(components)

    title = _text_component(title_block, "title", "title", page) if title_block else None
    subtitle = _text_component(subtitle_block, "subtitle", "subtitle", page) if subtitle_block else None
    images = [_image_component(block_id, payload, page, collected.notes) for block_id, _, payload in collected.images]
    body_chars = sum(component_chars(component) for component in components)
    framing = [component for component in (title, subtitle, *furniture) if component is not None]
    text_chars = body_chars + sum(component_chars(component) for component in framing)
    cover = is_cover(
        title, subtitle, components, images,
        slide_index=slide_index,
        title_placeholder=title_block.placeholder_type if title_block is not None else None,
    )
    archetype = (
        "cover"
        if cover
        else archetype_of(components, images, body_chars, has_text=bool(title or subtitle or body_chars))
    )
    return ComposerSlidePlan(
        slide_index=slide_index,
        source_unit_id=collected.unit_id,
        archetype=archetype,
        title=title,
        subtitle=subtitle,
        components=components,
        images=images,
        furniture=furniture,
        text_chars=text_chars,
        notes=collected.notes,
    )


# --- Collection ------------------------------------------------------------------


def _collect(unit: Mapping[str, Any], deck_context: Mapping[str, Any]) -> _Collected:
    unit_id = str(field(unit, "source_unit_id", "sourceUnitId", default="") or "")
    if not unit_id:
        raise ValueError("source unit identity is missing")
    raw_blocks = field(unit, "blocks", default=())
    if not is_sequence(raw_blocks):
        raise ValueError("source unit blocks must be a sequence")
    blocks = sorted((mapping_or_empty(block) for block in raw_blocks), key=_block_order)
    geometries: list[Geometry] = []
    collected = _Collected(unit_id=unit_id, page=None)
    for block in blocks:
        kind = str(field(block, "kind", default="") or "")
        block_id = str(field(block, "source_block_id", "sourceBlockId", default="") or "")
        payload = mapping_or_empty(field(block, "payload", default={}))
        box = parse_geometry(payload)
        if box is not None:
            geometries.append(box)
        if kind == "text":
            text_block = build_text_block(block)
            if text_block is None:
                collected.notes.append(f"text block {block_id} has no visible text")
            else:
                collected.text_blocks.append(text_block)
        elif kind == "table":
            collected.tables.append((block_id, _block_order(block), payload))
        elif kind == "image":
            collected.images.append((block_id, _block_order(block), payload))
        elif kind == "shape":
            collected.shapes.append(block)
        elif kind == "chart":
            collected.notes.append(f"chart {block_id} not rendered: the restyle plan has no chart component")
        elif kind != "source_note":
            collected.notes.append(f"block {block_id} of kind {kind!r} ignored")
    collected.page = page_box(deck_context, geometries)
    return collected


def _block_order(block: Mapping[str, Any]) -> int:
    try:
        return int(field(block, "order", default=0) or 0)
    except (TypeError, ValueError):
        return 0


# --- Components ------------------------------------------------------------------


def _text_component(
    block: TextBlock,
    kind: str,
    role: str,
    page: PageBox | None,
    *,
    furniture_kind: FurnitureKind | None = None,
    emphasis: str = "none",
) -> ComposerComponent:
    return ComposerComponent(
        component_id=block.block_id,
        kind=kind,
        semantic_role=role,
        paragraphs=bind_paragraphs(block),
        furniture_kind=furniture_kind,
        source_rect=normalized_rect(block.geometry, page),
        source_size_pt=block.dominant_size_pt,
        emphasis=emphasis,
    )


def _block_component(block: TextBlock, page: PageBox | None) -> ComposerComponent:
    """Rules 5a, 6 and 9 for one block: list, arrow flow, else paragraph."""

    kind = list_kind(block)
    if kind is not None:
        return _text_component(block, kind, "body", page)
    if is_arrow_flow(block):
        steps = bind_paragraphs(block)
        groups, group_headers = flow_step_groups(block, steps)
        return ComposerComponent(
            component_id=block.block_id,
            kind="process",
            semantic_role="body",
            groups=groups,
            group_headers=group_headers,
            source_rect=normalized_rect(block.geometry, page),
            source_size_pt=block.dominant_size_pt,
        )
    return _text_component(block, "paragraph", "body", page)


def _grouped_component(
    unit_id: str, kind: str, groups: Sequence[Sequence[TextBlock]], page: PageBox | None
) -> tuple[int, int, ComposerComponent]:
    """A comparison or process spanning several blocks, ordered at its first block."""

    group_paragraphs: list[list[Paragraph]] = []
    headers: list[Paragraph | None] = []
    for members in groups:
        head = group_header(members)
        headers.append(bind_paragraphs(head)[0] if head is not None else None)
        group_paragraphs.append(
            [paragraph for block in members if block is not head for paragraph in bind_paragraphs(block)]
        )
    blocks = [block for members in groups for block in members]
    first_order = min(block.order for block in blocks)
    component = ComposerComponent(
        component_id=f"{unit_id}:{kind}:{first_order}",
        kind=kind,
        semantic_role="body",
        groups=group_paragraphs,
        group_headers=headers,
        source_rect=normalized_rect(union_geometry(block.geometry for block in blocks), page),
        source_size_pt=weighted_size((block.dominant_size_pt, block.char_count) for block in blocks),
    )
    return first_order, -1, component


def _table_component(
    block_id: str, payload: Mapping[str, Any], page: PageBox | None, notes: list[str]
) -> ComposerComponent | None:
    rows, _ = table_rows(block_id, payload)
    if not rows:
        notes.append(f"table {block_id} has no cells")
        return None
    if payload.get("mergedCells"):
        notes.append(f"table {block_id} has merged cells the plan grid cannot express")
    return ComposerComponent(
        component_id=block_id,
        kind="table",
        semantic_role="body",
        table_rows=rows,
        source_rect=normalized_rect(parse_geometry(payload), page),
    )


def _image_component(
    block_id: str, payload: Mapping[str, Any], page: PageBox | None, notes: list[str]
) -> ComposerComponent:
    asset = field(payload, "assetId", "asset_id")
    asset_id = str(asset) if isinstance(asset, str) and asset.strip() else None
    if asset_id is None:
        notes.append(f"image {block_id} has no asset id")
    box = parse_geometry(payload)
    aspect = box.cx / box.cy if box is not None and box.cx > 0 and box.cy > 0 else None
    return ComposerComponent(
        component_id=block_id,
        kind="image",
        semantic_role="image",
        asset_id=asset_id,
        occurrence_id=block_id,
        aspect_ratio=aspect,
        source_rect=normalized_rect(box, page),
    )


__all__ = [
    "TextBlock",
    "bind_paragraphs",
    "build_text_block",
    "classify_furniture",
    "classify_unit",
    "detect_comparison",
    "detect_process",
    "is_arrow_flow",
    "list_kind",
    "paragraph_for_span",
    "select_title",
    "table_rows",
    "text_runs_for_span",
]
