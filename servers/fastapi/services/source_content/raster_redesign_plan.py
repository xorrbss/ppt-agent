"""Content model and candidate layout search for raster redesign pages.

The planner never drops or rewrites content. It enumerates a bounded set of
compositions (stacked bands, text/capture split, source-column sections,
card grids, column flow), keeps only those that fit above the 9.5 pt floor,
and picks the best score.

The supporting logic lives in sibling modules and is re-exported here so
existing imports of ``services.source_content.raster_redesign_plan`` keep
working: ``raster_redesign_plan_constants`` (shared style/size constants),
``raster_redesign_plan_items`` (building ordered layout items from planned
regions), ``raster_redesign_plan_compose`` (the candidate composition
builders searched below) and ``raster_redesign_plan_validate`` (the layout
invariants checked before a composition is accepted).
"""

from __future__ import annotations

from dataclasses import dataclass

from services.source_content.raster_redesign_plan_compose import (
    _Cache,
    _build,
    _configs,
    _grid_penalty,
    _split_imbalance,
)
from services.source_content.raster_redesign_plan_constants import BODY_TEXT, FLOOR_FONT, NAVY, TABLE_FONTS, TEXT_FONTS
from services.source_content.raster_redesign_plan_items import build_items, redesign_crop_regions, region_is_crop
from services.source_content.raster_redesign_plan_validate import _overlap, _text_boxes, layout_violation
from services.source_content.raster_redesign_semantic import blocks, card_runs, has_groups, semantic_configs
from services.source_content.raster_redesign_tree import Item, Placed, image_area, place, solve

__all__ = [
    "BODY_TEXT",
    "FLOOR_FONT",
    "Item",
    "NAVY",
    "Placed",
    "Plan",
    "TABLE_FONTS",
    "TEXT_FONTS",
    "_overlap",
    "_text_boxes",
    "build_items",
    "layout_violation",
    "plan_content",
    "redesign_crop_regions",
    "region_is_crop",
]


@dataclass
class Plan:
    placements: list[Placed]
    score: float
    used_height: float
    name: str


def plan_content(items, box, *, source_size: tuple[float, float], density: str = "regular",
                 semantic: bool | None = None, accept=None) -> Plan:
    """Choose the best fitting composition for ``items`` inside ``box``.

    ``accept(placements)`` returns None for an admissible composition or the
    reason it is not (the source-coherence check). When compositions fit but
    none is admissible, raises ``raster_redesign_incoherent:<reason>``.
    """

    from services.source_content.raster_layout_composite import expand_composites, merge_composites

    x, y, w, h = box
    items = merge_composites(items, source_size)  # touching source tiles stay one picture
    cache = _Cache()
    has_text = any(item.kind == "text" for item in items)
    has_table = any(item.kind == "table" for item in items)
    single_text = sum(item.kind == "text" for item in items) == 1
    fonts = TEXT_FONTS if density != "compact" else tuple(s for s in TEXT_FONTS if s <= 16.9)
    best: Plan | None = None
    rejected = None
    # A structured page stays semantic even after every group became pixels.
    semantic = has_groups(items) if semantic is None else semantic
    configs = semantic_configs(items) if semantic else _configs(items)
    runs = card_runs(blocks(items)) if semantic else []
    for f in fonts if has_text else (TEXT_FONTS[3],):
        for tf in TABLE_FONTS if has_table else (TABLE_FONTS[0],):
            for mode, k, per_row, ratio in configs:
                if k > 1 and not has_text and not semantic:
                    continue
                node = _build(mode, k, per_row, ratio, items, box, f, tf, cache, source_size)
                t = None if node is None else solve(node, h)
                if t is None:
                    continue
                placements = place(node, 0.0, t)
                if layout_violation(placements):
                    continue  # never emit an image over text or text outside its card
                used = max((p.y + p.h for p in placements), default=0.0)
                widest = max((p.w for p in placements if p.kind == "text"), default=0.0)
                score = (
                    (3.0 * f / TEXT_FONTS[0] if has_text else 0.0)
                    + (1.0 * tf / TABLE_FONTS[0] if has_table else 0.0)
                    + 2.6 * image_area(node, t) / (w * h)
                    + 0.3 * min(1.0, used / h)
                    - (0.35 if widest / f > 50 else 0.0)
                    - (0.12 * (k - 1) if single_text and k > 1 and not semantic else 0.0)
                    + (0.35 if mode == "sections" else 0.0)
                    # Semantic cards: prefer the natural grid and roomy padding.
                    - (_grid_penalty(runs, k) + (0.05 if per_row < 14.0 else 0.0) if semantic else 0.0)
                    - (_split_imbalance(node, t, h) if mode == "semantic-split" else 0.0)
                )
                if best is None or score > best.score + 1e-9:
                    issue = accept(expand_composites(placements)) if accept is not None else None
                    if issue is not None:
                        rejected = rejected or issue
                        continue
                    best = Plan(placements, score, used, f"{mode}-k{k}-r{per_row}-{ratio}-f{f:.1f}-t{tf:.1f}")
    if best is None:
        raise ValueError(f"raster_redesign_incoherent:{rejected}" if rejected else "raster_redesign_content_overflow")
    best.placements = expand_composites(best.placements)
    for placed in best.placements:
        placed.y += y
    return best
