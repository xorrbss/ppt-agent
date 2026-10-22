"""Server-filled ``data-native-table`` rows for source tables (SVG source output view, table stage)."""
from __future__ import annotations

import copy
import inspect
import json
from xml.etree import ElementTree as ET

import pytest

from services.generation_core.authoring import author_slide_svg as author_module
from services.generation_core.authoring import author_source_svg_layout as layout_module
from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
from services.generation_core.drivers.svg import native_declarations as declarations_module
from services.generation_core.drivers.svg.native_declarations import (
    TABLE_ATTRIBUTE, bind_native_declarations, extract_native_declarations, native_author_rules,
)
from services.generation_core.drivers.svg.native_publish import page_native_objects
from services.generation_core.native_objects import NativeObjects
from services.source_content.contracts import SourceContentManifest
from services.source_content.digests import digest_json
from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V8, QUALITY_RECOVERY_POLICY_V2
from services.svg_contract import finalize_svg, validate_svg
from services.svg_source_layout import _cells
from services.svg_source_native_tables import (
    COMPACT_SOURCE_TABLE_GROUP_RULE, NATIVE_TABLE_VIEW_VERSION, compact_source_table_rules,
    expand_source_table_declarations, source_table_group_id, source_table_rows_from_facts,
)
from utils.hashing import canonical_json_digest

NS = "http://www.w3.org/2000/svg"
HEAD = f'<svg xmlns="{NS}" width="1280" height="720" viewBox="0 0 1280 720">'


def manifest_for(blocks):
    packed = [dict(sourceBlockId=f"p0:{i}", kind=kind, semanticRole=kind,
        order=i, payload=payload, contentDigest=digest_json(payload)) for i, (kind, payload) in enumerate(blocks)]
    manifest = SourceContentManifest(manifest_id="m", owner_id="owner", reference_snapshot_id="snap",
        source_digest="sha256:input", manifest_digest="pending",
        object_inventory=dict(discoveredObjectCount=0, classifiedObjectCount=0, contentBearingObjectCount=0),
        inventory_objects=(), units=[dict(sourceUnitId="p0", sourceIndex=0, blocks=packed)],
        counts=dict(sourceUnits=1, textBlocks=sum(k == "text" for k, p in blocks), tableCells=0,
                    chartValues=0, uniqueImages=0, imageOccurrences=0))
    payload = manifest.model_dump(mode="json", by_alias=True)
    payload.pop("manifestDigest")
    return manifest.model_copy(update={"manifest_digest": digest_json(payload)})


# A 4x3 source table: a 2x2 rowSpan/gridSpan merge, a multi-line cell, a horizontal merge, XML-special
# text and an empty anchor cell.
MERGED_ROWS = [
    [{"text": "Merged & <src>", "rowSpan": 2, "gridSpan": 2}, {"text": "", "mergeCovered": True}, {"text": "A"}],
    [{"text": "", "mergeCovered": True}, {"text": "", "mergeCovered": True}, {"text": "첫 줄\n둘째 줄"}],
    [{"text": "가로 병합", "gridSpan": 2}, {"text": "", "hMerge": True, "mergeCovered": True}, {"text": ""}],
    [{"text": "C"}, {"text": "D"}, {"text": "E"}],
]
MERGED_EXPECTED = [["Merged & <src>", "", "A"], ["", "", "첫 줄\n둘째 줄"], ["가로 병합", "", ""], ["C", "D", "E"]]
PLAIN_ROWS = [[{"text": "지역", "sourceCellId": "h1"}, {"text": "매출", "sourceCellId": "h2"}],
              [{"text": "서울", "sourceCellId": "a"}, {"text": "1,200", "sourceCellId": "b"}]]
PLAIN_EXPECTED = [["지역", "매출"], ["서울", "1,200"]]


def _table_block(rows):
    return ("table", {"rows": rows, "columnWidthsEmu": [1] * len(rows[0]), "rowHeightsEmu": [1] * len(rows)})


