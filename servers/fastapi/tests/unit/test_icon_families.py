"""Vendored icon families, their library-style composition, and the deck icon selection call."""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET

import pytest

from services.generation_core.assets.icon_pack import families
from services.generation_core.drivers.svg.embed_assets import icon_group
from services.generation_core.drivers.svg.icon_flatten import ICON_POINT_BUDGET, flatten_icon_svg
from services.svg_contract import validate_svg
from utils.hashing import sha256_hex
from utils.llm_calls import select_deck_icons as selection


@pytest.mark.parametrize(("family_id", "version", "count", "licence"), [
    ("lucide", "1.67.0", 2019, "ISC License"),
    ("tabler-outline", "3.50.0", 4825, "MIT License"),
    ("phosphor-regular", "2.1.1", 1509, "MIT License"),
    ("phosphor-fill", "2.1.1", 1512, "MIT License"),
    ("phosphor-duotone", "2.1.1", 1477, "MIT License"),
])
def test_vendored_families_match_their_manifests(family_id, version, count, licence):
    directory = families.FAMILIES_DIRECTORY / family_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert sha256_hex((directory / manifest["file"]).read_bytes()) == manifest["sha256"]
    family = families.load_family(family_id)
    assert (family.version, len(family.names), manifest["icons"]) == (version, count, count)
    assert (directory / "LICENSE").read_text(encoding="utf-8").startswith(licence)
    assert not any(families.has_icon(name, family_id) for name in ("github", "slack", "linkedin", "youtube"))
    assert not any(name.startswith("brand-") for name in family.names)


def test_unknown_families_are_refused():
    assert set(families.available_families()) >= {"lucide", "tabler-outline"}
    with pytest.raises(ValueError, match="icon_family_unknown"):
        families.load_family("../lucide")


def test_a_single_icon_uses_the_library_layout_and_colour():
    svg = families.compose_icon_svg("shield-check", color="#123456")
    root = ET.fromstring(svg)
    assert root.get("viewBox") == "0 0 128 128" and root.get("stroke") == "#123456"
    assert (root.get("stroke-width"), root.get("stroke-linecap"), root.get("stroke-linejoin")) == \
        ("1.75", "round", "round")
    groups = [node.get("transform") for node in root if node.tag.endswith("g")]
    assert groups == ["translate(12 12) scale(4.333333)"]


def test_a_badge_sits_in_the_bottom_right_corner_and_filled_dots_take_the_ink():
    svg = families.compose_icon_svg("key-round", "database", color="#123456")
    root = ET.fromstring(svg)
    groups = [node.get("transform") for node in root if node.tag.endswith("g")]
    assert groups == ["translate(8 8) scale(3.25)", "translate(78 78) scale(1.75)"]
    assert "currentColor" not in svg and 'fill="#123456"' in svg
    assert families.compose_icon_svg("database", "database") == families.compose_icon_svg("database")


def test_unknown_names_are_refused():
    with pytest.raises(ValueError, match="icon_family_icon_unknown"):
        families.compose_icon_svg("not-an-icon")
    with pytest.raises(ValueError, match="icon_family_icon_unknown"):
        families.compose_icon_svg("database", "not-an-icon")


@pytest.mark.parametrize("family_id", ["lucide", "tabler-outline", "phosphor-regular", "phosphor-fill",
                                       "phosphor-duotone"])
def test_every_vendored_icon_flattens_within_budget_and_places_on_a_valid_page(family_id):
    worst = 0
    for name in families.load_family(family_id).names:
        svg = families.compose_icon_svg(name, color="#112233", family_id=family_id)
        worst = max(worst, flatten_icon_svg(svg, color="#112233").point_count)
    assert worst <= ICON_POINT_BUDGET
    icon = flatten_icon_svg(families.compose_icon_svg("users", "shield-check", color="#112233",
                                                      family_id=family_id), color="#112233", point_budget=None)
    outline = families.load_family(family_id).stroke_width is not None
    assert (icon.line_cap, icon.line_join) == (("round", "round") if outline else ("butt", "miter"))
    group = ET.tostring(icon_group(icon, x=100, y=100, width=64, height=64), encoding="unicode")
    validate_svg('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
                 '<text x="80" y="80" font-family="Noto Sans KR" font-size="32" fill="#112233">T</text>'
                 f"{group}</svg>")


def test_write_icon_is_content_addressed(tmp_path):
    first = families.write_icon("siren", None, color="#123456", directory=tmp_path)
    again = families.write_icon("siren", None, color="#123456", directory=tmp_path)
    other = families.write_icon("siren", None, color="#654321", directory=tmp_path)
    assert first == again != other
    assert first.name == sha256_hex(first.read_bytes()) + ".svg"


