"""Root-declared text style for SVG author output (services/svg_text_defaults.py).

Ports the 16 checks of the attr-inherit prototype (design/attr-inherit/test_text_defaults_proto.py) and
adds the properties the pipeline relies on: idempotence, no-op on today's explicit form, compaction ->
expansion identity, the real validator, the source table group invariant, classification coverage and the
rule rewriter. The stored-page corpus of the prototype is replaced by seeded synthetic canonical pages.
"""
from __future__ import annotations

import random
import xml.etree.ElementTree as ET

import pytest

from services.generation_core.authoring import author_slide_svg as author
from services.svg_contract import SVG_NS, source_digest, validate_svg
from services.svg_text_defaults import (
    TEXT_DEFAULT_PROPS_T0,
    TEXT_DEFAULT_PROPS_T1,
    TEXT_DEFAULT_PROPS_T2,
    TEXT_DEFAULTS_REPAIR_SENTENCE,
    TEXT_DEFAULTS_VERSION,
    compact_text_defaults,
    expand_text_defaults,
    text_defaults_rules,
)

OPEN = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"%s>'
USUAL_ORDER = ("id", "x", "y", "font-family", "font-size", "font-weight", "fill", "text-anchor")
ALL_PROPS = (TEXT_DEFAULT_PROPS_T0, TEXT_DEFAULT_PROPS_T1, TEXT_DEFAULT_PROPS_T2)


def page(inner, root_attrs=""):
    return (OPEN % root_attrs) + inner + "</svg>"


def expanded(svg):
    """What the pipeline does with a response: parse, expand, serialize."""
    root = ET.fromstring(svg)
    counters = expand_text_defaults(root)
    return ET.tostring(root, encoding="unicode"), counters


def verdict(svg):
    try:
        validate_svg(svg)
        return "ok"
    except ValueError as error:
        return str(error).split(":")[0]


def elements(root, tag):
    return [node for node in root.iter() if node.tag == "{%s}%s" % (SVG_NS, tag)]


def canonical(svg):
    """The serialization the pipeline stores (ET round trip of the response)."""
    return ET.tostring(ET.fromstring(svg), encoding="unicode")


def signature(svg_or_root):
    """Tree identity that ignores attribute order only."""
    root = ET.fromstring(svg_or_root) if isinstance(svg_or_root, str) else svg_or_root
    out, stack = [], [root]
    while stack:
        node = stack.pop()
        out.append((node.tag, tuple(sorted(node.attrib.items())), node.text, node.tail, len(node)))
        stack.extend(reversed(list(node)))
    return out


# --- ported prototype checks ---------------------------------------------------------------------------------------


def test_nearest_ancestor_wins_explicit_wins_validator_accepts():
    svg = page('<text x="1" y="2" fill="#111111">a</text><g font-size="20"><text x="3" y="4" fill="#111111">b</text>'
               '<text x="5" y="6" font-size="30" fill="#111111">c</text></g>',
               ' font-family="Noto Sans KR" font-size="14"')
    out, counters = expanded(svg)
    texts = elements(ET.fromstring(out), "text")
    assert [t.get("font-size") for t in texts] == ["14", "20", "30"]
    assert all(t.get("font-family") == "Noto Sans KR" for t in texts)
    assert verdict(out) == "ok"
    assert counters == {"texts_filled": 3, "containers_stripped": 2}


def test_container_declarations_of_text_only_properties_are_stripped():
    svg = page('<g font-size="20" font-weight="bold" text-anchor="middle">'
               '<text x="3" y="4" fill="#111111">b</text></g>',
               ' font-family="Noto Sans KR" font-size="14"')
    out, _ = expanded(svg)
    root = ET.fromstring(out)
    for key in ("font-family", "font-size", "font-weight", "text-anchor"):
        assert key not in root.attrib
        assert all(key not in g.attrib for g in elements(root, "g"))
    text = elements(root, "text")[0]
    assert (text.get("font-size"), text.get("font-weight"), text.get("text-anchor")) == ("20", "bold", "middle")
    assert verdict(out) == "ok"


