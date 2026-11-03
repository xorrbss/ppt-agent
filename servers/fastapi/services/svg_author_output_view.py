"""Source output view: the compact form the whole-page source SVG author reads and writes.

The stored page contract (``svg_content``, ``nativeDeclarations``, ``source_preservation``,
validation, binding, render and export) never changes. Only the model's view of it is shorter:

* ``id_alias`` - 20-hex provenance ids become page-local aliases
  (``services/generation_core/authoring/source_id_alias.py``);
* ``native_table_rows`` - a source table group declares only ``{"headerRows":N}`` and the server
  fills its rows from the source cells (helpers in ``services/svg_source_native_tables.py``);
* ``text_defaults:<tier>`` - the shared text style is declared once on the root ``<svg>``
  (``services/svg_text_defaults.py``); ``family`` is T0, ``family_size`` is T1.

A job pins its view once, at admission, in the frozen style snapshot under
``source_output_view``; every producer and every replay of a stored raw response reads that pin,
so a resumed or recovered episode keeps the view its responses were written in. Snapshots without
the key are today's behaviour, byte for byte. An unknown version fails closed
(``svg_output_view_unsupported``): a replay never guesses how a response was written.

Inbound order is fixed: ids -> native-table rows -> text defaults, then today's
``extract_native_declarations`` and everything after it. Table expansion matches canonical group
ids, so it runs after id canonicalization; text-default expansion strips container ``font-*`` so
an extracted source table group keeps its ``{"id"}``-only attributes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import xml.etree.ElementTree as ET

OUTPUT_VIEW_VERSION = "svg-source-output-view/v1"
OUTPUT_VIEW_SNAPSHOT_KEY = "source_output_view"
WHOLE_PAGE_CONTRACT = {"version": "svg-source-whole-page/v1"}

STAGE_ID_ALIAS = "id_alias"
STAGE_NATIVE_TABLE_ROWS = "native_table_rows"
STAGE_TEXT_DEFAULTS_FAMILY = "text_defaults:family"
STAGE_TEXT_DEFAULTS_FAMILY_SIZE = "text_defaults:family_size"
# Canonical stage order; it is also the fixed inbound application order.
STAGE_ORDER = (STAGE_ID_ALIAS, STAGE_NATIVE_TABLE_ROWS,
               STAGE_TEXT_DEFAULTS_FAMILY, STAGE_TEXT_DEFAULTS_FAMILY_SIZE)
V1_STAGES = (STAGE_ID_ALIAS, STAGE_NATIVE_TABLE_ROWS, STAGE_TEXT_DEFAULTS_FAMILY_SIZE)

_KNOB_OFF = frozenset({"", "off", "0", "false", "no", "none"})
# Anchor of the alias rule sentence in ``_author_source_whole_page``'s source rules.
_ALIAS_RULE_ANCHOR = "Source element_id values are provenance identities, not layout slots."


def _text_props(stage: str) -> tuple[str, ...]:
    from services.svg_text_defaults import TEXT_DEFAULT_PROPS_T0, TEXT_DEFAULT_PROPS_T1

    return {STAGE_TEXT_DEFAULTS_FAMILY: TEXT_DEFAULT_PROPS_T0,
            STAGE_TEXT_DEFAULTS_FAMILY_SIZE: TEXT_DEFAULT_PROPS_T1}[stage]


def _view(stages) -> dict:
    return {"version": OUTPUT_VIEW_VERSION, "stages": list(stages)}


def _valid_stages(stages) -> bool:
    return (isinstance(stages, list) and bool(stages)
            and all(isinstance(stage, str) and stage in STAGE_ORDER for stage in stages)
            and len(set(stages)) == len(stages)
            and stages == sorted(stages, key=STAGE_ORDER.index)
            and sum(stage.startswith("text_defaults:") for stage in stages) <= 1)


def parse_output_view_knob(value: str | None) -> dict | None:
    """``SVG_SOURCE_OUTPUT_VIEW`` text -> the view to pin, or ``None`` when off.

    Off: unset, blank, ``off``/``0``/``false``/``no``/``none``. ``v1``: every v1 stage, text
    defaults at T1. Otherwise a comma-separated stage list (A/B arms), e.g.
    ``id_alias,native_table_rows``; it is stored in canonical order. Any other text raises
    ``ValueError("svg_output_view_knob_invalid")``: a typo must never silently run another arm.
    """
    text = (value or "").strip().lower()
    if text in _KNOB_OFF:
        return None
    if text == "v1":
        return _view(V1_STAGES)
    tokens = [token.strip() for token in text.split(",")]
    if not all(token in STAGE_ORDER for token in tokens) or len(set(tokens)) != len(tokens):
        raise ValueError("svg_output_view_knob_invalid")
    stages = sorted(tokens, key=STAGE_ORDER.index)
    if not _valid_stages(stages):
        raise ValueError("svg_output_view_knob_invalid")
    return _view(stages)


def admission_output_view(request) -> dict | None:
    """The view a fresh admission pins in the frozen snapshot, or ``None``.

    Only whole-page source restyle authoring has a source output view; the knob is read here
    and nowhere else, so flipping it affects new jobs only.
    """
    if getattr(request, "source_mode", None) != "source_content_restyle":
        return None
    from utils.get_env import get_svg_source_output_view_env

    return parse_output_view_knob(get_svg_source_output_view_env())


def snapshot_output_view(snapshot) -> dict | None:
    """The validated view pinned in a frozen style snapshot; ``None`` when none is pinned.

    Raises ``ValueError("svg_output_view_unsupported")`` for any pin this code cannot interpret
    (unknown version, unknown or misordered stage, extra keys) and
    ``ValueError("svg_output_view_scope_invalid")`` when the snapshot is not a whole-page source
    snapshot. Callers fail closed on either.
    """
    if not isinstance(snapshot, dict) or OUTPUT_VIEW_SNAPSHOT_KEY not in snapshot:
        return None
    value = snapshot[OUTPUT_VIEW_SNAPSHOT_KEY]
    if (not isinstance(value, dict) or set(value) != {"version", "stages"}
            or value["version"] != OUTPUT_VIEW_VERSION or not _valid_stages(value["stages"])):
        raise ValueError("svg_output_view_unsupported")
    if snapshot.get("source_authoring_contract") != WHOLE_PAGE_CONTRACT:
        raise ValueError("svg_output_view_scope_invalid")
    return _view(value["stages"])


@dataclass(frozen=True)
class OutputViewPlan:
    """One page's effective view: what each pinned stage does for these source facts."""

    view: dict
    # Canonical id -> alias; ``None`` when the stage is not pinned or is inactive for this page.
    alias_map: dict | None
    # Canonical source table group id -> declaration rows; ``None`` when not pinned or inactive.
    table_rows: dict | None
    # Text-default properties; ``None`` when the stage is not pinned.
    text_props: tuple | None
    # Pinned stages this page cannot use, with the reason; such a stage keeps today's canonical
    # form in both directions (rules, prompt values and inbound) for this page only.
    inactive: dict = field(default_factory=dict)

    @property
    def stages(self) -> list:
        return self.view["stages"]