def _page(*tables):
    manifest = manifest_for([("text", {"text": "제목"}), *(_table_block(rows) for rows in tables)])
    return manifest.units[0]


def _page_based_rows(page):
    """What binding sees: ``_cells`` anchors over the source grid, per canonical group id."""
    tables = {}
    for block in page.blocks:
        if block.kind != "table":
            continue
        grid, cells = _cells(block)
        rows = [["" for _ in grid[0]] for _ in grid]
        for row, column, _row_span, _column_span, cell in cells:
            rows[row][column] = cell["text"]
        tables[source_table_group_id(block.source_block_id)] = rows
    return tables


def _svg(*groups):
    title = '<text x="64" y="100" font-family="Inter" font-size="32" fill="#111111">제목</text>'
    return HEAD + title + "".join(groups) + "</svg>"


def _group(group_id, declaration, *, tag="g"):
    node = ET.Element(f"{{{NS}}}{tag}", id=group_id)
    if declaration is not None:
        node.set(TABLE_ATTRIBUTE, declaration)
    ET.SubElement(node, f"{{{NS}}}rect", x="0", y="0", width="10", height="10")
    return node


def _tree(*groups):
    root = ET.fromstring(HEAD + "</svg>")
    for group in groups:
        root.append(group)
    return root


def _full(rows, header=1):
    return json.dumps({"rows": rows, "headerRows": header}, ensure_ascii=False, separators=(",", ":"))


# --- source_table_rows_from_facts -----------------------------------------------------------------

def test_view_version_is_pinned():
    assert NATIVE_TABLE_VIEW_VERSION == "svg-native-table-source-rows/v1"


def test_rows_from_facts_equal_the_page_based_rows_for_merges_empty_and_multiline_cells():
    page = _page(MERGED_ROWS, PLAIN_ROWS)
    facts = _whole_page_facts(page, {})
    expected = _page_based_rows(page)
    assert len(expected) == 2
    assert source_table_rows_from_facts(facts) == expected
    by_value = list(expected.values())
    assert by_value == [MERGED_EXPECTED, PLAIN_EXPECTED]
    # The same facts after a JSON round trip (what the content JSON carries) give the same rows.
    assert source_table_rows_from_facts(json.loads(json.dumps(facts, ensure_ascii=False))) == expected
    # The prompt view drops ``data.rows[r][c].text`` where ``cells[]`` repeats it; the rows do not change.
    reduced = [author_module._reduced_table_fact(fact) or fact for fact in facts]
    assert any(item is not fact for item, fact in zip(reduced, facts, strict=True))
    assert source_table_rows_from_facts(reduced) == expected


def test_rows_from_facts_use_canonical_group_ids_and_skip_other_facts():
    page = _page(MERGED_ROWS)
    facts = _whole_page_facts(page, {})
    [table_fact] = [fact for fact in facts if fact["kind"] == "table"]
    assert list(source_table_rows_from_facts(facts)) == [table_fact["element_id"]]
    assert table_fact["element_id"].startswith("source-native-table-")
    assert source_table_rows_from_facts([fact for fact in facts if fact["kind"] != "table"]) == {}
    assert source_table_rows_from_facts([]) == {}


def test_rows_from_facts_do_not_mutate_the_facts_and_rows_are_independent_lists():
    facts = _whole_page_facts(_page(MERGED_ROWS), {})
    before = copy.deepcopy(facts)
    first = source_table_rows_from_facts(facts)
    assert facts == before
    next(iter(first.values()))[0][0] = "changed"
    assert source_table_rows_from_facts(facts) != first