def test_compact_form_alone_is_rejected_by_the_unchanged_validator():
    svg = page('<text x="1" y="2" fill="#111111">a</text>', ' font-family="F" font-size="14"')
    assert verdict(svg) == "svg_contract_unsupported_attribute"
    assert verdict(expanded(svg)[0]) == "ok"


def test_tspan_is_never_touched():
    svg = page('<text x="1" y="2" fill="#111111">a<tspan x="1" y="20">b</tspan>'
               '<tspan x="1" y="40" font-size="9">c</tspan></text>', ' font-family="F" font-size="14"')
    out, _ = expanded(svg)
    spans = elements(ET.fromstring(out), "tspan")
    assert "font-size" not in spans[0].attrib and "font-family" not in spans[0].attrib
    assert spans[1].get("font-size") == "9" and "font-family" not in spans[1].attrib


def test_source_table_group_ends_with_only_its_id():
    svg = page('<g id="source-native-table-1" font-size="12"><rect x="0" y="0" width="9" height="9" fill="#ffffff"/>'
               '<text id="source-fact-a" x="1" y="2" fill="#111111">a</text></g>', ' font-family="F"')
    out, counters = expanded(svg)
    group = elements(ET.fromstring(out), "g")[0]
    assert group.attrib == {"id": "source-native-table-1"}
    assert group[1].get("font-size") == "12" and group[1].get("font-family") == "F"
    assert verdict(out) == "ok"
    assert counters == {"texts_filled": 1, "containers_stripped": 2}


def test_root_fill_is_stripped_group_fill_keeps_its_shape_meaning():
    svg = page('<g fill="#2563EB"><rect x="0" y="0" width="9" height="9"/>'
               '<text x="1" y="2" font-family="F" font-size="9">a</text></g><rect x="0" y="0" width="9" height="9"/>',
               ' fill="#0F172A"')
    out, _ = expanded(svg)
    root = ET.fromstring(out)
    group = elements(root, "g")[0]
    assert "fill" not in root.attrib
    assert group.get("fill") == "#2563EB" and elements(group, "text")[0].get("fill") == "#2563EB"
    assert all("fill" not in rect.attrib for rect in elements(root, "rect"))


def test_unresolved_attribute_stays_missing_and_keeps_the_existing_error_code():
    out, counters = expanded(page('<text x="1" y="2" fill="#111111">a</text>', ' font-family="F"'))
    assert verdict(out) == "svg_contract_explicit_text_attributes_required"
    assert counters["texts_filled"] == 1


@pytest.mark.parametrize("root_attrs, inner, code", [
    (' font-family="F" font-size="14px"', '<text x="1" y="2" fill="#111111">a</text>', "svg_contract_invalid_number"),
    (' font-family="F" font-size="-3"', '<text x="1" y="2" fill="#111111">a</text>', "svg_contract_positive_required"),
    (' font-family="a;b" font-size="9"', '<text x="1" y="2" fill="#111111">a</text>', "svg_contract_invalid_font"),
    (' font-family="F" font-size="9" fill="inherit"', '<text x="1" y="2">a</text>',
     "svg_contract_solid_paint_required"),
])
def test_declared_values_are_copied_verbatim_and_rejected_by_the_unchanged_validator(root_attrs, inner, code):
    out, _ = expanded(page(inner, root_attrs))
    assert verdict(out) == code


def test_hostile_nesting_expands_iteratively():
    deep = "<g>" * 3000 + '<text x="1" y="2" fill="#111111">a</text>' + "</g>" * 3000
    root = ET.fromstring(page(deep, ' font-family="F" font-size="9"'))
    assert expand_text_defaults(root) == {"texts_filled": 1, "containers_stripped": 1}


def test_model_view_round_trips_around_a_shadowing_group_fill():
    svg = page('<g fill="#2563EB"><text x="1" y="2" font-family="F" font-size="9" fill="#2563EB">a</text></g>'
               '<text x="1" y="2" font-family="F" font-size="9" fill="#0F172A">b</text>'
               '<text x="3" y="4" font-family="F" font-size="9" fill="#0F172A">c</text>')
    view = compact_text_defaults(svg, TEXT_DEFAULT_PROPS_T2)
    assert view != svg and 'font-family="F"' in view.split(">")[0]
    assert signature(expanded(view)[0]) == signature(svg)


