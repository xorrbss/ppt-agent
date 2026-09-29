"""Gate for routing the authored_vnext luminance/contrast copies through
``utils.color_contrast``.

The copies used the 0.04045 sRGB linearisation threshold while the shared
module uses WCAG's 0.03928.  No 8-bit channel value lies between the two
(10/255 = 0.0392 is below both, 11/255 = 0.0431 above both), so the results
are identical for every ``#RRGGBB`` colour; the first test proves that for
all 256 channel values.  The remaining tests pin that each caller keeps its
own error type for a malformed colour.
"""

from __future__ import annotations

import pytest

from utils.color_contrast import contrast_ratio, relative_luminance


def _copy_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _shared_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def test_threshold_fork_is_invisible_for_eight_bit_channels() -> None:
    for value in range(256):
        channel = value / 255.0
        assert _copy_linear(channel) == _shared_linear(channel)


def test_shared_ratio_matches_copy_formula_on_samples() -> None:
    samples = ("#000000", "#FFFFFF", "#111827", "#1463FF", "#64748B", "#0A0A0A", "#0B0B0B")
    for left in samples:
        for right in samples:
            l_left = sum(
                weight * _copy_linear(int(left[index:index + 2], 16) / 255)
                for weight, index in ((0.2126, 1), (0.7152, 3), (0.0722, 5))
            )
            l_right = sum(
                weight * _copy_linear(int(right[index:index + 2], 16) / 255)
                for weight, index in ((0.2126, 1), (0.7152, 3), (0.0722, 5))
            )
            lighter, darker = max(l_left, l_right), min(l_left, l_right)
            assert contrast_ratio(left, right) == pytest.approx((lighter + 0.05) / (darker + 0.05))
            assert relative_luminance(left) == pytest.approx(l_left)


def test_export_service_keeps_its_error_type() -> None:
    from services.authored_vnext.integration import export_service

    assert export_service._contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert export_service._relative_luminance("FFFFFF") == pytest.approx(1.0)
    with pytest.raises(export_service.ExportServiceError):
        export_service._relative_luminance("not-a-colour")


def test_native_scene_keeps_its_error_type() -> None:
    from services.authored_vnext.ooxml import native_scene

    assert native_scene._contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert native_scene._relative_luminance("FFFFFF") == pytest.approx(1.0)
    with pytest.raises(native_scene.NativeSceneError):
        native_scene._relative_luminance("not-a-colour")


def test_retained_reference_keeps_value_error() -> None:
    from services.authored_vnext.generation import retained_reference

    assert retained_reference.contrast_ratio is contrast_ratio
    assert not hasattr(retained_reference, "_contrast_ratio")
    assert not hasattr(retained_reference, "_relative_luminance")
    with pytest.raises(ValueError):
        relative_luminance("FFFFFF")


def test_scene_compiler_routes_through_shared_module() -> None:
    from services.authored_vnext.generation import scene_compiler

    assert scene_compiler.contrast_ratio is contrast_ratio
    assert not hasattr(scene_compiler, "_contrast_ratio")
    assert not hasattr(scene_compiler, "_rgb")
    # The old unvalidated _rgb silently mis-parsed a bare "FFFFFF"; it now fails loud.
    with pytest.raises(ValueError):
        relative_luminance("FFFFFF")
