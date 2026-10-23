"""Source-restyle rules for the selected catalogue templates.

This deliberately bounded capability is independent of generic SVG styles.
Rules describe content-led variations present in the briefs and exemplars;
source objects supply every title, label and relationship.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from utils.svg_template_fonts import FONT_FAMILY, font_material
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX
from utils.hashing import sha256_prefixed

RULE_VERSION = "svg-source-template-rules/v2.3"
SUPPORTED_TEMPLATE_IDS = frozenset({"default", "osb-smart-banking", "screenshot-blue"})
_CATALOGUE = Path(__file__).resolve().parents[1] / "authored_styles"


def _digest(value):
    return sha256_prefixed(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def resolve_source_template_style(style_id: str):
    from utils.authored_styles import canonical_authored_style_id, load_authored_styles

    styles = load_authored_styles()
    style_id = canonical_authored_style_id(style_id, {style.id: style for style in styles})
    style = next((s for s in styles if s.id == style_id), None)
    if style is None:
        raise ValueError("svg_source_template_unavailable:" + style_id)
    return style


def reference_template_snapshot(style_spec: dict, width: int, height: int) -> dict:
    """Pin source-restyle identity without introducing output slot topology.

    ``template_snapshot`` remains the replay contract for historical solver
    snapshots. New model composition consumes this reference-only contract.
    """
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise ValueError("svg_source_template_canvas_unsupported")
    font_digest, _ = font_material(FONT_FAMILY)
    scale = min(width / DEFAULT_CANVAS_WIDTH_PX, height / DEFAULT_CANVAS_HEIGHT_PX)
    source_tokens = style_spec.get("tokens", {})
    palette = source_tokens.get("palette") or []
    reference_tokens = (style_spec.get("master_template") or style_spec.get("design_pack_template") or {}).get("tokens", {})
    tokens = {
        "background": source_tokens.get("background", "#FFFFFF"),
        "text": reference_tokens.get("text", "#0F172A"), "heading": reference_tokens.get("text", "#0F172A"),
        "accent": source_tokens.get("primary", "#2563EB"),
        "surface": reference_tokens.get("surface", "#FFFFFF"), "border": reference_tokens.get("border", "#CBD5E1"), "palette": palette,
    }
    result = {
        "version": "svg-source-design-reference/v1",
        "rule_version": "svg-source-design-reference/v1",
        "style_id": style_spec["style_id"],
        "canvas": {"width": width, "height": height},
        "composition_mode": "free-svg",
        "reference_semantics": "design-identity",
        "tokens": tokens,
        "fonts": {
            role: {"family": FONT_FAMILY, "weight": weight, "size": size * scale, "digest": font_digest}
            for role, weight, size in (("heading", 800, 42), ("body", 400, 24), ("table", 400, 22))
        },
        "table": {"header_fill": tokens["accent"], "header_text": "#FFFFFF",
                  "body_fill": tokens["surface"], "border": tokens["border"], "header_weight": 700,
                  "align": "left", "numeric_align": "right", "cell_padding": 12 * scale},
        "shape": {"fill": tokens["surface"], "border": tokens["accent"], "radius": 6 * scale},
        "common_guidance": style_spec.get("common_guidance", {}),
        "reference_examples": style_spec.get("reference_examples", []),
        "provenance": {"style_digest": style_spec["digest"], "rules": "svg-source-design-reference/v1"},
        "content_contract": "verbatim-source-content",
        "text_wrap_policy": "latin-token/v1",
    }
    if style_spec.get("source_identity_policy") == "svg-source-identity/v2":
        from services.svg_reference_identity import contrast_foreground, identity_border

        identity = (style_spec.get("design_pack_template") or {}).get("identity", {})
        typography = identity.get("typography", {})
        foreground = source_tokens.get("foreground", reference_tokens.get("foreground", tokens["text"]))
        foreground = contrast_foreground(tokens["background"], foreground)
        tokens.update(text=foreground, heading=foreground,
                      accent=source_tokens.get("accent", tokens["accent"]),
                      surface=reference_tokens.get("surface", tokens["background"]),
                      border=reference_tokens.get("border", identity_border(tokens["background"], foreground)))
        result["version"] = result["rule_version"] = "svg-source-design-reference/v2"
        result["identity_policy"] = "svg-source-identity/v2"
        result["provenance"]["rules"] = result["version"]
        result["fonts"]["heading"]["weight"] = 700
        ratio = typography.get("heading", {}).get("relative_to_body")
        if isinstance(ratio, (int, float)) and 1 <= ratio <= 4:
            result["fonts"]["heading"]["size"] = result["fonts"]["body"]["size"] * ratio
        result["table"].update(header_fill=tokens["accent"],
                               header_text=contrast_foreground(tokens["accent"], foreground),
                               body_fill=tokens["surface"], border=tokens["border"])
        result["shape"].update(fill=tokens["surface"], border=tokens["accent"])
        result["identity"] = {
            "color_roles": {key: tokens[key] for key in ("background", "text", "accent")},
            "typography": result["fonts"],
            "font_policy": "Use the packaged source measurement font; preserve observed relative emphasis.",
        }
        result["composition_guidance"] = [
            "Apply the supplied background, foreground, accent and type hierarchy consistently across text and tables.",
            "Integrate immutable text crops with neighboring text using alignment, scale and whitespace; preserve their pixels and IDs.",
            "Keep approved cell text and image crops exact; an uncertain bitmap reading is never permission to rewrite or omit it.",
        ]
    furniture = style_spec.get("master_template", {}).get("system_furniture")
    if furniture:
        result["system_furniture"] = furniture
    result["digest"] = _digest(result)
    return result


def template_snapshot(style_spec: dict, width: int, height: int) -> dict:
    style_id = style_spec.get("style_id")
    if style_id not in SUPPORTED_TEMPLATE_IDS:
        raise ValueError("svg_source_template_unsupported:" + str(style_id))
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise ValueError("svg_source_template_canvas_unsupported")
    if style_spec.get("provenance", {}).get("registry_authority") != "packaged-authored-catalogue":
        raise ValueError("svg_source_template_authority_unsupported")
    for role in ("heading_font", "body_font"):
        if style_spec["tokens"].get(role) != FONT_FAMILY:
            raise ValueError("svg_source_template_font_mismatch")
    font_digest, _ = font_material(FONT_FAMILY)
    evidence = {}
    for role, path in (
        ("definition", _CATALOGUE / f"{style_id}.yaml"),
        ("exemplar", _CATALOGUE / "exemplars" / f"{style_id}.html"),
    ):
        evidence[role] = sha256_prefixed(path.read_bytes())
    key = {
        "style_id": style_id,
        "width": width,
        "height": height,
        "authority": "packaged-authored-catalogue",
        "style_digest": style_spec["digest"],
        "font_digest": font_digest,
        "evidence": evidence,
        "asset_digests": sorted(r["digest"] for refs in style_spec.get("references", {}).values() for r in refs),
        "rules": RULE_VERSION,
    }
    return deepcopy(_cached_snapshot(json.dumps(key, sort_keys=True)))


@lru_cache(maxsize=16)
def _cached_snapshot(serialized_key: str) -> dict:
    key = json.loads(serialized_key)
    style_id, width, height = key["style_id"], key["width"], key["height"]
    scale = min(width / DEFAULT_CANVAS_WIDTH_PX, height / DEFAULT_CANVAS_HEIGHT_PX)
    margin, gap = 56 * scale, 24 * scale
    title = dict(x=margin, y=36 * scale, width=width - 2 * margin, height=130 * scale)
    body = dict(x=margin, y=198 * scale, width=width - 2 * margin, height=height - 246 * scale)
    osb = style_id == "osb-smart-banking"
    screenshot = style_id == "screenshot-blue"
    tokens = dict(
        background="#FFFFFF" if osb else "#F8FAFC",
        text="#173A63" if osb else "#0F172A",
        heading="#173A63" if osb else "#0F172A",
        accent="#0879B7" if osb else "#2563EB",
        surface="#DCECF6" if osb else "#FFFFFF",
        border="#CBD5E1",
    )
    if screenshot:
        # A compact heading leaves the original screen readable on A4. The
        # source page/canvas still comes from the immutable manifest.
        title = dict(x=margin, y=32 * scale, width=width - 2 * margin, height=96 * scale)
        body = dict(x=margin, y=160 * scale, width=width - 2 * margin, height=height - 208 * scale)
        tokens = dict(
            background="#FFFFFF",
            text="#081D49",
            heading="#081D49",
            accent="#0866FF",
            surface="#EEF7FF",
            border="#CBDFF4",
        )
    fonts = {
        role: dict(family=FONT_FAMILY, weight=weight, size=size * scale, digest=key["font_digest"])
        for role, weight, size in (("heading", 800, 42), ("body", 400, 24), ("table", 400, 22))
    }
    layouts = []
    for layout_id, role, columns in (
        ("content-stack", "content", 1),
        ("content-columns", "content", 2),
        ("content-comparison-3", "content", 3),
        ("table-focus", "data", 1),
    ):
        if screenshot and height > width and columns > 2:
            continue
        layouts.append(
            dict(
                id=layout_id,
                role=role,
                columns=columns,
                title_box=title,
                body_box=body,
                gap=gap,
                min_gap=12 * scale,
                align="left",
                reading_order="source-semantic-order",
                slots=[
                    dict(role="title", box=title, required=False, accepts=["text"], hierarchy=1),
                    dict(
                        role="evidence",
                        box=body,
                        required=True,
                        accepts=["text", "table", "image", "shape", "chart"],
                        hierarchy=2,
                    ),
                ],
                allowed_variations=dict(
                    model_layout=True,
                    min_gap=12 * scale,
                    max_columns=columns,
                    column_ratios=[0.5, 0.65, 0.72] if columns == 2 else [1 / columns],
                    gap_range=[12 * scale, 32 * scale],
                    content_reflow=True,
                    title_hierarchy_required=True,
                    preserve_alignment=True,
                    decorative_overlap_only=True,
                ),
            )
        )
        if columns == 3:
            compact_title = {**title, "height": 80 * scale}
            compact_body = {**body, "y": 146 * scale, "height": height - 194 * scale}
            layouts[-1].update(title_box=compact_title, body_box=compact_body, intra_group_gap=3 * scale)
            layouts[-1]["slots"] = [
                {**layouts[-1]["slots"][0], "box": compact_title},
                {**layouts[-1]["slots"][1], "box": compact_body},
            ]
            layouts[-1]["allowed_variations"].update(
                intra_group_gap=3 * scale,
                requires_same_source_parallel_group=True,
                title_height_min=80 * scale,
                signature_rule_y=128 * scale,
            )
            layouts[-1]["applicability"] = dict(
                requires_source_parallel_groups=True,
                minimum_group_count=3,
                minimum_text_blocks_per_group=3,
                source_geometry_evidence=["repeated_group_dimensions", "aligned_top", "disjoint_horizontal_extent"],
                rationale="Source-backed control comparison and process groups"
                if osb
                else "Source-backed spacious or ranked comparison",
            )
    # Actual signature cue, with content-free geometry; no invented page label.
    decoration = (
        dict(id="template-section-rule", kind="rect", x=0, y=0, width=width, height=9 * scale, fill=tokens["accent"])
        if osb
        else dict(
            id="template-editorial-rule",
            kind="rect",
            x=margin,
            y=180 * scale,
            width=80 * scale,
            height=3 * scale,
            fill=tokens["accent"],
        )
    )
    if screenshot:
        decoration = dict(
            id="template-screen-rule",
            kind="rect",
            x=margin,
            y=144 * scale,
            width=80 * scale,
            height=3 * scale,
            fill=tokens["accent"],
        )
    result = dict(
        version=RULE_VERSION,
        rule_version=RULE_VERSION,
        style_id=style_id,
        canvas=dict(width=width, height=height),
        tokens=tokens,
        fonts=fonts,
        layouts=layouts,
        decorations=[decoration],
        table=dict(
            header_fill=tokens["accent"] if osb else "#E2E8F0",
            header_text="#FFFFFF" if osb else tokens["text"],
            body_fill=tokens["surface"],
            border=tokens["border"],
            header_weight=700,
            align="left",
            numeric_align="right",
            cell_padding=12 * scale,
        ),
        shape=dict(
            fill=tokens["surface"], border=tokens["accent"] if osb else tokens["border"], radius=6 * scale if osb else 0
        ),
        provenance=key,
        validation=dict(
            automatic=[
                "colors",
                "font_bytes",
                "title_hierarchy",
                "slot_bounds",
                "minimum_gap",
                "signature_rule",
                "table_style",
            ],
            visual_review="required",
            unsupported_roles=["cover", "section", "closing"],
            limitations=[
                "No synthesized section labels or source-note text",
                "No additional cover, agenda, or decorative relationships",
            ],
        ),
        allowed_variations=dict(
            model_layout=True, min_gap=12 * scale, max_columns=3, three_columns_require_source_parallel_groups=True
        ),
    )
    if screenshot:
        result["table"].update(header_fill="#435D80", header_text="#FFFFFF", body_fill="#FFFFFF")
        result["shape"].update(fill="#EEF7FF", border="#CBDFF4", radius=6 * scale)
        result["image"] = dict(max_height_fraction=0.82)
        result["allowed_variations"]["max_columns"] = 2 if height > width else 3
    result["digest"] = _digest(result)
    return result