def test_server_owned_text_stays_verbose_in_the_model_view():
    svg = page('<text id="svg-system-page-number" x="1" y="2" font-family="F" font-size="12" fill="#111111">01</text>'
               '<text x="1" y="2" font-family="F" font-size="9" fill="#111111">b</text>'
               '<text x="3" y="4" font-family="F" font-size="9" fill="#111111">c</text>')
    view = compact_text_defaults(svg, TEXT_DEFAULT_PROPS_T1)
    node = next(x for x in ET.fromstring(view).iter() if x.get("id") == "svg-system-page-number")
    assert (node.get("font-family"), node.get("font-size")) == ("F", "12")
    assert ET.fromstring(view).get("font-size") == "9"


# --- generated canonical pages (stand-in for the stored-page corpus) ----------------------------------------------


def generated_page(seed, *, shuffle=False):
    """A canonical page in the shape the pipeline stores: table group, group fill, tspans, system text."""
    rng = random.Random(seed)
    family = rng.choice(["Noto Sans KR", "Pretendard", "Inter"])
    sizes, fills = [14, 16, 18, 20, 28, 40], ["#0F172A", "#475569", "#FFFFFF", "#2563EB"]
    counter = iter(range(10_000))

    def text(body, x, y, *, size=None, fill=None, anchor=None, weight=None, ident=None, family_value=None, spans=0):
        attrs = {"id": ident, "x": x, "y": y, "font-family": family_value or family,
                 "font-size": size or rng.choice(sizes[:3]), "font-weight": weight,
                 "fill": fill or rng.choice(fills[:2]), "text-anchor": anchor}
        names = [k for k, v in attrs.items() if v is not None]
        names = rng.sample(names, len(names)) if shuffle else [k for k in USUAL_ORDER if k in names]
        inner = body + "".join(f'<tspan x="{x}" y="{y + 22 * (i + 1)}">{body}{i}</tspan>' for i in range(spans))
        return "<text " + " ".join(f'{k}="{attrs[k]}"' for k in names) + f">{inner}</text>"

    parts = ['<rect x="0" y="0" width="1280" height="720" fill="#F8FAFC"/>',
             text("제목", 64, 72, size=40, fill=fills[0], weight=rng.choice([None, "bold"]))]
    for _ in range(rng.randint(4, 16)):
        parts.append(text(f"항목{next(counter)}", rng.randint(40, 900), rng.randint(120, 600),
                          anchor=rng.choice([None, None, "middle"]), spans=rng.choice([0, 0, 2])))
    if seed % 3 == 0:
        cells = "".join(f'<rect x="{64 + 160 * c}" y="{300 + 40 * r}" width="160" height="40" '
                        'fill="#FFFFFF" stroke="#CBD5E1"/>'
                        + text(f"셀{r}{c}", 72 + 160 * c, 326 + 40 * r, ident=f"source-fact-{seed:04x}{r}{c}")
                        for r in range(2) for c in range(3))
        parts.append(f'<g id="source-native-table-{seed:04x}">{cells}</g>')
    if seed % 4 == 0:
        parts.append('<g fill="#2563EB"><rect x="0" y="0" width="12" height="720"/>'
                     + text("강조", 20, 40, fill="#2563EB", size=28) + "</g>")
    if seed % 5 == 0:
        parts.append(text("보조", 900, 650, size=14, fill=fills[1], family_value="Inter"))
    parts.append(text("01", 1230, 700, size=12, fill=fills[1], ident="svg-system-page-number"))
    return canonical(page("".join(parts)))


SEEDS = list(range(40))


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_pages_are_canonical_and_expansion_is_a_byte_noop(seed):
    svg = generated_page(seed)
    assert verdict(svg) == "ok"
    out, counters = expanded(svg)
    assert out == svg and counters == {"texts_filled": 0, "containers_stripped": 0}


