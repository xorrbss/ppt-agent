"""Mutation-envelope, projection, cache identity, and Chrome-boundary coverage."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from utils import slide_capture


_ROUNDTRIP_HTML = """<!doctype html><html><head><style>
  html,body { margin:0; width:1280px; height:720px; overflow:hidden; }
  #container { position:relative; left:40px; top:30px; width:500px; height:160px;
    overflow:hidden; background:#eef; }
  #static-copy { font:14px/18px Arial; color:#123; }
</style></head><body>
  <section id="container" data-qid="container">
    <div id="static-copy" data-qid="static-copy">정책 판단·범위 확정·예외 승인</div>
  </section>
</body></html>"""


def _by_qid(metrics: dict) -> dict[str, dict]:
    return {str(item["qid"]): item for item in metrics["elements"]}


def test_metric_evidence_cache_key_is_scoped_by_qid_metric_sensor_and_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = slide_capture._metric_evidence_cache_key(
        _ROUNDTRIP_HTML, qid="static-copy", metric="occlusion"
    )
    assert base == slide_capture._metric_evidence_cache_key(
        _ROUNDTRIP_HTML, qid="static-copy", metric="occlusion"
    )
    assert base != slide_capture._metric_evidence_cache_key(
        _ROUNDTRIP_HTML, qid="container", metric="occlusion"
    )
    assert base != slide_capture._metric_evidence_cache_key(
        _ROUNDTRIP_HTML, qid="static-copy", metric="glyph_clip"
    )
    assert base != slide_capture._metric_evidence_cache_key(
        _ROUNDTRIP_HTML.replace("#eef", "#def"),
        qid="static-copy",
        metric="occlusion",
    )

    monkeypatch.setattr(slide_capture, "OCCLUSION_SENSOR_VERSION", "owned-paint-next")
    assert base != slide_capture._metric_evidence_cache_key(
        _ROUNDTRIP_HTML, qid="static-copy", metric="occlusion"
    )


@pytest.mark.skipif(
    slide_capture.find_chrome() is None, reason="headless Chrome is required"
)
def test_imported_html_roundtrip_preserves_pixels_glyphs_and_static_containment() -> None:
    original = _ROUNDTRIP_HTML
    instrumented, _cache_key = slide_capture._instrument_authored_html(original)

    assert original == _ROUNDTRIP_HTML
    assert instrumented.count("data-codex-dom-probe") == 1
    assert 'id="static-copy" data-qid="static-copy"' in instrumented
    assert "font:14px/18px Arial" in instrumented

    before_png, before_metrics = asyncio.run(
        slide_capture.render_html_to_png_with_metrics(original)
    )
    after_png, after_metrics = asyncio.run(
        slide_capture.render_html_to_png_with_metrics(instrumented)
    )
    before = _by_qid(before_metrics)["static-copy"]
    after = _by_qid(after_metrics)["static-copy"]

    assert hashlib.sha256(before_png).digest() == hashlib.sha256(after_png).digest()
    assert before["rendered_text"] == after["rendered_text"]
    assert before["bbox"] == pytest.approx(after["bbox"])
    assert before["text_fragments"] == pytest.approx(after["text_fragments"])
    assert before["glyph_clipped"] is after["glyph_clipped"] is False
    assert before["line_box_clipped"] is after["line_box_clipped"] is False
    assert before["static_translation_probe"] == after["static_translation_probe"]
    probe = after["static_translation_probe"]
    assert probe["telemetryComplete"] is True
    assert probe["layoutIsolation"] is True
    assert probe["transformComposable"] is True
    assert probe["containmentBoundsPx"] == pytest.approx(
        {"x": 40, "y": 30, "width": 500, "height": 160}
    )
    assert probe["safeTranslationIntervalPx"]["minX"] <= 0
    assert probe["safeTranslationIntervalPx"]["maxX"] >= 0


class _TrackingSemaphore:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.entries = 0

    async def __aenter__(self) -> None:
        self.active += 1
        self.entries += 1
        self.maximum = max(self.maximum, self.active)

    async def __aexit__(self, *_args: object) -> None:
        self.active -= 1


@pytest.mark.parametrize("reuse_enabled", [False, True])
def test_failed_reuse_is_not_hidden_by_a_one_shot_retry(
    monkeypatch: pytest.MonkeyPatch, reuse_enabled: bool
) -> None:
    tracker = _TrackingSemaphore()

    def fake_run(args: list[str], _timeout: int) -> bytes:
        output_arg = next(item for item in args if item.startswith("--screenshot="))
        output_path = Path(output_arg.split("=", 1)[1])
        Image.new("RGB", (1280, 720), "white").save(output_path, format="PNG")
        return b""

    async def failed_reuse(_html: str, _timeout: int):
        raise RuntimeError("synthetic reusable Chrome failure")

    monkeypatch.setattr(slide_capture, "CHROME_REUSE_ENABLED", reuse_enabled)
    monkeypatch.setattr(slide_capture, "_get_render_sem", lambda: tracker)
    monkeypatch.setattr(slide_capture, "find_chrome", lambda: "synthetic-chrome")
    monkeypatch.setattr(slide_capture, "_run_chrome_screenshot", fake_run)
    monkeypatch.setattr(slide_capture, "_render_html_in_reusable_chrome", failed_reuse)

    source = '<html><body><div data-qid="bounded">bounded</div></body></html>'
    if reuse_enabled:
        with pytest.raises(RuntimeError, match="reusable authored Chrome render failed"):
            asyncio.run(slide_capture._render_html_to_png_uncached(source, timeout=2))
        assert tracker.entries == 1
    else:
        png = asyncio.run(slide_capture._render_html_to_png_uncached(source, timeout=2))
        assert png.startswith(b"\x89PNG")
        assert tracker.entries == 1
    assert tracker.active == 0
    assert tracker.maximum == 1