def test_selection_request_quotes_labels_and_lists_the_vocabulary():
    messages = selection.build_deck_icon_messages(["다중 인증", "데이터 암호화"], ["database", "lock"])
    system, user = messages[0].content, messages[-1].content
    assert "1. 다중 인증" in user and "2. 데이터 암호화" in user
    assert system.rstrip().endswith("database lock") and "다중 인증" not in system
    # the deck-independent prefix is identical for every deck
    assert selection.build_deck_icon_messages(["다른 라벨"], ["database", "lock"])[0].content == system


def test_anthropic_system_prompt_becomes_a_cache_breakpoint_only_when_asked():
    from llmai.shared.messages import SystemMessage, UserMessage

    from utils.anthropic_structured_output import RepositoryAnthropicClient, cache_system_prompt

    client = RepositoryAnthropicClient.__new__(RepositoryAnthropicClient)
    messages = [SystemMessage(content="fixed vocabulary"), UserMessage(content="labels")]
    assert client._get_system_prompt(messages) == "fixed vocabulary"
    with cache_system_prompt():
        assert client._get_system_prompt(messages) == [
            {"type": "text", "text": "fixed vocabulary", "cache_control": {"type": "ephemeral"}}]
    assert client._get_system_prompt(messages) == "fixed vocabulary"


def test_parse_keeps_valid_picks_and_drops_the_rest():
    content = {"icons": [
        {"k": 1, "main": "fingerprint", "badge": None},
        {"k": 2, "main": "database", "badge": "lock"},
        {"k": 3, "main": "made-up", "badge": None},
        {"k": 4, "main": "siren", "badge": "made-up"},
        {"k": 9, "main": "siren"},
        {"k": 1, "main": "lock"},
        {"k": 5, "main": "lock", "badge": "lock"},
    ]}
    labels = ["다중 인증", "데이터 암호화", "거버넌스", "사고 대응", "접근"]
    picks = selection.parse_deck_icon_selection(content, labels, ["fingerprint", "database", "lock", "siren"])
    assert picks == {"다중 인증": ("fingerprint", None), "데이터 암호화": ("database", "lock"),
                     "사고 대응": ("siren", None), "접근": ("lock", None)}


def test_selection_fails_open_and_uses_its_own_semantic_slot(monkeypatch):
    seen = {}

    async def boom(*_args, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("provider down")

    monkeypatch.setattr(selection, "get_client", lambda **_: object())
    monkeypatch.setattr(selection, "get_llm_config", lambda: None)
    monkeypatch.setattr(selection, "get_model", lambda: "model")
    monkeypatch.setattr(selection, "generate_structured_with_schema_retries", boom)
    assert asyncio.run(selection.select_deck_icons(["다중 인증"], ["lock"])) is None
    assert seen["semantic_slot_prefix"] == selection.SEMANTIC_SLOT_PREFIX != "structured"
    assert asyncio.run(selection.select_deck_icons(["", "  "], ["lock"])) == {}


def test_selection_returns_the_parsed_picks(monkeypatch):
    async def answer(*_args, **_kwargs):
        return {"icons": [{"k": 1, "main": "lock", "badge": None}, {"k": 2, "main": "nope"}]}

    monkeypatch.setattr(selection, "get_client", lambda **_: object())
    monkeypatch.setattr(selection, "get_llm_config", lambda: None)
    monkeypatch.setattr(selection, "get_model", lambda: "model")
    monkeypatch.setattr(selection, "generate_structured_with_schema_retries", answer)
    picks = asyncio.run(selection.select_deck_icons(["접근 제어", "접근 제어", "기타"], ["lock"]))
    assert picks == {"접근 제어": ("lock", None)}


def test_selection_prompt_version_is_named():
    assert re.fullmatch(r"deck-icon-selection-v\d+", selection.DECK_ICON_SELECTION_PROMPT_VERSION)


def test_every_bundled_style_names_a_vendored_icon_family():
    from utils.authored_styles import load_authored_styles

    styles = load_authored_styles()
    assert styles
    available = set(families.available_families())
    assert {style.icon_family for style in styles} <= available
    by_id = {style.id: style for style in styles}
    assert by_id["exec-report"].icon_family == "tabler-outline"
    assert by_id["cyber-ai"].icon_family == "lucide"
    assert by_id["flat-infographic"].icon_family == "phosphor-duotone"


def test_a_style_yaml_can_name_its_icon_family(tmp_path):
    from utils.authored_styles import _parse_style

    base = {"id": "x", "name": "X", "description": "d", "brief": "b", "category": "creative",
            "preview": {"bg": "#FFFFFF", "accent": "#000000"}}
    assert _parse_style(tmp_path / "x.yaml", tmp_path, data=base).icon_family == "lucide"
    named = _parse_style(tmp_path / "x.yaml", tmp_path, data={**base, "icon_family": "tabler-outline"})
    assert named.icon_family == "tabler-outline"
    with pytest.raises(ValueError, match="icon_family"):
        _parse_style(tmp_path / "x.yaml", tmp_path, data={**base, "icon_family": "../lucide"})
