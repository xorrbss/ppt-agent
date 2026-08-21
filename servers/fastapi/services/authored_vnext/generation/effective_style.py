"""Template-owned composition with company furniture overlaid separately."""

from __future__ import annotations

import json
from typing import Any

from models.authored_vnext.deck.composition import (
    AuthoredCompositionPin,
    AuthoredTemplateSnapshot,
    BodyVisualSnapshot,
    ConsistencyPolicySnapshot,
    DeckMetadataSnapshot,
    ReservedZoneSnapshot,
    ResolvedReferenceLayoutSnapshot,
    SlideRolePolicySnapshot,
    seal_authored_template_snapshot,
    seal_consistency_policy_snapshot,
)
from models.authored_vnext.master.contract import (
    CommonElementScope,
    DeckMasterContract,
    FontManifest,
    FontTokens,
    LayoutRegistry,
    LayoutSlot,
    LayoutElementScope,
    MasterLayout,
    Ownership,
    PowerPointMargins,
    Rect,
    SemanticElementContent,
    SemanticElementStyle,
    SemanticMasterElement,
    TextStyle,
    ThemeScripts,
    TitleFitPolicy,
)
from services.authored_vnext.master.digests import seal_master_contract
from services.authored_vnext.generation.composition import compose_authored_style
from services.authored_vnext.generation.retained_reference import (
    RETAINED_REFERENCE_REGISTRY,
    compile_retained_reference_layout,
)
from services.authored_vnext.retained_reference_marker import (
    RETAINED_REFERENCE_MARKER_CODEC,
    encode_reference_layout_marker,
)
from services.authored_vnext.body_grammar import BODY_GRAMMAR_ROLE_PREFIX
from services.authored_vnext.master.digests import canonical_digest
from utils.authored_styles import AuthoredStyle


_COMPOSED_ROLES = frozenset(
    {"authored-composition-panel", "authored-composition-accent"}
)
_LEGACY_MERGE_POLICY_VERSION = "master-slots-authored-body/v1"
_VISIBLE_BODY_LAYER_MERGE_POLICY_VERSION = "master-slots-authored-body/v2"
_BODY_GRAMMAR_MERGE_POLICY_VERSION = "master-slots-authored-body/v3"
_COMPANY_POLICY_MERGE_POLICY_VERSION = (
    "company-furniture-consistency-policy-authored-template/v1"
)
_FURNITURE_OVERLAY_MERGE_POLICY_VERSION = (
    "furniture-overlay-authored-template/v2"
)
_REFERENCE_LAYOUT_ROLE_PREFIX = "retained-reference-layout:"
_SYSTEM_BACKGROUND = "#FFFFFF"
_SYSTEM_SURFACE = "#F5F7FA"
_SYSTEM_TEXT = "#1F2937"
_SYSTEM_SECONDARY_TEXT = "#4B5563"
_SYSTEM_ACCENT = "#2563EB"
_SYSTEM_DIVIDER = "#D1D5DB"
_SYSTEM_WARNING = "#D97706"
_SYSTEM_FONT = "Noto Sans KR"


def _is_composed_role(role: str | None) -> bool:
    return role in _COMPOSED_ROLES or bool(
        role
        and (
            role.startswith(BODY_GRAMMAR_ROLE_PREFIX)
            or role.startswith(_REFERENCE_LAYOUT_ROLE_PREFIX)
        )
    )


def _furniture_reserved_zones(
    contract: DeckMasterContract,
) -> list[ReservedZoneSnapshot]:
    result: list[ReservedZoneSnapshot] = []
    for name, item in (
        ("logo", contract.furniture.logo),
        ("header", contract.furniture.header),
        ("footer", contract.furniture.footer),
        ("divider", contract.furniture.divider),
        ("pageNumber", contract.furniture.page_number),
    ):
        if item.visibility == "hidden":
            continue
        if item.kind == "logo" and item.asset_id is None:
            continue
        if item.kind == "text" and not (item.text or "").strip():
            continue
        result.append(
            ReservedZoneSnapshot(
                id=f"reserved:{name.lower()}",
                kind=name,
                bounds=item.bounds,
                source_element_id=item.id,
                classification="confirmed",
            )
        )
    return result


def _semantic_policy_evidence(
    contract: DeckMasterContract,
) -> tuple[list[ReservedZoneSnapshot], list[str]]:
    zones: list[ReservedZoneSnapshot] = []
    review: list[str] = []
    for element in contract.semantic_elements:
        placeholder = element.content.placeholder_role
        role = element.content.role or ""
        if element.type == "placeholder":
            # Title evidence is projected into the role policy. Body/content/
            # image placeholders are intentionally ignored by v4 composition.
            continue
        confirmed = role in {
            "brand-logo",
            "fixed-text",
            "fixed-copy",
            "header",
            "footer",
            "company-line",
            "divider",
            "background-decoration",
            "page-number",
            "slide-number",
        }
        if role == "ai-text-zone" or element.editable_by_ai:
            review.append(element.id)
            zones.append(
                ReservedZoneSnapshot(
                    id=f"reserved:review-{element.id.removeprefix('element:')}",
                    kind="reviewRequired",
                    bounds=element.bounds,
                    source_element_id=element.id,
                    classification="reviewRequired",
                )
            )
            continue
        if not confirmed:
            review.append(element.id)
            zones.append(
                ReservedZoneSnapshot(
                    id=f"reserved:review-{element.id.removeprefix('element:')}",
                    kind="reviewRequired",
                    bounds=element.bounds,
                    source_element_id=element.id,
                    classification="reviewRequired",
                )
            )
            continue
        kind = (
            "logo"
            if role == "brand-logo"
            else "fixedText"
            if element.type in {"text", "pageNumber"}
            else "backgroundDecoration"
        )
        zones.append(
            ReservedZoneSnapshot(
                id=f"reserved:semantic-{element.id.removeprefix('element:')}",
                kind=kind,
                bounds=element.bounds,
                source_element_id=element.id,
                classification="confirmed" if confirmed else "projected",
            )
        )
    return zones, sorted(set(review))


