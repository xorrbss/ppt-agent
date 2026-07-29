"""Content-volume-aware layout fitting.

Deterministically computes each layout's content capacity from its json_schema,
then post-processes a (outline, structure) pair so that slides whose content
overflows their chosen layout are either re-mapped to a higher-capacity layout
of the same kind (upgrade) or split across multiple slides. No LLM call.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from constants.presentation import MAX_NUMBER_OF_SLIDES
from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.presentation_structure_model import PresentationStructureModel
from utils.schema_utils import resolve_ref

CHART_TYPE_VOCAB = {
    "bar", "line", "area", "pie", "scatter",
    "donut", "doughnut", "radar", "column", "bubble",
}

TEXT_SLACK = 1.25       # tolerate moderate overflow (render stage rephrases to fit)
SPLIT_THRESHOLD = 1.6   # only split when content far exceeds the biggest fitting layout


@dataclass
class LayoutCapacity:
    text_chars: int
    list_items: int
    has_image: bool
    has_chart: bool
    has_table: bool
    kind: str  # chart > table > image > list > text


@dataclass
class ContentVolume:
    chars: int
    bullets: int
    has_table: bool


_BULLET_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+", re.MULTILINE)
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Capacity from json_schema
# --------------------------------------------------------------------------- #
def _is_chart_container(props: dict) -> bool:
    has_chart_enum = False
    has_array_sibling = False
    for value in props.values():
        if not isinstance(value, dict):
            continue
        enum = value.get("enum")
        if enum is None and "const" in value:
            enum = [value["const"]]
        if enum and all(
            isinstance(e, str) and e.lower() in CHART_TYPE_VOCAB for e in enum
        ):
            has_chart_enum = True
        if value.get("type") == "array" or "items" in value:
            has_array_sibling = True
    return has_chart_enum and has_array_sibling


def _is_table_container(props: dict) -> bool:
    return "rows" in props and ("headers" in props or "columns" in props)


def _walk(
    node, mult: int, suppress_list: bool, root: dict
) -> Tuple[int, int, bool, bool, bool]:
    """Return (text_chars, list_items, has_image, has_chart, has_table)."""
    if not isinstance(node, dict):
        return (0, 0, False, False, False)

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        try:
            resolved = resolve_ref(root=root, ref=ref)
        except Exception:
            resolved = None
        if isinstance(resolved, dict):
            return _walk(resolved, mult, suppress_list, root)
        return (0, 0, False, False, False)

    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if isinstance(branches, list) and branches:
            results = [_walk(b, mult, suppress_list, root) for b in branches]
            return (
                max((r[0] for r in results), default=0),
                max((r[1] for r in results), default=0),
                any(r[2] for r in results),
                any(r[3] for r in results),
                any(r[4] for r in results),
            )

    all_of = node.get("allOf")
    if isinstance(all_of, list) and all_of:
        acc = [0, 0, False, False, False]
        for entry in all_of:
            r = _walk(entry, mult, suppress_list, root)
            acc[0] += r[0]
            acc[1] += r[1]
            acc[2] = acc[2] or r[2]
            acc[3] = acc[3] or r[3]
            acc[4] = acc[4] or r[4]
        return tuple(acc)  # type: ignore[return-value]

    node_type = node.get("type")
    if isinstance(node_type, list):
        node_type = next((t for t in node_type if t != "null"), None)

    props = node.get("properties")
    if node_type == "object" or isinstance(props, dict):
        props = props or {}
        has_image = "__image_url__" in props
        # Asset marker objects: count neither their url/prompt/query strings.
        if "__image_url__" in props or "__icon_url__" in props:
            return (0, 0, has_image, False, False)
        chart = _is_chart_container(props)
        table = _is_table_container(props)
        child_suppress = suppress_list or chart or table
        text, items = 0, 0
        img, cht, tbl = has_image, chart, table
        for value in props.values():
            r = _walk(value, mult, child_suppress, root)
            text += r[0]
            items += r[1]
            img = img or r[2]
            cht = cht or r[3]
            tbl = tbl or r[4]
        return (text, items, img, cht, tbl)

    items_schema = node.get("items")
    if node_type == "array" or items_schema is not None:
        n = node.get("maxItems") or node.get("minItems") or 1
        child_mult = mult * n
        items_node = items_schema
        if items_node is None:
            prefix = node.get("prefixItems")
            items_node = prefix[0] if isinstance(prefix, list) and prefix else {}
        items_is_object = isinstance(items_node, dict) and (
            items_node.get("type") == "object" or "properties" in items_node
        )
        list_items_add = child_mult if (items_is_object and not suppress_list) else 0
        r = _walk(items_node, child_mult, suppress_list, root)
        return (r[0], list_items_add + r[1], r[2], r[3], r[4])

    if node_type == "string":
        if node.get("enum") or "const" in node:
            return (0, 0, False, False, False)
        max_len = node.get("maxLength")
        return ((mult * max_len if max_len else 0), 0, False, False, False)

    return (0, 0, False, False, False)


def _kind(text: int, items: int, image: bool, chart: bool, table: bool) -> str:
    if chart:
        return "chart"
    if table:
        return "table"
    if image and items == 0:
        return "image"
    if items >= 1:
        return "list"
    return "text"


def compute_layout_capacity(json_schema: dict) -> LayoutCapacity:
    root = json_schema or {}
    text, items, image, chart, table = _walk(root, 1, False, root)
    return LayoutCapacity(
        text_chars=text,
        list_items=items,
        has_image=image,
        has_chart=chart,
        has_table=table,
        kind=_kind(text, items, image, chart, table),
    )


# --------------------------------------------------------------------------- #
# Content volume + fitting
# --------------------------------------------------------------------------- #
def compute_content_volume(content: str) -> ContentVolume:
    text = content or ""
    bullets = len(_BULLET_RE.findall(text))
    has_table = bool(_TABLE_RE.search(text)) and text.count("|") >= 4
    return ContentVolume(chars=len(text), bullets=bullets, has_table=has_table)


def _fits(vol: ContentVolume, cap: LayoutCapacity) -> bool:
    if vol.chars > int(cap.text_chars * TEXT_SLACK):
        return False
    if cap.list_items > 0 and vol.bullets > cap.list_items:
        return False
    if vol.has_table and not cap.has_table:
        return False
    return True


def _find_fitting_layout(
    vol: ContentVolume, caps: List[LayoutCapacity], cur: LayoutCapacity
) -> Optional[int]:
    candidates = [
        (c.text_chars, i)
        for i, c in enumerate(caps)
        if c.kind == cur.kind and c.text_chars >= cur.text_chars and _fits(vol, c)
    ]
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _biggest_same_kind(caps: List[LayoutCapacity], cur: LayoutCapacity) -> int:
    same = [(c.text_chars, i) for i, c in enumerate(caps) if c.kind == cur.kind]
    if not same:
        same = [(c.text_chars, i) for i, c in enumerate(caps)]
    same.sort()
    return same[-1][1]


def _split_content(
    content: str, target_cap: LayoutCapacity, max_parts: int
) -> List[str]:
    text = content or ""
    lines = text.split("\n")
    title_line = ""
    body = lines
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#"):
            title_line = ln.rstrip()
            body = lines[i + 1:]
            break

    char_budget = max(120, int(target_cap.text_chars * 0.95))
    item_budget = target_cap.list_items if target_cap.list_items > 0 else 999
    head = (title_line + "\n") if title_line else ""

    parts: List[str] = []
    cur_lines: List[str] = []
    cur_chars = len(head)
    cur_items = 0
    for ln in body:
        is_bullet = bool(_BULLET_RE.match(ln))
        ln_len = len(ln) + 1
        over = cur_chars + ln_len > char_budget or (
            is_bullet and cur_items + 1 > item_budget
        )
        if cur_lines and over and len(parts) < max_parts - 1:
            parts.append((head + "\n".join(cur_lines)).strip())
            cur_lines, cur_chars, cur_items = [], len(head), 0
        cur_lines.append(ln)
        cur_chars += ln_len
        if is_bullet:
            cur_items += 1
    if cur_lines:
        parts.append((head + "\n".join(cur_lines)).strip())
    return parts or [text]


def apply_capacity_fit(
    outline: PresentationOutlineModel,
    structure: PresentationStructureModel,
    layout: PresentationLayoutModel,
    max_slides: int = MAX_NUMBER_OF_SLIDES,
) -> Tuple[PresentationOutlineModel, PresentationStructureModel]:
    """Re-map overflowing slides to a bigger same-kind layout, or split them.

    Ordered templates keep their fixed layout sequence (no-op). Output keeps
    outline.slides and structure.slides the same length (parallel growth on split).
    """
    if layout.ordered:
        return outline, structure

    caps = [compute_layout_capacity(s.json_schema) for s in layout.slides]
    total = min(len(outline.slides), len(structure.slides))
    new_slides: List[SlideOutlineModel] = []
    new_indices: List[int] = []

    for i in range(total):
        content_slide = outline.slides[i]
        idx = structure.slides[i]
        if idx < 0 or idx >= len(caps):
            new_slides.append(content_slide)
            new_indices.append(idx)
            continue

        cap = caps[idx]
        if cap.text_chars <= 0:  # cover / pure-image layouts: never resize/split
            new_slides.append(content_slide)
            new_indices.append(idx)
            continue

        vol = compute_content_volume(content_slide.content)
        if _fits(vol, cap):
            new_slides.append(content_slide)
            new_indices.append(idx)
            continue

        upgrade_idx = _find_fitting_layout(vol, caps, cap)
        if upgrade_idx is not None:
            new_slides.append(content_slide)
            new_indices.append(upgrade_idx)
            continue

        target_idx = _biggest_same_kind(caps, cap)
        target_cap = caps[target_idx]
        remaining = total - i - 1
        room = max_slides - len(new_indices) - remaining  # extra slides still allowed
        if room >= 1 and vol.chars > int(target_cap.text_chars * SPLIT_THRESHOLD):
            parts = _split_content(
                content_slide.content, target_cap, max_parts=min(3, 1 + room)
            )
        else:
            parts = [content_slide.content]
        for part in parts:
            new_slides.append(SlideOutlineModel(content=part))
            new_indices.append(target_idx)

    return (
        PresentationOutlineModel(slides=new_slides),
        PresentationStructureModel(slides=new_indices),
    )