@pytest.mark.parametrize("mutate", [
    lambda fact: fact.pop("cells"),
    lambda fact: fact.pop("data"),
    lambda fact: fact["data"].pop("rows"),
    lambda fact: fact["data"].update(rows=[]),
    lambda fact: fact["data"].update(rows=[[{"text": "a"}], [{"text": "b"}, {"text": "c"}]]),
    lambda fact: fact["cells"].append(dict(fact["cells"][0])),
    lambda fact: fact["cells"][0].update(row=99),
    lambda fact: fact["cells"][0].update(column=-1),
    lambda fact: fact["cells"][0].update(row=True),
    lambda fact: fact["cells"][0].update(text=None),
    lambda fact: fact["cells"].append("not a cell"),
    lambda fact: fact.update(element_id=None),
])
def test_rows_from_facts_reject_malformed_server_facts(mutate):
    facts = copy.deepcopy(_whole_page_facts(_page(MERGED_ROWS), {}))
    [table_fact] = [fact for fact in facts if fact["kind"] == "table"]
    mutate(table_fact)
    with pytest.raises(ValueError, match="svg_output_view_table_facts_invalid"):
        source_table_rows_from_facts(facts)


def test_rows_from_facts_reject_a_repeated_group_id():
    facts = _whole_page_facts(_page(MERGED_ROWS), {})
    [table_fact] = [fact for fact in facts if fact["kind"] == "table"]
    with pytest.raises(ValueError, match="svg_output_view_table_facts_invalid"):
        source_table_rows_from_facts([table_fact, copy.deepcopy(table_fact)])


# --- expand_source_table_declarations -------------------------------------------------------------

@pytest.fixture
def source():
    page = _page(MERGED_ROWS, PLAIN_ROWS)
    rows = source_table_rows_from_facts(_whole_page_facts(page, {}))
    merged_id, plain_id = list(rows)
    return page, rows, merged_id, plain_id


@pytest.mark.parametrize("compact, header", [
    ('{"headerRows":1}', 1), ('{"headerRows":0}', 0), ('{ "headerRows" : 2 }', 2), ('{"headerRows":-1}', -1),
])
def test_compact_declaration_expands_to_the_exact_full_declaration_a_model_writes_today(source, compact, header):
    page, rows, merged_id, plain_id = source
    root = _tree(_group(merged_id, compact), _group(plain_id, compact))
    counts = expand_source_table_declarations(root, rows)
    assert counts == {"expanded": 2, "full_form": 0, "absent": 0, "foreign": 0}
    # A model following today's rule writes the full JSON itself; the two trees are byte-identical.
    full_by_model = _tree(_group(merged_id, _full(MERGED_EXPECTED, header)),
                          _group(plain_id, _full(PLAIN_EXPECTED, header)))
    assert ET.tostring(root, encoding="unicode") == ET.tostring(full_by_model, encoding="unicode")
    assert root[0].get(TABLE_ATTRIBUTE) == _full(MERGED_EXPECTED, header)
    # ... and extraction cannot tell them apart, key order included.
    assert json.dumps(extract_native_declarations(root), ensure_ascii=False) == \
        json.dumps(extract_native_declarations(full_by_model), ensure_ascii=False)


def test_header_rows_is_optional_but_rows_always_come_first(source):
    _, rows, merged_id, _ = source
    root = _tree(_group(merged_id, "{}"))
    assert expand_source_table_declarations(root, rows)["expanded"] == 1
    assert root[0].get(TABLE_ATTRIBUTE) == json.dumps({"rows": MERGED_EXPECTED}, ensure_ascii=False,
                                                      separators=(",", ":"))
    root = _tree(_group(merged_id, '{"headerRows":1}'))
    expand_source_table_declarations(root, rows)
    assert list(json.loads(root[0].get(TABLE_ATTRIBUTE))) == ["rows", "headerRows"]


def test_expansion_changes_only_the_declaration_attribute(source):
    _, rows, merged_id, _ = source
    group = _group(merged_id, '{"headerRows":1}')
    group.set("transform", "translate(1,2)")
    ET.SubElement(group, f"{{{NS}}}text", id="cell", x="1", y="2").text = "Merged & <src>"
    root = _tree(group)
    expected = copy.deepcopy(root)
    expected[0].set(TABLE_ATTRIBUTE, _full(MERGED_EXPECTED))
    expand_source_table_declarations(root, rows)
    assert ET.tostring(root, encoding="unicode") == ET.tostring(expected, encoding="unicode")