def _canvas_rect(contract: DeckMasterContract) -> Rect:
    return Rect(
        x=0,
        y=0,
        width=float(contract.canvas.width_px),
        height=float(contract.canvas.height_px),
    )


def build_ai_edit_zone_template_design_context(
    contract: DeckMasterContract,
) -> dict[str, object]:
    """Project the effective template DNA into a closed AI-zone vocabulary.

    The authoring model receives the template's named tokens and semantic
    grammar, not an invitation to emit arbitrary CSS values.  All color and
    font values originate in the already sealed effective contract.
    """

    if contract.digest is None:
        raise ValueError("AI edit zone design context requires a sealed contract")
    font_tokens = contract.tokens.fonts.model_dump(mode="json", by_alias=True)
    color_tokens = contract.tokens.colors.model_dump(mode="json", by_alias=True)
    allowed_fonts = list(
        dict.fromkeys(
            [
                contract.tokens.fonts.latin,
                contract.tokens.fonts.east_asian,
                contract.tokens.fonts.complex_script,
                *contract.tokens.fonts.fallbacks,
                contract.font_manifest.primary,
                contract.font_manifest.windows_fallback,
                *contract.font_manifest.fallbacks,
            ]
        )
    )
    spacing_values = sorted(
        {
            value
            for style in (
                contract.semantic_styles.eyebrow,
                contract.semantic_styles.title,
                contract.semantic_styles.body,
                contract.semantic_styles.note,
                contract.semantic_styles.right_callout,
                contract.semantic_styles.warning,
                contract.semantic_styles.caption,
                contract.semantic_styles.source,
            )
            for value in (
                style.margins.left_pt,
                style.margins.right_pt,
                style.margins.top_pt,
                style.margins.bottom_pt,
            )
        }
    )
    semantic_roles = sorted(
        {
            element.content.role
            for element in contract.semantic_elements
            if element.content.role
            and not element.content.role.startswith("deck-metadata:")
        }
    )
    return {
        "schemaVersion": "authored-vnext-template-design-context/v1",
        "masterDigest": contract.digest,
        "stylePolicy": "inheritTemplate",
        "canvas": {
            "width": contract.canvas.width_px,
            "height": contract.canvas.height_px,
        },
        "tokens": {
            "fonts": font_tokens,
            "colors": color_tokens,
        },
        "allowedFonts": allowed_fonts,
        "spacing": {"unit": "pt", "values": spacing_values},
        "shapeGrammar": {
            "allowedPrimitives": [
                "text",
                "card",
                "list",
                "table",
                "chart",
                "icon",
                "rect",
                "ellipse",
            ],
            "semanticRoles": semantic_roles,
            "cardPolicy": "surfaceDividerTokensOnly",
            "iconPolicy": "templateSemanticIconsOnly",
            "rawCssPolicy": "forbid",
            "arbitraryColorPolicy": "forbid",
            "arbitraryFontPolicy": "forbid",
        },
    }


def _rect_union(values: list[Rect]) -> Rect:
    left = min(value.x for value in values)
    top = min(value.y for value in values)
    right = max(value.right for value in values)
    bottom = max(value.bottom for value in values)
    return Rect(x=left, y=top, width=right - left, height=bottom - top)


def _ai_edit_zone_envelopes(contract: DeckMasterContract) -> dict[str, Rect]:
    """Resolve only outer layout envelopes; never infer internal placeholders."""

    layouts = {
        "cover": {contract.layouts.cover.id},
        "section": {contract.layouts.section.id},
        "content": {
            contract.layouts.standard.id,
            contract.layouts.long_title.id,
        },
        "closing": {contract.layouts.section.id},
    }
    zones = list(getattr(contract, "ai_edit_zones", ()) or ())
    result: dict[str, Rect] = {}
    for role, layout_ids in layouts.items():
        matching: list[Rect] = []
        for zone in zones:
            scope = zone.scope
            if scope.kind == "common" or (
                scope.kind == "layout" and scope.layout_id in layout_ids
            ):
                matching.append(zone.bounds)
        result[role] = _rect_union(matching) if matching else _canvas_rect(contract)
    return result


def _role_policy(
    *,
    role: str,
    envelope: Rect,
) -> SlideRolePolicySnapshot:
    title_height = max(
        1.0,
        envelope.height * (0.28 if role in {"cover", "section", "closing"} else 0.20),
    )
    title = Rect(
        x=envelope.x,
        y=envelope.y,
        width=envelope.width,
        height=min(title_height, envelope.height),
    )
    body_top = min(envelope.bottom - 1.0, title.bottom)
    body = Rect(
        x=envelope.x,
        y=body_top,
        width=envelope.width,
        height=max(1.0, envelope.bottom - body_top),
    )
    return SlideRolePolicySnapshot(
        role=role,
        title_anchor=title,
        content_rect=body,
        max_title_lines=3 if role in {"cover", "closing"} else 2,
        title_alignment="center" if role in {"cover", "closing"} else "left",
        bullet_policy="none" if role in {"cover", "closing"} else "preserve",
        show_document_id=role in {"cover", "closing"},
    )


