"""Project a published ``DeckMasterContract`` (company standard) into the
same rule shape :mod:`utils.svg_template_rules` already produces for the
built-in source-restyle catalogue: tokens, fonts, layout geometry and
decorations, no markup.

A master contract's tokens, font manifest and layout slot geometry are pure
data (colours, font family names, normalized rectangles) -- nothing about
them is HTML- or SVG-specific, so this module lives in ``generation_core``
rather than a driver. What genuinely does not project (custom layouts,
semantic elements, AI edit zones, furniture logo/page-number placement) is
recorded in the result's ``validation`` block instead of silently invented,
following the same convention ``template_snapshot`` uses for its own
unsupported roles.

authored is frozen: this module only reads ``DeckMasterContract`` (a plain
pydantic model authored already publishes); it adds no new authored code path
and never mutates a master.
"""
from __future__ import annotations

from hashlib import sha256
import json

from models.authored_vnext.master.contract import DeckMasterContract, MasterLayout

RULE_VERSION = "svg-master-projection/v1"


class MasterProjectionError(ValueError):
    """A published master's rules cannot project into an svg template snapshot."""


def _digest(value: object) -> str:
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _scaled_box(rect, scale_x: float, scale_y: float) -> dict:
    return {
        "x": rect.x * scale_x, "y": rect.y * scale_y,
        "width": rect.width * scale_x, "height": rect.height * scale_y,
    }


def _layout_boxes(layout: MasterLayout, scale_x: float, scale_y: float) -> tuple[dict, dict]:
    title_slot = next((slot for slot in layout.slots if slot.role == "title"), None)
    body_slot = next((slot for slot in layout.slots if slot.role in ("body", "content")), None)
    if title_slot is None or body_slot is None:
        raise MasterProjectionError("svg_master_layout_geometry_missing")
    return _scaled_box(title_slot.bounds, scale_x, scale_y), _scaled_box(body_slot.bounds, scale_x, scale_y)


def project_master_contract(contract: DeckMasterContract, width: int, height: int) -> dict:
    """Return a template-snapshot-shaped dict for a published master.

    Raises :class:`MasterProjectionError` when the master's own standard
    layout has no title/body geometry to project (a furniture-only master) or
    the requested canvas is out of the supported range -- these are the
    concrete cases this projection genuinely cannot cover.
    """

    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise MasterProjectionError("svg_master_canvas_unsupported")
    scale_x = width / contract.canvas.width_px
    scale_y = height / contract.canvas.height_px
    title_box, body_box = _layout_boxes(contract.layouts.standard, scale_x, scale_y)
    colors = contract.tokens.colors
    tokens = {
        "background": colors.background, "text": colors.text_primary,
        "heading": colors.text_primary, "accent": colors.accent,
        "surface": colors.surface, "border": colors.divider,
    }
    fonts = contract.font_manifest
    font_digest = fonts.digest or _digest({"primary": fonts.primary, "fallbacks": fonts.fallbacks})
    title_style = contract.semantic_styles.title
    body_style = contract.semantic_styles.body
    projected_fonts = {
        "heading": {"family": fonts.primary, "weight": title_style.font_weight,
                    "size": title_style.font_pt * min(scale_x, scale_y), "digest": font_digest},
        "body": {"family": fonts.primary, "weight": body_style.font_weight,
                 "size": body_style.font_pt * min(scale_x, scale_y), "digest": font_digest},
    }
    gap = 24 * min(scale_x, scale_y)
    layout = {
        "id": f"master-{contract.layouts.standard.id}", "role": "content", "columns": 1,
        "title_box": title_box, "body_box": body_box, "gap": gap, "min_gap": 12 * min(scale_x, scale_y),
        "align": "left", "reading_order": "source-semantic-order",
        "slots": [
            {"role": "title", "box": title_box, "required": False, "accepts": ["text"], "hierarchy": 1},
            {"role": "evidence", "box": body_box, "required": True,
             "accepts": ["text", "table", "image", "shape", "chart"], "hierarchy": 2},
        ],
        "allowed_variations": {"model_layout": True, "min_gap": 12 * min(scale_x, scale_y),
                                "max_columns": 1, "content_reflow": True},
    }
    divider = contract.furniture.divider
    decorations = [{"id": "master-divider", "kind": "rect", "x": divider.bounds.x * scale_x,
                     "y": divider.bounds.y * scale_y, "width": divider.bounds.width * scale_x,
                     "height": divider.bounds.height * scale_y,
                     "fill": divider.fill or tokens["accent"]}] if divider.visibility != "hidden" else []
    result = {
        "version": RULE_VERSION, "rule_version": RULE_VERSION, "style_id": contract.master_id,
        "canvas": {"width": width, "height": height}, "tokens": tokens, "fonts": projected_fonts,
        "layouts": [layout], "decorations": decorations,
        "table": {"header_fill": tokens["accent"], "header_text": tokens["surface"],
                  "body_fill": tokens["surface"], "border": tokens["border"], "header_weight": 700,
                  "align": "left", "numeric_align": "right", "cell_padding": 12 * min(scale_x, scale_y)},
        "shape": {"fill": tokens["surface"], "border": tokens["accent"], "radius": 6 * min(scale_x, scale_y)},
        "provenance": {"master_id": contract.master_id, "semantic_version": contract.semantic_version,
                       "master_digest": contract.digest, "font_digest": font_digest, "rules": RULE_VERSION},
        "validation": {
            "automatic": ["colors", "layout_geometry", "title_hierarchy"],
            "visual_review": "required",
            "unsupported_roles": ["cover", "section", "custom_layouts"],
            "limitations": [
                "Only the standard layout's title/body geometry projects for svg.",
                "Custom layouts, semantic elements, AI edit zones and furniture "
                "logo/page-number placement are not projected.",
            ],
        },
        "allowed_variations": {"model_layout": True, "min_gap": 12 * min(scale_x, scale_y), "max_columns": 1},
    }
    result["digest"] = _digest(result)
    return result


__all__ = ["MasterProjectionError", "project_master_contract"]
