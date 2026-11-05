"""``SVG_TEMPLATE_VISUAL_GUIDANCE``: the template's body vocabulary instead of anti-decoration advice."""

from types import SimpleNamespace

import pytest

from models.svg_template_frame import SvgTemplateBodyComposition
from services.svg_templates import body_composition as composition
from services.svg_templates import generation
from utils.get_env_concurrency import get_svg_template_visual_guidance_env


def _grammar():
    return SvgTemplateBodyComposition.model_validate({
        "grammar": {"pages_sampled": 20, "sections": (2, 3, 4), "numbered_headings": 0.9, "key_message_bar": 0.8,
                    "content_kinds_per_page": (2, 3, 4), "kind_shares": (("table", 0.5), ("flow", 0.4)),
                    "text_items": (30, 50, 70), "ink_coverage": 0.6, "bottom_reach": 0.95,
                    "text_sizes": (18.0, 14.5, 12.0)},
        "exemplars": [],
    }).model_dump(mode="json")


def test_the_knob_reads_on_true_and_1_and_defaults_to_off(monkeypatch):
    monkeypatch.delenv("SVG_TEMPLATE_VISUAL_GUIDANCE", raising=False)
    assert get_svg_template_visual_guidance_env() is False
    for value in ("on", "True", "1"):
        monkeypatch.setenv("SVG_TEMPLATE_VISUAL_GUIDANCE", value)
        assert get_svg_template_visual_guidance_env() is True
    monkeypatch.setenv("SVG_TEMPLATE_VISUAL_GUIDANCE", "yes")
    assert get_svg_template_visual_guidance_env() is False


def test_the_snapshot_guidance_keeps_its_bytes_with_the_knob_off(monkeypatch):
    monkeypatch.delenv("SVG_TEMPLATE_VISUAL_GUIDANCE", raising=False)
    lines = generation.template_composition_guidance()
    assert lines == [
        "The server preserves original fonts, background, header, footer and logos.",
        "Author only the permitted body region. Reuse successful source hierarchy, comparison and image treatment when they fit this content.",
        "Adjust only what needs improvement; freely redesign the body when content or canvas makes the source composition unsuitable.",
        "Prefer clear simple compositions. Decorative cards, icons or visual difference are not evidence of improvement.",
        "Examples contain no factual authority: use only this request's facts, labels, figures and relationships.",
    ]


def test_the_knob_swaps_only_the_anti_decoration_line(monkeypatch):
    monkeypatch.setenv("SVG_TEMPLATE_VISUAL_GUIDANCE", "on")
    lines = generation.template_composition_guidance()
    monkeypatch.delenv("SVG_TEMPLATE_VISUAL_GUIDANCE")
    plain = generation.template_composition_guidance()
    assert len(lines) == len(plain) == 5
    assert [index for index, (a, b) in enumerate(zip(lines, plain, strict=True)) if a != b] == [3]
    assert lines[3] == generation.TEMPLATE_VOCABULARY_LINE
    assert "icons or visual difference are not evidence" not in lines[3]
    # The vocabulary is conditional on the content having that shape; nothing is invented.
    for phrase in ("wherever this page's content has that shape", "a table for attributes the source compares",
                   "source-stated sequence", "source order",
                   "each offered icon beside the label of its own item", "not a page-dominating number",
                   "Decoration without such content is not an improvement"):
        assert phrase in lines[3]
    # The factual-authority line is kept.
    assert lines[4] == plain[4] and "no factual authority" in lines[4]


def test_apply_template_snapshot_freezes_the_chosen_guidance(monkeypatch):
    selection = {"mode": "template-redesign", "template_pin": {"templateId": "tpl"},
                 "style_projection": {"fonts": {"heading": {"family": "Pretendard"}, "body": {"family": "Pretendard"}}}}
    monkeypatch.setattr(generation, "template_package", lambda value: {
        "font_manifest": {"assets": [{"family": "Pretendard", "weight": 700, "face": "Pretendard-Bold"}]}})
    request = SimpleNamespace(svg_template_pin={"templateId": "tpl"}, _svg_template_selection=selection)

    def snapshot():
        return {"provenance": {}, "tokens": {}, "digest": ""}

    monkeypatch.delenv("SVG_TEMPLATE_VISUAL_GUIDANCE", raising=False)
    off = snapshot()
    generation.apply_template_snapshot(off, request)
    monkeypatch.setenv("SVG_TEMPLATE_VISUAL_GUIDANCE", "on")
    on = snapshot()
    generation.apply_template_snapshot(on, request)
    assert generation.PLAIN_COMPOSITION_LINE in off["composition_guidance"]
    assert generation.TEMPLATE_VOCABULARY_LINE in on["composition_guidance"]
    assert generation.PLAIN_COMPOSITION_LINE not in on["composition_guidance"]
    # The guidance is a digest input, so the two snapshots differ in digest only through it.
    assert off["digest"] != on["digest"]
    assert {k: v for k, v in off.items() if k not in {"composition_guidance", "digest"}} == \
        {k: v for k, v in on.items() if k not in {"composition_guidance", "digest"}}