def _canonical_role_policy(contract: DeckMasterContract, role: str) -> SlideRolePolicySnapshot:
    """Return company-neutral Authored template geometry.

    Furniture is an adapter overlay. Moving it must never move title/body Scene
    elements or change their digest, so company AI zones and reserved edges are
    evidence for overlay validation only, not inputs to authored layout.
    """

    if (
        abs(contract.canvas.width_px - 1280.0) > 0.01
        or abs(contract.canvas.height_px - 720.0) > 0.01
    ):
        raise ValueError(
            "authored_vnext_canonical_canvas_unsupported: company composition "
            "requires the 1280x720 authored canvas"
        )
    if role == "cover":
        title = Rect(x=92.0, y=176.0, width=1096.0, height=180.0)
    elif role in {"section", "closing"}:
        title = Rect(x=92.0, y=146.0, width=1096.0, height=180.0)
    else:
        title = Rect(x=92.0, y=84.0, width=1096.0, height=140.0)
    content = Rect(
        x=60.0,
        y=title.bottom + 24.0,
        width=1160.0,
        height=640.0 - title.bottom - 24.0,
    )
    return SlideRolePolicySnapshot(
        role=role,
        title_anchor=title,
        content_rect=content,
        max_title_lines=3 if role in {"cover", "closing"} else 2,
        title_alignment="center" if role in {"cover", "closing"} else "left",
        bullet_policy="none" if role in {"cover", "closing"} else "preserve",
        show_document_id=role in {"cover", "closing"},
    )


def build_consistency_policy_snapshot(
    contract: DeckMasterContract,
) -> ConsistencyPolicySnapshot:
    if contract.digest is None:
        raise ValueError("consistency policy requires a sealed master")
    furniture = _furniture_reserved_zones(contract)
    semantic, review = _semantic_policy_evidence(contract)
    zones = [*furniture, *semantic]
    content_rect = _canvas_rect(contract)
    role_policies = [
        _canonical_role_policy(contract, "cover"),
        _canonical_role_policy(contract, "section"),
        _canonical_role_policy(contract, "content"),
        _canonical_role_policy(contract, "closing"),
    ]
    return seal_consistency_policy_snapshot(
        source_master_digest=contract.digest,
        content_rect=content_rect,
        reserved_zones=zones,
        role_policies=role_policies,
        metadata=DeckMetadataSnapshot(),
        review_required_element_ids=review,
    )


def _authored_template_snapshot(
    contract: DeckMasterContract,
    policy: ConsistencyPolicySnapshot,
    style: AuthoredStyle | None,
) -> AuthoredTemplateSnapshot:
    del policy
    style_digest = _style_source_digest(style) if style is not None else None
    retained_definition = (
        RETAINED_REFERENCE_REGISTRY.get(style.id) if style is not None else None
    )
    if retained_definition is not None:
        retained_reference_id = str(
            retained_definition["retainedReferenceId"]
        )
        archetypes = list(retained_definition["archetypes"])
        semantic_roles = list(retained_definition["semanticRoles"])
        resolved_reference_layout = ResolvedReferenceLayoutSnapshot.model_validate(
            compile_retained_reference_layout(
                retained_reference_id,
                canvas_width=contract.canvas.width_px,
                canvas_height=contract.canvas.height_px,
                role_envelopes=_ai_edit_zone_envelopes(contract),
            )
        )
        resolved_reference_layout_digest = resolved_reference_layout.digest
    else:
        retained_reference_id = (
            f"authored-style/{style.id}" if style is not None else "system-fallback/v1"
        )
        archetypes = ["cover", "content", "section", "closing"]
        semantic_roles = ["cover-summary", "content-card", "section-summary"]
        resolved_reference_layout = None
        resolved_reference_layout_digest = None
    return seal_authored_template_snapshot(
        style_id=style.id if style is not None else None,
        style_digest=style_digest,
        retained_reference_id=retained_reference_id,
        archetypes=archetypes,
        semantic_roles=semantic_roles,
        resolved_reference_layout=resolved_reference_layout,
        resolved_reference_layout_digest=resolved_reference_layout_digest,
    )


def _mix(background: str, foreground: str, foreground_ratio: float) -> str:
    def channels(value: str) -> tuple[int, int, int]:
        return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))

    bg = channels(background)
    fg = channels(foreground)
    mixed = tuple(
        round(left * (1 - foreground_ratio) + right * foreground_ratio)
        for left, right in zip(bg, fg, strict=True)
    )
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def _style_source_digest(style: AuthoredStyle) -> str:
    """Hash every source field consumed by the deterministic composer."""

    return canonical_digest(
        {
            "id": style.id,
            "brief": style.brief,
            "previewAccent": style.preview_accent,
            "previewBackground": style.preview_bg,
            "previewPalette": list(style.preview_palette or ()),
            "previewVariant": style.preview_variant,
            "primaryColor": style.primary_color,
            "backgroundColor": style.background_color,
            "headingFont": style.heading_font,
            "bodyFont": style.body_font,
            "monoFont": style.mono_font,
            "illustrationPrompt": style.illustration_prompt,
        }
    )


