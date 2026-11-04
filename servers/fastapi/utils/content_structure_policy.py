"""When a deck's pages may show tables and connectors, in one place.

Three separate instructions used to decide this, and they disagreed:

* the outline prompt no longer asks for tables (the line was commented out in the
  2026-04-15 upstream bulk commit ``c7860127f``), and the writing policy
  (``ppt-writing-policy/v1``, 2026-10-13) tells the model to avoid pipe
  separators, which is also how a Markdown table is written. Measured on a real
  12,000-character source: 0 tables with the policy alone, 2 to 3 once pipes
  that structure a table are allowed;
* the SVG author rules say "do not add arrows or connecting lines between
  independent items". That rule exists on purpose (docs/svg-generation-mode-
  design-20260916.md, section 7.1: the model must not invent an order, a cause
  or a hierarchy), but stated alone it also keeps flows and hierarchies the
  source does state from being drawn as flows and hierarchies.

A :class:`StructurePolicy` states both once. ``active_policy`` returns ``None``
unless the deck's mode is in scope (the default scope is ``off``), and every caller
treats ``None`` as "keep today's text exactly", so a deck outside the scope is
byte-identical to before.
"""
from __future__ import annotations

from dataclasses import dataclass

POLICY_ID = "content-structure-policy/v1"

#: Scopes: ``off`` (never), ``template`` (a template-redesign deck), ``svg`` (every svg deck).
#: The default is ``off``: measured live on a real template deck (2026-11-04), a deck with the
#: policy on had every page whose outline held a table (10 of 10, two runs) fall back to a text
#: page, because the native table authoring still fails downstream. It is opt-in until that is fixed.
SCOPES = ("off", "template", "svg")
DEFAULT_SCOPE = "off"

#: Parts a deck in scope gets (``CONTENT_STRUCTURE_POLICY_PARTS``, a comma list): ``tables`` (the outline
#: asks for Markdown tables, the author rules add the table cell and id clauses) and ``connectors`` (the
#: connector rule replaces the "no arrows between independent items" sentence). Unset, empty or naming
#: nothing known: both, which is what the policy meant before the parts could be switched separately.
PARTS = ("tables", "connectors")

#: The sentence of ``SVG_AUTHOR_RULES`` this policy refines (never edited in place:
#: the constant is a durable digest input, so a deck in scope gets it replaced per call).
ARROW_RULE = ("Do not add arrows or connecting lines between independent items. "
              "Only represent relationships and sequence supported by source.")

#: The outline instruction commented out upstream, restored for decks in scope.
OUTLINE_TABLE_LINE = "   - Must have content either in multiple bullet points or table or both.\n"

#: Markdown table syntax is not a decorative pipe separator. The exception is confirmed policy (2026-11-04): see docs/content-structure-policy-20261104.md, "파이프 예외 확정 (2026-11-04)".
TABLE_SYNTAX_CLAUSE = (
    " Markdown table pipes (|) that structure a table, and the table's separator row, are required syntax,"
    " not decorative pipe separators. Keep source tabular data (the source's own tables, key-value lists,"
    " comparisons, schedules) as Markdown tables. Keep a sequence of steps, a flow or a stack of layers as"
    " its list, so that it can be drawn as a diagram, unless the source itself gives it as a table."
    " When the source states one numeric series of at least three labelled values in one shared unit"
    " (for example a headcount per role or an amount per year), also write that series on its own line of"
    " the slide that presents those values, in plain text without backticks, in the form"
    " chart: Label value unit, Label value unit."
    " Use only values the source writes as numbers, with the source labels, values and unit exactly and no"
    " comma inside a label; never compute a value (such as a duration from two dates). Never write a"
    " chart line for values with different units or measures (for example a current-versus-target"
    " comparison of different indicators)."
)

CONNECTOR_RULE = (
    "Use arrows, connector lines, numbered steps, chevrons or layered stacks where the source states a"
    " sequence, flow, hierarchy, dependency or cause between the connected items, and connect exactly the"
    " items the source relates. Do not add arrows or connecting lines between independent items, and never"
    " invent an order, a cause or a hierarchy that the source does not state."
)


