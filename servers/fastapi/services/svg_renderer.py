"""Render a validated SVG through the existing isolated Chromium boundary."""
from __future__ import annotations
# ruff: noqa: ASYNC109

from dataclasses import dataclass
import base64
from dataclasses import replace
from functools import lru_cache
import re
import xml.etree.ElementTree as ET

from services.svg_contract import PAGE_NUMBER_ID, SVG_CONTRACT_VERSION, source_digest, validate_svg
from services.svg_trusted_fonts import trusted_font_weights_used as _trusted_font_weights_used

SVG_RENDERER_VERSION = "svg-chromium/v1"

# Keep this persisted profile identity for older SVG previews.
TRUSTED_FONT_PROFILE = "composer-bundled-pretendard/v1"


@lru_cache(maxsize=1)
def _trusted_font_materials() -> tuple[tuple[int, str, bytes], ...]:
    """Resolve only the fixed, identity-checked bundled Pretendard bytes."""
    from services.svg_trusted_fonts import read_trusted_pretendard_faces

    return read_trusted_pretendard_faces()


def _trusted_font_marker() -> str:
    materials = _trusted_font_materials()
    return TRUSTED_FONT_PROFILE + "|" + "|".join(
        f"{weight}={digest}" for weight, digest, _payload in materials
    )


def materialize_trusted_svg_fonts(source: str, admitted):
    """Inject fixed bundled faces after normal HTML admission inside the worker."""
    from services.svg_default_fonts import FONT_PROFILE as DEFAULT_FONT_PROFILE, materialize_default_fonts
    if f'<html title="{DEFAULT_FONT_PROFILE}|' in source:
        return materialize_default_fonts(source, admitted)
    from utils.svg_template_fonts import FONT_PROFILE
    if f'<html title="{FONT_PROFILE}|' in source:
        return _materialize_template_font(source, admitted)
    marker = _trusted_font_marker()
    declaration = f'<html title="{marker}">'
    if declaration not in source:
        return admitted
    if source.count(declaration) != 1 or "data:font/" in source.casefold() or "@font-face" in source.casefold():
        raise ValueError("svg_trusted_font_profile_invalid")
    match = re.search(r"(<(?:[A-Za-z_][\w.-]*:)?svg\b.*?</(?:[A-Za-z_][\w.-]*:)?svg>)", source,
                      re.IGNORECASE | re.DOTALL)
    if match is None:
        raise ValueError("svg_trusted_font_wrapper_invalid")
    root = ET.fromstring(match.group(1))
    if not _trusted_font_weights_used(root)[0]:
        raise ValueError("svg_trusted_font_profile_unused")
    csp_font_none = "font-src &#x27;none&#x27;"
    if admitted.html.count(csp_font_none) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_trusted_font_csp_invalid")
    faces = []
    digests = []
    for weight, digest, payload in _trusted_font_materials():
        encoded = base64.b64encode(payload).decode("ascii")
        faces.append(
            f"@font-face{{font-family:'Pretendard';font-style:normal;font-weight:{weight};"
            f"src:url(data:font/ttf;base64,{encoded}) format('truetype');}}"
        )
        digests.append(digest)
    injected = admitted.html.replace(csp_font_none, "font-src data:", 1).replace(
        "</head>",
        f'<style data-presenton-trusted-font-profile="{TRUSTED_FONT_PROFILE}">'
        + "".join(faces) + "</style></head>",
        1,
    )
    return replace(admitted, html=injected,
                   asset_digests=tuple(dict.fromkeys((*admitted.asset_digests, *digests))),
                   node_count=admitted.node_count + 1)


