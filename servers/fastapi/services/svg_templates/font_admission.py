"""Shared early admission for request-scoped render and native export fonts."""
from __future__ import annotations

import json

from models.svg_template_frame import SvgTemplateFontManifest
from services.svg_templates.frame_fonts import resolve_font_manifest
from services.svg_templates.frame_fonts import MAX_FONT_BYTES


def validate_template_export_fonts(value):
    manifest = SvgTemplateFontManifest.model_validate(value)
    entries = resolve_font_manifest(manifest)
    if not entries:
        raise ValueError("svg_template_font_manifest_invalid")
    if any(asset.weight not in (400, 700) or asset.collection_index != 0 for asset, _ in entries):
        raise ValueError("svg_template_export_font_face_unsupported")
    names = {}
    for asset, _ in entries:
        for name in (asset.family, *asset.family_aliases):
            key = (name.casefold(), asset.weight)
            if key in names and names[key] != asset.digest:
                raise ValueError("svg_template_font_face_conflict")
            names[key] = asset.digest
    if sum(len(payload) for _asset, payload in entries) > MAX_FONT_BYTES:
        raise ValueError("svg_template_font_manifest_bytes_exceeded")
    size = len(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))
    if size > 90 * 1024 * 1024:
        raise ValueError("svg_template_font_manifest_bytes_exceeded")
    return manifest