@pytest.mark.parametrize("value", [
    pytest.param(_full(MERGED_EXPECTED), id="full-json"),
    pytest.param(_full([["x"]]), id="full-json-other-rows"),
    pytest.param('{"headerRows":1,"note":"x"}', id="extra-key"),
    pytest.param('{"rows":[["a"]]}', id="rows-only"),
    pytest.param('{"headerRows":"1"}', id="string-header"),
    pytest.param('{"headerRows":true}', id="bool-header"),
    pytest.param('{"headerRows":1.0}', id="float-header"),
    pytest.param('{"headerRows":null}', id="null-header"),
    pytest.param('{"headerRows":[1]}', id="list-header"),
    pytest.param('[1,2]', id="array"),
    pytest.param('"headerRows"', id="string"),
    pytest.param('3', id="number"),
    pytest.param('null', id="null"),
    pytest.param('{"headerRows":1', id="truncated"),
    pytest.param('not json', id="invalid"),
    pytest.param('', id="empty"),
    pytest.param("[" * 200_000, id="recursion-bomb"),
    pytest.param('{"headerRows":' + "1" * 5000 + "}", id="oversized-int"),
])
def test_anything_that_is_not_the_compact_form_is_left_for_the_existing_validators(source, value):
    _, rows, merged_id, _ = source
    root = _tree(_group(merged_id, value))
    counts = expand_source_table_declarations(root, rows)
    assert root[0].get(TABLE_ATTRIBUTE) == value
    # The fixture's second source table is not drawn here, hence one absent group.
    assert counts == {"expanded": 0, "full_form": 1, "absent": 1, "foreign": 0}


def test_an_absent_attribute_is_counted_and_never_synthesized(source):
    _, rows, merged_id, plain_id = source
    root = _tree(_group(merged_id, None), _group(plain_id, '{"headerRows":1}'))
    counts = expand_source_table_declarations(root, rows)
    assert TABLE_ATTRIBUTE not in root[0].attrib
    assert root[1].get(TABLE_ATTRIBUTE) == _full(PLAIN_EXPECTED)
    assert counts == {"expanded": 1, "full_form": 0, "absent": 1, "foreign": 0}
    # A source table the model did not draw at all has no declaration either.
    counts = expand_source_table_declarations(_tree(_group(plain_id, '{"headerRows":1}')), rows)
    assert counts == {"expanded": 1, "full_form": 0, "absent": 1, "foreign": 0}


def test_declarations_on_other_nodes_are_foreign_and_untouched(source):
    _, rows, merged_id, plain_id = source
    invented = _group("native-table-sales", '{"headerRows":1}')
    alias_left = _group("t9", '{"headerRows":1}')
    anonymous = _group("x", '{"headerRows":1}')
    del anonymous.attrib["id"]
    chart = _group(plain_id, None)
    chart.set("data-native-chart", '{"headerRows":1}')
    root = _tree(invented, alias_left, anonymous, chart, _group(merged_id, '{"headerRows":1}'))
    counts = expand_source_table_declarations(root, rows)
    assert [node.get(TABLE_ATTRIBUTE) for node in root[:3]] == ['{"headerRows":1}'] * 3
    assert chart.get("data-native-chart") == '{"headerRows":1}' and TABLE_ATTRIBUTE not in chart.attrib
    assert counts == {"expanded": 1, "full_form": 0, "absent": 1, "foreign": 3}


def test_duplicate_source_group_ids_are_each_expanded_identically(source):
    _, rows, merged_id, plain_id = source
    root = _tree(_group(merged_id, '{"headerRows":1}'), _group(merged_id, '{"headerRows":1}'),
                 _group(plain_id, _full(PLAIN_EXPECTED)))
    counts = expand_source_table_declarations(root, rows)
    assert root[0].get(TABLE_ATTRIBUTE) == root[1].get(TABLE_ATTRIBUTE) == _full(MERGED_EXPECTED)
    assert counts == {"expanded": 2, "full_form": 1, "absent": 0, "foreign": 0}