def _template_font_used(root: ET.Element) -> bool:
    from utils.svg_template_fonts import FONT_FAMILY, font_material, require_font_coverage

    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "text"]
    if not any(node.get("font-family", "").strip("'\"") == FONT_FAMILY for node in nodes):
        return False
    for node in nodes:
        # Source templates pin every text node. Mixed/inherited font stacks
        # cannot use the template profile as evidence for a silent fallback.
        family = node.get("font-family", "").strip("'\"")
        font_material(family, node.get("font-weight", "400"))
        require_font_coverage("".join(node.itertext()), family)
        if any(child.get("font-family", family).strip("'\"") != family for child in node.iter()):
            raise ValueError("svg_template_font_mixed_families")
    return True


def _materialize_template_font(source: str, admitted):
    from utils.svg_template_fonts import FONT_FAMILY, FONT_PROFILE, font_material, trusted_font_marker

    declaration = f'<html title="{trusted_font_marker()}">'
    if source.count(declaration) != 1 or "data:font/" in source.casefold() or "@font-face" in source.casefold():
        raise ValueError("svg_trusted_font_profile_invalid")
    match = re.search(r"(<(?:[A-Za-z_][\w.-]*:)?svg\b.*?</(?:[A-Za-z_][\w.-]*:)?svg>)", source, re.I | re.S)
    if match is None or not _template_font_used(ET.fromstring(match.group(1))):
        raise ValueError("svg_trusted_font_profile_unused")
    csp_font_none = "font-src &#x27;none&#x27;"
    if admitted.html.count(csp_font_none) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_trusted_font_csp_invalid")
    digest, payload = font_material(FONT_FAMILY)
    encoded = base64.b64encode(payload).decode("ascii")
    # One variable font payload covers all permitted weights, once per page.
    face = (f"@font-face{{font-family:'{FONT_FAMILY}';font-style:normal;font-weight:100 900;"
            f"src:url(data:font/ttf;base64,{encoded}) format('truetype');}}")
    injected = admitted.html.replace(csp_font_none, "font-src data:", 1).replace(
        "</head>", f'<style data-presenton-trusted-font-profile="{FONT_PROFILE}">{face}</style></head>', 1)
    return replace(admitted, html=injected,
        asset_digests=tuple(dict.fromkeys((*admitted.asset_digests, digest))), node_count=admitted.node_count + 1)


def _materialize_authorized_raster_font(canonical: ET.Element, admitted):
    """Embed packaged Noto for authorized raster derivatives with real text."""
    if not _template_font_used(canonical):
        return admitted
    from utils.svg_template_fonts import FONT_FAMILY, FONT_PROFILE, font_material
    csp_font_none = "font-src &#x27;none&#x27;"
    if admitted.html.count(csp_font_none) != 1 or "</head>" not in admitted.html:
        raise ValueError("svg_trusted_font_csp_invalid")
    digest, payload = font_material(FONT_FAMILY)
    encoded = base64.b64encode(payload).decode("ascii")
    face = (f"@font-face{{font-family:'{FONT_FAMILY}';font-style:normal;font-weight:100 900;"
            f"src:url(data:font/ttf;base64,{encoded}) format('truetype');}}")
    injected = admitted.html.replace(csp_font_none, "font-src data:", 1).replace(
        "</head>", f'<style data-presenton-trusted-font-profile="{FONT_PROFILE}">{face}</style></head>', 1)
    return replace(admitted, html=injected,
        asset_digests=tuple(dict.fromkeys((*admitted.asset_digests, digest))), node_count=admitted.node_count + 1)


