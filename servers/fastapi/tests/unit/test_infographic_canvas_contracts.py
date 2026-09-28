"""Infographic figure contracts derive their vertical budget from the deck canvas.

1280x720 is frozen byte for byte against ``fixtures/infographic_16x9_baseline``;
every other canvas moves only the bottom content line (height minus the footer
band). The 430px box floor, the low-height ceilings and the measured 76px/108px
shortfalls are figure-intrinsic CSS heights and stay as stated. Design:
``docs/aspect-ratio-expansion-design-20260928.md`` §3.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from models.authored_infographic_plan import (
    ARCHETYPES,
    AuthoredSlideInfographic,
    InfographicNode,
)
from utils.authored_infographic.contracts import (
    LOW_HEIGHT_MAX_PX,
    content_bottom_px,
    slot_contract,
)
from utils.llm_calls.author_infographic_marker import infographic_marker

BASELINE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "infographic_16x9_baseline"
FIGURE_ARCHETYPES = tuple(a for a in ARCHETYPES if a != "none")
MODES: tuple[str | None, ...] = ("template", "companyStandard", "none", None)
FULL_TEXT_MODES = ("template", "none")
WEIGHTS = ("hero", "standard")
VARIANTS = ("plain", "rich")
OTHER_SIZES = ((1188, 840), (840, 1188), (720, 1280), (720, 1040), (720, 720), (1680, 720))


def _nodes(variant: str) -> tuple[InfographicNode, ...]:
    if variant == "plain":
        return (
            InfographicNode(label="진단", desc="수집 범위 확정"),
            InfographicNode(label="구축", desc="표준 설비 연결"),
            InfographicNode(label="확산", desc="전 라인 적용"),
        )
    return tuple(
        InfographicNode(
            label=f"단계{i + 1}",
            desc=f"설명 문구 {i + 1}",
            figure=f"{12 + i}%",
            before=f"{4 + i}.6%",
            after=f"{2 + i}.2%",
        )
        for i in range(5)
    )


def slide_for(archetype: str, weight: str, variant: str) -> AuthoredSlideInfographic:
    return AuthoredSlideInfographic(
        index=4,
        archetype=archetype,  # type: ignore[arg-type]
        weight=weight,  # type: ignore[arg-type]
        takeaway="검증 후 개통한다",
        nodes=_nodes(variant),
        emphasis=2 if variant == "rich" else None,
        density="high" if variant == "rich" else "medium",  # type: ignore[arg-type]
    )


def case_key(archetype: str, mode: str | None, weight: str, variant: str) -> str:
    return f"{archetype}|{mode or 'default'}|{weight}|{variant}"


def baseline_cases():
    for archetype in FIGURE_ARCHETYPES:
        for mode in MODES:
            for weight in WEIGHTS:
                for variant in VARIANTS:
                    yield case_key(archetype, mode, weight, variant), archetype, mode, weight, variant


def full_text_name(archetype: str, mode: str) -> str:
    return f"marker_{archetype}_{mode}.txt"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digests() -> dict[str, dict[str, str]]:
    return json.loads((BASELINE_DIR / "digests.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("archetype", FIGURE_ARCHETYPES)
@pytest.mark.parametrize("mode", FULL_TEXT_MODES)
def test_16x9_marker_is_byte_identical_to_the_captured_text(archetype: str, mode: str) -> None:
    expected = (BASELINE_DIR / full_text_name(archetype, mode)).read_bytes().decode("utf-8")
    slide = slide_for(archetype, "hero", "rich")
    assert infographic_marker(slide, page_number_mode=mode) == expected
    assert (
        infographic_marker(slide, page_number_mode=mode, canvas_height_px=720) == expected
    )


def test_every_16x9_marker_and_contract_matches_its_captured_digest() -> None:
    captured = _digests()
    cases = list(baseline_cases())
    assert set(captured) == {key for key, *_ in cases}
    for key, archetype, mode, weight, variant in cases:
        slide = slide_for(archetype, weight, variant)
        for canvas in ({}, {"canvas_height_px": 720}):
            assert digest(infographic_marker(slide, page_number_mode=mode, **canvas)) == captured[key]["marker"], key
            assert digest(slot_contract(slide, page_number_mode=mode, **canvas)) == captured[key]["slot_contract"], key


@pytest.mark.parametrize(("width", "height"), OTHER_SIZES)
def test_the_bottom_line_is_the_canvas_height_minus_the_footer_band(width: int, height: int) -> None:
    assert content_bottom_px("template", canvas_height_px=height) == height - 72
    assert content_bottom_px("companyStandard", canvas_height_px=height) == height - 72
    assert content_bottom_px("none", canvas_height_px=height) == height - 32
    assert content_bottom_px(None, canvas_height_px=height) == height - 72


def test_720_high_lines_are_the_measured_ones() -> None:
    assert content_bottom_px("template") == 648
    assert content_bottom_px("none") == 688


@pytest.mark.parametrize(("width", "height"), [s for s in OTHER_SIZES if s[1] != 720])
@pytest.mark.parametrize("archetype", ("process", "hierarchy", "kpi", "gauge", "scene"))
@pytest.mark.parametrize("mode", ("template", "none"))
def test_a_non_720_contract_moves_only_the_bottom_line(
    width: int, height: int, archetype: str, mode: str
) -> None:
    slide = slide_for(archetype, "standard", "plain")
    contract = slot_contract(slide, page_number_mode=mode, canvas_height_px=height)
    marker = infographic_marker(slide, page_number_mode=mode, canvas_height_px=height)
    bottom = content_bottom_px(mode, canvas_height_px=height)
    for text in (contract, marker):
        for stale in ("y=648px", "y=688px", "is 648 minus", "is 688 minus"):
            assert stale not in text
        assert f"y={bottom}px" in text
    ceiling = LOW_HEIGHT_MAX_PX.get(archetype)
    if ceiling is None:
        assert f"height is {bottom} minus the container's own top" in contract
        assert "shorter than 430px" in contract
        assert "stopped 76px above the line" in contract
        assert "lowest text 108px above it" in contract
    else:
        assert f"height of exactly {ceiling}px" in contract
        assert "the 430px floor" in contract


@pytest.mark.parametrize(("width", "height"), [s for s in OTHER_SIZES if s[1] != 720])
@pytest.mark.parametrize("archetype", FIGURE_ARCHETYPES)
def test_a_non_720_contract_is_the_720_one_with_only_the_line_moved(
    width: int, height: int, archetype: str
) -> None:
    slide = slide_for(archetype, "hero", "rich")
    for mode in ("template", "none"):
        base = slot_contract(slide, page_number_mode=mode)
        moved = slot_contract(slide, page_number_mode=mode, canvas_height_px=height)
        old, new = content_bottom_px(mode), content_bottom_px(mode, canvas_height_px=height)
        assert moved == base.replace(f"y={old}px", f"y={new}px").replace(
            f"height is {old} minus", f"height is {new} minus"
        )


def test_a_720_high_ultrawide_keeps_the_16x9_contract() -> None:
    slide = slide_for("process", "hero", "rich")
    assert slot_contract(slide, page_number_mode="template", canvas_height_px=720) == slot_contract(
        slide, page_number_mode="template"
    )