@pytest.mark.parametrize("props", ALL_PROPS)
@pytest.mark.parametrize("seed", SEEDS)
def test_compaction_then_expansion_is_the_identity(seed, props):
    svg = generated_page(seed)
    view = compact_text_defaults(svg, props)
    assert view != svg and len(view) < len(svg)
    for prop in props:
        assert prop in ET.fromstring(view).attrib
    # the compact form is not valid on its own; the expansion restores a page the real validator accepts
    assert verdict(view) != "ok"
    out, counters = expanded(view)
    assert out == svg  # usual attribute order -> even byte-exact
    assert verdict(out) == "ok"
    assert validate_svg(out)["source_digest"] == source_digest(svg)
    assert counters["texts_filled"] > 0 and counters["containers_stripped"] == 1


@pytest.mark.parametrize("seed", SEEDS)
def test_compaction_round_trip_is_tree_exact_for_any_attribute_order(seed):
    svg = generated_page(seed, shuffle=True)
    for props in ALL_PROPS:
        view = compact_text_defaults(svg, props)
        assert view != svg
        assert signature(expanded(view)[0]) == signature(svg)


@pytest.mark.parametrize("seed", SEEDS[:12])
def test_expansion_is_idempotent(seed):
    view = compact_text_defaults(generated_page(seed), TEXT_DEFAULT_PROPS_T2)
    once, first = expanded(view)
    twice, second = expanded(once)
    assert twice == once
    assert first["texts_filled"] > 0 and second == {"texts_filled": 0, "containers_stripped": 0}
    root = ET.fromstring(view)
    expand_text_defaults(root)
    snapshot = ET.tostring(root, encoding="unicode")
    expand_text_defaults(root)
    assert ET.tostring(root, encoding="unicode") == snapshot


def test_no_op_on_an_already_explicit_page_with_every_attribute_kind():
    svg = canonical(page(
        '<g fill="#2563EB"><rect x="0" y="0" width="9" height="9"/><text x="1" y="2" font-family="F" font-size="9" '
        'fill="#2563EB" text-anchor="end">a</text></g><text id="t" x="1" y="2" font-family="F" font-size="9" '
        'font-weight="bold" fill="#111111">b<tspan x="1" y="9" font-size="7">c</tspan></text>'))
    out, counters = expanded(svg)
    assert out == svg and counters == {"texts_filled": 0, "containers_stripped": 0}
    assert verdict(out) == "ok"


def test_compaction_does_not_change_a_page_that_gains_nothing():
    one = page('<text x="1" y="2" font-family="F" font-size="9" fill="#111111">only</text>')
    assert compact_text_defaults(one, TEXT_DEFAULT_PROPS_T1) is one
    root_declared = page('<text x="1" y="2" fill="#111111">a</text><text x="3" y="4" fill="#111111">b</text>',
                         ' font-family="F" font-size="9"')
    assert compact_text_defaults(root_declared, TEXT_DEFAULT_PROPS_T1) is root_declared


def test_compaction_never_raises_on_bad_input():
    for value in ("", "<svg", "not xml at all", None, 12):
        assert compact_text_defaults(value, TEXT_DEFAULT_PROPS_T1) is value
    sample = generated_page(1)
    assert compact_text_defaults(sample, ()) is sample
    assert compact_text_defaults(sample, ("stroke", "opacity")) is sample
    deep = page("<g>" * 3000 + '<text x="1" y="2" font-family="F" font-size="9" fill="#111111">a</text>' * 2
                + "</g>" * 3000)
    assert compact_text_defaults(deep, TEXT_DEFAULT_PROPS_T1) is deep  # serializer depth -> unchanged, not raised


def test_modal_value_wins_and_ties_pick_the_smaller_string():
    svg = page("".join(f'<text x="{i}" y="2" font-family="F" font-size="{size}" fill="#111111">a</text>'
                       for i, size in enumerate((18, 18, 18, 30, 30, 14))))
    assert ET.fromstring(compact_text_defaults(svg, TEXT_DEFAULT_PROPS_T1)).get("font-size") == "18"
    tie = page("".join(f'<text x="{i}" y="2" font-family="F" font-size="{size}" fill="#111111">a</text>'
                       for i, size in enumerate((20, 20, 16, 16))))
    assert ET.fromstring(compact_text_defaults(tie, TEXT_DEFAULT_PROPS_T1)).get("font-size") == "16"


