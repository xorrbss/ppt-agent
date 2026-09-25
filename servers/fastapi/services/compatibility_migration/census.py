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
from templates.native_ui.persistence import canonicalize_slide_ui

#: What a censused deck can be. A deck in one of the retired React-template
#: formats is ``invalid``: its profile cannot resolve, which is exactly the
#: report an operator needs. Slide-level evidence keeps its own richer
#: vocabulary below, because a slide's markers still distinguish the formats
#: even when the deck as a whole can no longer be described.
CensusClassification: TypeAlias = Literal[
    "template_v2",
    "authored",
    "svg",
    "mixed",
    "invalid",
]

_CLASSIFICATIONS = frozenset({"template_v2", "authored", "svg", "mixed", "invalid"})


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
        "legacy",
        "smart",
        "template_v2",
        "authored",
        "svg",
        "untyped_html",
        "mixed",
        "invalid",
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

    svg_content = _read(slide, "svg_content")
    if svg_content is not None and not isinstance(svg_content, str):
        return SlideEvidence(slide_id, index, "invalid", "slide_svg_invalid")
    has_svg = bool(svg_content and svg_content.strip())
    svg_marker = layout_group == "svg" or has_svg

    signal_count = sum((has_ui, authored_marker, smart_marker, svg_marker))
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
    if svg_marker:
        if not has_svg:
            return SlideEvidence(slide_id, index, "invalid", "svg_content_missing")
        return SlideEvidence(slide_id, index, "svg", "svg_marker")
    if has_html:
        return SlideEvidence(slide_id, index, "untyped_html", "html_without_slide_marker")
    return SlideEvidence(slide_id, index, "legacy", "legacy_elements")


def _canonical_classification(driver_id: str) -> CensusClassification:
    """Name the format of a deck whose profile resolved.

    Only these formats can. The retired React-template drivers this fork no
    longer ships (``standard``, ``smart``) are not in the driver registry, so
    a row in one of those formats never reaches here: its profile fails to
    resolve and the caller reports it ``invalid``, which is what the census
    exists to surface.
    """
    return cast(
        CensusClassification,
        {"template-v2": "template_v2", "authored": "authored", "svg-v1": "svg"}[driver_id],
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

    canonical = _canonical_classification(profile.driver_id)
    expected_evidence = {
        "template_v2": "template_v2",
        "authored": "authored",
        "svg": "svg",
    }[canonical]
    observed: set[str] = set()
    for item in evidence:
        kind = item.content_kind
        if kind == "untyped_html" and canonical == "authored":
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
