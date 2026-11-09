"""Content-only requirements shared by SVG authoring and native publication QA."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata

from collections.abc import Mapping

from services.generation_core.drivers.svg.native_publish import page_native_objects

#: Requirement revisions. A deck reads its revision from its frozen style snapshot
#: (:data:`SNAPSHOT_REVISION_KEY`); a snapshot without the key, and every caller that passes
#: no revision, keeps v2 exactly, so a deck admitted before v3 resumes with the derivation it
#: was authored and checked with.
#:
#: v3 (2026-11-05, docs/svg-mode-improvement-plan-20261105.md item S2) differs from v2 in the
#: literal grammar only: ``30%p`` / ``30%포인트`` is a unit, a trailing ``(note)`` after a value
#: joins its label, a comma inside brackets (or in a ``;``-separated list) stays in its label,
#: a label ending in a Korean magnitude amount (``1조 2,000억원``) refuses the line instead of
#: misreading ``1조`` as label text, and a non-data diagram marker (``간트 차트:``) is not a
#: chart. A v3 deck also has prose series written as ``chart:`` lines at outline time
#: (``prose_chart_literal.py``); that derivation lives in the pinned outline, not here.
REQUIREMENTS_VERSION = "svg-native-requirements/v2"
REQUIREMENTS_VERSION_V3 = "svg-native-requirements/v3"
REQUIREMENTS_VERSION_V4 = "svg-native-requirements/v4"
REQUIREMENTS_VERSION_V5 = "svg-native-requirements/v5"
REQUIREMENTS_REVISIONS = (REQUIREMENTS_VERSION, REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4,
                         REQUIREMENTS_VERSION_V5)
#: The revision a fresh svg admission pins.
ADMITTED_REQUIREMENTS_REVISION = REQUIREMENTS_VERSION_V4
SNAPSHOT_REVISION_KEY = "native_requirements_revision"
_NUMBER = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"(?:%|％|[A-Za-z가-힣][A-Za-z가-힣/²³]*)"
_UNIT_V3 = r"(?:[%％](?:p|포인트)?|[A-Za-z가-힣][A-Za-z가-힣/²³]*)"
_PAIR = re.compile(rf"\s*(.+?)\s+([$€£₩¥]?)({_NUMBER})\s*({_UNIT})?\s*")
_PAIR_V3 = re.compile(rf"\s*(.+?)\s+([$€£₩¥]?)({_NUMBER})\s*({_UNIT_V3})?\s*(\([^()]*\))?\s*")
#: A label that ends in a Korean magnitude amount: the value is a compound such as ``1조 2,000억원``.
_COMPOUND_AMOUNT_LABEL = re.compile(rf"(?:^|\s){_NUMBER}\s*(?:조|억|만|천)$")
_CHART = re.compile(r"(?:\bchart\b|차트)\s*[:：]\s*([^\n]+)", re.I)
#: Diagram names that precede a ``chart:``/``차트:`` marker without introducing numeric data.
#: (A "cash flow chart" is a data chart, so plain "flow"/"흐름" is not listed.)
_NON_DATA_CHART = re.compile(
    r"(?:간트|조직|플로우|순서|프로세스|마인드\s*맵|\b(?:gantt|org(?:ani[sz]ation(?:al)?)?|process|pert|mind\s*map))"
    r"\s*$", re.I)

_SERIES_NAME = r"(?:[^,;:：()]|\([^()]*\))+"
_NAMED_SERIES = re.compile(rf"\s*({_SERIES_NAME}?)\s*[:：]\s*(.+)")
_TABLE_PREFIX = re.compile(r"^[^|]*?(?:\btable\b|(?<![가-힣])표)\s*[:：]\s*", re.I)
_CELL_BREAK = re.compile(r"<br[ \t]*/?>", re.I)


def requirements_revision(snapshot: object) -> str:
    """The requirement revision a deck's frozen style snapshot pins (v2 when it pins none)."""
    return _revision(snapshot.get(SNAPSHOT_REVISION_KEY) if isinstance(snapshot, Mapping) else None)


def _revision(value: str | None) -> str:
    if value is None:
        return REQUIREMENTS_VERSION
    if value not in REQUIREMENTS_REVISIONS:
        raise ValueError("svg_native_requirements_revision_invalid")
    return value


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


_PAIR_SEPARATOR = r"[,;]\s*(?!\d{3}(?:\D|$))"


