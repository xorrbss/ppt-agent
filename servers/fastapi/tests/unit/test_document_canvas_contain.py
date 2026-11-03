from dataclasses import FrozenInstanceError

import pytest

from services.document_canvas import document_canvas_for_orientation
from services.document_canvas_contain import (
    CanvasRect, ContainCssTypographyError, ContainTypographyError,
    build_contain_plan, validate_contain_css_typography, validate_contain_typography,
)


def test_entire_source_contained_and_centered_with_inverse():
    source = CanvasRect(0, 0, 1280, 720)
    plan = build_contain_plan(source_rect=source,
                              target_canvas=document_canvas_for_orientation("landscape"))
    assert plan.scale == 0.834375
    bounds = plan.transformed_bounds
    assert (bounds.x, bounds.y, bounds.width, bounds.height) == (60, 119.625, 1068, 600.75)
    # Every source corner, including background, remains inside the safe area.
    for point in ((0, 0), (1280, 0), (0, 720), (1280, 720)):
        target = plan.map_point(*point)
        assert 60 <= target[0] <= 1128 and 60 <= target[1] <= 780
        assert plan.unmap_point(*target) == pytest.approx(point, abs=1e-10)
    with pytest.raises(FrozenInstanceError):
        plan.scale = 2
    with pytest.raises(FrozenInstanceError):
        plan.source_rect.width = 1


def test_nonzero_source_origin_and_portrait_safe_area():
    plan = build_contain_plan(source_rect=CanvasRect(20, -10, 720, 1040),
                              target_canvas=document_canvas_for_orientation("portrait"))
    assert plan.scale == 1
    assert plan.map_point(20, -10) == (60, 74)
    assert plan.map_point(740, 1030) == (780, 1114)
    assert plan.unmap_point(60, 74) == (20, -10)


def test_declared_physical_floor_and_already_transformed_measurement():
    plan = build_contain_plan(source_rect=CanvasRect(0, 0, 1280, 720),
                              target_canvas=document_canvas_for_orientation("portrait"))
    # The target width gives scale 0.5625; physical output uses 9000/12700 pt/U.
    before = validate_contain_typography(plan, measured_min_font_units=24,
        minimum_physical_pt=9.5, policy_id="test-physical-policy", measurement_space="source-local")
    after = validate_contain_typography(plan, measured_min_font_units=13.5,
        minimum_physical_pt=9.5, policy_id="test-physical-policy", measurement_space="target-transformed")
    assert before.effective_font_pt == pytest.approx(9.566929133858267)
    assert before.effective_font_pt == after.effective_font_pt
    assert before.contain_scale_applied and not after.contain_scale_applied
    with pytest.raises(ContainTypographyError) as error:
        validate_contain_typography(plan, measured_min_font_units=20,
            minimum_physical_pt=9.5, policy_id="test-physical-policy", measurement_space="source-local")
    assert error.value.effective_font_pt == pytest.approx(7.97244094488189)
    # The geometry helper does not impose a global font policy.
    assert validate_contain_typography(plan, measured_min_font_units=20,
        minimum_physical_pt=7, policy_id="different-explicit-policy",
        measurement_space="source-local").effective_font_pt > 7


def test_dense_css_floor_applies_contain_scale_once_without_pt_reinterpretation():
    plan = build_contain_plan(source_rect=CanvasRect(0, 0, 1280, 720),
                              target_canvas=document_canvas_for_orientation("portrait"))
    receipt = validate_contain_css_typography(
        plan, measured_min_font_px=16, minimum_css_px=7,
        policy_id="dense-source-7px", measurement_space="source-local")
    assert receipt.effective_font_px == 9
    assert receipt.contain_scale_applied
    with pytest.raises(ContainCssTypographyError):
        validate_contain_css_typography(
            plan, measured_min_font_px=12, minimum_css_px=7,
            policy_id="dense-source-7px", measurement_space="source-local")


@pytest.mark.parametrize("width", [0, -1, True, "1280", float("nan"), float("inf")])
def test_unmeasurable_source_rejected(width):
    with pytest.raises(ValueError):
        CanvasRect(0, 0, width, 720)


def test_font_measurement_and_policy_must_be_explicit():
    plan = build_contain_plan(source_rect=CanvasRect(0, 0, 100, 100),
                              target_canvas=document_canvas_for_orientation("landscape"))
    with pytest.raises(ValueError, match="measurement_space"):
        validate_contain_typography(plan, measured_min_font_units=20,
            minimum_physical_pt=9.5, policy_id="test", measurement_space="unknown")
    with pytest.raises(ValueError, match="font_measurement"):
        validate_contain_typography(plan, measured_min_font_units=float("nan"),
            minimum_physical_pt=9.5, policy_id="test", measurement_space="source-local")


def test_fixed_master_responsive_css_validates_every_branch_without_changing_source():
    from utils.source_canvas_css import flatten_layout_conditions
    from utils.renderer_isolation import _validate_css
    css = "@media(min-width:1000px){main{width:80vw;font-size:2em}}@media(max-width:999px){main{width:50vw}}"
    flattened = flatten_layout_conditions(css)
    _validate_css(flattened)
    assert "80vw" in flattened and "50vw" in flattened
    assert "@media" not in flattened