def output_view_plan(view, source_facts) -> OutputViewPlan | None:
    """Resolve a pinned view against the page's canonical ``source_facts`` (``None`` when off).

    Deterministic in its inputs, so the producer and every replay derive the same plan. A stage
    that cannot serve this page is inactive rather than failing the call: ``id_alias`` when the
    page's ids are not a clean bijection, ``native_table_rows`` when a server-built table fact is
    structurally invalid.
    """
    if view is None:
        return None
    if not isinstance(source_facts, list):
        raise ValueError("svg_output_view_facts_required")
    stages = view["stages"]
    amap = table_rows = props = None
    inactive = {}
    if STAGE_ID_ALIAS in stages:
        from services.generation_core.authoring.source_id_alias import alias_map

        amap = alias_map(source_facts)
        if amap is None:
            inactive[STAGE_ID_ALIAS] = "svg_output_view_alias_map_unavailable"
    if STAGE_NATIVE_TABLE_ROWS in stages:
        from services.svg_source_native_tables import source_table_rows_from_facts

        try:
            table_rows = source_table_rows_from_facts(source_facts)
        except ValueError as error:
            inactive[STAGE_NATIVE_TABLE_ROWS] = str(error)
    for stage in stages:
        if stage.startswith("text_defaults:"):
            props = _text_props(stage)
    return OutputViewPlan(view=view, alias_map=amap, table_rows=table_rows, text_props=props,
                          inactive=inactive)