def _body_visual_snapshot(
    contract: DeckMasterContract,
    style: AuthoredStyle | None,
    visual_direction: Any | None,
) -> BodyVisualSnapshot:
    del contract
    if style is None:
        imagery = getattr(visual_direction, "imagery", None)
        return BodyVisualSnapshot(
            accent=_SYSTEM_ACCENT,
            panel_fill=_SYSTEM_SURFACE,
            text_color=_SYSTEM_TEXT,
            secondary_text_color=_SYSTEM_SECONDARY_TEXT,
            warning=_SYSTEM_WARNING,
            panel_border_color=_SYSTEM_DIVIDER,
            chart_palette=[_SYSTEM_ACCENT, _SYSTEM_SECONDARY_TEXT, _SYSTEM_WARNING],
            table_header_fill=_SYSTEM_ACCENT,
            process_accent=_SYSTEM_ACCENT,
            heading_font=_SYSTEM_FONT,
            body_font=_SYSTEM_FONT,
            density="balanced",
            panel_enabled=False,
            image_direction=imagery,
            corner_radius_pt=0.0,
        )
    accent = style.primary_color or style.preview_accent
    background = style.background_color or style.preview_bg
    palette = list(style.preview_palette or ())
    text = palette[1] if len(palette) >= 2 else _SYSTEM_TEXT
    panel = palette[3] if len(palette) >= 4 else _mix(background, accent, 0.12)
    warning = palette[4] if len(palette) >= 5 else _SYSTEM_WARNING
    chart_palette = palette or [accent, text, warning]
    brief = style.brief.lower()
    density = (
        "compact"
        if any(marker in brief for marker in ("dense paragraph", "compact density"))
        else "airy"
        if "editorial whitespace" in brief and "table" not in brief
        else "balanced"
    )
    return BodyVisualSnapshot(
        accent=accent,
        panel_fill=panel,
        text_color=text,
        secondary_text_color=_SYSTEM_SECONDARY_TEXT,
        warning=warning,
        panel_border_color=_mix(background, accent, 0.32),
        chart_palette=chart_palette[:12],
        table_header_fill=accent,
        process_accent=accent,
        heading_font=style.heading_font or _SYSTEM_FONT,
        body_font=style.body_font or _SYSTEM_FONT,
        density=density,
        panel_enabled=True,
        image_direction=style.illustration_prompt,
        corner_radius_pt=8.0,
    )


def _value(snapshot: Any, name: str):
    if isinstance(snapshot, dict):
        if name in snapshot:
            return snapshot[name]
        camel = name.split("_")[0] + "".join(
            part[:1].upper() + part[1:] for part in name.split("_")[1:]
        )
        return snapshot.get(camel)
    return getattr(snapshot, name)


def _density_font_pt(current: float, density: str) -> float:
    ceiling = {
        "compact": 13.0,
        "dense": 13.0,
        "balanced": 16.0,
        "spacious": 18.0,
    }.get(density, 16.0)
    return max(8.0, min(current, ceiling))


def _body_panel_elements(
    contract: DeckMasterContract,
    body_visual: Any,
    *,
    visible_layers: bool,
) -> list[SemanticMasterElement]:
    if not bool(_value(body_visual, "panel_enabled")):
        return []
    panel_fill = str(_value(body_visual, "panel_fill"))
    border = str(_value(body_visual, "panel_border_color"))
    accent = str(_value(body_visual, "accent"))
    elements: list[SemanticMasterElement] = []
    for layout in (
        contract.layouts.cover,
        contract.layouts.section,
        contract.layouts.standard,
        contract.layouts.long_title,
    ):
        body_slot = next(slot for slot in layout.slots if slot.role == "body")
        bounds = body_slot.bounds
        suffix = layout.kind.lower().replace("title", "-title")
        elements.extend(
            [
                SemanticMasterElement(
                    id=f"element:composition-panel-{suffix}",
                    type="shape",
                    scope=LayoutElementScope(kind="layout", layout_id=layout.id),
                    bounds=bounds,
                    rotation=0.0,
                    # V2 keeps authored body styling above the slide background.
                    # V1 is replayed exactly for already-persisted composition pins.
                    z_index=0 if visible_layers else -90_000,
                    visibility="layoutOnly",
                    editable_by_user=False,
                    editableByAI=False,
                    locked=True,
                    style=SemanticElementStyle(
                        fill=panel_fill,
                        stroke=border,
                        stroke_width=1.0,
                        shape="rect",
                    ),
                    content=SemanticElementContent(role="authored-composition-panel"),
                ),
                SemanticMasterElement(
                    id=f"element:composition-accent-{suffix}",
                    type="shape",
                    scope=LayoutElementScope(kind="layout", layout_id=layout.id),
                    bounds=Rect(
                        x=bounds.x,
                        y=bounds.y,
                        width=min(6.0, bounds.width),
                        height=bounds.height,
                    ),
                    rotation=0.0,
                    z_index=1 if visible_layers else -89_999,
                    visibility="layoutOnly",
                    editable_by_user=False,
                    editableByAI=False,
                    locked=True,
                    style=SemanticElementStyle(fill=accent, shape="rect"),
                    content=SemanticElementContent(role="authored-composition-accent"),
                ),
            ]
        )
    return elements


