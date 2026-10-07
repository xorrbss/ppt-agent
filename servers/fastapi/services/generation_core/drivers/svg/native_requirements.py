"""Narrow native export requirements from explicit, parseable page data."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from services.generation_core.drivers.svg.native_publish import page_native_objects


def _compact(value: str) -> str:
    return "".join(value.split())


def requested_native_data(content: str) -> dict:
    """Recognize literal table rows/chart pairs; never infer a chart from prose."""
    if not isinstance(content, str):
        return {}
    result = {}
    if re.search(r"(?:표|\btable\b)", content, re.I):
        rows = []
        for line in content.splitlines():
            if "|" not in line:
                continue
            line = re.sub(r"^.*?[:：]\s*", "", line).strip()
            if line.startswith("|"):
                line = line[1:]
                if line.endswith("|"):
                    line = line[:-1]
            cells = [cell.strip() for cell in line.split("|")]
            if all(re.fullmatch(r"[:\-\s]+", cell or " ") for cell in cells):
                continue
            if len(cells) >= 2:
                rows.append(cells)
        if len(rows) >= 2 and len({len(row) for row in rows}) == 1:
            result["tableRows"] = rows
    charts = []
    for marker in re.finditer(r"(?:\bchart\b|차트)\s*[:：]\s*([^\n]+)", content, re.I):
        source = re.split(r"\.(?!\d)", marker.group(1), maxsplit=1)[0]
        pairs = []
        for part in re.split(r"[,;]\s*(?!\d{3}(?:\D|$))", source):
            pair = re.fullmatch(r"\s*(.+?)\s+([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*", part)
            if not pair:
                pairs = []
                break
            pairs.append((pair.group(1).strip(), pair.group(2).replace(",", "")))
        if len(pairs) >= 2:
            charts.append({"categories": [pair[0] for pair in pairs], "values": [pair[1] for pair in pairs]})
    if charts:
        result["charts"] = charts
    return result


def apply_requested_native_quality(quality: dict, payload: dict, svg: str, content: str, *, pt_per_px: float) -> None:
    requirements = requested_native_data(content)
    if not requirements:
        return
    wire, summary = page_native_objects({**payload, "svg_content": svg}, pt_per_px=pt_per_px)
    entries = (wire or {}).get("objects", [])
    failures = []
    required_rows = requirements.get("tableRows")
    if required_rows:
        wanted = [[_compact(cell) for cell in row] for row in required_rows]
        candidates = [entry["payload"].get("rows", []) for entry in entries if entry["kind"] == "table"]
        if not any([[_compact(cell["text"]) for cell in row] for row in rows] == wanted for rows in candidates):
            failures.append({"kind": "native_requested_table_missing", "requiredRows": required_rows})
    for expected in requirements.get("charts", []):
        found = False
        for entry in entries:
            if entry["kind"] != "chart":
                continue
            chart = entry["payload"]
            if [_compact(str(value)) for value in chart.get("categories", [])] != [_compact(value) for value in expected["categories"]]:
                continue
            for series in chart.get("series", []):
                try:
                    found = [Decimal(str(value)) for value in series.get("values", [])] == [Decimal(value) for value in expected["values"]]
                except InvalidOperation:
                    found = False
                if found:
                    break
            if found:
                break
        if not found:
            failures.append({"kind": "native_requested_chart_missing", **expected})
    quality["requestedNativeObjects"] = {"requiredTables": int(bool(required_rows)),
        "requiredCharts": len(requirements.get("charts", [])), "passed": not failures,
        "projection": summary, "repairInstruction": "Preserve the exact requested data and draw each requested table/chart in its data-native-table/data-native-chart group; fix rejected declaration geometry rather than replacing its meaning."}
    if failures:
        quality["passed"] = False
        quality.setdefault("failures", []).extend(failures)
