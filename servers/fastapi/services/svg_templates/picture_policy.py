"""Immutable, opt-in vector picture policy for SVG template redesigns.

Only admitted flags and template design tokens are read here. Template text,
reference images and process environment are never art-direction inputs.
"""
from __future__ import annotations

from dataclasses import replace
import math
import re

TEMPLATE_PICTURES_REVISION = "svg-template-pictures/v1"
_PARTS = ("scene", "spots", "pairs", "pictograph")
_HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


def _enabled_snapshot(snapshot: dict | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    selection = snapshot.get("svg_template")
    return (snapshot.get("template_pictures") is True
            and snapshot.get("template_pictures_revision") == TEMPLATE_PICTURES_REVISION
            and isinstance(selection, dict)
            and selection.get("mode") == "template-redesign")


def template_picture_policy(request, snapshot: dict | None) -> dict | None:
    """Return a new effective policy only for a pinned SVG template redesign.

    Twelve is the existing vector offer's MAX_CALLS ceiling, not a scene
    frequency cap. The offer also intersects it with the runtime profile's
    illustration budget, including physical structured-response retries.
    """
    if (not _enabled_snapshot(snapshot)
            or getattr(request, "strategy", None) not in (None, "svg")
            or getattr(request, "svg_template_pin", None) is None
            or getattr(request, "svg_template_mode", None) != "template-redesign"):
        return None
    return {"revision": TEMPLATE_PICTURES_REVISION, "parts": _PARTS,
            "max_calls": 12, "exclude_cover": True}


def template_picture_guidance(snapshot: dict | None, body_bbox=None) -> str:
    """Optional body-authoring guidance; an empty string preserves old prompts.

    ``body_bbox`` may supply the authoritative page body's x/y/width/height.
    Without page-local coordinates, refer to the existing template_page input.
    This is free composition guidance, not a new placement or slot schema.
    """
    if not _enabled_snapshot(snapshot):
        return ""
    region = "template_page.body_region"
    if body_bbox is not None:
        if isinstance(body_bbox, dict):
            values = [body_bbox.get(key) for key in ("x", "y", "width", "height")]
        else:
            values = list(body_bbox) if isinstance(body_bbox, (tuple, list)) else []
        if (len(values) != 4 or any(isinstance(value, bool) or not isinstance(value, (int, float))
                                    or not math.isfinite(value) for value in values)
                or values[2] <= 0 or values[3] <= 0):
            raise ValueError("svg_template_picture_body_bbox_invalid")
        region += " (x=%g, y=%g, width=%g, height=%g)" % tuple(values)
    return (
        "Template vector pictures: use an offered scene, step picture, comparison pair or pictograph"
        " only when it explains this page's source content. Keep the complete picture and its labels"
        f" inside {region}, clear of fixed frame artwork, header and footer. Preserve aspect ratio;"
        " freely size and arrange pictures with the content so facts stay readable. Omit an optional"
        " picture when it cannot fit; never omit required facts to make room. The frame already owns"
        " cover, section and closing artwork: do not add a second cover picture. Use the frozen"
        " template palette and type treatment; no template wording or source-page layout is factual"
        " authority. Explicit instructions prohibiting pictures take precedence."
    )


def template_picture_style(snapshot: dict, style):
    """Adapt an existing style using only frozen template colors and typography.

    Malformed projection data raises ``ValueError`` like a missing palette, so the
    offer degrades to its existing ``template_picture_style_unavailable`` path."""
    projection = (snapshot.get("svg_template") or {}).get("style_projection") or {}
    tokens = (projection.get("tokens") or {}) if isinstance(projection, dict) else None
    fonts = (projection.get("fonts") or {}) if isinstance(projection, dict) else None
    if (not isinstance(tokens, dict) or not isinstance(fonts, dict)
            or not isinstance(tokens.get("palette", ()), (list, tuple))
            or any(not isinstance(fonts.get(role) or {}, dict) for role in ("heading", "body"))):
        raise ValueError("svg_template_picture_style_invalid")

    def color(key):
        value = tokens.get(key)
        return value.upper() if isinstance(value, str) and _HEX.fullmatch(value) else None

    palette = [value.upper() for value in tokens.get("palette", ())
               if isinstance(value, str) and _HEX.fullmatch(value)]
    background, foreground, accent = color("background"), color("foreground"), color("accent")
    palette = list(dict.fromkeys(value for value in (accent, foreground, *palette, background) if value))
    if not palette or background is None:
        raise ValueError("svg_template_picture_palette_missing")
    accent = accent or foreground or palette[0]
    heading = (fonts.get("heading") or {}).get("family")
    body = (fonts.get("body") or {}).get("family")
    # This server-authored sentence deliberately excludes free-text template
    # references, extracted examples and any style brief from a different deck.
    brief = ("Compose content-relevant vector pictures in the frozen template palette: "
             + ", ".join(palette) + ". Preserve the template's type treatment; "
             "use the request's content as the only source of subjects and relationships.")
    return replace(style, id="svg-template-pictures", brief=brief,
                   preview_bg=background, preview_accent=accent, preview_palette=palette,
                   primary_color=accent, background_color=background,
                   heading_font=heading, body_font=body, illustration_prompt=None,
                   reference_images={}, slide_reference_images=(), slide_reference_indices=())


__all__ = ["TEMPLATE_PICTURES_REVISION", "template_picture_guidance", "template_picture_policy",
           "template_picture_style"]