#: Measured on a template deck (2026-11-04, 5 of 5 pages whose outline held a table fell back to a text page):
#: the model drew a table's cell texts with only x and y (``svg_contract_explicit_text_attributes_required``)
#: and named the group ``native-table-SLUG``, which the template body check rejects
#: (``svg_template_body_namespace_required``: every authored id starts with ``body-``).
TABLE_TEXT_CLAUSE = (
    " Every text inside a native table, each cell included, carries its own explicit x, y, font-family,"
    " font-size and fill like any other text; a header cell is bold and contrasts with the header fill."
    # Measured 2026-11-04 (native_chart_label_not_drawn, the page fell back): a single-series chart declared
    # a series name it never drew, and after a first clause the model drew the title outside the group.
    " Every text a native chart declares (its title, each category, each series name) is drawn inside the"
    " chart's own group, not beside it: for a single series, declare the exact drawn chart title as the"
    " series name, or draw the series name as a legend label in the group."
    " A page that holds both a native table and a native chart places them side by side or keeps the chart"
    " compact, so that every table row stays inside the body."
)
TEMPLATE_TABLE_ID_CLAUSE = (
    " In this deck every authored id starts with body-, so name a native table or chart group"
    " body-native-table-SLUG or body-native-chart-SLUG."
)


@dataclass(frozen=True)
class StructurePolicy:
    policy_id: str = POLICY_ID
    tables: bool = True
    connectors: bool = True


def normalize_scope(value: str | None) -> str:
    scope = (value or "").strip().lower()
    return scope if scope in SCOPES else DEFAULT_SCOPE


def normalize_parts(value: str | tuple[str, ...] | None) -> tuple[str, ...]:
    """The known parts named in ``value`` (a comma list or names), in :data:`PARTS` order;
    both when it names none of them. Unknown names are ignored."""
    items = value.split(",") if isinstance(value, str) else (value or ())
    named = {item.strip().lower() for item in items}
    return tuple(part for part in PARTS if part in named) or PARTS


def active_policy(*, svg: bool, template_redesign: bool, scope: str | None = None,
                  parts: str | tuple[str, ...] | None = None) -> StructurePolicy | None:
    """The policy for a deck, or ``None`` when its mode is outside ``scope``.
    ``parts`` picks tables and/or connectors (see :data:`PARTS`); unset means both."""
    scope = normalize_scope(scope)
    in_scope = (scope == "svg" and svg) or (scope == "template" and svg and template_redesign)
    if not in_scope:
        return None
    chosen = normalize_parts(parts)
    return StructurePolicy(tables="tables" in chosen, connectors="connectors" in chosen)


def outline_table_line(policy: StructurePolicy | None) -> str:
    """The outline instruction asking for tables, or ``""``."""
    return OUTLINE_TABLE_LINE if policy is not None and policy.tables else ""


def outline_table_clause(policy: StructurePolicy | None) -> str:
    """What to append after the writing policy so its pipe rule does not forbid tables."""
    return TABLE_SYNTAX_CLAUSE if policy is not None and policy.tables else ""


def author_rules(rules: str, policy: StructurePolicy | None, *, template_ids: bool = False) -> str:
    """The author rules with the connector rule refined and, with tables on, the table text
    and id clauses added. Never silent: a rules text that no longer holds the original
    sentence gets the connector rule appended instead. ``template_ids``: the deck's ids must
    start with ``body-`` (a template-redesign deck)."""
    if policy is None:
        return rules
    if policy.connectors:
        rules = rules.replace(ARROW_RULE, CONNECTOR_RULE, 1) if ARROW_RULE in rules else rules + "\n" + CONNECTOR_RULE
    if policy.tables:
        rules += TABLE_TEXT_CLAUSE + (TEMPLATE_TABLE_ID_CLAUSE if template_ids else "")
    return rules
