"""Fail-closed ownership, geometry, and title stress validation."""

from __future__ import annotations

from collections import Counter

from models.authored_vnext.master.contract import DeckMasterContract, Rect
from models.authored_vnext.master.operations import ValidationIssue, ValidationSummary
from models.authored_vnext.master.slide import ExplicitTitle

from .title_fit import fit_title


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

    writable_overlaps = 0
    seen_slot_ids: set[str] = set()
    for layout_name, layout in (
        ("standard", contract.layouts.standard),
        ("longTitle", contract.layouts.long_title),
    ):
        roles = Counter(slot.role for slot in layout.slots)
        if roles["title"] != 1 or roles["body"] != 1:
            issues.append(
                ValidationIssue(
                    code="authored_vnext_master_layout_slots_invalid",
                    path=f"contract.layouts.{layout_name}.slots",
                    message="Each layout requires exactly one title and one body slot.",
                )
            )
        for index, slot in enumerate(layout.slots):
            path = f"contract.layouts.{layout_name}.slots[{index}]"
            if slot.id in seen_slot_ids:
                duplicate_count += 1
                issues.append(
                    ValidationIssue(
                        code="authored_vnext_master_furniture_body_duplicate",
                        path=f"{path}.id",
                        message="Body slot identifiers must be unique across layouts.",
                    )
                )
            seen_slot_ids.add(slot.id)
            if slot.ownership.locked or not slot.ownership.model_writable:
                issues.append(
                    ValidationIssue(
                        code="authored_vnext_master_body_ownership_invalid",
                        path=f"{path}.ownership",
                        message="Body slots must be unlocked and model-writable.",
                    )
                )
            if not _inside_canvas(slot.bounds, contract):
                issues.append(
                    ValidationIssue(
                        code="authored_vnext_master_geometry_out_of_bounds",
                        path=f"{path}.bounds",
                        message="Body slot geometry must stay inside the canvas.",
                    )
                )
            for element in furniture_elements:
                if _overlaps(slot.bounds, element.bounds):
                    writable_overlaps += 1
                    issues.append(
                        ValidationIssue(
                            code="authored_vnext_master_furniture_body_geometry_overlap",
                            path=f"{path}.bounds",
                            message=f"Writable body overlaps locked {element.id}.",
                        )
                    )

    expected_margins = (7.2, 7.2, 3.6, 3.6)
    margins = contract.semantic_styles.title.margins
    actual_margins = (
        margins.left_pt,
        margins.right_pt,
        margins.top_pt,
        margins.bottom_pt,
    )
    if actual_margins != expected_margins:
        issues.append(
            ValidationIssue(
                code="authored_vnext_master_powerpoint_margin_contract_invalid",
                path="contract.semanticStyles.title.margins",
                message="Title margins must be 7.2pt LR and 3.6pt TB.",
            )
        )

    title_fit_failures = 0
    for index, case in enumerate(contract.title_stress_corpus):
        try:
            title = ExplicitTitle(text=case.text, lines=case.expected_lines)
            result = fit_title(contract, contract.layouts.long_title.id, title)
        except ValueError as error:
            result = None
            issues.append(
                ValidationIssue(
                    code="authored_vnext_master_stress_title_invalid",
                    path=f"contract.titleStressCorpus[{index}]",
                    message=str(error),
                )
            )
        if result is None or result.clipped:
            title_fit_failures += 1
            if result is not None:
                issues.append(
                    ValidationIssue(
                        code="authored_vnext_master_stress_title_clipped",
                        path=f"contract.titleStressCorpus[{index}]",
                        message=", ".join(result.violations),
                    )
                )

    return ValidationSummary(
        valid=not issues,
        errors=issues,
        furniture_body_duplicates=duplicate_count,
        writable_overlaps=writable_overlaps,
        title_fit_failures=title_fit_failures,
    )
