"""SVG style-brief canvas rewrite: 16:9 untouched, every other canvas free of 16:9 anchors.

Design: ``docs/svg-canvas-brief-rewrite-and-experiment-plan-20260929.md`` §1.3-1.7.
"""

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.generation_core.drivers.svg.canvas_brief import (
    SVG_CANVAS_BRIEF_VERSION,
    adapt_brief_line,
    reference_caveat,
    svg_brief_replacements,
)
from utils.authored_styles import resolve_authored_style
from utils.llm_calls.author_canvas_shape import aspect_ratio_label, composition_clause
from services.generation_core.authoring.author_slide_svg import svg_author_rules
from utils.svg_style_adapter import adapt_svg_style

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = FASTAPI_ROOT / "tests" / "fixtures" / "svg_design_16x9_baseline"
ADMITTED_IDS = sorted(
    path.stem for path in (FASTAPI_ROOT / "authored_styles").glob("*.yaml") if path.stem != "exact-osb-last"
)
SIZES = ((1188, 840), (840, 1188), (720, 720), (1680, 720), (720, 1280), (720, 1040))
ANCHOR = re.compile(r"1280x720|1280 by 720|16:9")

#: Anchors in each canonical style's 16:9 ``common_guidance``: occurrences of
#: ``1280x720`` (rewritten to ``{w}x{h}``) and of ``16:9`` (rewritten to the
#: aspect label). Measured 2026-09-29; edit deliberately when a YAML changes.
EXPECTED_ANCHORS = {
    "academic-edge": (1, 0), "architectural-portfolio": (1, 0), "botanical-journal": (1, 0),
    "broadside": (1, 1), "chroma-fold": (0, 0), "clinical-precision": (1, 0), "cobalt-editorial": (1, 0),
    "cyber-ai": (1, 0), "default": (1, 0), "devax-si-proposal": (1, 1), "editorial-tritone": (1, 0),
    "exec-report": (1, 0), "flat-infographic": (1, 0), "geometric-mono": (1, 0), "groovy-70s": (1, 0),
    "hyundai-culture-center": (1, 0), "iso-blueprint": (1, 0), "kbs-company-proposal": (0, 0),
    "liquid-executive": (1, 0), "luxury-editorial": (1, 0), "minimal-vellum": (1, 0), "neo-grid-bold": (1, 0),
    "neon-venture": (1, 0), "osb-smart-banking": (0, 1), "prestige-gold": (1, 0), "prime-noir": (1, 0),
    "prismatic-tech": (1, 0), "project-launch": (1, 0), "scholars-journal": (1, 0), "science-sketch": (1, 0),
    "screenshot-blue": (0, 0), "signal": (2, 0), "silicon-refined": (1, 0), "sketch-note": (1, 0),
    "startup-aura": (1, 0), "strategic-navy": (1, 0), "structured-mint": (1, 0), "tech-blueprint": (1, 0),
    "visual-discovery": (1, 0),
}


@pytest.fixture(autouse=True)
def _pinned_knobs(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("AUTHORED_", "SVG_")):
            monkeypatch.delenv(key, raising=False)


_STYLES: dict[str, object] = {}
_SNAPSHOTS: dict[tuple[str, int, int], dict] = {}


def _style(style_id: str):
    # Resolution reloads the whole catalogue (about a second); cache per module.
    if style_id not in _STYLES:
        _STYLES[style_id] = resolve_authored_style(style_id)
    return _STYLES[style_id]


def _snapshot(style_id: str, size=(1280, 720), **kwargs) -> dict:
    if kwargs:
        return adapt_svg_style(_style(style_id), width=size[0], height=size[1], **kwargs)
    key = (style_id, *size)
    if key not in _SNAPSHOTS:
        _SNAPSHOTS[key] = adapt_svg_style(_style(style_id), width=size[0], height=size[1])
    return _SNAPSHOTS[key]


def _guidance(snapshot: dict) -> str:
    return "\n".join(line for lines in snapshot["common_guidance"].values() for line in lines)