def test_materialized_attributes_land_in_the_usual_order():
    svg = page('<text id="t" x="1" y="2" fill="#111111" text-anchor="end">a</text>', ' font-family="F" font-size="9"')
    text = elements(ET.fromstring(expanded(svg)[0]), "text")[0]
    assert list(text.attrib) == ["id", "x", "y", "font-family", "font-size", "fill", "text-anchor"]
    odd = page('<text foo="1" x="1" y="2" fill="#111111">a</text>', ' font-family="F" font-size="9"')
    assert list(elements(ET.fromstring(expanded(odd)[0]), "text")[0].attrib) == [
        "foo", "x", "y", "font-family", "font-size", "fill"]


def test_nearest_group_wins_over_outer_groups_for_every_inherited_property():
    svg = page('<g font-size="30" font-weight="bold" text-anchor="end"><g font-size="20" text-anchor="middle">'
               '<text x="1" y="2" fill="#111111">a</text></g><text x="1" y="2" fill="#111111">b</text></g>',
               ' font-family="F" font-size="10"')
    out, _ = expanded(svg)
    inner, outer = elements(ET.fromstring(out), "text")
    assert (inner.get("font-size"), inner.get("font-weight"), inner.get("text-anchor")) == ("20", "bold", "middle")
    assert (outer.get("font-size"), outer.get("font-weight"), outer.get("text-anchor")) == ("30", "bold", "end")


def test_explicit_attribute_on_the_text_wins_even_when_invalid():
    svg = page('<text x="1" y="2" font-size="14px" fill="#111111">a</text>', ' font-family="F" font-size="20"')
    out, _ = expanded(svg)
    assert elements(ET.fromstring(out), "text")[0].get("font-size") == "14px"
    assert verdict(out) == "svg_contract_invalid_number"


def test_declarations_no_text_consumes_are_dropped():
    svg = page('<g font-size="abc" text-anchor="nowhere"><rect x="0" y="0" width="9" height="9" fill="#ffffff"/></g>'
               '<text x="1" y="2" font-family="F" font-size="9" fill="#111111">a</text>',
               ' font-weight="bold" fill="#000000"')
    root = ET.fromstring(expanded(svg)[0])
    assert dict(root.attrib) == {"width": "1280", "height": "720", "viewBox": "0 0 1280 720"}
    assert elements(root, "g")[0].attrib == {}
    assert elements(root, "text")[0].get("font-weight") == "bold"


def test_only_svg_namespace_text_is_expanded():
    svg = page('<text xmlns="urn:other" x="1" y="2">a</text><text x="1" y="2" fill="#111111">b</text>',
               ' font-family="F" font-size="9"')
    root = ET.fromstring(expanded(svg)[0])
    foreign = next(n for n in root.iter() if n.tag == "{urn:other}text")
    assert "font-family" not in foreign.attrib


# --- source classification coverage reads font-size/text-anchor directly: expansion must precede it ------------------


DIAGRAM = ("# 시스템 구성\n채널: 상점 화면, 기사 앱\n서비스: 주문 API, 배송 API\n"
           "외부 연계·저장소: 주문 DB, 운영 DB\n상점 화면 → 주문 API: 등록\n"
           "주문 API → 주문 DB: 주문 저장\n기사 앱 → 배송 API: 상태 전송\n"
           "배송 API → 운영 DB: 상태 저장")


def _aligned_blocks():
    blocks = ""
    for x, line in zip((61, 417, 773), DIAGRAM.splitlines()[1:4], strict=True):
        category, members = line.split(": ", 1)
        first, second = members.split(", ")
        blocks += (f'<text x="{x}" y="171" font-family="Inter" font-size="18" fill="#FFFFFF">{category}</text>'
                   f'<text x="{x}" y="209" text-anchor="start" font-family="Inter" font-size="17" fill="#111111">'
                   f'<tspan x="{x}" y="209">{first},</tspan><tspan x="{x}" y="235">{second}</tspan></text>')
    return blocks


