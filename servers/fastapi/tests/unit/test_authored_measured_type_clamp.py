"""The measured type clamp (design Q2): sub-floor owners the CSS clamp missed
are raised from the first render's computed sizes and rendered exactly once
more, behind ``AUTHORED_FRAGMENTATION_GATE_ENABLED``.

This repo has no ``pytest-asyncio`` plugin; async scenarios are driven with
``asyncio.run`` from ordinary sync test functions.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from services import authored_presentation_pipeline as pipeline
from tests.unit.test_authored_checkpoint_service import (
    _checkpoint_database,
    _one_slide_outline,
    _pipeline_brand,
)
from tests.unit.test_authored_in_render_fit_pipeline import _completed_author_job
from utils import slide_capture
from utils.authored_measured_type_clamp import (
    MEASURED_TYPE_CLAMP_RECEIPT_KEY,
    MeasuredTypeClampEdit,
    MeasuredTypeClampOutcome,
    clamp_measured_type,
    merge_measured_type_clamp_receipt,
    render_with_measured_type_clamp,
    sub_floor_text_owners,
)

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


def test_sub_floor_owners_follow_the_gate_ownership_rule():
    owners = sub_floor_text_owners(SUB_FLOOR_METRICS, floor_px=15)
    assert [qid for qid, _px in owners] == ["em", "var", "short", "sheet", "svg"]


def test_em_var_shorthand_stylesheet_and_svg_sizes_are_clamped_by_qid():
    rewritten, edits = clamp_measured_type(HTML, SUB_FLOOR_METRICS, floor_px=15)

    assert 'data-qid="em" style="font-size:15px"' in rewritten
    assert 'data-qid="var" style="font-size:15px"' in rewritten
    # The shorthand stays; the trailing longhand wins the cascade.
    assert 'data-qid="short" style="font:600 12px/1.2 Inter;font-size:15px"' in rewritten
    # An owner sized only by the stylesheet gets a new inline attribute.
    assert 'data-qid="sheet" class="body" style="font-size:15px"' in rewritten
    # Inline style beats the SVG presentation attribute.
    assert 'data-qid="svg" font-size="11" style="font-size:15px"' in rewritten
    assert 'data-qid="title" style="font-size:32px"' in rewritten
    assert edits == (
        MeasuredTypeClampEdit("em", 12.8, 15.0),
        MeasuredTypeClampEdit("var", 13.0, 15.0),
        MeasuredTypeClampEdit("short", 12.0, 15.0),
        MeasuredTypeClampEdit("sheet", 12.0, 15.0),
        MeasuredTypeClampEdit("svg", 11.0, 15.0),
    )


def test_an_existing_inline_font_size_is_replaced_not_duplicated():
    html = (
        '<div data-qid="chip" style="color:red; font-size : 0.8em ;margin:0">chip</div>'
    )
    rewritten, edits = clamp_measured_type(
        html, _metrics(_element("chip", "chip", 12.8)), floor_px=15
    )
    assert rewritten == '<div data-qid="chip" style="color:red;margin:0;font-size:15px">chip</div>'
    assert len(re.findall(r"font-size", rewritten)) == 1
    assert edits == (MeasuredTypeClampEdit("chip", 12.8, 15.0),)


def test_entities_and_functions_inside_the_style_survive_the_split():
    html = (
        '<p data-qid="q" style="font-family:&quot;Noto Sans&quot;;'
        'background:rgb(1, 2, 3);font-size:calc(0.5em + 2px)">x</p>'
    )
    rewritten, _edits = clamp_measured_type(html, _metrics(_element("q", "x", 10)), floor_px=15)
    assert (
        'style="font-family:&quot;Noto Sans&quot;;background:rgb(1, 2, 3);font-size:15px"'
        in rewritten
    )


def test_furniture_is_never_touched():
    rewritten, edits = clamp_measured_type(HTML, SUB_FLOOR_METRICS, floor_px=15)
    assert 'data-qid="page" data-semantic-role="page_number" style="font-size:0.7em"' in rewritten
    assert 'data-qid="deco" data-semantic-role="decoration" style="font-size:0.5em"' in rewritten
    assert {edit.qid for edit in edits}.isdisjoint({"page", "deco"})


def test_a_page_number_detected_by_geometry_is_furniture_too():
    html = '<div data-qid="num" style="font-size:0.7em">7</div>'
    footer = _element(
        "num", "7", 12, bbox={"x": 0, "y": 690, "width": 1280, "height": 20}
    )
    rewritten, edits = clamp_measured_type(html, _metrics(footer), floor_px=15)
    assert rewritten == html
    assert edits == ()


def test_nothing_sub_floor_returns_the_same_document():
    metrics = _metrics(_element("title", "Quarterly review", 32), _element("em", "em label", 18))
    rewritten, edits = clamp_measured_type(HTML, metrics, floor_px=15)
    assert rewritten is HTML
    assert edits == ()


def test_a_qid_missing_or_duplicated_in_the_document_is_skipped():
    html = '<p data-qid="dup">a</p><p data-qid="dup">b</p><p data-qid="ok">c</p>'
    metrics = _metrics(
        _element("dup", "a", 12), _element("ghost", "z", 12), _element("ok", "c", 12)
    )
    rewritten, edits = clamp_measured_type(html, metrics, floor_px=15)
    assert rewritten == '<p data-qid="dup">a</p><p data-qid="dup">b</p><p data-qid="ok" style="font-size:15px">c</p>'
    assert edits == (MeasuredTypeClampEdit("ok", 12.0, 15.0),)


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


def test_one_extra_render_feeds_the_clamped_document_forward(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "true")
    clean = _metrics(_element("em", "em label", 15))
    renderer = _Renderer(
        _capture(SUB_FLOOR_METRICS),
        _capture(clean, png=b"second", fit_report={"edits": []}),
    )

    outcome = asyncio.run(
        render_with_measured_type_clamp(
            HTML, reference_mode="style_reference", render=renderer
        )
    )

    assert len(renderer.calls) == 2
    assert renderer.calls[0] == HTML
    assert 'data-qid="em" style="font-size:15px"' in renderer.calls[1]
    assert outcome.rerendered is True
    assert outcome.capture.png == b"second"
    assert outcome.capture.browser_metrics is clean
    assert outcome.capture.fit_report == {"edits": []}
    # The second render's fitter changed nothing, so the clamped document is
    # the HTML of record the pipeline reseals its envelope over.
    assert outcome.capture.fitted_html == renderer.calls[1]
    assert [edit.qid for edit in outcome.edits] == ["em", "var", "short", "sheet", "svg"]
    assert outcome.residual_sub_floor == 0
    assert outcome.floor_px == 15.0


def test_the_first_renders_fitted_html_is_what_gets_clamped(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "true")
    fitted = HTML.replace(
        'data-qid="title" style="font-size:32px"',
        'data-qid="title" style="font-size:32px;line-height:1.1"',
    )
    renderer = _Renderer(
        _capture(SUB_FLOOR_METRICS, fitted_html=fitted),
        _capture(_metrics(), fitted_html=None),
    )
    outcome = asyncio.run(
        render_with_measured_type_clamp(HTML, reference_mode="style_reference", render=renderer)
    )
    assert "line-height:1.1" in renderer.calls[1]
    assert outcome.capture.fitted_html == renderer.calls[1]


def test_a_residual_sub_floor_owner_never_triggers_a_third_render(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "true")
    stubborn = _metrics(_element("em", "em label", 12))
    renderer = _Renderer(_capture(SUB_FLOOR_METRICS), _capture(stubborn))
    outcome = asyncio.run(
        render_with_measured_type_clamp(HTML, reference_mode="style_reference", render=renderer)
    )
    assert len(renderer.calls) == 2
    assert outcome.residual_sub_floor == 1
    assert outcome.capture.browser_metrics is stubborn


def test_no_sub_floor_owner_means_no_second_render(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "true")
    first = _capture(_metrics(_element("em", "em label", 18)))
    renderer = _Renderer(first)
    outcome = asyncio.run(
        render_with_measured_type_clamp(HTML, reference_mode="style_reference", render=renderer)
    )
    assert len(renderer.calls) == 1
    assert outcome.capture is first
    assert outcome.edits == ()
    assert outcome.rerendered is False


def test_knob_off_returns_the_first_capture_untouched(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "false")
    first = _capture(SUB_FLOOR_METRICS)
    renderer = _Renderer(first)
    outcome = asyncio.run(
        render_with_measured_type_clamp(HTML, reference_mode="style_reference", render=renderer)
    )
    assert renderer.calls == [HTML]
    assert outcome == MeasuredTypeClampOutcome(capture=first)
    assert merge_measured_type_clamp_receipt({}, outcome) == {}


def test_exact_reconstruction_is_exempt(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "true")
    first = _capture(SUB_FLOOR_METRICS)
    renderer = _Renderer(first)
    outcome = asyncio.run(
        render_with_measured_type_clamp(HTML, reference_mode="exact_reconstruction", render=renderer)
    )
    assert renderer.calls == [HTML]
    assert outcome.capture is first
    assert outcome.edits == ()


def test_the_receipt_records_the_edits_and_leaves_an_untouched_slide_alone():
    receipt = {"best_of": {"variant_chosen": 1}}
    assert merge_measured_type_clamp_receipt(receipt, None) is receipt
    outcome = MeasuredTypeClampOutcome(
        capture=None,
        edits=(MeasuredTypeClampEdit("em", 12.8, 15.0),),
        floor_px=15.0,
        residual_sub_floor=0,
        rerendered=True,
    )
    merged = merge_measured_type_clamp_receipt(receipt, outcome)
    assert merged["best_of"] == {"variant_chosen": 1}
    assert merged[MEASURED_TYPE_CLAMP_RECEIPT_KEY] == {
        "floor_px": 15.0,
        "edit_count": 1,
        "edits": [{"qid": "em", "from_px": 12.8, "to_px": 15.0}],
        "residual_sub_floor": 0,
        "rerendered": True,
    }


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
    assert slide_capture.MEASUREMENT_CACHE_VERSION == "authored-measurement/v12-fit-floor-identity"


# --- the shipped pipeline call site ----------------------------------------


_QID_OF_MAIN = re.compile(r'<main[^>]*data-qid="([^"]+)"')


async def _render_one_slide(tmp_path, monkeypatch, *, renderer):
    """Runs the real ``_author_illustrate_render_pipeline`` for one slide with
    Chromium replaced by ``renderer`` (see ``test_authored_in_render_fit_pipeline``)."""

    engine, maker = await _checkpoint_database(tmp_path, monkeypatch)
    monkeypatch.setattr(
        pipeline, "prepare_authored_deck", lambda *_a, **_k: SimpleNamespace(
            contents=("fit call site",),
            design_system="TEST DESIGN SYSTEM",
            reference_mode="style_reference",
        )
    )

    async def unexpected_author(*_args, **_kwargs):
        raise AssertionError("a completed author checkpoint must be resumed")

    async def unexpected_plain_render(_html):
        raise AssertionError("the knob-on path must not call the plain renderer")

    async def no_recording(**_kwargs):
        return None

    monkeypatch.setattr(pipeline, "author_planned_slide", unexpected_author)
    monkeypatch.setattr(pipeline, "render_html_to_png", unexpected_plain_render)
    monkeypatch.setattr(pipeline, "render_html_to_png_with_metrics", renderer)
    monkeypatch.setattr(pipeline, "record_local_stage", no_recording)
    try:
        job = await _completed_author_job(maker)
        return await pipeline._author_illustrate_render_pipeline(
            _one_slide_outline(),
            _pipeline_brand(),
            SimpleNamespace(reference_mode="style_reference", illustration_prompt=None),
            generation_job=job,
        )
    finally:
        await engine.dispose()


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


def test_the_pipeline_renders_an_affected_slide_exactly_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "true")
    renderer = _PipelineRenderer()

    result = asyncio.run(_render_one_slide(tmp_path, monkeypatch, renderer=renderer))

    assert len(renderer.calls) == 2
    qid = _QID_OF_MAIN.search(renderer.calls[0]).group(1)
    assert re.search(r'<main[^>]*style="font-size:15px"', renderer.calls[1])
    # The second render is the one of record: its document, PNG, fit report
    # and scene envelope reach the result, not the first render's.
    assert result.deck.htmls == [renderer.calls[1]]
    assert result.pngs == [b"png-2"]
    assert result.slide_fit_reports == {0: {"edits": []}}
    assert result.scene_envelope.html_projections == (renderer.calls[1],)
    clamp = result.slide_quality_receipts[0][MEASURED_TYPE_CLAMP_RECEIPT_KEY]
    assert clamp["edit_count"] == 1
    assert clamp["edits"] == [{"qid": qid, "from_px": 12.0, "to_px": 15.0}]
    assert clamp["residual_sub_floor"] == 0


def test_the_pipeline_with_the_knob_off_renders_once_and_keeps_the_html(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_IN_RENDER_FIT_ENABLED", "true")
    _clear(monkeypatch)
    monkeypatch.setenv(GATE_KNOB, "false")
    renderer = _PipelineRenderer()

    result = asyncio.run(_render_one_slide(tmp_path, monkeypatch, renderer=renderer))

    assert len(renderer.calls) == 1
    assert result.deck.htmls == [renderer.calls[0]]
    assert result.pngs == [b"png-1"]
    assert MEASURED_TYPE_CLAMP_RECEIPT_KEY not in result.slide_quality_receipts.get(0, {})
