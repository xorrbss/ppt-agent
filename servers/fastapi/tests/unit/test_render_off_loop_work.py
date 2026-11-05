"""CPU and disk work of an svg render stays off the event loop or is measured once."""

from __future__ import annotations

import asyncio
import io
import threading
from collections import OrderedDict

from PIL import Image

from services import svg_renderer
from utils import render_cache_budget
from utils.slide_capture_reusable_render import (
    _decode_metrics,
    _decode_probe,
    _decode_screenshot,
    _encode_png,
)


def test_metrics_footprint_is_measured_once_per_cached_entry(monkeypatch):
    measured = []
    original = render_cache_budget.metrics_payload_bytes

    def counting(value, *, overflow_bytes):
        measured.append(id(value))
        return original(value, overflow_bytes=overflow_bytes)

    monkeypatch.setattr(render_cache_budget, "metrics_payload_bytes", counting)
    cache, sizes = OrderedDict(a={"x": 1}, b={"y": [1, 2]}), {}
    total = render_cache_budget.metrics_cache_resident_bytes(cache, overflow_bytes=10, sizes=sizes)
    assert total == render_cache_budget.metrics_cache_resident_bytes(cache, overflow_bytes=10)
    measured.clear()
    assert render_cache_budget.metrics_cache_resident_bytes(cache, overflow_bytes=10, sizes=sizes) == total
    assert measured == []
    # A replaced entry is measured again; an evicted one leaves the memo.
    cache["a"] = {"x": "longer value"}
    cache.pop("b")
    render_cache_budget.metrics_cache_resident_bytes(cache, overflow_bytes=10, sizes=sizes)
    assert measured == [id(cache["a"])] and set(sizes) == {"a"}


def test_reusable_render_helpers_decode_and_encode():
    assert _decode_metrics("%7B%22elements%22%3A%5B%5D%7D") == {"elements": []}
    assert _decode_metrics("not json") is None and _decode_metrics("") is None and _decode_metrics(None) is None
    source = io.BytesIO()
    Image.new("RGBA", (40, 30), (10, 20, 30, 255)).save(source, format="PNG")
    image = _decode_screenshot(source.getvalue(), 32, 24)
    assert image.mode == "RGB" and image.size == (32, 24)
    assert _decode_probe(source.getvalue()).size == (40, 30)
    with Image.open(io.BytesIO(_encode_png(image))) as decoded:
        assert decoded.size == (32, 24)


def test_render_copies_the_metrics_in_a_worker_thread(monkeypatch):
    from utils import slide_capture
    from utils.slide_capture_result import RenderCapture

    on_main_thread = []
    original = svg_renderer._portable_metrics

    def recording(metrics):
        on_main_thread.append(threading.current_thread() is threading.main_thread())
        return original(metrics)

    async def capture(html, **_kwargs):
        return RenderCapture.from_payload(html_digest="sha256:" + "0" * 64, png=b"png",
                                          browser_metrics={"cache_key": "a\u0000b"}, runtime_counters={})

    monkeypatch.setattr(svg_renderer, "_portable_metrics", recording)
    monkeypatch.setattr(slide_capture, "render_html_to_png_with_metrics", capture)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
           '<text x="40" y="80" font-family="Noto Sans KR" font-size="32" fill="#111">가나다</text></svg>')
    quality = asyncio.run(svg_renderer.render_svg_with_metrics(svg)).quality
    assert on_main_thread == [False]
    assert quality["browser_metrics"] == {"cache_key": "a␟b"}
