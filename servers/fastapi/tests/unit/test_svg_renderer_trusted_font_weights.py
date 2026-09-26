"""Trusted-font weight normalization: never fail closed on a legitimate CSS
font-weight; embed the nearest declared weight and record an advisory.

Regression context: setting a request-level ``fonts`` override of
"Pretendard" (a real, common webfont name, and this renderer's only trusted
embeddable font) previously crashed svg generation whenever the model chose
any weight other than exactly 400/700 (e.g. 500 or 600 for emphasis), because
``_pretendard_weights`` hard-failed on anything outside a literal
{normal,400,bold,700} set. That path was unreachable before request.fonts
flowed through to svg; this session's StyleContext fix made it reachable.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from services.svg_trusted_fonts import _nearest_declared_weight
from services.svg_trusted_fonts import trusted_font_weights_used as _trusted_font_weights_used


def _svg(font_weight: str, font_family: str = "Pretendard") -> ET.Element:
    markup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
        f'<text font-family="{font_family}" font-weight="{font_weight}">hi</text></svg>'
    )
    return ET.fromstring(markup)


@pytest.mark.parametrize("weight,expected", [(300, 400), (500, 700), (600, 700), (100, 400), (900, 700)])
def test_nearest_declared_weight_rounds_up_to_the_next_heavier_available(weight, expected):
    assert _nearest_declared_weight(frozenset({400, 700}), weight) == expected


@pytest.mark.parametrize("weight,expected", [(400, 400), (700, 700)])
def test_nearest_declared_weight_is_exact_when_already_declared(weight, expected):
    assert _nearest_declared_weight(frozenset({400, 700}), weight) == expected


@pytest.mark.parametrize("raw,normalized", [("400", 400), ("700", 700), ("normal", 400), ("bold", 700)])
def test_declared_weights_need_no_advisory(raw, normalized):
    weights, advisories = _trusted_font_weights_used(_svg(raw))
    assert weights == {normalized}
    assert advisories == []


@pytest.mark.parametrize("raw,normalized", [("500", 700), ("600", 700), ("300", 400)])
def test_an_undeclared_weight_is_normalized_with_an_advisory_instead_of_failing(raw, normalized):
    weights, advisories = _trusted_font_weights_used(_svg(raw))
    assert weights == {normalized}
    assert advisories == [{"family": "Pretendard", "requested_weight": int(raw), "normalized_weight": normalized}]


def test_an_untrusted_font_family_is_never_checked_or_embedded():
    weights, advisories = _trusted_font_weights_used(_svg("500", font_family="Poppins"))
    assert weights == set()
    assert advisories == []


def test_a_non_numeric_non_keyword_weight_still_fails_closed():
    with pytest.raises(ValueError, match="svg_trusted_font_weight_invalid"):
        _trusted_font_weights_used(_svg("super-bold"))
