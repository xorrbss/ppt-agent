"""Deterministic sealed-content to exact Design Pack slot composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any


class CompositionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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


@dataclass(frozen=True)
class SealedContentUnit:
    content_unit_id: str
    content_digest: str
    kind: str
    required: bool
    text: str
    data: object
    source_page_index: int
    source_unit_index: int
    asset_occurrence_id: str | None
    asset_digest: str | None
    source_ownership: tuple[dict[str, Any], ...]
    semantic_role: str

    def payload(self) -> dict[str, Any]:
        return {
            "contentUnitId": self.content_unit_id,
            "contentDigest": self.content_digest,
            "kind": self.kind,
            "required": self.required,
            "text": self.text,
            "data": self.data,
            "sourcePageIndex": self.source_page_index,
            "sourceUnitIndex": self.source_unit_index,
            "assetOccurrenceId": self.asset_occurrence_id,
            "assetDigest": self.asset_digest,
            "sourceOwnership": list(self.source_ownership),
            "semanticRole": self.semantic_role,
        }


def _unit_from_contract(value: object, *, semantic_role: str = "body") -> SealedContentUnit:
    raw = _mapping(value)
    payload = _mapping(_value(value, "payload", default={}))
    unit_id = str(_value(value, "content_unit_id", "contentUnitId", default="") or "")
    if not unit_id:
        raise CompositionError("content_unit_id_required")
    kind = str(_value(value, "kind", default="text") or "text")
    text = str(_value(value, "text", default=payload.get("text", "")) or "")
    data = _value(value, "data", default=payload.get("data"))
    source_ownership = _value(value, "source_ownership", "sourceOwnership", default=())
    ownership = tuple(
        _mapping(item)
        for item in (source_ownership if isinstance(source_ownership, Sequence) else ())
        if not isinstance(item, (str, bytes))
    )
    unsigned = {
        "contentUnitId": unit_id,
        "kind": kind,
        "required": bool(_value(value, "required", default=True)),
        "text": text,
        "data": data,
        "sourcePageIndex": int(_value(value, "source_page_index", "sourcePageIndex", default=0) or 0),
        "sourceUnitIndex": int(_value(value, "source_unit_index", "sourceUnitIndex", default=0) or 0),
        "assetOccurrenceId": _value(value, "asset_occurrence_id", "assetOccurrenceId", default=payload.get("assetOccurrenceId", payload.get("asset_occurrence_id"))),
        "assetDigest": _value(value, "asset_digest", "assetDigest", default=payload.get("assetDigest", payload.get("asset_digest"))),
        "sourceOwnership": list(ownership),
    }
    supplied_digest = str(_value(value, "content_digest", "contentDigest", default="") or "")
    content_digest = supplied_digest or canonical_digest(unsigned)
    return SealedContentUnit(
        content_unit_id=unit_id,
        content_digest=content_digest,
        kind=kind,
        required=unsigned["required"],
        text=text,
        data=data,
        source_page_index=unsigned["sourcePageIndex"],
        source_unit_index=unsigned["sourceUnitIndex"],
        asset_occurrence_id=(str(unsigned["assetOccurrenceId"]) if unsigned["assetOccurrenceId"] else None),
        asset_digest=(str(unsigned["assetDigest"]) if unsigned["assetDigest"] else None),
        source_ownership=ownership,
        semantic_role=semantic_role,
    )


def sealed_units_for_slide(narrative: object, slide: object) -> list[SealedContentUnit]:
    """Read the sealed graph, retaining a digest-bound legacy compatibility path."""

    sealed = _value(slide, "sealed_content_units", "sealedContentUnits", default=())
    if not sealed:
        sealed = _value(narrative, "sealed_content_units", "sealedContentUnits", default=())
    ids = _value(slide, "content_unit_ids", "contentUnitIds", default=())
    if isinstance(sealed, Sequence) and not isinstance(sealed, (str, bytes)) and sealed:
        by_id = {
            str(_value(item, "content_unit_id", "contentUnitId", default="")): item
            for item in sealed
        }
        requested = [str(item) for item in ids] if isinstance(ids, Sequence) and not isinstance(ids, (str, bytes)) else []
        if not requested:
            sequence_index = int(_value(slide, "sequence_index", "sequenceIndex", default=0) or 0)
            requested = [
                unit_id
                for unit_id, item in by_id.items()
                if int(_value(item, "source_page_index", "sourcePageIndex", default=sequence_index) or 0)
                == sequence_index
            ]
        if len(requested) != len(set(requested)) or any(item not in by_id for item in requested):
            raise CompositionError("sealed_content_unit_binding_invalid")
        units = [
            _unit_from_contract(
                by_id[item],
                semantic_role=("title" if index == 0 and str(_value(by_id[item], "kind", default="text")) == "text" else "data" if str(_value(by_id[item], "kind", default="text")) != "text" else "body"),
            )
            for index, item in enumerate(requested)
        ]
        return sorted(units, key=lambda item: (item.source_page_index, item.source_unit_index, item.content_unit_id))

    # Compatibility for already-persisted v1 Narrative IR. This is deterministic
    # and digest-bound; it is never a DOM-order assignment.
    slide_key = str(_value(slide, "slide_key", "slideKey", default="slide"))
    source_page = int(_value(slide, "sequence_index", "sequenceIndex", default=0) or 0)
    ownership = _value(slide, "source_ownership", "sourceOwnership", default=())
    candidates: list[dict[str, Any]] = [
        {
            "contentUnitId": f"{slide_key}:key-message",
            "kind": "text",
            "required": True,
            "text": str(_value(slide, "key_message", "keyMessage", default="") or ""),
            "sourcePageIndex": source_page,
            "sourceUnitIndex": 0,
            "sourceOwnership": ownership,
            "semanticRole": "title",
        }
    ]
    supporting = _value(slide, "supporting_points", "supportingPoints", default=())
    if isinstance(supporting, Sequence) and not isinstance(supporting, (str, bytes)):
        candidates.extend(
            {
                "contentUnitId": f"{slide_key}:supporting-point:{index:03d}",
                "kind": "text",
                "required": True,
                "text": str(item),
                "sourcePageIndex": source_page,
                "sourceUnitIndex": index,
                "sourceOwnership": ownership,
                "semanticRole": "body",
            }
            for index, item in enumerate(supporting, start=1)
            if str(item).strip()
        )
    data = _value(slide, "data", default=())
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        candidates.extend(
            {
                "contentUnitId": f"{slide_key}:data:{index:03d}",
                "kind": str(_value(item, "kind", default="data") or "data"),
                "required": True,
                "text": str(_value(item, "label", "text", default="") or ""),
                "data": _mapping(item),
                "sourcePageIndex": source_page,
                "sourceUnitIndex": len(candidates) + index,
                "sourceOwnership": ownership,
                "semanticRole": "data",
            }
            for index, item in enumerate(data, start=1)
        )
    units: list[SealedContentUnit] = []
    for value in candidates:
        semantic_role = str(value.pop("semanticRole"))
        unsigned = dict(value)
        value["contentDigest"] = canonical_digest(unsigned)
        units.append(_unit_from_contract(value, semantic_role=semantic_role))
    return units


def _slot_contract(slot: object) -> dict[str, Any]:
    raw = _mapping(slot)
    constraints = _value(slot, "constraints", default=())
    envelope = _mapping(_value(slot, "capacity_envelope", "capacityEnvelope", default={}))
    for constraint in constraints if isinstance(constraints, Sequence) else ():
        kind = str(_value(constraint, "kind", default=""))
        value = _value(constraint, "value")
        if kind == "maximum-characters":
            envelope.setdefault("maxCharacters", value)
        elif kind == "maximum-lines":
            envelope.setdefault("maxLines", value)
    envelope.setdefault("maxCharacters", int(_value(slot, "capacity_units", "capacityUnits", default=1) or 1))
    bounds = _mapping(_value(slot, "bounds", default={}))
    width = float(bounds.get("width", 0) or 0)
    height = float(bounds.get("height", 0) or 0)
    envelope.setdefault("contentArea", max(0.0, width * height))
    handle = _mapping(_value(slot, "patch_handle", "patchHandle", default={}))
    if not handle:
        source_shape_id = str(_value(slot, "source_shape_id", "sourceShapeId", default="") or "")
        if source_shape_id:
            handle = {
                "kind": "authored-qid",
                "elementId": source_shape_id,
                "selector": f"[data-qid='{source_shape_id}']",
                "sourceDigest": _value(slot, "source_xml_digest", "sourceXmlDigest"),
            }
    return {
        "slotId": str(_value(slot, "slot_id", "slotId", default="") or ""),
        "semanticRole": str(_value(slot, "semantic_role", "semanticRole", default="body") or "body"),
        "acceptedKinds": [str(item) for item in (_value(slot, "accepted_kinds", "acceptedKinds", default=("text",)) or ())],
        "required": bool(_value(slot, "required", default=False)),
        "capacityEnvelope": envelope,
        "patchHandle": handle,
        "containerId": _value(slot, "container_id", "containerId"),
        "hidePolicy": _value(slot, "hide_policy", "hidePolicy", default="reject"),
        "fallbackPolicy": _value(slot, "fallback_policy", "fallbackPolicy", default="reject"),
        "extractionConfidence": float(_value(slot, "extraction_confidence", "extractionConfidence", default=1.0) or 0.0),
        "bounds": bounds,
        "raw": raw,
    }


def _unit_text(unit: SealedContentUnit) -> str:
    if unit.text.strip():
        return " ".join(unit.text.split())
    if unit.data not in (None, [], {}):
        return json.dumps(unit.data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ""


def _fits(slot: Mapping[str, Any], assigned: Sequence[SealedContentUnit], unit: SealedContentUnit) -> bool:
    accepted = set(slot["acceptedKinds"])
    kind = "text" if unit.kind in {"text", "title", "body", "quote"} else unit.kind
    if kind not in accepted and not (kind in {"metric", "data", "number"} and accepted & {"text", "table", "chart"}):
        return False
    combined = [*assigned, unit]
    envelope = slot["capacityEnvelope"]
    texts = [_unit_text(item) for item in combined]
    characters = sum(len(item) for item in texts) + max(0, len(texts) - 1)
    max_characters = int(envelope.get("maxCharacters", envelope.get("characters", 10000)) or 10000)
    if characters > max_characters:
        return False
    bounds = slot.get("bounds") or {}
    width = float(bounds.get("width", 0) or 0)
    chars_per_line = max(4, int(envelope.get("charactersPerLine", max(4, width / 11 if width else 40))))
    observed_lines = sum(max(1, math.ceil(len(item) / chars_per_line)) for item in texts if item)
    max_lines = int(envelope.get("maxLines", 1000) or 1000)
    if observed_lines > max_lines:
        return False
    content_area = float(envelope.get("contentArea", 0) or 0)
    estimated_area = characters * float(envelope.get("characterArea", 150) or 150)
    if content_area > 0 and estimated_area > content_area:
        return False
    rows = sum(len(item.data) if item.kind == "table" and isinstance(item.data, Sequence) else 0 for item in combined)
    columns = max(
        (len(row) for item in combined if item.kind == "table" and isinstance(item.data, Sequence) for row in item.data if isinstance(row, Sequence)),
        default=0,
    )
    if rows > int(envelope.get("maxTableRows", 10000) or 10000) or columns > int(envelope.get("maxTableColumns", 10000) or 10000):
        return False
    numeric_cards = sum(1 for item in combined if item.kind in {"metric", "number"})
    if numeric_cards > int(envelope.get("maxNumericCards", 10000) or 10000):
        return False
    images = [item for item in combined if item.kind == "image"]
    if len(images) > int(envelope.get("maxImages", 10000) or 10000):
        return False
    for image in images:
        data = image.data if isinstance(image.data, Mapping) else {}
        aspect = float(data.get("aspectRatio", data.get("aspect_ratio", 0)) or 0)
        if aspect and not float(envelope.get("minImageAspectRatio", 0) or 0) <= aspect <= float(envelope.get("maxImageAspectRatio", 1000) or 1000):
            return False
    return True


def _semantic_score(unit: SealedContentUnit, slot: Mapping[str, Any]) -> int:
    role = str(slot["semanticRole"]).lower()
    wanted = unit.semantic_role.lower()
    if wanted == role:
        return 0
    if wanted == "title" and any(token in role for token in ("title", "headline", "heading")):
        return 0
    if wanted == "body" and any(token in role for token in ("body", "content", "description", "bullet")):
        return 0
    if wanted == "data" and any(token in role for token in ("data", "metric", "table", "chart")):
        return 0
    return 1


def _capacity_slack_score(
    slot: Mapping[str, Any],
    assigned: Sequence[SealedContentUnit],
    unit: SealedContentUnit,
) -> float:
    """Prefer the tightest fitting slot so large envelopes remain available."""

    combined = [*assigned, unit]
    envelope = slot["capacityEnvelope"]
    texts = [_unit_text(item) for item in combined]
    characters = sum(len(item) for item in texts) + max(0, len(texts) - 1)
    max_characters = int(
        envelope.get("maxCharacters", envelope.get("characters", 10000)) or 10000
    )
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
    observed_lines = sum(
        max(1, math.ceil(len(item) / chars_per_line)) for item in texts if item
    )
    max_lines = int(envelope.get("maxLines", 1000) or 1000)
    character_slack = (max_characters - characters) / max(1, max_characters)
    line_slack = (max_lines - observed_lines) / max(1, max_lines)
    content_area = float(envelope.get("contentArea", 0) or 0)
    area_slack = 0.0
    if content_area > 0:
        estimated_area = characters * float(
            envelope.get("characterArea", 150) or 150
        )
        area_slack = (content_area - estimated_area) / content_area
    return character_slack + line_slack + area_slack


def build_composition_plan(
    *,
    narrative: object,
    slide: object,
    frame: object,
) -> tuple[dict[str, Any], list[SealedContentUnit], list[dict[str, Any]]]:
    units = sealed_units_for_slide(narrative, slide)
    slots = [_slot_contract(item) for item in (_value(frame, "slots", default=()) or ())]
    slots = [item for item in slots if item["slotId"] and item["patchHandle"].get("kind") in {"authored-qid", "scene-element"}]
    slots.sort(key=lambda item: item["slotId"])
    assigned: dict[str, list[SealedContentUnit]] = {item["slotId"]: [] for item in slots}
    assignments: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for unit in units:
        eligible = [item for item in slots if _fits(item, assigned[item["slotId"]], unit)]
        eligible.sort(
            key=lambda item: (
                _semantic_score(unit, item),
                not (item["required"] and not assigned[item["slotId"]]),
                _capacity_slack_score(
                    item, assigned[item["slotId"]], unit
                ),
                -item["extractionConfidence"],
                item["slotId"],
            )
        )
        if not eligible:
            unmapped.append(unit.content_unit_id)
            continue
        selected = eligible[0]
        assigned[selected["slotId"]].append(unit)
        assignments.append(
            {
                "contentUnitId": unit.content_unit_id,
                "contentDigest": unit.content_digest,
                "slotId": selected["slotId"],
                "patchHandle": selected["patchHandle"],
                "required": unit.required,
                "sourcePageIndex": unit.source_page_index,
                "sourceUnitIndex": unit.source_unit_index,
            }
        )
    mapped_ids = [item["contentUnitId"] for item in assignments]
    duplicates = sorted({item for item in mapped_ids if mapped_ids.count(item) > 1})
    required_ids = {item.content_unit_id for item in units if item.required}
    missing_required_slots = sorted(
        item["slotId"] for item in slots if item["required"] and not assigned[item["slotId"]]
    )
    unmapped_required = sorted(required_ids & set(unmapped))
    protected_element_digests = sorted(
        str(_value(item, "source_xml_digest", "sourceXmlDigest", default=""))
        for item in (_value(frame, "chrome", default=()) or ())
        if _value(item, "source_xml_digest", "sourceXmlDigest", default="")
    )
    unsigned = {
        "schemaVersion": "deterministic-composition-plan/v1",
        "slideKey": str(_value(slide, "slide_key", "slideKey", default="")),
        "sequenceIndex": int(_value(slide, "sequence_index", "sequenceIndex", default=0) or 0),
        "contentGraphDigest": str(_value(narrative, "content_graph_digest", "contentGraphDigest", default="") or canonical_digest([item.payload() for item in units])),
        "frameId": str(_value(frame, "frame_id", "frameId", default="")),
        "frameDigest": str(_value(frame, "source_digest", "sourceDigest", default="")),
        "protectedElementDigests": protected_element_digests,
        "protectedDigest": canonical_digest(protected_element_digests),
        "assignments": assignments,
        "unmappedContentUnits": sorted(unmapped),
        "unmappedRequiredUnits": unmapped_required,
        "duplicateRequiredUnits": duplicates,
        "missingRequiredSlots": missing_required_slots,
        "status": "passed" if not unmapped and not duplicates and not missing_required_slots else "failed",
    }
    plan = {**unsigned, "planDigest": canonical_digest(unsigned)}
    return plan, units, slots


def assert_composition_plan(plan: Mapping[str, Any]) -> None:
    if (
        plan.get("status") != "passed"
        or plan.get("unmappedContentUnits")
        or plan.get("unmappedRequiredUnits")
        or plan.get("duplicateRequiredUnits")
        or plan.get("missingRequiredSlots")
    ):
        raise CompositionError("source_native_composition_unavailable")


def build_content_integrity_page(
    *,
    plan: Mapping[str, Any],
    units: Sequence[SealedContentUnit],
    rendered_html: str,
    browser_metrics: Mapping[str, Any],
    protected_digest_before: str,
    protected_digest_after: str,
) -> dict[str, Any]:
    """Build PageContentIntegrityV1 input from DOM and Chromium readback."""

    from lxml import html as lxml_html

    document = lxml_html.document_fromstring(rendered_html)
    raw_metrics = browser_metrics.get("elements", ())
    metric_by_qid = {
        str(item.get("qid")): item
        for item in raw_metrics
        if isinstance(item, Mapping) and item.get("qid")
    } if isinstance(raw_metrics, Sequence) and not isinstance(raw_metrics, (str, bytes)) else {}
    by_id = {item.content_unit_id: item for item in units}
    expected_ids = [item.content_unit_id for item in units]
    bound_ids = [
        str(item.get("contentUnitId"))
        for item in plan.get("assignments", ())
        if isinstance(item, Mapping)
    ]
    rendered_ids: list[str] = []
    hidden: list[str] = []
    off_canvas: list[str] = []
    transformed: list[str] = []
    rendered_assets: list[str] = []
    for assignment in plan.get("assignments", ()):
        if not isinstance(assignment, Mapping):
            continue
        unit_id = str(assignment.get("contentUnitId") or "")
        unit = by_id.get(unit_id)
        handle = assignment.get("patchHandle")
        handle = handle if isinstance(handle, Mapping) else {}
        qid = str(handle.get("elementId") or "")
        nodes = document.xpath("//*[@data-qid=$qid]", qid=qid) if qid else []
        if len(nodes) != 1 or unit is None:
            continue
        node = nodes[0]
        ids = str(node.get("data-content-unit-ids") or "").split("|")
        digests = str(node.get("data-content-unit-digests") or "").split("|")
        if unit_id not in ids or unit.content_digest not in digests:
            continue
        rendered_ids.append(unit_id)
        metric = metric_by_qid.get(qid, {})
        invisible = (
            not metric
            or metric.get("css_visible") is False
            or float(metric.get("effective_opacity", 1) or 0) <= 0
            or float(metric.get("viewport_intersection_ratio", 1) or 0) <= 0
        )
        if invisible:
            hidden.append(unit_id)
        if not metric or float(metric.get("viewport_intersection_ratio", 1) or 0) < 0.999:
            off_canvas.append(unit_id)
        if bool(metric.get("clipped") or metric.get("glyph_clipped") or metric.get("line_box_clipped")):
            transformed.append(unit_id)
        if unit.asset_occurrence_id and not invisible:
            rendered_assets.append(unit.asset_occurrence_id)
    required_ids = {item.content_unit_id for item in units if item.required}
    duplicates = sorted({item for item in bound_ids if bound_ids.count(item) > 1} & required_ids)
    if protected_digest_before != protected_digest_after:
        transformed.extend(required_ids)
    expected_digest = canonical_digest([item.content_digest for item in units])
    bound_digest = canonical_digest([by_id[item].content_digest for item in bound_ids if item in by_id])
    rendered_digest = canonical_digest([by_id[item].content_digest for item in rendered_ids if item in by_id])
    expected_assets = [item.asset_occurrence_id for item in units if item.asset_occurrence_id]
    sequence_index = int(plan.get("sequenceIndex", 0) or 0)
    return {
        "pageIndex": sequence_index,
        "expectedContentUnitIds": expected_ids,
        "boundContentUnitIds": bound_ids,
        "renderedContentUnitIds": rendered_ids,
        "expectedDigest": expected_digest,
        "boundDigest": bound_digest,
        "renderedDigest": rendered_digest,
        "unmappedRequiredUnits": list(plan.get("unmappedRequiredUnits", ())),
        "duplicateRequiredUnits": duplicates,
        "transformedRequiredUnits": sorted(set(transformed) & required_ids),
        "hiddenRequiredUnits": sorted(set(hidden) & required_ids),
        "offCanvasRequiredUnits": sorted(set(off_canvas) & required_ids),
        "expectedAssetOccurrences": expected_assets,
        "renderedAssetOccurrences": rendered_assets,
        "sourceOwnershipDigest": canonical_digest(
            [
                {
                    "contentUnitId": item.content_unit_id,
                    "sourceOwnership": list(item.source_ownership),
                }
                for item in units
            ]
        ),
        "pageOrderError": any(item.source_page_index != sequence_index for item in units),
    }


__all__ = [
    "CompositionError",
    "SealedContentUnit",
    "assert_composition_plan",
    "build_content_integrity_page",
    "build_composition_plan",
    "canonical_digest",
    "sealed_units_for_slide",
]
