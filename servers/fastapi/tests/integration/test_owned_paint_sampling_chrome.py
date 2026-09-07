"""Real-Chrome regressions for per-owned-paint occlusion sampling."""

import asyncio

import pytest

from utils.slide_capture import find_chrome, render_html_to_png_with_metrics


def _by_qid(metrics: dict) -> dict[str, dict]:
    return {str(item["qid"]): item for item in metrics["elements"]}


def _footer_html(*, cover_left: int) -> str:
    # The 1184px parent grid has a 29.6px x-step at its 40-column cap.  The
    # 14px footer glyph at x=48 falls entirely before its first center (62.8),
    # exactly reproducing the former sampled=0 blind spot.
    return f"""<!doctype html><html><head><style>
      html,body {{ margin:0; width:1280px; height:720px; overflow:hidden; }}
      #footer {{ position:absolute; left:48px; top:696px; width:1184px; height:20px;
        color:#123; font:14px/14px Arial; padding-top:2px; white-space:nowrap; }}
      #cover {{ position:absolute; left:{cover_left}px; top:696px; width:18px; height:20px;
        z-index:5; background:#9dd8ef; }}
    </style></head><body>
      <div id="footer" data-qid="q-div-c6f2820b2605">5</div>
      <div id="cover" data-qid="summary-band"></div>
    </body></html>"""


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_owned_text_fragment_gets_samples_when_parent_grid_misses_14px_footer() -> None:
    _png, metrics = asyncio.run(render_html_to_png_with_metrics(_footer_html(cover_left=48)))
    footer = _by_qid(metrics)["q-div-c6f2820b2605"]

    assert footer["occlusion_coarse_sample_count"] == 0
    assert footer["occlusion_owned_rect_sample_count"] >= 1
    assert footer["occlusion_sample_count"] >= 1
    assert footer["occluded_fraction"] == pytest.approx(1)
    assert footer["continuous_occluded_fraction"] == pytest.approx(1)
    assert footer["occlusion_occluder_qids"] == ["summary-band"]
    assert footer["occlusion_measurement_error"] is None
    assert footer["occlusion_measurement_evidence"] == {
        **footer["occlusion_measurement_evidence"],
        "qid": "q-div-c6f2820b2605",
        "metric": "occlusion",
        "sensor_version": "owned-paint-v12-polygon-normal-hit",
        "stage": "chrome.owned_paint_sampling",
        "status": "measured",
        "reason": None,
    }


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_moving_overlay_clears_same_owned_text_target_on_fresh_post_render() -> None:
    _before_png, before_metrics = asyncio.run(
        render_html_to_png_with_metrics(_footer_html(cover_left=48))
    )
    _after_png, after_metrics = asyncio.run(
        render_html_to_png_with_metrics(_footer_html(cover_left=200))
    )
    before = _by_qid(before_metrics)["q-div-c6f2820b2605"]
    after = _by_qid(after_metrics)["q-div-c6f2820b2605"]

    assert before["occluded_fraction"] == pytest.approx(1)
    assert before["occlusion_occluder_qids"] == ["summary-band"]
    assert after["occluded_fraction"] == pytest.approx(0)
    assert after["continuous_occluded_fraction"] == pytest.approx(0)
    assert after["occlusion_occluder_qids"] == []
    assert after["occlusion_sample_count"] == before["occlusion_sample_count"]


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_zero_sample_and_hit_test_unknowns_have_scoped_reasons() -> None:
    html = """<!doctype html><html><head><style>
      html,body { margin:0; width:1280px; height:720px; }
      #outside-owner { position:absolute; left:100px; top:100px; width:10px; height:10px;
        color:#234; font:14px Arial; text-indent:100px; white-space:nowrap; }
      #no-hit { position:absolute; pointer-events:none; left:300px; top:100px;
        width:100px; height:30px; background:#456; }
    </style></head><body>
      <div id="outside-owner" data-qid="outside-owner">outside</div>
      <div id="no-hit" data-qid="no-hit">unknown</div>
    </body></html>"""

    _png, metrics = asyncio.run(render_html_to_png_with_metrics(html))
    measured = _by_qid(metrics)
    zero = measured["outside-owner"]
    unknown = measured["no-hit"]

    assert zero["occlusion_sample_count"] == 0
    assert zero["occluded_fraction"] is None
    assert zero["occlusion_measurement_error"] == "no_sample_on_owned_paint"
    assert zero["occlusion_measurement_evidence"]["qid"] == "outside-owner"
    assert zero["occlusion_measurement_evidence"]["metric"] == "occlusion"
    assert zero["occlusion_measurement_evidence"]["stage"] == "chrome.owned_paint_sampling"

    assert unknown["occlusion_sample_count"] > 0
    assert unknown["occlusion_known_sample_count"] == 0
    assert unknown["occluded_fraction"] is None
    assert unknown["occlusion_measurement_error"] == "owner_absent_from_hit_test"
    assert unknown["occlusion_measurement_evidence"]["qid"] == "no-hit"
    assert unknown["occlusion_measurement_evidence"]["status"] == "unknown"


def _wrapper_html() -> str:
    # The shape a source-content restyle produces for a multi-line source
    # block: the binding sits on a card body that paints nothing itself, and
    # each line is a <p> carrying its own qid.  `ownedTextNodes` assigns every
    # text node to its NEAREST qid ancestor, so the wrapper owns no rect.
    return """<!doctype html><html><head><style>
      html,body { margin:0; width:1280px; height:720px; overflow:hidden; }
      #body { position:absolute; left:64px; top:120px; width:400px; }
      p { margin:0; font:20px/28px Arial; color:#123; }
    </style></head><body>
      <div id="body" data-qid="source-block-wrapper">
        <p data-qid="line-1">first line</p>
        <p data-qid="line-2">second line</p>
      </div>
    </body></html>"""


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_wrapper_without_own_paint_is_measured_from_its_descendants() -> None:
    """A bound wrapper's ink lives in its children, and must still be measured.

    Answering `not_applicable`/`no_owned_paint` here made every consumer that
    requires a measured verdict reject a visible block: it is what failed
    `source_content_browser_readback_failed` on every multi-line source block
    of a restyled deck, 37 of them across 22 slides.
    """

    _png, metrics = asyncio.run(render_html_to_png_with_metrics(_wrapper_html()))
    wrapper = _by_qid(metrics)["source-block-wrapper"]
    evidence = wrapper["occlusion_measurement_evidence"]

    assert evidence["status"] == "measured"
    assert evidence["reason"] is None
    assert evidence["owned_paint_source"] == "descendant"
    assert wrapper["occlusion_sample_count"] >= 1
    assert wrapper["occluded_fraction"] == pytest.approx(0)
    assert wrapper["continuous_occluded_fraction"] == pytest.approx(0)


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_an_element_with_no_paint_anywhere_below_it_stays_not_applicable() -> None:
    """The fallback must not turn a genuinely empty box into a measurement."""

    html = """<!doctype html><html><head><style>
      html,body { margin:0; width:1280px; height:720px; overflow:hidden; }
      #spacer { position:absolute; left:64px; top:120px; width:200px; height:40px; }
    </style></head><body>
      <div id="spacer" data-qid="empty-spacer"></div>
    </body></html>"""
    _png, metrics = asyncio.run(render_html_to_png_with_metrics(html))
    spacer = _by_qid(metrics)["empty-spacer"]
    evidence = spacer["occlusion_measurement_evidence"]

    assert evidence["status"] == "not_applicable"
    assert evidence["reason"] == "no_owned_paint"
