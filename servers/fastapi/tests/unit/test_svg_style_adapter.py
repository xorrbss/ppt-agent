from types import SimpleNamespace

import pytest

from utils.authored_styles import resolve_authored_style
from utils.svg_style_adapter import adapt_svg_style


def _request(primary_color=None, fonts=None, wordmark=None):
    return SimpleNamespace(primary_color=primary_color, fonts=fonts, wordmark=wordmark)


def test_no_request_falls_back_to_the_preset_only_as_before():
    style = resolve_authored_style("default")
    result = adapt_svg_style(style, width=1280, height=720)
    assert result["tokens"]["primary"] == (style.primary_color or style.preview_accent)
    assert result["tokens"]["wordmark"] == ""


def test_request_brand_overrides_win_over_the_preset():
    style = resolve_authored_style("default")
    result = adapt_svg_style(
        style, width=1280, height=720,
        request=_request(primary_color="#FF00AA", fonts="Pretendard", wordmark="Acme Corp"),
    )
    assert result["tokens"]["primary"] == "#FF00AA"
    assert result["tokens"]["heading_font"] == "Pretendard"
    assert result["tokens"]["body_font"] == "Pretendard"
    assert result["tokens"]["wordmark"] == "Acme Corp"


def test_partial_request_overrides_leave_the_preset_field_alone():
    style = resolve_authored_style("default")
    result = adapt_svg_style(style, width=1280, height=720, request=_request(wordmark="Only Wordmark"))
    assert result["tokens"]["primary"] == (style.primary_color or style.preview_accent)
    assert result["tokens"]["heading_font"] == (style.heading_font or "Noto Sans KR")
    assert result["tokens"]["wordmark"] == "Only Wordmark"


def test_master_rules_and_pin_are_carried_into_the_snapshot():
    style = resolve_authored_style("default")
    rules = {"style_id": "master:acme-1", "tokens": {"accent": "#111111"}}
    pin = {"masterId": "master:acme-1", "masterRevision": 1}
    result = adapt_svg_style(
        style, width=1280, height=720,
        request=_request(), master_rules=rules, master_pin=pin,
    )
    assert result["master_template"] == rules
    assert result["master_pin"] == pin


def test_no_master_rules_means_no_master_keys_in_the_snapshot():
    style = resolve_authored_style("default")
    result = adapt_svg_style(style, width=1280, height=720)
    assert "master_template" not in result
    assert "master_pin" not in result


@pytest.mark.parametrize("width,height", [(0, 720), (1280, 0), (5000, 720)])
def test_canvas_bounds_are_still_enforced(width, height):
    style = resolve_authored_style("default")
    with pytest.raises(ValueError, match="svg_style_canvas_unsupported"):
        adapt_svg_style(style, width=width, height=height)
