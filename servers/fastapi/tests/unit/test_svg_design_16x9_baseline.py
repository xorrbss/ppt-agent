"""16:9 svg design-system goldens: one frozen ``design`` string per canonical style.

Design: ``docs/svg-canvas-brief-rewrite-and-experiment-plan-20260929.md`` §1.6.
Recapture with ``scripts/capture_svg_design_baseline.py`` and review the diff.
"""

import json
import os
from pathlib import Path

import pytest

from utils.authored_styles import AuthoredExactReconstructionUnavailableError, resolve_authored_style
from utils.llm_calls.author_slide_svg import svg_design_text
from utils.svg_style_adapter import adapt_svg_style

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = FASTAPI_ROOT / "tests" / "fixtures" / "svg_design_16x9_baseline"
STYLES_DIR = FASTAPI_ROOT / "authored_styles"
YAML_IDS = sorted(path.stem for path in STYLES_DIR.glob("*.yaml"))
EXACT_IDS = {"exact-osb-last"}


@pytest.fixture(autouse=True)
def _pinned_knobs(monkeypatch):
    """The fixtures were captured with no AUTHORED_*/SVG_* knob set."""
    for key in list(os.environ):
        if key.startswith(("AUTHORED_", "SVG_")):
            monkeypatch.delenv(key, raising=False)


def _fixture(style_id: str) -> str:
    # Git may check .txt files out with CRLF on Windows; the strings carry none.
    return (BASELINE_DIR / f"design_{style_id}.txt").read_bytes().decode("utf-8").replace("\r\n", "\n")


def _canonical_ids() -> set[str]:
    ids = set()
    for style_id in YAML_IDS:
        if style_id in EXACT_IDS:
            continue
        ids.add(resolve_authored_style(style_id).id)
    return ids


def test_the_catalogue_has_the_expected_shape():
    assert len(YAML_IDS) == 45
    assert EXACT_IDS <= set(YAML_IDS)


@pytest.mark.parametrize("style_id", YAML_IDS)
def test_16x9_design_is_byte_identical(style_id):
    if style_id in EXACT_IDS:
        with pytest.raises(AuthoredExactReconstructionUnavailableError):
            resolve_authored_style(style_id)
        return
    style = resolve_authored_style(style_id)
    snapshot = adapt_svg_style(style, width=1280, height=720)
    assert svg_design_text(snapshot) == _fixture(style.id)


@pytest.mark.parametrize("style_id", sorted(set(YAML_IDS) - EXACT_IDS))
def test_16x9_digest_matches_fixture(style_id):
    style = resolve_authored_style(style_id)
    snapshot = adapt_svg_style(style, width=1280, height=720)
    pinned = json.loads(_fixture(style.id))["digest"]
    assert snapshot["digest"] == pinned, f"style_digest moved for {style.id}"


def test_every_baseline_fixture_is_exercised():
    expected = {f"design_{style_id}.txt" for style_id in _canonical_ids()}
    assert {path.name for path in BASELINE_DIR.iterdir()} == expected
    assert len(expected) == 39