# --- inbound ------------------------------------------------------------------------------------

def apply_plan(root, plan: OutputViewPlan) -> dict:
    """:func:`apply_inbound` for an already resolved plan (the producer resolves it once)."""
    inbound = {}
    if STAGE_ID_ALIAS in plan.stages:
        from services.generation_core.authoring.source_id_alias import canonicalize_ids

        inbound["id_alias"] = (canonicalize_ids(root, plan.alias_map)
                               if plan.alias_map is not None else None)
    if STAGE_NATIVE_TABLE_ROWS in plan.stages:
        from services.svg_source_native_tables import expand_source_table_declarations

        inbound["native_table_rows"] = (expand_source_table_declarations(root, plan.table_rows)
                                        if plan.table_rows is not None else None)
    if plan.text_props is not None:
        from services.svg_text_defaults import expand_text_defaults

        inbound["text_defaults"] = expand_text_defaults(root)
    return inbound


def apply_inbound(root, *, view, source_facts) -> dict:
    """Restore the canonical tree from a parsed compact response, in place.

    Runs ids -> native-table rows -> text defaults and returns the per-stage counters
    (``{}`` when ``view`` is ``None``). Never raises on model output; a mistake the model made
    passes through to today's validator and binder.
    """
    plan = output_view_plan(view, source_facts)
    return {} if plan is None else apply_plan(root, plan)


def output_view_receipt(plan: OutputViewPlan, inbound: dict | None) -> dict:
    """The attempt receipt's ``output_view`` entry.

    ``inbound`` holds the per-stage counters (a pinned stage inactive for this page has ``None``)
    and is ``None`` itself when the response never parsed; ``inactive`` names each inactive stage
    with its reason.
    """
    from services.generation_core.authoring.source_id_alias import map_digest

    return {"version": plan.view["version"], "stages": list(plan.stages),
            "map_digest": map_digest(plan.alias_map) if plan.alias_map is not None else None,
            "inactive": dict(plan.inactive), "inbound": inbound}


def parse_author_response(raw, *, view, source_facts):
    """Re-derive the author tree from a stored raw response exactly as the producer did.

    ``normalize_svg_response`` -> ``ET.fromstring`` -> container font attributes onto texts
    (unless a text-defaults stage owns them) -> :func:`apply_inbound`. Returns
    ``(root, receipt)`` where ``receipt`` is the ``output_view`` receipt entry, or ``None`` when no
    view is pinned (then the tree is today's ``ET.fromstring(normalize_svg_response(raw))``).
    ``ET.ParseError`` propagates, as it does at every caller today.
    """
    from services.svg_contract import normalize_svg_response

    from services.svg_text_defaults import push_down_container_font_attributes

    plan = output_view_plan(view, source_facts)
    root = ET.fromstring(normalize_svg_response(raw))
    if plan is None or plan.text_props is None:
        # The producer restores container font attributes the same way.
        push_down_container_font_attributes(root)
    if plan is None:
        return root, None
    return root, output_view_receipt(plan, apply_plan(root, plan))


# --- outbound -----------------------------------------------------------------------------------

