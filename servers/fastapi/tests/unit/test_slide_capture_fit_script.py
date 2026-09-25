"""The fit fragment must carry the detectors' own numbers, and be valid JS.

The fragment is written once in Python and executed in Chrome, so nothing but a
test stands between an edited detector constant and a fitter that silently fits
to a stale line.  These checks are the parity the design asks for (section 7):
the numbers, the style-application contract, and that the text really parses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from services.authored_vnext.capability_registry import AUTHORED_FONT_FLOOR_PX
from utils.authored_dom_footer_zone import (
    NUMBERED_CONTENT_BOTTOM_PX,
    UNNUMBERED_CONTENT_BOTTOM_PX,
)
from utils.authored_dom_text_escape import ESCAPE_TOLERANCE_PX
from utils.slide_capture_fit_in_render_script import (
    DEFAULT_FIT_TIMEOUT_MS,
    FIT_BISECTION_STEPS,
    FIT_CLASS,
    FIT_MARGIN_PX,
    FIT_TIME_BUDGET_FRACTION,
    FIT_VERSION,
    MAX_FIT_CARDS,
    MAX_FIT_LAYOUTS,
    fit_constants,
    fit_script_fragment,
)

_MODULE = Path(__file__).resolve().parents[2] / "utils" / "slide_capture_fit_in_render_script.py"


def test_public_interface_is_the_agreed_one() -> None:
    assert FIT_CLASS == "codex-fit-v1"
    assert FIT_VERSION == "authored-in-render-fit/v1"
    assert FIT_MARGIN_PX == 6
    assert MAX_FIT_CARDS == 16
    assert MAX_FIT_LAYOUTS == 160
    assert FIT_TIME_BUDGET_FRACTION == 0.10


def test_constants_come_from_the_detectors_that_define_them() -> None:
    constants = fit_constants()
    assert constants["tolerance_px"] == ESCAPE_TOLERANCE_PX == 2.0
    assert constants["margin_px"] == FIT_MARGIN_PX == 6
    assert constants["numbered_content_bottom_px"] == NUMBERED_CONTENT_BOTTOM_PX == 648.0
    assert (
        constants["unnumbered_content_bottom_px"]
        == UNNUMBERED_CONTENT_BOTTOM_PX
        == 688.0
    )
    assert constants["font_floor_px"] == AUTHORED_FONT_FLOOR_PX == 12.0
    assert constants["max_cards"] == MAX_FIT_CARDS
    assert constants["max_layouts"] == MAX_FIT_LAYOUTS
    assert constants["time_budget_fraction"] == FIT_TIME_BUDGET_FRACTION
    assert constants["default_timeout_ms"] == DEFAULT_FIT_TIMEOUT_MS
    assert constants["bisection_steps"] == FIT_BISECTION_STEPS
    assert constants["fit_class"] == FIT_CLASS
    assert constants["version"] == FIT_VERSION


@pytest.mark.parametrize("key", sorted(fit_constants()))
def test_every_constant_is_interpolated_into_the_fragment(key: str) -> None:
    rendered = json.dumps({key: fit_constants()[key]}, sort_keys=True)[1:-1]
    assert rendered in fit_script_fragment()


def test_fragment_carries_the_card_predicate_inputs() -> None:
    constants = fit_constants()
    assert "transparent" in constants["transparent_backgrounds"]  # type: ignore[operator]
    assert "rgba(0,0,0,0)" in constants["transparent_backgrounds"]  # type: ignore[operator]
    assert "img" in constants["painted_tags"]  # type: ignore[operator]
    assert "svg" in constants["painted_tags"]  # type: ignore[operator]


def test_fragment_declares_only_the_fit_object() -> None:
    fragment = fit_script_fragment()
    top_level = [line for line in fragment.splitlines() if line and not line[0].isspace()]
    assert top_level == ["const __codexFit = {", "};"]
    for member in ("reset()", "apply(pass)", "report()"):
        name = member.split("(")[0]
        assert f"  {name}(" in fragment


def test_fragment_is_plain_javascript_without_brace_doubling() -> None:
    fragment = fit_script_fragment()
    assert "{{" not in fragment and "}}" not in fragment
    assert "<script" not in fragment and "nonce" not in fragment
    assert fragment.count("{") == fragment.count("}")
    assert fragment.count("(") == fragment.count(")")


def test_fragment_parses_as_javascript() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so the fragment cannot be parsed here")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fit-fragment.js"
        path.write_text(
            "(() => {\n" + fit_script_fragment() + "\nreturn __codexFit;\n})();\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True
        )
    assert result.returncode == 0, result.stderr


def test_entry_is_the_detector_threshold_and_the_target_is_the_margin() -> None:
    """Rev 4: engage only a card the detector would flag, then fit to the margin.

    The fragment is JavaScript, so this is a source-level assertion: the entry test
    must read the interpolated ``ESCAPE_TOLERANCE_PX`` against the margin-0 escape,
    measured with the footer line so an escape ``reconcile_footer_zone_reports``
    would hand to the footer detector does not engage the card either, and the old
    margin-based entry (``if (escape <= 0) return false``) must be gone, or the
    fitter would go on shrinking cards no detector complains about.
    """

    fragment = fit_script_fragment()
    assert f'"tolerance_px": {json.dumps(ESCAPE_TOLERANCE_PX)}' in fragment
    assert "const escapePx = this._escape(card, leaves, 0, contentLine);" in fragment
    assert "if (escapePx <= this.C.tolerance_px) return false;" in fragment
    assert "const bound = footerLine > 0 && rect.bottom > footerLine ? footerLine : Infinity;" in fragment
    # The target stays the margin, measured over every leaf: entry and target are
    # separate questions, and only entry is the detector's.
    assert "let escape = this._escape(card, leaves, this.C.margin_px);" in fragment
    assert "if (escape <= 0) return false;" not in fragment
    # The report still carries the raw, detector-measured escape.
    assert "escape_px: this._round(escapePx, 2)" in fragment


def test_styles_are_appended_as_strings_never_through_cssom() -> None:
    fragment = fit_script_fragment()
    assert "setAttribute('style'" in fragment
    assert ".style.fontSize" not in fragment
    assert ".style.setProperty" not in fragment
    assert ".style.lineHeight" not in fragment
    # The append itself: the snapshot attribute, a separator, the declarations.
    assert (
        "node.setAttribute('style', (snap.style ? snap.style + ';' : '') + declarations)"
        in fragment
    )


def test_module_stays_within_the_file_size_the_design_allows() -> None:
    assert len(_MODULE.read_text(encoding="utf-8").splitlines()) <= 350
