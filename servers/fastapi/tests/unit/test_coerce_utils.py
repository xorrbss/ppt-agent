"""Behaviour gate for ``utils.coerce``.

Pins the invariants every migrated ``_number``/``_mapping`` copy now shares:
``bool`` is never a number, non-finite values fall back, and the default is
whatever the caller names.
"""

from __future__ import annotations

import math
from types import MappingProxyType

import pytest

from utils.coerce import as_mapping, finite_float, strip_px_number


@pytest.mark.parametrize("value", [True, False])
def test_bool_is_never_a_number(value: bool) -> None:
    assert finite_float(value) is None
    assert finite_float(value, 0.0) == 0.0
    assert math.isnan(finite_float(value, math.nan))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "nan", "inf"])
def test_non_finite_falls_back_to_default(value: object) -> None:
    assert finite_float(value) is None
    assert finite_float(value, 0.0) == 0.0


@pytest.mark.parametrize("value", [None, "", "12px", object(), [1]])
def test_non_numeric_falls_back_to_default(value: object) -> None:
    assert finite_float(value) is None
    assert finite_float(value, 7.5) == 7.5


@pytest.mark.parametrize(
    ("value", "expected"), [(3, 3.0), (2.5, 2.5), ("12.5", 12.5), (" 4 ", 4.0), (-0.0, 0.0)]
)
def test_numbers_and_numeric_strings_coerce(value: object, expected: float) -> None:
    result = finite_float(value)
    assert isinstance(result, float)
    assert result == expected


def test_default_is_explicit_not_modal() -> None:
    assert finite_float("x", default=math.inf) == math.inf
    assert finite_float("x", default=None) is None


def test_strip_px_number() -> None:
    assert strip_px_number("12.5px") == 12.5
    assert strip_px_number("12.5") == 12.5
    assert strip_px_number(3) == 3.0
    assert strip_px_number("px") is None
    assert strip_px_number(True) is None
    assert strip_px_number(None, 0.0) == 0.0


def test_as_mapping() -> None:
    source = {"a": 1}
    assert as_mapping(source) is source
    proxy = MappingProxyType(source)
    assert as_mapping(proxy) is proxy
    assert as_mapping(None) == {}
    assert as_mapping([("a", 1)]) == {}
    assert as_mapping("a") == {}
