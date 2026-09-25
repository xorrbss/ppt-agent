from dataclasses import FrozenInstanceError
import hashlib
from types import SimpleNamespace

import pytest

from services.document_canvas import document_canvas_for_orientation
from services.document_canvas_contain import (
    CanvasRect, ContainCssTypographyError, ContainTypographyError,
    build_contain_plan, validate_contain_css_typography, validate_contain_typography,
)
from services.authored_vnext.source_native_canvas import (
    apply_source_native_canvas_placement,
    build_source_native_canvas_placement,
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


def test_source_native_final_html_is_digest_bound_to_one_fixed_geometry_transform():
    placement = build_source_native_canvas_placement(
        design_canvas=SimpleNamespace(width=1280, height=720, unit="px"),
        target_canvas=document_canvas_for_orientation("portrait"),
    )
    final_html, receipt = apply_source_native_canvas_placement(
        "<html><head></head><body><main data-qid='slide'>kept</main></body></html>",
        placement=placement,
        font_policy_id="dense-source-7px",
    )
    assert receipt["finalHtmlDigest"] == "sha256:" + hashlib.sha256(
        final_html.encode("utf-8")
    ).hexdigest()
    assert receipt["scale"] == 0.5625
    assert "kept" in final_html
    with pytest.raises(ValueError, match="viewport_dependent_geometry"):
        apply_source_native_canvas_placement(
            "<html><style>main{width:100vw}</style><body></body></html>",
            placement=placement,
            font_policy_id="dense-source-7px",
        )


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


def test_source_master_emu_geometry_uses_css_pixel_space_before_contain():
    placement = build_source_native_canvas_placement(
        design_canvas=SimpleNamespace(width=12192000, height=6858000, unit="emu"),
        target_canvas=document_canvas_for_orientation("portrait"),
    )
    assert placement.plan.source_rect.width == 1280
    assert placement.plan.source_rect.height == 720
    assert placement.plan.scale == 0.5625


def test_fixed_master_responsive_css_validates_every_branch_without_changing_source():
    from utils.source_canvas_css import flatten_layout_conditions
    from utils.renderer_isolation import _validate_css
    css = "@media(min-width:1000px){main{width:80vw;font-size:2em}}@media(max-width:999px){main{width:50vw}}"
    flattened = flatten_layout_conditions(css)
    _validate_css(flattened)
    assert "80vw" in flattened and "50vw" in flattened
    assert "@media" not in flattened


def test_target_body_typography_is_set_before_allocation_without_changing_caption():
    from services.authored_vnext.source_native_target_fit import target_typography_slots
    slots = [{"slotId": "body", "sourceFlowRole": "body", "textStyle": {"fontSize": 22}},
             {"slotId": "caption", "sourceFlowRole": "caption", "textStyle": {"fontSize": 16}}]
    target = target_typography_slots(slots, contain_scale=0.5625,
        minimum_effective_font_px=12 * 12700 / 9000,
        policy_id="source-native/min-body-12-physical-pt")
    physical = target[0]["textStyle"]["fontSize"] * 0.5625 * 9000 / 12700
    assert 12 <= physical < 12.01
    assert target[1] == slots[1]
    assert slots[0]["textStyle"]["fontSize"] == 22


def test_source_native_exempt_title_cannot_mask_missing_body_telemetry():
    from services.authored_vnext.source_native_canvas import (
        source_native_font_policy, validate_source_native_canvas_capture,
    )
    placement = build_source_native_canvas_placement(
        design_canvas=SimpleNamespace(width=1280, height=720, unit="px"),
        target_canvas=document_canvas_for_orientation("portrait"),
    )
    digest = "sha256:" + hashlib.sha256(b"html").hexdigest()
    with pytest.raises(ValueError, match="font_measurement_missing"):
        validate_source_native_canvas_capture(html="html", capture_html_digest=digest,
            receipt={"finalHtmlDigest": digest}, placement=placement,
            policy=source_native_font_policy(source_mode=None, placement=placement),
            text_expected=True, normalized_metrics={"elements": [
                {"qid": "source-flow:title", "semantic_role": "title", "effective_font_size": 24},
                {"qid": "source-flow:body", "semantic_role": "body", "effective_font_size": None},
            ]})
