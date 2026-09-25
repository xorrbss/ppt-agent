"""Deterministic SVG view of the existing Authored style catalogue."""
from __future__ import annotations

import base64
import hashlib
import json
import re

from services.svg_contract import SVG_CONTRACT_VERSION

SVG_STYLE_ADAPTER_VERSION = "svg-style/v1"
_COMMON_SECTIONS = {"MOOD", "PALETTE", "TYPOGRAPHY", "LAYOUT SYSTEM", "SIGNATURE ELEMENTS", "DATA VISUALIZATION", "IMAGE DIRECTION", "SLIDE ARCHETYPES", "AVOID"}
_FORMAT_TERMS = re.compile(r"\b(?:HTML|CSS|DOM|flexbox|flex|grid-template|position:absolute|exemplar)\b|<[/a-zA-Z]", re.I)
_MAPPINGS = {
    "default": "Cool white canvas; dark ink headings; blue editorial rules; shared left edges and a dominant evidence field with restrained supporting annotations.",
    "osb-smart-banking": "Deep navy titles; cobalt blue table headers; pale blue surfaces; restrained red emphasis. Connectors only for source-supported relations.",
    "hyundai-culture-center": "Emerald top band with a right tag, strong numbered heading hierarchy, teal tables and deep green cover. Section numbers must come from content; page number is system-owned.",
}


def adapt_svg_style(style, *, width: int = 1280, height: int = 720, source_template: bool = False) -> dict:
    if width <= 0 or height <= 0 or max(width, height) > 4096:
        raise ValueError("svg_style_canvas_unsupported")
    if getattr(style, "reference_mode", "style_reference") == "exact_reconstruction":
        raise ValueError("svg_exact_reconstruction_unsupported")
    tokens = {
        "primary": getattr(style, "primary_color", None) or getattr(style, "preview_accent", "#2563EB"),
        "background": getattr(style, "background_color", None) or getattr(style, "preview_bg", "#FFFFFF"),
        "palette": getattr(style, "preview_palette", None) or [],
        "heading_font": getattr(style, "heading_font", None) or "Noto Sans KR",
        "body_font": getattr(style, "body_font", None) or "Noto Sans KR",
        "mono_font": getattr(style, "mono_font", None) or "monospace",
        "chrome": dict(getattr(style, "composer_profile", None) or {}),
    }
    brief = getattr(style, "brief", "")
    section, sections, diagnostics = "GENERAL", {}, []
    for line in brief.splitlines():
        line = line.strip()
        if line in _COMMON_SECTIONS:
            section = line
            continue
        if not line:
            continue
        if _FORMAT_TERMS.search(line):
            diagnostics.append({"section": section, "reason": "format_specific_instruction", "text": line})
        else:
            sections.setdefault(section, []).append(line)
    overrides = dict(getattr(style, "svg_overrides", None) or {})
    allowed_overrides = {"layout_hints", "common_guidance", "resolved_sections"}
    if set(overrides) - allowed_overrides:
        raise ValueError("svg_style_override_unsupported")
    style_id = getattr(style, "id", "blueprint")
    unresolved = [item for item in diagnostics if item["section"] not in overrides.get("resolved_sections", [])]
    # The two reviewed reference styles have explicit brand mappings. Other
    # ambiguous briefs require an explicit same-YAML mapping before admission.
    if unresolved and style_id not in _MAPPINGS:
        raise ValueError("svg_style_mapping_required:" + style_id)
    references = {}
    for role, paths in (getattr(style, "reference_images", None) or {}).items():
        refs = []
        for path in paths:
            data = path.read_bytes()
            if len(data) > 1536 * 1024:
                raise ValueError("svg_style_reference_size_limit")
            mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if data.startswith(b"\xff\xd8") else None
            if not mime:
                raise ValueError("svg_style_reference_format_unsupported")
            refs.append({"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "data_url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")})
        references[role] = refs
    result = {"adapter_version": SVG_STYLE_ADAPTER_VERSION, "contract_version": SVG_CONTRACT_VERSION,
              "style_id": style_id, "canvas": {"width": width, "height": height}, "tokens": tokens,
              "common_guidance": sections, "mapped_guidance": _MAPPINGS.get(style_id, ""),
              "overrides": overrides, "diagnostics": diagnostics, "unresolved": [], "references": references,
              "provenance": {"tokens": "authored_style_fields", "brief_digest": "sha256:" + hashlib.sha256(brief.encode()).hexdigest(), "references": "authored_style_role_images"}}
    from utils.authored_styles import AUTHORED_STYLE_ASSETS_DIRECTORY
    if getattr(style, "assets_root", None) == AUTHORED_STYLE_ASSETS_DIRECTORY.resolve():
        result["provenance"]["registry_authority"] = "packaged-authored-catalogue"
    result["digest"] = "sha256:" + hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if source_template:
        from utils.svg_template_rules import template_snapshot
        result["source_template"] = template_snapshot(result, width, height)
    return result
