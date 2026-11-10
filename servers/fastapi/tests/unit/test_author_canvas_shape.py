"""Canvas-aware authoring prompts: 16:9 frozen byte for byte, other canvases rewritten.

Design: ``docs/aspect-ratio-expansion-design-20260928.md`` §3 and §3.1.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.document_canvas_generation import generation_font_floor
from services.generation_core.drivers.svg.canvas_brief import reference_caveat
from utils.authored_styles import resolve_authored_style
from utils.llm_calls.author_canvas_shape import (
    aspect_ratio_label,
    classify_canvas_shape,
)
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


def _style(name: str):
    return None if name == "default" else resolve_authored_style(name)


def _fixture(name: str) -> str:
    # Git may check .txt files out with CRLF on Windows; the prompts carry none.
    return (BASELINE_DIR / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


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


# --- 3. 16:9 byte identity --------------------------------------------------


@pytest.mark.parametrize("snapshot", [{}, {"canvas": {"width": 1280, "height": 720}}])
def test_16x9_svg_rules_are_byte_identical(snapshot):
    floor = generation_font_floor(None, None)
    assert svg_author_rules(snapshot, floor) == _fixture("svg_rules.txt")


# --- 4. other canvases ------------------------------------------------------


# --- 5. portrait regression against the old table ---------------------------


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
