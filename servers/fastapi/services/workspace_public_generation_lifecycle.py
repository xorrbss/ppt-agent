"""The public generation lifecycle state machine.

This module owns the ``public-generation-lifecycle/v1`` vocabulary that a
public V2 generation projects to API consumers: the ordered state tuple, the
forward/wait/terminal partitions of it, the internal-stage-to-public-state map,
and the two pure predicates that decide whether a transition is legal and which
monotonic CAS steps reach an observed state.

It was split out of ``services/workspace_operation_runner.py`` because the
state machine is a closed, side-effect-free contract that several runner call
sites consult, while the runner module itself is claim/lease orchestration. The
projection builder ``_public_generation_progress`` deliberately stays in the
runner: it needs the runner's event-payload sanitizer, and importing that back
here would form a cycle.
"""

from __future__ import annotations


PUBLIC_GENERATION_LIFECYCLE_SCHEMA = "public-generation-lifecycle/v1"
PUBLIC_GENERATION_STATES = (
    "accepted",
    "queued",
    "outlining",
    "authoring",
    "rendering",
    "quality_gate",
    "staging",
    "published",
    "export_queued",
    "exporting",
    "completed",
    "capacity_waiting",
    "retry_waiting",
    "cancelled",
    "failed",
    "dead_letter",
)
_PUBLIC_GENERATION_FORWARD_STATES = PUBLIC_GENERATION_STATES[:11]
_PUBLIC_GENERATION_WAIT_STATES = frozenset({"capacity_waiting", "retry_waiting"})
_PUBLIC_GENERATION_TERMINAL_STATES = frozenset(
    {"completed", "cancelled", "failed", "dead_letter"}
)
_PUBLIC_GENERATION_STAGE_STATES = {
    "generation_started": "outlining",
    "content_brief": "outlining",
    "outline": "outlining",
    "authored_outline": "outlining",
    "authored_generation": "authoring",
    "authored_slide_generation": "authoring",
    "authored_author": "authoring",
    "authored_illustrate": "authoring",
    "authored_render": "rendering",
    "authored_repair": "quality_gate",
    "authored_checkpoint": "quality_gate",
    "authored_generation_finalizing": "quality_gate",
    # An immutable export is staged before the publication CAS.
    "authored_export": "staging",
    "authored_publication": "staging",
}

_SVG_JOB_STAGE_ORDER = {
    stage: index
    for index, stage in enumerate((
        "outline", "author", "illustrate", "render", "persist", "repair",
        "checkpoint", "export", "publication",
    ))
}


def monotonic_svg_job_stage(current: str | None, candidate: str) -> str:
    """Project a page-local SVG update without regressing deck progress."""
    current_order = _SVG_JOB_STAGE_ORDER.get(current)
    candidate_order = _SVG_JOB_STAGE_ORDER[candidate]
    if current_order is None or current_order > candidate_order:
        return current or candidate
    return candidate


def _public_generation_transition_allowed(current: str, target: str) -> bool:
    if current == target:
        return True
    if current in _PUBLIC_GENERATION_TERMINAL_STATES:
        return False
    if target in {"cancelled", "failed", "dead_letter"}:
        return True
    if target in _PUBLIC_GENERATION_WAIT_STATES:
        return current not in {"accepted", "published", "export_queued", "exporting"}
    if current in _PUBLIC_GENERATION_WAIT_STATES:
        return target == "queued"
    try:
        return (
            _PUBLIC_GENERATION_FORWARD_STATES.index(target)
            == _PUBLIC_GENERATION_FORWARD_STATES.index(current) + 1
        )
    except ValueError:
        return False


def _public_generation_transition_path(current: str, target: str) -> tuple[str, ...]:
    """Return the monotonic CAS steps needed to reach an observed lifecycle state."""

    if current == target:
        return (target,)
    if current in _PUBLIC_GENERATION_FORWARD_STATES and target in _PUBLIC_GENERATION_FORWARD_STATES:
        current_index = _PUBLIC_GENERATION_FORWARD_STATES.index(current)
        target_index = _PUBLIC_GENERATION_FORWARD_STATES.index(target)
        if target_index > current_index:
            return _PUBLIC_GENERATION_FORWARD_STATES[current_index + 1 : target_index + 1]
    if _public_generation_transition_allowed(current, target):
        return (target,)
    raise ValueError(f"public_generation_transition_invalid:{current}:{target}")
