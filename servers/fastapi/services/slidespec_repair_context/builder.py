"""Facade-neutral orchestration for authored SlideSpec repair context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from models.slidespec_v1 import RenderRequest, SlideSpec
from services.slidespec_repair_context.base_projection import project_base_candidates
from services.slidespec_repair_context.composition_groups import (
    build_composition_groups,
)
from services.slidespec_repair_context.constraint_projection import (
    project_geometry_issues,
)
from services.slidespec_repair_context.coupled_projection import (
    project_coupled_repairs,
)
from services.slidespec_repair_context.exact_compositions import (
    build_exact_compositions,
)
from services.slidespec_repair_context.glyph_compositions import (
    build_glyph_compositions,
)
from services.slidespec_repair_context.issue_groups import build_issue_groups
from services.slidespec_repair_context.policy import (
    MAX_AFFECTED_BLOCKS,
    MAX_GEOMETRY_COMPOSITIONS,
    MAX_OPERATIONS,
)
from services.slidespec_repair_context.state import RepairContextState
from services.slidespec_repair_pipeline import DomEvidence
from utils.slidespec_constraints import ConstraintReport, constraint_targets
from utils.slidespec_core import render_slidespec


def build_repair_context(
    request: RenderRequest | SlideSpec,
    evidence: DomEvidence,
    browser_metrics: Mapping[str, Any] | None = None,
    constraint_report: ConstraintReport | None = None,
    *,
    render_request_type: type[Any] = RenderRequest,
    render_native: Callable[[Any], Any] = render_slidespec,
) -> dict[str, Any]:
    """Build the historical repair-context payload in its original phase order."""

    state = RepairContextState.build(
        request,
        evidence,
        browser_metrics,
        constraint_report,
        render_request_type=render_request_type,
        render_native=render_native,
    )
    artifacts = project_base_candidates(state)
    project_geometry_issues(state, artifacts)
    project_coupled_repairs(state, artifacts)
    build_issue_groups(state, artifacts)
    build_composition_groups(state, artifacts)
    build_glyph_compositions(state, artifacts)
    build_exact_compositions(state, artifacts)

    spec = state.spec
    refs_by_target = artifacts.diagnostic_refs_by_target
    affected_targets = artifacts.affected_targets
    return {
        "slideId": spec.slideId,
        "baseSpecRevision": spec.specRevision,
        "baseSpecHash": spec.digests.spec,
        "expectedContentDigest": spec.digests.content,
        "diagnosticRefs": tuple(dict.fromkeys(artifacts.diagnostic_refs)),
        "diagnosticRefsByTarget": {
            target: tuple(dict.fromkeys(target_refs)) for target, target_refs in refs_by_target.items()
        },
        "containerBounds": artifacts.container_bounds,
        "fontScale": artifacts.font_scale,
        "glyphFit": artifacts.glyph_fit,
        "imageCrop": artifacts.image_crop,
        "staticTranslation": artifacts.static_translation,
        "connectorRoute": artifacts.connector_route,
        "geometryIssues": artifacts.geometry_issues,
        "geometryIssueGroups": artifacts.geometry_issue_groups,
        "geometryCompositionGroups": artifacts.geometry_composition_groups,
        "geometryGlyphCompositions": tuple(artifacts.geometry_glyph_compositions),
        "geometryExactCompositions": tuple(artifacts.geometry_exact_compositions),
        "geometryExactDiagnostics": tuple(artifacts.geometry_exact_diagnostics),
        "coupledRepairs": tuple(artifacts.coupled_repairs),
        "requiredGeometryIssueGroups": artifacts.required_geometry_issue_groups,
        "maxGeometryCompositions": MAX_GEOMETRY_COMPOSITIONS,
        "relationalConstraints": tuple(
            constraint.model_dump(mode="json")
            for constraint in spec.constraints
            if affected_targets.intersection(constraint_targets(constraint))
        ),
        "limits": {
            "maxOperations": MAX_OPERATIONS,
            "maxAffectedBlocks": MAX_AFFECTED_BLOCKS,
        },
    }
