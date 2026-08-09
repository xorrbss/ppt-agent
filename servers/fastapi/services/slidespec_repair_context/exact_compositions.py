"""Ordered orchestration for exact Chrome-proven compositions."""

from __future__ import annotations

from typing import Any

from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.exact_bottom_stack import (
    build_exact_bottom_stack,
)
from services.slidespec_repair_context.exact_vertical_pack import (
    build_exact_vertical_pack,
)
from services.slidespec_repair_context.state import RepairContextState


def build_exact_compositions(state: RepairContextState, artifacts: RepairArtifacts) -> None:
    source = state.source
    if source is None:
        return

    viewport_width = float(source.viewportWidth)
    viewport_height = float(source.viewportHeight)
    constraint_report = state.constraint_report
    required_postconditions: dict[str, Any] = {
        "domNoNewIssue": True,
        "hardFailure": False,
        "softCostMaximum": (constraint_report.soft_cost if constraint_report is not None else 0.0),
        "unknownHard": [],
        "roundTripErrors": [],
    }
    build_exact_bottom_stack(
        state,
        artifacts,
        viewport_height=viewport_height,
        required_postconditions=required_postconditions,
    )
    build_exact_vertical_pack(
        state,
        artifacts,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        required_postconditions=required_postconditions,
    )