def _policy_by_role(
    policy: ConsistencyPolicySnapshot,
    role: str,
) -> SlideRolePolicySnapshot:
    return next(item for item in policy.role_policies if item.role == role)


def _policy_layout(
    source: MasterLayout,
    policy: SlideRolePolicySnapshot,
) -> MasterLayout:
    return source.model_copy(
        update={
            "slots": [
                LayoutSlot(
                    id=f"slot:template-{source.kind.lower()}-title",
                    role="title",
                    placeholder_index=0,
                    bounds=policy.title_anchor,
                    ownership=Ownership(locked=False, model_writable=True),
                    style="title",
                ),
                LayoutSlot(
                    id=f"slot:template-{source.kind.lower()}-body",
                    role="body",
                    placeholder_index=1,
                    bounds=policy.content_rect,
                    ownership=Ownership(locked=False, model_writable=True),
                    style="body",
                ),
            ]
        }
    )


def _snapshot_value(snapshot: Any, name: str) -> Any:
    if isinstance(snapshot, dict):
        return snapshot.get(name)
    return getattr(snapshot, name)


def _snapshot_rect(value: Any) -> Rect:
    if isinstance(value, Rect):
        return value
    return Rect.model_validate(value)


def _reference_layout_record(template: AuthoredTemplateSnapshot, archetype: str) -> dict[str, Any]:
    snapshot = template.resolved_reference_layout
    if snapshot is None:
        raise ValueError("retained template has no resolved reference layout")
    record = next(
        (item for item in snapshot.layouts if item.get("archetype") == archetype),
        None,
    )
    if record is None:
        raise ValueError(f"retained template is missing {archetype} geometry")
    return record


def _reference_policy_layout(
    source: MasterLayout,
    template: AuthoredTemplateSnapshot,
    archetype: str,
) -> MasterLayout:
    record = _reference_layout_record(template, archetype)
    return source.model_copy(
        update={
            "slots": [
                LayoutSlot(
                    id=f"slot:template-{source.kind.lower()}-title",
                    role="title",
                    placeholder_index=0,
                    bounds=_snapshot_rect(record["title"]["bounds"]),
                    ownership=Ownership(locked=False, model_writable=True),
                    style="title",
                ),
                LayoutSlot(
                    id=f"slot:template-{source.kind.lower()}-body",
                    role="body",
                    placeholder_index=1,
                    bounds=_snapshot_rect(record["contentRect"]),
                    ownership=Ownership(locked=False, model_writable=True),
                    style="body",
                ),
            ]
        }
    )


def _reference_layout_markers(
    template: AuthoredTemplateSnapshot,
) -> list[SemanticMasterElement]:
    snapshot = template.resolved_reference_layout
    if snapshot is None:
        return []
    payload = encode_reference_layout_marker(
        snapshot.model_dump(mode="json", by_alias=True)
    )
    chunk_size = 9000
    chunks = [payload[index : index + chunk_size] for index in range(0, len(payload), chunk_size)]
    digest_hex = snapshot.digest.removeprefix("sha256:")
    return [
        SemanticMasterElement(
            id=f"element:retained-reference-layout-{index + 1:03d}",
            type="text",
            scope=CommonElementScope(kind="common"),
            bounds=Rect(x=0, y=0, width=1, height=1),
            rotation=0,
            z_index=-100_000,
            visibility="hidden",
            editable_by_user=False,
            editableByAI=False,
            locked=True,
            style=SemanticElementStyle(),
            content=SemanticElementContent(
                text=chunk,
                role=(
                    f"{_REFERENCE_LAYOUT_ROLE_PREFIX}{RETAINED_REFERENCE_MARKER_CODEC}:"
                    f"{index + 1:03d}-of-"
                    f"{len(chunks):03d}:{digest_hex}"
                ),
            ),
        )
        for index, chunk in enumerate(chunks)
    ]


def _company_semantic_elements(
    contract: DeckMasterContract,
) -> list[SemanticMasterElement]:
    """Keep confirmed furniture/decor; omit all source content placeholders."""

    kept: list[SemanticMasterElement] = []
    for element in contract.semantic_elements:
        role = element.content.role or ""
        if element.type == "placeholder":
            continue
        if role == "ai-text-zone" or element.editable_by_ai:
            # A free-text RFP/document number is not legal in v4. The source
            # element remains immutable in the published master and is recorded
            # as review-required by the policy snapshot, but is hidden here.
            continue
        confirmed = role in {
            "brand-logo",
            "fixed-text",
            "fixed-copy",
            "header",
            "footer",
            "company-line",
            "divider",
            "background-decoration",
            "page-number",
            "slide-number",
        }
        if confirmed:
            kept.append(element)
    return kept