def test_nested_non_group_and_root_declarations_follow_the_id_only(source):
    _, rows, merged_id, _ = source
    outer = _group(merged_id, '{"headerRows":1}', tag="rect")
    root = _tree(outer)
    root.set(TABLE_ATTRIBUTE, '{"headerRows":1}')
    counts = expand_source_table_declarations(root, rows)
    assert outer.get(TABLE_ATTRIBUTE) == _full(MERGED_EXPECTED)  # extraction drops it as not_group later
    assert root.get(TABLE_ATTRIBUTE) == '{"headerRows":1}'  # the root has no source id
    assert counts["expanded"] == 1 and counts["foreign"] == 1
    assert extract_native_declarations(root)["dropped"] == [
        {"id": "", "kind": "table", "reason": "native_declaration_not_group"},
        {"id": merged_id, "kind": "table", "reason": "native_declaration_not_group"}]


def test_expansion_is_idempotent_and_does_not_touch_the_rows_mapping(source):
    _, rows, merged_id, plain_id = source
    before = copy.deepcopy(rows)
    root = _tree(_group(merged_id, '{"headerRows":1}'), _group(plain_id, '{"headerRows":0}'))
    expand_source_table_declarations(root, rows)
    once = ET.tostring(root, encoding="unicode")
    counts = expand_source_table_declarations(root, rows)
    assert ET.tostring(root, encoding="unicode") == once
    assert counts == {"expanded": 0, "full_form": 2, "absent": 0, "foreign": 0}
    assert rows == before


def test_no_table_rows_means_every_declaration_is_foreign():
    root = _tree(_group("native-table-a", '{"headerRows":1}'))
    assert expand_source_table_declarations(root, {}) == {"expanded": 0, "full_form": 0, "absent": 0, "foreign": 1}
    assert root[0].get(TABLE_ATTRIBUTE) == '{"headerRows":1}'


# --- the expanded declaration passes today's extraction and table checks ---------------------------

DRAWN_ROWS = [["지역", "매출(억원)", "비고"], ["서울", "1,200", "본사"], ["부산", "850", "지점\n신규"],
              ["대구", "430", ""]]


def _cell_text(x, y, text, *, anchor="start", header=False):
    style = ('font-size="20" font-weight="bold" fill="#FFFFFF"' if header
             else 'font-size="18" fill="#222222"')
    return (f'<text x="{x}" y="{y}" font-family="Noto Sans KR" {style} text-anchor="{anchor}">{text}</text>')


def _drawn_table(group_id, declaration):
    grid = ('<rect x="0" y="0" width="600" height="60" fill="#1F4E79"/>'
            + "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="240" stroke="#CCCCCC"/>' for x in (200, 400))
            + "".join(f'<line x1="0" y1="{y}" x2="600" y2="{y}" stroke="#CCCCCC"/>' for y in (120, 180))
            + '<line x1="0" y1="60" x2="600" y2="60" stroke="#1F4E79" stroke-width="2" stroke-opacity="0.5"/>'
            + '<rect x="0" y="0" width="600" height="240" fill="none" stroke="#333333" stroke-width="2"/>')
    texts = (_cell_text(100, 38, "지역", anchor="middle", header=True)
             + _cell_text(300, 38, "매출(억원)", anchor="middle", header=True)
             + _cell_text(500, 38, "비고", anchor="middle", header=True)
             + _cell_text(12, 98, "서울") + _cell_text(388, 98, "1,200", anchor="end") + _cell_text(412, 98, "본사")
             + _cell_text(12, 158, "부산") + _cell_text(388, 158, "850", anchor="end")
             + '<text x="412" y="140" font-family="Noto Sans KR" font-size="18" fill="#222222">'
               '<tspan x="412" y="140">지점</tspan><tspan x="412" y="165">신규</tspan></text>'
             + _cell_text(12, 218, "대구") + _cell_text(388, 218, "430", anchor="end"))
    attribute = "" if declaration is None else f" {TABLE_ATTRIBUTE}='{declaration}'"
    return f'<g transform="translate(120,160)"><g id="{group_id}"{attribute}>{grid}{texts}</g></g>'


