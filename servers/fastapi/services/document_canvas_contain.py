"""Immutable geometry for one pre-capture contain transform.

This module does not modify HTML or enable an unsupported rendering path.
Callers must provide measured source geometry and preserve the complete source
rectangle, including its background, unless an explicit content frame exists.
Exporters consume the resulting target coordinates and must not apply this plan
again. Typography policy belongs to the caller, not to the canvas preset.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from services.document_canvas import canvas_physical_size, parse_canvas_metadata


def _finite_number(value: float, name: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"document_canvas_contain_invalid_{name}")
    if positive and value <= 0:
        raise ValueError(f"document_canvas_contain_invalid_{name}")
    return float(value)


@dataclass(frozen=True, slots=True)
class CanvasRect:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        for name in ("x", "y", "width", "height"):
            _finite_number(getattr(self, name), name, positive=name in ("width", "height"))
        _finite_number(self.x + self.width, "right")
        _finite_number(self.y + self.height, "bottom")


@dataclass(frozen=True, slots=True)
class ContainPlan:
    source_rect: CanvasRect
    target_safe_area: CanvasRect
    scale: float
    offset_x: float
    offset_y: float
    point_per_unit: float
    target_width: int
    target_height: int

    def map_point(self, x: float, y: float) -> tuple[float, float]:
        return (
            _finite_number(x, "x") * self.scale + self.offset_x,
            _finite_number(y, "y") * self.scale + self.offset_y,
        )

    def unmap_point(self, x: float, y: float) -> tuple[float, float]:
        return (
            (_finite_number(x, "x") - self.offset_x) / self.scale,
            (_finite_number(y, "y") - self.offset_y) / self.scale,
        )

    @property
    def transformed_bounds(self) -> CanvasRect:
        x, y = self.map_point(self.source_rect.x, self.source_rect.y)
        return CanvasRect(x, y, self.source_rect.width * self.scale,
                          self.source_rect.height * self.scale)


def build_contain_plan(*, source_rect: CanvasRect, target_canvas: object) -> ContainPlan:
    """Center one intact source rectangle in the target's safe area.

    No crop, separate background stretching, visible-bounds inference, or font
    resizing happens here. Source origins may be nonzero, as in an SVG viewBox.
    """
    if not isinstance(source_rect, CanvasRect):
        raise ValueError("document_canvas_contain_source_geometry_required")
    canvas = parse_canvas_metadata(target_canvas)
    safe = canvas.safe_area
    target = CanvasRect(safe.x, safe.y, safe.width, safe.height)
    scale = _finite_number(min(target.width / source_rect.width,
                               target.height / source_rect.height), "scale", positive=True)
    offset_x = _finite_number(target.x + (target.width - source_rect.width * scale) / 2
                              - source_rect.x * scale, "offset_x")
    offset_y = _finite_number(target.y + (target.height - source_rect.height * scale) / 2
                              - source_rect.y * scale, "offset_y")
    width_emu, _ = canvas_physical_size(canvas)
    return ContainPlan(source_rect, target, scale, offset_x, offset_y,
                       width_emu / canvas.width_px / 12700, canvas.width_px, canvas.height_px)


@dataclass(frozen=True, slots=True)
class ContainTypographyReceipt:
    policy_id: str
    measurement_space: Literal["source-local", "target-transformed"]
    effective_font_pt: float
    minimum_font_pt: float
    contain_scale_applied: bool


@dataclass(frozen=True, slots=True)
class ContainCssTypographyReceipt:
    """Effective browser-space font size for an explicit CSS-pixel policy."""

    policy_id: str
    measurement_space: Literal["source-local", "target-transformed"]
    effective_font_px: float
    minimum_font_px: float
    contain_scale_applied: bool


class ContainTypographyError(ValueError):
    def __init__(self, effective_font_pt: float, minimum_font_pt: float):
        super().__init__("document_canvas_contain_font_floor")
        self.effective_font_pt = effective_font_pt
        self.minimum_font_pt = minimum_font_pt


class ContainCssTypographyError(ValueError):
    def __init__(self, effective_font_px: float, minimum_font_px: float):
        super().__init__("document_canvas_contain_css_font_floor")
        self.effective_font_px = effective_font_px
        self.minimum_font_px = minimum_font_px


def validate_contain_typography(
    plan: ContainPlan, *, measured_min_font_units: float,
    minimum_physical_pt: float, policy_id: str,
    measurement_space: Literal["source-local", "target-transformed"],
) -> ContainTypographyReceipt:
    """Validate the measured final physical size against an explicit policy.

    source-local measurements receive the contain scale exactly once; a DOM
    measurement that already includes that transform uses target-transformed.
    This is geometric admission only, not evidence of actual text rendering.
    """
    measured = _finite_number(measured_min_font_units, "font_measurement", positive=True)
    minimum = _finite_number(minimum_physical_pt, "font_policy", positive=True)
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ValueError("document_canvas_contain_font_policy_required")
    if measurement_space not in {"source-local", "target-transformed"}:
        raise ValueError("document_canvas_contain_measurement_space_required")
    apply_scale = measurement_space == "source-local"
    effective = _finite_number(measured * (plan.scale if apply_scale else 1)
                               * plan.point_per_unit, "effective_font", positive=True)
    # Only floating-point representation noise is tolerated, never a px/pt fit margin.
    if effective < minimum and not math.isclose(effective, minimum, rel_tol=0, abs_tol=1e-12):
        raise ContainTypographyError(effective, minimum)
    return ContainTypographyReceipt(policy_id, measurement_space, effective, minimum, apply_scale)


def validate_contain_css_typography(
    plan: ContainPlan, *, measured_min_font_px: float,
    minimum_css_px: float, policy_id: str,
    measurement_space: Literal["source-local", "target-transformed"],
) -> ContainCssTypographyReceipt:
    """Validate a CSS-pixel floor without reinterpreting it as points.

    A source-local computed font size receives the contain scale exactly once.
    Browser metrics collected after the transform must use target-transformed.
    """

    measured = _finite_number(measured_min_font_px, "font_measurement", positive=True)
    minimum = _finite_number(minimum_css_px, "font_policy", positive=True)
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ValueError("document_canvas_contain_font_policy_required")
    if measurement_space not in {"source-local", "target-transformed"}:
        raise ValueError("document_canvas_contain_measurement_space_required")
    apply_scale = measurement_space == "source-local"
    effective = _finite_number(
        measured * (plan.scale if apply_scale else 1),
        "effective_font",
        positive=True,
    )
    if effective < minimum and not math.isclose(
        effective, minimum, rel_tol=0, abs_tol=1e-12
    ):
        raise ContainCssTypographyError(effective, minimum)
    return ContainCssTypographyReceipt(
        policy_id, measurement_space, effective, minimum, apply_scale
    )