def _template_visual_values(
    template: AuthoredTemplateSnapshot,
    body_visual: Any,
) -> dict[str, Any]:
    snapshot = template.resolved_reference_layout
    if snapshot is None:
        return {
            "background": _SYSTEM_BACKGROUND,
            "surface": str(_value(body_visual, "panel_fill")),
            "text_primary": str(_value(body_visual, "text_color")),
            "text_secondary": str(_value(body_visual, "secondary_text_color")),
            "accent": str(_value(body_visual, "accent")),
            "divider": str(_value(body_visual, "panel_border_color")),
            "warning": str(_value(body_visual, "warning")),
            "title_font_pt": 36.0,
            "title_line_height": 1.2,
            "title_weight": 700,
            "body_font_pt": 16.0,
            "body_line_height": 1.25,
            "body_weight": 400,
        }
    content = _reference_layout_record(template, "content")
    title_style = dict(content["title"].get("textStyle") or {})
    block = next(
        (item for item in content["blocks"] if isinstance(item, dict)),
        {},
    )
    body_style = dict(block.get("textStyle") or {})
    background = str(content.get("canvasBackground") or _SYSTEM_BACKGROUND)
    surface = str(block.get("fill") or background)
    primary = str(title_style.get("color") or _SYSTEM_TEXT)
    secondary = str(body_style.get("color") or block.get("textColor") or primary)
    accent = str(block.get("accent") or block.get("stroke") or _SYSTEM_ACCENT)
    return {
        "background": background,
        "surface": surface,
        "text_primary": primary,
        "text_secondary": secondary,
        "accent": accent,
        "divider": str(block.get("stroke") or accent),
        "warning": str(
            block.get("warning")
            or block.get("accent")
            or block.get("stroke")
            or _SYSTEM_WARNING
        ),
        "title_font_pt": float(title_style.get("fontPt") or 36.0),
        "title_line_height": float(title_style.get("lineHeight") or 1.2),
        "title_weight": int(title_style.get("fontWeight") or 700),
        "body_font_pt": float(body_style.get("fontPt") or 16.0),
        "body_line_height": float(body_style.get("lineHeight") or 1.25),
        "body_weight": int(body_style.get("fontWeight") or 400),
    }


def _template_semantic_styles(
    contract: DeckMasterContract,
    values: dict[str, Any],
):
    title_font_pt = max(8.0, min(96.0, values["title_font_pt"]))
    body_font_pt = max(8.0, min(96.0, values["body_font_pt"]))
    title = TextStyle(
        font_token="eastAsian",
        color_token="textPrimary",
        font_pt=title_font_pt,
        min_font_pt=title_font_pt,
        line_height=max(0.8, min(2.0, values["title_line_height"])),
        font_weight=max(100, min(900, round(values["title_weight"] / 100) * 100)),
        max_lines=4,
        margins=PowerPointMargins(left_pt=0, right_pt=0, top_pt=0, bottom_pt=0),
    )
    body = TextStyle(
        font_token="eastAsian",
        color_token="textSecondary",
        font_pt=body_font_pt,
        min_font_pt=body_font_pt,
        line_height=max(0.8, min(2.0, values["body_line_height"])),
        font_weight=max(100, min(900, round(values["body_weight"] / 100) * 100)),
        max_lines=12,
        margins=PowerPointMargins(left_pt=0, right_pt=0, top_pt=0, bottom_pt=0),
    )
    return contract.semantic_styles.model_copy(update={"title": title, "body": body})


def _template_font_contract(
    template: AuthoredTemplateSnapshot,
    body_visual: Any,
) -> tuple[FontTokens, FontManifest]:
    snapshot = template.resolved_reference_layout
    if snapshot is None:
        title_font = str(_value(body_visual, "heading_font") or _SYSTEM_FONT)
        body_font = str(_value(body_visual, "body_font") or _SYSTEM_FONT)
    else:
        content = _reference_layout_record(template, "content")
        title_font = str(
            content["title"].get("textStyle", {}).get("fontFamily")
            or _SYSTEM_FONT
        )
        body_font = str(
            next(
                (
                    block.get("textStyle", {}).get("fontFamily")
                    for block in content["blocks"]
                    if block.get("textStyle", {}).get("fontFamily")
                ),
                title_font,
            )
        )
    fallbacks = list(dict.fromkeys((body_font, title_font)))
    tokens = FontTokens(
        latin=body_font,
        east_asian=body_font,
        complex_script=body_font,
        fallbacks=fallbacks,
    )
    manifest = FontManifest(
        primary=body_font,
        fallbacks=fallbacks,
        windows_fallback=body_font,
        theme_scripts=ThemeScripts(
            latin=body_font,
            hang=body_font,
            jpan=body_font,
            hans=body_font,
            hant=body_font,
        ),
        files=[],
        digest=None,
    )
    return tokens, manifest


