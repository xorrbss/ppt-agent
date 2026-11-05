"""The default 1280x720 canvas is named once, in ``models.canvas_defaults``.

docs/refactoring-audit-20260929.md section 4, hardcoding item 1: every canvas
fallback and every "is this the default landscape deck" branch resolves through
the helpers there (re-exported by ``services.authored_canvas``), never through a
restated literal, so a non-16:9 deck cannot silently take the wrong branch. The
scan below is the ratchet that keeps it so across ``models``, ``services``,
``utils`` and ``api``.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.authored_header_metrics import SLIDE_HEIGHT_PX, SLIDE_WIDTH_PX
from models.authored_vnext.master.contract import CanvasContract, Rect
from models import canvas_defaults
from services import authored_canvas
from services.authored_canvas import (
    DEFAULT_CANVAS_ASPECT_RATIO,
    DEFAULT_CANVAS_HEIGHT_PX,
    DEFAULT_CANVAS_SIZE_PX,
    DEFAULT_CANVAS_WIDTH_PX,
    LANDSCAPE_ASPECT_RATIO,
    PORTRAIT_ASPECT_RATIO,
    canvas_metadata_for_aspect_ratio,
    canvas_or_default,
    canvas_render_kwargs,
    canvas_size_for_aspect_ratio,
    default_canvas_size,
    is_default_landscape,
    is_default_landscape_canvas,
)

FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def test_default_canvas_is_the_landscape_preset():
    assert DEFAULT_CANVAS_ASPECT_RATIO == LANDSCAPE_ASPECT_RATIO
    assert DEFAULT_CANVAS_SIZE_PX == (DEFAULT_CANVAS_WIDTH_PX, DEFAULT_CANVAS_HEIGHT_PX)
    assert default_canvas_size() == canvas_size_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO)
    landscape = canvas_metadata_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO)
    assert (landscape.width_px, landscape.height_px) == default_canvas_size()
    assert canvas_size_for_aspect_ratio(PORTRAIT_ASPECT_RATIO) == (DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX)


def test_models_layer_defaults_agree_with_the_service_default():
    """``CanvasContract`` and the header metrics read the same leaf definition."""

    contract = CanvasContract(safe_area=Rect(x=0.0, y=0.0, width=1.0, height=1.0))
    assert (contract.width_px, contract.height_px) == default_canvas_size()
    assert (SLIDE_WIDTH_PX, SLIDE_HEIGHT_PX) == default_canvas_size()


def test_service_module_re_exports_the_models_leaf():
    """``services.authored_canvas`` re-exports ``models.canvas_defaults`` unchanged,
    so utils modules (which may not import services) and services callers agree."""

    for name in canvas_defaults.__all__:
        assert getattr(authored_canvas, name) is getattr(canvas_defaults, name), name


def test_canvas_or_default_reads_width_and_height_or_falls_back():
    assert canvas_or_default(None) == default_canvas_size()
    assert canvas_or_default(object()) == default_canvas_size()
    assert canvas_or_default(SimpleNamespace(width_px=720, height_px=1280)) == (720, 1280)
    assert canvas_or_default(SimpleNamespace(width_px="720", height_px=1040.0)) == (720, 1040)


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (DEFAULT_CANVAS_WIDTH_PX, DEFAULT_CANVAS_HEIGHT_PX, True),
        (float(DEFAULT_CANVAS_WIDTH_PX), float(DEFAULT_CANVAS_HEIGHT_PX), True),
        (DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX, False),
        (DEFAULT_CANVAS_WIDTH_PX, DEFAULT_CANVAS_HEIGHT_PX + 1, False),
        (None, None, False),
        (str(DEFAULT_CANVAS_WIDTH_PX), str(DEFAULT_CANVAS_HEIGHT_PX), False),
    ],
)
def test_is_default_landscape_is_exact_equality(width, height, expected):
    assert is_default_landscape(width, height) is expected


def test_is_default_landscape_canvas_treats_absence_as_default():
    assert is_default_landscape_canvas(None) is True
    assert is_default_landscape_canvas(canvas_metadata_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO)) is True
    assert is_default_landscape_canvas(canvas_metadata_for_aspect_ratio(PORTRAIT_ASPECT_RATIO)) is False


def test_canvas_render_kwargs_is_empty_only_on_the_default_canvas():
    assert canvas_render_kwargs(DEFAULT_CANVAS_WIDTH_PX, DEFAULT_CANVAS_HEIGHT_PX) == {}
    assert canvas_render_kwargs(*canvas_or_default(None)) == {}
    assert canvas_render_kwargs(720, 1280) == {"width_px": 720, "height_px": 1280}


# ---------------------------------------------------------------------------
# Literal ratchet
# ---------------------------------------------------------------------------

_CANVAS_LITERALS = {"1280", "720", "1280.0", "720.0"}

#: Whole directories scanned for restated default-canvas literals. ``models``
#: and ``utils`` cannot import ``services``; they resolve the default through
#: the ``models.canvas_defaults`` leaf instead.
_SCANNED_DIRECTORIES = ("models", "services", "utils", "api")

#: Files whose 1280/720 mean something other than the default deck canvas.
_ALLOWED_LITERAL_FILES = {
    # The single definition.
    "models/canvas_defaults.py": "canonical default canvas",
    # The other presets share the 720 short edge with the default.
    "services/authored_canvas.py": "canonical preset table",
    # An upper bound on a style sample's canvas edge, not a canvas branch.
    "utils/authored_style_samples.py": "sample canvas upper bound",
    # An upper bound on a decorative panel's CSS height, not a canvas branch.
    "services/authored_decoration_panel_sheen.py": "panel height upper bound",
    # An upper bound on native-object margins in points.
    "services/generation_core/native_objects/payload.py": "margin upper bound",
    # Pixel bounds of the vision-QA contact sheet (an image budget, not a deck canvas).
    "services/generation_core/quality/vision_contact_sheet.py": "contact sheet pixel bound",
    # 720 is a character budget for repair witnesses, not a canvas edge.
    "services/ppt_writing_visible.py": "text character budget",
}


def _numeric_literals(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    found: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NUMBER and token.string in _CANVAS_LITERALS:
            found.append((token.start[0], token.line.strip()))
    return found


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for directory in _SCANNED_DIRECTORIES:
        files.extend(sorted((FASTAPI_ROOT / directory).rglob("*.py")))
    return files


def test_no_default_canvas_literal_outside_the_canonical_module():
    offenders: list[str] = []
    for path in _scanned_files():
        relative = path.relative_to(FASTAPI_ROOT).as_posix()
        if relative in _ALLOWED_LITERAL_FILES:
            continue
        for line_number, line in _numeric_literals(path):
            offenders.append(f"{relative}:{line_number}: {line}")
    assert offenders == [], "restated default canvas literal:\n" + "\n".join(offenders)


def test_allowed_literal_files_still_need_their_exemption():
    for relative in _ALLOWED_LITERAL_FILES:
        path = FASTAPI_ROOT / relative
        assert path.is_file(), relative
        assert _numeric_literals(path), f"{relative} no longer carries the literal; drop its exemption"


def test_canonical_module_exports_the_helpers():
    for name in (
        "DEFAULT_CANVAS_SIZE_PX",
        "canvas_or_default",
        "canvas_render_kwargs",
        "default_canvas_size",
        "is_default_landscape",
        "is_default_landscape_canvas",
    ):
        assert name in authored_canvas.__all__
