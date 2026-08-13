"""Fail-closed census for the authored-only compatibility migration.

The presentation profile resolver remains the only authority for persisted deck
identity.  This module adds a read-only slide-evidence cross-check so malformed
and mixed rows are quarantined instead of silently entering a migration cohort.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal, TypeAlias, cast

from services.presentation_profiles import (
    PresentationProfileError,
    resolve_presentation_profile,
)
from templates.v2.persistence import canonicalize_slide_ui

CensusClassification: TypeAlias = Literal[
    "legacy",
    "adaptive",
    "smart",
    "template_v2",
    "authored",
    "mixed",
    "invalid",
]

_CLASSIFICATIONS = frozenset(
    {"legacy", "adaptive", "smart", "template_v2", "authored", "mixed", "invalid"}
)


def _read(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _identifier(value: object) -> str:
    identifier = _read(value, "id")
    return str(identifier) if identifier is not None else "unknown"


@dataclass(frozen=True, slots=True)
class SlideEvidence:
    slide_id: str
    index: int | None
    content_kind: Literal[
        "legacy", "smart", "template_v2", "authored", "untyped_html", "mixed", "invalid"
    ]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PresentationCensusEntry:
    presentation_id: str
    owner_id: str | None
    classification: CensusClassification
    canonical_driver_id: str | None
    quarantine: bool
    quarantine_reason: str | None
    slide_count: int
    slide_evidence: tuple[SlideEvidence, ...]

    def __post_init__(self) -> None:
        if self.classification not in _CLASSIFICATIONS:
            raise ValueError("unsupported_census_classification")

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["slide_evidence"] = [item.as_dict() for item in self.slide_evidence]
        return value


def _slide_evidence(slide: object) -> SlideEvidence:
    slide_id = _identifier(slide)
    index_value = _read(slide, "index")
    index = index_value if isinstance(index_value, int) and not isinstance(index_value, bool) else None
    if index_value is not None and index is None:
        return SlideEvidence(slide_id, None, "invalid", "slide_index_invalid")

    content = _read(slide, "content", {})
    if not isinstance(content, Mapping):
        return SlideEvidence(slide_id, index, "invalid", "slide_content_invalid")
    html = _read(slide, "html_content")
    if html is not None and not isinstance(html, str):
        return SlideEvidence(slide_id, index, "invalid", "slide_html_invalid")
    has_html = bool(html and html.strip())

    ui = _read(slide, "ui")
    if ui is not None:
        try:
            canonicalize_slide_ui(ui)
        except Exception:
            return SlideEvidence(slide_id, index, "invalid", "slide_ui_invalid")
    has_ui = ui is not None

    layout_group = _read(slide, "layout_group", "")
    layout = _read(slide, "layout", "")
    if not isinstance(layout_group, str) or not isinstance(layout, str):
        return SlideEvidence(slide_id, index, "invalid", "slide_layout_identity_invalid")
    authored_marker = (
        layout_group == "authored"
        or layout.startswith("authored:")
        or content.get("__authored__") is True
    )
    smart_marker = layout_group == "smart-html" or layout == "smart-html"

    signal_count = sum((has_ui, authored_marker, smart_marker))
    if (has_ui and has_html) or signal_count > 1:
        return SlideEvidence(slide_id, index, "mixed", "slide_content_models_conflict")
    if has_ui:
        return SlideEvidence(slide_id, index, "template_v2", "native_ui")
    if authored_marker:
        if not has_html:
            return SlideEvidence(slide_id, index, "invalid", "authored_html_missing")
        return SlideEvidence(slide_id, index, "authored", "authored_marker")
    if smart_marker:
        if not has_html:
            return SlideEvidence(slide_id, index, "invalid", "smart_html_missing")
        return SlideEvidence(slide_id, index, "smart", "smart_html_marker")
    if has_html:
        return SlideEvidence(slide_id, index, "untyped_html", "html_without_slide_marker")
    return SlideEvidence(slide_id, index, "legacy", "legacy_elements")


def _canonical_classification(presentation: object, driver_id: str) -> CensusClassification:
    if driver_id == "standard":
        mode = _read(presentation, "mode")
        return "adaptive" if mode == "adaptive" or _read(presentation, "deck_plan") is not None else "legacy"
    return cast(
        CensusClassification,
        {"smart": "smart", "template-v2": "template_v2", "authored": "authored"}[driver_id],
    )


def classify_presentation(
    presentation: object,
    slides: Sequence[object],
) -> PresentationCensusEntry:
    """Return exactly one census classification for a presentation and its slides."""

    presentation_id = _identifier(presentation)
    owner = _read(presentation, "owner_id")
    owner_id = str(owner) if owner is not None else None
    evidence = tuple(_slide_evidence(slide) for slide in slides)

    try:
        profile = resolve_presentation_profile(presentation)
    except PresentationProfileError as error:
        return PresentationCensusEntry(
            presentation_id,
            owner_id,
            "invalid",
            None,
            True,
            error.code,
            len(evidence),
            evidence,
        )

    malformed = next((item for item in evidence if item.content_kind == "invalid"), None)
    if malformed is not None:
        return PresentationCensusEntry(
            presentation_id,
            owner_id,
            "invalid",
            profile.driver_id,
            True,
            malformed.reason,
            len(evidence),
            evidence,
        )

    canonical = _canonical_classification(presentation, profile.driver_id)
    expected_evidence = {
        "legacy": "legacy",
        "adaptive": "legacy",
        "smart": "smart",
        "template_v2": "template_v2",
        "authored": "authored",
    }[canonical]
    observed: set[str] = set()
    for item in evidence:
        kind = item.content_kind
        if kind == "untyped_html" and canonical in {"smart", "authored"}:
            kind = canonical
        observed.add(kind)
    if "mixed" in observed or any(kind != expected_evidence for kind in observed):
        return PresentationCensusEntry(
            presentation_id,
            owner_id,
            "mixed",
            profile.driver_id,
            True,
            "canonical_profile_slide_evidence_conflict",
            len(evidence),
            evidence,
        )
    return PresentationCensusEntry(
        presentation_id,
        owner_id,
        canonical,
        profile.driver_id,
        False,
        None,
        len(evidence),
        evidence,
    )


def build_census(
    presentations: Sequence[object],
    slides: Sequence[object],
) -> tuple[PresentationCensusEntry, ...]:
    """Census every supplied presentation, including decks with zero slides."""

    by_presentation: dict[str, list[object]] = {}
    for slide in slides:
        by_presentation.setdefault(str(_read(slide, "presentation")), []).append(slide)
    return tuple(
        classify_presentation(
            presentation,
            sorted(
                by_presentation.get(_identifier(presentation), []),
                key=lambda slide: (_read(slide, "index", -1), _identifier(slide)),
            ),
        )
        for presentation in presentations
    )


__all__ = [
    "CensusClassification",
    "PresentationCensusEntry",
    "SlideEvidence",
    "build_census",
    "classify_presentation",
]
