"""The content structure policy: tables and connectors, stated once for every caller."""

from services.generation_core.authoring import author_slide_svg
from services.generation_core.authoring.author_slide_svg import SVG_AUTHOR_RULES
from utils import content_structure_policy as policy_module
from utils.content_structure_policy import (
    ARROW_RULE, CONNECTOR_RULE, OUTLINE_TABLE_LINE, TABLE_SYNTAX_CLAUSE, TABLE_TEXT_CLAUSE,
    TEMPLATE_TABLE_ID_CLAUSE, StructurePolicy,
    PARTS, active_policy, author_rules, normalize_parts, normalize_scope, outline_table_clause,
    outline_table_line,
)
from utils.llm_calls.generate_presentation_outlines import get_system_prompt
from utils.ppt_writing_prompt import WRITING_POLICY_ID, writing_policy_prompt


def test_the_policy_is_scoped_and_a_deck_outside_the_scope_gets_none():
    assert active_policy(svg=True, template_redesign=True, scope="template") == StructurePolicy()
    assert active_policy(svg=True, template_redesign=False, scope="template") is None
    assert active_policy(svg=False, template_redesign=True, scope="template") is None
    assert active_policy(svg=True, template_redesign=False, scope="svg") == StructurePolicy()
    assert active_policy(svg=False, template_redesign=False, scope="svg") is None
    assert active_policy(svg=True, template_redesign=True, scope="off") is None
    # An unknown or missing scope is the default (off), never "everything".
    assert [normalize_scope(value) for value in (None, "", "bogus", "SVG", " template ")] == [
        "off", "off", "off", "svg", "template"]


def test_the_parts_switch_tables_and_connectors_separately_and_default_to_both():
    # Unset, empty or nothing known: both parts, as the policy was before the split.
    assert [normalize_parts(value) for value in (None, "", " , ", "bogus", ())] == [PARTS] * 5
    assert normalize_parts("connectors") == ("connectors",)
    assert normalize_parts(" Tables ") == ("tables",)
    assert normalize_parts("connectors,tables,bogus") == ("tables", "connectors")
    assert normalize_parts(("connectors",)) == ("connectors",)
    on = dict(svg=True, template_redesign=True, scope="template")
    assert active_policy(**on, parts=None) == StructurePolicy()
    assert active_policy(**on, parts="connectors") == StructurePolicy(tables=False, connectors=True)
    assert active_policy(**on, parts="tables") == StructurePolicy(tables=True, connectors=False)
    # The parts never widen the scope.
    assert active_policy(svg=True, template_redesign=True, scope="off", parts="connectors") is None
    assert active_policy(svg=True, template_redesign=False, scope="template", parts="tables") is None


def test_connectors_only_leaves_the_outline_alone_and_refines_only_the_arrow_rule():
    policy = active_policy(svg=True, template_redesign=True, scope="template", parts="connectors")
    plain = get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean")
    assert get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean",
                             structure_policy=policy) == plain
    refined = author_rules(SVG_AUTHOR_RULES, policy, template_ids=True)
    assert refined == SVG_AUTHOR_RULES.replace(ARROW_RULE, CONNECTOR_RULE, 1)
    tables = author_rules(SVG_AUTHOR_RULES, active_policy(
        svg=True, template_redesign=True, scope="template", parts="tables"), template_ids=True)
    assert tables == SVG_AUTHOR_RULES + TABLE_TEXT_CLAUSE + TEMPLATE_TABLE_ID_CLAUSE


def test_no_policy_leaves_the_outline_prompt_exactly_as_before():
    plain = get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean")
    assert get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean",
                             structure_policy=None) == plain
    assert OUTLINE_TABLE_LINE not in plain and TABLE_SYNTAX_CLAUSE not in plain