def _split_outside_brackets(source: str, separator: str) -> list[str]:
    """Split on ``separator`` except inside (), [] or full-width brackets."""
    parts, depth, start = [], 0, 0
    pattern = re.compile(separator)
    position = 0
    while position < len(source):
        char = source[position]
        if char in "([（［":
            depth += 1
        elif char in ")]）］" and depth:
            depth -= 1
        elif depth == 0:
            match = pattern.match(source, position)
            if match and match.end() > position:
                parts.append(source[start:position])
                start = position = match.end()
                continue
        position += 1
    parts.append(source[start:])
    return parts


def _pair_parts(source: str, revision: str) -> list[str]:
    if revision == REQUIREMENTS_VERSION:
        return re.split(_PAIR_SEPARATOR, source)
    # v3: a ';'-separated list keeps a comma in its labels when every part reads as one pair
    # and no part also reads as several comma-separated pairs (that would be ambiguous).
    if ";" in source:
        parts = _split_outside_brackets(source, r";\s*")
        if len(parts) >= 2 and all(_PAIR_V3.fullmatch(part) for part in parts) and not any(
                len(pieces) > 1 and all(_PAIR_V3.fullmatch(piece) for piece in pieces)
                for pieces in (_split_outside_brackets(part, _PAIR_SEPARATOR) for part in parts)):
            return parts
    return _split_outside_brackets(source, _PAIR_SEPARATOR)


def _chart_pairs(source: str, revision: str = REQUIREMENTS_VERSION, *, value_spans=None,
                 source_offset: int = 0) -> dict | None:
    pairs, spans, cursor = [], [], 0
    for part in _pair_parts(source, revision):
        part_start = source.find(part, cursor)
        cursor = part_start + len(part)
        if revision == REQUIREMENTS_VERSION:
            match = _PAIR.fullmatch(part)
            if not match:
                return None
            label, prefix, value, suffix = match.groups()
        else:
            match = _PAIR_V3.fullmatch(part)
            if not match:
                return None
            label, prefix, value, suffix, note = match.groups()
            label = label.strip()
            if _COMPOUND_AMOUNT_LABEL.search(label):
                return None
            label += note or ""
        pairs.append((label.strip(), value.replace(",", ""), prefix + (suffix or "")))
        spans.append((source_offset + part_start + match.start(3),
                      source_offset + part_start + (match.end(4) if suffix else match.end(3))))
    if len(pairs) < 2:
        return None
    result = {"categories": [pair[0] for pair in pairs], "values": [pair[1] for pair in pairs]}
    if any(pair[2] for pair in pairs):
        result["units"] = [pair[2] for pair in pairs]
    if value_spans is not None:
        value_spans.extend(spans)
    return result


def _unit_key(unit: str) -> str:
    return _compact(unicodedata.normalize("NFKC", unit))


_V5_WON_MAGNITUDE = re.compile(r"(천|만|백만|천만|억|조)원?")


def _v5_unit_key(unit: str) -> str:
    """V5 unit identity: a Korean magnitude with or without 원 is one unit.

    "38억" under a "(억원)" title states the same amount as "38억원"; only the
    v5 proof uses this, so earlier revisions keep their exact unit identity.
    """
    key = _unit_key(unit)
    match = _V5_WON_MAGNITUDE.fullmatch(key)
    return f"{match[1]}원" if match else key


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


def _charts(content: str, revision: str | None = None, *, value_spans=None) -> list[dict]:
    revision = _revision(revision)
    unit_pattern = _UNIT if revision == REQUIREMENTS_VERSION else _UNIT_V3
    charts = []
    for marker in _CHART.finditer(content):
        if revision != REQUIREMENTS_VERSION:
            line_start = content.rfind("\n", 0, marker.start()) + 1
            if _NON_DATA_CHART.search(content[line_start:marker.start()]):
                continue
        segments = re.split(r"\.(?!\d)", marker.group(1), maxsplit=1)
        source = segments[0].strip()
        source_offset = marker.start(1) + len(segments[0]) - len(segments[0].lstrip())
        unit = re.fullmatch(rf"\s*(?:단위|unit)\s*[:：]?\s*({unit_pattern})\s*\.?\s*", segments[1], re.I) if len(segments) > 1 else None
        series_parts = re.split(rf";\s*(?={_SERIES_NAME}[:：])", source)
        named_parts = [_NAMED_SERIES.fullmatch(part) for part in series_parts]
        named, spans, cursor = [], [], 0
        if all(named_parts):
            for part in named_parts:
                name, values = part.groups()
                part_start = source.find(part.string, cursor)
                cursor = part_start + len(part.string)
                parsed = _chart_pairs(values, revision, value_spans=spans,
                                      source_offset=source_offset + part_start + part.start(2))
                if parsed is None:
                    break
                named.append({"name": name.strip(), **parsed})
            if len(named) != len(series_parts) or any(item["categories"] != named[0]["categories"] for item in named):
                continue
            chart = {"categories": named[0]["categories"], "series": [
                {key: value for key, value in item.items() if key != "categories"} for item in named]}
        else:
            chart = _chart_pairs(source, revision, value_spans=spans, source_offset=source_offset)
        if chart is None:
            continue
        if unit:
            chart["unit"] = unit[1]
        _chart_unit_contract(chart)
        charts.append({"id": f"chart-{len(charts) + 1}", **chart})
        if value_spans is not None:
            value_spans[charts[-1]["id"]] = tuple(spans)
    return charts


