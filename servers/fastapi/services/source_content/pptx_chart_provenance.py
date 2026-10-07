"""Sealed native-chart facts, including the original embedded workbook."""
from io import BytesIO
import math
import re
from zipfile import ZipFile, BadZipFile

from services.source_content.digests import digest_bytes
from services.source_content.pptx_extraction_constants import NS, R_NS
from services.source_content.pptx_package_parts import _relationship_map
from services.source_content.security import secure_xml, _admit_entry, MAX_FILES, MAX_UNCOMPRESSED_BYTES
from services.source_content.errors import SourceContentError

_S = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def native_chart_provenance(package, part, root, chart, series):
    """Record unsupported semantics explicitly; never infer them from labels."""
    reasons = []
    plot = root.find(".//c:plotArea", NS)
    if len([x for x in plot if x.tag.rsplit('}', 1)[-1].endswith('Chart')]) != 1:
        reasons.append("combined_chart")
    grouping = chart.find("c:grouping", NS)
    grouping = grouping.get("val") if grouping is not None else "standard"
    if grouping not in {"standard", "clustered"}:
        reasons.append("grouping")
    if chart.findall(".//c:trendline", NS) or chart.findall(".//c:errBars", NS):
        reasons.append("derived_series")
    title = "".join(root.xpath("./c:chart/c:title//a:t/text()", namespaces=NS))
    if root.findall("./c:chart/c:title/c:strRef", NS):
        reasons.append("referenced_title")
    ordered_series = sorted(chart.findall("c:ser", NS), key=lambda node: int(node.find("c:order", NS).get("val")))
    source_series = []
    for element in ordered_series:
        idx, order = element.find("c:idx", NS), element.find("c:order", NS)
        source_series.append({"index": idx.get("val") if idx is not None else None,
                              "order": order.get("val") if order is not None else None})
    if (any(s["index"] is None or s["order"] is None for s in source_series)
            or len({s["index"] for s in source_series}) != len(source_series)
            or len({s["order"] for s in source_series}) != len(source_series)):
        reasons.append("series_identity")
    axes = []
    for axis in plot:
        kind = axis.tag.rsplit('}', 1)[-1]
        if not kind.endswith("Ax"):
            continue
        axis_id, cross_axis = axis.find("c:axId", NS), axis.find("c:crossAx", NS)
        facts = {"kind": kind, "title": "".join(axis.xpath("./c:title//a:t/text()", namespaces=NS)),
                 "id": axis_id.get("val") if axis_id is not None else None,
                 "crossAxisId": cross_axis.get("val") if cross_axis is not None else None}
        for name, path in {"orientation": "c:scaling/c:orientation", "minimum": "c:scaling/c:min",
                "maximum": "c:scaling/c:max", "majorUnit": "c:majorUnit", "minorUnit": "c:minorUnit",
                "crosses": "c:crosses", "crossesAt": "c:crossesAt", "logBase": "c:scaling/c:logBase",
                "displayUnits": "c:dispUnits/c:builtInUnit", "customUnits": "c:dispUnits/c:custUnit"}.items():
            node = axis.find(path, NS)
            facts[name] = node.get("val") if node is not None else None
        fmt = axis.find("c:numFmt", NS)
        facts["numberFormat"] = fmt.get("formatCode") if fmt is not None else "General"
        if (kind not in {"catAx", "valAx"} or facts["orientation"] not in {None, "minMax"}
                or facts["crosses"] not in {None, "autoZero"}
                or any(facts[k] is not None for k in ("minimum", "maximum", "majorUnit", "minorUnit", "crossesAt", "displayUnits", "customUnits", "logBase"))
                or facts["numberFormat"] != "General"):
            reasons.append("axis_semantics")
        axes.append(facts)
    if axes and sorted(a["kind"] for a in axes) != ["catAx", "valAx"]:
        reasons.append("axis_relationship")
    if axes and (len({a["id"] for a in axes}) != len(axes) or
                 any(a["crossAxisId"] not in {b["id"] for b in axes if b is not a} for a in axes)):
        reasons.append("axis_relationship")
    external = root.find("c:externalData", NS)
    rel = _relationship_map(package, part).get(external.get(f"{{{R_NS}}}id") if external is not None else "")
    workbook = None
    if rel and rel[1].endswith("/package") and rel[0].endswith(".xlsx") and rel[0] in package.namelist():
        data = package.read(rel[0])
        workbook = {"part": rel[0], "digest": digest_bytes(data), "cacheVerified": False}
        try:
            _verify_workbook(data, chart, series)
            workbook["cacheVerified"] = True
        except (ValueError, KeyError, TypeError, OverflowError, BadZipFile, SourceContentError):
            reasons.append("workbook_cache_mismatch")
    else:
        reasons.append("embedded_workbook_missing")
    return {"version": "pptx-native-chart-facts/v1", "chartDigest": digest_bytes(package.read(part)),
            "title": title, "grouping": grouping, "axes": axes, "seriesIdentity": source_series, "workbook": workbook,
            "unsupported": sorted(set(reasons))}


def _verify_workbook(data, chart, series):
    with ZipFile(BytesIO(data)) as book:
        infos = book.infolist()
        if not infos or len(infos) > MAX_FILES or sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("workbook_package_limit")
        names = [_admit_entry(info, archive_size=len(data)) for info in infos]
        if len(set(names)) != len(names):
            raise ValueError("workbook_duplicate_part")
        root = secure_xml(book.read("xl/workbook.xml"), part_name="xl/workbook.xml")
        rels = _relationship_map(book, "xl/workbook.xml")
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            strings = ["".join(x.itertext()) for x in secure_xml(book.read("xl/sharedStrings.xml"), part_name="xl/sharedStrings.xml").findall("s:si", _S)]
        sheets = {}
        for sheet in root.findall("s:sheets/s:sheet", _S):
            part = rels[sheet.get(f"{{{R_NS}}}id")][0]
            cells = {}
            for cell in secure_xml(book.read(part), part_name=part).findall(".//s:sheetData/s:row/s:c", _S):
                value = cell.find("s:v", _S)
                text = value.text if value is not None else ""
                if cell.get("t") == "s":
                    text = strings[int(text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(cell.find("s:is", _S).itertext())
                elif cell.get("t") not in {None, "n", "str"}:
                    raise ValueError("workbook_cell_invalid")
                cells[cell.get("r")] = text
            sheets[sheet.get("name")] = cells
        ordered_series = sorted(chart.findall("c:ser", NS), key=lambda node: int(node.find("c:order", NS).get("val")))
        for element, expected in zip(ordered_series, series, strict=True):
            for tag, values in (("tx", [expected["name"]]), ("cat", expected["categories"]), ("val", expected["values"])):
                formula = element.find(f"c:{tag}//c:f", NS)
                if formula is None:
                    raise ValueError("workbook_formula_missing")
                match = re.fullmatch(r"(?:'((?:[^']|'')+)'|([^!]+))!\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?", formula.text or "")
                if not match or (match[5] and match[5] != match[3]):
                    raise ValueError("workbook_formula_unsupported")
                sheet = (match[1] or match[2]).replace("''", "'")
                actual = [sheets[sheet][f"{match[3]}{i}"] for i in range(int(match[4]), int(match[6] or match[4]) + 1)]
                if tag == "val":
                    actual = [float(x) for x in actual]
                    if not all(math.isfinite(x) for x in actual):
                        raise ValueError("workbook_nonfinite")
                if actual != values:
                    raise ValueError("workbook_cache_mismatch")
