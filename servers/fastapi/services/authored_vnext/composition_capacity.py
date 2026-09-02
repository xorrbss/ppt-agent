"""Deterministic same-page capacity admission for source-verbatim composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from typing import Any

from .source_render_geometry import (
    BODY_LINE_HEIGHT,
    TITLE_LINE_HEIGHT,
    flow_font_px,
)


MIN_SOURCE_VERBATIM_TEXT_SCALE = 0.70
# `_renderer_text_geometry` falls back to this column gap when a design pack
# declares no `layoutHints.columnGap`.
SOURCE_FLOW_DEFAULT_COLUMN_GAP_PX = 18.0


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _value(value: object, *names: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="json", by_alias=True))
    return {}


def slot_contract(slot: object) -> dict[str, Any]:
    raw = _mapping(slot)
    constraints = _value(slot, "constraints", default=())
    envelope = _mapping(
        _value(slot, "capacity_envelope", "capacityEnvelope", default={})
    )
    for constraint in constraints if isinstance(constraints, Sequence) else ():
        kind = str(_value(constraint, "kind", default=""))
        value = _value(constraint, "value")
        if kind == "maximum-characters":
            envelope.setdefault("maxCharacters", value)
        elif kind == "maximum-lines":
            envelope.setdefault("maxLines", value)
    envelope.setdefault(
        "maxCharacters",
        int(_value(slot, "capacity_units", "capacityUnits", default=1) or 1),
    )
    bounds = _mapping(_value(slot, "bounds", default={}))
    width = float(bounds.get("width", 0) or 0)
    height = float(bounds.get("height", 0) or 0)
    envelope.setdefault("contentArea", max(0.0, width * height))
    accepted_kinds = [
        str(item)
        for item in (
            _value(
                slot,
                "accepted_kinds",
                "acceptedKinds",
                default=("text",),
            )
            or ()
        )
    ]
    capacity_units = max(
        1,
        int(_value(slot, "capacity_units", "capacityUnits", default=1) or 1),
    )
    if "image" in accepted_kinds:
        envelope.setdefault("maxImages", capacity_units)
    if "maxNumericItems" in envelope:
        envelope.setdefault("maxNumericCards", envelope["maxNumericItems"])
    if "aspectRatioMin" in envelope:
        envelope.setdefault("minImageAspectRatio", envelope["aspectRatioMin"])
    if "aspectRatioMax" in envelope:
        envelope.setdefault("maxImageAspectRatio", envelope["aspectRatioMax"])
    handle = _mapping(_value(slot, "patch_handle", "patchHandle", default={}))
    if not handle:
        source_shape_id = str(
            _value(slot, "source_shape_id", "sourceShapeId", default="") or ""
        )
        if source_shape_id:
            handle = {
                "kind": "authored-qid",
                "elementId": source_shape_id,
                "selector": f"[data-qid='{source_shape_id}']",
                "sourceDigest": _value(
                    slot, "source_xml_digest", "sourceXmlDigest"
                ),
            }
    return {
        "slotId": str(_value(slot, "slot_id", "slotId", default="") or ""),
        "semanticRole": str(
            _value(slot, "semantic_role", "semanticRole", default="body")
            or "body"
        ),
        "acceptedKinds": accepted_kinds,
        "required": bool(_value(slot, "required", default=False)),
        "capacityEnvelope": envelope,
        "patchHandle": handle,
        "containerId": _value(slot, "container_id", "containerId"),
        "hidePolicy": _value(
            slot, "hide_policy", "hidePolicy", default="reject"
        ),
        "fallbackPolicy": _value(
            slot, "fallback_policy", "fallbackPolicy", default="reject"
        ),
        "extractionConfidence": float(
            _value(
                slot,
                "extraction_confidence",
                "extractionConfidence",
                default=1.0,
            )
            or 0.0
        ),
        "bounds": bounds,
        "textStyle": _mapping(_value(slot, "text_style", "textStyle", default={})),
        "spacing": _mapping(_value(slot, "spacing", default={})),
        "density": str(_value(slot, "density", default="medium") or "medium"),
        "constraints": [
            _mapping(item)
            for item in constraints
            if not isinstance(item, (str, bytes))
        ]
        if isinstance(constraints, Sequence)
        else [],
        "raw": raw,
    }


def executable_slots(frame: object) -> list[dict[str, Any]]:
    slots = [
        slot_contract(item)
        for item in (_value(frame, "slots", default=()) or ())
    ]
    slots = [
        item
        for item in slots
        if item["slotId"]
        and item["patchHandle"].get("kind")
        in {"authored-qid", "scene-element"}
    ]
    return sorted(slots, key=lambda item: item["slotId"])


def _rect(value: object) -> dict[str, float] | None:
    bounds = _mapping(value)
    try:
        rect = {
            "x": float(bounds.get("x", 0) or 0),
            "y": float(bounds.get("y", 0) or 0),
            "width": float(bounds.get("width", 0) or 0),
            "height": float(bounds.get("height", 0) or 0),
        }
    except (TypeError, ValueError):
        return None
    if rect["width"] <= 0 or rect["height"] <= 0:
        return None
    return rect


def _rect_area(rect: Mapping[str, float]) -> float:
    return max(0.0, rect["width"]) * max(0.0, rect["height"])


def _right(rect: Mapping[str, float]) -> float:
    return rect["x"] + rect["width"]


def _bottom(rect: Mapping[str, float]) -> float:
    return rect["y"] + rect["height"]


def _intersection_area(
    left: Mapping[str, float], right: Mapping[str, float]
) -> float:
    width = max(0.0, min(_right(left), _right(right)) - max(left["x"], right["x"]))
    height = max(
        0.0, min(_bottom(left), _bottom(right)) - max(left["y"], right["y"])
    )
    return width * height


def _union_rect(rects: Sequence[Mapping[str, float]]) -> dict[str, float]:
    x = min(item["x"] for item in rects)
    y = min(item["y"] for item in rects)
    right = max(_right(item) for item in rects)
    bottom = max(_bottom(item) for item in rects)
    return {"x": x, "y": y, "width": right - x, "height": bottom - y}


def _canvas_bounds(
    frame: object,
    slots: Sequence[Mapping[str, Any]],
    protected: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    explicit = _mapping(_value(frame, "canvas", "canvas_bounds", "canvasBounds"))
    explicit_rect = _rect(explicit)
    if explicit_rect is not None:
        return explicit_rect
    if explicit.get("width") and explicit.get("height"):
        return {
            "x": 0.0,
            "y": 0.0,
            "width": float(explicit["width"]),
            "height": float(explicit["height"]),
        }
    rects = [
        item
        for item in (
            *(_rect(slot.get("bounds")) for slot in slots),
            *(_rect(region.get("bounds")) for region in protected),
        )
        if item is not None
    ]
    if not rects:
        return {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    extent = _union_rect(rects)
    return {
        "x": 0.0,
        "y": 0.0,
        "width": max(1.0, _right(extent)),
        "height": max(1.0, _bottom(extent)),
    }


def _protected_regions(frame: object) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    editable_rects = [
        rect
        for rect in (
            _rect(slot.get("bounds")) for slot in executable_slots(frame)
        )
        if rect is not None
    ]
    for element in (_value(frame, "chrome", default=()) or ()):
        bounds = _rect(_value(element, "bounds", default={}))
        if bounds is None or _value(element, "protected", default=True) is False:
            continue
        # Compiler evidence includes structural wrapper nodes such as the
        # entire content body or card container. They remain protected in the
        # DOM digest, but are not painted obstacles when they substantially
        # contain an approved editable slot. Treating wrappers as obstacles
        # collapses every data-derived flow region to zero.
        if any(
            _intersection_area(bounds, slot_rect)
            >= min(_rect_area(bounds), _rect_area(slot_rect)) * 0.8
            and _rect_area(bounds) >= _rect_area(slot_rect) * 0.8
            for slot_rect in editable_rects
        ):
            continue
        regions.append(
            {
                "elementId": str(
                    _value(element, "element_id", "elementId", default="") or ""
                ),
                "kind": str(_value(element, "kind", default="shape") or "shape"),
                "bounds": bounds,
                "sourceDigest": str(
                    _value(
                        element,
                        "source_xml_digest",
                        "sourceXmlDigest",
                        default="",
                    )
                    or ""
                ),
            }
        )
    return sorted(
        regions,
        key=lambda item: (
            item["bounds"]["y"],
            item["bounds"]["x"],
            item["elementId"],
            item["sourceDigest"],
        ),
    )


def _role_family(slot: Mapping[str, Any]) -> str:
    handle = _mapping(slot.get("patchHandle"))
    role = " ".join(
        str(value or "").casefold()
        for value in (
            slot.get("semanticRole"),
            slot.get("slotId"),
            handle.get("elementId"),
        )
    )
    kinds = {str(item).casefold() for item in slot.get("acceptedKinds", ())}
    if "image" in kinds or any(
        token in role for token in ("image", "visual", "photo", "media", "screen")
    ):
        return "images"
    if any(token in role for token in ("title", "headline", "heading")):
        return "title"
    if any(token in role for token in ("subtitle", "eyebrow", "kicker")):
        return "subtitle"
    if kinds & {"table", "chart"} or any(
        token in role for token in ("data", "metric", "number", "stat", "table", "chart")
    ):
        return "data"
    return "body"


def _rects_are_near(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    if _intersection_area(left, right) > 0:
        return True
    horizontal_overlap = max(
        0.0, min(_right(left), _right(right)) - max(left["x"], right["x"])
    )
    vertical_overlap = max(
        0.0, min(_bottom(left), _bottom(right)) - max(left["y"], right["y"])
    )
    horizontal_gap = max(0.0, max(left["x"], right["x"]) - min(_right(left), _right(right)))
    vertical_gap = max(0.0, max(left["y"], right["y"]) - min(_bottom(left), _bottom(right)))
    horizontal_band = vertical_overlap >= min(left["height"], right["height"]) * 0.2
    vertical_band = horizontal_overlap >= min(left["width"], right["width"]) * 0.2
    return (
        horizontal_band
        and horizontal_gap <= min(left["width"], right["width"]) * 0.35
    ) or (
        vertical_band
        and vertical_gap <= min(left["height"], right["height"]) * 0.35
    )


def _spatial_components(
    slots: Sequence[Mapping[str, Any]],
) -> list[list[Mapping[str, Any]]]:
    remaining = list(slots)
    components: list[list[Mapping[str, Any]]] = []
    while remaining:
        seed = remaining.pop(0)
        component = [seed]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                candidate_rect = _rect(candidate.get("bounds"))
                if candidate_rect is not None and any(
                    member_rect is not None
                    and _rects_are_near(candidate_rect, member_rect)
                    for member_rect in (_rect(item.get("bounds")) for item in component)
                ):
                    remaining.remove(candidate)
                    component.append(candidate)
                    changed = True
        components.append(component)
    return components


def _free_rect(
    envelope: Mapping[str, float],
    member_rects: Sequence[Mapping[str, float]],
    obstacles: Sequence[Mapping[str, float]],
) -> dict[str, float] | None:
    current = dict(envelope)
    for obstacle in obstacles:
        if _intersection_area(current, obstacle) <= 0:
            continue
        candidates = [
            {
                "x": current["x"],
                "y": current["y"],
                "width": max(0.0, obstacle["x"] - current["x"]),
                "height": current["height"],
            },
            {
                "x": min(_right(current), _right(obstacle)),
                "y": current["y"],
                "width": max(0.0, _right(current) - max(current["x"], _right(obstacle))),
                "height": current["height"],
            },
            {
                "x": current["x"],
                "y": current["y"],
                "width": current["width"],
                "height": max(0.0, obstacle["y"] - current["y"]),
            },
            {
                "x": current["x"],
                "y": min(_bottom(current), _bottom(obstacle)),
                "width": current["width"],
                "height": max(0.0, _bottom(current) - max(current["y"], _bottom(obstacle))),
            },
        ]
        candidates = [item for item in candidates if _rect_area(item) > 0]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                -sum(_intersection_area(item, member) for member in member_rects),
                -_rect_area(item),
                item["y"],
                item["x"],
            )
        )
        current = candidates[0]
    return current if _rect_area(current) > 0 else None


def _positive_gaps(
    rects: Sequence[Mapping[str, float]], *, axis: str
) -> list[float]:
    """Gutters between *adjacent* tracks on one axis, in CSS ``gap`` terms.

    The median of this list becomes ``layoutHints['columnGap'|'rowGap']``, and
    the renderer emits that number verbatim as the grid gap of the region it
    paints: ``source_native_generation`` reads those hints for both the fixed
    text columns and the image lane.  ``_group_capacity`` then subtracts the
    same number before measuring one column.  A CSS gap is the distance between
    *neighbouring* tracks, so only neighbours may be measured here.  Counting
    every pair mixes the column 1 -> column 3 and column 1 -> column 4
    separations into the median and reports a gutter several times the authored
    one, which starves the rendered column width and shrinks the admitted
    characters per line by the same factor.

    Each rectangle therefore contributes at most one gap: the distance to the
    nearest rectangle that follows it on ``axis`` while sharing a band with it
    on the cross axis.  That rule needs no band clustering and stays correct on
    a ragged final row.
    """

    gaps: list[float] = []
    for left in rects:
        nearest: float | None = None
        for right in rects:
            if right is left:
                continue
            if axis == "x":
                overlap = max(
                    0.0,
                    min(_bottom(left), _bottom(right)) - max(left["y"], right["y"]),
                )
                gap = right["x"] - _right(left)
            else:
                overlap = max(
                    0.0,
                    min(_right(left), _right(right)) - max(left["x"], right["x"]),
                )
                gap = right["y"] - _bottom(left)
            if overlap > 0 and gap > 0 and (nearest is None or gap < nearest):
                nearest = gap
        if nearest is not None:
            gaps.append(nearest)
    return sorted(gaps)


def _band_count(rects: Sequence[Mapping[str, float]], *, axis: str) -> int:
    intervals = sorted(
        (
            (item["x"], _right(item))
            if axis == "x"
            else (item["y"], _bottom(item))
        )
        for item in rects
    )
    bands: list[list[float]] = []
    for start, end in intervals:
        overlapping = next(
            (band for band in bands if start < band[1] and end > band[0]), None
        )
        if overlapping is None:
            bands.append([start, end])
        else:
            overlapping[0] = min(overlapping[0], start)
            overlapping[1] = max(overlapping[1], end)
    return max(1, len(bands))


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _primary_mapping(
    members: Sequence[Mapping[str, Any]], field: str
) -> dict[str, Any]:
    weighted: dict[str, tuple[float, dict[str, Any]]] = {}
    for member in members:
        value = _mapping(member.get(field))
        if not value:
            continue
        digest = canonical_digest(value)
        area = _rect_area(_rect(member.get("bounds")) or {"x": 0, "y": 0, "width": 1, "height": 1})
        total, _ = weighted.get(digest, (0.0, value))
        weighted[digest] = (total + area, value)
    if not weighted:
        return {}
    return max(weighted.items(), key=lambda item: (item[1][0], item[0]))[1][1]


def _inset(spacing: Mapping[str, Any] | None, name: str) -> float:
    if not isinstance(spacing, Mapping):
        return 0.0
    value = spacing.get(name)
    if isinstance(value, bool):
        return 0.0
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return 0.0
    return resolved if math.isfinite(resolved) else 0.0


def _group_capacity(
    members: Sequence[Mapping[str, Any]],
    region: Mapping[str, float],
    *,
    role: str,
    columns: int = 1,
    spacing: Mapping[str, Any] | None = None,
    column_gap: float = SOURCE_FLOW_DEFAULT_COLUMN_GAP_PX,
) -> dict[str, Any]:
    envelopes = [dict(member.get("capacityEnvelope") or {}) for member in members]
    content_area = _rect_area(region)
    if role == "images":
        minimums = [
            float(value)
            for envelope in envelopes
            for value in (envelope.get("minImageAspectRatio"),)
            if value not in (None, "")
        ]
        maximums = [
            float(value)
            for envelope in envelopes
            for value in (envelope.get("maxImageAspectRatio"),)
            if value not in (None, "")
        ]
        # An image region carries exactly the picture budget the Design Pack
        # declared for the merged placeholders.  Merging placeholders widens
        # the region but does not create new authored picture positions, and
        # the renderer sizes a picture from its own aspect ratio rather than
        # tiling the region, so no area-derived floor may raise this budget.
        # A geometric floor here silently admits pictures that the region can
        # never lay out, which is content loss behind `overflow:hidden` instead
        # of the fail-closed rejection this module contracts for.
        declared_capacity = sum(
            max(1, int(envelope.get("maxImages", 1) or 1))
            for envelope in envelopes
        )
        return {
            "measurementBasis": "design-pack-derived",
            "contentArea": content_area,
            "maxImages": declared_capacity,
            **({"minImageAspectRatio": min(minimums)} if minimums else {}),
            **({"maxImageAspectRatio": max(maximums)} if maximums else {}),
        }
    maximum_characters = sum(
        max(1, int(envelope.get("maxCharacters", envelope.get("characters", 1)) or 1))
        for envelope in envelopes
    )
    maximum_lines = sum(
        max(1, int(envelope.get("maxLines", 1) or 1)) for envelope in envelopes
    )
    primary_style = _primary_mapping(members, "textStyle")
    declared_font_px = float(primary_style.get("fontSize", 0) or 0)
    # Mirror the renderer's resolved slot typography: the title role floors at
    # 32px and defaults to 40px, every other role floors at the 12pt readable
    # body floor and defaults to 22px, and only the title role renders at a
    # 1.18 line height.
    font_px = flow_font_px(declared_font_px, role=role, adaptive_scale=1.0)
    line_height = TITLE_LINE_HEIGHT if role == "title" else BODY_LINE_HEIGHT
    resolved_columns = max(1, min(4, int(columns or 1)))
    # The renderer writes the region bounds as a border-box with the spacing
    # insets as padding, so text only ever flows inside the content box.
    content_width = max(
        1.0,
        float(region["width"])
        - max(0.0, _inset(spacing, "insetLeft"))
        - max(0.0, _inset(spacing, "insetRight")),
    )
    content_height = max(
        1.0,
        float(region["height"])
        - max(0.0, _inset(spacing, "insetTop"))
        - max(0.0, _inset(spacing, "insetBottom")),
    )
    column_width = max(
        1.0,
        (content_width - max(0.0, column_gap) * (resolved_columns - 1))
        / resolved_columns,
    )
    characters_per_line = max(
        4, int(column_width / max(1.0, font_px * 0.70))
    )
    geometry_lines = max(
        1,
        resolved_columns
        * int(content_height / max(1.0, font_px * line_height)),
    )
    maximum_lines = max(maximum_lines, geometry_lines)
    maximum_characters = max(
        maximum_characters, characters_per_line * maximum_lines
    )
    member_area = sum(
        max(1.0, float(envelope.get("contentArea", 0) or 0))
        for envelope in envelopes
    )
    character_area = member_area / max(1, maximum_characters)
    return {
        "measurementBasis": "design-pack-derived",
        "contentArea": content_area,
        "maxCharacters": maximum_characters,
        "maxLines": maximum_lines,
        "maxImages": max(1, int(content_area // (120.0 * 90.0))),
        "charactersPerLine": characters_per_line,
        "characterArea": character_area,
        "minimumTextScale": MIN_SOURCE_VERBATIM_TEXT_SCALE,
    }


def _component_container_flow_slots(
    frame: object,
    slots: Sequence[Mapping[str, Any]],
    resolved: Sequence[Mapping[str, Any]],
    canvas: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Retain heterogeneous pack surfaces and annotate their component graph.

    The compiler records each editable slot's exact container plus the parent
    link, bounds and visual style of protected containers.  Those relationships
    describe reusable component surfaces without requiring a frame identifier
    or selector-name convention.  In particular, a large common ancestor is
    not itself evidence that title, cards, images and footer form one text flow.
    """

    chrome = [
        item
        for item in (_value(frame, "chrome", default=()) or ())
        if _value(item, "container_id", "containerId")
    ]
    if not chrome:
        return [dict(item) for item in resolved]
    by_selector = {
        str(_value(item, "selector", default="") or ""): item
        for item in chrome
        if str(_value(item, "selector", default="") or "")
    }
    if not by_selector:
        return [dict(item) for item in resolved]

    active = [item for item in resolved if item.get("sourceFlowLayout")]
    if not active:
        return [dict(item) for item in resolved]

    canvas_width = max(1.0, float(canvas["width"]))
    canvas_height = max(1.0, float(canvas["height"]))

    def container(item: Mapping[str, Any]) -> Mapping[str, Any]:
        return by_selector.get(str(item.get("containerId") or ""), {})

    def parent_id(item: Mapping[str, Any]) -> str:
        owner = container(item)
        return str(_value(owner, "container_id", "containerId", default="") or "")

    def style_features(item: Mapping[str, Any]) -> dict[str, Any]:
        owner = container(item)
        text_style = _mapping(item.get("textStyle")) or _mapping(
            _value(owner, "text_style", "textStyle", default={})
        )
        spacing = _mapping(item.get("spacing")) or _mapping(
            _value(owner, "spacing", default={})
        )
        paint = _mapping(_value(owner, "paint", default={}))
        return {
            "fontSize": float(text_style.get("fontSize", 0) or 0),
            "fontWeight": int(text_style.get("fontWeight", 0) or 0),
            "paintDigest": canonical_digest(paint) if paint else "",
            "spacingDigest": canonical_digest(spacing) if spacing else "",
        }

    def normalized_bounds(item: Mapping[str, Any]) -> dict[str, float]:
        rect = _rect(item.get("bounds")) or dict(canvas)
        return {
            "x": (rect["x"] - canvas["x"]) / canvas_width,
            "y": (rect["y"] - canvas["y"]) / canvas_height,
            "width": rect["width"] / canvas_width,
            "height": rect["height"] / canvas_height,
        }

    def kind_family(item: Mapping[str, Any]) -> str:
        kinds = {str(value).casefold() for value in item.get("acceptedKinds", ())}
        if "image" in kinds and kinds <= {"image"}:
            return "images"
        if kinds & {"table", "chart", "metric", "number", "data"}:
            return "data"
        return "text"

    styles = {str(item["slotId"]): style_features(item) for item in active}
    normalized = {str(item["slotId"]): normalized_bounds(item) for item in active}
    text_fonts = sorted(
        feature["fontSize"]
        for item in active
        for feature in (styles[str(item["slotId"])],)
        if kind_family(item) == "text" and feature["fontSize"] > 0
    )
    median_font = _median(text_fonts) or 16.0

    siblings_by_parent: dict[str, list[Mapping[str, Any]]] = {}
    for item in active:
        parent = parent_id(item)
        if parent:
            siblings_by_parent.setdefault(parent, []).append(item)

    def comparable_siblings(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        slot_id = str(item["slotId"])
        rect = normalized[slot_id]
        feature = styles[slot_id]
        family = kind_family(item)
        comparable: list[Mapping[str, Any]] = []
        for sibling in siblings_by_parent.get(parent_id(item), ()):
            sibling_id = str(sibling["slotId"])
            sibling_rect = normalized[sibling_id]
            sibling_feature = styles[sibling_id]
            if kind_family(sibling) != family:
                continue
            width_ratio = max(rect["width"], sibling_rect["width"]) / max(
                0.001, min(rect["width"], sibling_rect["width"])
            )
            height_ratio = max(rect["height"], sibling_rect["height"]) / max(
                0.001, min(rect["height"], sibling_rect["height"])
            )
            font_values = [
                value
                for value in (feature["fontSize"], sibling_feature["fontSize"])
                if value > 0
            ]
            font_ratio = (
                max(font_values) / min(font_values) if len(font_values) == 2 else 1.0
            )
            paint_compatible = (
                not feature["paintDigest"]
                or not sibling_feature["paintDigest"]
                or feature["paintDigest"] == sibling_feature["paintDigest"]
            )
            spacing_compatible = (
                not feature["spacingDigest"]
                or not sibling_feature["spacingDigest"]
                or feature["spacingDigest"] == sibling_feature["spacingDigest"]
            )
            if (
                width_ratio <= 1.45
                and height_ratio <= 1.65
                and font_ratio <= 1.50
                and paint_compatible
                and spacing_compatible
            ):
                comparable.append(sibling)
        return sorted(comparable, key=lambda value: str(value["slotId"]))

    def component_kind(item: Mapping[str, Any]) -> str:
        slot_id = str(item["slotId"])
        bounds = normalized[slot_id]
        feature = styles[slot_id]
        role = str(item.get("sourceFlowRole") or "body")
        family = kind_family(item)
        siblings = comparable_siblings(item)
        repeated = len(siblings) >= 2
        if family == "images" or role == "images":
            return "evidenceGallery"
        if family == "data" or role == "data":
            return "tableMatrix" if repeated and len(siblings) >= 6 else "dataGrid"
        if role == "title" or (
            bounds["y"] <= 0.22
            and bounds["width"] >= 0.34
            and feature["fontSize"] >= median_font * 1.30
        ):
            return "titleBand"
        if role == "subtitle":
            return "introBand"
        if (
            bounds["y"] + bounds["height"] >= 0.90
            and bounds["height"] <= 0.12
            and bounds["width"] >= 0.18
        ):
            return "footerBand"
        if repeated:
            if len(siblings) >= 6 and bounds["height"] <= 0.13:
                return "tableMatrix"
            return "cardGrid"
        if (
            bounds["y"] <= 0.44
            and bounds["width"] >= 0.42
            and bounds["height"] <= 0.22
        ):
            return "introBand"
        return "bodyFlow"

    output: list[dict[str, Any]] = []
    for original in resolved:
        if not original.get("sourceFlowLayout"):
            output.append(dict(original))
            continue
        item = dict(original)
        slot_id = str(item["slotId"])
        component = component_kind(item)
        siblings = comparable_siblings(item)
        sibling_ids = [str(value["slotId"]) for value in siblings]
        group_id = "source-component:" + canonical_digest(
            {
                "parentContainerId": parent_id(item),
                "componentKind": component,
                "surfaces": sibling_ids or [slot_id],
            }
        ).split(":", 1)[1][:20]
        layout_hints = dict(item.get("layoutHints") or {})
        layout_hints.update(
            {
                "componentKind": component,
                "componentGroupId": group_id,
                "normalizedBounds": normalized[slot_id],
                "siblingSurfaceCount": len(sibling_ids),
            }
        )
        item.update(
            {
                "sourceFlowSurface": "component-container",
                "sourceFlowContainerGraphVersion": "v2",
                "sourceFlowComponentKind": component,
                "sourceFlowComponentGroupId": group_id,
                "sourceFlowParentContainerId": parent_id(item),
                "sourceFlowSiblingSurfaceIds": sibling_ids,
                "sourceFlowContainerFeatures": styles[slot_id],
                "layoutHints": layout_hints,
            }
        )
        output.append(item)
    return _promote_component_content_canvas(
        _coalesce_component_surface_groups(output)
    )


def _coalesce_component_surface_groups(
    slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Promote only homogeneous sibling components to their shared surface.

    A repeated card, evidence, data, or table component needs one bounded canvas
    for its internal grid.  The promotion is deliberately narrower than the old
    frame-wide body surface: every member must share the same pack-derived
    component kind and component-group digest, and title/intro/footer/body
    surfaces are never absorbed.
    """

    promotable = {"cardGrid", "evidenceGallery", "dataGrid", "tableMatrix"}
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for item in slots:
        component = str(item.get("sourceFlowComponentKind") or "")
        group_id = str(item.get("sourceFlowComponentGroupId") or "")
        if (
            item.get("sourceFlowLayout")
            and component in promotable
            and group_id
        ):
            groups.setdefault((component, group_id), []).append(item)

    replacements: dict[str, dict[str, Any]] = {}
    suppressed: dict[str, str] = {}
    for (component, group_id), members in sorted(groups.items()):
        unique_members = {
            str(item.get("slotId") or ""): item for item in members
        }
        members = [unique_members[key] for key in sorted(unique_members) if key]
        if len(members) < 2:
            continue
        rects = [
            rect
            for rect in (_rect(item.get("bounds")) for item in members)
            if rect is not None
        ]
        if len(rects) != len(members):
            continue
        region = _union_rect(rects)
        # Do not turn a decorative row of tiny badges into a false content
        # canvas. The group must occupy a material authored region.
        canvas = _rect(members[0].get("canvasBounds"))
        if canvas is not None and _rect_area(region) < _rect_area(canvas) * 0.025:
            continue
        anchor = max(
            members,
            key=lambda item: (
                float(item.get("extractionConfidence", 0) or 0),
                _rect_area(_rect(item.get("bounds")) or region),
                str(item.get("slotId") or ""),
            ),
        )
        accepted = sorted(
            {
                str(kind)
                for item in members
                for kind in item.get("acceptedKinds", ())
                if str(kind)
            }
        )
        role = (
            "images"
            if component == "evidenceGallery" and accepted == ["image"]
            else "data"
            if component in {"dataGrid", "tableMatrix"}
            else "body"
        )
        columns = max(1, _band_count(rects, axis="x"))
        rows = max(1, _band_count(rects, axis="y"))
        member_slot_ids = sorted(
            {
                str(slot_id)
                for item in members
                for slot_id in (
                    item.get("sourceFlowMemberSlotIds")
                    or (item.get("slotId"),)
                )
                if str(slot_id or "")
            }
        )
        promoted = dict(anchor)
        layout_hints = dict(promoted.get("layoutHints") or {})
        layout_hints.update(
            {
                "componentKind": component,
                "componentGroupId": group_id,
                "flowAxis": (
                    "grid" if columns > 1 and rows > 1 else
                    "horizontal" if columns > 1 else "vertical"
                ),
                "columnCount": columns,
                "rowCount": rows,
                "columnGap": _median(_positive_gaps(rects, axis="x")),
                "rowGap": _median(_positive_gaps(rects, axis="y")),
                "memberCount": len(member_slot_ids),
                "preserveUnitBoundary": True,
                "overflowPolicy": "same-page-fail-before-provider",
            }
        )
        component_spacing = {
            "insetLeft": 8.0,
            "insetTop": 8.0,
            "insetRight": 8.0,
            "insetBottom": 8.0,
        }
        promoted.update(
            {
                "semanticRole": f"source-flow-{role}",
                "acceptedKinds": accepted or ["text"],
                "required": any(bool(item.get("required")) for item in members),
                "bounds": region,
                "capacityEnvelope": _group_capacity(
                    members,
                    region,
                    role=role,
                    columns=columns,
                    spacing=component_spacing,
                    column_gap=float(layout_hints["columnGap"]),
                ),
                "textStyle": _primary_mapping(members, "textStyle"),
                "spacing": component_spacing,
                "sourceFlowLayout": "images" if role == "images" else "text",
                "sourceFlowRole": role,
                "sourceFlowSurface": "component-group",
                "sourceFlowMemberSlotIds": member_slot_ids,
                "sourceFlowMemberBounds": rects,
                "layoutHints": layout_hints,
            }
        )
        anchor_id = str(anchor.get("slotId") or "")
        replacements[anchor_id] = promoted
        for item in members:
            slot_id = str(item.get("slotId") or "")
            if slot_id and slot_id != anchor_id:
                suppressed[slot_id] = group_id

    output: list[dict[str, Any]] = []
    for original in slots:
        slot_id = str(original.get("slotId") or "")
        if slot_id in replacements:
            output.append(replacements[slot_id])
            continue
        if slot_id in suppressed:
            item = dict(original)
            item["acceptedKinds"] = []
            item["required"] = False
            item.pop("sourceFlowLayout", None)
            item["sourceFlowSuppressedBy"] = suppressed[slot_id]
            output.append(item)
            continue
        output.append(dict(original))
    return _promote_chrome_lanes(output)


def _promote_chrome_lanes(
    slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Coalesce pack-derived header and footer leaves into two atomic lanes."""

    active = [
        item
        for item in slots
        if item.get("sourceFlowLayout") and item.get("acceptedKinds")
    ]
    if not active:
        return [dict(item) for item in slots]
    canvas = _rect(active[0].get("canvasBounds"))

    def lane_members(lane: str) -> list[Mapping[str, Any]]:
        values: list[Mapping[str, Any]] = []
        for item in active:
            component = str(item.get("sourceFlowComponentKind") or "")
            rect = _rect(item.get("bounds"))
            if rect is None:
                continue
            if lane == "footer":
                if component == "footerBand":
                    values.append(item)
                continue
            if component not in {"titleBand", "introBand"}:
                continue
            if canvas is None or (
                rect["y"] <= canvas["y"] + canvas["height"] * 0.28
                and rect["width"] >= canvas["width"] * 0.18
            ):
                values.append(item)
        return values

    current = [dict(item) for item in slots]
    for lane in ("header", "footer"):
        members = lane_members(lane)
        rects = [
            rect
            for rect in (_rect(item.get("bounds")) for item in members)
            if rect is not None
        ]
        if len(members) < 2 or len(rects) != len(members):
            continue
        region = _union_rect(rects)
        if lane == "header":
            member_ids_for_region = {
                str(item.get("slotId") or "") for item in members
            }
            following_tops = [
                rect["y"]
                for item in active
                if str(item.get("slotId") or "") not in member_ids_for_region
                and str(item.get("sourceFlowComponentKind") or "")
                != "footerBand"
                for rect in (_rect(item.get("bounds")),)
                if rect is not None and rect["y"] >= _bottom(region)
            ]
            available_bottom = min(following_tops, default=_bottom(region)) - 4.0
            if available_bottom > _bottom(region):
                region = {
                    **region,
                    "height": available_bottom - region["y"],
                }
        anchor = max(
            members,
            key=lambda item: (
                _rect_area(_rect(item.get("bounds")) or region),
                float(item.get("extractionConfidence", 0) or 0),
                str(item.get("slotId") or ""),
            ),
        )
        anchor_id = str(anchor.get("slotId") or "")
        member_ids = {str(item.get("slotId") or "") for item in members}
        lane_id = f"source-{lane}-lane:" + canonical_digest(
            {"bounds": region, "members": sorted(member_ids)}
        ).split(":", 1)[1][:20]
        promoted = dict(anchor)
        lane_spacing = {
            "insetLeft": 8.0,
            "insetTop": 4.0,
            "insetRight": 8.0,
            "insetBottom": 4.0,
        }
        promoted.update(
            {
                "semanticRole": (
                    "source-flow-title" if lane == "header" else "source-flow-body"
                ),
                "acceptedKinds": ["text"],
                "required": False,
                "bounds": region,
                "capacityEnvelope": _group_capacity(
                    members,
                    region,
                    role="title" if lane == "header" else "body",
                    columns=1,
                    spacing=lane_spacing,
                ),
                "textStyle": _primary_mapping(members, "textStyle"),
                "spacing": lane_spacing,
                "sourceFlowLayout": "text",
                "sourceFlowRole": "title" if lane == "header" else "body",
                "sourceFlowSurface": f"component-{lane}-lane",
                "sourceFlowComponentKind": (
                    "headerCanvas" if lane == "header" else "footerBand"
                ),
                "sourceFlowComponentGroupId": lane_id,
                "sourceFlowMemberSlotIds": sorted(
                    {
                        str(slot_id)
                        for item in members
                        for slot_id in (
                            item.get("sourceFlowMemberSlotIds")
                            or (item.get("slotId"),)
                        )
                        if str(slot_id or "")
                    }
                ),
                "sourceFlowMemberBounds": rects,
                "layoutHints": {
                    "componentKind": (
                        "headerCanvas" if lane == "header" else "footerBand"
                    ),
                    "flowAxis": "vertical" if lane == "header" else "horizontal",
                    "columnCount": 1,
                    "rowCount": 1,
                    "preserveUnitBoundary": True,
                    "overflowPolicy": "same-page-fail-before-provider",
                },
            }
        )
        replaced: list[dict[str, Any]] = []
        for item in current:
            slot_id = str(item.get("slotId") or "")
            if slot_id == anchor_id:
                replaced.append(promoted)
            elif slot_id in member_ids:
                suppressed = dict(item)
                suppressed["acceptedKinds"] = []
                suppressed["required"] = False
                suppressed.pop("sourceFlowLayout", None)
                suppressed["sourceFlowSuppressedBy"] = lane_id
                replaced.append(suppressed)
            else:
                replaced.append(item)
        current = replaced
    return current


def _promote_component_content_canvas(
    slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expose one body canvas while retaining its pack-derived surface graph.

    The renderer needs a material area for a table or readable evidence grid,
    but title, intro and footer must remain independent.  This canvas therefore
    owns only non-chrome content components and carries their exact surface
    graph for component-aware rendering; it is not the former flat body flow.
    """

    active = [
        item
        for item in slots
        if item.get("sourceFlowLayout") and item.get("acceptedKinds")
    ]
    if not active:
        return [dict(item) for item in slots]
    canvas = _rect(active[0].get("canvasBounds"))

    def is_header_lane(item: Mapping[str, Any]) -> bool:
        component = str(item.get("sourceFlowComponentKind") or "")
        rect = _rect(item.get("bounds"))
        if component not in {"titleBand", "introBand", "headerCanvas"} or rect is None:
            return False
        if canvas is None:
            return True
        return (
            rect["y"] <= canvas["y"] + canvas["height"] * 0.28
            and rect["width"] >= canvas["width"] * 0.18
        )

    members = [
        item
        for item in active
        if str(item.get("sourceFlowComponentKind") or "") != "footerBand"
        and not is_header_lane(item)
    ]
    rects = [
        rect
        for rect in (_rect(item.get("bounds")) for item in members)
        if rect is not None
    ]
    if len(members) < 3 or len(rects) != len(members):
        return [dict(item) for item in slots]
    region = _union_rect(rects)
    header_rects = [
        rect
        for item in active
        if str(item.get("sourceFlowComponentKind") or "") == "headerCanvas"
        for rect in (_rect(item.get("bounds")),)
        if rect is not None
    ]
    footer_rects = [
        rect
        for item in active
        if str(item.get("sourceFlowComponentKind") or "") == "footerBand"
        for rect in (_rect(item.get("bounds")),)
        if rect is not None
    ]
    content_top = max(
        region["y"],
        max((_bottom(rect) + 4.0 for rect in header_rects), default=region["y"]),
    )
    content_bottom = min(
        _bottom(region),
        min((rect["y"] - 4.0 for rect in footer_rects), default=_bottom(region)),
    )
    if content_bottom <= content_top:
        return [dict(item) for item in slots]
    wide_rects = [
        rect
        for rect in (*header_rects, *rects)
        if canvas is None or rect["width"] >= canvas["width"] * 0.50
    ]
    content_left = min((rect["x"] for rect in wide_rects), default=region["x"])
    content_right = max(
        (_right(rect) for rect in wide_rects), default=_right(region)
    )
    if canvas is not None:
        content_left = max(canvas["x"], content_left)
        content_right = min(_right(canvas), content_right)
    region = {
        "x": content_left,
        "y": content_top,
        "width": max(1.0, content_right - content_left),
        "height": content_bottom - content_top,
    }
    if canvas is not None and _rect_area(region) < _rect_area(canvas) * 0.22:
        return [dict(item) for item in slots]
    anchor = max(
        members,
        key=lambda item: (
            _rect_area(_rect(item.get("bounds")) or region),
            float(item.get("extractionConfidence", 0) or 0),
            str(item.get("slotId") or ""),
        ),
    )
    member_ids = {str(item.get("slotId") or "") for item in members}
    component_surfaces: list[dict[str, Any]] = []
    for item in sorted(members, key=lambda value: str(value.get("slotId") or "")):
        item_rect = _rect(item.get("bounds"))
        if item_rect is None or _intersection_area(item_rect, region) <= 0:
            continue
        clipped_left = max(item_rect["x"], region["x"])
        clipped_top = max(item_rect["y"], region["y"])
        clipped_right = min(_right(item_rect), _right(region))
        clipped_bottom = min(_bottom(item_rect), _bottom(region))
        relative_bounds = {
            "x": clipped_left - region["x"],
            "y": clipped_top - region["y"],
            "width": max(0.0, clipped_right - clipped_left),
            "height": max(0.0, clipped_bottom - clipped_top),
        }
        component_surfaces.append(
            {
                "componentKind": str(
                    item.get("sourceFlowComponentKind") or "bodyFlow"
                ),
                "componentGroupId": str(
                    item.get("sourceFlowComponentGroupId") or ""
                ),
                "slotId": str(item.get("slotId") or ""),
                "memberSlotIds": list(item.get("sourceFlowMemberSlotIds") or ()),
                "sourceFlowRole": str(item.get("sourceFlowRole") or "body"),
                "bounds": dict(item_rect),
                "relativeBounds": relative_bounds,
                "acceptedKinds": list(item.get("acceptedKinds") or ()),
                "textStyle": dict(_mapping(item.get("textStyle"))),
                "spacing": dict(_mapping(item.get("spacing"))),
                "layoutHints": dict(_mapping(item.get("layoutHints"))),
                "capacityEnvelope": dict(_mapping(item.get("capacityEnvelope"))),
            }
        )
    if not component_surfaces:
        return [dict(item) for item in slots]
    graph_digest = canonical_digest(component_surfaces)
    promoted = dict(anchor)
    hints = dict(promoted.get("layoutHints") or {})
    aspect = region["width"] / max(1.0, region["height"])
    hints.update(
        {
            "componentKind": "contentCanvas",
            "componentGraphDigest": graph_digest,
            "flowAxis": "grid",
            "columnCount": 3 if aspect >= 2.1 else 2,
            "rowCount": 1,
            "columnGap": 16.0,
            "rowGap": 16.0,
            "memberCount": len(component_surfaces),
            "preserveUnitBoundary": True,
            "overflowPolicy": "same-page-fail-before-provider",
        }
    )
    accepted = sorted(
        {
            str(kind)
            for item in members
            for kind in item.get("acceptedKinds", ())
            if str(kind)
        }
        | {"image", "text"}
    )
    canvas_spacing = {
        "insetLeft": 0.0,
        "insetTop": 0.0,
        "insetRight": 0.0,
        "insetBottom": 0.0,
    }
    promoted.update(
        {
            "semanticRole": "source-flow-body",
            "acceptedKinds": accepted,
            "required": False,
            "bounds": region,
            "capacityEnvelope": _group_capacity(
                members,
                region,
                role="body",
                columns=int(hints["columnCount"]),
                spacing=canvas_spacing,
                column_gap=float(hints["columnGap"]),
            ),
            "textStyle": _primary_mapping(members, "textStyle"),
            "spacing": canvas_spacing,
            "sourceFlowLayout": "components",
            "sourceFlowRole": "body",
            "sourceFlowSurface": "component-content-canvas",
            "sourceFlowComponentKind": "contentCanvas",
            "sourceFlowComponentGroupId": "source-content-canvas:" + graph_digest.split(":", 1)[1][:20],
            "sourceFlowComponentSurfaces": component_surfaces,
            "sourceFlowComponentGraphDigest": graph_digest,
            "sourceFlowMemberSlotIds": sorted(
                {
                    str(slot_id)
                    for item in members
                    for slot_id in (
                        item.get("sourceFlowMemberSlotIds")
                        or (item.get("slotId"),)
                    )
                    if str(slot_id or "")
                }
            ),
            "sourceFlowMemberBounds": rects,
            "layoutHints": hints,
        }
    )
    anchor_id = str(anchor.get("slotId") or "")
    output: list[dict[str, Any]] = []
    for original in slots:
        slot_id = str(original.get("slotId") or "")
        if slot_id == anchor_id:
            output.append(promoted)
        elif slot_id in member_ids:
            item = dict(original)
            item["acceptedKinds"] = []
            item["required"] = False
            item.pop("sourceFlowLayout", None)
            item["sourceFlowSuppressedBy"] = promoted[
                "sourceFlowComponentGroupId"
            ]
            output.append(item)
        else:
            output.append(dict(original))
    return output


def source_flow_slots(frame: object) -> list[dict[str, Any]]:
    """Derive bounded source-flow regions only from the registered Design Pack.

    Each sealed source unit remains atomic.  Adjacent same-role slots can form a
    flow region, but the region is clipped around protected chrome and other
    editable role groups.  One exact existing patch handle anchors each region;
    all other handles remain in the result solely so template copy can be
    cleared without inventing a selector or template-specific branch.
    """

    slots = executable_slots(frame)
    if not slots:
        return []
    protected = _protected_regions(frame)
    canvas = _canvas_bounds(frame, slots, protected)
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for slot in slots:
        if _rect(slot.get("bounds")) is None:
            continue
        role = _role_family(slot)
        container_id = str(slot.get("containerId") or slot.get("slotId") or "")
        grouped.setdefault((role, container_id), []).append(slot)
    components: list[tuple[str, str, list[Mapping[str, Any]]]] = []
    for (role, container_id), members in sorted(
        grouped.items(), key=lambda item: item[0]
    ):
        ordered_members = sorted(members, key=lambda item: str(item["slotId"]))
        # A semantic role may repeat on visually different panels.  Only slots
        # owned by the same Design Pack container can flow together; crossing
        # this boundary loses the panel's paint/typography context and can put
        # dark text on dark retained chrome.  The rule is entirely pack-derived
        # and remains independent of template identity.
        role_components = [ordered_members]
        for component in role_components:
            components.append((role, container_id, component))
    member_group = {
        str(member["slotId"]): (role, container_id)
        for role, container_id, members in components
        for member in members
    }
    active_by_slot: dict[str, dict[str, Any]] = {}
    suppressed_by_slot: dict[str, str] = {}
    pending_components = list(components)
    while pending_components:
        role, container_id, members = pending_components.pop(0)
        member_rects = [
            rect
            for rect in (_rect(member.get("bounds")) for member in members)
            if rect is not None
        ]
        if not member_rects:
            continue
        envelope = _union_rect(member_rects)
        other_member_rects = [
            rect
            for slot in slots
            if member_group.get(str(slot["slotId"])) != (role, container_id)
            for rect in (_rect(slot.get("bounds")),)
            if rect is not None and _intersection_area(envelope, rect) > 0
        ]
        canvas_area = _rect_area(canvas)
        constraining_chrome = [
            region
            for region in protected
            if region["kind"] != "background"
            and _rect_area(region["bounds"]) < canvas_area * 0.8
            and _intersection_area(envelope, region["bounds"]) > 0
        ]
        region = _free_rect(
            envelope,
            member_rects,
            [
                *(item["bounds"] for item in constraining_chrome),
                *other_member_rects,
            ],
        )
        if region is None:
            continue
        included_members = [
            member
            for member in members
            for member_rect in (_rect(member.get("bounds")),)
            if member_rect is not None
            and _intersection_area(region, member_rect) > 0
        ]
        if not included_members:
            continue
        if len(included_members) < len(members):
            remaining_members = [
                member for member in members if member not in included_members
            ]
            pending_components.insert(0, (role, container_id, remaining_members))
            members = included_members
            member_rects = [
                rect
                for rect in (_rect(member.get("bounds")) for member in members)
                if rect is not None
            ]
        anchor = max(
            members,
            key=lambda item: (
                float(item.get("extractionConfidence", 0) or 0),
                _rect_area(_rect(item.get("bounds")) or region),
                str(item["slotId"]),
            ),
        )
        member_ids = sorted(str(item["slotId"]) for item in members)
        group_id = "source-flow:" + canonical_digest(
            {
                "containerId": container_id,
                "role": role,
                "members": member_ids,
                "bounds": region,
            }
        ).split(":", 1)[1][:20]
        horizontal_gaps = _positive_gaps(member_rects, axis="x")
        vertical_gaps = _positive_gaps(member_rects, axis="y")
        columns = _band_count(member_rects, axis="x")
        rows = _band_count(member_rects, axis="y")
        if role in {"body", "data"} and len(member_rects) > 1:
            aspect = max(
                0.25,
                float(region["width"]) / max(1.0, float(region["height"])),
            )
            geometry_columns = round(math.sqrt(len(member_rects) * aspect))
            columns = max(columns, min(4, max(1, geometry_columns)))
            rows = max(rows, math.ceil(len(member_rects) / columns))
        axis = (
            "grid"
            if columns > 1 and rows > 1
            else "horizontal"
            if columns > 1
            else "vertical"
        )
        active = dict(anchor)
        member_spacing = _primary_mapping(members, "spacing")
        active.update(
            {
                "semanticRole": f"source-flow-{role}",
                "acceptedKinds": (
                    ["image"]
                    if role == "images"
                    else sorted(
                        {
                            kind
                            for member in members
                            for kind in member.get("acceptedKinds", ())
                            if kind != "image"
                        }
                        | ({"image"} if role in {"body", "data"} else set())
                        or {"text"}
                    )
                ),
                "required": any(bool(item.get("required")) for item in members),
                "bounds": region,
                "capacityEnvelope": _group_capacity(
                    members,
                    region,
                    role=role,
                    columns=columns,
                    spacing=member_spacing,
                    column_gap=float(_median(horizontal_gaps)),
                ),
                "textStyle": _primary_mapping(members, "textStyle"),
                "spacing": member_spacing,
                "sourceFlowLayout": "images" if role == "images" else "text",
                "sourceFlowRole": role,
                "sourceFlowGroupId": group_id,
                "sourceFlowMemberSlotIds": member_ids,
                "sourceFlowMemberBounds": member_rects,
                "canvasBounds": canvas,
                "protectedRegions": constraining_chrome,
                "layoutHints": {
                    "flowAxis": axis,
                    "columnCount": columns,
                    "rowCount": rows,
                    "columnGap": _median(horizontal_gaps),
                    "rowGap": _median(vertical_gaps),
                    "memberCount": len(members),
                    "preserveUnitBoundary": True,
                    "overflowPolicy": "same-page-fail-before-provider",
                },
            }
        )
        anchor_id = str(anchor["slotId"])
        active_by_slot[anchor_id] = active
        for member_id in member_ids:
            if member_id != anchor_id:
                suppressed_by_slot[member_id] = group_id
    resolved: list[dict[str, Any]] = []
    for original in slots:
        slot_id = str(original["slotId"])
        if slot_id in active_by_slot:
            resolved.append(active_by_slot[slot_id])
            continue
        if slot_id not in suppressed_by_slot:
            # A protected region or another semantic group can make a merged
            # envelope impossible even though the original authored slot is
            # itself valid.  Preserve that exact slot as a one-member flow
            # region.  Dropping it would turn data-derived template capacity
            # into zero and incorrectly reject documents before Chromium.
            role = _role_family(original)
            bounds = _rect(original.get("bounds"))
            if bounds is not None and original.get("acceptedKinds"):
                item = dict(original)
                envelope = dict(item.get("capacityEnvelope") or {})
                if role != "images":
                    envelope.setdefault(
                        "minimumTextScale", MIN_SOURCE_VERBATIM_TEXT_SCALE
                    )
                    if role in {"body", "data"}:
                        item["acceptedKinds"] = sorted(
                            {*item.get("acceptedKinds", ()), "image"}
                        )
                        envelope.setdefault(
                            "maxImages",
                            max(1, int(_rect_area(bounds) // (120.0 * 90.0))),
                        )
                group_id = "source-flow:" + canonical_digest(
                    {
                        "containerId": item.get("containerId"),
                        "role": role,
                        "members": [slot_id],
                        "bounds": bounds,
                    }
                ).split(":", 1)[1][:20]
                item.update(
                    {
                        "bounds": bounds,
                        "capacityEnvelope": envelope,
                        "sourceFlowLayout": (
                            "images" if role == "images" else "text"
                        ),
                        "sourceFlowRole": role,
                        "sourceFlowGroupId": group_id,
                        "sourceFlowMemberSlotIds": [slot_id],
                        "sourceFlowMemberBounds": [bounds],
                        "canvasBounds": canvas,
                        "protectedRegions": [],
                        "layoutHints": {
                            "flowAxis": "vertical",
                            "columnCount": 1,
                            "rowCount": 1,
                            "columnGap": 0.0,
                            "rowGap": 0.0,
                            "memberCount": 1,
                            "preserveUnitBoundary": True,
                            "overflowPolicy": "same-page-fail-before-provider",
                        },
                    }
                )
                resolved.append(item)
                continue
        item = dict(original)
        item["acceptedKinds"] = []
        item["required"] = False
        item["canvasBounds"] = canvas
        item["protectedRegions"] = []
        item["sourceFlowSuppressedBy"] = suppressed_by_slot.get(slot_id)
        resolved.append(item)
    return _component_container_flow_slots(frame, slots, resolved, canvas)


def unit_text(unit: object) -> str:
    text = str(_value(unit, "text", default="") or "")
    if text.strip():
        return " ".join(text.split())
    data = _value(unit, "data")
    if data not in (None, [], {}):
        return json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
    return ""


def _text_metrics_fit(
    slot: Mapping[str, Any], texts: Sequence[str], *, scale: float
) -> bool:
    envelope = slot["capacityEnvelope"]
    characters = sum(len(item) for item in texts) + max(0, len(texts) - 1)
    max_characters = int(
        envelope.get("maxCharacters", envelope.get("characters", 10000)) or 10000
    )
    if characters * scale > max_characters:
        return False
    bounds = slot.get("bounds") or {}
    width = float(bounds.get("width", 0) or 0)
    chars_per_line = max(
        4,
        int(
            envelope.get(
                "charactersPerLine", max(4, width / 11 if width else 40)
            )
        ),
    )
    effective_chars_per_line = max(4, int(chars_per_line / scale))
    observed_lines = sum(
        max(1, math.ceil(len(item) / effective_chars_per_line))
        for item in texts
        if item
    )
    if observed_lines > int(envelope.get("maxLines", 1000) or 1000):
        return False
    content_area = float(envelope.get("contentArea", 0) or 0)
    estimated_area = (
        characters
        * float(envelope.get("characterArea", 150) or 150)
        * scale
        * scale
    )
    return content_area <= 0 or estimated_area <= content_area


def largest_fitting_text_scale(
    slot: Mapping[str, Any], assigned: Sequence[object]
) -> float | None:
    texts = [
        unit_text(item)
        for item in assigned
        if str(_value(item, "kind", default="text") or "text")
        in {"text", "title", "body", "quote"}
    ]
    if not texts or not _text_metrics_fit(
        slot, texts, scale=MIN_SOURCE_VERBATIM_TEXT_SCALE
    ):
        return None
    if _text_metrics_fit(slot, texts, scale=1.0):
        return 1.0
    low = MIN_SOURCE_VERBATIM_TEXT_SCALE
    high = 1.0
    for _ in range(18):
        candidate = (low + high) / 2
        if _text_metrics_fit(slot, texts, scale=candidate):
            low = candidate
        else:
            high = candidate
    return low


def unit_fits(
    slot: Mapping[str, Any], assigned: Sequence[object], unit: object
) -> bool:
    accepted = set(slot["acceptedKinds"])
    unit_kind = str(_value(unit, "kind", default="text") or "text")
    kind = "text" if unit_kind in {"text", "title", "body", "quote"} else unit_kind
    if kind not in accepted and not (
        kind in {"metric", "data", "number"}
        and accepted & {"text", "table", "chart"}
    ):
        return False
    combined = [*assigned, unit]
    envelope = slot["capacityEnvelope"]
    if kind == "text" and largest_fitting_text_scale(slot, combined) is None:
        return False
    rows = sum(
        len(_value(item, "data"))
        if str(_value(item, "kind", default="")) == "table"
        and isinstance(_value(item, "data"), Sequence)
        else 0
        for item in combined
    )
    columns = max(
        (
            len(row)
            for item in combined
            if str(_value(item, "kind", default="")) == "table"
            and isinstance(_value(item, "data"), Sequence)
            for row in (_value(item, "data") or ())
            if isinstance(row, Sequence)
        ),
        default=0,
    )
    if rows > int(envelope.get("maxTableRows", 10000) or 10000) or columns > int(
        envelope.get("maxTableColumns", 10000) or 10000
    ):
        return False
    numeric_cards = sum(
        1
        for item in combined
        if str(_value(item, "kind", default="")) in {"metric", "number"}
    )
    if numeric_cards > int(envelope.get("maxNumericCards", 10000) or 10000):
        return False
    images = [
        item
        for item in combined
        if str(_value(item, "kind", default="")) == "image"
    ]
    if len(images) > int(envelope.get("maxImages", 10000) or 10000):
        return False
    for image in images:
        data = _value(image, "data")
        data = data if isinstance(data, Mapping) else {}
        aspect = float(data.get("aspectRatio", data.get("aspect_ratio", 0)) or 0)
        # Source occurrences render with object-fit:contain. Aspect mismatch is
        # therefore a ranking signal, not content loss. Only a Design Pack
        # that explicitly declares an exact crop contract may make it a hard
        # admission boundary.
        if (
            aspect
            and envelope.get("imageAspectPolicy") == "exact"
            and not float(envelope.get("minImageAspectRatio", 0) or 0)
            <= aspect
            <= float(envelope.get("maxImageAspectRatio", 1000) or 1000)
        ):
            return False
    return True


def _unit_semantic_metadata(unit: object) -> dict[str, str]:
    payload = _mapping(_value(unit, "payload", default={}))
    data = _value(unit, "data", default=payload.get("data", ()))
    metadata: dict[str, str] = {}
    aliases = {
        "source-semantic.group-id": "source-semantic.group-id",
        "source-semantic-group-id": "source-semantic.group-id",
        "semantic-group-id": "source-semantic.group-id",
        "semanticgroupid": "source-semantic.group-id",
        "source-semantic.group-kind": "source-semantic.group-kind",
        "source-semantic-group-kind": "source-semantic.group-kind",
        "semantic-group-kind": "source-semantic.group-kind",
        "semanticgroupkind": "source-semantic.group-kind",
        "source-semantic.role": "source-semantic.role",
        "source-semantic-role": "source-semantic.role",
        "semantic-role": "source-semantic.role",
        "semanticrole": "source-semantic.role",
    }

    def retain(key: object, value: object) -> None:
        normalized = re.sub(r"[^a-z0-9.-]+", "", str(key or "").casefold())
        canonical = aliases.get(normalized)
        if canonical and value not in (None, ""):
            metadata[canonical] = str(value)

    direct = _mapping(data)
    if direct:
        nested = _mapping(direct.get("source-semantic"))
        for key, value in direct.items():
            retain(key, value)
        for key, value in nested.items():
            retain(f"source-semantic.{key}", value)
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        for datum in data:
            key = str(
                _value(datum, "datum_key", "datumKey", "key", default="") or ""
            )
            retain(key, _value(datum, "value", default=""))
    direct_fields = {
        "source-semantic.group-id": _value(
            unit, "semantic_group_id", "semanticGroupId", default=""
        ),
        "source-semantic.group-kind": _value(
            unit, "semantic_group_kind", "semanticGroupKind", default=""
        ),
        "source-semantic.role": _value(
            unit, "semantic_role", "semanticRole", default=""
        ),
    }
    for key, value in direct_fields.items():
        if value and key not in metadata:
            metadata[key] = str(value)
    return metadata


def _semantic_group_id(unit: object) -> str:
    if not bool(_value(unit, "required", default=True)):
        return ""
    return _unit_semantic_metadata(unit).get("source-semantic.group-id", "")


def _semantic_score(unit: object, slot: Mapping[str, Any]) -> int:
    role = str(slot["semanticRole"]).lower()
    metadata = _unit_semantic_metadata(unit)
    raw_kind = str(_value(unit, "kind", default="text") or "text").lower()
    wanted = str(
        metadata.get("source-semantic.role")
        or _value(
            unit,
            "semantic_role",
            "semanticRole",
            default="images" if raw_kind == "image" else "body",
        )
    ).lower()
    group_kind = metadata.get("source-semantic.group-kind", "").lower()
    component = str(slot.get("sourceFlowComponentKind") or "").lower()
    preferred_components = {
        "title-stack": {"titleband", "introband", "headercanvas"},
        "process-step": {"cardgrid", "contentcanvas"},
        "media-card": {"cardgrid", "evidencegallery", "contentcanvas"},
        "kpi": {"datagrid", "contentcanvas"},
        "table-matrix": {"tablematrix", "contentcanvas"},
        "compact-footer": {"footerband", "contentcanvas"},
        "body-flow": {"bodyflow", "introband", "contentcanvas"},
    }.get(group_kind)
    if preferred_components and component:
        return 0 if component in preferred_components else 2
    if wanted == role:
        return 0
    if wanted == "title" and any(
        token in role for token in ("title", "headline", "heading")
    ):
        return 0
    if wanted == "body" and any(
        token in role for token in ("body", "content", "description", "bullet")
    ):
        return 0
    if wanted == "data" and any(
        token in role for token in ("data", "metric", "table", "chart")
    ):
        return 0
    if wanted in {"image", "images"} and any(
        token in role for token in ("image", "visual", "photo", "media", "screen")
    ):
        return 0
    return 1


def capacity_slack_score(
    slot: Mapping[str, Any], assigned: Sequence[object], unit: object
) -> float:
    combined = [*assigned, unit]
    envelope = slot["capacityEnvelope"]
    texts = [
        unit_text(item)
        for item in combined
        if str(_value(item, "kind", default="text") or "text")
        in {"text", "title", "body", "quote"}
    ]
    characters = sum(len(item) for item in texts) + max(0, len(texts) - 1)
    base_max_characters = int(
        envelope.get("maxCharacters", envelope.get("characters", 10000)) or 10000
    )
    max_characters = max(
        1, int(base_max_characters / MIN_SOURCE_VERBATIM_TEXT_SCALE)
    )
    bounds = slot.get("bounds") or {}
    width = float(bounds.get("width", 0) or 0)
    base_chars_per_line = max(
        4,
        int(
            envelope.get(
                "charactersPerLine", max(4, width / 11 if width else 40)
            )
        ),
    )
    chars_per_line = max(
        4, int(base_chars_per_line / MIN_SOURCE_VERBATIM_TEXT_SCALE)
    )
    observed_lines = sum(
        max(1, math.ceil(len(item) / chars_per_line)) for item in texts if item
    )
    max_lines = int(envelope.get("maxLines", 1000) or 1000)
    character_slack = (max_characters - characters) / max(1, max_characters)
    line_slack = (max_lines - observed_lines) / max(1, max_lines)
    content_area = float(envelope.get("contentArea", 0) or 0)
    area_slack = 0.0
    if content_area > 0:
        estimated_area = (
            characters
            * float(envelope.get("characterArea", 150) or 150)
            * MIN_SOURCE_VERBATIM_TEXT_SCALE
            * MIN_SOURCE_VERBATIM_TEXT_SCALE
        )
        area_slack = (content_area - estimated_area) / content_area
    return character_slack + line_slack + area_slack


def allocate_units(
    units: Sequence[object], slots: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, list[object]], list[dict[str, Any]], list[str]]:
    unit_order = {
        str(_value(unit, "content_unit_id", "contentUnitId", default="")): index
        for index, unit in enumerate(units)
    }
    descending = sorted(
        units,
        key=lambda unit: (
            -len(unit_text(unit)),
            unit_order[str(_value(unit, "content_unit_id", "contentUnitId", default=""))],
        ),
    )
    source_order = sorted(
        units,
        key=lambda unit: unit_order[
            str(_value(unit, "content_unit_id", "contentUnitId", default=""))
        ],
    )
    required_groups: dict[str, list[object]] = {}
    for unit in source_order:
        group_id = _semantic_group_id(unit)
        if group_id:
            required_groups.setdefault(group_id, []).append(unit)

    def allocation_bundles(order: Sequence[object]) -> list[tuple[object, ...]]:
        bundles: list[tuple[object, ...]] = []
        emitted_groups: set[str] = set()
        for unit in order:
            group_id = _semantic_group_id(unit)
            members = required_groups.get(group_id, ()) if group_id else ()
            if group_id and len(members) > 1:
                if group_id in emitted_groups:
                    continue
                emitted_groups.add(group_id)
                bundles.append(tuple(members))
            else:
                bundles.append((unit,))
        return bundles

    def bundle_id(bundle: Sequence[object]) -> str:
        group_id = _semantic_group_id(bundle[0]) if bundle else ""
        if group_id and all(_semantic_group_id(item) == group_id for item in bundle):
            return f"group:{group_id}"
        return "unit:" + "|".join(
            str(_value(item, "content_unit_id", "contentUnitId", default="") or "")
            for item in bundle
        )

    def bundle_fits(
        slot: Mapping[str, Any], assigned_values: Sequence[object], bundle: Sequence[object]
    ) -> bool:
        if str(slot.get("sourceFlowLayout") or "") == "components":
            accepted = set(slot.get("acceptedKinds") or ())
            combined = [*assigned_values, *bundle]
            for unit in bundle:
                raw_kind = str(_value(unit, "kind", default="text") or "text")
                kind = (
                    "text"
                    if raw_kind in {"text", "title", "body", "quote"}
                    else raw_kind
                )
                if kind not in accepted and not (
                    kind in {"metric", "data", "number"}
                    and accepted & {"text", "table", "chart"}
                ):
                    return False
            image_count = sum(
                1
                for item in combined
                if str(_value(item, "kind", default="") or "") == "image"
            )
            maximum_images = int(
                _mapping(slot.get("capacityEnvelope")).get("maxImages", 10000)
                or 10000
            )
            return image_count <= maximum_images
        trial = list(assigned_values)
        for unit in bundle:
            if not unit_fits(slot, trial, unit):
                return False
            trial.append(unit)
        return True

    def bundle_semantic_score(
        bundle: Sequence[object], slot: Mapping[str, Any]
    ) -> int:
        return sum(_semantic_score(unit, slot) for unit in bundle)

    def bundle_slack_score(
        slot: Mapping[str, Any], assigned_values: Sequence[object], bundle: Sequence[object]
    ) -> float:
        trial = list(assigned_values)
        slack = 0.0
        for unit in bundle:
            slack = capacity_slack_score(slot, trial, unit)
            trial.append(unit)
        return slack

    def attempt(
        placement_order: Sequence[object], *, preference: str
    ) -> tuple[dict[str, list[object]], list[dict[str, Any]], list[str]]:
        assigned: dict[str, list[object]] = {
            str(item["slotId"]): [] for item in slots
        }
        assignments: list[dict[str, Any]] = []
        unmapped_bundles: list[tuple[object, ...]] = []
        for bundle in allocation_bundles(placement_order):
            unit_kind = "image" if any(
                str(_value(unit, "kind", default="text") or "text") == "image"
                for unit in bundle
            ) else "text"
            eligible = [
                item
                for item in slots
                if bundle_fits(item, assigned[str(item["slotId"])], bundle)
            ]

            def preference_score(
                item: Mapping[str, Any],
                *,
                # Bound at definition time so the comparator cannot read a
                # later iteration's bundle if it ever outlives this sort.
                bundle: Sequence[object] = bundle,
                unit_kind: str = unit_kind,
            ) -> tuple[object, ...]:
                slot_id = str(item["slotId"])
                occupancy = len(assigned[slot_id])
                image_count = sum(
                    1
                    for assigned_unit in assigned[slot_id]
                    if str(_value(assigned_unit, "kind", default="")) == "image"
                )
                semantic = bundle_semantic_score(bundle, item)
                required_open = not (item["required"] and not assigned[slot_id])
                slack = bundle_slack_score(item, assigned[slot_id], bundle)
                packing = -slack if preference == "largest" else slack
                stable = 0 if preference != "stable" else len(assigned[slot_id])
                empty_first = occupancy if preference == "empty-first" else 0
                return (
                    semantic,
                    image_count if unit_kind == "image" else 0,
                    empty_first,
                    required_open,
                    stable,
                    packing,
                    -float(item["extractionConfidence"]),
                    slot_id,
                )

            eligible.sort(key=preference_score)
            if not eligible:
                unmapped_bundles.append(tuple(bundle))
                continue
            selected = eligible[0]
            slot_id = str(selected["slotId"])
            assigned[slot_id].extend(bundle)

        # Greedy placement is fast but can strand one late unit in a tight
        # multi-slot page even when a one- or two-hop relocation yields a
        # complete same-page packing. Keep the repair bounded and rebuild the
        # receipt from the final deterministic assignment rather than omitting
        # a required source unit.
        def place_with_relocation(
            candidate_bundle: Sequence[object],
            *,
            depth: int,
            forbidden_slots: frozenset[str] = frozenset(),
            chain: frozenset[str] = frozenset(),
        ) -> bool:
            candidate_id = bundle_id(candidate_bundle)
            if not candidate_id or candidate_id in chain:
                return False
            eligible = [
                item
                for item in slots
                if str(item["slotId"]) not in forbidden_slots
                and bundle_fits(
                    item, assigned[str(item["slotId"])], candidate_bundle
                )
            ]
            eligible.sort(
                key=lambda item: (
                    bundle_semantic_score(candidate_bundle, item),
                    bundle_slack_score(
                        item, assigned[str(item["slotId"])], candidate_bundle
                    ),
                    str(item["slotId"]),
                )
            )
            if eligible:
                assigned[str(eligible[0]["slotId"])].extend(candidate_bundle)
                return True
            if depth <= 0:
                return False
            target_slots = [
                item
                for item in slots
                if str(item["slotId"]) not in forbidden_slots
                and bundle_fits(item, (), candidate_bundle)
            ]
            target_slots.sort(
                key=lambda item: (
                    bundle_semantic_score(candidate_bundle, item),
                    str(item["slotId"]),
                )
            )
            for target in target_slots:
                target_id = str(target["slotId"])
                ordered_occupants = sorted(
                    assigned[target_id],
                    key=lambda item: (
                        len(unit_text(item)),
                        str(
                            _value(
                                item,
                                "content_unit_id",
                                "contentUnitId",
                                default="",
                            )
                        ),
                    ),
                )
                occupant_bundles: list[tuple[object, ...]] = []
                seen: set[str] = set()
                for occupant in ordered_occupants:
                    occupant_group = _semantic_group_id(occupant)
                    displaced = tuple(
                        item
                        for item in assigned[target_id]
                        if occupant_group
                        and _semantic_group_id(item) == occupant_group
                    ) or (occupant,)
                    displaced_id = bundle_id(displaced)
                    if displaced_id not in seen:
                        seen.add(displaced_id)
                        occupant_bundles.append(displaced)
                for displaced_bundle in occupant_bundles:
                    displaced_ids = {
                        str(
                            _value(
                                item,
                                "content_unit_id",
                                "contentUnitId",
                                default="",
                            )
                            or ""
                        )
                        for item in displaced_bundle
                    }
                    remaining = [
                        item
                        for item in assigned[target_id]
                        if str(
                            _value(
                                item,
                                "content_unit_id",
                                "contentUnitId",
                                default="",
                            )
                            or ""
                        )
                        not in displaced_ids
                    ]
                    if not bundle_fits(target, remaining, candidate_bundle):
                        continue
                    snapshot = {
                        slot_id: list(values) for slot_id, values in assigned.items()
                    }
                    assigned[target_id] = [*remaining, *candidate_bundle]
                    if place_with_relocation(
                        displaced_bundle,
                        depth=depth - 1,
                        forbidden_slots=frozenset({target_id}),
                        chain=chain | {candidate_id},
                    ):
                        return True
                    for slot_id, values in snapshot.items():
                        assigned[slot_id] = values
            return False

        remaining_unmapped: list[str] = []
        unmapped_count = sum(len(bundle) for bundle in unmapped_bundles)
        for candidate_bundle in unmapped_bundles:
            if (
                unmapped_count > 4
                or not place_with_relocation(candidate_bundle, depth=1)
            ):
                remaining_unmapped.extend(
                    str(
                        _value(
                            item,
                            "content_unit_id",
                            "contentUnitId",
                            default="",
                        )
                        or ""
                    )
                    for item in candidate_bundle
                )
        unmapped = remaining_unmapped
        assignments = []
        slots_by_id = {str(item["slotId"]): item for item in slots}
        for slot_id, values in assigned.items():
            for unit in values:
                assignments.append(
                    {
                        "contentUnitId": str(
                            _value(
                                unit,
                                "content_unit_id",
                                "contentUnitId",
                                default="",
                            )
                            or ""
                        ),
                        "contentDigest": str(
                            _value(
                                unit,
                                "content_digest",
                                "contentDigest",
                                default="",
                            )
                            or ""
                        ),
                        "slotId": slot_id,
                        "patchHandle": slots_by_id[slot_id]["patchHandle"],
                        "required": bool(_value(unit, "required", default=True)),
                        "sourcePageIndex": int(
                            _value(
                                unit,
                                "source_page_index",
                                "sourcePageIndex",
                                default=0,
                            )
                            or 0
                        ),
                        "sourceUnitIndex": int(
                            _value(
                                unit,
                                "source_unit_index",
                                "sourceUnitIndex",
                                default=0,
                            )
                            or 0
                        ),
                    }
                )
        assignments.sort(key=lambda item: unit_order[str(item["contentUnitId"])])
        for values in assigned.values():
            values.sort(
                key=lambda item: unit_order[
                    str(_value(item, "content_unit_id", "contentUnitId", default=""))
                ]
            )
        return assigned, assignments, unmapped

    attempts = [
        attempt(order, preference=preference)
        for order in (descending, source_order)
        for preference in ("tightest", "largest", "stable", "empty-first")
    ]
    units_by_id = {
        str(_value(item, "content_unit_id", "contentUnitId", default="")): item
        for item in units
    }
    slots_by_id = {str(item["slotId"]): item for item in slots}

    def attempt_semantic_penalty(
        result: tuple[dict[str, list[object]], list[dict[str, Any]], list[str]],
    ) -> int:
        return sum(
            _semantic_score(
                units_by_id[str(item["contentUnitId"])],
                slots_by_id[str(item["slotId"])],
            )
            for item in result[1]
        )

    attempts.sort(
        key=lambda item: (
            len(item[2]),
            attempt_semantic_penalty(item),
            canonical_digest(item[1]),
        )
    )
    return attempts[0]


def allocation_quality_score(
    units: Sequence[object],
    slots: Sequence[Mapping[str, Any]],
    assigned: Mapping[str, Sequence[object]],
    assignments: Sequence[Mapping[str, Any]],
) -> tuple[int, float]:
    """Return deterministic semantic penalty and final-envelope slack."""

    units_by_id = {
        str(_value(item, "content_unit_id", "contentUnitId", default="")): item
        for item in units
    }
    slots_by_id = {str(item["slotId"]): item for item in slots}
    semantic_penalty = sum(
        _semantic_score(
            units_by_id[str(item["contentUnitId"])],
            slots_by_id[str(item["slotId"])],
        )
        for item in assignments
    )
    capacity_slack = 0.0
    for slot_id, values in assigned.items():
        if not values:
            continue
        final_slack = capacity_slack_score(
            slots_by_id[slot_id], values[:-1], values[-1]
        )
        capacity_slack += final_slack * len(values)
    return semantic_penalty, round(capacity_slack, 12)


__all__ = [
    "allocate_units",
    "allocation_quality_score",
    "canonical_digest",
    "capacity_slack_score",
    "executable_slots",
    "source_flow_slots",
    "slot_contract",
    "unit_fits",
    "unit_text",
]
