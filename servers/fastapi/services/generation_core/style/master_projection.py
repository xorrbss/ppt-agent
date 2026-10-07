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

import json
import base64
from pathlib import Path
import uuid

from models.authored_vnext.master.contract import DeckMasterContract, MasterLayout
from utils.hashing import sha256_prefixed

RULE_VERSION = "svg-master-projection/v1"


class MasterProjectionError(ValueError):
    """A published master's rules cannot project into an svg template snapshot."""


def _digest(value: object) -> str:
    return sha256_prefixed(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


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


def project_master_reference(contract: DeckMasterContract, width: int, height: int) -> dict:
    """Project brand identity for new SVG composition, without master slots.

    The historical geometry projection above remains available for replay.
    Brand assets retain their digest authority for the system asset channel;
    they are never rewritten into model-owned output placement instructions.
    """
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise MasterProjectionError("svg_master_canvas_unsupported")
    colors = contract.tokens.colors
    fonts = contract.font_manifest
    result = {
        "version": "svg-master-design-reference/v1",
        "style_id": contract.master_id,
        "canvas": {"width": width, "height": height},
        "composition_mode": "free-svg",
        "reference_semantics": "design-identity",
        "tokens": {"background": colors.background, "surface": colors.surface,
                   "text": colors.text_primary, "accent": colors.accent,
                   "border": colors.divider, "palette": [colors.accent, colors.text_primary, colors.surface]},
        "fonts": {"heading": {"family": fonts.primary, "weight": contract.semantic_styles.title.font_weight},
                  "body": {"family": fonts.primary, "weight": contract.semantic_styles.body.font_weight}},
        "typography": {
            role: {"font_weight": style.font_weight, "size_ratio": style.font_pt / contract.semantic_styles.body.font_pt,
                   "line_height": style.line_height, "color_token": style.color_token}
            for role, style in contract.semantic_styles
        },
        "reference_examples": [],
        "brand_assets": contract.brand_assets.model_dump(mode="json", by_alias=True),
        "composition_guidance": ["Keep the company's palette, type hierarchy and brand treatment while composing the target canvas freely."],
        "provenance": {"master_id": contract.master_id, "semantic_version": contract.semantic_version,
                       "master_digest": contract.digest, "font_digest": fonts.digest,
                       "reference_semantics": "design-identity"},
    }
    result["digest"] = _digest(result)
    return result


def enrich_master_reference(rules: dict, contract: DeckMasterContract, *, owner_id: object,
                            width: int, height: int) -> dict:
    """Pin owner-verified brand furniture for deterministic SVG stamping."""
    from services.authored_vnext.master.assets import MasterAssetError, OwnerMasterAssetStore
    from services.svg_contract import validate_raster_data_url
    from services.svg_trusted_fonts import bundled_svg_font_family
    from utils.get_env import get_app_data_directory_env

    owner = uuid.UUID(str(owner_id))
    scale = min(width / contract.canvas.width_px, height / contract.canvas.height_px)
    store = None
    elements = []
    for role in ("logo", "header", "footer", "divider", "page_number"):
        source = getattr(contract.furniture, role)
        if source.visibility == "hidden":
            continue
        rect = source.bounds
        box_width, box_height = rect.width * scale, rect.height * scale
        center_x = (rect.x + rect.width / 2) / contract.canvas.width_px
        center_y = (rect.y + rect.height / 2) / contract.canvas.height_px
        x = (rect.x * scale if center_x < 1 / 3 else
             width - (contract.canvas.width_px - rect.x) * scale if center_x > 2 / 3 else
             width * center_x - box_width / 2)
        y = (rect.y * scale if center_y < 1 / 3 else
             height - (contract.canvas.height_px - rect.y) * scale if center_y > 2 / 3 else
             height * center_y - box_height / 2)
        if role == "divider" and rect.width > contract.canvas.width_px / 2:
            x = rect.x * scale
            box_width = width - x - (contract.canvas.width_px - rect.right) * scale
        element = {"id": "svg-system-master-" + role.replace("_", "-"), "kind": source.kind,
                   "x": x, "y": y, "width": box_width, "height": box_height,
                   "visibility": source.visibility,
                   "fill": source.fill or contract.tokens.colors.text_secondary,
                   "stroke": source.stroke or contract.tokens.colors.divider}
        if role == "logo":
            asset = contract.brand_assets.logo
            if asset is None and source.asset_id is None:
                continue
            if asset is None or source.asset_id != asset.asset_id:
                raise MasterProjectionError("svg_master_logo_asset_binding_missing")
            if store is None:
                store = OwnerMasterAssetStore(Path(get_app_data_directory_env() or "app_data") /
                                              "authored_vnext" / "master-assets" / "users" / str(owner))
            try:
                payload = store.open_verified(asset.asset_id, asset.digest)
            except MasterAssetError as error:
                raise MasterProjectionError(error.code) from error
            element["data_url"] = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
            validate_raster_data_url(element["data_url"])
            element["asset_digest"] = asset.digest
            element["width"] = max(1, int(box_width))
            element["height"] = max(1, int(box_height))
        elif role in {"header", "footer", "page_number"}:
            if role != "page_number" and not source.text:
                continue
            element.update(text=source.text or "", font_family=bundled_svg_font_family(contract.font_manifest.primary),
                           font_size=min(box_height * .75, (14 if role == "header" else 12) * scale), font_weight=400)
        elements.append(element)
    reservations = [{key: element[key] for key in ("id", "x", "y", "width", "height", "visibility")} for element in elements]
    furniture = {"version": "svg-master-furniture/v1", "elements": elements, "reservations": reservations,
                 "provenance": {"master_digest": contract.digest, "owner_id": str(owner)},
                 "placement": "canvas-edge-anchors-uniform-scale"}
    furniture["digest"] = _digest(furniture)
    result = {**rules, "system_furniture": furniture}
    result["digest"] = _digest({key: value for key, value in result.items() if key != "digest"})
    return result


__all__ = ["MasterProjectionError", "project_master_contract", "project_master_reference", "enrich_master_reference"]