def _materialize_company_policy_contract(
    contract: DeckMasterContract,
    body_visual: Any,
    *,
    policy: ConsistencyPolicySnapshot,
    template: AuthoredTemplateSnapshot,
) -> DeckMasterContract:
    if policy.source_master_digest != contract.digest:
        raise ValueError("consistency policy master digest mismatch")
    retained_layout_selected = template.resolved_reference_layout is not None
    visual = _template_visual_values(template, body_visual)
    colors = contract.tokens.colors.model_copy(
        update={key: visual[key] for key in (
            "background",
            "surface",
            "text_primary",
            "text_secondary",
            "accent",
            "divider",
            "warning",
        )}
    )
    semantic_styles = _template_semantic_styles(contract, visual)
    font_tokens, font_manifest = _template_font_contract(template, body_visual)
    if retained_layout_selected:
        layouts = LayoutRegistry(
            cover=_reference_policy_layout(contract.layouts.cover, template, "cover"),
            section=_reference_policy_layout(contract.layouts.section, template, "section"),
            standard=_reference_policy_layout(contract.layouts.standard, template, "content"),
            long_title=_reference_policy_layout(contract.layouts.long_title, template, "content"),
        )
    else:
        content_policy = _policy_by_role(policy, "content")
        long_title_title = Rect(x=92.0, y=84.0, width=1096.0, height=176.0)
        long_title_policy = content_policy.model_copy(
            update={
                "title_anchor": long_title_title,
                "content_rect": Rect(
                    x=60.0,
                    y=long_title_title.bottom + 24.0,
                    width=1160.0,
                    height=640.0 - long_title_title.bottom - 24.0,
                ),
                "max_title_lines": 3,
            }
        )
        layouts = LayoutRegistry(
            cover=_policy_layout(
                contract.layouts.cover,
                _policy_by_role(policy, "cover"),
            ),
            section=_policy_layout(
                contract.layouts.section,
                _policy_by_role(policy, "section"),
            ),
            standard=_policy_layout(contract.layouts.standard, content_policy),
            long_title=_policy_layout(contract.layouts.long_title, long_title_policy),
        )
    marker_style = template.style_id or "system-fallback"
    markers = [
        SemanticMasterElement(
            id="element:authored-body-grammar-v2",
            type="text",
            scope=CommonElementScope(kind="common"),
            bounds=Rect(x=0, y=0, width=1, height=1),
            rotation=0,
            z_index=-100_000,
            visibility="hidden",
            editable_by_user=False,
            editableByAI=False,
            locked=True,
            style=SemanticElementStyle(),
            content=SemanticElementContent(
                role=(
                    f"{BODY_GRAMMAR_ROLE_PREFIX}{marker_style}:v3"
                    if retained_layout_selected
                    else f"{BODY_GRAMMAR_ROLE_PREFIX}{marker_style}:v2"
                )
            ),
        ),
        SemanticMasterElement(
            id="element:company-consistency-policy",
            type="text",
            scope=CommonElementScope(kind="common"),
            bounds=Rect(x=0, y=0, width=1, height=1),
            rotation=0,
            z_index=-100_000,
            visibility="hidden",
            editable_by_user=False,
            editableByAI=False,
            locked=True,
            style=SemanticElementStyle(),
            content=SemanticElementContent(
                role=f"company-consistency-policy:{policy.digest.removeprefix('sha256:')}"
            ),
        ),
    ]
    if policy.metadata.document_id is not None:
        markers.append(
            SemanticMasterElement(
                id="element:deck-metadata",
                type="text",
                scope=CommonElementScope(kind="common"),
                bounds=Rect(x=0, y=0, width=1, height=1),
                rotation=0,
                z_index=-100_000,
                visibility="hidden",
                editable_by_user=False,
                editableByAI=False,
                locked=True,
                style=SemanticElementStyle(),
                content=SemanticElementContent(
                    text=policy.metadata.document_id,
                    role=f"deck-metadata:{policy.metadata.kind}",
                ),
            )
        )
    markers.extend(_reference_layout_markers(template))
    candidate = contract.model_copy(
        update={
            "tokens": contract.tokens.model_copy(
                update={"colors": colors, "fonts": font_tokens}
            ),
            "font_manifest": font_manifest,
            "semantic_styles": semantic_styles,
            "layouts": layouts,
            "custom_layouts": [],
            "fit_policy": TitleFitPolicy(
                preferred_font_pt=max(
                    16.0,
                    min(72.0, float(visual["title_font_pt"])),
                ),
                minimum_font_pt=12.0,
                standard_max_lines=3,
                long_title_max_lines=3,
                promotion_layout_id=layouts.long_title.id,
            ),
            # Historical stress fixtures remain readable on the published
            # master but are not composition inputs for an authored template.
            "title_stress_corpus": [],
            "semantic_elements": [*_company_semantic_elements(contract), *markers],
            "digest": None,
        }
    )
    return seal_master_contract(candidate)


def materialize_body_visual_contract(
    contract: DeckMasterContract,
    body_visual: Any,
    *,
    style_selected: bool,
    style_id: str | None = None,
    merge_policy_version: str = _BODY_GRAMMAR_MERGE_POLICY_VERSION,
) -> DeckMasterContract:
    """Return one sealed contract; master geometry/assets remain byte-for-byte owned."""

    if merge_policy_version in {
        _COMPANY_POLICY_MERGE_POLICY_VERSION,
        _FURNITURE_OVERLAY_MERGE_POLICY_VERSION,
    }:
        raise ValueError("company policy composition requires frozen snapshots")

    if not style_selected:
        return contract
    colors = contract.tokens.colors.model_copy(
        update={
            "surface": str(_value(body_visual, "panel_fill")),
            "text_secondary": str(_value(body_visual, "text_color")),
            "accent": str(_value(body_visual, "accent")),
            "divider": str(_value(body_visual, "panel_border_color")),
            "warning": str(_value(body_visual, "warning")),
        }
    )
    body = contract.semantic_styles.body
    body_font_pt = _density_font_pt(
        body.font_pt,
        str(_value(body_visual, "density")),
    )
    body = body.model_copy(
        update={
            "font_pt": body_font_pt,
            "min_font_pt": min(body.min_font_pt, body_font_pt),
        }
    )
    existing = [
        element
        for element in contract.semantic_elements
        if not _is_composed_role(element.content.role)
    ]
    grammar_marker = (
        [
            SemanticMasterElement(
                id="element:authored-body-grammar",
                type="text",
                scope=CommonElementScope(kind="common"),
                bounds=Rect(x=0, y=0, width=1, height=1),
                rotation=0,
                z_index=-100_000,
                visibility="hidden",
                editable_by_user=False,
                editableByAI=False,
                locked=True,
                style=SemanticElementStyle(),
                content=SemanticElementContent(
                    role=f"{BODY_GRAMMAR_ROLE_PREFIX}{style_id}"
                ),
            )
        ]
        if merge_policy_version == _BODY_GRAMMAR_MERGE_POLICY_VERSION
        and style_id
        else []
    )
    candidate = contract.model_copy(
        update={
            "tokens": contract.tokens.model_copy(
                # Font manifests are master-owned export assets.  The style's
                # requested families remain pinned as an allowed direction,
                # while the effective deck uses the master's legal family.
                update={"colors": colors}
            ),
            "semantic_styles": contract.semantic_styles.model_copy(
                update={"body": body}
            ),
            "semantic_elements": [
                *existing,
                *_body_panel_elements(
                    contract,
                    body_visual,
                    visible_layers=merge_policy_version
                    != _LEGACY_MERGE_POLICY_VERSION,
                ),
                *grammar_marker,
            ],
            "digest": None,
        }
    )
    return seal_master_contract(candidate)


