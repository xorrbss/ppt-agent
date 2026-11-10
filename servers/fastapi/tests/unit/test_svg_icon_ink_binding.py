"""Icons are painted with the accent the author uses, whatever the icon family."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from services.generation_core.assets import offer as offer_mod
from services.generation_core.assets.icon_pack import families
from services.generation_core.assets.vector_offer import deck_palette
from services.generation_core.assets.style_primary import resolve_style_primary
from services.generation_core.style.brand import Brand
from services.generation_core.drivers.svg.asset_slots import _ink
from utils.authored_styles import resolve_authored_style

STYLES_DIRECTORY = Path(__file__).resolve().parents[2] / "authored_styles"
STYLE_IDS = sorted(path.stem for path in STYLES_DIRECTORY.glob("*.yaml"))
LEGACY_INK = "#1e5eff"
PHOSPHOR = ("phosphor-regular", "phosphor-fill", "phosphor-duotone")
STROKE_FAMILIES = ("lucide", "tabler-outline")


def _brand(**fields) -> Brand:
    return Brand(topic="deck", **fields)


def _stroke_widths(svg: str) -> tuple[float, float]:
    """Effective drawn stroke width of the main icon and of the badge, in 128-unit space."""
    root = float(re.search(r'<svg[^>]*stroke-width="([\d.]+)"', svg)[1])
    groups = re.findall(r'<g transform="translate\([\d.]+ [\d.]+\) scale\(([\d.]+)\)"( stroke-width="([\d.]+)")?>',
                        svg)
    (main_scale, _, _), (badge_scale, _, badge_override) = groups
    return root * float(main_scale), float(badge_override or root) * float(badge_scale)


def test_style_list_is_not_empty():
    assert len(STYLE_IDS) >= 30


@pytest.mark.parametrize("style_id", STYLE_IDS)
def test_icon_ink_is_the_authors_accent(style_id):
    style = resolve_authored_style(style_id)
    brand = _brand()
    # The accent the author is told to use (the former HTML author's
    # apply_style_defaults: the default style keeps the brand's own colour).
    accent = brand.primary if style.id == "default" else resolve_style_primary(brand, style)
    assert offer_mod.icon_ink(brand, style) == accent
    assert deck_palette(brand, style)[0]["ACCENT"].lower() == accent.lower()


@pytest.mark.parametrize("style_id", STYLE_IDS)
def test_explicit_brand_colour_wins(style_id):
    style = resolve_authored_style(style_id)
    brand = _brand(primary="#AA3311", primary_is_explicit=True)
    assert offer_mod.icon_ink(brand, style) == "#AA3311"


@pytest.mark.parametrize("family_id", PHOSPHOR + STROKE_FAMILIES)
@pytest.mark.parametrize("style_id", STYLE_IDS)
def test_slot_carries_the_ink_every_family_is_painted_with(style_id, family_id, tmp_path):
    style = resolve_authored_style(style_id)
    ink = offer_mod.icon_ink(_brand(), style)
    family = families.load_family(family_id)
    path = families.write_icon(family.names[0], None, color=ink, directory=tmp_path, family_id=family_id)
    slots = asyncio.run(offer_mod._icon_slots(
        [(0, ["x"])], pack_key="p", context=None, color=ink, receipt={}, preselected={"x": path}))
    assert [slot.ink for slot in slots] == [ink]
    text = Path(slots[0].path).read_text(encoding="utf-8")
    assert _ink(text, slots[0]) == ink


@pytest.mark.parametrize("family_id", PHOSPHOR)
def test_phosphor_icons_are_not_the_legacy_blue(family_id, tmp_path):
    ink = "#B03A2E"
    family = families.load_family(family_id)
    path = families.write_icon(family.names[0], None, color=ink, directory=tmp_path, family_id=family_id)
    text = path.read_text(encoding="utf-8")
    assert ink in text and LEGACY_INK not in text.lower()


@pytest.mark.parametrize("family_id", STROKE_FAMILIES)
def test_badge_stroke_matches_main_stroke(family_id):
    family = families.load_family(family_id)
    svg = families.compose_icon_svg(family.names[0], family.names[1], color="#102030", family_id=family_id)
    main, badge = _stroke_widths(svg)
    assert abs(badge - main) / main <= 0.10


def test_stored_slot_without_ink_still_loads(tmp_path):
    slot = offer_mod.AssetSlot(page_index=0, role="figure-icon", kind="icon", alt="a", path=str(tmp_path / "a.svg"),
                               media_type="image/svg+xml", sha256="0" * 64, width=128, height=128)
    stored = offer_mod.AssetOffer(slots=(slot,)).to_json(root=tmp_path)
    for item in stored["slots"]:
        item.pop("ink")
    assert offer_mod.AssetOffer.from_json(stored, root=tmp_path).slots[0].ink is None
