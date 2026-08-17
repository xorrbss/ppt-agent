"""Adapters from generation models to published quality wire reports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from models.authored_vnext.deck.quality import (
    ConsistencyCheck,
    DeckQualityReport,
    GateIssue,
    SlideGateReport,
)
from services.authored_vnext.master.digests import canonical_digest

from .quality import _observed_coverage, _plain, _read, _slide_id, _string


def validate_pipeline_slide_candidate(
    contract: object,
    validated: object,
    plan: object,
) -> SlideGateReport:
    """Validate canonical slide/plan bindings and emit the strict wire report."""

    slide = _read(validated, "slide", default=None)
    checkpoint = _read(validated, "checkpoint", default=None)
    slide_id = _string(_read(slide, "slideId", "slide_id", default=None))
    errors: list[GateIssue] = []

    def reject(code: str, path: str, message: str) -> None:
        errors.append(
            GateIssue(code=code, slide_id=slide_id, path=path, message=message)
        )

    plan_slide_id = _string(_read(plan, "slideId", "slide_id", default=None))
    plan_layout = _string(_read(plan, "layoutId", "layout_id", default=None))
    plan_source = _string(_read(plan, "sourceDigest", "source_digest", default=None))
    body_slot_id = _string(_read(plan, "bodySlotId", "body_slot_id", default=None))
    elements = _read(plan, "elements", default=None)
    dom_passed = (
        isinstance(elements, Sequence)
        and not isinstance(elements, (str, bytes))
        and bool(elements)
    )
    body_texts: list[str] = []
    if dom_passed:
        for index, element in enumerate(elements):
            element_id = _string(
                _read(element, "elementId", "element_id", default=None)
            )
            if element_id is None or not element_id.startswith("body:"):
                dom_passed = False
                reject(
                    "authored_vnext_dom_body_ownership_failed",
                    f"visualPlan.elements[{index}]",
                    "Visual plan may contain body-owned elements only.",
                )
            text = _read(element, "text", default=None)
            if isinstance(text, str):
                body_texts.append(text)
    if (
        slide_id is None
        or plan_slide_id != slide_id
        or plan_layout != _string(_read(slide, "layoutId", "layout_id", default=None))
        or body_slot_id
        != _string(_read(checkpoint, "bodySlotId", "body_slot_id", default=None))
    ):
        dom_passed = False
        reject(
            "authored_vnext_dom_binding_failed",
            "visualPlan",
            "Visual plan must bind to the validated slide layout and body slot.",
        )

    expected_body = _string(_read(slide, "body", default=None))
    observed_body = " ".join(body_texts)
    checkpoint_source = _string(
        _read(checkpoint, "sourceDigest", "source_digest", default=None)
    )
    ocr_passed = (
        expected_body is not None
        and _observed_coverage(expected_body, observed_body) >= 0.95
        and plan_source == checkpoint_source
    )
    if not ocr_passed:
        reject(
            "authored_vnext_ocr_source_failed",
            "visualPlan.elements",
            "Deterministic OCR projection must retain body text and source binding.",
        )

    title_fit = _read(slide, "titleFit", "title_fit", default=None)
    title_fit_passed = (
        _read(title_fit, "clipped", default=None) is False
        and not tuple(_read(title_fit, "violations", default=()) or ())
        and _string(_read(title_fit, "layoutId", "layout_id", default=None))
        == plan_layout
        and _string(
            _read(
                title_fit,
                "fontManifestDigest",
                "font_manifest_digest",
                default=None,
            )
        )
        == _string(
            _read(
                checkpoint,
                "fontManifestDigest",
                "font_manifest_digest",
                default=None,
            )
        )
    )
    if not title_fit_passed:
        reject(
            "authored_vnext_title_fit_failed",
            "slide.titleFit",
            "Title-fit must be unclipped and bound to the sealed layout/font checkpoint.",
        )

    furniture = _plain(_read(contract, "furniture", default={}))
    furniture_values = furniture.values() if isinstance(furniture, Mapping) else ()
    furniture_ids = {
        _string(_read(element, "id", default=None)) for element in furniture_values
    }
    plan_ids = {
        _string(_read(element, "elementId", "element_id", default=None))
        for element in (elements or ())
    }
    duplicate_count = len((furniture_ids - {None}) & (plan_ids - {None}))
    if duplicate_count:
        reject(
            "authored_vnext_furniture_body_duplicate",
            "visualPlan.elements",
            "Master furniture cannot be duplicated in the body plan.",
        )

    candidate_digest = canonical_digest(
        {"slide": _plain(validated), "visualPlan": _plain(plan)}
    )
    passed = (
        dom_passed
        and ocr_passed
        and title_fit_passed
        and duplicate_count == 0
        and not errors
    )
    return SlideGateReport(
        slide_id=slide_id,
        candidate_digest=candidate_digest,
        dom_passed=dom_passed,
        ocr_passed=ocr_passed,
        title_fit_passed=title_fit_passed,
        furniture_body_duplicates=duplicate_count,
        issues=errors,
        passed=passed,
    )


def _digest_observations(
    slides: Sequence[object],
    camel_name: str,
    snake_name: str,
) -> list[tuple[str | None, str | None]]:
    return [
        (
            _slide_id(_read(item, "slide", default=None)),
            _string(
                _read(
                    _read(item, "checkpoint", default=None),
                    camel_name,
                    snake_name,
                    default=None,
                )
            ),
        )
        for item in slides
    ]


def _consistency_check(
    observations: Iterable[tuple[str | None, str | None]],
    expected: str | None,
) -> ConsistencyCheck:
    materialized = tuple(observations)
    mismatches = [
        slide_id
        for slide_id, digest in materialized
        if digest != expected and slide_id is not None
    ]
    digests = sorted({digest for _, digest in materialized if digest is not None})
    return ConsistencyCheck(
        passed=not mismatches,
        expected_digest=expected,
        observed_digests=digests,
        mismatched_slide_ids=mismatches,
    )


def build_deck_quality_report(
    contract: object,
    deck: object,
    plans: Iterable[object],
) -> DeckQualityReport:
    """Build the canonical deck publication report from sealed checkpoints."""

    slides = tuple(_read(deck, "slides", default=()) or ())
    plans_by_id = {
        _string(_read(plan, "slideId", "slide_id", default=None)): plan
        for plan in plans
    }
    gates: list[SlideGateReport | None] = []
    for item in slides:
        plan = plans_by_id.get(_slide_id(_read(item, "slide", default=None)))
        gates.append(
            validate_pipeline_slide_candidate(contract, item, plan)
            if plan is not None
            else None
        )
    issues: list[GateIssue] = []
    if any(gate is None for gate in gates):
        issues.append(
            GateIssue(
                code="authored_vnext_visual_plan_missing",
                path="plans",
                message="Every validated slide requires its visual plan.",
            )
        )
    slide_gates = [gate for gate in gates if gate is not None]
    master = _read(deck, "master", default=None)
    master_digest = _string(
        _read(master, "masterDigest", "master_digest", default=None)
    )
    contract_master_digest = _string(_read(contract, "digest", default=None))
    contract_font_digest = _string(
        _read(
            _read(contract, "fontManifest", "font_manifest", default=None),
            "digest",
            default=None,
        )
    )
    contract_layout_digest = canonical_digest(
        _plain(_read(contract, "layouts", default={}))
    )
    composition = _read(deck, "composition", default=None)
    if composition is None:
        expected_font_digest = contract_font_digest
        expected_layout_digest = contract_layout_digest
        if (
            master_digest is None
            or contract_master_digest is None
            or master_digest != contract_master_digest
        ):
            issues.append(
                GateIssue(
                    code="authored_vnext_master_digest_mismatch",
                    path="deck.master.masterDigest",
                    message="Deck master digest must identify the supplied published contract.",
                )
            )
    else:
        composition_master_digest = _string(
            _read(composition, "masterDigest", "master_digest", default=None)
        )
        effective_contract_digest = _string(
            _read(
                composition,
                "effectiveContractDigest",
                "effective_contract_digest",
                default=None,
            )
        )
        expected_font_digest = _string(
            _read(master, "fontManifestDigest", "font_manifest_digest", default=None)
        )
        expected_layout_digest = _string(
            _read(master, "layoutRegistryDigest", "layout_registry_digest", default=None)
        )
        if (
            master_digest is None
            or composition_master_digest is None
            or master_digest != composition_master_digest
        ):
            issues.append(
                GateIssue(
                    code="authored_vnext_composition_master_digest_mismatch",
                    path="deck.composition.masterDigest",
                    message=(
                        "Composition master digest must match the durable published "
                        "deck master pin."
                    ),
                )
            )
        if (
            contract_master_digest is None
            or effective_contract_digest is None
            or contract_master_digest != effective_contract_digest
        ):
            issues.append(
                GateIssue(
                    code="authored_vnext_composition_effective_contract_digest_mismatch",
                    path="deck.composition.effectiveContractDigest",
                    message=(
                        "Supplied rendering contract must match the composition's "
                        "sealed effective contract digest."
                    ),
                )
            )
    source_digest = _string(
        _read(deck, "sourceDigest", "source_digest", default=None)
    )
    color_digest = canonical_digest(
        _plain(_read(_read(contract, "tokens", default={}), "colors", default={}))
    )
    frame_digest = canonical_digest(_plain(_read(contract, "furniture", default={})))
    ids = [_slide_id(_read(item, "slide", default=None)) for item in slides]
    colors = [(slide_id, color_digest) for slide_id in ids]
    frames = [(slide_id, frame_digest) for slide_id in ids]
    layouts = _digest_observations(
        slides, "layoutRegistryDigest", "layout_registry_digest"
    )
    fonts = _digest_observations(
        slides, "fontManifestDigest", "font_manifest_digest"
    )
    sources = _digest_observations(slides, "sourceDigest", "source_digest")

    slot_mismatches: list[str] = []
    registered_layouts = {
        _string(_read(layout, "id", default=None)): layout
        for layout in (
            _read(_read(contract, "layouts", default={}), "cover", default=None),
            _read(_read(contract, "layouts", default={}), "section", default=None),
            _read(_read(contract, "layouts", default={}), "standard", default=None),
            _read(
                _read(contract, "layouts", default={}),
                "longTitle",
                "long_title",
                default=None,
            ),
        )
        if layout is not None
    }
    page_number = _read(
        _read(contract, "furniture", default={}),
        "pageNumber",
        "page_number",
        default=None,
    )
    page_number_valid = (
        _read(page_number, "kind", default=None) == "pageNumber"
        and _read(
            _read(page_number, "ownership", default=None),
            "locked",
            default=None,
        )
        is True
        and _read(
            _read(page_number, "ownership", default=None),
            "modelWritable",
            "model_writable",
            default=None,
        )
        is False
    )
    page_mismatches = [slide_id for slide_id in ids if not page_number_valid]
    for item in slides:
        slide = _read(item, "slide", default=None)
        slide_id = _slide_id(slide)
        if slide_id is None:
            continue
        checkpoint = _read(item, "checkpoint", default=None)
        layout = registered_layouts.get(
            _string(_read(slide, "layoutId", "layout_id", default=None))
        )
        valid_slots = {
            _string(_read(slot, "id", default=None))
            for slot in (_read(layout, "slots", default=()) or ())
        }
        body_slot = _string(
            _read(checkpoint, "bodySlotId", "body_slot_id", default=None)
        )
        if body_slot not in valid_slots:
            slot_mismatches.append(slide_id)

    checks = {
        "fonts": _consistency_check(fonts, expected_font_digest),
        "colors": _consistency_check(colors, color_digest),
        "frame": _consistency_check(frames, frame_digest),
        "page_numbers": ConsistencyCheck(
            passed=not page_mismatches,
            mismatched_slide_ids=page_mismatches,
        ),
        "layouts": _consistency_check(layouts, expected_layout_digest),
        "slots": ConsistencyCheck(
            passed=not slot_mismatches,
            mismatched_slide_ids=slot_mismatches,
        ),
        "sources": _consistency_check(sources, source_digest),
    }
    passed = (
        all(check.passed for check in checks.values())
        and all(gate.passed for gate in slide_gates)
        and not issues
    )
    return DeckQualityReport(
        deck_id=_read(deck, "deckId", "deck_id"),
        deck_digest=_read(deck, "digest"),
        master_digest=master_digest,
        slide_gates=slide_gates,
        passed=passed,
        issues=issues,
        **checks,
    )


__all__ = ["build_deck_quality_report", "validate_pipeline_slide_candidate"]
