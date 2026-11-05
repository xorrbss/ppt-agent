"""Unit tests for unit-carrying Office number formats inferred from drawn chart labels."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.generation_core.drivers.svg.export_chart_style import _source_data_label_format


def _texts(*values):
    return [SimpleNamespace(text=value) for value in values]


@pytest.mark.parametrize("labels, expected", [
    (["30", "45"], "0"),
    (["1,200", "3,400"], "#,##0"),
    (["1.5", "2.0"], "0.0"),
    (["30%", "45%"], '0"%"'),
    (["12.5%", "7.0%"], '0.0"%"'),
    (["1,200억원", "850억원"], '#,##0"억원"'),
    (["1200억원", "850억원"], '0"억원"'),
    (["1,200억원", "3,400억원"], '#,##0"억원"'),
    (["120만대", "80만대"], '0"만대"'),
    (["12명", "7명"], '0"명"'),
    (["$1.5", "$22.0"], '"$"0.0'),
    (["$1,500.0", "$2,200.0"], '"$"#,##0.0'),
    (["₩300", "₩450"], '"₩"0'),
    (["30%", "45"], None),
    (["30%", "45건"], None),
    (["30%", "4.5%"], None),
    (["abc", "def"], None),
    (["3 0%", "45%"], None),
    ([], None),
])
def test_label_format_inference(labels, expected):
    assert _source_data_label_format(_texts(*labels)) == expected


def test_unsafe_unit_is_not_quoted():
    assert _source_data_label_format(_texts('3"', '4"')) is None