def requested_native_data(content: str, revision: str | None = None) -> dict:
    """Recognize independent literal blocks; never derive values from prose.

    ``revision`` is the deck's pinned requirement revision (:func:`requirements_revision`);
    omitted, it is v2."""
    revision = _revision(revision)
    if not isinstance(content, str):
        return {}
    tables, charts = _tables(content), _charts(content, revision)
    if not tables and not charts:
        return {}
    result = {"version": revision, "tables": tables, "charts": charts}
    if len(tables) == 1:
        result["tableRows"] = tables[0]["rows"]
    return result


def same_requirements_as_v2(content: str, snapshot: object) -> bool:
    """Whether the snapshot's revision reads ``content`` exactly as v2 does (version aside)."""
    revision = requirements_revision(snapshot)
    if revision == REQUIREMENTS_VERSION:
        return True

    def data(value: dict) -> dict:
        return {key: item for key, item in value.items() if key != "version"}
    return data(requested_native_data(content, revision)) == data(requested_native_data(content))


def format_requested_native_data(content: str, revision: str | None = None) -> str:
    requirements = requested_native_data(content, revision)
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


def required_numeric_claims_with_native_charts(content: str, payload: dict, svg: str, *,
                                             native_source: str, pt_per_px: float,
                                             revision: str | None = None, quality_policy=None) -> list[str]:
    """V5 data-value occurrences may use bound geometry instead of plaintext.

    Only successful literal value spans are removed before the existing numeric
    deduplication. Categories, notes and equal-valued prose keep their claims.
    This is a private pre-render decision; the same-capture final paint gate is
    still mandatory and no scratch evidence is written to page quality.
    """
    from services.svg_presentation_checkpoint import _required_numeric_claims

    legacy = _required_numeric_claims(content, quality_policy=quality_policy)
    if revision != REQUIREMENTS_VERSION_V5 or not isinstance(native_source, str):
        return legacy
    try:
        requested = _charts(native_source, revision)
        if not requested:
            return legacy
        scratch = {"passed": True, "failures": []}
        apply_requested_native_quality(scratch, payload, svg, native_source,
                                       pt_per_px=pt_per_px, revision=revision)
        if any(item.get("kind") != "native_requested_chart_not_editable"
               for item in scratch["failures"]):
            return legacy
        from services.generation_core.drivers.svg.native_chart_evidence import chart_drawing_evidence

        from services.generation_core.drivers.svg.native_declarations import bound_declarations
        from services.generation_core.drivers.svg.server_chart import trusted_server_chart_ids

        evidence = chart_drawing_evidence(payload, svg, revision=revision)
        record, _ = bound_declarations(payload, svg)
        trusted = {item["id"]: item["raw"] for item in (record or {}).get("objects") or []
                   if isinstance(item, dict) and item.get("id") in trusted_server_chart_ids(record, svg)}
        candidates = []
        for expected in requested:
            # A trusted server chart draws its declaration by construction.
            matches = [identifier for identifier, raw in trusted.items() if _chart_matches(raw, expected)]
            for identifier, proof in evidence.items():
                if identifier in trusted:
                    continue
                if proof.get("missingLabels") or proof.get("contentGeometry", {}).get("passed") is not True:
                    continue
                chart = proof["chart"]
                if (chart.get("axisContract") == "native-chart-fixed-value-axis/v1"
                        and proof["contentGeometry"].get("calibrated")):
                    chart = {**chart, "title": chart.get("title", "") + " " + chart.get("valueAxis", {}).get("title", "")}
                if _chart_matches(chart, expected):
                    matches.append(identifier)
            candidates.append(matches)
        # An occurrence consumes one distinct declaration. Equivalent repeated
        # requests may consume equivalent distinct charts; no numeric set grants authority.
        owners = {}

        def assign(index, seen):
            for identifier in candidates[index]:
                if identifier in seen:
                    continue
                seen.add(identifier)
                if identifier not in owners or assign(owners[identifier], seen):
                    owners[identifier] = index
                    return True
            return False

        if not all(assign(index, set()) for index in range(len(requested))):
            return legacy

        def signature(chart):
            if chart.get("unsupportedReason"):
                return None
            series = chart.get("series", [chart])
            return (tuple(_compact(value) for value in chart["categories"]),
                    tuple((_compact(item["name"]) if "name" in item else None,
                           tuple(Decimal(value) for value in item["values"])) for item in series),
                    tuple((_compact(item["seriesName"]) if "seriesName" in item else None,
                           _unit_key(item["unit"])) for item in chart.get("requiredUnitLabels", [])))

        available = {}
        for expected in requested:
            key = signature(expected)
            if key is not None:
                available[key] = available.get(key, 0) + 1
        measured = content
        if quality_policy is not None and quality_policy.get("version") == 2:
            from services.generation_core.drivers.svg.visual_intent import source_content_evidence

            measured = "\n".join(block["text"] for block in source_content_evidence(content)["blocks"])
        spans = {}
        literal_charts = _charts(measured, revision, value_spans=spans)
        excluded = []
        for chart in literal_charts:
            key = signature(chart)
            if key is not None and available.get(key, 0):
                available[key] -= 1
                excluded.extend(spans[chart["id"]])
        if not excluded:
            return legacy
        masked = list(measured)
        for start, end in excluded:
            masked[start:end] = " " * (end - start)
        return _required_numeric_claims("".join(masked))
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, AttributeError, InvalidOperation,
            RecursionError):
        # Parsing or association uncertainty keeps every original text claim.
        return legacy


