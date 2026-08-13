"""Browser-level regression checks for authored slide rendering policy.

These tests deliberately exercise the same headless-shell screenshot path used
by authored generation.  The small-text probes turn computed font sizes into
solid pixel bars after DOMContentLoaded; the removed minimum-font script would
therefore turn every bar into 12px and make this test fail.
"""

import asyncio
import html as html_module
import io
import json
import os
import pathlib
import re
import subprocess
import tempfile

import pytest
from PIL import Image

from utils import slide_capture


_RENDER_POLICY_HTML = """<!doctype html>
<html><head><style>
html, body { margin: 0; width: 1280px; height: 720px; background: #fff; overflow: hidden; }
.probe { position: absolute; height: 4px; top: 20px; }
#bar8 { left: 20px; width:8px; background: #ff0000; } #bar10 { left: 50px; width:10px; background: #00aa00; }
#bar11 { left: 80px; width:11px; background: #0000ff; }
#text8 { position: absolute; left: 20px; top: 50px; font-size: 8px; color: #111; }
#text10 { position: absolute; left: 20px; top: 70px; font-size: 10px; color: #111; }
#text11 { position: absolute; left: 20px; top: 90px; font-size: 11px; color: #111; }
#overlap-back { position: absolute; left: 500px; top: 100px; width: 100px; height: 100px; background: #0055ff; }
#overlap-front { position: absolute; left: 530px; top: 130px; width: 80px; height: 80px; background: #ff3300; z-index: 2; }
#crop { position: absolute; left: 700px; top: 100px; width: 40px; height: 30px; overflow: hidden; }
#crop img { position: absolute; left: -10px; top: 0; width: 60px; height: 30px; }
</style></head><body>
<span id="text8">eight</span><span id="text10">ten</span><span id="text11">eleven</span>
<div id="bar8" class="probe"></div><div id="bar10" class="probe"></div><div id="bar11" class="probe"></div>
<div id="overlap-back"></div><div id="overlap-front"></div>
<div id="crop"><img alt="test photo" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAAD0lEQVR4AQEEAPv/AABmmQFpAQDxXVSHAAAAAElFTkSuQmCC"></div>
</body></html>"""