def test_the_outline_asks_for_tables_and_the_pipe_rule_stops_forbidding_them():
    policy = StructurePolicy()
    plain = get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean")
    prompt = get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean",
                               structure_policy=policy)
    assert OUTLINE_TABLE_LINE + "   - Must be in Markdown format.\n" in prompt
    # The clause follows the writing policy, which is what forbids pipe separators.
    assert prompt.endswith(writing_policy_prompt(language="Korean") + TABLE_SYNTAX_CLAUSE)
    assert prompt.replace(OUTLINE_TABLE_LINE, "").replace(TABLE_SYNTAX_CLAUSE, "") == plain
    # Without the writing policy there is no pipe rule to qualify.
    assert TABLE_SYNTAX_CLAUSE not in get_system_prompt("text-heavy", True, False, structure_policy=policy)
    assert outline_table_line(None) == "" and outline_table_clause(None) == ""
    assert outline_table_line(StructurePolicy(tables=False)) == ""


def test_the_connector_rule_refines_the_author_rules_and_keeps_the_guard_against_invention():
    assert ARROW_RULE in SVG_AUTHOR_RULES  # a reworded rule must fail here, not silently skip the policy
    assert author_rules(SVG_AUTHOR_RULES, None) == SVG_AUTHOR_RULES
    assert author_rules(SVG_AUTHOR_RULES, StructurePolicy(connectors=False, tables=False)) == SVG_AUTHOR_RULES
    refined = author_rules(SVG_AUTHOR_RULES, StructurePolicy(tables=False))
    assert CONNECTOR_RULE in refined and ARROW_RULE not in refined and TABLE_TEXT_CLAUSE not in refined
    assert "never invent an order, a cause or a hierarchy" in refined
    assert "independent items" in refined  # independent items still get no connectors
    assert refined.replace(CONNECTOR_RULE, ARROW_RULE) == SVG_AUTHOR_RULES
    # Rules that no longer hold the sentence get the rule appended, never dropped.
    assert CONNECTOR_RULE in author_rules("other rules", StructurePolicy(tables=False))


def test_tables_add_the_cell_text_clause_and_a_template_deck_its_body_id_clause():
    plain = author_rules(SVG_AUTHOR_RULES, StructurePolicy())
    assert plain.endswith(TABLE_TEXT_CLAUSE) and TEMPLATE_TABLE_ID_CLAUSE not in plain
    template = author_rules(SVG_AUTHOR_RULES, StructurePolicy(), template_ids=True)
    assert template.endswith(TABLE_TEXT_CLAUSE + TEMPLATE_TABLE_ID_CLAUSE)
    assert "body-native-table-SLUG" in template
    assert author_rules(SVG_AUTHOR_RULES, None, template_ids=True) == SVG_AUTHOR_RULES


def test_author_rules_follow_the_scope_and_the_template_mode(monkeypatch):
    monkeypatch.setattr("services.svg_templates.generation.template_selection",
                        lambda snapshot: {"mode": "template-redesign"} if snapshot.get("svg_template") else None)
    template = {"svg_template": {"anything": True}}
    monkeypatch.delenv("CONTENT_STRUCTURE_POLICY_SCOPE", raising=False)
    # Default scope is off: nothing changes until it is opted into.
    assert author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, template) == SVG_AUTHOR_RULES
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_SCOPE", "template")
    # Scope template: a template-redesign deck only.
    assert CONNECTOR_RULE in author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, template)
    assert author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, {}) == SVG_AUTHOR_RULES
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_SCOPE", "svg")
    assert CONNECTOR_RULE in author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, {})
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_SCOPE", "off")
    assert author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, template) == SVG_AUTHOR_RULES
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_PARTS", "connectors")
    assert author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, template) == SVG_AUTHOR_RULES
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_SCOPE", "template")
    connectors_only = author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, template)
    assert CONNECTOR_RULE in connectors_only and TABLE_TEXT_CLAUSE not in connectors_only
    monkeypatch.setenv("CONTENT_STRUCTURE_POLICY_PARTS", "tables")
    tables_only = author_slide_svg.author_rules_with_structure_policy(SVG_AUTHOR_RULES, template)
    assert ARROW_RULE in tables_only and tables_only.endswith(TABLE_TEXT_CLAUSE + TEMPLATE_TABLE_ID_CLAUSE)


def test_the_policy_module_stays_a_pure_contract():
    source = open(policy_module.__file__, encoding="utf-8").read()
    assert "from services" not in source and "import services" not in source  # utils must not import services
