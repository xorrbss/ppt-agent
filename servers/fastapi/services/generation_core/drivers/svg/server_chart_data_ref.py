"""Server charts by reference: the data comes from the slide's chart literal.

A server-drawn chart (``server_chart.py``) still had the author re-type every
category and value into its JSON, and a string-proximity heuristic
(``apply_server_chart_grounding``) then had to prove each value was stated next
to its category. When the slide content carries a ``chart:`` literal the author
is already shown it, with its id, in ``EXPLICIT NATIVE DATA``
(``native_requirements.requested_native_data``). A deck admitted with
:data:`DATA_REF_REVISION` lets the author reference that literal instead::

    <g id="native-chart-SLUG" data-native-chart='{"chartType":"column","title":"매출 (억원)"}'
       data-chart-ref="chart-1" data-chart-render="svg-server-chart/vN" data-chart-box="X Y W H"/>

and the server fills categories, series and values from the literal, exactly.
The resolution runs inside ``extract_native_declarations`` before the renderer
draws, from the requirement literals of the content the author was given (the
pinned outline slide content), so every replay of a stored raw response
(author, partial recovery, page remake) resolves and draws the identical tree.
The declaration record stores the resolved raw (the trusted replay redraws
from it) and ``"dataRef"``, which exempts the chart from the value grounding
heuristic: its values are the literal's by construction. An unknown or
unreferenceable id leaves the placeholder empty with a reason, like any other
refusal. Decks admitted without the key keep their exact behaviour: the
attribute is never read for them.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.server_chart import ServerChartUnsupported, server_chart_policy

DATA_REF_REVISION = "svg-server-chart-data-ref/v1"
SNAPSHOT_KEY = "server_chart_data_ref"
REF_ATTRIBUTE = "data-chart-ref"
RECORD_KEY = "dataRef"
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_PRESENTATION_KEYS = ("grouping", "valueLabels")

DATA_REF_RULE = (
    "When CHART REFERENCES lists a chart id (the \"id\" of a chart in EXPLICIT NATIVE DATA, such as "
    "\"chart-1\"), draw that chart by reference: add data-chart-ref=\"chart-1\" to its server placeholder and "
    "write in data-native-chart only {\"chartType\":\"column\",\"title\":\"매출 (억원)\"} with optional "
    "\"grouping\", \"valueLabels\", \"colors\" (one #RRGGBB per series; for a single series or a pie/donut, one "
    "per category or one for all) and, for a single series, \"seriesName\". \"title\" is required. The server "
    "fills the categories, series names and values from that chart exactly and adds its unit to the title when "
    "missing; never retype them. A chart that CHART REFERENCES does not list keeps the full JSON.\n"
)


def data_ref_policy(style_snapshot: dict | None) -> bool:
    """Whether a deck was admitted with server charts by reference (frozen at admission)."""
    return (server_chart_policy(style_snapshot)
            and style_snapshot.get(SNAPSHOT_KEY) == DATA_REF_REVISION)


def data_ref_rule(style_snapshot: dict | None) -> str:
    """The author rule addition for decks admitted with the policy (``""`` otherwise)."""
    return DATA_REF_RULE if data_ref_policy(style_snapshot) else ""


def chart_literals(style_snapshot: dict | None, content: object) -> dict[str, dict] | None:
    """The chart literals a placeholder of this page may reference, by id; ``None`` when the
    deck was not admitted with the policy (references are then never read).

    ``content`` must be the content the author was given: its ids are the ones the
    author prompt shows (``format_requested_native_data`` reads the same content
    with the same pinned requirement revision).
    """
    if not data_ref_policy(style_snapshot):
        return None
    from services.generation_core.drivers.svg.native_requirements import (
        requested_native_data, requirements_revision,
    )

    data = requested_native_data(content, requirements_revision(style_snapshot)) if isinstance(content, str) else {}
    return {chart["id"]: chart for chart in data.get("charts", [])}


def _refusal(chart: dict) -> str | None:
    if chart.get("unsupportedReason"):
        return "server_chart_data_ref_unsupported"
    if any("seriesName" in label for label in chart.get("requiredUnitLabels", [])):
        # Units bound per series cannot be carried by one title unit.
        return "server_chart_data_ref_units_mixed"
    return None


def referenceable_ids(literals: Mapping[str, dict] | None) -> list[str]:
    return [identifier for identifier, chart in (literals or {}).items() if _refusal(chart) is None]


def data_ref_prompt(style_snapshot: dict | None, content: object) -> str:
    """The page's referenceable chart ids for the author prompt (``""`` when none or not admitted)."""
    identifiers = referenceable_ids(chart_literals(style_snapshot, content))
    if not identifiers:
        return ""
    return ("\nCHART REFERENCES: " + ", ".join(identifiers)
            + ". Draw each of these charts by reference (data-chart-ref) as the rules describe.\n")


