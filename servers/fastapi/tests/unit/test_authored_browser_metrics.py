import asyncio

import pytest

from utils import slide_capture

HTML = """<!doctype html><html><head><style>
html,body{margin:0;width:1280px;height:720px}.box{position:absolute;left:12px;top:16px;width:80px;height:20px;overflow:hidden}
</style></head><body><div class="box">Metric 42%</div></body></html>"""


def test_metrics_parser_rejects_missing_or_malformed_payload():
    assert slide_capture._extract_browser_metrics(b"") is None
    assert (
        slide_capture._extract_browser_metrics(
            b'<meta id="__codex_authored_dom_metrics" content="%7Bbad">'
        )
        is None
    )


def test_render_cache_hit_reuses_atomic_capture_after_metrics_eviction(monkeypatch):
    slide_capture._render_cache.clear()
    slide_capture._browser_metrics_cache.clear()
    slide_capture._capture_cache.clear()
    slide_capture._browser_metrics_inflight.clear()
    calls = 0

    async def fake_uncached(html, timeout=60):
        nonlocal calls
        calls += 1
        slide_capture._cache_browser_metrics(
            html,
            {"width": 1280, "height": 720, "elements": []},
        )
        return b"fresh-png"

    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 8)

    first = asyncio.run(slide_capture.render_html_to_png(HTML))
    slide_capture._browser_metrics_cache.clear()  # Simulate LRU divergence.
    second, metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(HTML))

    assert first == second == b"fresh-png"
    # The capture-result adapter must not launch a second browser pass when
    # the PNG is already retained; metrics are recovered from the same atomic
    # capture path (or reported unavailable) instead.
    assert calls == 1
    assert metrics is not None
    assert metrics["width"] == 1280


def test_metrics_recollection_failure_is_explicitly_unavailable(monkeypatch):
    slide_capture._render_cache.clear()
    slide_capture._browser_metrics_cache.clear()
    slide_capture._capture_cache.clear()
    slide_capture._browser_metrics_inflight.clear()

    async def fake_uncached(html, timeout=60):
        return b"png-without-probe"

    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 8)

    png, metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(HTML))

    assert png == b"png-without-probe"
    assert metrics is None


@pytest.mark.skipif(
    slide_capture.find_chrome() is None, reason="headless Chrome is required"
)
def test_render_collects_dom_metrics_in_the_same_browser_invocation():
    slide_capture._render_cache.clear()
    slide_capture._browser_metrics_cache.clear()

    png = asyncio.run(slide_capture.render_html_to_png(HTML))
    metrics = slide_capture.get_cached_browser_metrics(HTML)

    assert png.startswith(b"\x89PNG")
    assert metrics is not None
    assert metrics["width"] == 1280
    assert len(metrics["elements"]) == 1
    element = metrics["elements"][0]
    assert element["qid"]
    assert element["rendered_text"] == "Metric 42%"
    assert "parent_qid" in element
    assert element["bbox"]["x"] == 12
    assert element["computed_style"]["fontSize"]