def apply_requested_native_quality(quality: dict, payload: dict, svg: str, content: str, *, pt_per_px: float,
                                   revision: str | None = None, render_capture=None, post_render: bool = False,
                                   declared_chart_only: bool = False, paint_deferred: bool = False) -> None:
    requirements = requested_native_data(content, revision)
    declared_charts = []
    from services.generation_core.drivers.svg.native_declarations import PAYLOAD_KEY, bound_declarations
    from services.generation_core.drivers.svg.server_chart import trusted_server_chart_ids
    record, _ = bound_declarations(payload, svg)
    # Trusted server charts are accepted by construction: no drawing or paint proof,
    # and a source literal is matched against the declaration itself.
    trusted = trusted_server_chart_ids(record, svg)
    proof_payload = payload if not trusted else {**payload, PAYLOAD_KEY: {
        **record, "objects": [item for item in record.get("objects") or []
                              if not (isinstance(item, dict) and item.get("id") in trusted)]}}
    if revision == REQUIREMENTS_VERSION_V5:
        declared_charts = [item for item in [*(record or {}).get("objects", []), *(record or {}).get("dropped", [])]
                           if isinstance(item, dict) and item.get("kind") == "chart" and item.get("id") not in trusted]
        if declared_charts and not requirements:
            requirements = {"version": revision, "tables": [], "charts": []}
    if not requirements:
        return
    if revision == REQUIREMENTS_VERSION_V5 and declared_chart_only:
        # Preservation paths retain their existing table authority. The
        # additive final chart gate still checks every source chart literal.
        requirements = {**requirements, "tables": []}
    projection_kwargs = {"revision": revision} if revision in (REQUIREMENTS_VERSION_V4, REQUIREMENTS_VERSION_V5) else {}
    wire, summary = page_native_objects({**payload, "svg_content": svg}, pt_per_px=pt_per_px, **projection_kwargs)
    evidence = {}
    if revision in (REQUIREMENTS_VERSION_V4, REQUIREMENTS_VERSION_V5):
        from services.generation_core.drivers.svg.native_chart_evidence import (
            chart_drawing_evidence, chart_projection_diagnostics, chart_value_differences,
        )
        evidence = chart_drawing_evidence(proof_payload, svg, revision=revision,
            **({"measurement": quality, "render_capture": render_capture, "post_render": post_render,
                **({"paint_deferred": True} if paint_deferred else {})}
               if revision == REQUIREMENTS_VERSION_V5 else {}))
        summary = chart_projection_diagnostics(summary, evidence)
        dropped_ids = {item.get("id") for item in (summary or {}).get("dropped", [])
                       if item.get("kind") == "chart"}
        if revision == REQUIREMENTS_VERSION_V4:
            evidence = {group_id: proof for group_id, proof in evidence.items() if group_id in dropped_ids}
    remaining = list((wire or {}).get("objects", []))
    failures = []

    def v5_chart_matches(proof, expected):
        if proof is None:
            return False
        chart = proof["chart"]
        if _chart_matches(chart, expected):
            return True
        if (chart.get("axisContract") == "native-chart-fixed-value-axis/v1"
                and proof.get("contentGeometry", {}).get("calibrated")):
            axis_title = chart.get("valueAxis", {}).get("title", "")
            return _chart_matches({**chart, "title": chart.get("title", "") + " " + axis_title}, expected)
        return False

    def v5_proven(proof, expected=None):
        if (proof is None or proof["missingLabels"]
                or not proof.get("contentGeometry", {}).get("passed")):
            return False
        own = dict(proof["chart"])
        title = own.get("title", "")
        title_unit = _series_name_unit(title) if title.count("(") <= 1 else None
        if (own.get("axisContract") == "native-chart-fixed-value-axis/v1"
                and proof["contentGeometry"].get("calibrated")):
            axis_title = own.get("valueAxis", {}).get("title", "")
            axis_unit = _series_name_unit(axis_title) or _series_name_unit(f"({axis_title})")
            if title_unit and axis_unit and _v5_unit_key(title_unit) != _v5_unit_key(axis_unit):
                return False
            title_unit = title_unit or axis_unit
        series_units = [_series_name_unit(series["name"]) for series in own["series"]]
        if title_unit and any(unit and _v5_unit_key(title_unit) != _v5_unit_key(unit) for unit in series_units):
            return False
        # A proven share label's "%" states the datum's share of its whole
        # (pie/donut/percent-stacked only), not the unit of the datum itself.
        bindings = [binding for binding in proof["contentGeometry"].get("valueLabelBindings", [])
                    if binding.get("valueMatch") != "share"]
        if expected is not None:
            declared_units = {_v5_unit_key(unit) for unit in (title_unit, *series_units) if unit}
            expected_units = {_v5_unit_key(label["unit"]) for label in expected.get("requiredUnitLabels", [])}
            if not declared_units <= expected_units:
                return False
            # A proof for a different chart (another series count) cannot match; check
            # that first so a unit binding never indexes past the expected series.
            if not v5_chart_matches(proof, expected):
                return False
            expected_series_list = expected.get("series", [expected])
            for binding in bindings:
                unit = _v5_unit_key(binding["unit"])
                if not unit:
                    continue
                series_index = binding["seriesIndex"]
                if not 0 <= series_index < len(expected_series_list):
                    return False
                expected_series = expected_series_list[series_index]
                own_units = {_v5_unit_key(label["unit"]) for label in expected.get("requiredUnitLabels", [])
                             if "seriesName" not in label or label["seriesName"] == expected_series.get("name")}
                if unit not in own_units:
                    return False
            return v5_chart_matches(proof, expected)
        # Without a source literal this proves only the drawing/declaration
        # relationship. Do not infer source fidelity from self-declared JSON.
        return all(not binding["unit"] or _v5_unit_key(binding["unit"]) in {
            _v5_unit_key(unit) for unit in (title_unit, series_units[binding["seriesIndex"]]) if unit}
            for binding in bindings)

    paint_evidence = {group_id: proof.get("paintEvidence", {}) for group_id, proof in evidence.items()}
    if revision == REQUIREMENTS_VERSION_V5:
        for declaration in declared_charts:
            proof = evidence.get(declaration.get("id"))
            candidates = [expected for expected in requirements["charts"]
                          if proof is not None and v5_chart_matches(proof, expected)]
            proven = any(v5_proven(proof, expected) for expected in candidates) if candidates else v5_proven(proof)
            if not proven:
                failures.append({"kind": "native_requested_chart_missing",
                                 "declarationId": declaration.get("id"),
                                 "reason": "drawing_content_unproven"})
            elif (proof.get("paintEvidence", {}).get("status") != "pending"
                    and proof.get("paintEvidence", {}).get("passed") is not True):
                failures.append({"kind": "measurement_incomplete", "scope": "native_chart_paint",
                                 "declarationId": declaration.get("id"),
                                 "reason": proof.get("paintEvidence", {}).get("reason", "native_chart_capture_unproven")})
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
        match = next((entry for entry in remaining if entry["kind"] == "chart"
                      and (v5_proven(evidence.get(entry["id"]), expected)
                           if revision == REQUIREMENTS_VERSION_V5 and entry["id"] not in trusted
                           else _chart_matches(entry["payload"], expected))), None)
        if match is None:
            # A native annotation is optional only after the bound declaration
            # matches the requirement and its own group visibly draws every
            # category, series/unit label and value occurrence. Independent
            # visibility and content failures remain content failures.
            drawn = next((group_id for group_id, proof in evidence.items()
                if (v5_proven(proof, expected) if revision == REQUIREMENTS_VERSION_V5 else
                    not proof["missingLabels"] and _chart_matches(proof["chart"], expected)
                    and not any(chart_value_differences(proof, expected).values()))), None)
            if drawn is not None:
                evidence.pop(drawn)
                failures.append({"kind": "native_requested_chart_not_editable", **expected,
                                 "declarationId": drawn})
            else:
                failures.append({"kind": "native_requested_chart_missing", **expected})
        else:
            remaining.remove(match)
            evidence.pop(match["id"], None)
    quality["requestedNativeObjects"] = {"version": requirements["version"],
        "requiredTables": len(requirements["tables"]), "requiredCharts": len(requirements["charts"]),
        "passed": not failures, "projection": summary,
        "repairInstruction": "Preserve each independent table/chart with its exact data and all series in its own data-native-table/data-native-chart group. Retain explicit source cell paragraph breaks; additional visual wrapping may occur within a paragraph, but cannot replace its original boundary with a space. Retain every required unit label with its series association in the declared chart title. Unsupported unit mappings cannot be repaired by changing values or inventing units. Fix rejected declaration geometry without changing its meaning."}
    if revision == REQUIREMENTS_VERSION_V5:
        quality["requestedNativeObjects"]["drawingEvidence"] = {
            group_id: proof["contentGeometry"] for group_id, proof in evidence.items()
            if "contentGeometry" in proof}
        quality["requestedNativeObjects"]["paintEvidence"] = paint_evidence
        quality["requestedNativeObjects"]["contentRetentionProven"] = (
            bool(paint_evidence) and all(item.get("passed") is True for item in paint_evidence.values())
            and all(item["kind"] == "native_requested_chart_not_editable" for item in failures))
    if failures:
        quality["passed"] = False
        quality.setdefault("failures", []).extend(failures)


