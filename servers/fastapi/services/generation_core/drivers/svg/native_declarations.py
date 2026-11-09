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


NATIVE_CHART_GUIDANCE_V4 = (
    "Prefer donut or pie for composition (parts of a whole), and bar or line for trends and comparisons. "
    "A single-series chart may use its one unique drawn title naming that series instead of a separate "
    "legend; draw that title once above the plot. Multi-series charts still draw every series name "
    "separately. Draw all data visibly inside the chart group: every category, series name and required "
    "value; declaration metadata alone never counts as drawn data. Set valueLabels.shown=true for "
    "required values and use their declared label position.\n"
)

NATIVE_CHART_GUIDANCE_V5 = (
    "Preserve the requested chart family, category/series associations, signed or zero values and units. "
    "Draw truthful bar/column lengths, common-scale line/area traces, or pie/donut shares; correct numeric "
    "text alone does not prove the data drawing. Bind each series name visibly to its actual marks through "
    "a matching legend or an unambiguous direct label. Keep every displayed value with its own datum. "
    "Inside the chart group, draw the declared title text exactly as declared; draw every bar or column "
    "of one chart on one shared scale from one baseline with equal thickness; draw axis and baseline "
    "lines before the bars, beneath them, never over them. "
    "Use only the existing chart declaration/export contracts, including their grouping and fixed-axis "
    "rules. If the requested form cannot be represented faithfully, preserve its source meaning and report "
    "the limitation; do not convert it into another family, invent data, or claim unsupported acceptance. "
    "Safe preservation of a correctly drawn chart after an editability-only failure requires independent "
    "data-geometry and captured-paint evidence; it does not establish native editability or release readiness.\n"
)


def _native_chart_rules(requirements_revision: str | None) -> str:
    # Local import: native_requirements imports native_publish, which imports this module.
    from services.generation_core.drivers.svg.native_requirements import REQUIREMENTS_VERSION_V4, REQUIREMENTS_VERSION_V5

    return (NATIVE_CHART_RULE
            + (NATIVE_CHART_GUIDANCE_V4 if requirements_revision in (REQUIREMENTS_VERSION_V4, REQUIREMENTS_VERSION_V5) else "")
            + (NATIVE_CHART_GUIDANCE_V5 if requirements_revision == REQUIREMENTS_VERSION_V5 else ""))


#: Revision ten's stacked bar/column rule (plan item S7), appended to ``NATIVE_CHART_RULE``.
#: Earlier revisions never send it; their native rule bytes are unchanged.
NATIVE_STACKED_CHART_RULE = (
    "Stacked bar/column: set \"grouping\":\"stacked\" (values add up) or \"percentStacked\" (every "
    "stack fills the full length) and declare at least two series. Draw each category's segments from "
    "one shared baseline in series order with no gaps, equal thickness, centred on the category label; "
    "lengths proportional to values (percentStacked: equal totals, segments proportional to shares). "
    "Value labels only center/insideEnd/insideBase; values > 0; one colour per series.\n"
)


def native_author_rules_v10(*, requirements_revision: str | None = None) -> str:
    """Revision ten's native rules: the table and chart rules plus the stacked chart rule.

    A new-copy free deck has no source tables or source-native bindings, so the source
    paragraph sentence of the table rule and the merged-cell addendum of
    ``native_author_rules(source_grounded=True)`` are left out.
    """
    table_rule = NATIVE_TABLE_RULE.replace(_SOURCE_PARAGRAPH_SENTENCE, "")
    return table_rule + (_native_chart_rules(requirements_revision) + NATIVE_STACKED_CHART_RULE
                         if NATIVE_CHARTS_OFFERED else "")


_SOURCE_PARAGRAPH_SENTENCE = (
    "Preserve explicit source cell paragraph breaks in the drawing and JSON; "
    "additional visual wrapping is allowed within a paragraph, but cannot join original paragraphs. "
)


def native_author_rules(*, source_grounded: bool = False, requirements_revision: str | None = None) -> str:
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
    return table_rule + (_native_chart_rules(requirements_revision) if NATIVE_CHARTS_OFFERED else "")


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
        if kind == "table":
            # In place, before anything validates or stores the tree, so every replay of a
            # raw response (author, partial recovery, page remake) folds identically.
            from services.generation_core.drivers.svg.native_table import fold_wrapped_cell_lines

            fold_wrapped_cell_lines(node, raw)
        if kind == "chart" and "data-chart-render" in node.attrib:
            # A server-rendered placeholder: drawn here, in place, by the renderer revision
            # its marker names, so every replay of a raw response draws the identical tree.
            from services.generation_core.drivers.svg.server_chart import (
                BOX_ATTRIBUTE, RENDER_ATTRIBUTE, ServerChartUnsupported, render_server_chart,
            )
            try:
                revision, box, extras = render_server_chart(root, node, raw)
            except ServerChartUnsupported as error:
                node.attrib.pop(RENDER_ATTRIBUTE, None)
                node.attrib.pop(BOX_ATTRIBUTE, None)
                _drop(dropped, node, kind, str(error))
                continue
            objects.append({"id": node.get("id"), "kind": kind, "raw": raw, "render": revision, "box": box,
                            **extras})
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


