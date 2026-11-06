"""V4 evidence for a drawn chart whose optional native projection was dropped."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import math
import re
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS, _drawn_series_name, _model_chart
from services.generation_core.drivers.svg.native_declarations import bound_declarations
from services.generation_core.drivers.svg.native_geometry import (
    NativeRejection, collect_primitives, find_group, normalize_text,
)

_VALUE = re.compile(r"([-+−]?)([₩$€£¥]?)\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([%％A-Za-z가-힣/²³]*)")


def _visible_group(root: ET.Element, group: ET.Element) -> bool:
    parents = {child: parent for parent in root.iter() for child in parent}
    ancestors, node = [], parents.get(group)
    while node is not None:
        if node.tag.rsplit("}", 1)[-1] not in {"svg", "g"}:
            return False
        ancestors.append(node)
        node = parents.get(node)
    # Evidence must not borrow hidden, clipped or CSS-controlled text. Browser
    # completeness/visibility checks still run independently on the whole page.
    for node in [*ancestors, *group.iter()]:
        if (any(node.get(key) not in (None, "none") for key in ("clip-path", "mask", "filter"))
                or any(key in node.attrib for key in ("style", "class"))
                or node.get("display") not in (None, "inline")
                or node.get("visibility") not in (None, "visible")):
            return False
        for key in ("opacity", "fill-opacity"):
            try:
                alpha = float(node.get(key, "1"))
            except ValueError:
                return False
            if not math.isfinite(alpha) or alpha <= 0:
                return False
    return True


def chart_drawing_evidence(payload: dict, svg: str, *, revision: str) -> dict[str, dict]:
    """Read only current bound chart groups; no drawing is inferred from JSON."""
    record, current = bound_declarations(payload, svg)
    if not current or record is None:
        return {}
    root = ET.fromstring(svg)
    evidence = {}
    for declaration in record.get("objects") or []:
        if not isinstance(declaration, dict) or declaration.get("kind") != "chart":
            continue
        group_id = declaration.get("id")
        try:
            chart = _model_chart(declaration.get("raw"))
            found = find_group(root, group_id)
            if found is None or not _visible_group(root, found[0]):
                continue
            prims = collect_primitives(*found, CHART_ELEMENTS)
            texts = [normalize_text(" ".join(text.lines)) for text in prims.texts
                     if text.fill is not None and text.size > 0 and all(run.fill is not None and run.size > 0
                         for runs in text.run_lines for run in runs)]
            available = Counter(texts)
            missing = []
            for label in chart["categories"]:
                if available[normalize_text(label)] > 0:
                    available[normalize_text(label)] -= 1
                else:
                    missing.append(label)
            title = chart.get("title")
            if title and normalize_text(title) not in texts:
                missing.append(title)
            for series in chart["series"]:
                try:
                    _drawn_series_name(series["name"], set(texts), revision=revision,
                        declared_title=(title if title and len(chart["series"]) == 1
                                        and texts.count(normalize_text(title)) == 1 else None))
                except NativeRejection:
                    missing.append(series["name"])
            labels = {normalize_text(label) for label in chart["categories"]}
            labels.update(normalize_text(series["name"]) for series in chart["series"])
            if title:
                labels.add(normalize_text(title))
            evidence[group_id] = {"chart": chart, "missingLabels": missing,
                "valueTexts": [text for text in texts if text not in labels]}
        except (NativeRejection, TypeError, ValueError, AttributeError):
            continue
    return evidence


def chart_value_differences(evidence: dict, expected: dict, *, strict_units: bool = True) -> dict:
    """Require every value occurrence, with compatible units, inside this group."""
    from services.generation_core.drivers.svg.native_requirements import _unit_key

    units = {_unit_key(label["unit"]) for label in expected.get("requiredUnitLabels", [])}
    # With heterogeneous units, visible text alone cannot establish which
    # series owns a value. Keep the strict native projection requirement.
    ambiguous_units = strict_units and len(units) > 1
    drawn: Counter = Counter()
    incompatible = []
    for text in evidence["valueTexts"]:
        match = _VALUE.fullmatch(text)
        if not match:
            continue
        unit = _unit_key(match[2] + match[5])
        if strict_units and unit and unit not in units:
            incompatible.append(text)
            continue
        value = ("-" if match[1] in ("-", "−") else "") + match[3].replace(",", "") + (match[4] or "")
        drawn[Decimal(value)] += 1
    missing = []
    for series in expected.get("series", [expected]):
        for value in series.get("values", []):
            try:
                number = Decimal(str(value).replace(",", ""))
            except InvalidOperation:
                missing.append(str(value))
                continue
            if ambiguous_units or not number.is_finite() or drawn[number] == 0:
                missing.append(str(value))
            else:
                drawn[number] -= 1
    return {"missingValues": missing, "unexpectedValues": [*incompatible,
            *(str(value) for value, count in drawn.items() for _ in range(count))]}


def chart_projection_diagnostics(summary: dict | None, evidence: dict[str, dict]) -> dict | None:
    """Attach actionable witnesses without changing legacy receipt summaries."""
    if summary is None:
        return None
    dropped = []
    for item in summary.get("dropped", []):
        proof = evidence.get(item.get("id"))
        if item.get("kind") == "chart" and proof is not None:
            item = {**item, "missingLabels": proof["missingLabels"],
                    **chart_value_differences(proof, proof["chart"], strict_units=False)}
        dropped.append(item)
    return {**summary, "dropped": dropped}