# --- 1. no anchor survives off-16:9 -----------------------------------------


def test_expected_anchor_table_covers_every_canonical_style():
    canonical = {_style(style_id).id for style_id in ADMITTED_IDS}
    assert set(EXPECTED_ANCHORS) == canonical
    assert sum(1 for a, b in EXPECTED_ANCHORS.values() if a or b) == 36


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("style_id", ADMITTED_IDS)
def test_no_16x9_anchor_survives_in_common_guidance(style_id, size):
    w, h = size
    canonical = _style(style_id).id
    base = _guidance(_snapshot(style_id))
    assert (base.count("1280x720"), base.count("16:9")) == EXPECTED_ANCHORS[canonical]
    text = _guidance(_snapshot(style_id, size))
    assert not ANCHOR.search(text), (style_id, size)
    n_size, n_ratio = EXPECTED_ANCHORS[canonical]
    # Off-16:9 the size token appears once per rewritten 1280x720, plus once per
    # 16:9 anchor when the aspect label is the size itself (A4: 1188x840 and
    # 840x1188 reduce to 99:70 / 70:99, beyond the 32 cap of aspect_ratio_label).
    label_is_size = aspect_ratio_label(w, h) == f"{w}x{h}"
    assert text.count(f"{w}x{h}") == n_size + (n_ratio if label_is_size else 0)
    if n_ratio and not label_is_size:
        assert aspect_ratio_label(w, h) in text
    assert "  " not in text.replace("\n", "")


# --- 2. identity at 16:9 ----------------------------------------------------


def test_baseline_has_no_replacements():
    assert svg_brief_replacements(1280, 720) == ()


@pytest.mark.parametrize("style_id", ADMITTED_IDS)
def test_adapt_brief_line_is_the_identity_at_16x9(style_id):
    for lines in _snapshot(style_id)["common_guidance"].values():
        for line in lines:
            assert adapt_brief_line(line, 1280, 720) == line


# --- 3. overrides are untouched ---------------------------------------------


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("style_id", ADMITTED_IDS)
def test_overrides_are_byte_identical_across_sizes(style_id, size):
    base, other = _snapshot(style_id), _snapshot(style_id, size)
    assert json.dumps(other["overrides"], ensure_ascii=False) == json.dumps(base["overrides"], ensure_ascii=False)


@pytest.mark.parametrize("size", SIZES)
def test_the_two_override_anchors_survive_by_design(size):
    blue = json.dumps(_snapshot("screenshot-blue", size)["overrides"], ensure_ascii=False)
    assert "never output the 1280 by 720 preview size" in blue
    fold = json.dumps(_snapshot("chroma-fold", size)["overrides"], ensure_ascii=False)
    assert "using 1280x720 as the design reference" in fold


# --- 4. the replacement table -----------------------------------------------


@pytest.mark.parametrize(("size", "r4", "r5", "r6"), [
    ((720, 720), "Compose for a fixed 1:1 square canvas", "Work on a disciplined 1:1 square grid",
     "Use a 1:1 square corporate grid"),
    ((1680, 720), "Compose for a fixed 7:3 ultrawide canvas", "Work on a disciplined 7:3 ultrawide grid",
     "Use a 7:3 ultrawide corporate grid"),
    ((1188, 840), "Compose for a fixed 1188x840 canvas", "Work on a disciplined 1188x840 grid",
     "Use a 1188x840 corporate grid"),
    # 840x1188 reduces to 70:99, past aspect_ratio_label's 32 cap, so the label
    # is the size (the design table's "9:16" column belongs to 720x1280).
    ((840, 1188), "Compose for a fixed 840x1188 portrait canvas", "Work on a disciplined 840x1188 portrait grid",
     "Use a 840x1188 portrait corporate grid"),
    ((720, 1280), "Compose for a fixed 9:16 portrait canvas", "Work on a disciplined 9:16 portrait grid",
     "Use a 9:16 portrait corporate grid"),
])
def test_composition_anchor_rewrites(size, r4, r5, r6):
    w, h = size
    assert adapt_brief_line("Compose for a fixed 16:9 canvas", w, h) == r4
    assert adapt_brief_line("Work on a disciplined 16:9 grid", w, h) == r5
    assert adapt_brief_line("Use a wide 16:9 corporate grid", w, h) == r6
    assert adapt_brief_line("At 1280x720, body", w, h) == f"At {w}x{h}, body"
    assert adapt_brief_line("Scale on the 1280x720 canvas:", w, h) == f"Scale on the {w}x{h} canvas:"
    for _, new in svg_brief_replacements(w, h):
        assert "  " not in new