def native_chart_repair_hint(quality: dict, *, revision: str) -> str | None:
    """V4 gives repair attempts the projection cause and missing drawing data."""
    if revision not in (REQUIREMENTS_VERSION_V4, REQUIREMENTS_VERSION_V5):
        return None
    requested = quality.get("requestedNativeObjects") or {}
    dropped = [item for item in (requested.get("projection") or {}).get("dropped", [])
               if item.get("kind") == "chart"]
    if not dropped:
        return None
    # Leave room for the renderer's writing and overlap witnesses without
    # slicing serialized JSON in the middle of a label/value.
    compact = [{key: str(item[key])[:120] for key in ("id", "reason") if key in item}
               | {key: [str(value)[:60] for value in item.get(key, [])[:4]]
                  for key in ("missingLabels", "missingValues", "unexpectedValues")}
               for item in dropped[:3]]
    kinds = [str(item.get("kind", "unknown"))[:80] for item in quality.get("failures", [])
             if isinstance(item, dict)]
    hint = {"kind": "native_chart_projection_repair", "dropped": compact,
        "failureKinds": list(dict.fromkeys(kinds))[:8],
        "minimumBodyPx": quality.get("body_font_floor_px"),
        "instruction": "Keep exact categories, every series, value and unit. Draw missing labels/values inside their group; fix the projection reason and other page failures without deleting content or shrinking fonts. A unique single-series title naming its series may replace a legend."}
    while len(json.dumps(hint, ensure_ascii=False, separators=(",", ":"))) > 2300 and len(compact) > 1:
        compact.pop()
    hint["omittedCharts"] = len(dropped) - len(compact)
    return json.dumps(hint, ensure_ascii=False, separators=(",", ":"))
