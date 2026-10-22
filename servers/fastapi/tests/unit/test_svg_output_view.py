"""Source output view orchestration: knob, snapshot pin, fixed inbound order, outbound views.

The stage modules (ids, native-table rows, text defaults) have their own tests; this file covers
``services/svg_author_output_view.py``, which composes them for the producer and every replay.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from services import svg_author_output_view as output_view
from services.svg_author_output_view import (
    OUTPUT_VIEW_VERSION, V1_STAGES, admission_output_view, apply_inbound, outbound_correction_hint,
    outbound_previous_svg, outbound_rules, outbound_source_content, outbound_source_rules,
    output_view_plan, output_view_receipt, parse_author_response, parse_output_view_knob,
    snapshot_output_view,
)
from services.svg_contract import normalize_svg_response

WHOLE_PAGE = {"version": "svg-source-whole-page/v1"}
V1 = {"version": OUTPUT_VIEW_VERSION, "stages": list(V1_STAGES)}
SVG_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"'
CANONICAL_TOKEN = re.compile(r"source-fact-[0-9a-f]{20}|source-native-table-[0-9a-f]{16}")


def source_page(*, image=False):
    from test_svg_source_layout import manifest_for

    blocks = [("text", {"text": "제목 & 요약"}),
              ("table", {"rows": [[{"text": "구분"}, {"text": "값"}], [{"text": "A"}, {"text": "1,200"}]]})]
    if image:
        blocks.append(("image", {"contentDigest": "sha256:original-image"}))
    return manifest_for(blocks)


IMAGE_SLOT = "source-slot-original-image"


def page_responses(source, *, image_slots=None, header_rows=1):
    """(canonical raw, compact raw, facts) of one valid whole-page drawing of ``source``.

    The canonical raw is what a model writes today; the compact raw is the same drawing in the
    v1 output view (aliased ids, compact table declaration, root-declared font family and size).
    """
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.generation_core.authoring.source_id_alias import alias_map

    facts = _whole_page_facts(source.units[0], image_slots or {})
    amap = alias_map(facts)
    text, table = facts[0], facts[1]
    image = facts[2] if len(facts) > 2 else None
    rows = [["" for _ in table["data"]["rows"][0]] for _ in table["data"]["rows"]]
    for cell in table["cells"]:
        rows[cell["row"]][cell["column"]] = cell["text"]

    def draw(compact):
        name = amap.__getitem__ if compact else (lambda value: value)
        family = "" if compact else 'font-family="Inter" '
        size = "" if compact else 'font-size="18" '
        declaration = json.dumps({"headerRows": header_rows} if compact else {"rows": rows, "headerRows": header_rows},
                                 ensure_ascii=False, separators=(",", ":"))
        parts = [SVG_OPEN + (' font-family="Inter" font-size="18">' if compact else ">"),
                 f'<text id="{name(text["element_id"])}" x="40" y="80" {family}font-size="32" '
                 'fill="#111111">제목 &amp; 요약</text>',
                 f"<g id=\"{name(table['element_id'])}\" data-native-table='{declaration}'>"]
        for cell in table["cells"]:
            x, y = 100 + cell["column"] * 120, 200 + cell["row"] * 40
            parts.append(f'<rect x="{x}" y="{y}" width="120" height="40" fill="#FFFFFF" stroke="#111111"/>')
            parts.append(f'<text id="{name(cell["element_id"])}" x="{x + 6}" y="{y + 26}" '
                         f'{family}{size}fill="#111111">{cell["text"]}</text>')
        parts.append("</g>")
        if image is not None:
            parts.append(f'<image id="{name(image["element_id"])}" href="{IMAGE_SLOT}" x="640" y="120" '
                         'width="400" height="400" preserveAspectRatio="xMidYMid meet"/>')
        return "".join(parts) + "</svg>"

    return draw(False), draw(True), facts


def _tree(svg):
    return ET.tostring(ET.fromstring(normalize_svg_response(svg)), encoding="unicode")


# --- knob and pin ---------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "  ", "off", "OFF", "0", "false", "no", "none"])
def test_knob_off_values_pin_nothing(value):
    assert parse_output_view_knob(value) is None


@pytest.mark.parametrize("value,stages", [
    ("v1", list(V1_STAGES)),
    (" V1 ", list(V1_STAGES)),
    ("id_alias", ["id_alias"]),
    ("text_defaults:family_size,id_alias", ["id_alias", "text_defaults:family_size"]),
    ("native_table_rows, id_alias", ["id_alias", "native_table_rows"]),
    ("text_defaults:family", ["text_defaults:family"]),
])
def test_knob_stage_lists_pin_canonical_order(value, stages):
    assert parse_output_view_knob(value) == {"version": OUTPUT_VIEW_VERSION, "stages": stages}


@pytest.mark.parametrize("value", ["v2", "on", "true", "id_alias,", "id_alias,id_alias", "ids",
                                   "text_defaults:family,text_defaults:family_size",
                                   "text_defaults:family_size_fill", "text_defaults"])
def test_knob_typos_fail_loudly(value):
    with pytest.raises(ValueError, match="svg_output_view_knob_invalid"):
        parse_output_view_knob(value)


def test_admission_reads_the_knob_only_for_source_restyle(monkeypatch):
    restyle = SimpleNamespace(source_mode="source_content_restyle")
    monkeypatch.delenv("SVG_SOURCE_OUTPUT_VIEW", raising=False)
    assert admission_output_view(restyle) is None
    monkeypatch.setenv("SVG_SOURCE_OUTPUT_VIEW", "v1")
    assert admission_output_view(restyle) == V1
    for mode in (None, "raster_reconstruction", "source_content_verbatim"):
        assert admission_output_view(SimpleNamespace(source_mode=mode)) is None
    monkeypatch.setenv("SVG_SOURCE_OUTPUT_VIEW", "bogus")
    assert admission_output_view(SimpleNamespace(source_mode=None)) is None
    with pytest.raises(ValueError, match="svg_output_view_knob_invalid"):
        admission_output_view(restyle)


def test_snapshot_pin_is_validated_and_fails_closed():
    snapshot = {"digest": "d", "source_authoring_contract": WHOLE_PAGE}
    assert snapshot_output_view(snapshot) is None
    assert snapshot_output_view(None) is None
    pinned = {**snapshot, "source_output_view": deepcopy(V1)}
    view = snapshot_output_view(pinned)
    assert view == V1
    view["stages"].append("mutated")
    assert pinned["source_output_view"] == V1
    for bad in (None, "v1", {"version": OUTPUT_VIEW_VERSION},
                {"version": "svg-source-output-view/v2", "stages": ["id_alias"]},
                {**V1, "extra": True}, {**V1, "stages": []}, {**V1, "stages": ["id_alias", "id_alias"]},
                {**V1, "stages": ["native_table_rows", "id_alias"]}, {**V1, "stages": ["ids"]},
                {**V1, "stages": ["text_defaults:family", "text_defaults:family_size"]}):
        with pytest.raises(ValueError, match="svg_output_view_unsupported"):
            snapshot_output_view({**snapshot, "source_output_view": bad})
    with pytest.raises(ValueError, match="svg_output_view_scope_invalid"):
        snapshot_output_view({"digest": "d", "source_output_view": deepcopy(V1)})


def test_design_text_never_carries_the_pin():
    from utils.hashing import canonical_json_digest
    from services.generation_core.authoring.author_slide_svg import svg_design_text, svg_prompt_design

    snapshot = {"contract_version": "svg-static/v2", "digest": "d", "references": {},
                "source_authoring_contract": WHOLE_PAGE, "tokens": {"primary": "#123456"}}
    pinned = {**snapshot, "source_output_view": deepcopy(V1)}
    assert svg_design_text(pinned) == svg_design_text(snapshot)
    legacy = {**snapshot, "authoring_profile": {"id": "art-direction-v1", "version": 7}}
    legacy["digest"] = canonical_json_digest({k: v for k, v in legacy.items() if k != "digest"})
    legacy_pinned = {**legacy, "source_output_view": deepcopy(V1)}
    legacy_pinned["digest"] = canonical_json_digest({k: v for k, v in legacy_pinned.items() if k != "digest"})
    assert svg_prompt_design(legacy_pinned) == svg_prompt_design(legacy)


# --- inbound --------------------------------------------------------------------------------------

def test_no_view_is_todays_parse_byte_for_byte():
    canonical, compact, facts = page_responses(source_page())
    for raw in (canonical, compact, "```svg\n" + canonical + "\n```"):
        root, receipt = parse_author_response(raw, view=None, source_facts=None)
        assert receipt is None
        assert ET.tostring(root, encoding="unicode") == _tree(raw)
        before = ET.tostring(root, encoding="unicode")
        assert apply_inbound(root, view=None, source_facts=facts) == {}
        assert ET.tostring(root, encoding="unicode") == before
    assert output_view_plan(None, facts) is None
    for value in ({"source_facts": facts}, "text", None):
        assert outbound_source_content(value, None) is value
    assert outbound_previous_svg(canonical, None) == (canonical, False)
    assert outbound_correction_hint("hint " + facts[0]["element_id"], None) == "hint " + facts[0]["element_id"]
    assert outbound_correction_hint(None, None) is None
    assert outbound_rules("rules", None) == "rules"
    assert outbound_source_rules("rules", None) == "rules"


def test_compact_response_restores_the_canonical_tree_and_counts_each_stage():
    from services.generation_core.authoring.source_id_alias import alias_map, map_digest
    from services.generation_core.drivers.svg.native_declarations import extract_native_declarations

    canonical, compact, facts = page_responses(source_page(image=True), image_slots={"p0:2": IMAGE_SLOT})
    root, receipt = parse_author_response(compact, view=V1, source_facts=facts)
    assert ET.tostring(root, encoding="unicode") == _tree(canonical)
    assert receipt == {"version": OUTPUT_VIEW_VERSION, "stages": list(V1_STAGES),
                       "map_digest": map_digest(alias_map(facts)), "inactive": {},
                       "inbound": {"id_alias": {"aliased": 7, "canonical": 0, "unknown_alias": 0, "part": 0},
                                   "native_table_rows": {"expanded": 1, "full_form": 0, "absent": 0, "foreign": 0},
                                   "text_defaults": {"texts_filled": 5, "containers_stripped": 1}}}
    canonical_root = ET.fromstring(canonical)
    assert extract_native_declarations(root) == extract_native_declarations(canonical_root)
    # The extracted source table group keeps exactly its id, as the binder requires.
    group = next(node for node in root.iter() if node.get("id") == facts[1]["element_id"])
    assert group.attrib == {"id": facts[1]["element_id"]}
    assert ET.tostring(root, encoding="unicode") == ET.tostring(canonical_root, encoding="unicode")


def test_today_verbose_response_under_a_view_is_unchanged():
    canonical, _, facts = page_responses(source_page())
    root, receipt = parse_author_response(canonical, view=V1, source_facts=facts)
    assert ET.tostring(root, encoding="unicode") == _tree(canonical)
    assert receipt["inbound"] == {
        "id_alias": {"aliased": 0, "canonical": 6, "unknown_alias": 0, "part": 0},
        "native_table_rows": {"expanded": 0, "full_form": 1, "absent": 0, "foreign": 0},
        "text_defaults": {"texts_filled": 0, "containers_stripped": 0}}


def test_inbound_order_is_ids_then_tables_then_text_defaults(monkeypatch):
    from services.svg_source_native_tables import expand_source_table_declarations, source_table_rows_from_facts

    _, compact, facts = page_responses(source_page())
    # A table expansion that ran before id canonicalization would see an alias, not its group.
    early = ET.fromstring(compact)
    assert expand_source_table_declarations(early, source_table_rows_from_facts(facts))["foreign"] == 1
    calls = []
    for module, name in (("services.generation_core.authoring.source_id_alias", "canonicalize_ids"),
                         ("services.svg_source_native_tables", "expand_source_table_declarations"),
                         ("services.svg_text_defaults", "expand_text_defaults")):
        real = getattr(__import__(module, fromlist=[name]), name)

        def spy(*args, _real=real, _name=name):
            calls.append(_name)
            return _real(*args)
        monkeypatch.setattr(f"{module}.{name}", spy)
    root = ET.fromstring(compact)
    inbound = apply_inbound(root, view=V1, source_facts=facts)
    assert calls == ["canonicalize_ids", "expand_source_table_declarations", "expand_text_defaults"]
    assert list(inbound) == ["id_alias", "native_table_rows", "text_defaults"]
    assert inbound["native_table_rows"]["expanded"] == 1


def test_partial_stage_lists_apply_only_their_stages():
    canonical, compact, facts = page_responses(source_page())
    ids_only = {"version": OUTPUT_VIEW_VERSION, "stages": ["id_alias"]}
    root, receipt = parse_author_response(compact, view=ids_only, source_facts=facts)
    assert list(receipt["inbound"]) == ["id_alias"]
    assert receipt["stages"] == ["id_alias"]
    group = next(node for node in root.iter() if node.get("id") == facts[1]["element_id"])
    assert group.get("data-native-table") == '{"headerRows":1}'
    assert root.get("font-family") == "Inter"
    tables_and_text = {"version": OUTPUT_VIEW_VERSION, "stages": ["native_table_rows", "text_defaults:family"]}
    plan = output_view_plan(tables_and_text, facts)
    assert plan.alias_map is None and plan.text_props == ("font-family",)
    root, receipt = parse_author_response(canonical, view=tables_and_text, source_facts=facts)
    assert receipt["map_digest"] is None and list(receipt["inbound"]) == ["native_table_rows", "text_defaults"]
    assert ET.tostring(root, encoding="unicode") == _tree(canonical)


def test_a_stage_that_cannot_serve_the_page_stays_canonical():
    canonical, compact, facts = page_responses(source_page())
    duplicated = deepcopy(facts)
    duplicated[1]["cells"][1]["element_id"] = duplicated[1]["cells"][0]["element_id"]
    plan = output_view_plan(V1, duplicated)
    assert plan.alias_map is None and plan.inactive == {"id_alias": "svg_output_view_alias_map_unavailable"}
    root, receipt = parse_author_response(canonical, view=V1, source_facts=duplicated)
    assert receipt["map_digest"] is None and receipt["inbound"]["id_alias"] is None
    assert receipt["inactive"] == {"id_alias": "svg_output_view_alias_map_unavailable"}
    from services.generation_core.authoring.source_id_alias import ALIAS_RULE_SENTENCE

    rules = ("Source element_id values are provenance identities, not layout slots. More. "
             "keep table group attributes to its id, with no transform.")
    assert ALIAS_RULE_SENTENCE not in outbound_source_rules(rules, plan)
    assert outbound_source_content({"source_facts": duplicated}, plan) == {"source_facts": duplicated}

    malformed = deepcopy(facts)
    malformed[1]["cells"][0]["row"] = 9
    plan = output_view_plan(V1, malformed)
    assert plan.table_rows is None
    assert plan.inactive == {"native_table_rows": "svg_output_view_table_facts_invalid"}
    root, receipt = parse_author_response(compact, view=V1, source_facts=malformed)
    assert receipt["inbound"]["native_table_rows"] is None
    group = next(node for node in root.iter() if node.get("id") == facts[1]["element_id"])
    assert group.get("data-native-table") == '{"headerRows":1}'
    assert outbound_source_rules(rules, plan) == rules.replace(
        "Source element_id values are provenance identities, not layout slots.",
        "Source element_id values are provenance identities, not layout slots. " + ALIAS_RULE_SENTENCE)
    table_rules = 'one explicit y per line). JSON is {"rows":[["Header","Header"],["cell","1,200"]],"headerRows":1}:'
    tables_only = output_view_plan({"version": OUTPUT_VIEW_VERSION, "stages": ["native_table_rows"]}, malformed)
    assert outbound_rules(table_rules, tables_only) == table_rules
    with pytest.raises(ValueError, match="svg_output_view_facts_required"):
        output_view_plan(V1, None)


def test_replay_facts_without_image_slots_resolve_the_same_plan():
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts, whole_page_view_facts

    source = source_page(image=True)
    with_slots = _whole_page_facts(source.units[0], {"p0:2": IMAGE_SLOT})
    replay = whole_page_view_facts(source.units[0])
    assert "slot_id" in with_slots[2] and "slot_id" not in replay[2]
    assert output_view_plan(V1, with_slots) == output_view_plan(V1, replay)
    # Facts that went through the prompt's JSON round trip resolve the same plan too.
    assert output_view_plan(V1, json.loads(json.dumps(with_slots, ensure_ascii=False))) == output_view_plan(V1, replay)


# --- outbound -------------------------------------------------------------------------------------

def test_outbound_views_alias_ids_and_compact_previous_svg():
    from services.generation_core.authoring.author_slide_svg import _llm_source_content_view

    canonical, compact, facts = page_responses(source_page())
    plan = output_view_plan(V1, facts)
    content = json.dumps({"source_authoring_contract": WHOLE_PAGE, "source_page_id": "p0",
                          "source_facts": facts}, ensure_ascii=False)
    prompt_value, _ = _llm_source_content_view(content)
    viewed = outbound_source_content(prompt_value, plan)
    assert [fact["element_id"] for fact in viewed["source_facts"]] == ["f1", "t1"]
    assert [cell["element_id"] for cell in viewed["source_facts"][1]["cells"]] == ["c1", "c2", "c3", "c4"]
    assert not CANONICAL_TOKEN.search(json.dumps(viewed, ensure_ascii=False))
    assert prompt_value["source_facts"][0]["element_id"] == facts[0]["element_id"]
    assert not CANONICAL_TOKEN.search(outbound_source_content(content, plan))

    from services.generation_core.drivers.svg.native_declarations import extract_native_declarations

    stored_root = ET.fromstring(canonical)
    extract_native_declarations(stored_root)
    stored = ET.tostring(stored_root, encoding="unicode")  # previous_svg is a stored canonical page
    previous, compacted = outbound_previous_svg(stored, plan)
    assert compacted and not CANONICAL_TOKEN.search(previous)
    assert ET.fromstring(previous).get("font-family") == "Inter"
    # What the model is shown is exactly what inbound restores to the canonical page.
    restored, receipt = parse_author_response(previous, view=V1, source_facts=facts)
    assert ET.tostring(restored, encoding="unicode") == stored
    assert receipt["inbound"]["id_alias"]["aliased"] == 6

    cell_id = facts[1]["cells"][3]["element_id"]
    hint = f"svg_source_receipt_binding_mismatch\n{{\"missing\": [\"{cell_id}\"], \"x\": \"{cell_id}-panel\"}}"
    assert outbound_correction_hint(hint, plan) == hint.replace(f'"{cell_id}"', '"c4"')
    text_only = output_view_plan({"version": OUTPUT_VIEW_VERSION, "stages": ["text_defaults:family_size"]}, facts)
    assert outbound_correction_hint(hint, text_only) == hint
    previous, compacted = outbound_previous_svg(stored, text_only)
    assert compacted and CANONICAL_TOKEN.search(previous)


def test_rule_rewrites_apply_to_the_call_string_once():
    from services.generation_core.authoring import author_slide_svg as author
    from services.svg_text_defaults import TEXT_DEFAULTS_REPAIR_SENTENCE

    _, _, facts = page_responses(source_page())
    plan = output_view_plan(V1, facts)
    style = {"contract_version": author.SVG_CONTRACT_VERSION, "digest": "d", "references": {},
             "source_authoring_contract": WHOLE_PAGE}
    constant = author.SVG_AUTHOR_RULES
    rules = author.svg_author_rules(style, 18, source_preserved=True)
    viewed = outbound_rules(rules, plan)
    assert author.SVG_AUTHOR_RULES == constant
    assert viewed != rules and '{"headerRows":N}' in viewed and 'font-family="F" font-size="S"' in viewed
    assert TEXT_DEFAULTS_REPAIR_SENTENCE not in viewed
    assert outbound_rules(rules, plan, previous_svg_compacted=True).endswith(TEXT_DEFAULTS_REPAIR_SENTENCE + ".\n")
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        outbound_rules(viewed, plan)

    source_rules = ("Compose freely. Source element_id values are provenance identities, not layout slots. "
                    "Place and group content freely. keep table group attributes to its id, with no transform, "
                    "and put captions outside.")
    from services.generation_core.authoring.source_id_alias import ALIAS_RULE_SENTENCE

    viewed_source = outbound_source_rules(source_rules, plan)
    assert ("not layout slots. " + ALIAS_RULE_SENTENCE + " Place and group content freely.") in viewed_source
    assert "its id and the compact data-native-table, with no transform" in viewed_source
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        outbound_source_rules("Compose freely.", plan)


@pytest.mark.parametrize("revision,error_code", [("svg-provider-overload-recovery/v1", "public_provider_overload"),
                                                  ("svg-comment-normalization/v1", "svg_validation_failed")])
def test_legacy_raw_restoring_recoveries_fail_closed_for_a_pinned_job(revision, error_code):
    import asyncio
    from services.svg_checkpoint_recovery import verified_svg_recovery_checkpoints

    raw = "<!-- note -->" + SVG_OPEN + "/>"
    rows = [SimpleNamespace(svg_checkpoint={"attempts": [{"raw_response": raw,
        "validation_error": "svg_contract_declaration_forbidden", "receipt": {}}]})]

    class Session:
        def __init__(self, job):
            self.job = job

        async def scalar(self, _statement):
            return self.job

        async def scalars(self, _statement):
            return rows

    operation = SimpleNamespace(id="op", presentation_id="p", kind="presentation.generate", state="failed",
                                error_code=error_code)
    request = SimpleNamespace(action="retry", recovery_revision=revision)

    def job(snapshot):
        return SimpleNamespace(id="job", state="failed", request_payload={"strategy": "svg"},
                               svg_style_snapshot=snapshot)
    plain = {"digest": "d", "source_authoring_contract": WHOLE_PAGE}
    for snapshot in (plain, None):
        assert asyncio.run(verified_svg_recovery_checkpoints(Session(job(snapshot)), operation, request)) == rows
    for pin in (deepcopy(V1), {"version": "svg-source-output-view/v9", "stages": ["id_alias"]}):
        pinned = job({**plain, "source_output_view": pin})
        assert asyncio.run(verified_svg_recovery_checkpoints(Session(pinned), operation, request)) is None


def test_receipt_entry_shape():
    _, _, facts = page_responses(source_page())
    plan = output_view_plan(V1, facts)
    assert set(output_view_receipt(plan, None)) == {"version", "stages", "map_digest", "inactive", "inbound"}
    assert output_view_receipt(plan, None)["inbound"] is None
    assert output_view.STAGE_ORDER[:2] == ("id_alias", "native_table_rows")