def test_the_density_line_swaps_enlarging_for_explaining():
    off = composition.composition_rules(_grammar(), body_floor=14.0)
    on = composition.composition_rules(_grammar(), body_floor=14.0, visual_guidance=True)
    assert composition.FILL_BY_ENLARGING in off and composition.FILL_BY_EXPLAINING not in off
    assert composition.FILL_BY_EXPLAINING in on and composition.FILL_BY_ENLARGING not in on
    assert off.replace(composition.FILL_BY_ENLARGING, composition.FILL_BY_EXPLAINING) == on
    assert "Never invent facts to fill space." in on
    assert "larger type or taller empty cards" in on


def test_the_off_path_density_paragraph_is_pinned_literally():
    rules = composition.composition_rules(_grammar(), body_floor=14.0)
    assert rules == (
        "\nTEMPLATE PAGE COMPOSITION (measured over 20 of this template's body pages; how it fills a page, not a"
        " layout to copy):\n"
        "- Sections: a body page holds 3 sections (quartiles 2-4); 90% of pages start each section with a numbered"
        " sub-heading; 80% open the body with a key-message bar across the content width right under the header,"
        " stating the page's conclusion.\n"
        "- Mix: a page combines 3 different content kinds (quartiles 2-4); across pages: table 50%, flow 40%. Give"
        " each section the component its own content calls for (a table for compared attributes, steps for a"
        " sequence, a hierarchy for an organisation or layers, a chart or figures for numbers, cards for parallel"
        " points) instead of repeating one component down the page.\n"
        "- Density: 50 text items per page (quartiles 30-70), body text about 14.5px (from 14 to 18px); elements"
        " cover 60% of the body area and reach 95% of its height. Fill body_region from top to bottom at a"
        " comparable density using every fact, label and figure of this page (state each point fully; give labels a"
        " short explanation from the source); when the facts are few, enlarge the components rather than leaving"
        " the lower half empty. Never invent facts to fill space.\n"
        "- template_exemplars, when given, outline one or two of this template's own body pages whose content"
        " resembles this page's: one element per line in reading order, kind, x,y relative to the body's top-left,"
        " width x height in px, paint, then text size/weight colour and amount (p paragraphs, ch characters that"
        " fit; the words are left out; number = an item number, number+ = a heading that starts with one); picture"
        " and icon mark the source's images, where this page uses its own offered images or icons, or content."
        " They show how this template divides and fills a page; never copy their positions, counts or arrangement,"
        " and never take content from them: compose this page from its own content.\n"
    )


@pytest.mark.parametrize("frozen", [None, "on"])
@pytest.mark.parametrize("env", [None, "on"])
def test_body_author_rules_follow_the_frozen_snapshot_not_the_environment(monkeypatch, frozen, env):
    """A deck admitted under one setting and authored, retried or remade under the other keeps one text."""
    monkeypatch.setattr(generation, "template_selection", lambda snapshot: {"mode": "template-redesign"})
    monkeypatch.setattr(generation, "template_package", lambda value: {"body_composition": _grammar()})
    monkeypatch.delenv("SVG_TEMPLATE_COMPONENT_STYLE", raising=False)
    monkeypatch.delenv("SVG_TEMPLATE_BODY_EXEMPLARS", raising=False)
    if frozen is None:
        monkeypatch.delenv("SVG_TEMPLATE_VISUAL_GUIDANCE", raising=False)
    else:
        monkeypatch.setenv("SVG_TEMPLATE_VISUAL_GUIDANCE", frozen)
    guidance = generation.template_composition_guidance()  # frozen at admission
    if env is None:
        monkeypatch.delenv("SVG_TEMPLATE_VISUAL_GUIDANCE", raising=False)
    else:
        monkeypatch.setenv("SVG_TEMPLATE_VISUAL_GUIDANCE", env)
    snapshot = {"composition_guidance": guidance}
    assert generation.snapshot_has_visual_guidance(snapshot) is (frozen == "on")
    rules = generation.body_author_rules("BASE", snapshot, body_floor=14.0)
    assert (composition.FILL_BY_EXPLAINING in rules) is (frozen == "on")
    assert (composition.FILL_BY_ENLARGING in rules) is (frozen is None)
    assert (generation.TEMPLATE_VOCABULARY_LINE in rules) is (frozen == "on")
    assert (generation.PLAIN_COMPOSITION_LINE in rules) is (frozen is None)
    # A snapshot made before the knob existed reads as off.
    assert generation.snapshot_has_visual_guidance({}) is False
