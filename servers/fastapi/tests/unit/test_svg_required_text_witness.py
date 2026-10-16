"""Required literal failures reach the existing bounded author repair unchanged."""
import asyncio
import json
from types import SimpleNamespace


def test_required_text_witness_preserves_gate_bounds_and_repair_context(monkeypatch):
    from services.generation_core.drivers.svg.render import RETRY, attempt_render
    from services.svg_renderer import render_svg_with_metrics

    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>20 16 4</text></svg>'
    item = {"qid": "svg-1", "tag": "text", "has_owned_text": True, "owned_text": "20 16 4",
            "bbox": {"x": 64, "y": 200, "width": 100, "height": 32}, "font_size": 32}

    async def capture(*args, **kwargs):
        return SimpleNamespace(png=b"png", png_digest="png", html_digest="html", metrics_digest="metrics",
            browser_metrics={"fonts_ready": True, "elements": [item]})

    monkeypatch.setattr("utils.slide_capture.render_html_to_png_with_metrics", capture)
    monkeypatch.setattr("services.svg_renderer.svg_preview_html", lambda *args, **kwargs: "<html></html>")
    monkeypatch.setattr("services.svg_contract.finalize_svg", lambda source, **kwargs: source)
    monkeypatch.setattr("services.svg_master_furniture.master_furniture_receipt", lambda *args, **kwargs: None)
    ctx = SimpleNamespace(snapshot={}, source_reflow=False, raster_reconstruction=False,
        canonical_assets=False, count=1, canvas=SimpleNamespace(width_px=1280, height_px=720),
        render_semaphore=asyncio.Semaphore(1), physical_kwargs={})
    payload = {"attempts": [{"receipt": {}}]}

    async def checkpoint(*args, **kwargs):
        pass

    result = asyncio.run(attempt_render(ctx=ctx, request=SimpleNamespace(instructions=None, aspect_ratio="16:9"),
        presentation_id="presentation", index=0, content="20건 16건 4건", payload=payload, svg=svg,
        ordinal=0, job=None, checkpoint=checkpoint, async_session_maker=None, write_png=lambda _: {}))
    assert result is RETRY
    failure = payload["attempts"][0]["quality"]["failures"][0]
    assert failure == {"kind": "required_text_missing", "count": 3, "missing_literals": ["20건", "16건", "4건"]}
    assert json.loads(payload["repair_hint"])["failures"][0] == failure

    required = ["x" * 161] + [f"{number}건" for number in range(100, 120)]
    bounded = asyncio.run(render_svg_with_metrics(svg, required_text=required)).quality
    witness = next(failure for failure in bounded["failures"] if failure["kind"] == "required_text_missing")
    assert witness["count"] == len(required)
    assert witness["missing_literals"] == required[1:9]
    assert witness["missing_literals_truncated"] is True
    assert bounded["passed"] is False

    item["owned_text"] = "20건 16건 4건"
    complete = asyncio.run(render_svg_with_metrics(svg, required_text=["20건", "16건", "4건"])).quality
    assert complete["passed"] is True