def rebind_native_declarations(payload: dict, drawn_svg: str | None, composed_svg: str | None) -> bool:
    """Carry the declarations bound to ``drawn_svg`` over to ``composed_svg``, its server composition.

    A template-redesign page publishes ``compose_template_page(drawn)``: the body moves into
    ``svg-system-template-body`` between the frame's layers and every id-less body node gets a
    ``body-node-N`` id, so the composed page never has the drawing's digest. Measured
    2026-11-04: every template page's declarations were dropped as ``native_declaration_stale_svg``
    and its outline table reported ``native_requested_table_missing``. The caller passes only
    the composition of exactly the drawing the record binds (checked here); any other SVG,
    including a later correction of the composed page, still leaves the record stale.
    Returns whether the record was moved.
    """
    record, current = bound_declarations(payload, drawn_svg)
    digest = native_binding_digest(composed_svg)
    if record is None or not current or digest is None:
        return False
    payload[PAYLOAD_KEY] = {**record, "svgDigest": digest}
    return True


def follow_visible_text_edits(payload: dict, before_svg: str | None, after_svg: str | None) -> bool:
    """Carry the declarations bound to ``before_svg`` over a text-only server edit to ``after_svg``.

    The writing policy's visible-symbol substitution
    (``ppt_writing_visible.substitute_svg_visible_symbols``) rewrites text content after the
    declarations were bound, and never an element or attribute. Measured 2026-11-04: one
    substituted ``·`` on a page left both its table and chart ``native_declaration_stale_svg``.
    A declared group's drawn text that changed is changed the same way in its JSON (every string
    equal to the old drawn text, with its lines joined by a newline or a space), so the
    declaration still says exactly what the group shows. Gives up (the record stays stale) when
    the two pages differ in more than text, or one old text became two different new texts.
    """
    record, current = bound_declarations(payload, before_svg)
    digest = native_binding_digest(after_svg)
    if record is None or not current or digest is None:
        return False
    from services.generation_core.drivers.svg.native_geometry import normalize_text
    from services.generation_core.drivers.svg.native_table import drawn_cell_text

    try:
        before, after = ET.fromstring(before_svg), ET.fromstring(after_svg)
    except ET.ParseError:
        return False
    if [(node.tag, node.attrib) for node in before.iter()] != [(node.tag, node.attrib) for node in after.iter()]:
        return False
    by_id = {node.get("id"): (node, twin) for node, twin in zip(before.iter(), after.iter(), strict=True)
             if node.get("id")}
    objects = []
    for item in record.get("objects") or []:
        nodes = by_id.get(item.get("id"))
        if nodes is None:
            return False
        mapping: dict[str, str] = {}
        for old, new in zip(nodes[0].iter(), nodes[1].iter(), strict=True):
            if old.tag.rsplit("}", 1)[-1] != "text":
                continue
            was, now = drawn_cell_text(old), drawn_cell_text(new)
            for key, value in ((was, now), (normalize_text(was.replace("\n", " ")), normalize_text(now.replace("\n", " ")))):
                if key != value:
                    if mapping.setdefault(key, value) != value:
                        return False

        objects.append({**item, "raw": _replace_texts(item.get("raw"), mapping)} if mapping else item)
    payload[PAYLOAD_KEY] = {**record, "objects": objects, "svgDigest": digest}
    return True


def _replace_texts(value, mapping: dict[str, str]):
    """``value`` with every string whose normalized text is a key of ``mapping`` replaced."""
    from services.generation_core.drivers.svg.native_geometry import normalize_cell

    if isinstance(value, str):
        return mapping.get(normalize_cell(value), value)
    if isinstance(value, list):
        return [_replace_texts(entry, mapping) for entry in value]
    if isinstance(value, dict):
        return {key: _replace_texts(entry, mapping) for key, entry in value.items()}
    return value


def bound_declarations(payload: dict, svg: str | None) -> tuple[dict | None, bool]:
    """``(record, current)``: the page's declaration record and whether it binds ``svg``."""
    record = payload.get(PAYLOAD_KEY)
    if not isinstance(record, dict):
        return None, False
    return record, record.get("svgDigest") is not None and record["svgDigest"] == native_binding_digest(svg)


__all__ = [
    "CHART_ATTRIBUTE", "NATIVE_CHARTS_OFFERED", "NATIVE_CHART_RULE", "NATIVE_TABLE_RULE", "PAYLOAD_KEY",
    "TABLE_ATTRIBUTE", "bind_native_declarations", "bound_declarations", "declaration_receipt",
    "extract_native_declarations", "follow_visible_text_edits", "native_author_rules", "native_binding_digest",
    "rebind_native_declarations",
]
