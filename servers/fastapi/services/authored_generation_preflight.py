from __future__ import annotations

from dataclasses import dataclass
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

    code = (
        "authored_slide_capacity_exceeded"
        if any(item.startswith(_CAPACITY_PREFIXES) for item in violations)
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