@pytest.mark.parametrize(("size", "expected"), [
    ((720, 720), "At a 720x720 square canvas, "), ((1680, 720), "At a 1680x720 ultrawide canvas, "),
    ((1188, 840), "At a 1188x840 canvas, "), ((840, 1188), "At a 840x1188 portrait canvas, "),
])
def test_type_size_anchor_rewrite_keeps_px_values(size, expected):
    line = "At a 1280x720 canvas, target body 22-28px and labels at least 16px"
    assert adapt_brief_line(line, *size) == expected + "target body 22-28px and labels at least 16px"


@pytest.mark.parametrize("size", SIZES)
def test_residual_tokens_are_caught_by_the_safety_net(size):
    w, h = size
    assert "1280x720" not in adapt_brief_line("a fresh 1280x720 anchor", w, h)
    assert "1280 by 720" not in adapt_brief_line("a fresh 1280 by 720 anchor", w, h)
    assert "16:9" not in adapt_brief_line("a fresh 16:9 anchor", w, h)


# --- 5. digests and provenance ----------------------------------------------


@pytest.mark.parametrize("style_id", ADMITTED_IDS)
def test_digest_moves_off_16x9_only_and_is_stable(style_id):
    base = _snapshot(style_id)
    assert "canvas_brief_rewrite" not in base["provenance"]
    for size in SIZES:
        other = _snapshot(style_id, size)
        assert other["provenance"]["brief_digest"] == base["provenance"]["brief_digest"]
        assert other["provenance"]["canvas_brief_rewrite"] == SVG_CANVAS_BRIEF_VERSION
        assert other["digest"] != base["digest"], (style_id, size)
    again = adapt_svg_style(_style(style_id), width=1188, height=840)
    assert again["digest"] == _snapshot(style_id, (1188, 840))["digest"]


# --- 6. rules -----------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_svg_rules_end_with_the_clause_and_the_caveat(size):
    w, h = size
    rules = svg_author_rules({"canvas": {"width": w, "height": h}}, 16)
    assert rules.endswith(composition_clause(w, h) + reference_caveat(w, h))
    assert f"Fill the {w}x{h} canvas" in rules
    assert rules.count("REFERENCE IMAGES") == 1


def test_no_caveat_at_16x9():
    assert reference_caveat(1280, 720) == ""
    assert "REFERENCE IMAGES" not in svg_author_rules({"canvas": {"width": 1280, "height": 720}}, 16)


# --- 7. other sections pass through -------------------------------------------


@pytest.mark.parametrize("style_id", ["default", "osb-smart-banking", "hyundai-culture-center"])
def test_mapped_guidance_and_master_template_are_unchanged_across_sizes(style_id):
    master = {"style_id": "master:acme-1", "tokens": {"accent": "#7A1FA2"},
              "brief": "Compose for a fixed 16:9 canvas"}
    request = SimpleNamespace(primary_color=None, fonts=None, wordmark=None)
    base = _snapshot(style_id, request=request, master_rules=master, master_pin={"masterId": "master:acme-1"})
    for size in SIZES:
        other = _snapshot(style_id, size, request=request, master_rules=master,
                          master_pin={"masterId": "master:acme-1"})
        assert other["mapped_guidance"] == base["mapped_guidance"]
        assert other["master_template"] == base["master_template"] == master
        assert other["master_pin"] == base["master_pin"]
