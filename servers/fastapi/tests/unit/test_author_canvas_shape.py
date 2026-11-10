"""Canvas-aware authoring prompts: 16:9 frozen byte for byte, other canvases rewritten.

Design: ``docs/aspect-ratio-expansion-design-20260928.md`` §3 and §3.1.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from models.authored_header_contract import default_header_contract
from models.authored_vnext.capability_registry import prompt_capability_clause
from services.document_canvas_generation import generation_font_floor
from services.generation_core.drivers.svg.canvas_brief import reference_caveat
from utils.authored_fragmentation_gate import fragmentation_prompt_clause
from utils.authored_style_exemplars import exemplar_block
from utils.authored_styles import resolve_authored_style
from utils.llm_calls.author_canvas_shape import (
    BASELINE_TOKENS,
    aspect_ratio_label,
    classify_canvas_shape,
    composition_clause,
)
from utils.llm_calls.author_deck_tokens import deck_tokens_block
from utils.llm_calls.author_prompt_profile import (
    AUGUST,
    CURRENT,
    PROFILE_NAMES,
    AuthorPromptProfile,
    author_prompt_profile_scope,
)
from services.generation_core.authoring.author_slide import Brand, build_design_system
from services.generation_core.authoring.author_slide_svg import free_composition_canvas_clause, svg_author_rules

BASELINE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "prompt_16x9_baseline"
MODES = ("template", "companyStandard", "none")
OTHER_SIZES = ((1188, 840), (840, 1188), (720, 1280), (720, 1040), (720, 720), (1680, 720))
COMPOSITION_MARKER = " COMPOSITION (authoritative for this deck)"

def _old_portrait_replacements(h):
    """The pre-change portrait table, kept verbatim as the regression oracle."""
    return (
        ("single 16:9 slide", f"single {'9:13' if h == 1040 else '9:16'} portrait slide"),
        ("1280x720px", f"720x{h}px"),
        ("1280x720", f"720x{h}"),
        ("y=648px", f"y={h - 72}px"),
        ("y=688px", f"y={h - 32}px"),
        ("y=749", f"y={h + 29}"),
        ("an element is never 1280px wide", "an element is never 720px wide"),
        ("a 72px inset on each side leaves 1136px", "a 72px inset on each side leaves 576px"),
        ("width:1280px", "width:720px"),
    )


def _old_portrait_clause(h):
    return (
        "\n- PORTRAIT COMPOSITION (authoritative for this deck): use the full "
        f"720x{h} canvas without rotation or landscape scaling. Place cover titles "
        "and subtitles toward the top; use one readable vertical body column; stack "
        "comparisons and three-card groups vertically; keep images and charts within "
        "the 592px safe content width with captions below; and compose closing "
        "messages and next actions as a vertical sequence.\n"
    )


@pytest.fixture(autouse=True)
def _pinned_knobs(monkeypatch):
    """The fixtures were captured with no AUTHORED_* knob set."""
    import os

    for key in list(os.environ):
        if key.startswith("AUTHORED_"):
            monkeypatch.delenv(key, raising=False)


def _brand() -> Brand:
    return Brand(topic="2026 디지털 전환 전략", language="Korean", primary="#2563EB", fonts="Noto Sans KR")


def _style(name: str):
    return None if name == "default" else resolve_authored_style(name)


def _design(profile, style="default", mode="template", header=False, size=(1280, 720)) -> str:
    with author_prompt_profile_scope(AuthorPromptProfile(name=profile)):
        return build_design_system(
            _brand(),
            _style(style),
            page_number_mode=mode,
            header_contract=default_header_contract() if header else None,
            canvas_width_px=size[0],
            canvas_height_px=size[1],
        )


def _fixture(name: str) -> str:
    # Git may check .txt files out with CRLF on Windows; the prompts carry none.
    return (BASELINE_DIR / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


DESIGN_CASES = [
    (profile, style, mode, header)
    for profile in PROFILE_NAMES
    for style in ("default", "cobalt-editorial")
    for mode in MODES
    for header in (False, True)
]


def _case_name(profile, style, mode, header) -> str:
    return f"design_{profile}_{style}_{mode}_{'header' if header else 'noheader'}.txt"


# --- 1. classification ------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "shape"),
    [
        ((1280, 720), "baseline"),
        ((1188, 840), "wide-landscape"),
        ((1920, 1080), "wide-landscape"),
        ((840, 1188), "portrait"),
        ((720, 1280), "portrait"),
        ((720, 1040), "portrait"),
        ((720, 720), "square"),
        ((1680, 720), "ultrawide"),
    ],
)
def test_canvas_shape_classification(size, shape):
    assert classify_canvas_shape(*size) == shape


@pytest.mark.parametrize(
    ("size", "label"),
    [((720, 1280), "9:16"), ((720, 1040), "9:13"), ((1188, 840), "1188x840"), ((1280, 720), "16:9"),
     ((720, 720), "1:1"), ((1680, 720), "7:3")],
)
def test_aspect_ratio_label(size, label):
    assert aspect_ratio_label(*size) == label


# --- 2. token coverage ------------------------------------------------------

#: Which rewrite sources each profile's 16:9 text carries. ``august-0819``
#: predates the width/overflow paragraphs, so it only carries the first five.
EXPECTED_TOKENS = {
    CURRENT: set(BASELINE_TOKENS),
    AUGUST: {"single 16:9 slide", "1280x720px", "1280x720", "y=648px", "y=688px"},
}


def test_every_rewrite_token_is_present_in_the_profile_rules():
    assert set(EXPECTED_TOKENS) == set(PROFILE_NAMES)
    covered = set()
    for profile in PROFILE_NAMES:
        text = "".join(_design(profile, mode=mode) for mode in MODES)
        present = {token for token in BASELINE_TOKENS if token in text}
        assert present == EXPECTED_TOKENS[profile], profile
        covered |= present
    assert covered == set(BASELINE_TOKENS)


# --- 3. 16:9 byte identity --------------------------------------------------


@pytest.mark.parametrize(("profile", "style", "mode", "header"), DESIGN_CASES)
def test_16x9_design_system_is_byte_identical(profile, style, mode, header):
    assert _design(profile, style, mode, header) == _fixture(_case_name(profile, style, mode, header))


@pytest.mark.parametrize("snapshot", [{}, {"canvas": {"width": 1280, "height": 720}}])
def test_16x9_svg_rules_are_byte_identical(snapshot):
    floor = generation_font_floor(None, None)
    assert svg_author_rules(snapshot, floor) == _fixture("svg_rules.txt")


def test_every_baseline_fixture_is_exercised():
    names = {_case_name(*case) for case in DESIGN_CASES} | {"svg_rules.txt"}
    assert {path.name for path in BASELINE_DIR.iterdir()} == names


# --- 4. other canvases ------------------------------------------------------


@pytest.mark.parametrize("size", OTHER_SIZES)
@pytest.mark.parametrize("profile", PROFILE_NAMES)
@pytest.mark.parametrize("header", [False, True])
def test_non_baseline_design_system_names_only_its_canvas(size, profile, header):
    w, h = size
    for mode in MODES:
        text = _design(profile, "default", mode, header, size)
        assert "1280x720" not in text
        # 720x1280 legitimately names its own height as 1280px in these two places.
        own = text.replace(f"{w}x{h}px", "").replace(f"on a {h}px canvas", "")
        assert "1280px" not in own
        assert "1136px" not in text
        assert f"{w}x{h}px" in text
        bottom_line = {"template": f"y={h - 72}px", "none": f"y={h - 32}px"}.get(mode)
        assert bottom_line is None or bottom_line in text
        assert text.count(COMPOSITION_MARKER) == 1
        assert composition_clause(w, h) in text
        assert f"- {classify_canvas_shape(w, h).upper()}{COMPOSITION_MARKER}" in text


# --- 5. portrait regression against the old table ---------------------------


def _tail(profile) -> str:
    if profile == AUGUST:
        return ""
    return (prompt_capability_clause() + fragmentation_prompt_clause()
            + deck_tokens_block(None, _brand()) + exemplar_block(None))


@pytest.mark.parametrize("h", [1280, 1040])
@pytest.mark.parametrize(("profile", "mode", "header"),
                         [(p, m, hd) for p in PROFILE_NAMES for m in MODES for hd in (False, True)])
def test_portrait_output_matches_the_old_table_except_the_two_fixed_misses(h, profile, mode, header):
    baseline = _fixture(_case_name(profile, "default", mode, header))
    tail = _tail(profile)
    assert baseline.endswith(tail)
    old_rules = baseline[: len(baseline) - len(tail)]
    for old, new in _old_portrait_replacements(h):
        old_rules = old_rules.replace(old, new)
    expected = (old_rules + _old_portrait_clause(h) + tail).replace(
        "on a 720px canvas", f"on a {h}px canvas"
    ).replace("1136px", "576px")
    assert _design(profile, "default", mode, header, (720, h)) == expected


# --- 6. svg rules -----------------------------------------------------------


@pytest.mark.parametrize("size", OTHER_SIZES)
def test_svg_rules_carry_the_shape_clause(size):
    w, h = size
    rules = svg_author_rules({"canvas": {"width": w, "height": h}}, 16)
    # The svg author gets the free-composition clause, not the shape-class one.
    assert rules.endswith(free_composition_canvas_clause(w, h) + reference_caveat(w, h))
    assert rules.count(COMPOSITION_MARKER) == 0


def test_svg_rules_carry_no_clause_for_the_baseline():
    assert COMPOSITION_MARKER not in svg_author_rules({"canvas": {"width": 1280, "height": 720}}, 16)


# --- 7. deck wiring: the DOCUMENT CANVAS clause -----------------------------


def _canvas(w, h):
    return SimpleNamespace(width_px=w, height_px=h,
                           safe_area=SimpleNamespace(x=64, y=64, width=w - 128, height=h - 128))
