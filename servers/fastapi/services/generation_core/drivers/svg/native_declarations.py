"""Model-declared native tables/charts in an authored SVG slide.

The SVG author draws a table (or chart) as ordinary contract primitives inside
``<g id=".." data-native-table='JSON'>``. The ``data-*`` attribute is not part
of the SVG contract, so :func:`extract_native_declarations` removes it from the
parsed tree *before* validation and returns the parsed JSON as a declaration.
The stored SVG is therefore contract-clean and identical to what the model drew.

A declaration only means something for the exact drawing it came from.
:func:`bind_native_declarations` stores the page's declarations in
``payload["nativeDeclarations"]`` together with :func:`native_binding_digest`
of that SVG; publication (``native_publish.py``) honours them only when the
digest of the page's final SVG is the same. A correction attempt, a
``last_valid_svg`` restore or a terminal fallback page carries a different SVG
and so can never pick up another drawing's declarations.

The binding digest ignores the system page number: ``finalize_svg`` removes
and re-injects that one element (and re-serializes the tree) between the author
attempt and the final page, and it is never inside a declared group.
"""
from __future__ import annotations

import json
import re
from xml.etree import ElementTree as ET

from services.svg_contract import PAGE_NUMBER_ID, source_digest

#: Whether the author prompt offers native charts (and extraction keeps chart
#: declarations). The converter probe confirmed native support for every V1
#: chartType; setting this to False withdraws the offer in one place.
NATIVE_CHARTS_OFFERED = True

TABLE_ATTRIBUTE = "data-native-table"
CHART_ATTRIBUTE = "data-native-chart"
PAYLOAD_KEY = "nativeDeclarations"
_KINDS = {TABLE_ATTRIBUTE: "table", CHART_ATTRIBUTE: "chart"}
_MAX_JSON_CHARS = 64_000
_MAX_DECLARATIONS = 32
_GROUP = "{http://www.w3.org/2000/svg}g"

NATIVE_TABLE_RULE = (
    "For tabular data draw each table as <g id=\"native-table-SLUG\" data-native-table='JSON'> "
    "(SLUG unique on the slide) holding only rect, line and text: draw every row and column "
    "boundary, outer edges included, as a line or a cell-rect edge (for a borderless look use "
    "cell rects or lines in the background colour), and exactly one text per non-empty cell "
    "(a multi-line cell uses tspan lines, one explicit y per line). "
    "JSON is {\"rows\":[[\"Header\",\"Header\"],[\"cell\",\"1,200\"]],\"headerRows\":1}: every row "
    "has the same number of cells, each cell string is exactly the text drawn in that cell "
    "(\\n between lines), \"\" for an empty cell. No other text inside the group; put captions "
    "and sources outside it. Preserve explicit source cell paragraph breaks in the drawing and JSON; "
    "additional visual wrapping is allowed within a paragraph, but cannot join original paragraphs. "
    "XML-escape &, < and ' inside the attribute.\n"
)
NATIVE_CHART_RULE = (
    "For a bar, column, line, area, pie or donut chart draw it as <g id=\"native-chart-SLUG\" "
    "data-native-chart='JSON'> (SLUG unique on the slide) with JSON {\"chartType\":\"column\","
    "\"categories\":[\"A\",\"B\"],\"series\":[{\"name\":\"Sales\",\"values\":[120,95],"
    "\"pointColors\":[\"#2457D6\",\"#68A4FF\"]}],\"categoryOrder\":\"leftToRight\","
    "\"valueLabels\":{\"shown\":true,\"position\":\"insideEnd\"},\"markers\":false,"
    "\"title\":\"optional\"}. Use topToBottom for bar categories, leftToRight for column/line/area, "
    "clockwise for pie/donut. Point colours must match each drawn bar or slice in category order. "
    "Draw the title above the plot and every category and series name as text. Draw one value label "
    "per value only when shown=true; its position must agree with the JSON. Line markers are "
    "circles when markers=true. No axis tick labels or images inside the group.\n"
)


def native_author_rules(*, source_grounded: bool = False) -> str:
    """The native-object rule text appended to the SVG author rules."""
    table_rule = NATIVE_TABLE_RULE
    if source_grounded:
        table_rule = table_rule.replace("draw every row and column boundary", "draw every original row and column boundary")
        table_rule += (
            "When current source-native bindings or source visual evidence explicitly establish a "
            "horizontal merged cell, preserve its common application across those columns. "
            "Keep the exact rectangular rows and original empty covered cells in JSON; never repeat "
            "the value into the covered cells or add mergedCells to this declaration. Draw full "
            "column boundaries in the header and unmerged rows, but omit internal vertical "
            "separators within each evidenced merged cell and place its original text within "
            "the merged area. This source-supported merge is an exception to drawing every cell "
            "boundary; all other grid and text requirements remain. Do not infer merging from "
            "empty cells or visual convenience alone.\n"
        )
    return table_rule + (NATIVE_CHART_RULE if NATIVE_CHARTS_OFFERED else "")


