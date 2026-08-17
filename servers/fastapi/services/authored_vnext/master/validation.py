"""Fail-closed furniture ownership and reserved-envelope validation."""

from __future__ import annotations

from collections import Counter

from models.authored_vnext.master.contract import DeckMasterContract, Rect
from models.authored_vnext.master.operations import ValidationIssue, ValidationSummary


def _overlaps(left: Rect, right: Rect) -> bool:
    return (
        left.x < right.right
        and left.right > right.x
        and left.y < right.bottom
        and left.bottom > right.y
    )


def _inside_canvas(rect: Rect, contract: DeckMasterContract) -> bool:
    return (
        rect.x >= 0
        and rect.y >= 0
        and rect.right <= contract.canvas.width_px
        and rect.bottom <= contract.canvas.height_px
    )


def validate_master(contract: DeckMasterContract) -> ValidationSummary:
    issues: list[ValidationIssue] = []
    furniture = list(contract.furniture.model_dump().keys())
    furniture_elements = [getattr(contract.furniture, name) for name in furniture]
    furniture_counts = Counter(item.id for item in furniture_elements)
    duplicate_count = sum(count - 1 for count in furniture_counts.values() if count > 1)

    for name, element in zip(furniture, furniture_elements, strict=True):
        path = f"contract.furniture.{name}"
        if not element.ownership.locked or element.ownership.model_writable:
            issues.append(
                ValidationIssue(
                    code="authored_vnext_master_furniture_ownership_invalid",
                    path=f"{path}.ownership",
                    message="Master furniture must be locked and not model-writable.",
                )
            )
        if not _inside_canvas(element.bounds, contract):
            issues.append(
                ValidationIssue(
                    code="authored_vnext_master_geometry_out_of_bounds",
                    path=f"{path}.bounds",
                    message="Furniture geometry must stay inside the canvas.",
                )
            )

    if duplicate_count:
        issues.append(
            ValidationIssue(
                code="authored_vnext_master_furniture_body_duplicate",
                path="contract.furniture",
                message="Furniture identifiers must be unique.",
            )
        )

    semantic_ids = Counter(element.id for element in contract.semantic_elements)
    for count in semantic_ids.values():
        if count > 1:
            duplicate_count += count - 1
    for index, element in enumerate(contract.semantic_elements):
        path = f"contract.semanticElements[{index}]"
        if not _inside_canvas(element.bounds, contract):
            issues.append(
                ValidationIssue(
                    code="authored_vnext_master_geometry_out_of_bounds",
                    path=f"{path}.bounds",
                    message="Semantic element geometry must stay inside the canvas.",
                )
            )
        if element.type == "image" and (
            element.content.asset_id is None or element.content.digest is None
        ):
            issues.append(
                ValidationIssue(
                    code="authored_vnext_master_image_asset_unpinned",
                    path=f"{path}.content",
                    message="Semantic images require digest-pinned assetId and digest values.",
                )
            )

    writable_overlaps = 0
    for index, zone in enumerate(contract.ai_edit_zones):
        path = f"contract.aiEditZones[{index}]"
        if not _inside_canvas(zone.bounds, contract):
            issues.append(
                ValidationIssue(
                    code="authored_vnext_master_geometry_out_of_bounds",
                    path=f"{path}.bounds",
                    message="AI edit zone geometry must stay inside the canvas.",
                )
            )
        for element in furniture_elements:
            if not _occupies_canvas(element):
                continue
            if _overlaps(zone.bounds, element.bounds):
                writable_overlaps += 1
                issues.append(
                    ValidationIssue(
                        code="authored_vnext_master_furniture_ai_zone_geometry_overlap",
                        path=f"{path}.bounds",
                        message=f"AI edit zone overlaps locked {element.id}.",
                    )
                )

    return ValidationSummary(
        valid=not issues,
        errors=issues,
        furniture_body_duplicates=duplicate_count,
        writable_overlaps=writable_overlaps,
        title_fit_failures=0,
    )


def _occupies_canvas(element: object) -> bool:
    if getattr(element, "visibility", None) == "hidden":
        return False
    kind = getattr(element, "kind", None)
    if kind == "logo":
        return getattr(element, "asset_id", None) is not None
    if kind in {"text", "pageNumber"}:
        return bool((getattr(element, "text", None) or "").strip())
    if kind in {"line", "rect"}:
        return bool(getattr(element, "stroke", None) or getattr(element, "fill", None))
    return True
