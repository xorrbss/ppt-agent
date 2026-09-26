"""Bounded native chart payload projection for editable scenes."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping


_CHART_PALETTE = ("#2563EB", "#0EA5E9", "#7C3AED", "#059669")


def _bounded_native_chart_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize the exact line/pie v1 semantic subset or reject to raster."""

    if not set(payload).issubset(
        {"chartType", "categories", "series", "chartStyle", "legend", "title"}
    ):
        return None
    chart_type = str(payload.get("chartType") or "").lower()
    categories = payload.get("categories")
    series = payload.get("series")
    if (
        chart_type not in {"line", "pie"}
        or not isinstance(categories, list)
        or not categories
        or len(categories) > 100
        or any(not isinstance(item, str) or not item or len(item) > 500 for item in categories)
        or not isinstance(series, list)
        or not series
        or len(series) > 30
        or chart_type == "pie" and len(series) != 1
        or payload.get("title") not in {None, ""}
    ):
        return None
    normalized_series: list[dict[str, Any]] = []
    colors: list[str] = []
    for index, raw_series in enumerate(series):
        if not isinstance(raw_series, Mapping) or not set(raw_series).issubset(
            {"name", "values", "color"}
        ):
            return None
        name = raw_series.get("name")
        values = raw_series.get("values")
        color = raw_series.get("color", _CHART_PALETTE[index % len(_CHART_PALETTE)])
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 500
            or not isinstance(values, list)
            or len(values) != len(categories)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in values
            )
            or not isinstance(color, str)
            or re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None
        ):
            return None
        normalized_series.append(
            {"name": name, "values": [float(value) for value in values]}
        )
        colors.append(color.upper())
    if len({item["name"] for item in normalized_series}) != len(normalized_series):
        return None
    chart_style = payload.get("chartStyle", 2)
    if isinstance(chart_style, bool) or not isinstance(chart_style, int) or not 1 <= chart_style <= 48:
        return None
    legend_value = payload.get("legend")
    if legend_value is None:
        legend = chart_type == "pie" or len(normalized_series) > 1
    elif isinstance(legend_value, bool):
        legend = legend_value
    elif (
        isinstance(legend_value, Mapping)
        and set(legend_value) == {"visible"}
        and isinstance(legend_value.get("visible"), bool)
    ):
        legend = bool(legend_value["visible"])
    else:
        return None
    return {
        "categories": list(categories),
        "series": normalized_series,
        "style": {
            "chartStyle": chart_style,
            "seriesColors": colors,
            "legend": legend,
        },
    }
