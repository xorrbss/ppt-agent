"""Deterministic same-page capacity admission for source-verbatim composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from typing import Any


MIN_SOURCE_VERBATIM_TEXT_SCALE = 0.70


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
    gaps: list[float] = []
    for index, left in enumerate(rects):
        for right in rects[index + 1 :]:
            if axis == "x":
                overlap = max(
                    0.0,
                    min(_bottom(left), _bottom(right)) - max(left["y"], right["y"]),
                )
                gap = max(
                    0.0,
                    max(left["x"], right["x"]) - min(_right(left), _right(right)),
                )
            else:
                overlap = max(
                    0.0,
                    min(_right(left), _right(right)) - max(left["x"], right["x"]),
                )
                gap = max(
                    0.0,
                    max(left["y"], right["y"]) - min(_bottom(left), _bottom(right)),
                )
            if overlap > 0 and gap > 0:
                gaps.append(gap)
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


def _group_capacity(
    members: Sequence[Mapping[str, Any]],
    region: Mapping[str, float],
    *,
    role: str,
    columns: int = 1,
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
        declared_capacity = sum(
            max(1, int(envelope.get("maxImages", 1) or 1))
            for envelope in envelopes
        )
        geometry_capacity = max(1, int(content_area // (64.0 * 64.0)))
        return {
            "measurementBasis": "design-pack-derived",
            "contentArea": content_area,
            "maxImages": max(declared_capacity, geometry_capacity),
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
    minimum_font_px = 32.0 if role == "title" else 16.0
    font_px = max(minimum_font_px, declared_font_px or minimum_font_px)
    resolved_columns = max(1, min(4, int(columns or 1)))
    characters_per_line = max(
        4,
        int(
            (float(region["width"]) / resolved_columns)
            / max(1.0, font_px * 0.70)
        ),
    )
    geometry_lines = max(
        1,
        resolved_columns
        * int(float(region["height"]) / max(1.0, font_px * 1.32)),
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


def _promoted_container_flow_slots(
    frame: object,
    slots: Sequence[Mapping[str, Any]],
    resolved: Sequence[Mapping[str, Any]],
    canvas: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Promote authored leaf slots to one sealed ancestor content surface.

    The authored compiler records the exact DOM parent selector for protected
    containers.  A source-verbatim page must flow through a usable ancestor
    surface rather than treating every nested label/value leaf as independent
    capacity.  Old packs without the graph retain the legacy projection.
    """

    chrome = [
        item
        for item in (_value(frame, "chrome", default=()) or ())
        if _value(item, "container_id", "containerId")
    ]
    if not chrome or len(slots) < 8:
        return [dict(item) for item in resolved]
    by_selector = {
        str(_value(item, "selector", default="") or ""): item
        for item in chrome
        if str(_value(item, "selector", default="") or "")
    }
    if not by_selector:
        return [dict(item) for item in resolved]

    def ancestors(slot: Mapping[str, Any]) -> set[str]:
        current = str(slot.get("containerId") or "")
        seen: set[str] = set()
        while current and current not in seen and len(seen) < 64:
            seen.add(current)
            parent = by_selector.get(current)
            current = str(
                _value(parent, "container_id", "containerId", default="") or ""
            ) if parent is not None else ""
        return seen

    ancestry = {str(slot["slotId"]): ancestors(slot) for slot in slots}
    canvas_area = _rect_area(canvas)
    candidates: list[tuple[tuple[int, float, float, str], object, dict[str, float], list[Mapping[str, Any]]]] = []
    for element in chrome:
        selector = str(_value(element, "selector", default="") or "")
        bounds = _rect(_value(element, "bounds", default={}))
        if not selector or bounds is None:
            continue
        area = _rect_area(bounds)
        if (
            area < canvas_area * 0.12
            or area > canvas_area * 0.86
            or bounds["width"] < canvas["width"] * 0.34
            or bounds["height"] < canvas["height"] * 0.22
        ):
            continue
        descendants = [
            slot
            for slot in slots
            if selector in ancestry.get(str(slot["slotId"]), set())
        ]
        text_descendants = [
            slot for slot in descendants if "text" in set(slot.get("acceptedKinds", ()))
        ]
        if len(descendants) < 4 or len(text_descendants) < 3:
            continue
        occupied = sum(
            _rect_area(_rect(slot.get("bounds")) or bounds) for slot in descendants
        )
        candidates.append(
            (
                (len(descendants), min(1.0, occupied / max(1.0, area)), area, selector),
                element,
                bounds,
                descendants,
            )
        )
    if not candidates:
        return [dict(item) for item in resolved]
    _, selected, selected_bounds, descendants = max(candidates, key=lambda item: item[0])
    descendant_ids = {str(item["slotId"]) for item in descendants}
    active_descendants = [
        item
        for item in resolved
        if str(item.get("slotId") or "") in descendant_ids
        and item.get("sourceFlowLayout")
    ]
    if not active_descendants:
        return [dict(item) for item in resolved]
    anchor = max(
        active_descendants,
        key=lambda item: (
            _rect_area(_rect(item.get("bounds")) or selected_bounds),
            float(item.get("extractionConfidence", 0) or 0),
            str(item.get("slotId") or ""),
        ),
    )
    inset = min(18.0, selected_bounds["width"] * 0.025, selected_bounds["height"] * 0.04)
    region = {
        "x": selected_bounds["x"] + inset,
        "y": selected_bounds["y"] + inset,
        "width": max(1.0, selected_bounds["width"] - inset * 2),
        "height": max(1.0, selected_bounds["height"] - inset * 2),
    }
    aspect = region["width"] / max(1.0, region["height"])
    columns = 3 if aspect >= 2.5 else 2 if aspect >= 1.45 else 1
    font_px = 16.0
    characters_per_line = max(
        8, int((region["width"] / columns) / (font_px * 0.70))
    )
    maximum_lines = max(
        1, columns * int(region["height"] / (font_px * 1.32))
    )
    selector = str(_value(selected, "selector", default="") or "")
    group_id = "source-flow:" + canonical_digest(
        {
            "containerId": selector,
            "members": sorted(str(item["slotId"]) for item in slots),
            "bounds": region,
            "projection": "promoted-container-antichain/v1",
        }
    ).split(":", 1)[1][:20]
    promoted = dict(anchor)
    promoted.update(
        {
            "semanticRole": "source-flow-body",
            "acceptedKinds": ["image", "text"],
            "required": False,
            "bounds": region,
            "containerId": selector,
            "capacityEnvelope": {
                "measurementBasis": "design-pack-container-graph",
                "contentArea": _rect_area(region),
                "maxCharacters": characters_per_line * maximum_lines,
                "maxLines": maximum_lines,
                "maxImages": max(1, int(_rect_area(region) // (140.0 * 105.0))),
                "charactersPerLine": characters_per_line,
                "characterArea": (font_px * 0.70) * (font_px * 1.32),
                "minimumTextScale": MIN_SOURCE_VERBATIM_TEXT_SCALE,
            },
            "textStyle": _primary_mapping(active_descendants, "textStyle"),
            "spacing": {
                "insetLeft": 8.0,
                "insetTop": 8.0,
                "insetRight": 8.0,
                "insetBottom": 8.0,
            },
            "sourceFlowLayout": "text",
            "sourceFlowRole": "body",
            "sourceFlowGroupId": group_id,
            "sourceFlowMemberSlotIds": sorted(str(item["slotId"]) for item in slots),
            "sourceFlowMemberBounds": [
                rect for rect in (_rect(item.get("bounds")) for item in slots) if rect
            ],
            "sourceFlowSurface": "container-inherited",
            "sourceFlowContainerGraphVersion": "v1",
            "canvasBounds": dict(canvas),
            "protectedRegions": [],
            "layoutHints": {
                "flowAxis": "grid" if columns > 1 else "vertical",
                "columnCount": columns,
                "rowCount": 1,
                "columnGap": 20.0,
                "rowGap": 8.0,
                "memberCount": len(slots),
                "preserveUnitBoundary": True,
                "overflowPolicy": "same-page-fail-before-provider",
            },
        }
    )
    anchor_id = str(anchor["slotId"])
    output: list[dict[str, Any]] = []
    for original in resolved:
        slot_id = str(original.get("slotId") or "")
        if slot_id == anchor_id:
            output.append(promoted)
            continue
        suppressed = dict(original)
        suppressed["acceptedKinds"] = []
        suppressed["required"] = False
        suppressed.pop("sourceFlowLayout", None)
        suppressed["sourceFlowSuppressedBy"] = group_id
        output.append(suppressed)
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
                    members, region, role=role, columns=columns
                ),
                "textStyle": _primary_mapping(members, "textStyle"),
                "spacing": _primary_mapping(members, "spacing"),
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
    return _promoted_container_flow_slots(frame, slots, resolved, canvas)


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


def _semantic_score(unit: object, slot: Mapping[str, Any]) -> int:
    role = str(slot["semanticRole"]).lower()
    wanted = str(_value(unit, "semantic_role", "semanticRole", default="body")).lower()
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

    def attempt(
        placement_order: Sequence[object], *, preference: str
    ) -> tuple[dict[str, list[object]], list[dict[str, Any]], list[str]]:
        assigned: dict[str, list[object]] = {
            str(item["slotId"]): [] for item in slots
        }
        assignments: list[dict[str, Any]] = []
        unmapped: list[str] = []
        for unit in placement_order:
            unit_kind = str(_value(unit, "kind", default="text") or "text")
            unit_id = str(
                _value(unit, "content_unit_id", "contentUnitId", default="") or ""
            )
            eligible = [
                item
                for item in slots
                if unit_fits(item, assigned[str(item["slotId"])], unit)
            ]

            def preference_score(item: Mapping[str, Any]) -> tuple[object, ...]:
                slot_id = str(item["slotId"])
                occupancy = len(assigned[slot_id])
                image_count = sum(
                    1
                    for assigned_unit in assigned[slot_id]
                    if str(_value(assigned_unit, "kind", default="")) == "image"
                )
                semantic = _semantic_score(unit, item)
                required_open = not (item["required"] and not assigned[slot_id])
                slack = capacity_slack_score(item, assigned[slot_id], unit)
                packing = -slack if preference == "largest" else slack
                stable = 0 if preference != "stable" else len(assigned[slot_id])
                empty_first = occupancy if preference == "empty-first" else 0
                return (
                    image_count if unit_kind == "image" else 0,
                    empty_first,
                    semantic,
                    required_open,
                    stable,
                    packing,
                    -float(item["extractionConfidence"]),
                    slot_id,
                )

            eligible.sort(key=preference_score)
            if not eligible:
                unmapped.append(unit_id)
                continue
            selected = eligible[0]
            slot_id = str(selected["slotId"])
            assigned[slot_id].append(unit)
            assignments.append(
                {
                    "contentUnitId": unit_id,
                    "contentDigest": str(
                        _value(unit, "content_digest", "contentDigest", default="")
                        or ""
                    ),
                    "slotId": slot_id,
                    "patchHandle": selected["patchHandle"],
                    "required": bool(_value(unit, "required", default=True)),
                    "sourcePageIndex": int(
                        _value(unit, "source_page_index", "sourcePageIndex", default=0)
                        or 0
                    ),
                    "sourceUnitIndex": int(
                        _value(unit, "source_unit_index", "sourceUnitIndex", default=0)
                        or 0
                    ),
                }
            )

        # Greedy placement is fast but can strand one late unit in a tight
        # multi-slot page even when a one- or two-hop relocation yields a
        # complete same-page packing. Keep the repair bounded and rebuild the
        # receipt from the final deterministic assignment rather than omitting
        # a required source unit.
        units_by_id = {
            str(_value(item, "content_unit_id", "contentUnitId", default="")): item
            for item in units
        }

        def place_with_relocation(
            candidate: object,
            *,
            depth: int,
            forbidden_slots: frozenset[str] = frozenset(),
            chain: frozenset[str] = frozenset(),
        ) -> bool:
            candidate_id = str(
                _value(candidate, "content_unit_id", "contentUnitId", default="")
                or ""
            )
            if not candidate_id or candidate_id in chain:
                return False
            eligible = [
                item
                for item in slots
                if str(item["slotId"]) not in forbidden_slots
                and unit_fits(item, assigned[str(item["slotId"])], candidate)
            ]
            eligible.sort(
                key=lambda item: (
                    _semantic_score(candidate, item),
                    capacity_slack_score(
                        item, assigned[str(item["slotId"])], candidate
                    ),
                    str(item["slotId"]),
                )
            )
            if eligible:
                assigned[str(eligible[0]["slotId"])].append(candidate)
                return True
            if depth <= 0:
                return False
            target_slots = [
                item
                for item in slots
                if str(item["slotId"]) not in forbidden_slots
                and (
                    str(_value(candidate, "kind", default="text") or "text")
                    in set(item["acceptedKinds"])
                    or (
                        str(_value(candidate, "kind", default="text") or "text")
                        in {"title", "body", "quote"}
                        and "text" in set(item["acceptedKinds"])
                    )
                )
            ]
            target_slots.sort(
                key=lambda item: (_semantic_score(candidate, item), str(item["slotId"]))
            )
            for target in target_slots:
                target_id = str(target["slotId"])
                occupants = sorted(
                    list(assigned[target_id]),
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
                for displaced in occupants:
                    remaining = [item for item in assigned[target_id] if item is not displaced]
                    if not unit_fits(target, remaining, candidate):
                        continue
                    snapshot = {
                        slot_id: list(values) for slot_id, values in assigned.items()
                    }
                    assigned[target_id] = [*remaining, candidate]
                    if place_with_relocation(
                        displaced,
                        depth=depth - 1,
                        forbidden_slots=frozenset({target_id}),
                        chain=chain | {candidate_id},
                    ):
                        return True
                    for slot_id, values in snapshot.items():
                        assigned[slot_id] = values
            return False

        remaining_unmapped: list[str] = []
        for unit_id in unmapped:
            candidate = units_by_id.get(unit_id)
            if (
                len(unmapped) > 4
                or candidate is None
                or not place_with_relocation(candidate, depth=1)
            ):
                remaining_unmapped.append(unit_id)
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
    attempts.sort(
        key=lambda item: (
            len(item[2]),
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