def admit_preserved_raster_preview(svg: str, materialized_svg: str, *, width: int, height: int):
    """Recheck the bounded raster derivative in both parent and isolated worker.

    One embedded crop for a full_page_preserve page, N for a redesign page
    (screenshot/logo/table captures); either way every slot is required, so
    the canonical (slot-referenced) SVG keeps the normal 2MB contract limit
    while the expanded, base64-embedded materialized_svg is measured through
    the authorized-materialized admission's much larger wire/decode budget.
    """
    from utils.renderer_isolation import admit_authorized_svg_materialized

    canonical = ET.fromstring(svg)
    slots = [node.get("href") for node in canonical.iter()
             if node.tag.rsplit("}", 1)[-1] == "image"]
    if not slots or any(not slot for slot in slots):
        raise ValueError("raster_preview_image_slots_invalid")
    admitted = admit_authorized_svg_materialized(
        svg, materialized_svg,
        source_manifest={slot: {"required": True, "allowOmit": False} for slot in slots},
        width_px=width, height_px=height, allow_image_only=True,
    )
    admitted = _materialize_authorized_raster_font(canonical, admitted)
    root = ET.fromstring(materialized_svg)
    for index, node in enumerate(root.iter()):
        node.set("data-qid", f"svg-{index}")
    measured = ET.tostring(root, encoding="unicode")
    wrapper = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
               f'html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden}}'
               f'body>svg{{display:block}}</style></head><body>{measured}</body></html>')
    return replace(admitted, html=admitted.html.replace(materialized_svg, measured),
                   source_digest=source_digest(wrapper)), wrapper