def _coverage(svg):
    return author.source_classification_coverage(
        DIAGRAM, ET.fromstring(svg), allow_associated_blocks=True, allow_split_list_blocks=True)


@pytest.mark.parametrize("props", ALL_PROPS)
def test_classification_coverage_matches_after_compaction_and_expansion(props):
    svg = canonical(page(_aligned_blocks()))
    baseline = _coverage(svg)
    assert baseline["checked_count"] == 3 and baseline["missing_count"] == 0
    view = compact_text_defaults(svg, props)
    assert view != svg
    assert _coverage(expanded(view)[0]) == baseline
    # without the expansion the geometry (it reads font-size) is unproven, which is why the expansion runs
    # before the coverage check; a font-family-only view leaves every number the check reads explicit
    assert _coverage(view)["missing_count"] == (3 if "font-size" in props else 0)


# --- rule rewriter ------------------------------------------------------------------------------------------------


TEMPLATE_LINE = 'Return <svg xmlns="http://www.w3.org/2000/svg" width="W" height="H" viewBox="0 0 W H">.'
ATTRIBUTES_LINE = "Use explicit finite unitless coordinates. Set font-family,font-size,fill,x,y on EACH text."
T1_TEMPLATE = ('Return <svg xmlns="http://www.w3.org/2000/svg" width="W" height="H" viewBox="0 0 W H" '
               'font-family="F" font-size="S">.')
T1_ATTRIBUTES = (
    "Use explicit finite unitless coordinates. Set x,y,fill on EACH text. The root svg declares the page's shared "
    "text style once: F is tokens.body_font and S the most common text size on this page. Every text inherits F and "
    "S as in SVG; write font-family or font-size on a text only when it differs from the root. After inheritance "
    "every text must have font-family, font-size and fill. Never declare font-weight or text-anchor on svg or g. "
    "The inherited style applies to text only; shapes always carry their own fill and stroke.")
RULE_SOURCES = {
    "constant": lambda: author.SVG_AUTHOR_RULES,
    "new_copy_constant": lambda: author.SVG_NEW_COPY_AUTHOR_RULES,
    "call_rules": lambda: author.svg_author_rules({"canvas": {"width": 1280, "height": 720}}, 16),
    "call_rules_new_copy": lambda: author.svg_author_rules({"canvas": {"width": 1280, "height": 720}}, 16,
                                                           new_copy=True),
    "portrait_call_rules": lambda: author.svg_author_rules({"canvas": {"width": 720, "height": 1280}}, 16),
}


def test_the_rule_anchors_exist_in_the_unmodified_constant():
    assert TEMPLATE_LINE in author.SVG_AUTHOR_RULES and ATTRIBUTES_LINE in author.SVG_AUTHOR_RULES
    assert TEMPLATE_LINE in author.SVG_NEW_COPY_AUTHOR_RULES and ATTRIBUTES_LINE in author.SVG_NEW_COPY_AUTHOR_RULES


@pytest.mark.parametrize("source", sorted(RULE_SOURCES))
def test_t1_rewrites_exactly_the_template_line_and_the_attribute_sentence(source):
    rules = RULE_SOURCES[source]()
    rewritten = text_defaults_rules(rules, TEXT_DEFAULT_PROPS_T1)
    assert TEMPLATE_LINE not in rewritten and ATTRIBUTES_LINE not in rewritten
    assert T1_TEMPLATE in rewritten and T1_ATTRIBUTES in rewritten
    before, after = rules.splitlines(), rewritten.splitlines()
    assert len(before) == len(after)
    assert [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b] == [
        before.index(TEMPLATE_LINE), before.index(ATTRIBUTES_LINE)]
    # the rest of the rules text, including the heading/body font rule, is untouched
    assert rewritten.replace(T1_TEMPLATE, TEMPLATE_LINE).replace(T1_ATTRIBUTES, ATTRIBUTES_LINE) == rules
    assert "tokens.heading_font/tokens.body_font as the font-family of every heading/body text element." in rewritten


