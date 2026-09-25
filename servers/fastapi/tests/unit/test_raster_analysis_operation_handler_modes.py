"""Mode-selection unit coverage for the raster analysis operation handler.

Full ``execute()`` coverage requires a live workspace/session stack that is
exercised elsewhere; these tests isolate the two pure dispatch helpers that
decide how "auto_redesign" behaves: which analyzer runs, and which
approval_method makes a page eligible for automatic approval.

Depends on agent A's ``analyze_redesign_pptx`` (services/source_content/
raster_analysis.py) existing; skips cleanly until it lands.
"""
from __future__ import annotations

import pytest

from services.source_content.raster_analysis import (
    analyze_auto_preserve_pptx, analyze_raster_pptx,
)

try:
    from services.source_content.raster_analysis import analyze_redesign_pptx
except ImportError:
    analyze_redesign_pptx = None

if analyze_redesign_pptx is None:
    pytest.skip("analyze_redesign_pptx not landed yet", allow_module_level=True)

from services.raster_analysis_operation_handler import (  # noqa: E402
    automatic_approval_method_for_mode, raster_analyzer_for_mode,
)


def test_auto_redesign_analyzer_is_analyze_redesign_pptx():
    assert raster_analyzer_for_mode("auto_redesign") is analyze_redesign_pptx


def test_auto_preserve_analyzer_unchanged():
    assert raster_analyzer_for_mode("auto_preserve") is analyze_auto_preserve_pptx


def test_reconstruct_and_unknown_modes_use_manual_analyzer():
    assert raster_analyzer_for_mode("reconstruct") is analyze_raster_pptx
    assert raster_analyzer_for_mode("something_else") is analyze_raster_pptx


def test_automatic_approval_method_mapping():
    assert automatic_approval_method_for_mode("auto_preserve") == "automatic_preserve"
    assert automatic_approval_method_for_mode("auto_redesign") == "automatic_redesign"
    assert automatic_approval_method_for_mode("reconstruct") is None
