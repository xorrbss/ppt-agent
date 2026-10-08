"""Deterministic SVG view of the existing Authored style catalogue.

Token/brief projection now goes through :mod:`generation_core.style`: this
module keeps the admission-shaped validation (canvas bounds, exact-
reconstruction rejection, preset ``svg_overrides`` shape, reference-image
bytes) and builds the :class:`StyleContext` that
``generation_core.drivers.svg.project_style`` turns into the flat dict below.
"""
from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path
from dataclasses import replace

from services.generation_core.drivers.svg.project_style import (
    SVG_STYLE_ADAPTER_VERSION,
    project_style,
)
from services.generation_core.style.build import build_style_context
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX
from utils.hashing import sha256_prefixed

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


@lru_cache(maxsize=128)
def _reference_image(path: str, modified_ns: int, size: int) -> dict:
    """Reuse immutable image material while detecting edited catalogue assets."""
    data = Path(path).read_bytes()
    if len(data) > 1536 * 1024:
        raise ValueError("svg_style_reference_size_limit")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    else:
        raise ValueError("svg_style_reference_format_unsupported")
    encoded = base64.b64encode(data).decode("ascii")
    return {"digest": sha256_prefixed(data), "data_url": f"data:{mime};base64,{encoded}"}


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
    overrides = dict(getattr(style, "svg_overrides", None) or {})
    allowed_overrides = {"layout_hints", "common_guidance", "resolved_sections"}
    if set(overrides) - allowed_overrides:
        raise ValueError("svg_style_override_unsupported")
    style_id = getattr(style, "id", "blueprint")
    references = {}
    role_images = dict(getattr(style, "reference_images", None) or {})
    if not role_images and getattr(style, "slide_reference_images", None):
        role_images["content"] = tuple(style.slide_reference_images[:2])
    for role, paths in role_images.items():
        refs = []
        for path in paths:
            stat = path.stat()
            refs.append(dict(_reference_image(str(path.resolve()), stat.st_mtime_ns, stat.st_size)))
        references[role] = refs
    context = build_style_context(
        request if request is not None else _NullRequest(),
        style=style,
        mapped_guidance=_MAPPINGS.get(style_id, ""),
        reference_images=references,
        master_rules=master_rules or design_pack_rules,
        master_pin=master_pin,
        design_pack_rules=design_pack_rules,
    )
    if master_rules is None and design_pack_rules is not None:
        context = replace(context, master_rules=None)
    diagnostics = [
        {"section": line.section, "reason": "format_specific_instruction", "text": line.text}
        for line in context.brief_lines
        if "svg" not in line.applies_to
    ]
    # Format-specific instructions remain diagnostics. Every validated style
    # can provide identity and examples without executable HTML-to-SVG mapping.
    from services.svg_authoring_profile import request_quality_recovery_policy

    result = project_style(
        context, width=width, height=height,
        overrides=overrides, diagnostics=diagnostics, references=references,
        quality_recovery_policy=request_quality_recovery_policy(request),
    )
    if result.get("quality_recovery_policy") is not None:
        from utils.authored_style_svg_recovery import validated_style_recovery_snapshot

        result["validated_recovery_candidates"] = validated_style_recovery_snapshot(style, result)
    from utils.authored_styles import AUTHORED_STYLE_ASSETS_DIRECTORY
    if getattr(style, "assets_root", None) == AUTHORED_STYLE_ASSETS_DIRECTORY.resolve():
        result["provenance"]["registry_authority"] = "packaged-authored-catalogue"
    result["digest"] = sha256_prefixed(json.dumps(result, ensure_ascii=False, sort_keys=True).encode())
    if source_template:
        from utils.svg_template_rules import reference_template_snapshot
        result["source_template"] = reference_template_snapshot(result, width, height)
    return result


class _NullRequest:
    """No request-level brand overrides (the three existing callers that pass
    no request: the built-in catalogue tests and any future preset-only use)."""

    primary_color = None
    fonts = None
    wordmark = None
