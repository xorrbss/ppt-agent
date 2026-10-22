"""Page-local source id aliases: map, outbound views, inbound canonicalization, exact round trip."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import pytest

from services.generation_core.authoring.source_id_alias import (
    ALIAS_RE, ALIAS_RULE_SENTENCE, SOURCE_ID_ALIAS_VERSION, alias_facts, alias_map, alias_svg,
    alias_text, canonicalize_ids, map_digest)
from services.svg_contract import SVG_NS, normalize_svg_response

CANONICAL_ID_RE = re.compile(
    r"native-chart-source-fact-[0-9a-f]{20}|source-native-table-[0-9a-f]{16}|source-fact-[0-9a-f]{20}")


def _fact_id(name):
    return "source-fact-" + hashlib.sha256(name.encode()).hexdigest()[:20]


def _table_id(name):
    return "source-native-table-" + hashlib.sha256(name.encode()).hexdigest()[:16]


def _chart_id(name):
    return "native-chart-" + _fact_id(name)


def _canonical_facts():
    """Hand-built facts in the shape `_whole_page_facts` produces (every kind, two tables)."""
    return [
        {"source_id": "p0:0", "kind": "text", "element_id": _fact_id("p0:0"), "data": {"text": "Title"}},
        {"source_id": "p0:1", "kind": "table", "element_id": _table_id("p0:1"), "data": {"rows": []},
         "cells": [{"row": 0, "column": 0, "row_span": 1, "column_span": 2, "text": "A", "element_id": _fact_id("a")},
                   {"row": 1, "column": 0, "row_span": 1, "column_span": 1, "text": "", "element_id": _fact_id("b")},
                   {"row": 1, "column": 1, "row_span": 1, "column_span": 1, "text": "C", "element_id": _fact_id("c")}]},
        {"source_id": "p0:2", "kind": "shape", "element_id": _fact_id("p0:2"), "data": {"preset": "rect"}},
        {"source_id": "p0:3", "kind": "chart", "element_id": _chart_id("p0:3"), "data": {}},
        {"source_id": "p0:4", "kind": "image", "element_id": _fact_id("p0:4"), "data": {}, "slot_id": "source-slot-x"},
        {"source_id": "p0:5", "kind": "table", "element_id": _table_id("p0:5"), "data": {"rows": []},
         "cells": [{"row": 0, "column": 0, "row_span": 1, "column_span": 1, "text": "D", "element_id": _fact_id("d")}]},
        {"source_id": "p0:6", "kind": "text", "element_id": _fact_id("p0:6"), "data": {"text": "Footer"}},
    ]


def test_alias_map_grammar_and_order():
    facts = _canonical_facts()
    amap = alias_map(facts)
    assert list(amap.values()) == ["f1", "t1", "c1", "c2", "c3", "f2", "k1", "f3", "t2", "c4", "f4"]
    assert list(amap) == [facts[0]["element_id"], facts[1]["element_id"], *(c["element_id"] for c in facts[1]["cells"]),
                          facts[2]["element_id"], facts[3]["element_id"], facts[4]["element_id"],
                          facts[5]["element_id"], facts[5]["cells"][0]["element_id"], facts[6]["element_id"]]
    assert all(ALIAS_RE.fullmatch(alias) for alias in amap.values())
    assert not any(ALIAS_RE.fullmatch(canonical) for canonical in amap)
    assert len(set(amap.values())) == len(amap)
    assert ALIAS_RE.pattern == "[fktc][1-9][0-9]*"
    assert not any(ALIAS_RE.fullmatch(text) for text in ("f0", "c07", "F3", "f", "f3-part-1", "x1", "f1 "))


def test_alias_map_counters_run_past_nine_and_the_cell_counter_is_page_global():
    facts = [{"kind": "text", "element_id": _fact_id(f"t{n}")} for n in range(12)]
    facts.append({"kind": "table", "element_id": _table_id("t"),
                  "cells": [{"element_id": _fact_id(f"cell{n}")} for n in range(11)]})
    amap = alias_map(facts)
    assert [amap[fact["element_id"]] for fact in facts[:12]] == [f"f{n}" for n in range(1, 13)]
    assert [amap[cell["element_id"]] for cell in facts[12]["cells"]] == [f"c{n}" for n in range(1, 12)]
    assert alias_map([]) == {}


def test_alias_map_follows_the_real_whole_page_fact_and_cell_order():
    from test_svg_source_layout import manifest_for
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.svg_source_layout import source_pages

    manifest = manifest_for([
        ("text", {"text": "Title", "shapeId": "4"}),
        ("table", {"rows": [[{"text": "Merged", "gridSpan": 2, "sourceCellId": "cell-a"},
                             {"text": "", "hMerge": True, "mergeCovered": True}],
                            [{"text": "B", "sourceCellId": "cell-b"}, {"text": "", "sourceCellId": "cell-c"}]]}),
        ("shape", {"preset": "line", "stCxn": {"id": "4", "idx": "2"}, "tailEnd": {"type": "triangle"}}),
        ("image", {"assetId": "asset", "contentDigest": "sha256:image", "crop": {"left": 0}}),
        ("text", {"text": "Footer"})])
    facts = _whole_page_facts(source_pages(manifest)[0], {"p0:3": "source-slot-image"})
    amap = alias_map(facts)
    table = facts[1]
    assert [fact["kind"] for fact in facts] == ["text", "table", "shape", "image", "text"]
    assert len(table["cells"]) == 3
    table_aliases = [amap[table["element_id"]], *(amap[cell["element_id"]] for cell in table["cells"])]
    assert table_aliases == ["t1", "c1", "c2", "c3"]
    assert [amap[fact["element_id"]] for fact in (facts[0], facts[2], facts[3], facts[4])] == ["f1", "f2", "f3", "f4"]
    assert alias_map(facts) == amap and map_digest(alias_map(facts)) == map_digest(amap)

    view = alias_facts(facts, amap)
    assert view[2]["connections"]["stCxn"]["element_id"] == "f1"
    assert view[2]["connections"]["stCxn"]["source_id"] == facts[0]["source_id"]
    assert not CANONICAL_ID_RE.search(json.dumps(view, ensure_ascii=False))
    assert facts[2]["connections"]["stCxn"]["element_id"] == facts[0]["element_id"]


def test_alias_map_is_none_when_ids_are_not_a_clean_bijection():
    facts = _canonical_facts()
    repeated = deepcopy(facts)
    repeated[6]["element_id"] = repeated[0]["element_id"]
    assert alias_map(repeated) is None
    cell_collides_with_fact = deepcopy(facts)
    cell_collides_with_fact[1]["cells"][0]["element_id"] = facts[0]["element_id"]
    assert alias_map(cell_collides_with_fact) is None
    duplicate_cell = deepcopy(facts)
    duplicate_cell[1]["cells"][1]["element_id"] = duplicate_cell[1]["cells"][0]["element_id"]
    assert alias_map(duplicate_cell) is None
    reserved = deepcopy(facts)
    reserved[0]["element_id"] = "f2"
    assert alias_map(reserved) is None
    missing = deepcopy(facts)
    del missing[2]["element_id"]
    assert alias_map(missing) is None
    assert alias_map([{"kind": "text", "element_id": 7}]) is None
    assert alias_map([None]) is None
    assert alias_map(facts) is not None


def test_map_digest_is_deterministic_and_order_independent():
    amap = alias_map(_canonical_facts())
    digest = map_digest(amap)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    assert map_digest(dict(reversed(list(amap.items())))) == digest
    assert map_digest(alias_map(_canonical_facts())) == digest
    changed = dict(amap)
    first, second = list(changed)[:2]
    changed[first], changed[second] = changed[second], changed[first]
    assert map_digest(changed) != digest
    assert map_digest({}) != digest


def test_constants_pin_the_view_version_and_the_rule_sentence():
    assert SOURCE_ID_ALIAS_VERSION == "svg-source-id-alias/v1"
    assert ALIAS_RULE_SENTENCE.startswith(
        "element_id values are short page-local identities (f, t, c or k followed by a number).")
    assert "never derive, renumber, combine or reuse them" in ALIAS_RULE_SENTENCE
    assert ALIAS_RULE_SENTENCE == ALIAS_RULE_SENTENCE.strip()


def test_alias_facts_is_a_deep_copy_that_aliases_ids_and_connection_targets_only():
    facts = _canonical_facts()
    shape = facts[2]
    shape["data"] = {"preset": "line", "stCxn": {"id": "4", "idx": "2"}, "endCxn": {"id": "9", "idx": "0"}}
    shape["connections"] = {
        "stCxn": {"id": "4", "idx": "2", "source_id": "p0:0", "element_id": facts[0]["element_id"]},
        "endCxn": {"id": "9", "idx": "0"}}
    original = deepcopy(facts)
    amap = alias_map(facts)
    view = alias_facts(facts, amap)

    assert facts == original
    assert view is not facts and all(new is not old for new, old in zip(view, facts, strict=True))
    assert view[2]["data"] is not facts[2]["data"] and view[1]["cells"][0] is not facts[1]["cells"][0]
    assert [fact["element_id"] for fact in view] == ["f1", "t1", "f2", "k1", "f3", "t2", "f4"]
    assert [cell["element_id"] for cell in view[1]["cells"]] == ["c1", "c2", "c3"]
    assert view[5]["cells"][0]["element_id"] == "c4"
    assert view[2]["connections"]["stCxn"] == {"id": "4", "idx": "2", "source_id": "p0:0", "element_id": "f1"}
    assert view[2]["connections"]["endCxn"] == {"id": "9", "idx": "0"}
    # Everything but the ids is untouched, in the same key order.
    for new, old in zip(view, original, strict=True):
        assert list(new) == list(old)
        assert {k: v for k, v in new.items() if k not in ("element_id", "cells", "connections")} == \
            {k: v for k, v in old.items() if k not in ("element_id", "cells", "connections")}
    assert [{k: v for k, v in cell.items() if k != "element_id"} for cell in view[1]["cells"]] == \
        [{k: v for k, v in cell.items() if k != "element_id"} for cell in original[1]["cells"]]
    assert not CANONICAL_ID_RE.search(json.dumps(view, ensure_ascii=False))
    assert alias_facts(facts, amap) == view


def test_alias_facts_leaves_a_connection_target_outside_the_map_alone():
    facts = _canonical_facts()
    facts[2]["connections"] = {"stCxn": {"id": "4", "source_id": "elsewhere", "element_id": _fact_id("elsewhere")}}
    view = alias_facts(facts, alias_map(facts))
    assert view[2]["connections"]["stCxn"]["element_id"] == _fact_id("elsewhere")


def _svg_with_ids(ids):
    body = "".join(f'<rect id="{value}" width="1" height="1"/>' for value in ids)
    return f'<svg xmlns="{SVG_NS}" width="10" height="10">{body}</svg>'


def _ids(svg):
    return [node.get("id") for node in ET.fromstring(svg).iter() if node.get("id") is not None]


def test_alias_svg_renames_only_id_values_including_chart_parts():
    facts = _canonical_facts()
    amap = alias_map(facts)
    chart, table, text = facts[3]["element_id"], facts[1]["element_id"], facts[0]["element_id"]
    canonical = ET.tostring(ET.fromstring(
        f'<svg xmlns="{SVG_NS}" width="10" height="10"><g id="{chart}" data-native-chart="{{}}">'
        f'<g id="{chart}-part-1"/><g id="{chart}-part-12"/></g>'
        f'<g id="{table}"><text id="{facts[1]["cells"][0]["element_id"]}" data-note="{text}">{text}</text></g>'
        f'<text id="{text}" x="1">{text}</text><rect id="svg-system-page-number"/></svg>'), encoding="unicode")
    view = alias_svg(canonical, amap)
    assert _ids(view) == ["k1", "k1-part-1", "k1-part-12", "t1", "c1", "f1", "svg-system-page-number"]
    # Text content and every other attribute keep canonical ids verbatim.
    assert f'data-note="{text}">{text}</text>' in view
    assert alias_svg(view, amap) == view


def test_alias_svg_returns_unparsable_or_non_rewritable_input_unchanged():
    amap = alias_map(_canonical_facts())
    known = _canonical_facts()[0]["element_id"]
    for svg in ("", "not xml", f'<svg xmlns="{SVG_NS}"><rect id="{known}"', f'<svg xmlns="{SVG_NS}"><g></svg>',
                f'<svg xmlns="{SVG_NS}"><!-- {known} --><rect id="{known}"/></svg>',
                f'<?xml version="1.0"?><svg xmlns="{SVG_NS}"><rect id="{known}"/></svg>',
                f'<svg xmlns="{SVG_NS}"><rect id="{known}"/><![CDATA[x]]></svg>'):
        assert alias_svg(svg, amap) is svg
    no_ids = f'<svg xmlns="{SVG_NS}"><rect  width="1"/></svg>'
    assert alias_svg(no_ids, amap) is no_ids
    unknown_only = _svg_with_ids(["svg-system-page-number", "f9", _fact_id("unknown")])
    assert alias_svg(unknown_only, amap) is unknown_only


def test_alias_svg_keeps_derived_decorative_and_alias_shaped_ids():
    amap = alias_map(_canonical_facts())
    known = _canonical_facts()[0]["element_id"]
    decorative = [f"{known}-evaluation", f"{known}.x", "f1-panel", "f9"]
    svg = ET.tostring(ET.fromstring(_svg_with_ids([known, *decorative])), encoding="unicode")
    assert _ids(alias_svg(svg, amap)) == ["f1", *decorative]


def test_alias_text_rewrites_canonical_tokens_only():
    facts = _canonical_facts()
    amap = alias_map(facts)
    text_id, table_id = facts[0]["element_id"], facts[1]["element_id"]
    cell_id, chart_id = facts[1]["cells"][0]["element_id"], facts[3]["element_id"]
    hint = (f'svg_source_receipt_binding_mismatch\n{{"missing": [{{"element_id": "{text_id}"}}], '
            f'"table": "{table_id}", "cells": ["{cell_id}",\'{facts[1]["cells"][1]["element_id"]}\'], '
            f'"chart": "{chart_id}-part-3 {chart_id}", "again": {text_id}}}')
    rewritten = alias_text(hint, amap)
    assert rewritten == (
        'svg_source_receipt_binding_mismatch\n{"missing": [{"element_id": "f1"}], "table": "t1", '
        '"cells": ["c1",\'c2\'], "chart": "k1-part-3 k1", "again": f1}')
    assert not CANONICAL_ID_RE.search(rewritten)
    assert alias_text("", amap) == ""
    assert alias_text("no ids here, f1 c2 k3", amap) == "no ids here, f1 c2 k3"


def test_alias_text_leaves_unknown_tokens_derived_ids_and_longer_tokens_alone():
    amap = alias_map(_canonical_facts())
    known = _canonical_facts()[0]["element_id"]
    unknown = _fact_id("unknown")
    untouched = " ".join([
        unknown, _table_id("unknown"), _chart_id("unknown") + "-part-2", f"{known}-evaluation", f"{known}-panel",
        f"{known}.x", f"{known}_x", f"x{known}", f"{known}0", known + "ab", f"prefix.{known}"])
    assert alias_text(untouched, amap) == untouched
    assert alias_text(f"{known}-evaluation and {known}", amap) == f"{known}-evaluation and f1"


def test_canonicalize_ids_restores_aliases_and_counts_every_form():
    facts = _canonical_facts()
    amap = alias_map(facts)
    chart = facts[3]["element_id"]
    root = ET.fromstring(_svg_with_ids([
        "f1", "t1", "c1", facts[2]["element_id"], "k1-part-2", "f99", "svg-system-page-number",
        f"{facts[0]['element_id']}-panel"]))
    stats = canonicalize_ids(root, amap)
    assert stats == {"aliased": 3, "canonical": 1, "unknown_alias": 1, "part": 1}
    assert [node.get("id") for node in root.iter()][1:] == [
        facts[0]["element_id"], facts[1]["element_id"], facts[1]["cells"][0]["element_id"], facts[2]["element_id"],
        f"{chart}-part-2", "f99", "svg-system-page-number", f"{facts[0]['element_id']}-panel"]
    assert canonicalize_ids(root, amap) == {"aliased": 0, "canonical": 4, "unknown_alias": 1, "part": 0}


@pytest.mark.parametrize("unknown", ["c999", "f0", "F3", "c07", "f", "x1", "f1x", " f1", "f1 ", "t3", "k2", "c5"])
def test_unknown_or_malformed_aliases_pass_through_unchanged(unknown):
    amap = alias_map(_canonical_facts())
    root = ET.fromstring(_svg_with_ids([unknown]))
    stats = canonicalize_ids(root, amap)
    assert root[0].get("id") == unknown
    assert stats["aliased"] == 0 and stats["part"] == 0
    assert stats["unknown_alias"] == (1 if ALIAS_RE.fullmatch(unknown) else 0)


def test_unknown_chart_part_passes_through():
    amap = alias_map(_canonical_facts())
    root = ET.fromstring(_svg_with_ids(["k2-part-1", "k1-part-", "k1-part-x", "f1-part-1", "K1-part-1"]))
    assert canonicalize_ids(root, amap) == {"aliased": 0, "canonical": 0, "unknown_alias": 0, "part": 0}
    assert [node.get("id") for node in root] == ["k2-part-1", "k1-part-", "k1-part-x", "f1-part-1", "K1-part-1"]


def test_duplicate_alias_and_alias_plus_canonical_both_become_the_same_canonical_id():
    facts = _canonical_facts()
    amap = alias_map(facts)
    text = facts[0]["element_id"]
    root = ET.fromstring(_svg_with_ids(["f1", "f1", text]))
    assert canonicalize_ids(root, amap) == {"aliased": 2, "canonical": 1, "unknown_alias": 0, "part": 0}
    assert [node.get("id") for node in root] == [text, text, text]


def test_alias_on_the_wrong_element_is_still_renamed():
    facts = _canonical_facts()
    amap = alias_map(facts)
    root = ET.fromstring(f'<svg xmlns="{SVG_NS}"><rect id="c1"/><tspan id="f1"/><image id="t1"/><text id="k1"/></svg>')
    canonicalize_ids(root, amap)
    assert [(node.tag.rsplit("}", 1)[1], node.get("id")) for node in root] == [
        ("rect", facts[1]["cells"][0]["element_id"]), ("tspan", facts[0]["element_id"]),
        ("image", facts[1]["element_id"]), ("text", facts[3]["element_id"])]


def test_decorative_ids_are_untouched_and_a_reserved_alias_shaped_one_is_renamed():
    facts = _canonical_facts()
    amap = alias_map(facts)
    derived = [f"{facts[0]['element_id']}-evaluation", "f1-panel", "svg-system-header", "title", ""]
    root = ET.fromstring(_svg_with_ids(derived))
    assert canonicalize_ids(root, amap) == {"aliased": 0, "canonical": 0, "unknown_alias": 0, "part": 0}
    assert [node.get("id") for node in root] == derived
    # A decorative id that equals a live alias is indistinguishable from it: renamed, then rejected by the binder.
    reserved = ET.fromstring(_svg_with_ids(["t1"]))
    canonicalize_ids(reserved, amap)
    assert reserved[0].get("id") == facts[1]["element_id"]


def test_canonicalize_ids_never_raises_on_odd_trees_and_only_touches_id_values():
    amap = alias_map(_canonical_facts())
    root = ET.fromstring(
        f'<svg xmlns="{SVG_NS}" id="f1" data-k="f1"><g><!-- f1 --><text data-native-table=\'{{"a":"f1"}}\'>f1</text>'
        f'<?pi f1?></g></svg>')
    before = ET.tostring(root, encoding="unicode")
    stats = canonicalize_ids(root, amap)
    after = ET.tostring(root, encoding="unicode")
    assert stats["aliased"] == 1
    assert after == before.replace('id="f1"', f'id="{_canonical_facts()[0]["element_id"]}"', 1)
    assert canonicalize_ids(ET.fromstring(f'<svg xmlns="{SVG_NS}"/>'), amap) == {
        "aliased": 0, "canonical": 0, "unknown_alias": 0, "part": 0}
    assert canonicalize_ids(ET.fromstring(f'<svg xmlns="{SVG_NS}"><g id="f1"/></svg>'), {}) == {
        "aliased": 0, "canonical": 0, "unknown_alias": 1, "part": 0}


def _realistic_page():
    """Real `_whole_page_facts` (text, merged/empty-cell table, connector, image) plus a chart fact."""
    from test_svg_source_layout import manifest_for
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.svg_source_layout import source_pages

    manifest = manifest_for([
        ("text", {"text": "분기 실적 & <요약>", "shapeId": "4"}),
        ("table", {"rows": [[{"text": "매출 100", "gridSpan": 2, "sourceCellId": "cell-a"},
                             {"text": "", "hMerge": True, "mergeCovered": True}],
                            [{"text": "A&B", "sourceCellId": "cell-b"}, {"text": "", "sourceCellId": "cell-c"}]]}),
        ("shape", {"preset": "line", "stCxn": {"id": "4", "idx": "2"}, "tailEnd": {"type": "triangle"}}),
        ("image", {"assetId": "asset", "contentDigest": "sha256:image", "crop": {"left": 0}}),
        ("text", {"text": "Footer"})])
    facts = _whole_page_facts(source_pages(manifest)[0], {"p0:3": "source-slot-image"})
    facts.insert(3, {"source_id": "p0:9", "kind": "chart", "element_id": _chart_id("p0:9"), "data": {}})
    return facts


def _canonical_page_svg(facts):
    parts = [f'<svg xmlns="{SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">',
             '<rect id="svg-system-background" width="1280" height="720" fill="#fff"/>']
    for number, fact in enumerate(facts):
        identity = fact["element_id"]
        if fact["kind"] == "table":
            parts.append(f'<g id="{identity}">')
            for cell in fact["cells"]:
                parts.append(f'<rect x="{number}" y="{cell["row"]}" width="10" height="10"/>')
                parts.append(f'<text id="{cell["element_id"]}" x="{number}" y="{cell["row"]}" font-size="20">'
                             f'{escape(cell["text"])}</text>')
            parts.append("</g>")
        elif fact["kind"] == "chart":
            parts.append(f'<g id="{identity}" data-native-chart=\'{{"chartType":"bar"}}\'>'
                         f'<g id="{identity}-part-1"><rect width="4" height="4"/></g>'
                         f'<g id="{identity}-part-2"><rect width="5" height="5"/></g></g>')
        elif fact["kind"] == "image":
            parts.append(f'<image id="{identity}" href="{fact["slot_id"]}" width="10" height="10"/>')
        elif fact["kind"] == "text":
            text = escape(fact["data"]["text"])
            parts.append(f'<rect id="{identity}-panel" width="9" height="9"/>'
                         f'<text id="{identity}" x="{number}" y="9" font-size="24">{text}</text>')
        else:
            parts.append(f'<line id="{identity}" x1="0" y1="0" x2="{number}" y2="9"/>')
    parts.append('<text id="svg-system-page-number" x="1" y="1">1</text></svg>')
    return ET.tostring(ET.fromstring("".join(parts)), encoding="unicode")


def test_round_trip_is_byte_exact_on_a_realistic_page():
    facts = _realistic_page()
    amap = alias_map(facts)
    assert amap is not None and list(amap.values())[:7] == ["f1", "t1", "c1", "c2", "c3", "f2", "k1"]
    canonical = _canonical_page_svg(facts)
    assert "native-chart-source-fact-" in canonical and "-part-2" in canonical

    view = alias_svg(canonical, amap)
    assert view != canonical and len(view) < len(canonical)
    view_ids = _ids(view)
    assert not [value for value in view_ids if CANONICAL_ID_RE.fullmatch(value)]
    assert view_ids[0] == "svg-system-background" and view_ids[-1] == "svg-system-page-number"
    assert f"{facts[0]['element_id']}-panel" in view_ids  # derived decorative ids stay as written

    root = ET.fromstring(normalize_svg_response(view))
    stats = canonicalize_ids(root, amap)
    assert ET.tostring(root, encoding="unicode") == canonical
    # Every fact, table and cell id was emitted once, plus the two server-stamped chart parts.
    assert stats == {"aliased": len(amap), "canonical": 0, "unknown_alias": 0, "part": 2}

    # The facts view and the SVG view use the same aliases, so a model copying one onto the other round-trips.
    facts_view_ids = {fact["element_id"] for fact in alias_facts(facts, amap)}
    assert facts_view_ids <= set(view_ids)


def test_round_trip_survives_the_model_mixing_forms_and_the_view_is_idempotent():
    facts = _realistic_page()
    amap = alias_map(facts)
    canonical = _canonical_page_svg(facts)
    view = alias_svg(canonical, amap)
    assert alias_svg(view, amap) == view
    mixed = view.replace('id="f1"', f'id="{facts[0]["element_id"]}"', 1)
    assert mixed != view
    root = ET.fromstring(mixed)
    stats = canonicalize_ids(root, amap)
    assert ET.tostring(root, encoding="unicode") == canonical and stats["canonical"] == 1
    # Aliasing the canonical page twice (with a fresh parse each time) is stable as well.
    assert alias_svg(canonical, amap) == view
