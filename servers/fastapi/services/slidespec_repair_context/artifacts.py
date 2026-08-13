"""Per-call mutable outputs shared by ordered repair-context bands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RepairArtifacts:
    container_bounds: list[dict[str, Any]] = field(default_factory=list)
    font_scale: list[dict[str, Any]] = field(default_factory=list)
    glyph_fit: list[dict[str, Any]] = field(default_factory=list)
    image_crop: list[dict[str, Any]] = field(default_factory=list)
    static_translation: list[dict[str, Any]] = field(default_factory=list)
    connector_route: list[dict[str, Any]] = field(default_factory=list)
    diagnostic_refs: list[str] = field(default_factory=list)
    diagnostic_refs_by_target: dict[str, list[str]] = field(default_factory=dict)
    affected_targets: set[str] = field(default_factory=set)
    geometry_issues: list[dict[str, Any]] = field(default_factory=list)
    geometry_issue_groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    composition_qids: dict[str, set[str]] = field(default_factory=dict)
    required_geometry_issue_groups: tuple[str, ...] = ()
    geometry_composition_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    geometry_glyph_compositions: list[dict[str, Any]] = field(default_factory=list)
    geometry_exact_compositions: list[dict[str, Any]] = field(default_factory=list)
    geometry_exact_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    coupled_repairs: list[dict[str, Any]] = field(default_factory=list)
