"""Deterministic SVG view of the existing Authored style catalogue.

Token/brief projection now goes through :mod:`generation_core.style`: this
module keeps the admission-shaped validation (canvas bounds, exact-
reconstruction rejection, preset ``svg_overrides`` shape, reference-image
bytes) and builds the :class:`StyleContext` that
``generation_core.drivers.svg.project_style`` turns into the flat dict below.
"""
from __future__ import annotations

import base64
import hashlib
import json

from services.generation_core.drivers.svg.project_style import (
    SVG_STYLE_ADAPTER_VERSION,
    project_style,
)
from services.generation_core.style.build import build_style_context
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

__all__ = ["SVG_STYLE_ADAPTER_VERSION", "adapt_svg_style"]

_MAPPINGS = {
    "default": (
        "Cool white canvas; dark ink headings; blue editorial rules; shared left "
        "edges and a dominant evidence field with restrained supporting annotations."
    ),
    "osb-smart-banking": (
        "Deep navy titles; cobalt blue table headers; pale blue surfaces; "
        "restrained red emphasis. Connectors only for source-supported relations."
    ),
    "hyundai-culture-center": (
        "Emerald top band with a right tag, strong numbered heading hierarchy, "
        "teal tables and deep green cover. Section numbers must come from content; "
        "page number is system-owned."
    ),
}


def adapt_svg_style(
    style,
    *,
    width: int = DEFAULT_CANVAS_WIDTH_PX,
    height: int = DEFAULT_CANVAS_HEIGHT_PX,
    source_template: bool = False,
    request: object | None = None,
    master_rules: dict | None = None,
    master_pin: dict | None = None,
    design_pack_rules: dict | None = None,
) -> dict:
    if width <= 0 or height <= 0 or max(width, height) > 4096:
        raise ValueError("svg_style_canvas_unsupported")
    if getattr(style, "reference_mode", "style_reference") == "exact_reconstruction":
        raise ValueError("svg_exact_reconstruction_unsupported")
    overrides = dict(getattr(style, "svg_overrides", None) or {})
    allowed_overrides = {"layout_hints", "common_guidance", "resolved_sections"}
    if set(overrides) - allowed_overrides:
        raise ValueError("svg_style_override_unsupported")
    style_id = getattr(style, "id", "blueprint")
    references = {}
    for role, paths in (getattr(style, "reference_images", None) or {}).items():
        refs = []
        for path in paths:
            data = path.read_bytes()
            if len(data) > 1536 * 1024:
                raise ValueError("svg_style_reference_size_limit")
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif data.startswith(b"\xff\xd8"):
                mime = "image/jpeg"
            else:
                mime = None
            if not mime:
                raise ValueError("svg_style_reference_format_unsupported")
            encoded = base64.b64encode(data).decode("ascii")
            refs.append({
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "data_url": f"data:{mime};base64,{encoded}",
            })
        references[role] = refs
    context = build_style_context(
        request if request is not None else _NullRequest(),
        style=style,
        mapped_guidance=_MAPPINGS.get(style_id, ""),
        reference_images=references,
        master_rules=master_rules,
        master_pin=master_pin,
        design_pack_rules=design_pack_rules,
    )
    diagnostics = [
        {"section": line.section, "reason": "format_specific_instruction", "text": line.text}
        for line in context.brief_lines
        if "svg" not in line.applies_to
    ]
    # The two reviewed reference styles have explicit brand mappings. Other
    # ambiguous briefs require an explicit same-YAML mapping before admission.
    unresolved = [item for item in diagnostics if item["section"] not in overrides.get("resolved_sections", [])]
    if unresolved and style_id not in _MAPPINGS:
        raise ValueError("svg_style_mapping_required:" + style_id)
    result = project_style(
        context, width=width, height=height,
        overrides=overrides, diagnostics=diagnostics, references=references,
    )
    from utils.authored_styles import AUTHORED_STYLE_ASSETS_DIRECTORY
    if getattr(style, "assets_root", None) == AUTHORED_STYLE_ASSETS_DIRECTORY.resolve():
        result["provenance"]["registry_authority"] = "packaged-authored-catalogue"
    result["digest"] = "sha256:" + hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if source_template:
        from utils.svg_template_rules import template_snapshot
        result["source_template"] = template_snapshot(result, width, height)
    return result


class _NullRequest:
    """No request-level brand overrides (the three existing callers that pass
    no request: the built-in catalogue tests and any future preset-only use)."""

    primary_color = None
    fonts = None
    wordmark = None
