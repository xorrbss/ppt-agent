from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

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


#: A QID names an element; anything else in a violation tail is a measurement.
_QID_TOKEN = re.compile(r"[A-Za-z][\w.\-]*(?::[\w.\-]+)*")


def is_slide_over_budget(violation: str) -> bool:
    """Whether a capacity violation says the slide's text does not fit.

    A violation reads ``<check>:<qid>[,<qid>...]`` when it is about one
    element, and ``<check>:<measured>><allowed>`` when it is about the slide's
    budget.  Only the second kind is the impossibility that ends a verbatim
    restyle: its text cannot be shortened to fit.  Which element ended up
    invisible, clipped or off-canvas is a layout mistake, and re-laying out the
    slide is what the repair call does.
    """

    _, separator, tail = str(violation).rpartition(":")
    if not separator:
        return True
    names = [
        item.strip()
        for item in tail.split(",")
        if _QID_TOKEN.fullmatch(item.strip())
    ]
    return not names


def over_budget_violations(violations: Iterable[str]) -> tuple[str, ...]:
    """The violations that measure a slide against its budget, not an element.

    A violation that names no element at all -- ``browser_metrics_unavailable``
    is the one that matters -- is counted here so every caller stays
    fail-closed on an unmeasured slide: only a violation that positively names
    an element is a layout defect a re-layout can fix.
    """

    return tuple(item for item in violations if is_slide_over_budget(item))


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
    # shortened to fit.  That reasoning belongs to the violations that measure
    # the slide against its budget, not to the ones naming an element that
    # ended up invisible, clipped or off-canvas: those are layout mistakes and
    # re-laying out the slide is exactly what a repair call does. Operation
    # 3d0b9e72 was ended for a "+" ornament that rendered no glyph, and
    # 069c620d for one clipped block on a page carrying 168 characters --
    # neither deck was over budget, and neither got a repair call. That
    # distinction is not particular to a restyled deck: an ordinary authored
    # slide can carry a server-generated infographic figure (the component
    # library in utils/authored_infographic/) whose text is held verbatim to
    # the plan by the checker, so it is no more the model's to shorten than a
    # restyled slide's source block is. Slide 14 of a 24-slide operation was
    # ended for exactly that figure's description node pushed off the bottom
    # of the canvas, with no measurement anywhere over budget.
    capacity_violations_found = [
        item for item in violations if item.startswith(_CAPACITY_PREFIXES)
    ]
    source_capacity_blocked = bool(over_budget_violations(capacity_violations_found))
    safety_qids = {
        str(qid)
        for issue in safety_issues
        for qid in getattr(issue, "qids", ())
        if qid
    }
    layout_visibility_violations = {
        f"browser_text_not_visible:{qid}" for qid in safety_qids
    }
    layout_repairable = bool(safety_issues) and not source_capacity_blocked and all(
        item in layout_visibility_violations for item in violations
    )
    if layout_repairable:
        return AuthoredSlidePreflight(
            slide_index=slide_index,
            classification="provider_repair",
            code="authored_composition_safety_gate_failed",
            violations=all_violations,
            retryable=True,
            terminal=False,
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
    "is_slide_over_budget",
    "over_budget_violations",
    "preflight_authored_provider_repair",
]
