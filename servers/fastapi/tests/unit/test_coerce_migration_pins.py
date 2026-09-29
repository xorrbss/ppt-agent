"""Pins the behaviour changes introduced by routing ``_number`` copies through
``utils.coerce.finite_float``.

Each assertion below documents a case where the former private copy
disagreed with the shared invariant (``bool`` is not a number, non-finite is
not a number).  The old result is noted next to each assertion.
"""

from __future__ import annotations

import math

from utils.coerce import as_mapping, finite_float


def test_slidespec_importer_rejects_bool() -> None:
    # Old copy lacked the bool guard: float(True) == 1.0.
    from services import slidespec_authored_importer as module

    assert module.finite_float is finite_float
    assert not hasattr(module, "_number")
    assert finite_float(True, 0.0) == 0.0


def test_editable_scene_rejects_bool_and_non_finite() -> None:
    # Old copy returned float(value) unguarded: NaN and True passed through.
    from services.authored_vnext.semantic import editable_scene as module

    assert module.finite_float is finite_float
    assert not hasattr(module, "_number")
    assert finite_float(math.nan, 18) == 18
    assert finite_float(True, 400) == 400


def test_delegating_wrappers_share_the_invariant() -> None:
    from services import slidespec_coupled_repair, slidespec_geometry_candidates, slidespec_repair
    from utils import authored_dom_fit_magnitude, authored_dom_footer_zone, authored_dom_text_escape
    from utils import authored_empty_media

    assert slidespec_repair._number(True) == 0.0
    assert math.isnan(slidespec_geometry_candidates._number(True))
    assert math.isnan(slidespec_coupled_repair._number("x"))
    assert slidespec_coupled_repair._number("12.5") == 12.5
    # Old footer-zone/text-escape copies only rejected NaN, so inf leaked.
    assert authored_dom_footer_zone._number(math.inf) == 0.0
    assert authored_dom_text_escape._number(math.inf) == 0.0
    # Old fit-magnitude/empty-media copies lacked the bool guard.
    assert authored_dom_fit_magnitude._number(True) == 0.0
    assert authored_empty_media._number(True) == 0.0


def test_none_default_modules_import_the_helper() -> None:
    import importlib

    # The pattern modules form a pre-existing import cycle that only resolves
    # when entered through the package's public module, as production does.
    importlib.import_module("services.slidespec_repair_patterns")
    for name in (
        "utils.slidespec_constraints",
        "services.source_content.browser_readback",
        "services.authored_planned_image_visibility",
        "services.authored_local_fit_evidence_reconstruction",
        "services.authored_offcanvas_repair",
        "services.slidespec_repair_pattern_recipe",
        "services.authored_decoration_containment",
    ):
        module = importlib.import_module(name)
        assert module.finite_float is finite_float, name
        assert not hasattr(module, "_number"), name
    # Old evidence-reconstruction copy returned float("nan") for "nan".
    assert finite_float("nan") is None
    # Old offcanvas/recipe/containment copies rejected numeric strings and
    # (offcanvas) let inf through; the shared rule accepts "12" and drops inf.
    assert finite_float("12") == 12.0
    assert finite_float(math.inf) is None


def test_raising_wrappers_keep_their_error_types() -> None:
    import pytest

    from services.authored_vnext.ooxml import native_scene
    from services.chat import slide_ui_geometry

    with pytest.raises(TypeError):
        slide_ui_geometry._number("12", name="x")
    with pytest.raises(ValueError):
        slide_ui_geometry._number(math.inf, name="x")
    assert slide_ui_geometry._number(3, name="x") == 3.0
    with pytest.raises(native_scene.NativeSceneError):
        native_scene._number(True, "code")
    with pytest.raises(native_scene.NativeSceneError):
        native_scene._number(math.nan, "code")
    assert native_scene._number(2, "code") == 2.0


def test_plain_mapping_copies_use_as_mapping() -> None:
    import importlib

    for name in (
        "services.authored_vnext.deck_ir.strategy",
        "services.slidespec_coupled_repair",
        "services.slidespec_geometry_candidates",
        "services.slidespec_repair_context.coupled_projection",
        "services.slidespec_repair_context.coupled_overlap_clearance",
        "services.trusted_executor.container_isolation_evidence",
    ):
        module = importlib.import_module(name)
        assert module.as_mapping is as_mapping, name
        assert not hasattr(module, "_mapping"), name
