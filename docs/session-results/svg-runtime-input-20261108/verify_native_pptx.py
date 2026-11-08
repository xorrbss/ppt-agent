"""Read-only native chart audit of the frozen, independently requested nine cases.

Run only after the final PPTX exists. This does not render, publish, or establish
visual fidelity. The output must be new; even failed audits retain their results.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from zipfile import ZipFile


FIXTURE_SHA256 = "a317f42dfac7bedd4725911be34f04dba6033b866cea53c7ed78c1b70bd186b5"
CASE_ORDER = ("column", "coincident-line", "stacked-column", "donut", "fixed-column",
              "separator-pie", "marker-line", "horizontal-bar", "signed-zero-multiseries-area")
NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


class AuditFailure(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise AuditFailure(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            "input_changed_during_read")
    return data, {"sha256": digest(data), "size": after.st_size, "mtimeNs": after.st_mtime_ns}


def number(value):
    require(not isinstance(value, bool) and value is not None, "numeric_value_invalid")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise AuditFailure("numeric_value_invalid") from error
    require(result.is_finite(), "numeric_value_nonfinite")
    return result


def one(node, path, reason):
    found = node.findall(path, NS)
    require(len(found) == 1, reason)
    return found[0]


def cache_values(series, role, expected, numeric=False):
    role_node = one(series, f"c:{role}", "chart_series_role_ambiguous")
    cache = role_node.findall(".//c:strCache", NS) + role_node.findall(".//c:numCache", NS)
    require(len(cache) == 1, "chart_cache_missing_or_ambiguous")
    cache = cache[0]
    count = one(cache, "c:ptCount", "chart_cache_count_missing")
    require(int(count.get("val")) == len(expected), "chart_cache_count_mismatch")
    points = cache.findall("c:pt", NS)
    indices = [int(point.get("idx")) for point in points]
    require(len(points) == len(expected) and sorted(indices) == list(range(len(expected))),
            "chart_cache_indices_mismatch")
    values = [one(point, "c:v", "chart_cache_value_missing").text or ""
              for point in sorted(points, key=lambda item: int(item.get("idx")))]
    require(([number(value) for value in values] == [number(value) for value in expected])
            if numeric else values == expected, f"chart_{role}_cache_mismatch")


def xml_bool(node, default=False):
    if node is None:
        return default
    value = node.get("val", "1")
    require(value in {"0", "1", "false", "true"}, "chart_boolean_invalid")
    return value in {"1", "true"}


def check_labels(plot, series, shown, point_count):
    require(type(shown) is bool and type(point_count) is int and point_count > 0,
            "value_label_expected_contract_invalid")

    def flags(node):
        result = {}
        for name in ("showVal", "delete"):
            found = node.findall(f"c:{name}", NS)
            require(len(found) <= 1, "value_label_field_ambiguous")
            result[name] = xml_bool(found[0]) if found else None
        return result

    def scope(parent):
        containers = parent.findall("c:dLbls", NS)
        require(len(containers) <= 1, "value_label_container_ambiguous")
        if not containers:
            return {"showVal": None, "delete": None}, {}
        container, points = containers[0], {}
        labels = container.findall("c:dLbl", NS)
        require(len(labels) <= point_count, "value_label_point_count_excess")
        for label in labels:
            index_node = one(label, "c:idx", "value_label_point_index_ambiguous")
            raw_index = (index_node.get("val") or "").strip()
            require(re.fullmatch(r"\+?[0-9]+", raw_index) is not None, "value_label_point_index_invalid")
            index = int(raw_index)
            require(0 <= index < point_count and index not in points, "value_label_point_index_duplicate_or_out_of_range")
            points[index] = flags(label)
        return flags(container), points

    chart_defaults, chart_points = scope(plot)
    for entry in series:
        series_defaults, series_points = scope(entry)
        for index in range(point_count):
            chart_point = chart_points.get(index, {})
            series_point = series_points.get(index, {})
            effective = {"showVal": False, "delete": False}
            for name in effective:
                # Mixed explicit chart-point/series-default conflicts have no
                # verified producer contract; preserve a conservative failure.
                require(not (chart_point.get(name) is not None and series_defaults[name] is not None
                             and chart_point[name] != series_defaults[name]),
                        "value_label_cross_scope_conflict")
                for layer in (chart_defaults, series_defaults, chart_point, series_point):
                    if layer.get(name) is not None:
                        effective[name] = layer[name]
            require((effective["showVal"] and not effective["delete"]) == shown,
                    "value_label_visibility_mismatch")


def verify_slide(slide, case, package, iter_shapes, workbook_bound, secure_xml):
    shapes = list(iter_shapes(slide.shapes))
    charts = [shape.chart for shape in shapes if shape.has_chart]
    require(len(charts) == 1, "slide_native_chart_count_mismatch")
    require(not any(shape.has_table for shape in shapes), "unexpected_native_table")
    chart = charts[0]
    expected = case["requestedSource"]["payload"]
    part = str(chart.part.partname).lstrip("/")
    raw = package.read(part)
    root = secure_xml(raw, part_name=part)
    plot_area = one(root, ".//c:plotArea", "plot_area_ambiguous")
    plots = [node for node in plot_area if node.tag.rsplit("}", 1)[-1].endswith("Chart")]
    require(len(plots) == 1, "native_chart_plot_ambiguous")
    plot = plots[0]
    family = expected["chartType"]
    tags = {"column": "barChart", "bar": "barChart", "line": "lineChart",
            "area": "areaChart", "pie": "pieChart", "donut": "doughnutChart"}
    require(plot.tag == f"{{{NS['c']}}}{tags[family]}", "chart_family_mismatch")
    if family in {"column", "bar"}:
        require(one(plot, "c:barDir", "bar_direction_missing").get("val") ==
                ("bar" if family == "bar" else "col"), "bar_direction_mismatch")
    if "grouping" in expected:
        require(one(plot, "c:grouping", "grouping_missing").get("val") == expected["grouping"],
                "grouping_mismatch")
    series = plot.findall("c:ser", NS)
    orders = [int(one(entry, "c:order", "series_order_missing").get("val")) for entry in series]
    indices = [int(one(entry, "c:idx", "series_index_missing").get("val")) for entry in series]
    require(len(series) == len(expected["series"]) and len(set(indices)) == len(indices)
            and all(index >= 0 for index in indices) and sorted(orders) == list(range(len(series))),
            "series_identity_or_count_mismatch")
    series.sort(key=lambda entry: int(entry.find("c:order", NS).get("val")))
    for actual, wanted in zip(series, expected["series"], strict=True):
        cache_values(actual, "tx", [wanted["name"]])
        cache_values(actual, "cat", expected["categories"])
        cache_values(actual, "val", wanted["values"], numeric=True)
    workbook_bound(chart, expected)
    workbook = chart.part.chart_workbook.xlsx_part
    workbook_part = str(workbook.partname).lstrip("/")
    workbook_bytes = package.read(workbook_part)
    require(workbook_bytes == workbook.blob, "workbook_relationship_bytes_mismatch")

    declarations = case["nativeDeclarations"]
    require(not declarations["dropped"], "fixture_declaration_dropped")
    declared = [item["raw"] for item in declarations["objects"] if item["kind"] == "chart"]
    require(len(declared) == 1, "fixture_chart_appearance_ambiguous")
    appearance = declared[0]
    if "valueLabels" in appearance:
        check_labels(plot, series, appearance["valueLabels"]["shown"], len(expected["categories"]))
    if family == "donut":
        require(number(expected["holeSize"]) == Decimal("0.7"), "fixture_hole_unexpected")
        require(one(plot, "c:holeSize", "donut_hole_missing").get("val") == "70", "donut_hole_mismatch")
    if case["id"] == "marker-line":
        for entry in series:
            marker = one(entry, "c:marker/c:symbol", "series_marker_missing")
            require(marker.get("val") not in {None, "none"}, "series_marker_hidden")
    if family in {"column", "bar", "line", "area"}:
        axes = plot_area.findall("c:catAx", NS) + plot_area.findall("c:valAx", NS)
        require(len(axes) == 2, "chart_axes_ambiguous")
        ids = [one(axis, "c:axId", "axis_id_missing").get("val") for axis in axes]
        require(len(set(ids)) == 2 and all(ids), "axis_id_ambiguous")
        require({node.get("val") for node in plot.findall("c:axId", NS)} == set(ids),
                "plot_axis_binding_mismatch")
        for axis, own in zip(axes, ids, strict=True):
            require(one(axis, "c:crossAx", "axis_cross_missing").get("val") == next(i for i in ids if i != own),
                    "axis_cross_binding_mismatch")
        if family == "bar" and appearance.get("categoryOrder") == "topToBottom":
            require(one(plot_area, "c:catAx/c:scaling/c:orientation", "category_orientation_missing").get("val") == "maxMin",
                    "category_orientation_mismatch")
    title_binding = None
    if "valueAxis" in expected:
        axis = one(plot_area, "c:valAx", "fixed_value_axis_ambiguous")
        for key, path in (("minimum", "c:scaling/c:min"), ("maximum", "c:scaling/c:max"), ("majorUnit", "c:majorUnit")):
            require(number(one(axis, path, "fixed_axis_field_missing").get("val")) == number(expected["valueAxis"][key]),
                    "fixed_axis_value_mismatch")
        title = expected["valueAxis"]["title"]
        internal = "".join(node.text or "" for node in axis.findall("c:title//a:t", NS))
        if internal == title:
            title_binding = "native-chart-axis"
        else:
            require(not internal, "fixed_axis_title_conflict")
            texts = [shape.text for shape in shapes if shape.has_text_frame]
            require(texts.count(title) == 1, "retained_axis_title_missing_or_ambiguous")
            # Existence is observed; source/position binding remains the export receipt's obligation.
            title_binding = "native-sibling-text-present;position-binding-not-verified"
    return {"case": case["id"], "index": case["index"], "passed": True, "family": family,
            "nativeCharts": 1, "seriesCount": len(series), "categoryCount": len(expected["categories"]),
            "chartPart": part, "chartSha256": digest(raw), "workbookPart": workbook_part,
            "workbookSha256": digest(workbook_bytes), "axisTitleObservation": title_binding}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {"pptx": args.pptx.resolve(), "fixtures": args.fixtures.resolve(), "helper": Path(__file__).resolve()}
    require(args.output.resolve() not in inputs.values(), "output_aliases_input")
    report = {"schema": "independent-native-nine-pptx-audit/v1", "passed": False, "slides": [],
              "scope": "native chart data, workbook and bounded OOXML appearance; no rendering or visual acceptance",
              "unverified": ["publication/export receipt provenance", "separator stroke visual fidelity",
                             "retained sibling axis title position binding", "COM visual fidelity"]}
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        snapshots = {}
        try:
            require(args.fixture_sha256.removeprefix("sha256:") == FIXTURE_SHA256, "fixture_authority_mismatch")
            data = {}
            for name, path in inputs.items():
                data[name], snapshots[name] = snapshot(path)
            report["inputs"] = snapshots
            require(snapshots["fixtures"]["sha256"] == FIXTURE_SHA256, "fixture_bytes_mismatch")
            fixture = json.loads(data["fixtures"])
            cases = fixture["cases"]
            require(fixture["caseCount"] == 9 and tuple(case["id"] for case in cases) == CASE_ORDER
                    and [case["index"] for case in cases] == list(range(9)), "fixture_order_mismatch")
            backend = Path(__file__).resolve().parents[3] / "servers" / "fastapi"
            sys.path.insert(0, str(backend))
            from pptx import Presentation
            from workers.public_svg_pptx_readback import iter_shapes, _chart_workbook_bound
            from services.source_content.security import secure_xml
            deck = Presentation(io.BytesIO(data["pptx"]))
            require(len(deck.slides) == 9, "slide_count_mismatch")
            with ZipFile(io.BytesIO(data["pptx"])) as package:
                require(len(package.namelist()) == len(set(package.namelist())), "duplicate_pptx_zip_part")
                for slide, case in zip(deck.slides, cases, strict=True):
                    try:
                        report["slides"].append(verify_slide(slide, case, package, iter_shapes, _chart_workbook_bound, secure_xml))
                    except Exception as error:
                        report["slides"].append({"case": case["id"], "index": case["index"], "passed": False,
                            "reason": str(error) if isinstance(error, AuditFailure) else type(error).__name__})
            report["passed"] = len(report["slides"]) == 9 and all(row["passed"] for row in report["slides"])
        except Exception as error:
            report["failure"] = str(error) if isinstance(error, AuditFailure) else type(error).__name__
        finally:
            for name, before in snapshots.items():
                try:
                    _, after = snapshot(inputs[name])
                    if after != before:
                        report["passed"] = False
                        report.setdefault("changedInputs", []).append(name)
                except Exception:
                    report["passed"] = False
                    report.setdefault("changedInputs", []).append(name)
            json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    print("native PPTX audit passed" if report["passed"] else "native PPTX audit failed; inspect new report")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
