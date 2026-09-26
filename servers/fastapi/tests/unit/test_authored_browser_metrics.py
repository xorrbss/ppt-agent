import asyncio
import json
import re

import pytest

from utils import slide_capture

HTML = """<!doctype html><html><head><style>
html,body{margin:0;width:1280px;height:720px}.box{position:absolute;left:12px;top:16px;width:80px;height:20px;overflow:hidden}
</style></head><body><div class="box">Metric 42%</div></body></html>"""


def test_metrics_parser_rejects_missing_or_malformed_payload(monkeypatch):
    from services.authored_vnext.capability_registry import (
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


@pytest.mark.skipif(
    slide_capture.find_chrome() is None, reason="headless Chrome is required"
)
def test_render_collects_dom_metrics_in_the_same_browser_invocation(monkeypatch):
    import base64
    import hashlib
    import os
    from pathlib import Path

    from utils.slide_capture_glyph_probe import (
        GLYPH_CLIPPING_SENSOR_VERSION, apply_glyph_probe,
    )

    monkeypatch.setattr(slide_capture, "CHROME_REUSE_ENABLED", True)
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

    numeric = """<!doctype html><html><head><style>
    html,body{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Inter,Aptos,sans-serif}
    .field{position:absolute;left:40px;top:92px;width:446px;height:544px;background:#2546a0;overflow:hidden}
    .metric{position:absolute;left:42px;bottom:38px;margin:0;font-size:250px;line-height:.78;font-weight:900;color:white}
    .true{left:700px;height:400px}.true .metric{bottom:-12px}
    </style></head><body>
    <div class="field" data-qid="false.field"><p class="metric" data-qid="false.metric">5</p></div>
    <div class="field true" data-qid="true.field"><p class="metric" data-qid="true.metric">5</p></div>
    </body></html>"""
    numeric_digest = hashlib.sha256(numeric.encode()).hexdigest()
    png, metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(numeric))
    assert hashlib.sha256(numeric.encode()).hexdigest() == numeric_digest
    assert metrics["dom_metrics_version"] == slide_capture.DOM_METRICS_VERSION
    owners = {item["qid"]: item for item in metrics["elements"]}
    visible, clipped = owners["false.metric"], owners["true.metric"]
    assert visible["range_clip_suspect"] is True
    assert visible["glyph_clipping_evidence"]["status"] == "measured", visible["glyph_clipping_evidence"]
    assert visible["glyph_clipping_evidence"]["paint_loss_pixels"] == 0
    assert visible["glyph_clipping_evidence"]["original_png_digest"] == hashlib.sha256(png).hexdigest()
    assert not any(visible[key] for key in ("clipped", "glyph_clipped", "line_box_clipped"))
    assert clipped["range_clip_suspect"] and clipped["clipped"] and clipped["glyph_clipped"]
    assert clipped["glyph_clipping_evidence"]["paint_loss_pixels"] > 0, clipped["glyph_clipping_evidence"]
    raw = json.loads(json.dumps(metrics))
    for item in raw["elements"]:
        if item.get("range_clip_suspect"):
            item.update(clipped=True, glyph_clipped=True, line_box_clipped=True)
    incomplete = apply_glyph_probe(raw, {"targets": ["false.metric", "true.metric"], "height": 2880}, None, None, png)
    for item in incomplete["elements"]:
        if item.get("range_clip_suspect"):
            assert item["clipped"] and item["glyph_clipped"] and item["line_box_clipped"]
            assert item["measurement"]["clipping"] is False
            assert item["glyph_clipping_evidence"]["status"] == "unknown"
    runtime = slide_capture.get_render_runtime_snapshot()
    assert runtime["cacheIdentity"]["clippingSensor"] == GLYPH_CLIPPING_SENSOR_VERSION
    identity = slide_capture._render_cache_identity(numeric).digest
    with monkeypatch.context() as patch:
        patch.setattr(slide_capture, "MEASUREMENT_CACHE_VERSION", "authored-measurement/v2-owned-text")
        assert slide_capture._render_cache_identity(numeric).digest != identity

    rounded = """<!doctype html><html><head><style>
    html,body{margin:0;width:1280px;height:720px;overflow:hidden;font-family:Inter,Aptos,sans-serif}
    .mark{position:absolute;top:305.75px;width:34px;height:34px;box-sizing:border-box;
      display:flex;align-items:center;justify-content:center;border:2px solid #2563eb;border-radius:18px;
      color:#2563eb;font-size:20px;line-height:20px;font-weight:800}
    .visible{left:810px}.covered{left:710px}.unknown{left:610px;box-shadow:0 0 2px blue}
    .corner{left:510px;align-items:flex-start;justify-content:flex-start;font-size:12px}
    .intruder{position:absolute;left:710px;top:305.75px;width:34px;height:34px;background:#123456}
    </style></head><body>
    <div class="mark visible" data-qid="rounded.visible">✓</div>
    <div class="mark covered" data-qid="rounded.covered">✓</div>
    <div class="mark unknown" data-qid="rounded.unsupported">✓</div>
    <div class="mark corner" data-qid="rounded.corner-text">M</div>
    <div class="intruder" data-qid="rounded.intruder"></div>
    </body></html>"""
    _png, rounded_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(rounded))
    rounded_owners = {item["qid"]: item for item in rounded_metrics["elements"]}
    for qid in ("rounded.visible", "rounded.covered"):
        item = rounded_owners[qid]
        assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
        assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] > 0
        assert item["occlusion_sensor_version"] == slide_capture.OCCLUSION_SENSOR_VERSION
    assert rounded_owners["rounded.visible"]["occluded_fraction"] == 0
    assert rounded_owners["rounded.covered"]["occluded_fraction"] > 0.95
    assert "rounded.intruder" in rounded_owners["rounded.covered"]["occlusion_occluder_qids"]
    unknown = rounded_owners["rounded.unsupported"]
    assert unknown["measurement"]["occlusion"] is False
    assert unknown["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] == 0
    assert unknown["occlusion_measurement_evidence"]["rounded_domain_unsupported_rects"] == 1
    assert rounded_owners["rounded.corner-text"]["occlusion_owned_rect_sample_count"] == 18
    assert runtime["cacheIdentity"]["occlusionSensor"] == slide_capture.OCCLUSION_SENSOR_VERSION

    independent = """<!doctype html><html><head><style>
    html,body{margin:0;width:1280px;height:720px;overflow:hidden}
    .status{position:absolute;top:150.25px;width:42px;height:42px;box-sizing:border-box;
      border-radius:24px;background:#f8fafc;border:1px solid #e2e8f0;
      display:flex;align-items:center;justify-content:center}
    .mark{box-sizing:border-box;width:15px;height:8px;border-left:3px solid #2563eb;
      border-bottom:3px solid #2563eb;transform:rotate(-45deg) translateY(-2px)}
    .visible{left:100px}.covered{left:200px}.missing{left:300px;pointer-events:none}
    .unowned{left:400px}.multiple{left:500px}.child-covered{left:600px}
    .intruder{position:absolute;z-index:3;left:200px;top:150.25px;width:42px;height:42px;background:#123456}
    .child-intruder{left:609px;top:159.25px;width:24px;height:24px}
    .two{top:250.5px;width:28px;height:28px;border:2px solid #2563eb;border-radius:18px;background:white}
    .stem{position:absolute;left:12px;top:5px;width:2px;height:11px;background:#2563eb}
    .dot{position:absolute;left:11px;top:19px;width:4px;height:4px;border-radius:18px;background:#2563eb}
    .two-visible{left:100px}.two-covered{left:200px}.two-unowned{left:300px}
    .two-duplicate{left:400px}.two-child-covered{left:500px}
    .two-intruder{left:200px;top:250.5px;width:28px;height:28px}
    .two-child-intruder{left:513px;top:271.5px;width:4px;height:4px}
    </style></head><body>
    <div class="status visible" data-qid="status.visible"><div class="mark" data-qid="mark.visible"></div></div>
    <div class="status covered" data-qid="status.covered"><div class="mark" data-qid="mark.covered"></div></div>
    <div class="status missing" data-qid="status.missing"><div class="mark" data-qid="mark.missing"></div></div>
    <div class="status unowned" data-qid="status.unowned"><div class="mark" data-qid-ignore=""></div></div>
    <div class="status multiple" data-qid="status.multiple"><div class="mark" data-qid="mark.first"></div><div class="mark" data-qid="mark.second"></div><div class="mark" data-qid="mark.third"></div></div>
    <div class="status child-covered" data-qid="status.child-covered"><div class="mark" data-qid="mark.child-covered"></div></div>
    <div class="intruder" data-qid="status.intruder"></div>
    <div class="intruder child-intruder" data-qid="mark.intruder"></div>
    <div class="status two two-visible" data-qid="status.two-visible"><div class="stem" data-qid="stem.two-visible"></div><div class="dot" data-qid="dot.two-visible"></div></div>
    <div class="status two two-covered" data-qid="status.two-covered"><div class="stem" data-qid="stem.two-covered"></div><div class="dot" data-qid="dot.two-covered"></div></div>
    <div class="status two two-unowned" data-qid="status.two-unowned"><div class="stem" data-qid="stem.two-unowned"></div><div class="dot" data-qid-ignore=""></div></div>
    <div class="status two two-duplicate" data-qid="status.two-duplicate"><div class="stem" data-qid="child.two-duplicate"></div><div class="dot" data-qid="child.two-duplicate"></div></div>
    <div class="status two two-child-covered" data-qid="status.two-child-covered"><div class="stem" data-qid="stem.two-child-covered"></div><div class="dot" data-qid="dot.two-child-covered"></div></div>
    <div class="intruder two-intruder" data-qid="status.two-intruder"></div>
    <div class="intruder two-child-intruder" data-qid="dot.two-intruder"></div>
    </body></html>"""
    with monkeypatch.context() as patch:
        # Exercise the sensor's own duplicate-QID rejection without the earlier
        # authoring normalizer replacing the deliberately duplicated child ID.
        patch.setattr(slide_capture, "ensure_authored_qids", lambda html: html)
        _png, independent_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(independent))
    assert sum(item["qid"] == "child.two-duplicate" for item in independent_metrics["elements"]) == 2
    independent_owners = {item["qid"]: item for item in independent_metrics["elements"]}
    for qid in ("status.visible", "status.covered", "status.child-covered"):
        item = independent_owners[qid]
        assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
        assert item["occlusion_measurement_evidence"]["rounded_domain_supported_rects"] == 1
        assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] > 0
    assert independent_owners["status.visible"]["occluded_fraction"] == 0
    assert independent_owners["mark.visible"]["occluded_fraction"] == 0
    assert independent_owners["status.covered"]["occluded_fraction"] == 1
    assert independent_owners["mark.covered"]["occluded_fraction"] == 1
    child = independent_owners["mark.child-covered"]
    assert child["measurement"]["occlusion"] is True
    assert child["occluded_fraction"] == 1
    assert "mark.intruder" in child["occlusion_occluder_qids"]
    assert 0 < independent_owners["status.child-covered"]["occluded_fraction"] < 1
    assert independent_owners["status.missing"]["measurement"]["occlusion"] is False
    assert independent_owners["status.missing"]["occlusion_measurement_error"] == "owner_absent_from_hit_test"
    for qid in ("status.unowned", "status.multiple"):
        item = independent_owners[qid]
        assert item["measurement"]["occlusion"] is False
        assert item["occlusion_measurement_evidence"]["rounded_domain_unsupported_rects"] == 1
        assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] == 0
    for qid in ("status.two-visible", "status.two-covered", "status.two-child-covered"):
        item = independent_owners[qid]
        assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
        assert item["occlusion_measurement_evidence"]["rounded_domain_supported_rects"] == 1
        assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] > 0
    for qid in ("status.two-visible", "stem.two-visible", "dot.two-visible", "stem.two-child-covered"):
        assert independent_owners[qid]["occluded_fraction"] == independent_owners[qid]["occlusion_unknown_fraction"] == 0
    for qid in ("status.two-covered", "stem.two-covered", "dot.two-covered", "dot.two-child-covered"):
        assert independent_owners[qid]["measurement"]["occlusion"] is True
        assert independent_owners[qid]["occluded_fraction"] == 1
    assert "dot.two-intruder" in independent_owners["dot.two-child-covered"]["occlusion_occluder_qids"]
    assert 0 < independent_owners["status.two-child-covered"]["occluded_fraction"] < 1
    for qid in ("status.two-unowned", "status.two-duplicate"):
        item = independent_owners[qid]
        assert item["measurement"]["occlusion"] is False
        assert item["occlusion_measurement_evidence"]["rounded_domain_unsupported_rects"] == 1
        assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] == 0

    thin = """<!doctype html><html><head><style>
    html,body{margin:0;width:1280px;height:720px;overflow:hidden}
    .line{position:absolute;bottom:0;width:200px;height:1px;background:#2563eb}
    .visible{left:64px}.covered{left:364px}.missing{left:664px;pointer-events:none}
    .intruder{left:364px;background:#123456}
    .clipper{position:absolute;left:964px;top:719px;width:200px;height:.3px;overflow:hidden}
    .border-clipper{position:absolute;left:64px;top:0;width:200px;height:1.3px;box-sizing:border-box;overflow:hidden;border-bottom:1px solid transparent}
    .clipper .line,.border-clipper .line{left:0;top:0;bottom:auto}
    </style></head><body>
    <div class="line visible" data-qid="thin.visible"></div>
    <div class="line covered" data-qid="thin.covered"></div>
    <div class="line missing" data-qid="thin.missing"></div>
    <div class="line intruder" data-qid="thin.intruder"></div>
    <div class="clipper" data-qid="thin.clip-parent"><div class="line" data-qid="thin.clipped"></div></div>
    <div class="border-clipper" data-qid="thin.border-parent"><div class="line" data-qid="thin.border-clipped"></div></div>
    </body></html>"""
    _png, thin_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(thin))
    thin_owners = {item["qid"]: item for item in thin_metrics["elements"]}
    for qid in ("thin.visible", "thin.covered"):
        item = thin_owners[qid]
        assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
        assert item["occlusion_measurement_evidence"]["thin_edge_recovered_samples"] == 43
        assert item["occlusion_sample_count"] == item["occlusion_known_sample_count"] == 203
    assert thin_owners["thin.visible"]["occluded_fraction"] == 0
    assert thin_owners["thin.covered"]["occluded_fraction"] == 1
    assert "thin.intruder" in thin_owners["thin.covered"]["occlusion_occluder_qids"]
    assert thin_owners["thin.missing"]["measurement"]["occlusion"] is False
    assert thin_owners["thin.missing"]["occlusion_measurement_error"] == "owner_absent_from_hit_test"
    assert thin_owners["thin.clipped"]["measurement"]["occlusion"] is False
    assert thin_owners["thin.clipped"]["occlusion_measurement_evidence"]["thin_edge_retry_count"] == 0
    assert thin_owners["thin.border-clipped"]["measurement"]["occlusion"] is False
    assert thin_owners["thin.border-clipped"]["occlusion_measurement_evidence"]["thin_edge_retry_count"] == 0

    polygon = """<!doctype html><html><head><style>
    html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#2546a0}
    .poly{position:absolute;top:70px;width:100px;height:150px;
      background:linear-gradient(135deg,rgba(255,255,255,.13),rgba(255,255,255,.03));
      clip-path:polygon(64% 0,100% 0,100% 100%,0 100%)}
    .visible{left:20px}.covered{left:160px}.missing{left:300px;pointer-events:none}
    .concave{left:440px;clip-path:polygon(0 0,100% 0,30% 50%,0 100%)}
    .transformed{left:580px;transform:translateX(0)}.text{left:720px}.children{left:860px}
    .intruder{position:absolute;left:160px;top:70px;width:100px;height:150px;background:#123456}
    .clipper{position:absolute;left:20px;top:350px;width:100px;height:40px;border-radius:20px;overflow:hidden}
    .clipper .poly{left:0;top:0}.radius{left:160px;top:350px;border-radius:15px}
    .clipper .clip-full{clip-path:polygon(0% 0%,100% 0%,100% 100%,0% 100%)}
    .boundary{left:440px;top:350px;height:100px;clip-path:polygon(50.5px 0px,100px 0px,100px 100px,50.5px 100px)}
    .fractional{left:1050.25px;top:250.5px;width:130.71875px;height:382.609375px}
    .mapped-fractional{left:580px;top:350px;width:130.71875px;height:100.609375px;transform:translateX(0)}
    </style></head><body>
    <div class="poly visible" data-qid="poly.visible"></div>
    <div class="poly covered" data-qid="poly.covered"></div>
    <div class="poly missing" data-qid="poly.missing"></div>
    <div class="poly concave" data-qid="poly.concave"></div>
    <div class="poly transformed" data-qid="poly.transformed"></div>
    <div class="poly text" data-qid="poly.text">X</div>
    <div class="poly children" data-qid="poly.children"><div data-qid="poly.child"></div></div>
    <div class="intruder" data-qid="poly.intruder"></div>
    <div class="clipper" data-qid="poly.clip-parent"><div class="poly" data-qid="poly.clipped"></div><div class="poly clip-full" data-qid="poly.clip-inside"></div></div>
    <div class="poly radius" data-qid="poly.radius"></div>
    <div class="poly boundary" data-qid="poly.boundary"></div>
    <div class="poly fractional" data-qid="poly.fractional"></div>
    <div class="poly mapped-fractional" data-qid="poly.mapped-fractional"></div>
    </body></html>"""
    polygon_digest = hashlib.sha256(polygon.encode()).hexdigest()
    _png, polygon_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(polygon))
    assert hashlib.sha256(polygon.encode()).hexdigest() == polygon_digest
    polygon_owners = {item["qid"]: item for item in polygon_metrics["elements"]}
    for qid in ("poly.visible", "poly.covered", "poly.missing", "poly.clipped", "poly.boundary"):
        item = polygon_owners[qid]
        assert item["occlusion_measurement_evidence"]["polygon_domain_supported_rects"] == 1
        assert item["occlusion_measurement_evidence"]["polygon_domain_excluded_samples"] > 0
    assert polygon_owners["poly.visible"]["measurement"]["occlusion"] is True
    assert polygon_owners["poly.visible"]["occluded_fraction"] == 0
    assert polygon_owners["poly.covered"]["occluded_fraction"] == 1
    assert polygon_owners["poly.covered"]["occlusion_occluder_qids"] == ["poly.intruder"]
    assert polygon_owners["poly.covered"]["occlusion_measurement_evidence"]["polygon_edge_recovered_samples"] > 0
    assert polygon_owners["poly.missing"]["measurement"]["occlusion"] is False
    assert polygon_owners["poly.missing"]["occlusion_unknown_fraction"] == 1
    # Own polygon excludes no ancestor clipping: these still-painted points
    # remain unknown when the parent crops the child, not relabelled empty.
    assert polygon_owners["poly.clipped"]["measurement"]["occlusion"] is False
    assert polygon_owners["poly.clipped"]["occlusion_unknown_fraction"] > .05
    assert polygon_owners["poly.clip-inside"]["measurement"]["occlusion"] is False
    assert polygon_owners["poly.clip-inside"]["occlusion_unknown_fraction"] > .05
    assert polygon_owners["poly.clip-inside"]["occlusion_measurement_evidence"]["polygon_edge_retry_count"] == 0
    for qid in ("poly.concave", "poly.transformed", "poly.text", "poly.children", "poly.radius"):
        item = polygon_owners[qid]
        assert item["occlusion_measurement_evidence"]["polygon_domain_supported_rects"] == 0
        assert item["occlusion_measurement_evidence"]["polygon_domain_unsupported_rects"] == 1
        assert item["occlusion_measurement_evidence"]["polygon_domain_excluded_samples"] == 0
    # The x=50 sample is .5 CSSpx outside the polygon. All 25 coarse and
    # three owned-rect points in that boundary column must remain sampled.
    assert polygon_owners["poly.boundary"]["occlusion_sample_count"] == 331
    assert polygon_owners["poly.boundary"]["occlusion_owned_rect_sample_count"] == 6
    assert polygon_owners["poly.boundary"]["occlusion_measurement_evidence"]["polygon_edge_recovered_samples"] == 28
    fractional = polygon_owners["poly.fractional"]
    box = fractional["bbox"]
    assert box == {"x": 1050.25, "y": 250.5, "width": 130.71875, "height": 382.609375}
    assert fractional["quad"] == [
        {"x": box["x"], "y": box["y"]},
        {"x": box["x"] + box["width"], "y": box["y"]},
        {"x": box["x"] + box["width"], "y": box["y"] + box["height"]},
        {"x": box["x"], "y": box["y"] + box["height"]},
    ]
    # A non-none transform stays on the existing matrix fallback, even when
    # its matrix happens to be identity; no broad transform rewrite is made.
    mapped = polygon_owners["poly.mapped-fractional"]
    assert mapped["quad"][1]["x"] - mapped["quad"][0]["x"] == 131
    assert mapped["quad"][2]["y"] - mapped["quad"][1]["y"] == 101

    # Optional retained product evidence is supplied only by the local scoped run;
    # no provider, DB or new product generation is involved.
    retained = os.getenv("AUTHORED_GLYPH_PROBE_RETAINED_JSON")
    if retained:
        evidence = json.loads(Path(retained).read_text(encoding="utf-8"))
        original_png = base64.b64decode(evidence["pngBase64"])
        fresh_png, fresh_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(evidence["html"]))
        assert fresh_png == original_png  # Sensor-only change preserves exact original PNG bytes.
        metric = next(item for item in fresh_metrics["elements"] if item["qid"] == "cover.metric")
        assert metric["range_clip_suspect"] is True
        assert metric["glyph_clipping_evidence"]["status"] == "measured", metric["glyph_clipping_evidence"]
        assert metric["glyph_clipping_evidence"]["paint_loss_pixels"] == 0
        assert not any(metric[key] for key in ("clipped", "glyph_clipped", "line_box_clipped"))

    retained_rounded = os.getenv("AUTHORED_ROUNDED_PROBE_RETAINED_JSON")
    if retained_rounded:
        evidence = json.loads(Path(retained_rounded).read_text(encoding="utf-8"))
        original_png = base64.b64decode(evidence["pngBase64"])
        source_digest = hashlib.sha256(evidence["html"].encode()).hexdigest()
        fresh_png, fresh_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(evidence["html"]))
        assert fresh_png == original_png
        assert hashlib.sha256(evidence["html"].encode()).hexdigest() == source_digest
        fresh_owners = {item["qid"]: item for item in fresh_metrics["elements"]}
        for qid in ("slide5.check_accessibility_mark", "slide5.check_contrast_mark", "slide5.check_media_mark"):
            item = fresh_owners[qid]
            assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
            assert item["occluded_fraction"] == 0
            assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] > 0
            original = next(value for value in evidence["metrics"]["elements"] if value["qid"] == qid)
            assert item["bbox"] == original["bbox"]
            assert item["computed_style"] == original["computed_style"]

    retained_thin = os.getenv("AUTHORED_THIN_EDGE_PROBE_RETAINED_JSON")
    if retained_thin:
        evidence = json.loads(Path(retained_thin).read_text(encoding="utf-8"))
        original_png = base64.b64decode(evidence["pngBase64"])
        source_digest = hashlib.sha256(evidence["html"].encode()).hexdigest()
        fresh_png, fresh_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(evidence["html"]))
        assert fresh_png == original_png
        assert hashlib.sha256(evidence["html"].encode()).hexdigest() == source_digest
        qid = "q-div-59f86380b966"
        item = next(value for value in fresh_metrics["elements"] if value["qid"] == qid)
        original = next(value for value in evidence["metrics"]["elements"] if value["qid"] == qid)
        assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
        assert item["occluded_fraction"] == 0
        assert item["occlusion_sample_count"] == original["occlusion_sample_count"] == 203
        assert item["occlusion_known_sample_count"] == 203
        assert item["occlusion_measurement_evidence"]["thin_edge_recovered_samples"] == 43
        assert item["bbox"] == original["bbox"]
        assert item["computed_style"] == original["computed_style"]

    retained_independent = os.getenv("AUTHORED_ROUNDED_CHILD_PROBE_RETAINED_JSON")
    if retained_independent:
        evidence = json.loads(Path(retained_independent).read_text(encoding="utf-8"))
        original_png = base64.b64decode(evidence["pngBase64"])
        source_digest = hashlib.sha256(evidence["html"].encode()).hexdigest()
        fresh_png, fresh_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(evidence["html"]))
        assert fresh_png == original_png
        assert hashlib.sha256(evidence["html"].encode()).hexdigest() == source_digest
        fresh_owners = {item["qid"]: item for item in fresh_metrics["elements"]}
        original_owners = {item["qid"]: item for item in evidence["metrics"]["elements"]}
        for prefix in ("asset.fonts", "asset.media", "asset.ratio"):
            for suffix in ("status", "mark"):
                qid = f"{prefix}.{suffix}"
                item, original = fresh_owners[qid], original_owners[qid]
                assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
                assert item["occluded_fraction"] == item["occlusion_unknown_fraction"] == 0
                assert item["bbox"] == original["bbox"]
                assert item["computed_style"] == original["computed_style"]
                assert item["text_fragments"] == original["text_fragments"]
                assert item["quad"] == original["quad"]
                if suffix == "status":
                    assert item["occlusion_measurement_evidence"]["rounded_domain_supported_rects"] == 1
                    assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] > 0
                    assert item["occlusion_sample_count"] < original["occlusion_sample_count"]
                else:
                    assert item["occlusion_sample_count"] == original["occlusion_sample_count"]
                    assert item["measurement"] == original["measurement"]
                print("retained-rounded-child", qid, {
                    key: item[key] for key in ("occlusion_sample_count", "occlusion_known_sample_count", "occlusion_unknown_fraction")
                }, "excluded", item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"])

    retained_two_children = os.getenv("AUTHORED_ROUNDED_TWO_CHILD_PROBE_RETAINED_JSON")
    if retained_two_children:
        evidence_path = Path(retained_two_children)
        original_bytes = evidence_path.read_bytes()
        assert hashlib.sha256(original_bytes).hexdigest() == evidence_path.name
        evidence = json.loads(original_bytes)
        original_png = base64.b64decode(evidence["pngBase64"])
        source_digest = hashlib.sha256(evidence["html"].encode()).hexdigest()
        fresh_png, fresh_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(evidence["html"]))
        assert fresh_png == original_png and evidence_path.read_bytes() == original_bytes
        assert fresh_metrics["source_digest"] == "sha256:" + source_digest == evidence["metrics"]["source_digest"]
        fresh_owners = {item["qid"]: item for item in fresh_metrics["elements"]}
        original_owners = {item["qid"]: item for item in evidence["metrics"]["elements"]}
        assert list(fresh_owners) == list(original_owners)
        for qid, item in fresh_owners.items():
            for key in ("bbox", "quad", "computed_style", "owned_text", "rendered_text", "text_fragments", "semantic_role", "parent_qid", "source_block_id"):
                assert item[key] == original_owners[qid][key]
        for qid in ("check.fonts.status", "check.spacing.status"):
            item, original = fresh_owners[qid], original_owners[qid]
            assert (original["occlusion_sample_count"], original["occlusion_known_sample_count"]) == (58, 54)
            assert item["measurement"]["occlusion"] is True, item["occlusion_measurement_evidence"]
            assert item["occluded_fraction"] == item["occlusion_unknown_fraction"] == 0
            assert item["occlusion_sample_count"] == item["occlusion_known_sample_count"] < 58
            assert item["occlusion_measurement_evidence"]["rounded_domain_supported_rects"] == 1
            assert item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"] > 0
            for suffix in ("stem", "dot"):
                child, prior_child = fresh_owners[f"{qid}.{suffix}"], original_owners[f"{qid}.{suffix}"]
                for key in ("measurement", "occluded_fraction", "continuous_occluded_fraction", "occlusion_sample_count", "occlusion_known_sample_count", "occlusion_coarse_sample_count", "occlusion_owned_rect_sample_count", "occlusion_confirmed_covered_sample_count", "occlusion_unknown_fraction", "occlusion_occluder_qids"):
                    assert child[key] == prior_child[key]
            print("retained-rounded-two-children", qid, {
                key: item[key] for key in ("occlusion_sample_count", "occlusion_known_sample_count", "occlusion_unknown_fraction")
            }, "excluded", item["occlusion_measurement_evidence"]["rounded_domain_excluded_samples"])

    retained_polygon = os.getenv("AUTHORED_POLYGON_PROBE_RETAINED_JSON")
    if retained_polygon:
        evidence_path = Path(retained_polygon)
        original_bytes = evidence_path.read_bytes()
        assert hashlib.sha256(original_bytes).hexdigest() == evidence_path.name
        evidence = json.loads(original_bytes)
        source_digest = hashlib.sha256(evidence["html"].encode()).hexdigest()
        fresh_png, fresh_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(evidence["html"]))
        assert fresh_png == base64.b64decode(evidence["pngBase64"])
        assert evidence_path.read_bytes() == original_bytes
        assert fresh_metrics["source_digest"] == "sha256:" + source_digest == evidence["metrics"]["source_digest"]
        original_owners = {item["qid"]: item for item in evidence["metrics"]["elements"]}
        fresh_owners = {item["qid"]: item for item in fresh_metrics["elements"]}
        assert list(fresh_owners) == list(original_owners)
        for qid, item in fresh_owners.items():
            for key in ("bbox", "computed_style", "owned_text", "rendered_text", "text_fragments", "semantic_role", "parent_qid", "source_block_id"):
                assert item[key] == original_owners[qid][key]
            if item["quad"] != original_owners[qid]["quad"]:
                import math

                # The old fallback rounded offsetWidth/Height. Only that
                # diagnosed identity-mapping measurement correction is allowed.
                box = item["bbox"]
                assert item["computed_style"]["transform"] == "none"
                exact = [
                    {"x": box["x"], "y": box["y"]},
                    {"x": box["x"] + box["width"], "y": box["y"]},
                    {"x": box["x"] + box["width"], "y": box["y"] + box["height"]},
                    {"x": box["x"], "y": box["y"] + box["height"]},
                ]
                rounded = [
                    {"x": box["x"], "y": box["y"]},
                    {"x": box["x"] + math.floor(box["width"] + .5), "y": box["y"]},
                    {"x": box["x"] + math.floor(box["width"] + .5), "y": box["y"] + math.floor(box["height"] + .5)},
                    {"x": box["x"], "y": box["y"] + math.floor(box["height"] + .5)},
                ]
                assert item["quad"] == exact
                assert original_owners[qid]["quad"] == rounded
        item = fresh_owners["risk.metric.diagonal"]
        original = original_owners["risk.metric.diagonal"]
        assert (original["occlusion_sample_count"], original["occlusion_known_sample_count"], original["occlusion_confirmed_covered_sample_count"]) == (1609, 1101, 163)
        assert item["occlusion_measurement_evidence"]["polygon_domain_supported_rects"] == 1
        assert item["occlusion_measurement_evidence"]["polygon_domain_excluded_samples"] > 0
        assert item["occlusion_sample_count"] < original["occlusion_sample_count"]
        assert item["occlusion_confirmed_covered_sample_count"] == 163
        assert item["occlusion_occluder_qids"] == original["occlusion_occluder_qids"] == ["risk.metric.message"]
        # Sensor support does not grant layout admission or hide residual
        # parent clipping/real message coverage; report the fresh evidence.
        print("retained-polygon", {
            key: item[key] for key in ("occlusion_sample_count", "occlusion_known_sample_count", "occlusion_confirmed_covered_sample_count", "occlusion_unknown_fraction", "occluded_fraction", "continuous_occluded_fraction")
        }, "excluded", item["occlusion_measurement_evidence"]["polygon_domain_excluded_samples"])

        candidate_proof = os.getenv("AUTHORED_POLYGON_CANDIDATE_PROOF_DIRECTORY")
        if candidate_proof:
            from services.authored_decoration_containment import (
                plan_decoration_containment, validate_decoration_containment,
            )

            proof_dir = Path(candidate_proof)
            saved_plan = json.loads((proof_dir / "decoration-containment-3-plan.json").read_bytes())
            plan = plan_decoration_containment(evidence["html"], fresh_metrics)
            assert plan is not None
            assert list(plan.targets) == saved_plan["targets"]
            assert plan.scales == saved_plan["scales"]
            assert {key: list(value) for key, value in plan.translations.items()} == saved_plan["translations"]
            assert {key: list(value) for key, value in plan.clips.items()} == saved_plan["clips"]
            assert json.loads(json.dumps(plan.geometry_styles)) == saved_plan["geometry_styles"]
            candidate_png, candidate_metrics = asyncio.run(slide_capture.render_html_to_png_with_metrics(plan.candidate))
            assert candidate_png == (proof_dir / "decoration-containment-3-candidate.png").read_bytes()
            prior_candidate = json.loads((proof_dir / "decoration-containment-3-metrics.json").read_bytes())
            candidate_owners = {value["qid"]: value for value in candidate_metrics["elements"]}
            prior_candidate_owners = {value["qid"]: value for value in prior_candidate["elements"]}
            for qid, value in candidate_owners.items():
                assert value["bbox"] == prior_candidate_owners[qid]["bbox"]
                assert value["computed_style"] == prior_candidate_owners[qid]["computed_style"]
                if qid not in plan.targets:
                    for key in ("bbox", "quad", "computed_style", "owned_text", "rendered_text", "text_fragments", "semantic_role", "parent_qid"):
                        assert value[key] == fresh_owners[qid][key]
            target = candidate_owners["risk.metric.diagonal"]
            print("retained-polygon-candidate", {
                key: target[key] for key in ("bbox", "quad", "occlusion_sample_count", "occlusion_known_sample_count", "occlusion_confirmed_covered_sample_count", "occlusion_unknown_fraction")
            }, "retry", {
                key: target["occlusion_measurement_evidence"][key]
                for key in ("polygon_edge_retry_count", "polygon_edge_recovered_samples")
            })
            assert target["occlusion_sample_count"] == target["occlusion_known_sample_count"] == 913
            assert target["occlusion_measurement_evidence"]["polygon_edge_recovered_samples"] > 0
            assert target["occluded_fraction"] == target["occlusion_unknown_fraction"] == 0
            assert target["occlusion_occluder_qids"] == []
            validated = validate_decoration_containment(plan, evidence["html"], fresh_metrics, plan.candidate, candidate_metrics)
            assert validated
            output_dir = os.getenv("AUTHORED_POLYGON_PROBE_OUTPUT_DIR")
            if output_dir:
                directory = Path(output_dir)
                directory.mkdir(parents=True, exist_ok=False)
                artifacts = {
                    "original.png": fresh_png,
                    "candidate.png": candidate_png,
                    "original-metrics.json": json.dumps(fresh_metrics, ensure_ascii=False, indent=2).encode(),
                    "candidate-metrics.json": json.dumps(candidate_metrics, ensure_ascii=False, indent=2).encode(),
                    "validation.json": json.dumps({
                        "originalPngByteExact": True, "candidatePngByteExact": True,
                        "frozenPlanExact": True, "nonTargetGeometryAndTextExact": True,
                        "validatorAccepted": validated, "sourceDigest": fresh_metrics["source_digest"],
                        "candidateSourceDigest": candidate_metrics["source_digest"],
                        "domMetricsVersion": candidate_metrics["dom_metrics_version"],
                        "target": {key: target[key] for key in (
                            "bbox", "quad", "occlusion_sample_count", "occlusion_known_sample_count",
                            "occlusion_confirmed_covered_sample_count", "occlusion_unknown_fraction",
                            "occlusion_occluder_qids", "occlusion_measurement_evidence",
                        )},
                    }, ensure_ascii=False, indent=2).encode(),
                }
                for name, payload in artifacts.items():
                    with (directory / name).open("xb") as stream:
                        stream.write(payload)
                print("polygon-normal-evidence", directory)