def test_rewriting_never_mutates_the_constant():
    text_defaults_rules(author.SVG_AUTHOR_RULES, TEXT_DEFAULT_PROPS_T1)
    assert TEMPLATE_LINE in author.SVG_AUTHOR_RULES and ATTRIBUTES_LINE in author.SVG_AUTHOR_RULES
    assert 'font-family="F"' not in author.SVG_AUTHOR_RULES


def test_t0_and_t2_wording_matches_what_the_expansion_accepts():
    t0 = text_defaults_rules(author.SVG_AUTHOR_RULES, TEXT_DEFAULT_PROPS_T0)
    assert 'viewBox="0 0 W H" font-family="F">.' in t0
    assert "Set x,y,font-size,fill on EACH text." in t0 and "write font-family on a text only when" in t0
    assert "Every text inherits F as in SVG;" in t0 and "S the most common" not in t0
    t2 = text_defaults_rules(author.SVG_AUTHOR_RULES, TEXT_DEFAULT_PROPS_T2)
    assert 'viewBox="0 0 W H" font-family="F" font-size="S" fill="C">.' in t2
    assert ("Set x,y on EACH text. The root svg declares the page's shared text style once: F is tokens.body_font, "
            "S the most common text size on this page, C the most common text colour. Every text inherits F, S and C "
            "as in SVG; write font-family, font-size or fill on a text only when it differs from the root.") in t2
    for variant in (t0, T1_ATTRIBUTES, t2):
        assert "After inheritance every text must have font-family, font-size and fill." in variant
        assert "Never declare font-weight or text-anchor on svg or g." in variant


@pytest.mark.parametrize("missing", [TEMPLATE_LINE, ATTRIBUTES_LINE])
def test_a_missing_anchor_sentence_is_never_a_silent_no_op(missing):
    rules = author.SVG_AUTHOR_RULES.replace(missing, "")
    with pytest.raises(ValueError, match="^svg_output_view_rule_anchor_missing$"):
        text_defaults_rules(rules, TEXT_DEFAULT_PROPS_T1)
    with pytest.raises(ValueError, match="^svg_output_view_rule_anchor_missing$"):
        text_defaults_rules("", TEXT_DEFAULT_PROPS_T1)


def test_a_second_application_and_unsupported_props_fail_loudly():
    once = text_defaults_rules(author.SVG_AUTHOR_RULES, TEXT_DEFAULT_PROPS_T1)
    with pytest.raises(ValueError, match="svg_output_view_rule_anchor_missing"):
        text_defaults_rules(once, TEXT_DEFAULT_PROPS_T1)
    with pytest.raises(ValueError, match="svg_output_view_text_defaults_props_unsupported"):
        text_defaults_rules(author.SVG_AUTHOR_RULES, ("font-weight",))


def test_published_constants():
    assert TEXT_DEFAULTS_VERSION == "svg-text-defaults/v1"
    assert TEXT_DEFAULT_PROPS_T1 == ("font-family", "font-size")
    assert TEXT_DEFAULTS_REPAIR_SENTENCE == (
        "previous_svg declares its shared text style on the root svg; keep that convention")


def test_container_font_attributes_are_pushed_onto_texts_without_touching_fill():
    from services.svg_text_defaults import push_down_container_font_attributes

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
           '<g font-family="Noto Sans KR" font-size="18" fill="#112233">'
           '<rect x="0" y="0" width="10" height="10"/>'
           '<text x="10" y="20" fill="#112233">a</text>'
           '<text x="10" y="40" font-size="24" fill="#112233">b</text></g></svg>')
    root = ET.fromstring(svg)
    assert push_down_container_font_attributes(root) == 2
    group = next(node for node in root.iter() if node.tag.endswith("}g"))
    assert "font-family" not in group.attrib and group.get("fill") == "#112233"
    texts = [node for node in root.iter() if node.tag.endswith("}text")]
    assert [(t.get("font-family"), t.get("font-size")) for t in texts] == [("Noto Sans KR", "18"),
                                                                          ("Noto Sans KR", "24")]
    assert verdict(ET.tostring(root, encoding="unicode")) == "ok"