def _json_number(value: str) -> int | float:
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ServerChartUnsupported("server_chart_data_ref_value_invalid") from error
    if not number.is_finite():
        raise ServerChartUnsupported("server_chart_data_ref_value_invalid")
    if number == number.to_integral_value():
        return int(number)
    converted = float(number)
    if Decimal(str(converted)) != number:
        # The JSON number would not state the literal exactly.
        raise ServerChartUnsupported("server_chart_data_ref_value_invalid")
    return converted


def _colors(raw: dict, series_count: int, category_count: int) -> list[list[str]] | None:
    colors = raw.get("colors")
    if not (isinstance(colors, list) and colors
            and all(isinstance(color, str) and _COLOR.fullmatch(color) for color in colors)):
        return None
    if series_count > 1:
        return [[color] * category_count for color in colors] if len(colors) == series_count else None
    if len(colors) == category_count:
        return [list(colors)]
    return [[colors[0]] * category_count] if len(colors) == 1 else None


def resolve_reference(node: ET.Element, raw: dict, literals: Mapping[str, dict]) -> tuple[dict, str]:
    """``(resolved raw, id)`` for a placeholder that references a chart literal.

    Removes the reference attribute. Raises :class:`ServerChartUnsupported` when
    the id names no referenceable literal of the page or the placeholder lacks
    a title. Only the presentation fields of ``raw`` are kept; any data the
    author typed is ignored, the literal's data is used.
    """
    from services.generation_core.drivers.svg.native_requirements import _unit_in_title

    identifier = node.attrib.pop(REF_ATTRIBUTE, "")
    chart = literals.get(identifier)
    if chart is None:
        raise ServerChartUnsupported("server_chart_data_ref_unknown")
    refusal = _refusal(chart)
    if refusal is not None:
        raise ServerChartUnsupported(refusal)
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ServerChartUnsupported("server_chart_data_ref_title_missing")
    title = title.strip()
    labels = chart.get("requiredUnitLabels", [])
    if labels and not _unit_in_title(labels[0]["unit"], title):
        # Value labels carry the title's unit (server_chart._label_unit).
        title = f"{title} ({labels[0]['unit'].strip()})"
    literal_series = chart.get("series") or [{"values": chart["values"]}]
    series = []
    for item in literal_series:
        name = item.get("name")
        if name is None:
            declared = raw.get("seriesName")
            name = declared.strip() if isinstance(declared, str) and declared.strip() else title
        series.append({"name": name, "values": [_json_number(value) for value in item["values"]]})
    colors = _colors(raw, len(series), len(chart["categories"]))
    if colors is not None:
        for item, item_colors in zip(series, colors, strict=True):
            item["pointColors"] = item_colors
    resolved = {"chartType": raw.get("chartType"), "title": title, "categories": list(chart["categories"]),
                "series": series}
    for key in _PRESENTATION_KEYS:
        if key in raw:
            resolved[key] = deepcopy(raw[key])
    return resolved, identifier


def reference_view(raw: dict) -> dict:
    """The placeholder JSON a correction attempt is shown for a referenced chart: its
    presentation fields only, so the model keeps the reference instead of retyping data."""
    series = raw.get("series") or []
    view = {"chartType": raw.get("chartType"), "title": raw.get("title")}
    for key in _PRESENTATION_KEYS:
        if raw.get(key) is not None:
            view[key] = deepcopy(raw[key])
    if len(series) == 1 and series[0].get("name") != raw.get("title"):
        view["seriesName"] = series[0].get("name")
    colors = [item.get("pointColors") for item in series]
    if all(isinstance(item, list) and item for item in colors) and colors:
        view["colors"] = list(colors[0]) if len(series) == 1 else [item[0] for item in colors]
    return view


__all__ = ["DATA_REF_REVISION", "DATA_REF_RULE", "RECORD_KEY", "REF_ATTRIBUTE", "SNAPSHOT_KEY", "chart_literals",
           "data_ref_policy", "data_ref_prompt", "data_ref_rule", "reference_view", "referenceable_ids",
           "resolve_reference"]