def _published(root_svg, rows_by_group):
    """Run the inbound steps that follow the expansion, then publication, as the pipeline does."""
    root = ET.fromstring(root_svg)
    if rows_by_group is not None:
        expand_source_table_declarations(root, rows_by_group)
    found = extract_native_declarations(root)
    authored = ET.tostring(root, encoding="unicode")
    payload: dict = {}
    bind_native_declarations(payload, authored, found)
    payload["svg_content"] = finalize_svg(authored, index=0, n=3)
    validate_svg(payload["svg_content"])
    wire, summary = page_native_objects(payload, pt_per_px=0.75)
    return found, payload, wire, summary


def test_expanded_declaration_passes_extraction_and_the_native_table_checks_like_the_full_form():
    cells = [[{"text": text} for text in row] for row in DRAWN_ROWS]
    page = _page(cells)
    facts = _whole_page_facts(page, {})
    rows = source_table_rows_from_facts(facts)
    [group_id] = rows
    assert rows[group_id] == DRAWN_ROWS
    compact_svg = _svg(_drawn_table(group_id, '{"headerRows":1}'))
    full_svg = _svg(_drawn_table(group_id, _full(DRAWN_ROWS).replace("'", "&apos;")))
    found, payload, wire, summary = _published(compact_svg, rows)
    full_found, full_payload, full_wire, full_summary = _published(full_svg, None)
    assert found == {"objects": [{"id": group_id, "kind": "table", "raw": {"rows": DRAWN_ROWS, "headerRows": 1}}],
                     "dropped": []}
    assert summary == {"declared": 1, "accepted": 1, "dropped": []} == full_summary
    assert (found, payload, wire) == (full_found, full_payload, full_wire)
    [entry] = NativeObjects.from_wire(wire).objects
    assert entry.id == group_id and entry.kind == "table"
    assert [[cell["text"] for cell in row] for row in entry.payload["rows"]] == DRAWN_ROWS
    assert entry.payload["headerRows"] == 1
    # Without the expansion the compact value is rejected by the same checks (nothing to fall back on).
    _, _, no_wire, no_summary = _published(compact_svg, None)
    assert no_wire is None and no_summary["accepted"] == 0
    assert no_summary["dropped"] == [{"id": group_id, "kind": "table", "reason": "native_table_json_invalid"}]


# --- rule rewriters --------------------------------------------------------------------------------

def _source_grounded_snapshot():
    snapshot = {"source_authoring_contract": {"version": "svg-source-whole-page/v1"},
                "authoring_profile": ART_DIRECTION_PROFILE_V8, "quality_recovery_policy": QUALITY_RECOVERY_POLICY_V2}
    snapshot["digest"] = canonical_json_digest(dict(snapshot), allow_nan=True)
    return snapshot


COMPACT_SENTENCE = ('For a source table (a source_facts entry of kind table) the group id is its element_id and '
                    'the JSON is only {"headerRows":N} (N = the rows you draw as header); the server fills the '
                    "rows from that table's source cells, so never copy cell text into this attribute. "
                    'For any other table JSON is {"rows":[["Header","Header"],["cell","1,200"]],"headerRows":1}:')


