"""Canonical, versioned Template V2 compiler model.

The wire payload remains available on every node so newer upstream fields can
round-trip without being silently discarded.  Semantic consumers operate on
this model and the render plan, never on dialect-specific repeat heuristics.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AST_SCHEMA_VERSION = "presenton.template-ast/v1"
RENDER_PLAN_SCHEMA_VERSION = "presenton.template-render-plan/v1"


class AstModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Geometry(AstModel):
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    rotation: float | None = None


class TemplateCapabilityReport(AstModel):
    validation: bool = True
    content_schema: bool = True
    hydration: bool = True
    editor_projection: bool = True
    html_render: bool = True
    pptx_compile: bool = True
    review_required: bool = False
    issues: tuple[str, ...] = ()


class RepeatAnchor(AstModel):
    anchor_id: str
    source_index: int = Field(ge=0)
    geometry: Geometry
    source: dict[str, Any]


class RepeatNode(AstModel):
    repeat_id: str
    component_id: str
    source_path: str
    content_field: str
    arrangement: Literal["flow", "grid", "positioned"]
    selection: Literal["prototype", "prefix"]
    min_items: int = Field(ge=0)
    max_items: int = Field(ge=1)
    item_signature: str
    source_indices: tuple[int, ...]
    prototype: dict[str, Any] | None = None
    anchors: tuple[RepeatAnchor, ...] = ()
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bounds_and_strategy_are_consistent(self) -> "RepeatNode":
        if self.max_items < self.min_items:
            raise ValueError("repeat_bounds_invalid")
        if self.selection == "prototype" and self.prototype is None:
            raise ValueError("repeat_prototype_required")
        if self.selection == "prefix" and len(self.anchors) != self.max_items:
            raise ValueError("repeat_anchor_capacity_mismatch")
        return self


class CanonicalComponent(AstModel):
    component_id: str
    source_path: str
    source: dict[str, Any]
    repeats: tuple[RepeatNode, ...] = ()


class CanonicalLayout(AstModel):
    layout_id: str
    source_path: str
    source: dict[str, Any]
    components: tuple[CanonicalComponent, ...]


class CanonicalTemplateAst(AstModel):
    schema_version: Literal["presenton.template-ast/v1"] = AST_SCHEMA_VERSION
    source_dialect: Literal["57b194b", "a3352f3c", "future"]
    source_version: str | None = None
    source: dict[str, Any]
    layouts: tuple[CanonicalLayout, ...]
    capabilities: TemplateCapabilityReport


class RenderPlanRepeat(AstModel):
    repeat_id: str
    component_id: str
    source_path: str
    content_field: str
    arrangement: Literal["flow", "grid", "positioned"]
    selected_count: int = Field(ge=0)
    source_indices: tuple[int, ...]
    selected_sources: tuple[dict[str, Any], ...]


class TemplateRenderPlan(AstModel):
    schema_version: Literal["presenton.template-render-plan/v1"] = (
        RENDER_PLAN_SCHEMA_VERSION
    )
    ast_schema_version: Literal["presenton.template-ast/v1"] = AST_SCHEMA_VERSION
    layout_id: str
    source_layout: dict[str, Any]
    content: dict[str, Any]
    repeats: tuple[RenderPlanRepeat, ...]
    capabilities: TemplateCapabilityReport


class TemplateCompilerContract(AstModel):
    """Schema-generation root covering both AST and render-plan artifacts."""

    ast: CanonicalTemplateAst
    render_plan: TemplateRenderPlan


__all__ = [
    "AST_SCHEMA_VERSION",
    "RENDER_PLAN_SCHEMA_VERSION",
    "CanonicalComponent",
    "CanonicalLayout",
    "CanonicalTemplateAst",
    "Geometry",
    "RenderPlanRepeat",
    "RepeatAnchor",
    "RepeatNode",
    "TemplateCapabilityReport",
    "TemplateCompilerContract",
    "TemplateRenderPlan",
]
