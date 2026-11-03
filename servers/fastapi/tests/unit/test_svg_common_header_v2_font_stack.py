"""Header design fields name fonts the way models write them: as a CSS stack."""

import pytest

from services.svg_common_header_v2 import _field

CANVAS = {"width": 1280, "height": 720}


def _raw(font_family):
    return {"node_id": "title", "role": "title", "value_ref": "title",
            "box": {"x": 40, "y": 20, "width": 900, "height": 80},
            "font_family": font_family, "font_weight": 700, "font_size_px": 32}


def test_a_css_stack_resolves_to_the_bundled_face_it_names():
    field = _field(_raw("Noto Sans KR, Malgun Gothic, Apple SD Gothic Neo, sans-serif"), CANVAS)
    assert field["font"]["family"] == "Noto Sans KR"
    assert field["font"]["weight"] == 700


def test_a_single_untrusted_family_still_fails_closed():
    with pytest.raises(ValueError):
        _field(_raw("Comic Sans MS"), CANVAS)
