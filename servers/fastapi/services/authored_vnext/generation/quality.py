"""Fail-closed slide and deck quality gates for authored vNext generation.

The gate deliberately consumes a structural ``Mapping``/attribute protocol instead
of a concrete deck model.  This keeps generation checkpoints independently
testable while the canonical deck wire model evolves.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from types import MappingProxyType


_MISSING = object()
_CHECK_NAMES = (
    "master",
    "fonts",
    "colors",
    "frame",
    "pageNo",
    "layout",
    "slot",
    "source",
)


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: str
    path: str
    message: str
    slide_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.slide_id is not None:
            payload["slideId"] = self.slide_id
        return payload


@dataclass(frozen=True, slots=True)
class SlideGateReport:
    valid: bool
    slide_id: str | None
    errors: tuple[QualityIssue, ...]
    checks: Mapping[str, bool]
    ocr_text_coverage: float

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "slideId": self.slide_id,
            "errors": [error.to_dict() for error in self.errors],
            "checks": dict(self.checks),
            "ocrTextCoverage": self.ocr_text_coverage,
        }


@dataclass(frozen=True, slots=True)
class DeckGateReport:
    valid: bool
    errors: tuple[QualityIssue, ...]
    checks: Mapping[str, bool]
    slide_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": [error.to_dict() for error in self.errors],
            "checks": dict(self.checks),
            "slideCount": self.slide_count,
        }


def _plain(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python", by_alias=True)
    return value


def _read(value: object, *names: str, default: object = _MISSING) -> object:
    value = _plain(value)
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return default


def _slide_id(slide: object) -> str | None:
    value = _read(slide, "slideId", "slide_id", default=None)
    return value if isinstance(value, str) and value else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _strings(value: object) -> tuple[str, ...] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    if not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return tuple(value)


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _observed_coverage(expected: str, observed: str) -> float:
    expected_normalized = _normalized_text(expected)
    observed_normalized = _normalized_text(observed)
    if not expected_normalized:
        return 1.0
    if not observed_normalized:
        return 0.0
    matcher = SequenceMatcher(None, expected_normalized, observed_normalized, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return min(1.0, matched / len(expected_normalized))


def _expected_rendered_text(candidate: object) -> str | None:
    title = _read(candidate, "title", default=None)
    title_text = _read(title, "text", default=None)
    if not _string(title_text):
        lines = _strings(_read(title, "lines", "explicitLines", "explicit_lines", default=None))
        title_text = " ".join(lines) if lines else None
    body = _read(candidate, "body", default=None)
    pieces = [piece for piece in (title_text, body) if _string(piece)]
    return " ".join(pieces) if pieces else None


def _issue(
    errors: list[QualityIssue],
    *,
    code: str,
    path: str,
    message: str,
    slide_id: str | None,
) -> None:
    errors.append(QualityIssue(code=code, path=path, message=message, slide_id=slide_id))


def _expected_slots(
    expected_slot_ids: Mapping[str, Iterable[str]] | Iterable[str] | None,
    layout_id: str | None,
) -> tuple[str, ...] | None:
    if expected_slot_ids is None:
        return None
    if isinstance(expected_slot_ids, Mapping):
        if layout_id is None or layout_id not in expected_slot_ids:
            return ()
        return tuple(expected_slot_ids[layout_id])
    return tuple(expected_slot_ids)


def validate_slide_candidate(
    candidate: object,
    *,
    expected_master_digest: str,
    allowed_layout_ids: Iterable[str] | None = None,
    expected_slot_ids: Mapping[str, Iterable[str]] | Iterable[str] | None = None,
    expected_source_digest: str | None = None,
    min_ocr_text_coverage: float = 0.95,
) -> SlideGateReport:
    """Validate detached candidate evidence before it can enter publication.

    DOM, OCR and title-fit evidence are mandatory. Missing evidence is rejected;
    callers cannot accidentally convert an unobserved check into a pass.
    """

    if not 0.0 <= min_ocr_text_coverage <= 1.0:
        raise ValueError("min_ocr_text_coverage must be between zero and one")

    errors: list[QualityIssue] = []
    slide_id = _slide_id(candidate)
    checks = {name: True for name in ("master", "layout", "slot", "source", "dom", "ocr", "titleFit")}

    master_digest = _string(_read(candidate, "masterDigest", "master_digest", default=None))
    if master_digest != expected_master_digest:
        checks["master"] = False
        _issue(
            errors,
            code="quality_candidate_master_digest_mismatch",
            path="candidate.masterDigest",
            message="Candidate must use the operation's immutable master digest.",
            slide_id=slide_id,
        )

    layout_id = _string(_read(candidate, "layoutId", "layout_id", default=None))
    allowed = frozenset(allowed_layout_ids) if allowed_layout_ids is not None else None
    if layout_id is None or (allowed is not None and layout_id not in allowed):
        checks["layout"] = False
        _issue(
            errors,
            code="quality_candidate_layout_invalid",
            path="candidate.layoutId",
            message="Candidate layout must be present in the published layout registry.",
            slide_id=slide_id,
        )

    slot_ids = _strings(_read(candidate, "slotIds", "slot_ids", default=None))
    expected_slots = _expected_slots(expected_slot_ids, layout_id)
    if (
        slot_ids is None
        or len(set(slot_ids)) != len(slot_ids)
        or (expected_slots is not None and set(slot_ids) != set(expected_slots))
    ):
        checks["slot"] = False
        _issue(
            errors,
            code="quality_candidate_slot_mismatch",
            path="candidate.slotIds",
            message="Candidate slots must be unique and match its registered layout.",
            slide_id=slide_id,
        )

    source_digest = _string(_read(candidate, "sourceDigest", "source_digest", default=None))
    if source_digest is None or (
        expected_source_digest is not None and source_digest != expected_source_digest
    ):
        checks["source"] = False
        _issue(
            errors,
            code="quality_candidate_source_digest_mismatch",
            path="candidate.sourceDigest",
            message="Candidate source digest must match the immutable source snapshot.",
            slide_id=slide_id,
        )

    expected_text = _expected_rendered_text(candidate)
    dom = _read(candidate, "dom", "domEvidence", "dom_evidence", default=None)
    dom_text = _string(_read(dom, "text", "renderedText", "rendered_text", default=None))
    dom_slots = _strings(_read(dom, "slotIds", "slot_ids", default=None))
    overflow_count = _read(dom, "overflowCount", "overflow_count", default=None)
    overlap_count = _read(dom, "overlapCount", "overlap_count", default=None)
    duplicates = _read(dom, "duplicatedFurnitureIds", "duplicated_furniture_ids", default=None)
    dom_source_digest = _string(_read(dom, "sourceDigest", "source_digest", default=None))
    dom_ok = (
        expected_text is not None
        and dom_text is not None
        and _observed_coverage(expected_text, dom_text) == 1.0
        and overflow_count == 0
        and overlap_count == 0
        and isinstance(duplicates, Sequence)
        and not isinstance(duplicates, (str, bytes))
        and len(duplicates) == 0
        and slot_ids is not None
        and dom_slots is not None
        and set(dom_slots) == set(slot_ids)
        and source_digest is not None
        and dom_source_digest == source_digest
    )
    if not dom_ok:
        checks["dom"] = False
        _issue(
            errors,
            code="quality_candidate_dom_gate_failed",
            path="candidate.dom",
            message="DOM evidence must preserve content, slots, source, bounds, and master/body separation.",
            slide_id=slide_id,
        )

    ocr = _read(candidate, "ocr", "ocrEvidence", "ocr_evidence", default=None)
    ocr_text = _string(_read(ocr, "text", "recognizedText", "recognized_text", default=None))
    ocr_source_digest = _string(_read(ocr, "sourceDigest", "source_digest", default=None))
    ocr_coverage = (
        _observed_coverage(expected_text, ocr_text)
        if expected_text is not None and ocr_text is not None
        else 0.0
    )
    if ocr_coverage < min_ocr_text_coverage or source_digest is None or ocr_source_digest != source_digest:
        checks["ocr"] = False
        _issue(
            errors,
            code="quality_candidate_ocr_gate_failed",
            path="candidate.ocr",
            message=f"OCR evidence must match source and retain at least {min_ocr_text_coverage:.3f} text coverage.",
            slide_id=slide_id,
        )

    title_fit = _read(candidate, "titleFit", "title_fit", default=None)
    clipped = _read(title_fit, "clipped", default=None)
    violations = _read(title_fit, "violations", default=None)
    fit_layout_id = _string(_read(title_fit, "layoutId", "layout_id", default=None))
    fit_font_digest = _string(
        _read(title_fit, "fontManifestDigest", "font_manifest_digest", default=None)
    )
    candidate_font_digest = _string(
        _read(candidate, "fontManifestDigest", "font_manifest_digest", default=None)
    )
    title_fit_ok = (
        clipped is False
        and isinstance(violations, Sequence)
        and not isinstance(violations, (str, bytes))
        and len(violations) == 0
        and layout_id is not None
        and fit_layout_id == layout_id
        and candidate_font_digest is not None
        and fit_font_digest == candidate_font_digest
    )
    if not title_fit_ok:
        checks["titleFit"] = False
        _issue(
            errors,
            code="quality_candidate_title_fit_failed",
            path="candidate.titleFit",
            message="Title-fit must be unclipped and bound to the candidate layout and font manifest.",
            slide_id=slide_id,
        )

    return SlideGateReport(
        valid=not errors,
        slide_id=slide_id,
        errors=tuple(errors),
        checks=MappingProxyType(checks),
        ocr_text_coverage=round(ocr_coverage, 6),
    )


def validate_deck_consistency(
    slides: Iterable[object],
    *,
    expected_master_digest: str,
    expected_font_manifest_digest: str,
    expected_color_digest: str,
    expected_frame_digest: str,
    expected_layout_registry_digest: str | None = None,
    expected_source_digest: str | None = None,
    allowed_layout_ids: Iterable[str] | None = None,
    expected_slot_ids: Mapping[str, Iterable[str]] | None = None,
    require_page_numbers: bool = True,
) -> DeckGateReport:
    """Check the immutable rendering checkpoint and aggregate deck invariants."""

    materialized = tuple(slides)
    errors: list[QualityIssue] = []
    checks = {name: True for name in _CHECK_NAMES}
    if not materialized:
        issue = QualityIssue(
            code="quality_deck_empty",
            path="slides",
            message="Deck consistency cannot be established without slides.",
        )
        return DeckGateReport(
            valid=False,
            errors=(issue,),
            checks=MappingProxyType({name: False for name in _CHECK_NAMES}),
            slide_count=0,
        )

    allowed = frozenset(allowed_layout_ids) if allowed_layout_ids is not None else None
    first_source = _string(_read(materialized[0], "sourceDigest", "source_digest", default=None))
    source_checkpoint = expected_source_digest or first_source
    observed_slots_by_layout: dict[str, frozenset[str]] = {}

    field_checks = (
        ("master", "masterDigest", "master_digest", expected_master_digest),
        ("fonts", "fontManifestDigest", "font_manifest_digest", expected_font_manifest_digest),
        ("colors", "colorDigest", "color_digest", expected_color_digest),
        ("frame", "frameDigest", "frame_digest", expected_frame_digest),
    )
    for index, slide in enumerate(materialized):
        slide_id = _slide_id(slide)
        path = f"slides[{index}]"
        for check, camel_name, snake_name, expected in field_checks:
            actual = _string(_read(slide, camel_name, snake_name, default=None))
            if actual != expected:
                checks[check] = False
                _issue(
                    errors,
                    code=f"quality_deck_{check}_consistency_failed",
                    path=f"{path}.{camel_name}",
                    message=f"{camel_name} must equal the sealed deck checkpoint.",
                    slide_id=slide_id,
                )

        page_number = _read(slide, "pageNumber", "page_number", default=None)
        if require_page_numbers and (type(page_number) is not int or page_number != index + 1):
            checks["pageNo"] = False
            _issue(
                errors,
                code="quality_deck_page_number_discontinuity",
                path=f"{path}.pageNumber",
                message=f"Expected logical page number {index + 1}.",
                slide_id=slide_id,
            )

        layout_id = _string(_read(slide, "layoutId", "layout_id", default=None))
        layout_digest = _string(
            _read(slide, "layoutRegistryDigest", "layout_registry_digest", default=None)
        )
        if (
            layout_id is None
            or (allowed is not None and layout_id not in allowed)
            or (
                expected_layout_registry_digest is not None
                and layout_digest != expected_layout_registry_digest
            )
        ):
            checks["layout"] = False
            _issue(
                errors,
                code="quality_deck_layout_consistency_failed",
                path=f"{path}.layoutId",
                message="Layout ID and registry digest must match the published master.",
                slide_id=slide_id,
            )

        slot_ids = _strings(_read(slide, "slotIds", "slot_ids", default=None))
        slot_set = frozenset(slot_ids or ())
        expected_for_layout = expected_slot_ids.get(layout_id, ()) if expected_slot_ids and layout_id else None
        previous_slots = observed_slots_by_layout.get(layout_id) if layout_id is not None else None
        slots_ok = slot_ids is not None and len(slot_set) == len(slot_ids)
        if expected_for_layout is not None:
            slots_ok = slots_ok and slot_set == frozenset(expected_for_layout)
        elif previous_slots is not None:
            slots_ok = slots_ok and slot_set == previous_slots
        if layout_id is not None and previous_slots is None and slots_ok:
            observed_slots_by_layout[layout_id] = slot_set
        if not slots_ok:
            checks["slot"] = False
            _issue(
                errors,
                code="quality_deck_slot_consistency_failed",
                path=f"{path}.slotIds",
                message="Slot IDs must be unique and stable for each registered layout.",
                slide_id=slide_id,
            )

        source_digest = _string(_read(slide, "sourceDigest", "source_digest", default=None))
        if source_checkpoint is None or source_digest != source_checkpoint:
            checks["source"] = False
            _issue(
                errors,
                code="quality_deck_source_consistency_failed",
                path=f"{path}.sourceDigest",
                message="All slides must use the sealed source snapshot digest.",
                slide_id=slide_id,
            )

    return DeckGateReport(
        valid=not errors,
        errors=tuple(errors),
        checks=MappingProxyType(checks),
        slide_count=len(materialized),
    )


validate_candidate_gate = validate_slide_candidate
validate_deck_gate = validate_deck_consistency


__all__ = [
    "DeckGateReport",
    "QualityIssue",
    "SlideGateReport",
    "validate_candidate_gate",
    "validate_deck_consistency",
    "validate_deck_gate",
    "validate_slide_candidate",
]
