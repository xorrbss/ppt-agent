from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from services.authored_composition_policy import (
    SlideCapacity,
    blocking_safety_issues,
    bounded_capacity_repair_eligible,
    capacity_violations,
)
from utils.authored_dom_quality import inspect_authored_dom


_CAPACITY_PREFIXES = (
    "images:",
    "qids:",
    "visible_text:",
    "title:",
    "body:",
    "browser_images:",
    "browser_title:",
    "browser_body:",
    "browser_qid_",
    "browser_font_",
    "browser_text_not_visible:",
    "font:",
    "supporting_font:",
)


#: ``data-qid="..."`` of an element that also carries ``data-source-block-id``.
#: Those elements render immutable source text; everything else on the slide is
#: the model's own composition.
_SOURCE_OWNER_QID = re.compile(
    r"<[^>]*?data-qid=\"([^\"]+)\"[^>]*?data-source-block-id=|"
    r"<[^>]*?data-source-block-id=[^>]*?data-qid=\"([^\"]+)\"",
    re.IGNORECASE,
)


#: A QID names an element; anything else in a violation tail is a measurement.
_QID_TOKEN = re.compile(r"[A-Za-z][\w.\-]*(?::[\w.\-]+)*")


def _source_owner_qids(html: str) -> frozenset[str]:
    """QIDs of the elements that own source text on this slide."""

    found: set[str] = set()
    for first, second in _SOURCE_OWNER_QID.findall(str(html or "")):
        name = (first or second or "").strip()
        if name:
            found.add(name)
    return frozenset(found)


def _names_a_source_owner(violation: str, source_qids: frozenset[str]) -> bool:
    """Whether a capacity violation is about source text rather than decoration.

    A violation reads ``<check>:<qid>[,<qid>...]`` or carries a measurement
    instead of an element.  Anything this cannot resolve to the model's own
    elements is treated as source-owned, so the fail-closed classification is
    the default and only a violation proven to be about decoration relaxes it.
    """

    _, separator, tail = str(violation).rpartition(":")
    if not separator:
        return True
    names = [
        item.strip()
        for item in tail.split(",")
        if _QID_TOKEN.fullmatch(item.strip())
    ]
    if not names:
        # A measurement rather than an element ("visible_text:1200>900"): the
        # slide as a whole is over budget, which is the source text's problem.
        return True
    return any(
        name in source_qids or name.startswith("source-block-") for name in names
    )


@dataclass(frozen=True)
class AuthoredSlideArtifact:
    """Exact HTML/PNG/Chromium tuple owned by one slide checkpoint."""

    html: str
    png: bytes
    browser_metrics: Mapping[str, Any]


@dataclass(frozen=True)
class AuthoredSlidePreflight:
    slide_index: int
    classification: str
    code: str | None
    violations: tuple[str, ...]
    retryable: bool
    terminal: bool

    @property
    def provider_call_allowed(self) -> bool:
        return self.classification == "provider_repair"


def preflight_authored_provider_repair(
    *,
    slide_index: int,
    artifact: AuthoredSlideArtifact,
    capacity: SlideCapacity,
    profile_id: str,
    reference_mode: str,
    repair_attempted: bool = False,
) -> AuthoredSlidePreflight:
    """Classify one existing slide before any repair-provider call.

    The decision is entirely derived from the exact checkpoint tuple.  QID
    coverage, font loading/floors, visible text, and capacity remain delegated
    to the existing hard gate.  DOM safety is evaluated in the same call so a
    title-edge or semantic-adjacent geometry blocker cannot be converted into a
    provider retry merely because capacity happens to pass.
    """

    violations = list(
        capacity_violations(artifact.html, artifact.browser_metrics, capacity)
    )
    report = inspect_authored_dom(
        artifact.html,
        artifact.browser_metrics,
        reference_mode=reference_mode,
        strict_structural_off_canvas=(str(profile_id).strip().lower() != "ordinary"),
    )
    safety_issues = blocking_safety_issues(report)
    safety_violations = tuple(
        "safety:"
        + str(getattr(issue, "category", "unknown"))
        + ":"
        + ",".join(str(qid) for qid in getattr(issue, "qids", ()) if qid)
        for issue in safety_issues
    )
    all_violations = tuple(dict.fromkeys((*violations, *safety_violations)))
    if not all_violations:
        return AuthoredSlidePreflight(
            slide_index=slide_index,
            classification="provider_repair",
            code=None,
            violations=(),
            retryable=True,
            terminal=False,
        )

    eyebrow_capacity_only = tuple(violations) == (
        "browser_text_not_visible:header.eyebrow",
    )
    eyebrow_safety_only = bool(safety_issues) and all(
        getattr(issue, "category", None) == "clipping"
        and set(getattr(issue, "qids", ())).issubset(
            {"header.eyebrow", "header.band"}
        )
        for issue in safety_issues
    )
    deterministic_repair = (
        not repair_attempted
        and bounded_capacity_repair_eligible(
            artifact.html,
            artifact.browser_metrics,
            capacity,
            profile_id=profile_id,
        )
        and (not safety_issues or (eyebrow_capacity_only and eyebrow_safety_only))
    )
    if deterministic_repair:
        return AuthoredSlidePreflight(
            slide_index=slide_index,
            classification="deterministic_repair",
            code=None,
            violations=all_violations,
            retryable=False,
            terminal=False,
        )

    # A capacity violation is terminal because verbatim source text cannot be
    # shortened to fit.  That reasoning covers the elements that own source
    # text, not the decoration the model drew around them: a hero mark that
    # renders no visible glyph and lands on top of a source block is the
    # model's own composition and exactly what a repair call can fix.  Slide 16
    # of operation 3d0b9e72 was ended without a single repair call for one such
    # "+" ornament.
    source_qids = _source_owner_qids(artifact.html)
    capacity_violations_found = [
        item for item in violations if item.startswith(_CAPACITY_PREFIXES)
    ]
    source_capacity_blocked = bool(capacity_violations_found) and (
        # An ordinary authored deck owns all of its text, so the distinction
        # does not exist there and the original classification stands.
        not source_qids
        or any(
            _names_a_source_owner(item, source_qids)
            for item in capacity_violations_found
        )
    )
    code = (
        "authored_slide_capacity_exceeded"
        if source_capacity_blocked
        else (
            "authored_composition_safety_gate_failed"
            if safety_issues
            else "authored_deterministic_preflight_failed"
        )
    )
    return AuthoredSlidePreflight(
        slide_index=slide_index,
        classification="terminal",
        code=code,
        violations=all_violations,
        retryable=False,
        terminal=True,
    )


__all__ = [
    "AuthoredSlideArtifact",
    "AuthoredSlidePreflight",
    "preflight_authored_provider_repair",
]