@pytest.mark.parametrize("grounded", [False, True])
def test_native_rule_is_rewritten_for_the_compact_declaration(grounded):
    rules = native_author_rules(source_grounded=grounded)
    rewritten = compact_source_table_rules(rules)
    assert COMPACT_SENTENCE in rewritten
    assert "in the drawing and JSON;" not in rewritten
    assert "Preserve explicit source cell paragraph breaks in the drawing; additional visual" in rewritten
    assert ("Keep the exact rectangular rows" in rules) is grounded
    assert "Keep the exact rectangular rows" not in rewritten
    assert ("For a source table the server writes the exact rectangular rows" in rewritten) is grounded
    # Everything else, the chart rule included, is untouched.
    assert rewritten.endswith(declarations_module.NATIVE_CHART_RULE)
    assert len(rewritten) > len(rules)


def test_rule_rewriter_never_touches_the_rule_constants():
    constants = (declarations_module.NATIVE_TABLE_RULE, declarations_module.NATIVE_CHART_RULE,
                 author_module.SVG_AUTHOR_RULES, author_module.SVG_NEW_COPY_AUTHOR_RULES)
    compact_source_table_rules(native_author_rules(source_grounded=True))
    assert constants == (declarations_module.NATIVE_TABLE_RULE, declarations_module.NATIVE_CHART_RULE,
                         author_module.SVG_AUTHOR_RULES, author_module.SVG_NEW_COPY_AUTHOR_RULES)
    assert all('"headerRows":N' not in constant for constant in constants)
    assert native_author_rules(source_grounded=True) == declarations_module.native_author_rules(source_grounded=True)


OLD_DECLARATION_SENTENCE = (
    "Every explicitly requested table or supported data chart must have its own data-native-table or\n"
    "data-native-chart declaration containing that exact requested data.")
NEW_DECLARATION_SENTENCE = (
    "Every explicitly requested table or supported data chart must have its own data-native-table or\n"
    "data-native-chart declaration. A chart declaration, and the declaration of a table that is not a "
    "source table, contains that exact requested data; for a source table the compact "
    '{"headerRows":N} declaration is that declaration and the server supplies its rows.')
ADDENDUM_OLD = ("Keep the exact rectangular rows and original empty covered cells in JSON; never repeat the "
                "value into the covered cells or add mergedCells to this declaration.")
ADDENDUM_NEW = ("For a source table the server writes the exact rectangular rows and original empty covered "
                "cells, so declare only headerRows; for any other table keep them in JSON. Never repeat the "
                "value into the covered cells or add mergedCells to this declaration.")
CHART_REQUIREMENT_TAIL = (" A chart drawn only as SVG bars\nor labels does not satisfy an editable chart request; "
                          "an unrelated declared chart does not cover it.\n")


@pytest.mark.parametrize("new_copy", [False, True])
@pytest.mark.parametrize("grounded", [False, True])
def test_rewrites_the_real_per_call_rules_exactly_once(grounded, new_copy):
    snapshot = (_source_grounded_snapshot() if grounded
                else {"source_authoring_contract": {"version": "svg-source-whole-page/v1"}})
    rules = author_module.svg_author_rules(snapshot, 14.0, new_copy=new_copy, source_preserved=True)
    assert ("Keep the exact rectangular rows" in rules) is grounded
    assert rules.count(OLD_DECLARATION_SENTENCE) == 1
    rewritten = compact_source_table_rules(rules)
    assert rewritten.count(COMPACT_SENTENCE) == 1
    assert ("Keep the exact rectangular rows" in rewritten) is False
    assert rewritten.count(NEW_DECLARATION_SENTENCE) == 1
    assert OLD_DECLARATION_SENTENCE not in rewritten
    # The requirement for every table or chart stays, and the chart sentences that follow it are untouched.
    assert NEW_DECLARATION_SENTENCE + CHART_REQUIREMENT_TAIL in rewritten
    # Undoing the three edits (four with the addendum) restores the input byte for byte: nothing else moved.
    restored = (rewritten
                .replace(NEW_DECLARATION_SENTENCE, OLD_DECLARATION_SENTENCE)
                .replace("one explicit y per line). " + COMPACT_SENTENCE,
                         'one explicit y per line). JSON is {"rows":[["Header","Header"],["cell","1,200"]],'
                         '"headerRows":1}:')
                .replace("paragraph breaks in the drawing;", "paragraph breaks in the drawing and JSON;")
                .replace(ADDENDUM_NEW, ADDENDUM_OLD))
    assert restored == rules
    assert author_module.svg_author_rules(snapshot, 14.0, new_copy=new_copy, source_preserved=True) == rules


