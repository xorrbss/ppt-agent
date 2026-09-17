"""Render a validated SVG through the existing isolated Chromium boundary."""
from __future__ import annotations

from dataclasses import dataclass
import xml.etree.ElementTree as ET

from services.svg_contract import PAGE_NUMBER_ID, SVG_CONTRACT_VERSION, source_digest, validate_svg

SVG_RENDERER_VERSION = "svg-chromium/v1"


def svg_preview_html(svg: str, *, width: int = 1280, height: int = 720,
                     materialized_svg: str | None = None) -> str:
    rendered_source = materialized_svg if materialized_svg is not None else svg
    validate_svg(rendered_source, width=width, height=height)
    if materialized_svg is not None:
        # Only image hrefs may differ at the authorized resolver boundary.
        # Geometry and text must remain the canonical document being measured.
        canonical_root = ET.fromstring(svg)
        resolved_root = ET.fromstring(materialized_svg)
        for document in (canonical_root, resolved_root):
            for node in document.iter():
                if node.tag.rsplit("}", 1)[-1] == "image":
                    for key in ("href", "{http://www.w3.org/1999/xlink}href"):
                        node.attrib.pop(key, None)
        if ET.tostring(canonical_root) != ET.tostring(resolved_root):
            raise ValueError("svg_materialized_source_mismatch")
    root = ET.fromstring(rendered_source)
    # Measurement IDs exist only in this deterministic derivative. The SVG
    # source remains the same bytes for persistence and editable export.
    for i, node in enumerate(root.iter()):
        node.set("data-qid", f"svg-{i}")
    rendered = ET.tostring(root, encoding="unicode")
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
            f'html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden}}'
            f'body>svg{{display:block}}</style></head><body>{rendered}</body></html>')


@dataclass(frozen=True)
class SvgRenderResult:
    png: bytes
    quality: dict
    wrapper_html: str
    source_digest: str


async def render_svg_with_metrics(svg: str, *, width: int = 1280, height: int = 720,
                                  timeout: int = 60, deck_scope: str | None = None,
                                  minimum_font_px: float | None = None,
                                  instructions: str | None = None,
                                  required_text: list[str] | None = None,
                                  materialized_svg: str | None = None) -> SvgRenderResult:
    from utils.authored_font_policy import MIN_AUTHORED_FONT_SIZE_PX, requested_font_floor_px
    from utils.slide_capture import render_html_to_png_with_metrics
    wrapper = svg_preview_html(svg, width=width, height=height,
                               materialized_svg=materialized_svg)
    capture = await render_html_to_png_with_metrics(wrapper, timeout=timeout,
            width_px=width, height_px=height, deck_scope=deck_scope, fit=False)
    # Probe cache keys contain NUL separators, which PostgreSQL JSON operators
    # cannot decode. They are diagnostic keys, never source text.
    import json
    metrics = json.loads(json.dumps(capture.browser_metrics or {}).replace("\\u0000", "\\u241f"))
    elements = metrics.get("elements") or []
    text = [item for item in elements if item.get("has_owned_text")]
    failures, overlaps = [], []
    floor = minimum_font_px if minimum_font_px is not None else MIN_AUTHORED_FONT_SIZE_PX
    requested_floor = requested_font_floor_px(instructions)
    if requested_floor is not None:
        floor = requested_floor
    root = ET.fromstring(svg)
    footer_qid = next((f"svg-{i}" for i, node in enumerate(root.iter()) if node.get("id") == PAGE_NUMBER_ID), None)
    if not metrics or metrics.get("probe_error") or not metrics.get("fonts_ready") or not text:
        failures.append({"kind": "measurement_incomplete"})
    for item in text:
        qid = item.get("qid")
        rects = item.get("text_fragments") or [item.get("bbox", {})]
        for r in rects:
            x, y = r.get("x", r.get("left", 0)), r.get("y", r.get("top", 0))
            if x < -0.25 or y < -0.25 or x + r.get("width", 0) > width + 0.25 or y + r.get("height", 0) > height + 0.25:
                failures.append({"kind": "text_off_canvas", "qid": qid})
        if item.get("glyph_clipped"):
            failures.append({"kind": "text_clipped", "qid": qid})
        if not item.get("css_visible", True) or item.get("effective_opacity", 1) < 0.9:
            failures.append({"kind": "text_hidden", "qid": qid})
        if qid != footer_qid and item.get("font_size", 0) < floor:
            failures.append({"kind": "small_body_text", "qid": qid, "minimum_px": floor})
        if item.get("font_loaded") is False:
            failures.append({"kind": "font_unresolved", "qid": qid})
    for i, a in enumerate(text):
        for b in text[i + 1:]:
            if a.get("parent_qid") == b.get("qid") or b.get("parent_qid") == a.get("qid"):
                continue
            collision = False
            for ar in a.get("text_fragments", []):
                for br in b.get("text_fragments", []):
                    dx = min(ar["x"] + ar["width"], br["x"] + br["width"]) - max(ar["x"], br["x"])
                    dy = min(ar["y"] + ar["height"], br["y"] + br["height"]) - max(ar["y"], br["y"])
                    if dx > 0.5 and dy > 0.5:
                        collision = True
            if collision:
                overlaps.append([a.get("qid"), b.get("qid")])
    if overlaps:
        failures.append({"kind": "text_overlap", "pairs": overlaps})
    normalized = "".join("".join(item.get("owned_text", "") for item in text).split())
    missing = [value for value in required_text or [] if "".join(value.split()) not in normalized]
    if missing:
        failures.append({"kind": "required_text_missing", "count": len(missing)})
    quality = {"contract_version": SVG_CONTRACT_VERSION, "renderer_version": SVG_RENDERER_VERSION,
               "source_digest": source_digest(svg), "png_digest": capture.png_digest,
               "wrapper_digest": capture.html_digest, "metrics_digest": capture.metrics_digest,
               "measurement_complete": bool(metrics and not metrics.get("probe_error") and metrics.get("fonts_ready") and text),
               "geometry_passed": not failures, "passed": not failures,
               "verified": False, "semantic_review": "required", "failures": failures,
               "body_font_floor_px": floor, "browser_metrics": metrics}
    return SvgRenderResult(capture.png, quality, wrapper, source_digest(svg))
