from __future__ import annotations

from .models import ChartSeriesCandidate


NS = {
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}

_CHART_TAGS = {
    "areaChart": "area",
    "barChart": "bar",
    "doughnutChart": "donut",
    "lineChart": "line",
    "pieChart": "pie",
    "radarChart": "radar",
}


def _points(parent) -> list[str]:
    if parent is None:
        return []
    indexed: list[tuple[int, str]] = []
    for point in parent.findall("./c:pt", NS):
        value = point.find("./c:v", NS)
        if value is not None:
            indexed.append((int(point.get("idx") or 0), value.text or ""))
    return [value for _, value in sorted(indexed)]


def _cache_values(node, kind: str) -> list[str]:
    if node is None:
        return []
    cache = node.find(f".//c:{kind}Cache", NS)
    return _points(cache)


def _chart_type(chart) -> str | None:
    tag = chart.tag.rsplit("}", 1)[-1]
    chart_type = _CHART_TAGS.get(tag)
    if tag != "barChart":
        return chart_type
    direction = chart.find("./c:barDir", NS)
    grouping = chart.find("./c:grouping", NS)
    horizontal = direction is not None and direction.get("val") == "bar"
    stacked = grouping is not None and grouping.get("val") in {
        "stacked",
        "percentStacked",
    }
    if horizontal and stacked:
        return "horizontal_stacked_bar"
    if horizontal:
        return "horizontal_bar"
    if stacked:
        return "stacked_bar"
    return "bar"


def parse_cached_chart(root) -> tuple[
    str,
    list[str],
    list[ChartSeriesCandidate],
] | None:
    """Read inert chart cache values; linked workbook formulas are not opened."""

    plot_area = root.find(".//c:plotArea", NS)
    if plot_area is None:
        return None
    chart = next(
        (
            child
            for child in list(plot_area)
            if child.tag.rsplit("}", 1)[-1] in _CHART_TAGS
        ),
        None,
    )
    if chart is None:
        return None
    chart_type = _chart_type(chart)
    series_nodes = chart.findall("./c:ser", NS)
    if chart_type is None or not series_nodes:
        return None
    category_node = series_nodes[0].find("./c:cat", NS)
    categories = _cache_values(category_node, "str")
    if not categories:
        categories = _cache_values(category_node, "num")
    series: list[ChartSeriesCandidate] = []
    for index, node in enumerate(series_nodes, start=1):
        name_values = _cache_values(node.find("./c:tx", NS), "str")
        direct_name = node.find("./c:tx/c:v", NS)
        name = (
            name_values[0]
            if name_values
            else (direct_name.text or "" if direct_name is not None else "")
        )
        raw_values = _cache_values(node.find("./c:val", NS), "num")
        try:
            values = [float(value) for value in raw_values]
        except ValueError:
            return None
        if len(values) != len(categories):
            return None
        series.append(
            ChartSeriesCandidate(name=name or f"Series {index}", values=values)
        )
    return chart_type, categories, series
