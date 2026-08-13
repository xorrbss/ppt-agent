"""Slide-local identity and diagnostic families for repair patterns."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.slidespec_v1 import RenderRequest
from services.slidespec_repair_pattern_index import (
    _layout_indexes,
    _target_selector,
    _tree_shape,
)
from services.slidespec_repair_patterns import (
    PatternObservation,
    _LayoutIndexes,
    _digest,
)
from services.slidespec_repair_pipeline import DomEvidence

def _defect_selector(
    indexes: _LayoutIndexes, target: str
) -> Mapping[str, Any]:
    selector = _target_selector(indexes, target)
    if selector is None:
        return {"unbound": True}
    return {"path": selector.path, "shape": selector.shape}


def _normalize_constraint_value(
    indexes: _LayoutIndexes, value: object
) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_constraint_value(indexes, item)
            for key, item in value.items()
            if str(key) not in {"constraintId", "constraint_id"}
        }
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_constraint_value(indexes, item) for item in value)
    if isinstance(value, str):
        selector = _target_selector(indexes, value)
        if selector is not None:
            return {
                "target_kind": selector.target_kind,
                "path": selector.path,
                "shape": selector.shape,
            }
    return value


def repair_pattern_key(request: RenderRequest, evidence: DomEvidence) -> str:
    """Return a strict fingerprint with no slide-local IDs or absolute bounds."""

    indexes = _layout_indexes(request)
    defects: list[dict[str, Any]] = []
    for defect in evidence.defects:
        if defect.code == "constraint.evidence.missing" or defect.severity not in {
            "high",
            "medium",
        }:
            continue
        defects.append(
            {
                "code": defect.code,
                "severity": defect.severity,
                "target": _defect_selector(indexes, defect.semantic_id),
                "related": sorted(
                    (
                        _defect_selector(indexes, semantic_id)
                        for semantic_id in defect.related_semantic_ids
                    ),
                    key=_digest,
                ),
            }
        )
    text_area = sum(
        node.bounds.width * node.bounds.height
        for node in indexes.by_id.values()
        if node.bounds is not None
        and node.contentSemanticId is not None
        and indexes.content_text_by_semantic.get(node.contentSemanticId)
    )
    text_characters = sum(
        len(indexes.content_text_by_semantic.get(node.contentSemanticId or "", ""))
        for node in indexes.by_id.values()
    )
    text_density = text_characters / max(text_area, 1e-6)
    density_band = (
        "low" if text_density < 300 else "medium" if text_density < 900 else "high"
    )
    payload = {
        "defects": sorted(defects, key=_digest),
        "layout": _tree_shape(indexes, request.spec.layout),
        "text_density_band": density_band,
        "constraint_footprint": sorted(
            (
                {
                    "kind": constraint.kind,
                    "strength": constraint.strength,
                    "shape": _normalize_constraint_value(
                        indexes,
                        constraint.model_dump(mode="json", by_alias=True),
                    ),
                }
                for constraint in request.spec.constraints
            ),
            key=_digest,
        ),
    }
    return "repair-pattern:" + _digest(payload)


def repair_diagnostic_family(
    request: RenderRequest, evidence: DomEvidence
) -> str:
    """Coarse family for ordering/learning, never for recipe publication."""

    indexes = _layout_indexes(request)
    family: list[tuple[str, str, str, str]] = []
    for defect in evidence.defects:
        if defect.code == "constraint.evidence.missing" or defect.severity not in {
            "high",
            "medium",
        }:
            continue
        node = indexes.by_semantic.get(defect.semantic_id) or indexes.by_id.get(
            defect.semantic_id
        )
        parent = indexes.parent_by_id.get(node.layoutId) if node is not None else None
        family.append(
            (
                defect.code,
                defect.severity,
                node.kind if node is not None else "unbound",
                parent.kind if parent is not None else "root",
            )
        )
    return "diagnostic-family:" + _digest(sorted(family))


def pattern_observation(
    position: int, request: RenderRequest, evidence: DomEvidence
) -> PatternObservation | None:
    actionable = tuple(
        defect
        for defect in evidence.defects
        if defect.code != "constraint.evidence.missing"
        and defect.severity in {"high", "medium"}
    )
    if not actionable:
        return None
    indexes = _layout_indexes(request)
    text_area = 0.0
    occupied_area = 0.0
    text_characters = 0
    for node in indexes.by_id.values():
        if node.bounds is None:
            continue
        area = node.bounds.width * node.bounds.height
        if not node.children:
            occupied_area += area
        semantic_id = node.contentSemanticId
        if semantic_id is not None and indexes.content_text_by_semantic.get(semantic_id):
            text_area += area
            text_characters += len(indexes.content_text_by_semantic.get(semantic_id, ""))
    return PatternObservation(
        position=position,
        slide_id=request.spec.slideId,
        pattern_key=repair_pattern_key(request, evidence),
        text_density=round(text_characters / max(text_area, 1e-6), 6),
        available_space=round(max(0.0, 1.0 - min(1.0, occupied_area)), 6),
        diagnostic_family=repair_diagnostic_family(request, evidence),
    )