def materialize_authored_composition(
    contract: DeckMasterContract,
    composition_pin: Any,
) -> DeckMasterContract:
    """Replay a persisted composition pin without consulting mutable style YAML."""

    master_digest = _value(composition_pin, "master_digest")
    if contract.digest != master_digest:
        raise ValueError("authored composition master digest mismatch")
    style_selected = _value(composition_pin, "style_digest") is not None
    merge_policy_version = _value(composition_pin, "merge_policy_version")
    if merge_policy_version not in {
        _LEGACY_MERGE_POLICY_VERSION,
        _VISIBLE_BODY_LAYER_MERGE_POLICY_VERSION,
        _BODY_GRAMMAR_MERGE_POLICY_VERSION,
        _COMPANY_POLICY_MERGE_POLICY_VERSION,
        _FURNITURE_OVERLAY_MERGE_POLICY_VERSION,
    }:
        raise ValueError("authored composition merge policy is unsupported")
    if merge_policy_version in {
        _COMPANY_POLICY_MERGE_POLICY_VERSION,
        _FURNITURE_OVERLAY_MERGE_POLICY_VERSION,
    }:
        policy = _value(composition_pin, "consistency_policy")
        template = _value(composition_pin, "authored_template")
        if isinstance(policy, dict):
            policy = ConsistencyPolicySnapshot.model_validate(policy)
        if isinstance(template, dict):
            template = AuthoredTemplateSnapshot.model_validate(template)
        if policy is None or template is None:
            raise ValueError("authored company composition snapshot missing")
        effective = _materialize_company_policy_contract(
            contract,
            _value(composition_pin, "body_visual"),
            policy=policy,
            template=template,
        )
    else:
        effective = materialize_body_visual_contract(
            contract,
            _value(composition_pin, "body_visual"),
            style_selected=style_selected,
            style_id=_value(composition_pin, "style_id"),
            merge_policy_version=merge_policy_version,
        )
    expected = _value(composition_pin, "effective_contract_digest")
    if (
        effective.digest != expected
        and merge_policy_version != _COMPANY_POLICY_MERGE_POLICY_VERSION
    ):
        raise ValueError("authored composition effective contract digest mismatch")
    return effective


def resolve_authored_composition(
    contract: DeckMasterContract,
    *,
    style: AuthoredStyle | None,
    visual_direction: Any | None = None,
    metadata: DeckMetadataSnapshot | None = None,
) -> tuple[AuthoredCompositionPin, DeckMasterContract]:
    """Resolve and cache one immutable master/style composition at generation start."""

    if contract.digest is None:
        raise ValueError("authored composition requires a sealed master")
    body_visual = _body_visual_snapshot(contract, style, visual_direction)
    policy = build_consistency_policy_snapshot(contract)
    if metadata is not None:
        policy = seal_consistency_policy_snapshot(
            source_master_digest=policy.source_master_digest,
            content_rect=policy.content_rect,
            reserved_zones=policy.reserved_zones,
            role_policies=policy.role_policies,
            metadata=metadata,
            review_required_element_ids=policy.review_required_element_ids,
        )
    template = _authored_template_snapshot(contract, policy, style)
    effective = _materialize_company_policy_contract(
        contract,
        body_visual,
        policy=policy,
        template=template,
    )
    if effective.digest is None:
        raise ValueError("authored effective contract must be sealed")
    pin = compose_authored_style(
        master_digest=contract.digest,
        style_digest=_style_source_digest(style) if style is not None else None,
        style_id=style.id if style is not None else None,
        body_visual=body_visual,
        consistency_policy=policy,
        authored_template=template,
        effective_contract_digest=effective.digest,
        merge_policy_version=_COMPANY_POLICY_MERGE_POLICY_VERSION,
    )
    return pin, effective


__all__ = [
    "build_ai_edit_zone_template_design_context",
    "materialize_authored_composition",
    "materialize_body_visual_contract",
    "build_consistency_policy_snapshot",
    "resolve_authored_composition",
]
