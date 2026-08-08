"""Real-Chromium regression coverage for SlideSpec's rendered evidence contract."""

import asyncio

import pytest

from utils.authored_dom_quality import STYLE_REFERENCE, inspect_authored_dom
from utils.slide_capture import (
    chrome_runtime_digest,
    find_chrome,
    get_render_runtime_counters,
    render_html_to_png_with_metrics,
    reset_render_runtime,
)


def _by_qid(metrics: dict) -> dict[str, dict]:
    return {str(item["qid"]): item for item in metrics["elements"]}


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_browser_probe_measures_pixel_contrast_clipping_quad_and_continuous_occlusion() -> None:
    html = """<!doctype html>
    <html><head><style>
      * { box-sizing: border-box; }
      html, body { margin: 0; width: 1280px; height: 720px; }
      #contrast-parent { position:absolute; left:20px; top:20px; background:rgb(0,0,0); }
      #contrast { color:rgba(255,255,255,.5); background:rgba(255,255,255,.5); font:20px Arial; }
      #clip { position:absolute; left:20px; top:100px; width:90px; height:18px; overflow:hidden; font:32px/40px Arial; }
      #rotated { position:absolute; left:220px; top:100px; width:120px; height:50px; transform:rotate(30deg); background:#369; }
      #behind, #cover { position:absolute; left:420px; top:100px; width:120px; height:80px; }
      #behind { z-index:1; background:#eee; }
      #cover { z-index:2; background:#111; }
      #complex { position:absolute; left:620px; top:100px; width:260px; height:42px;
        color:white; font:28px/42px Arial; background-image:linear-gradient(90deg,#000,#fff); }
      #image-text { position:absolute; left:620px; top:170px; width:260px; height:42px;
        color:white; font:28px/42px Arial;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='42'%3E%3Crect width='130' height='42' fill='black'/%3E%3Crect x='130' width='130' height='42' fill='white'/%3E%3C/svg%3E"); }
      #effect { position:absolute; left:620px; top:240px; width:260px; height:46px;
        color:white; background:#111; font:28px/46px Arial; transform:skewX(12deg);
        filter:contrast(.9); mask-image:linear-gradient(90deg,transparent 0,#000 20%,#000 100%); }
      #strip-behind { position:absolute; left:920px; top:100px; width:120px; height:80px; background:#ddd; }
      #strip-cover { position:absolute; left:920px; top:131px; width:120px; height:6px; background:#222; z-index:2; }
      #unmeasurable { position:absolute; pointer-events:none; left:920px; top:220px; width:120px; height:50px; background:#369; }
    </style></head><body>
      <div id="contrast-parent"><span id="contrast" data-qid="contrast">contrast</span></div>
      <div id="clip" data-qid="clip">CLIPPED GLYPHS</div>
      <div id="rotated" data-qid="rotated"></div>
      <div id="behind" data-qid="behind">hidden</div>
      <div id="cover" data-qid="cover"></div>
      <div id="complex" data-qid="complex">Gradient contrast</div>
      <div id="image-text" data-qid="image-text">Image contrast</div>
      <div id="effect" data-qid="effect">Filtered mask</div>
      <div id="strip-behind" data-qid="strip-behind"></div>
      <div id="strip-cover"></div>
      <div id="unmeasurable" data-qid="unmeasurable"></div>
    </body></html>"""

    png, metrics = asyncio.run(render_html_to_png_with_metrics(html))
    measured = _by_qid(metrics)

    assert png.startswith(b"\x89PNG")
    assert metrics["width"] == 1280
    assert metrics["height"] == 720
    for item in measured.values():
        assert item["measurement"]["geometry"] is True
        assert item["measurement"]["clipping"] is True
        assert item["measurement"]["stacking"] is True
    # Semi-transparent foreground and background are composited before WCAG
    # luminance is calculated.  A null/guessed ratio must never pass this check.
    assert 1.0 < measured["contrast"]["contrast_ratio"] < 3.0
    assert measured["clip"]["glyph_clipped"] is True
    assert measured["clip"]["line_box_clipped"] is True
    assert measured["rotated"]["rotation_degrees"] == pytest.approx(30, abs=0.2)
    quad = measured["rotated"]["quad"]
    assert len(quad) == 4
    assert quad[0]["y"] != pytest.approx(quad[1]["y"], abs=0.2)
    assert measured["behind"]["occluded_fraction"] >= 0.96
    assert measured["cover"]["effective_z_index"] > measured["behind"]["effective_z_index"]
    assert 0.02 <= measured["strip-behind"]["continuous_occluded_fraction"] <= 0.12
    assert measured["strip-behind"]["occlusion_sample_step_px"] <= 4
    assert measured["unmeasurable"]["occluded_fraction"] is None
    assert measured["unmeasurable"]["measurement"]["occlusion"] is False

    # Gradient/image backgrounds and filter+mask composites are taken from a
    # paired real-Chrome background/glyph render rather than guessed CSS colors.
    for qid in ("complex", "image-text", "effect"):
        assert measured[qid]["contrast_measurement_method"] == "chrome-paired-pixel-glyph-v1"
        assert measured[qid]["contrast_sample_count"] >= 4
        assert measured[qid]["measurement"]["contrast"] is True
    assert measured["complex"]["contrast_ratio"] < 2
    assert measured["image-text"]["contrast_ratio"] < 2
    assert measured["effect"]["measurement"]["visual_effects"] is True
    assert measured["effect"]["measurement"]["transform"] is True

    report = inspect_authored_dom(html, metrics, reference_mode=STYLE_REFERENCE)
    contrast_qids = {
        qid for issue in report.issues if issue.category == "contrast" for qid in issue.qids
    }
    assert {"complex", "image-text"}.issubset(contrast_qids)

    # A hard pixel probe failure must not silently fall back to a guessed clean
    # ratio.  Preserve the structured unknown state and escalate it as high.
    measured["complex"]["contrast_ratio"] = None
    measured["complex"]["measurement"]["contrast"] = False
    measured["complex"]["contrast_measurement_error"] = "synthetic-probe-failure"
    failed_report = inspect_authored_dom(html, metrics, reference_mode=STYLE_REFERENCE)
    assert any(
        issue.category == "contrast"
        and issue.severity == "high"
        and issue.qids == ("complex",)
        and "could not establish pixel contrast" in issue.detail
        for issue in failed_report.issues
    )


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_browser_probe_attributes_glyph_clipping_and_occlusion_to_owned_paint() -> None:
    html = """<!doctype html><html><head><style>
      html,body { margin:0; width:1280px; height:720px; }
      #wrapper { position:absolute; left:20px; top:20px; width:180px; height:24px; overflow:hidden; }
      #copy { display:block; font:32px/40px Arial; }
      #behind,#cover { position:absolute; left:300px; top:40px; width:180px; height:60px; }
      #cover { z-index:2; background:#111; }
    </style></head><body>
      <section id="wrapper" data-qid="wrapper"><p id="copy" data-qid="copy">Owned glyphs</p></section>
      <p id="behind" data-qid="behind">Behind</p><div id="cover" data-qid="cover"></div>
    </body></html>"""

    _png, metrics = asyncio.run(render_html_to_png_with_metrics(html))
    measured = _by_qid(metrics)

    assert measured["wrapper"]["has_owned_text"] is False
    assert measured["wrapper"]["glyph_clipped"] is False
    assert measured["copy"]["has_owned_text"] is True
    assert measured["copy"]["glyph_clipped"] is True
    assert measured["copy"]["glyph_clip_cause"] == "ancestor"
    assert measured["copy"]["offset_parent_qid"] == "wrapper"
    assert measured["copy"]["containing_block_proof"] == {
        "source": "chrome",
        "telemetryComplete": True,
        "exactBoundOffsetParent": True,
        "offsetParentQid": "wrapper",
        "offsetParentTag": "section",
        "offsetParentBoundsPx": {
            "x": pytest.approx(20),
            "y": pytest.approx(20),
            "width": pytest.approx(180),
            "height": pytest.approx(24),
        },
    }
    assert measured["behind"]["occlusion_probe_version"] == "owned-paint-v3"
    assert "cover" in measured["behind"]["occlusion_occluder_qids"]
    assert measured["cover"]["has_owned_paint"] is True

    report = inspect_authored_dom(html, metrics, reference_mode=STYLE_REFERENCE)
    assert any(
        issue.category == "overlap" and "behind" in issue.qids
        for issue in report.issues
    )


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_reusable_chrome_has_stable_identity_and_closes_every_page_session() -> None:
    before = get_render_runtime_counters()
    first = '<html><body><p data-qid="reuse-a">reuse A</p></body></html>'
    second = '<html><body><p data-qid="reuse-b">reuse B</p></body></html>'

    first_png, _ = asyncio.run(render_html_to_png_with_metrics(first))
    second_png, _ = asyncio.run(render_html_to_png_with_metrics(second))
    after = get_render_runtime_counters()

    assert first_png.startswith(b"\x89PNG")
    assert second_png.startswith(b"\x89PNG")
    assert chrome_runtime_digest() == chrome_runtime_digest()
    assert chrome_runtime_digest().startswith("sha256:")
    assert after["chrome_process_launches"] - before["chrome_process_launches"] <= 1
    assert after["session_reuse_hits"] - before["session_reuse_hits"] >= 1
    assert after["page_sessions_opened"] - before["page_sessions_opened"] == 2
    assert after["page_sessions_closed"] - before["page_sessions_closed"] == 2


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_render_runtime_reset_makes_repeated_html_cold_without_resetting_counters() -> None:
    html = '<html><body><p data-qid="cold-reset">cold reset</p></body></html>'
    reset_render_runtime()
    before = get_render_runtime_counters()
    asyncio.run(render_html_to_png_with_metrics(html))
    middle = get_render_runtime_counters()

    reset_render_runtime()
    asyncio.run(render_html_to_png_with_metrics(html))
    after = get_render_runtime_counters()
    reset_render_runtime()

    assert middle["chrome_process_launches"] - before["chrome_process_launches"] == 1
    assert after["chrome_process_launches"] - middle["chrome_process_launches"] == 1
    assert after["render_cache_hits"] == middle["render_cache_hits"]
    assert after["requests"] - before["requests"] == 2