def _drop(dropped: list, node, kind: str, reason: str) -> None:
    dropped.append({"id": node.get("id") or "", "kind": kind, "reason": reason})


def extract_native_declarations(root: ET.Element) -> dict | None:
    """Strip declaration attributes from ``root`` in place and return them.

    Returns ``None`` when the tree declares nothing, else
    ``{"objects": [{"id", "kind", "raw"}], "dropped": [{"id", "kind", "reason"}]}``.
    A malformed declaration is dropped with a reason; it never fails the page.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    declared: list[tuple[ET.Element, list[str]]] = []
    for node in root.iter():
        attributes = [key for key in _KINDS if key in node.attrib]
        if attributes:
            declared.append((node, attributes))
    if not declared:
        return None
    declared_nodes = {node for node, _ in declared}
    objects: list[dict] = []
    dropped: list[dict] = []
    for node, attributes in declared:
        raws = {key: node.attrib.pop(key) for key in attributes}
        kind = _KINDS[attributes[0]]
        if len(attributes) > 1:
            _drop(dropped, node, kind, "native_declaration_kind_ambiguous")
            continue
        if node.tag != _GROUP:
            _drop(dropped, node, kind, "native_declaration_not_group")
            continue
        if not node.get("id"):
            _drop(dropped, node, kind, "native_declaration_id_missing")
            continue
        ancestor = parents.get(node)
        while ancestor is not None and ancestor not in declared_nodes:
            ancestor = parents.get(ancestor)
        if ancestor is not None:
            _drop(dropped, node, kind, "native_declaration_nested")
            continue
        if kind == "chart" and not NATIVE_CHARTS_OFFERED:
            _drop(dropped, node, kind, "native_chart_not_offered")
            continue
        raw_text = raws[attributes[0]]
        if len(raw_text) > _MAX_JSON_CHARS:
            _drop(dropped, node, kind, "native_declaration_too_large")
            continue
        try:
            raw = json.loads(raw_text)
        except ValueError:
            _drop(dropped, node, kind, "native_declaration_json_invalid")
            continue
        if not isinstance(raw, dict):
            _drop(dropped, node, kind, "native_declaration_json_invalid")
            continue
        if len(objects) >= _MAX_DECLARATIONS:
            _drop(dropped, node, kind, "native_declaration_limit")
            continue
        objects.append({"id": node.get("id"), "kind": kind, "raw": raw})
    return {"objects": objects, "dropped": dropped}


def declaration_receipt(declarations: dict | None) -> dict:
    """The author receipt's summary of one attempt's declarations (no raw JSON)."""
    if not declarations:
        return {}
    return {"native_declarations": {
        "declared": len(declarations["objects"]) + len(declarations["dropped"]),
        "extracted": [{"id": item["id"], "kind": item["kind"]} for item in declarations["objects"]],
        "dropped": list(declarations["dropped"])}}


def native_binding_digest(svg: str | None) -> str | None:
    """Digest excluding server-owned furniture; ``None`` if unparsable."""
    if not isinstance(svg, str) or not svg or re.search(r"<!|<\?", svg):
        return None
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return None
    from services.svg_common_header import GROUP_ID

    common_header_count = sum(child.tag == _GROUP and child.get("id") == GROUP_ID for child in root)
    for parent in root.iter():
        for child in list(parent):
            if (child.get("id") == PAGE_NUMBER_ID or child.get("id", "").startswith("svg-system-master-")
                    or (common_header_count == 1 and parent is root
                        and child.tag == _GROUP and child.get("id") == GROUP_ID)):
                parent.remove(child)
    return source_digest(ET.tostring(root, encoding="unicode"))


def bind_native_declarations(payload: dict, svg: str | None, declarations: dict | None) -> None:
    """Record ``declarations`` as the ones drawn by ``svg`` (or clear the record).

    Called wherever an author attempt sets the page's SVG; every other path
    that replaces the SVG leaves a record whose digest no longer matches.
    """
    digest = native_binding_digest(svg)
    if not declarations or digest is None:
        payload.pop(PAYLOAD_KEY, None)
        return
    payload[PAYLOAD_KEY] = {"svgDigest": digest, "objects": list(declarations["objects"]),
                            "dropped": list(declarations["dropped"])}


def bound_declarations(payload: dict, svg: str | None) -> tuple[dict | None, bool]:
    """``(record, current)``: the page's declaration record and whether it binds ``svg``."""
    record = payload.get(PAYLOAD_KEY)
    if not isinstance(record, dict):
        return None, False
    return record, record.get("svgDigest") is not None and record["svgDigest"] == native_binding_digest(svg)


__all__ = [
    "CHART_ATTRIBUTE", "NATIVE_CHARTS_OFFERED", "NATIVE_CHART_RULE", "NATIVE_TABLE_RULE", "PAYLOAD_KEY",
    "TABLE_ATTRIBUTE", "bind_native_declarations", "bound_declarations", "declaration_receipt",
    "extract_native_declarations", "native_author_rules", "native_binding_digest",
]