_FONT_MATRIX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body { margin: 0; width: 1280px; height: 720px; background: #fff; overflow: hidden; }
.font { position: absolute; color: #1b1b1b; }
#english { left: 40px; top: 40px; font-family: Arial, sans-serif; font-size: 11px; font-weight: 700; line-height: 13px; }
#korean { left: 40px; top: 65px; font-family: Arial, "Malgun Gothic", sans-serif; font-size: 10px; font-weight: 700; line-height: 14px; }
#mixed { left: 40px; top: 92px; font-family: Arial, "Malgun Gothic", sans-serif; font-size: 8px; font-weight: 400; line-height: 11px; }
#wrap { left: 40px; top: 120px; width: 72px; font-family: Arial, "Malgun Gothic", sans-serif; font-size: 10px; font-weight: 400; line-height: 12px; white-space: normal; }
#linebreak { left: 145px; top: 120px; width: 72px; font-family: Arial, sans-serif; font-size: 10px; font-weight: 700; line-height: 12px; white-space: pre-wrap; }
#special { left: 40px; top: 180px; font-family: Arial, "Malgun Gothic", sans-serif; font-size: 11px; font-weight: 400; line-height: 14px; }
#matrix-table { position: absolute; left: 700px; top: 300px; border-collapse: collapse; font: 700 10px/12px Arial, "Malgun Gothic", sans-serif; color: #1b1b1b; }
#matrix-table td { width: 65px; height: 20px; border: 1px solid #222; padding: 2px; }
#chart { position: absolute; left: 980px; top: 300px; width: 180px; height: 90px; }
</style></head><body>
<span id="english" class="font">English bold</span>
<span id="korean" class="font">한글 굵게</span>
<span id="mixed" class="font">A한B글 8px</span>
<span id="wrap" class="font">한글 English fallback width wraps here</span>
<span id="linebreak" class="font">first\nsecond</span>
<span id="special" class="font">① ≤ → “한글” € %</span>
<table id="matrix-table"><tr><td>한글</td><td>English</td></tr><tr><td>① ≤</td><td>42%</td></tr></table>
<svg id="chart" viewBox="0 0 180 90" aria-label="chart"><rect x="10" y="25" width="35" height="55" fill="#0072ce"/><rect x="60" y="10" width="35" height="70" fill="#00a878"/><polyline points="10,70 78,22 150,48" fill="none" stroke="#ff4d00" stroke-width="4"/></svg>
<script>
window.addEventListener('DOMContentLoaded', () => {
  const metrics = {};
  for (const id of ['english', 'korean', 'mixed', 'wrap', 'linebreak', 'special']) {
    const element = document.getElementById(id);
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    metrics[id] = {
      family: style.fontFamily,
      size: style.fontSize,
      weight: style.fontWeight,
      lineHeight: style.lineHeight,
      width: rect.width,
      height: rect.height,
      scrollHeight: element.scrollHeight,
    };
  }
  const context = document.createElement('canvas').getContext('2d');
  const text = 'mmmmmmmmmlli한글①';
  const isAvailable = (font) => ['monospace', 'sans-serif', 'serif'].some((base) => {
    context.font = `72px ${base}`;
    const baseline = context.measureText(text).width;
    context.font = `72px "${font}", ${base}`;
    return context.measureText(text).width !== baseline;
  });
  metrics.fontAvailability = {
    arial: isAvailable('Arial'),
    malgunGothic: isAvailable('Malgun Gothic'),
    notoSansKr: isAvailable('Noto Sans KR'),
    definitelyMissing: isAvailable('Definitely Missing Font'),
  };
  document.body.dataset.fontMetrics = JSON.stringify(metrics);
});
</script>
</body></html>"""


def _solid_run_length(image: Image.Image, start_x: int, y: int, color: tuple[int, int, int]) -> int:
    length = 0
    while image.getpixel((start_x + length, y)) == color:
        length += 1
    return length


def _dump_font_metrics_in_headless_shell(html: str) -> dict[str, dict[str, object]]:
    """Run the screenshot browser's flags with --dump-dom to read computed styles."""
    chrome = slide_capture.find_chrome()
    assert chrome, "headless Chrome is required"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        root = pathlib.Path(temp_dir)
        html_path = root / "font-matrix.html"
        html_path.write_text(html, encoding="utf-8")
        args = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            f"--user-data-dir={root / 'profile'}",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={slide_capture.SLIDE_W},{slide_capture.SLIDE_H}",
            "--virtual-time-budget=20000",
            "--dump-dom",
            html_path.as_uri(),
        ]
        kwargs: dict[str, object] = {"capture_output": True, "text": True, "timeout": 60}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(args, **kwargs)
    assert result.returncode == 0, result.stderr
    match = re.search(r'data-font-metrics="([^"]+)"', result.stdout)
    assert match, result.stdout
    return json.loads(html_module.unescape(match.group(1)))


def _ink_count(image: Image.Image, bounds: tuple[int, int, int, int]) -> int:
    left, top, right, bottom = bounds
    cropped = image.crop((left, top, right, bottom))
    pixels = (
        cropped.get_flattened_data()
        if hasattr(cropped, "get_flattened_data")
        else cropped.getdata()
    )
    return sum(
        pixel != (255, 255, 255)
        for pixel in pixels
    )


@pytest.mark.skipif(slide_capture.find_chrome() is None, reason="headless Chrome is required")
def test_actual_render_preserves_small_text_crop_overlap_and_image_geometry():
    """The renderer must preserve authored CSS rather than correct the visual design."""
    png = asyncio.run(slide_capture._render_html_to_png_uncached(_RENDER_POLICY_HTML))
    image = Image.open(io.BytesIO(png)).convert("RGB")

    assert image.size == (slide_capture.SLIDE_W, slide_capture.SLIDE_H)
    assert _solid_run_length(image, 20, 21, (255, 0, 0)) == 8
    assert _solid_run_length(image, 50, 21, (0, 170, 0)) == 10
    assert _solid_run_length(image, 80, 21, (0, 0, 255)) == 11

    # Author-defined stacking order and clipping must remain untouched.
    assert image.getpixel((510, 110)) == (0, 85, 255)
    assert image.getpixel((540, 140)) == (255, 51, 0)
    assert image.getpixel((699, 110)) == (255, 255, 255)
    assert image.getpixel((700, 110)) == (0, 102, 153)
    assert image.getpixel((710, 110)) == (0, 102, 153)
    assert image.getpixel((730, 110)) == (0, 102, 153)
    assert image.getpixel((740, 110)) == (255, 255, 255)


@pytest.mark.skipif(slide_capture.find_chrome() is None, reason="headless Chrome is required")
def test_windows_headless_shell_preserves_font_metrics_text_and_table_chart_geometry():
    """Exercise small Korean/English text through the real Windows headless shell."""
    metrics = _dump_font_metrics_in_headless_shell(_FONT_MATRIX_HTML)

    assert metrics["english"]["family"] == "Arial, sans-serif"
    assert metrics["korean"]["family"] == 'Arial, "Malgun Gothic", sans-serif'
    assert metrics["mixed"]["family"] == 'Arial, "Malgun Gothic", sans-serif'
    assert [metrics[key]["size"] for key in ("mixed", "korean", "english")] == ["8px", "10px", "11px"]
    assert metrics["english"]["weight"] == "700"
    assert metrics["korean"]["weight"] == "700"
    assert metrics["mixed"]["lineHeight"] == "11px"
    assert metrics["korean"]["lineHeight"] == "14px"
    assert metrics["english"]["lineHeight"] == "13px"
    assert metrics["korean"]["width"] > 20
    assert metrics["english"]["width"] > 20
    assert metrics["wrap"]["height"] >= 24
    assert metrics["linebreak"]["height"] >= 24
    assert metrics["special"]["width"] > 20
    assert metrics["fontAvailability"]["definitelyMissing"] is False
    if os.name == "nt":
        assert metrics["fontAvailability"]["arial"] is True
        assert metrics["fontAvailability"]["malgunGothic"] is True

    safe_render_html = re.sub(
        r"<script>.*?</script>", "", _FONT_MATRIX_HTML, flags=re.DOTALL | re.IGNORECASE
    )
    image = Image.open(
        io.BytesIO(asyncio.run(slide_capture._render_html_to_png_uncached(safe_render_html)))
    ).convert("RGB")
    assert _ink_count(image, (35, 35, 240, 115)) > 100  # English, Korean, mixed 8px
    assert _ink_count(image, (35, 115, 230, 165)) > 100  # natural and explicit line wraps
    assert _ink_count(image, (35, 175, 240, 205)) > 40  # Unicode punctuation/symbols
    assert image.getpixel((700, 300)) == (34, 34, 34)  # table border remains exact
    assert image.getpixel((1000, 350)) == (0, 114, 206)  # chart bar remains exact


def test_render_cache_hits_and_render_policy_version_invalidates(monkeypatch: pytest.MonkeyPatch):
    """A policy version change must not reuse an older rendered PNG."""
    calls = 0

    async def fake_uncached(html: str, timeout: int = 60) -> bytes:
        nonlocal calls
        calls += 1
        return f"png-{calls}:{html}:{timeout}".encode()

    original_size = slide_capture.RENDER_CACHE_SIZE
    slide_capture._render_cache.clear()
    slide_capture._render_inflight.clear()
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 2)
    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)
    try:
        first = asyncio.run(slide_capture.render_html_to_png("<p>8px</p>"))
        cached = asyncio.run(slide_capture.render_html_to_png("<p>8px</p>"))
        assert cached == first
        assert calls == 1

        monkeypatch.setattr(
            slide_capture,
            "RENDER_CACHE_VERSION",
            f"{slide_capture.RENDER_CACHE_VERSION}-test-next-policy",
        )
        assert asyncio.run(slide_capture.render_html_to_png("<p>8px</p>")) != first
        assert calls == 2
    finally:
        slide_capture.RENDER_CACHE_SIZE = original_size
        slide_capture._render_cache.clear()
        slide_capture._render_inflight.clear()