def outbound_source_content(prompt_source, plan: OutputViewPlan | None):
    """The ``source_content`` prompt value with fact, cell and connection-target ids aliased."""
    if plan is None or plan.alias_map is None:
        return prompt_source
    from services.generation_core.authoring.source_id_alias import alias_facts, alias_text

    if isinstance(prompt_source, dict) and isinstance(prompt_source.get("source_facts"), list):
        try:
            facts = alias_facts(prompt_source["source_facts"], plan.alias_map)
        except KeyError as error:
            raise ValueError("svg_output_view_facts_mismatch") from error
        return {**prompt_source, "source_facts": facts}
    if isinstance(prompt_source, str):
        # The structured view was not available; the JSON text carries the ids as exact tokens.
        return alias_text(prompt_source, plan.alias_map)
    return prompt_source


def outbound_previous_svg(previous_svg, plan: OutputViewPlan | None) -> tuple[str, bool]:
    """``previous_svg`` as the model should see it, and whether its text style is root-declared.

    Text defaults are compacted first (on canonical ids), then ids are aliased. Both helpers fail
    open: an SVG they cannot rewrite exactly is shown unchanged.
    """
    if plan is None or not isinstance(previous_svg, str):
        return previous_svg, False
    view, compacted = previous_svg, False
    if plan.text_props is not None:
        from services.svg_text_defaults import compact_text_defaults

        view = compact_text_defaults(view, plan.text_props)
        compacted = view != previous_svg
    if plan.alias_map is not None:
        from services.generation_core.authoring.source_id_alias import alias_svg

        view = alias_svg(view, plan.alias_map)
    return view, compacted


def outbound_correction_hint(hint, plan: OutputViewPlan | None):
    """The correction text at the single point it enters the prompt, canonical ids aliased."""
    if plan is None or plan.alias_map is None or not isinstance(hint, str) or not hint:
        return hint
    from services.generation_core.authoring.source_id_alias import alias_text

    return alias_text(hint, plan.alias_map)


def outbound_rules(rules: str, plan: OutputViewPlan | None, *, previous_svg_compacted: bool = False) -> str:
    """The per-call svg author rules for this view (never the ``SVG_AUTHOR_RULES`` constant)."""
    if plan is None:
        return rules
    if plan.table_rows is not None:
        from services.svg_source_native_tables import compact_source_table_rules

        rules = compact_source_table_rules(rules)
    if plan.text_props is not None:
        from services.svg_text_defaults import TEXT_DEFAULTS_REPAIR_SENTENCE, text_defaults_rules

        rules = text_defaults_rules(rules, plan.text_props)
        if previous_svg_compacted:
            rules += "\n" + TEXT_DEFAULTS_REPAIR_SENTENCE + ".\n"
    return rules


def outbound_source_rules(source_rules: str, plan: OutputViewPlan | None) -> str:
    """``_author_source_whole_page``'s source rules for this view.

    The alias sentence follows "...not layout slots." only when this page's ids are aliased; the
    table group sentence admits the compact declaration only when that stage is pinned. A missing
    anchor raises ``ValueError("svg_output_view_rule_anchor_missing")``.
    """
    if plan is None:
        return source_rules
    if plan.alias_map is not None:
        from services.generation_core.authoring.source_id_alias import ALIAS_RULE_SENTENCE

        if source_rules.count(_ALIAS_RULE_ANCHOR) != 1:
            raise ValueError("svg_output_view_rule_anchor_missing")
        source_rules = source_rules.replace(_ALIAS_RULE_ANCHOR,
                                            _ALIAS_RULE_ANCHOR + " " + ALIAS_RULE_SENTENCE)
    if plan.table_rows is not None:
        from services.svg_source_native_tables import COMPACT_SOURCE_TABLE_GROUP_RULE

        old, new = COMPACT_SOURCE_TABLE_GROUP_RULE
        if source_rules.count(old) != 1:
            raise ValueError("svg_output_view_rule_anchor_missing")
        source_rules = source_rules.replace(old, new)
    return source_rules
