"""The measured type clamp (design Q2): sub-floor owners the CSS clamp missed
are raised from the first render's computed sizes and rendered exactly once
more, behind ``AUTHORED_FRAGMENTATION_GATE_ENABLED``.

This repo has no ``pytest-asyncio`` plugin; async scenarios are driven with
``asyncio.run`` from ordinary sync test functions.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from utils import slide_capture

GATE_KNOB = "AUTHORED_FRAGMENTATION_GATE_ENABLED"
FLOOR_KNOB = "AUTHORED_TYPE_FLOOR_PX"


def _clear(monkeypatch) -> None:
    for key in (GATE_KNOB, FLOOR_KNOB, "AUTHORED_FRAGMENTATION_MAX_TEXT_OWNERS"):
        monkeypatch.delenv(key, raising=False)


def _element(qid: str, text: str, font: float, *, role: str = "body", **extra) -> dict:
    item = {
        "qid": qid,
        "tag": "div",
        "semantic_role": role,
        "has_owned_text": bool(text),
        "rendered_text": text,
        "font_size": font,
        "parent_qid": "root",
        "bbox": {"x": 100, "y": 100, "width": 200, "height": 40},
    }
    item.update(extra)
    return item


def _metrics(*elements: dict) -> dict:
    return {"viewport": {"width": 1280, "height": 720}, "elements": list(elements)}


HTML = (
    "<html><head><style>.body{font-size:0.75rem}</style></head><body>"
    '<h1 data-qid="title" style="font-size:32px">Quarterly review</h1>'
    '<p data-qid="em" style="font-size:0.8em">em label</p>'
    '<span data-qid="var" style="font-size:var(--chip)">var label</span>'
    '<div data-qid="short" style="font:600 12px/1.2 Inter">shorthand label</div>'
    '<p data-qid="sheet" class="body">stylesheet body</p>'
    '<svg><text data-qid="svg" font-size="11">svg label</text></svg>'
    '<div data-qid="page" data-semantic-role="page_number" style="font-size:0.7em">7</div>'
    '<div data-qid="deco" data-semantic-role="decoration" style="font-size:0.5em">*</div>'
    "</body></html>"
)

SUB_FLOOR_METRICS = _metrics(
    _element("title", "Quarterly review", 32, role="title"),
    _element("em", "em label", 12.8),
    _element("var", "var label", 13),
    _element("short", "shorthand label", 12),
    _element("sheet", "stylesheet body", 12),
    _element("svg", "svg label", 11),
    _element("page", "7", 11.2, role="page_number"),
    _element("deco", "*", 8, role="decoration"),
)


# --- the rewrite ------------------------------------------------------------


# --- the render seam --------------------------------------------------------


class _Renderer:
    """A fake ``render_html_to_png_with_metrics`` that scripts one capture per call."""

    def __init__(self, *captures):
        self.captures = list(captures)
        self.calls: list[str] = []

    async def __call__(self, html, *, fit=False, **_kwargs):
        assert fit is True
        self.calls.append(html)
        return self.captures[len(self.calls) - 1]


def _capture(metrics, *, fitted_html=None, png=b"png", fit_report=None):
    return SimpleNamespace(
        png=png, browser_metrics=metrics, fitted_html=fitted_html, fit_report=fit_report
    )


# --- render cache identity (review finding S7) -----------------------------


def test_the_render_cache_identity_follows_the_fitter_floor(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "false")
    off = slide_capture._render_cache_identity(HTML)
    assert off.fit_font_floor_px == 12.0

    monkeypatch.setenv(GATE_KNOB, "true")
    on = slide_capture._render_cache_identity(HTML)
    assert on.fit_font_floor_px == 15.0
    assert on.digest != off.digest

    monkeypatch.setenv(FLOOR_KNOB, "18")
    raised = slide_capture._render_cache_identity(HTML)
    assert raised.fit_font_floor_px == 18.0
    assert raised.digest not in {on.digest, off.digest}
    assert slide_capture._metrics_cache_key(HTML) != (off.source_digest, off.digest, "metrics")
    # The fit-floor/canvas identity landed in measurement revision 15; later
    # revisions (e.g. f4038b9df, v19) keep it, so the cache version may only
    # move forward. The exact current value is pinned in
    # test_svg_renderer_raster_preview.
    version = re.fullmatch(
        r"authored-measurement/v(\d+)-[a-z0-9-]+", slide_capture.MEASUREMENT_CACHE_VERSION
    )
    assert version is not None and int(version.group(1)) >= 15


# --- the shipped pipeline call site ----------------------------------------


_QID_OF_MAIN = re.compile(r'<main[^>]*data-qid="([^"]+)"')


class _PipelineRenderer:
    """First call: the slide's ``<main>`` measures 12px. Second call: 15px."""

    def __init__(self):
        self.calls: list[str] = []

    async def __call__(self, html, *, fit=False, **_kwargs):
        assert fit is True
        self.calls.append(html)
        qid = _QID_OF_MAIN.search(html).group(1)
        font = 12.0 if len(self.calls) == 1 else 15.0
        return _capture(
            _metrics(_element(qid, "fit call site", font)),
            png=b"png-%d" % len(self.calls),
            fit_report={"edits": []},
        )