def test_an_explicit_page_is_a_no_op_and_a_grouped_one_replays_into_a_valid_page():
    from services.svg_author_output_view import parse_author_response
    from services.svg_text_defaults import push_down_container_font_attributes

    explicit = page('<text x="1" y="2" font-family="F" font-size="14" fill="#111111">a</text>')
    root = ET.fromstring(explicit)
    assert push_down_container_font_attributes(root) == 0
    assert ET.tostring(root, encoding="unicode") == ET.tostring(ET.fromstring(explicit), encoding="unicode")
    grouped = explicit.replace('<text x="1" y="2" font-family="F" font-size="14"',
                               '<g font-family="F" font-size="14"><text x="1" y="2"').replace("</text>", "</text></g>")
    assert verdict(grouped) == "svg_contract_unsupported_attribute"
    replayed, receipt = parse_author_response(grouped, view=None, source_facts=None)
    assert receipt is None and verdict(ET.tostring(replayed, encoding="unicode")) == "ok"


def test_a_group_fill_is_pushed_onto_texts_that_lack_one_and_the_group_keeps_it():
    from services.svg_text_defaults import push_down_container_fill, push_down_container_font_attributes

    svg = page('<g font-family="Pretendard" fill="#243B7C">'
               '<rect x="0" y="0" width="10" height="10" fill="#EEEEEE"/>'
               '<text x="10" y="20" font-size="16">a</text>'
               '<text x="10" y="40" font-size="16" fill="#FF0000">b</text></g>')
    root = ET.fromstring(svg)
    assert verdict(svg) == "svg_contract_unsupported_attribute"
    push_down_container_font_attributes(root)
    assert push_down_container_fill(root) == 1
    group = next(node for node in root.iter() if node.tag.endswith("}g"))
    assert group.get("fill") == "#243B7C"  # shapes under the group keep painting with it
    assert [t.get("fill") for t in elements(root, "text")] == ["#243B7C", "#FF0000"]  # own fill wins
    assert verdict(ET.tostring(root, encoding="unicode")) == "ok"


def test_the_nearest_fill_wins_and_a_none_or_paint_server_pushes_nothing():
    from services.svg_text_defaults import push_down_container_fill

    inner = ('<g fill="#111111"><g fill="#222222"><text x="1" y="2" font-family="F" font-size="14">a</text></g>'
             '<g fill="none"><text x="1" y="9" font-family="F" font-size="14">b</text></g>'
             '<g fill="url(#p)"><text x="1" y="9" font-family="F" font-size="14">c</text></g></g>')
    root = ET.fromstring(page(inner))
    assert push_down_container_fill(root) == 1
    assert [t.get("fill") for t in elements(root, "text")] == ["#222222", None, None]


def test_an_explicit_page_is_a_no_op_for_the_fill_push_down_and_replay_matches_the_producer():
    from services.svg_author_output_view import parse_author_response
    from services.svg_text_defaults import push_down_container_fill

    explicit = page('<g fill="#111111"><text x="1" y="2" font-family="F" font-size="14" fill="#222222">a</text></g>')
    root = ET.fromstring(explicit)
    assert push_down_container_fill(root) == 0
    assert ET.tostring(root, encoding="unicode") == ET.tostring(ET.fromstring(explicit), encoding="unicode")
    # The pattern measured on real author responses: family and colour on a group, size on each text.
    grouped = page('<g font-family="Pretendard" fill="#243B7C"><text x="1" y="2" font-size="14">a</text>'
                   '<text x="1" y="30" font-size="14">b</text></g>')
    assert verdict(grouped) == "svg_contract_unsupported_attribute"
    replayed, receipt = parse_author_response(grouped, view=None, source_facts=None)
    assert receipt is None and verdict(ET.tostring(replayed, encoding="unicode")) == "ok"