@pytest.mark.parametrize("new_copy", [False, True])
def test_declaration_sentence_is_rewritten_only_where_it_exists(new_copy):
    # A bare native rule string has no ``SVG_AUTHOR_RULES`` sentence: nothing to rewrite, nothing raised.
    bare = compact_source_table_rules(native_author_rules())
    assert "must have its own data-native-table" not in bare
    assert NEW_DECLARATION_SENTENCE not in bare
    # Both constants carry the sentence exactly once and stay untouched by the rewriter.
    for constant in (author_module.SVG_AUTHOR_RULES, author_module.SVG_NEW_COPY_AUTHOR_RULES):
        assert constant.count(OLD_DECLARATION_SENTENCE) == 1
    before = (author_module.SVG_AUTHOR_RULES, author_module.SVG_NEW_COPY_AUTHOR_RULES)
    rules = author_module.svg_author_rules({}, 14.0, new_copy=new_copy)
    compact_source_table_rules(rules)
    assert before == (author_module.SVG_AUTHOR_RULES, author_module.SVG_NEW_COPY_AUTHOR_RULES)


def test_reworded_declaration_sentence_raises_instead_of_leaving_the_contradiction():
    rules = author_module.svg_author_rules({}, 14.0)
    for reworded in (rules.replace("declaration containing that exact requested data.", "declaration of that data."),
                     rules.replace("data-native-table or\ndata-native-chart", "data-native-table or data-native-chart"),
                     rules + OLD_DECLARATION_SENTENCE):
        assert reworded != rules
        with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
            compact_source_table_rules(reworded)
    once = compact_source_table_rules(rules)
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        compact_source_table_rules(once)
    # The declaration edit is not repeatable either: its marker stays in the rewritten text.
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        compact_source_table_rules(native_author_rules() + NEW_DECLARATION_SENTENCE)


@pytest.mark.parametrize("rules", [
    "",
    "no native table rule here",
    native_author_rules().replace("in the drawing and JSON;", "in the drawing;"),
    native_author_rules().replace('JSON is {"rows":', 'JSON is {"cells":'),
])
def test_missing_anchor_raises_instead_of_silently_doing_nothing(rules):
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        compact_source_table_rules(rules)


def test_unique_anchor_is_required_and_a_second_application_is_rejected():
    rules = native_author_rules(source_grounded=True)
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        compact_source_table_rules(rules + rules)
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        compact_source_table_rules(compact_source_table_rules(rules))


def test_source_grounded_addendum_must_be_reworded_together_with_its_marker():
    rules = native_author_rules(source_grounded=True)
    sentence = ("Keep the exact rectangular rows and original empty covered cells in JSON; never repeat the "
                "value into the covered cells or add mergedCells to this declaration.")
    assert rules.count(sentence) == 1
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        compact_source_table_rules(rules.replace(sentence, "Keep the rectangular rows in JSON."))


def test_group_rule_pair_matches_the_source_rules_literal():
    old, new = COMPACT_SOURCE_TABLE_GROUP_RULE
    assert old == "keep table group attributes to its id, with no transform"
    assert new == "keep table group attributes to its id and the compact data-native-table, with no transform"
    source = inspect.getsource(layout_module)
    # Either the literal still says the old words (the integrator applies the pair) or it was rewritten to new.
    assert source.count(old) == 1 or source.count(new) >= 1
    sentence = ("keep table group attributes to its id, with no transform, and put captions outside. ")
    assert (sentence.replace(old, new)
            == "keep table group attributes to its id and the compact data-native-table, with no transform, "
               "and put captions outside. ")
