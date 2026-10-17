"""Content-only requirements shared by SVG authoring and native publication QA."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata

from services.generation_core.drivers.svg.native_publish import page_native_objects

REQUIREMENTS_VERSION = "svg-native-requirements/v2"
_NUMBER = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"(?:%|％|[A-Za-z가-힣][A-Za-z가-힣/²³]*)"
_PAIR = re.compile(rf"\s*(.+?)\s+([$€£₩¥]?)({_NUMBER})\s*({_UNIT})?\s*")
_CHART = re.compile(r"(?:\bchart\b|차트)\s*[:：]\s*([^\n]+)", re.I)
_SERIES_NAME = r"(?:[^,;:：()]|\([^()]*\))+"
_NAMED_SERIES = re.compile(rf"\s*({_SERIES_NAME}?)\s*[:：]\s*(.+)")
_TABLE_PREFIX = re.compile(r"^[^|]*?(?:\btable\b|(?<![가-힣])표)\s*[:：]\s*", re.I)
_CELL_BREAK = re.compile(r"<br[ \t]*/?>", re.I)


def _compact(value: str) -> str:
    return "".join(value.split())


def _table_cells(line: str) -> list[str] | None:
    line = _TABLE_PREFIX.sub("", line.strip(), count=1)
    if "|" not in line:
        return None
    enclosed = line.startswith("|") and line.endswith("|") and not line.endswith("\\|")
    if line.startswith("|"):
        line = line[1:]
        if line.endswith("|") and not line.endswith("\\|"):
            line = line[:-1]
    cells = [_CELL_BREAK.sub("\n", cell.strip().replace(r"\|", "|"))
             for cell in re.split(r"(?<!\\)\|", line)]
    return cells if len(cells) >= 2 or enclosed else None


def _tables(content: str) -> list[dict]:
    tables, rows = [], []
    single_column_header = False

    def flush():
        nonlocal single_column_header
        if (len(rows) >= 2 and len({len(row) for row in rows}) == 1
                and (len(rows[0]) > 1 or single_column_header)):
            tables.append({"id": f"table-{len(tables) + 1}", "rows": list(rows), "headerRows": 1})
        rows.clear()
        single_column_header = False

    for line in content.splitlines():
        cells = _table_cells(line)
        if cells is None:
            flush()
        elif all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            if len(rows) > 1:
                header = rows.pop()
                flush()
                rows.append(header)
            if rows and len(cells) != len(rows[0]):
                flush()
            elif len(rows) == 1 and len(cells) == 1:
                single_column_header = True
        else:
            if rows and _TABLE_PREFIX.match(line.strip()):
                flush()
            rows.append(cells)
    flush()
    return tables


def _chart_pairs(source: str) -> dict | None:
    pairs = []
    for part in re.split(r"[,;]\s*(?!\d{3}(?:\D|$))", source):
        match = _PAIR.fullmatch(part)
        if not match:
            return None
        label, prefix, value, suffix = match.groups()
        pairs.append((label.strip(), value.replace(",", ""), prefix + (suffix or "")))
    if len(pairs) < 2:
        return None
    result = {"categories": [pair[0] for pair in pairs], "values": [pair[1] for pair in pairs]}
    if any(pair[2] for pair in pairs):
        result["units"] = [pair[2] for pair in pairs]
    return result


def _unit_key(unit: str) -> str:
    return _compact(unicodedata.normalize("NFKC", unit))


def _series_name_unit(name: str) -> str | None:
    match = re.search(r"\((?:(단위|unit)\s*[:：]\s*)?([^()]+)\)\s*$", name, re.I)
    if not match:
        return None
    unit = match[2].strip()
    if match[1] or re.fullmatch(
        r"%|％|‰|[$€£₩¥]|USD|EUR|GBP|KRW|JPY|CNY|(?:천|만|백만|천만|억|조)?원|명|건|개|회|kg|g|km|cm|mm|m|초|분|시간",
        _unit_key(unit),
    ):
        return unit
    return None


def _chart_unit_contract(chart: dict) -> None:
    """Retain unit attribution through title text supported by native V1."""
    series_items = chart.get("series", [chart])
    series_units = []
    for series in series_items:
        units = {_unit_key(unit): unit for unit in series.get("units", []) if unit}
        if len(units) > 1:
            chart["unsupportedReason"] = "mixed_point_units"
            return
        for unit in [chart.get("unit"), _series_name_unit(series.get("name", ""))]:
            if unit:
                units[_unit_key(unit)] = unit
        if len(units) > 1:
            chart["unsupportedReason"] = "conflicting_series_units"
            return
        if units:
            series_units.append((series.get("name"), next(iter(units.values()))))
    if not series_units:
        return
    distinct = {_unit_key(unit) for _, unit in series_units}
    if len(distinct) == 1 and len(series_units) == len(series_items):
        unit = series_units[0][1]
        chart["requiredUnitLabels"] = [{"text": unit, "unit": unit}]
    else:
        chart["requiredUnitLabels"] = [
            {"seriesName": name, "unit": unit,
             "text": name if _series_name_unit(name) else f"{name} ({unit})"}
            for name, unit in series_units]


def _charts(content: str) -> list[dict]:
    charts = []
    for marker in _CHART.finditer(content):
        segments = re.split(r"\.(?!\d)", marker.group(1), maxsplit=1)
        source = segments[0].strip()
        unit = re.fullmatch(rf"\s*(?:단위|unit)\s*[:：]?\s*({_UNIT})\s*\.?\s*", segments[1], re.I) if len(segments) > 1 else None
        series_parts = re.split(rf";\s*(?={_SERIES_NAME}[:：])", source)
        named_parts = [_NAMED_SERIES.fullmatch(part) for part in series_parts]
        named = []
        if all(named_parts):
            for part in named_parts:
                name, values = part.groups()
                parsed = _chart_pairs(values)
                if parsed is None:
                    break
                named.append({"name": name.strip(), **parsed})
            if len(named) != len(series_parts) or any(item["categories"] != named[0]["categories"] for item in named):
                continue
            chart = {"categories": named[0]["categories"], "series": [
                {key: value for key, value in item.items() if key != "categories"} for item in named]}
        else:
            chart = _chart_pairs(source)
        if chart is None:
            continue
        if unit:
            chart["unit"] = unit[1]
        _chart_unit_contract(chart)
        charts.append({"id": f"chart-{len(charts) + 1}", **chart})
    return charts


def requested_native_data(content: str) -> dict:
    """Recognize independent literal blocks; never derive values from prose."""
    if not isinstance(content, str):
        return {}
    tables, charts = _tables(content), _charts(content)
    if not tables and not charts:
        return {}
    result = {"version": REQUIREMENTS_VERSION, "tables": tables, "charts": charts}
    if len(tables) == 1:
        result["tableRows"] = tables[0]["rows"]
    return result


def format_requested_native_data(content: str) -> str:
    requirements = requested_native_data(content)
    if not requirements:
        return ""
    requirements = {key: value for key, value in requirements.items() if key != "tableRows"}
    return ("\nEXPLICIT NATIVE DATA (content only; choose composition freely):\n"
            + json.dumps(requirements, ensure_ascii=False, separators=(",", ":"))
            + "\nDraw each independent table/chart in its own native declaration group. Preserve table cell text, "
              "empty cells, row/column order, chart category order and every explicit series exactly. "
              "A cell's explicit <br> or <br/> is supplied as a newline in rows: retain that original "
              "paragraph boundary in both the drawn tspan lines and native JSON. Additional visual "
              "wrapping is allowed within each paragraph; never replace an original break with a space. "
              "Convert chart values to JSON numbers without rescaling (30% remains 30). "
              "Draw every requiredUnitLabels.text exactly in the declared chart title; each paired label "
              "binds its exact source series name to its unit. Keep each series name unchanged. "
              "For unsupportedReason preserve all literal labels and values visibly, and do not claim "
              "native chart fidelity or invent a unit mapping. "
              "The unit labels, units, unsupported reason and requirement ids here are grounding metadata, "
              "not native declaration fields. "
              "Use the native declaration schema above. Do not merge tables, omit series or invent data.\n")


def _same_values(actual: list, expected: list) -> bool:
    try:
        return [Decimal(str(value)) for value in actual] == [Decimal(str(value)) for value in expected]
    except (InvalidOperation, ValueError, TypeError):
        return False


def _table_cell_matches(actual: str, expected: str) -> bool:
    """Keep explicit source breaks while allowing extra visual wrapping."""
    if _compact(actual) != _compact(expected):
        return False

    def boundaries(value):
        offset = 0
        for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")[:-1]:
            offset += len(_compact(line))
            yield offset

    drawn = iter(boundaries(actual))
    return all(any(candidate == required for candidate in drawn) for required in boundaries(expected))


def _table_rows_match(actual: list, expected: list) -> bool:
    return len(actual) == len(expected) and all(
        len(row) == len(wanted) and all(
            _table_cell_matches(cell["text"], text) for cell, text in zip(row, wanted, strict=True))
        for row, wanted in zip(actual, expected, strict=True))


def _unit_in_title(unit: str, title: str) -> bool:
    unit = _unit_key(unit)
    title = unicodedata.normalize("NFKC", title)
    pattern = r"\s*".join(re.escape(char) for char in unit)
    if unit[0].isalpha():
        pattern = r"(?<![^\W\d_])" + pattern
    if unit[-1].isalpha():
        pattern += r"(?![^\W\d_])"
    return re.search(pattern, title) is not None


def _chart_matches(chart: dict, expected: dict) -> bool:
    if expected.get("unsupportedReason"):
        return False
    if [_compact(str(value)) for value in chart.get("categories", [])] != [_compact(value) for value in expected["categories"]]:
        return False
    actual = chart.get("series", [])
    wanted = expected.get("series", [{"values": expected.get("values", [])}])
    if len(actual) != len(wanted):
        return False
    for series, requirement in zip(actual, wanted, strict=True):
        if not _same_values(series.get("values", []), requirement["values"]):
            return False
        if "name" in requirement and _compact(series.get("name", "")) != _compact(requirement["name"]):
            return False
    # Paired labels disambiguate heterogeneous units without changing native V1.
    title = chart.get("title", "")
    for label in expected.get("requiredUnitLabels", []):
        if "seriesName" in label:
            if not _unit_in_title(label["text"], title):
                return False
        elif not _unit_in_title(label["unit"], title):
            return False
    return True


def apply_requested_native_quality(quality: dict, payload: dict, svg: str, content: str, *, pt_per_px: float) -> None:
    requirements = requested_native_data(content)
    if not requirements:
        return
    wire, summary = page_native_objects({**payload, "svg_content": svg}, pt_per_px=pt_per_px)
    remaining = list((wire or {}).get("objects", []))
    failures = []
    for expected in requirements["tables"]:
        match = next((entry for entry in remaining if entry["kind"] == "table"
                      and _table_rows_match(entry["payload"].get("rows", []), expected["rows"])), None)
        if match is None:
            failures.append({"kind": "native_requested_table_missing", "requirementId": expected["id"], "requiredRows": expected["rows"]})
        else:
            remaining.remove(match)
    for expected in requirements["charts"]:
        if expected.get("unsupportedReason"):
            failures.append({"kind": "native_requested_chart_unsupported", **expected})
            continue
        match = next((entry for entry in remaining if entry["kind"] == "chart" and _chart_matches(entry["payload"], expected)), None)
        if match is None:
            failures.append({"kind": "native_requested_chart_missing", **expected})
        else:
            remaining.remove(match)
    quality["requestedNativeObjects"] = {"version": REQUIREMENTS_VERSION,
        "requiredTables": len(requirements["tables"]), "requiredCharts": len(requirements["charts"]),
        "passed": not failures, "projection": summary,
        "repairInstruction": "Preserve each independent table/chart with its exact data and all series in its own data-native-table/data-native-chart group. Retain explicit source cell paragraph breaks; additional visual wrapping may occur within a paragraph, but cannot replace its original boundary with a space. Retain every required unit label with its series association in the declared chart title. Unsupported unit mappings cannot be repaired by changing values or inventing units. Fix rejected declaration geometry without changing its meaning."}
    if failures:
        quality["passed"] = False
        quality.setdefault("failures", []).extend(failures)
