import asyncio
import json
import re


from utils import slide_capture

HTML = """<!doctype html><html><head><style>
html,body{margin:0;width:1280px;height:720px}.box{position:absolute;left:12px;top:16px;width:80px;height:20px;overflow:hidden}
</style></head><body><div class="box">Metric 42%</div></body></html>"""


def test_metrics_parser_rejects_missing_or_malformed_payload(monkeypatch):
    from models.authored_vnext.capability_registry import (
        CAPABILITY_REGISTRY_DIGEST,
        require_capability_metadata,
    )

    assert slide_capture._extract_browser_metrics(b"") is None
    assert (
        slide_capture._extract_browser_metrics(
            b'<meta id="__codex_authored_dom_metrics" content="%7Bbad">'
        )
        is None
    )
    instrumented, _ = slide_capture._instrument_authored_html(HTML, issue_capability=False)
    metadata_match = re.search(r"capabilityRegistry: (\{[^\n]+\}),", instrumented)
    assert metadata_match is not None
    require_capability_metadata(json.loads(metadata_match.group(1)), consumer="chromium")
    assert "owned_text: ownedTextNodes(node)" in instrumented
    failed_probe = instrumented.split("const failed = (error) =>", 1)[1]
    assert "capabilityRegistry" not in failed_probe

    # Extraction must not upgrade historical or failed probe results by adding
    # a current registry binding after the browser has already completed.
    for payload in (
        b'{"width":1280,"height":720,"elements":[]}',
        b'{"elements":[],"probe_error":"failed"}',
    ):
        raw = b'<meta id="__codex_authored_dom_metrics" content=\'' + payload + b"'>"
        extracted = slide_capture._extract_browser_metrics(raw)
        assert extracted is not None
        assert "capabilityRegistry" not in extracted

    runtime = slide_capture.get_render_runtime_snapshot()
    assert runtime["cacheIdentity"]["capabilityRegistryDigest"] == CAPABILITY_REGISTRY_DIGEST
    cache_identity = slide_capture._render_cache_identity(HTML).digest
    evidence_key = slide_capture._metric_evidence_cache_key(HTML)
    monkeypatch.setattr(slide_capture, "CAPABILITY_REGISTRY_DIGEST", "sha256:" + "f" * 64)
    assert slide_capture._render_cache_identity(HTML).digest != cache_identity
    assert slide_capture._metric_evidence_cache_key(HTML) != evidence_key


def test_render_cache_hit_reuses_atomic_capture_after_metrics_eviction(monkeypatch):
    slide_capture._render_cache.clear()
    slide_capture._browser_metrics_cache.clear()
    slide_capture._capture_cache.clear()
    slide_capture._browser_metrics_inflight.clear()
    calls = 0

    async def fake_uncached(html, timeout=60, **_kwargs):
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
    # A retained PNG must not trigger a second browser pass.
    assert calls == 1
    assert metrics is not None
    assert metrics["width"] == 1280


def test_metrics_recollection_failure_is_explicitly_unavailable(monkeypatch):
    slide_capture._render_cache.clear()
    slide_capture._browser_metrics_cache.clear()
    slide_capture._capture_cache.clear()
    slide_capture._browser_metrics_inflight.clear()

    async def fake_uncached(html, timeout=60, **_kwargs):
        return b"png-without-probe"

    async def failed_recollection(*_args, **_kwargs):
        raise RuntimeError("metrics probe unavailable")

    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)
    monkeypatch.setattr(slide_capture, "_render_html_to_png_subprocess", failed_recollection)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 8)

    png, metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(HTML))

    assert png == b"png-without-probe"
    assert metrics is None