def svg_preview_html(svg: str, *, width: int = 1280, height: int = 720,
                     materialized_svg: str | None = None,
                     source_font_profile: str | None = None,
                     allow_image_only: bool = False) -> str:
    if allow_image_only and materialized_svg is not None:
        return admit_preserved_raster_preview(svg, materialized_svg, width=width, height=height)[1]
    rendered_source = materialized_svg if materialized_svg is not None else svg
    validate_svg(rendered_source, width=width, height=height, allow_image_only=allow_image_only)
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
    weights, _advisories = _trusted_font_weights_used(root)
    template_font = False
    if source_font_profile is not None:
        from utils.svg_template_fonts import trusted_font_marker
        if source_font_profile == trusted_font_marker():
            template_font = _template_font_used(root)
            if not template_font:
                raise ValueError("svg_template_font_profile_unused")
        elif source_font_profile != _trusted_font_marker():
            raise ValueError("svg_trusted_font_profile_invalid")
    # Measurement IDs exist only in this deterministic derivative. The SVG
    # source remains the same bytes for persistence and editable export.
    for i, node in enumerate(root.iter()):
        node.set("data-qid", f"svg-{i}")
    rendered = ET.tostring(root, encoding="unicode")
    marker = f' title="{_trusted_font_marker()}"' if weights else ""
    if template_font:
        from utils.svg_template_fonts import trusted_font_marker
        marker = f' title="{trusted_font_marker()}"'
    elif source_font_profile is None:
        from services.svg_default_fonts import font_marker, uses_default_font
        if uses_default_font(root):
            marker = f' title="{font_marker()}"'
    return (f'<!DOCTYPE html><html{marker}><head><meta charset="utf-8"><style>'
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
                                  materialized_svg: str | None = None,
                                  physical_canvas: object | None = None,
                                  source_font_profile: str | None = None,
                                  allow_image_only: bool = False) -> SvgRenderResult:
    from utils.authored_font_policy import requested_font_floor_px
    from utils.slide_capture import render_html_to_png_with_metrics
    wrapper = svg_preview_html(svg, width=width, height=height,
                               materialized_svg=materialized_svg, source_font_profile=source_font_profile,
                               allow_image_only=allow_image_only)
    if allow_image_only and materialized_svg is not None:
        from utils.slide_capture_worker_client import _render_html_in_isolated_worker
        from utils.slide_capture_result import RenderCapture
        from utils.renderer_worker_policy import ensure_renderer_enabled
        from utils.slide_capture import _get_render_sem
        ensure_renderer_enabled()
        async with _get_render_sem():
            png, browser_metrics = await _render_html_in_isolated_worker(
                svg, timeout, width_px=width, height_px=height,
                preserved_raster_svg=materialized_svg,
            )
        capture = RenderCapture.from_payload(
            html_digest=source_digest(wrapper), png=png, browser_metrics=browser_metrics,
            runtime_counters={},
        )
    else:
        capture = await render_html_to_png_with_metrics(wrapper, timeout=timeout,
                width_px=width, height_px=height, deck_scope=deck_scope, fit=False)
    # Probe cache keys contain NUL separators, which PostgreSQL JSON operators
    # cannot decode. They are diagnostic keys, never source text.
    import json
    metrics = json.loads(json.dumps(capture.browser_metrics or {}).replace("\\u0000", "\\u241f"))
    elements = metrics.get("elements") or []
    text = [item for item in elements if item.get("has_owned_text")]
    failures, overlaps = [], []
    from services.document_canvas_generation import generation_font_floor
    floor = minimum_font_px if minimum_font_px is not None else generation_font_floor(None, physical_canvas)
    requested_floor = requested_font_floor_px(
        instructions, point_per_unit=float(getattr(physical_canvas, "point_per_unit", 0.75))
    )
    if requested_floor is not None:
        floor = requested_floor
    root = ET.fromstring(svg)
    footer_qid = next((f"svg-{i}" for i, node in enumerate(root.iter()) if node.get("id") == PAGE_NUMBER_ID), None)
    images = [item for item in elements if item.get("tag") == "image"]
    # Approved raster pages have already passed digest, decoding and source
    # geometry checks at materialization. Measure their visible image surface.
    image_measurement_complete = bool(allow_image_only and materialized_svg and images) and all(
        item.get("css_visible", False) and item.get("effective_opacity", 0) >= 0.9
        and item.get("bbox", {}).get("width", 0) > 0
        and item.get("bbox", {}).get("height", 0) > 0
        and item.get("viewport_intersection_ratio", 0) >= 0.999
        for item in images
    )
    measurement_complete = bool(metrics and not metrics.get("probe_error")
                                and metrics.get("fonts_ready") and (text or image_measurement_complete))
    if not measurement_complete:
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
    font_profile = None
    trusted_weights, weight_advisories = _trusted_font_weights_used(root)
    if trusted_weights:
        font_profile = {"profile": TRUSTED_FONT_PROFILE,
            "font_digests": [digest for _weight, digest, _payload in _trusted_font_materials()],
            "worker_materialized": bool(text) and all(item.get("font_loaded") is True for item in text),
            **({"weight_normalizations": weight_advisories} if weight_advisories else {})}
    from utils.svg_template_fonts import FONT_PROFILE
    if f'<html title="{FONT_PROFILE}|' in wrapper:
        from utils.svg_template_fonts import FONT_DIGEST, FONT_FAMILY, FONT_PROFILE
        font_profile = {"profile": FONT_PROFILE, "family": FONT_FAMILY,
            "font_digests": [FONT_DIGEST],
            "worker_materialized": bool(text) and all(item.get("font_loaded") is True for item in text)}
    from services.svg_default_fonts import FONT_PROFILE as DEFAULT_FONT_PROFILE
    if f'<html title="{DEFAULT_FONT_PROFILE}|' in wrapper:
        from services.svg_trusted_fonts import DEFAULT_FONT_FAMILY
        font_profile = {"profile": DEFAULT_FONT_PROFILE, "family": DEFAULT_FONT_FAMILY,
            "worker_materialized": bool(text) and all(item.get("font_loaded") is True for item in text)}
    quality = {"contract_version": SVG_CONTRACT_VERSION, "renderer_version": SVG_RENDERER_VERSION,
               "source_digest": source_digest(svg), "png_digest": capture.png_digest,
               "wrapper_digest": capture.html_digest, "metrics_digest": capture.metrics_digest,
               "measurement_complete": measurement_complete,
               "geometry_passed": not failures, "passed": not failures,
               "verified": False, "semantic_review": "required", "failures": failures,
               "body_font_floor_px": floor, "browser_metrics": metrics,
               "trusted_font_materialization": font_profile}
    return SvgRenderResult(capture.png, quality, wrapper, source_digest(svg))
