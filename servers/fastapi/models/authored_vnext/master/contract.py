"""Strict DeckMasterContract v1 schema."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, model_validator

from .base import Digest, LayoutId, MasterId, StrictCamelModel


class Rect(StrictCamelModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


class Ownership(StrictCamelModel):
    locked: bool
    model_writable: bool


class CommonElementScope(StrictCamelModel):
    kind: Literal["common"]


class LayoutElementScope(StrictCamelModel):
    kind: Literal["layout"]
    layout_id: str = Field(min_length=1, max_length=200)


class AIEditZone(StrictCamelModel):
    """Authored-template composition envelope with optional AI intent.

    Historical contracts persisted only ``id``, ``scope`` and ``bounds``.  All
    authoring fields therefore remain optional and are omitted again when they
    were absent, so those immutable revisions retain their geometry-only wire
    representation.
    """

    id: str = Field(pattern=r"^ai-zone:[a-z0-9][a-z0-9._-]*$")
    scope: CommonElementScope | LayoutElementScope
    bounds: Rect
    instruction: str | None = Field(
        default=None,
        max_length=4000,
        exclude_if=lambda value: value is None,
    )
    content_type: Literal[
        "auto", "text", "cards", "list", "table", "chart", "mixed"
    ] | None = Field(default=None, exclude_if=lambda value: value is None)
    pattern: Literal[
        "auto",
        "numberedCards",
        "bulletList",
        "comparisonTable",
        "metricChart",
        "iconCards",
    ] | None = Field(default=None, exclude_if=lambda value: value is None)
    source_policy: Literal[
        "documentOnly", "promptAndDocument", "promptOnly"
    ] | None = Field(default=None, exclude_if=lambda value: value is None)
    empty_behavior: Literal["hidden", "blank", "fallback"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    fallback_text: str | None = Field(
        default=None,
        max_length=10000,
        exclude_if=lambda value: value is None,
    )
    max_chars: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        exclude_if=lambda value: value is None,
    )
    max_items: int | None = Field(
        default=None,
        ge=1,
        le=24,
        exclude_if=lambda value: value is None,
    )
    density: Literal["sparse", "balanced", "dense"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    text_align: Literal["left", "center", "right"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    style_policy: Literal["inheritTemplate"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def _fallback_is_explicit_and_bounded(self) -> "AIEditZone":
        if self.empty_behavior == "fallback" and not (self.fallback_text or "").strip():
            raise ValueError("fallback emptyBehavior requires fallbackText")
        if self.fallback_text is not None and self.empty_behavior != "fallback":
            raise ValueError("fallbackText requires emptyBehavior=fallback")
        if (
            self.fallback_text is not None
            and self.max_chars is not None
            and len(self.fallback_text) > self.max_chars
        ):
            raise ValueError("fallbackText cannot exceed maxChars")
        return self


class ImageCrop(StrictCamelModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside_source(self) -> "ImageCrop":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("crop must stay within normalized image coordinates")
        return self


class SemanticElementStyle(StrictCamelModel):
    font_family: str | None = Field(default=None, min_length=1, max_length=200)
    font_size: float | None = Field(default=None, ge=1, le=400)
    font_weight: int | None = Field(default=None, ge=100, le=900)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    text_align: Literal["left", "center", "right", "justify"] | None = None
    vertical_align: Literal["top", "middle", "bottom"] | None = None
    fill: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke_width: float | None = Field(default=None, ge=0, le=100)
    opacity: float | None = Field(default=None, ge=0, le=1)
    shape: Literal["rect", "ellipse"] | None = None
    line_dash: list[float] | None = Field(default=None, max_length=16)


class AITextBinding(StrictCamelModel):
    instruction: str = Field(min_length=1, max_length=4000)
    example_text: str | None = Field(default=None, max_length=4000)
    empty_behavior: Literal["hidden", "blank", "fallback"]
    fallback_text: str | None = Field(default=None, max_length=10000)
    max_chars: int | None = Field(default=None, ge=1, le=10000)

    @model_validator(mode="after")
    def _fallback_is_explicit(self) -> "AITextBinding":
        if self.empty_behavior == "fallback" and not self.fallback_text:
            raise ValueError("fallback emptyBehavior requires fallbackText")
        return self


class SemanticElementContent(StrictCamelModel):
    text: str | None = Field(default=None, max_length=10000)
    asset_id: str | None = Field(
        default=None, pattern=r"^asset:[a-z0-9][a-z0-9._-]*$"
    )
    digest: Digest | None = None
    media_type: Literal["image/png", "image/jpeg", "image/svg+xml"] | None = None
    alt_text: str | None = Field(default=None, max_length=500)
    fit: Literal["contain", "cover"] | None = None
    crop: ImageCrop | None = None
    placeholder_role: Literal["title", "body", "content", "image"] | None = None
    placeholder_index: int | None = Field(default=None, ge=0, le=65535)
    role: str | None = Field(default=None, min_length=1, max_length=200)
    ai_binding: AITextBinding | None = None
    shared_group_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[a-z][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*$",
    )
    shared_override: bool | None = None

    @model_validator(mode="after")
    def _shared_logo_metadata(self) -> "SemanticElementContent":
        if self.shared_override is not None and self.shared_group_id is None:
            raise ValueError("sharedOverride requires sharedGroupId")
        if self.shared_group_id is not None and self.role != "brand-logo":
            raise ValueError("sharedGroupId is reserved for role=brand-logo")
        return self


class SemanticMasterElement(StrictCamelModel):
    id: str = Field(pattern=r"^element:[a-z0-9][a-z0-9._-]*$")
    type: Literal["text", "image", "shape", "line", "pageNumber", "placeholder"]
    scope: CommonElementScope | LayoutElementScope
    bounds: Rect
    rotation: float = Field(ge=-360, le=360)
    z_index: int = Field(ge=-100000, le=100000)
    visibility: Literal[
        "always", "exceptCover", "coverOnly", "layoutOnly", "hidden"
    ]
    editable_by_user: bool
    # Keep the public acronym stable while accepting the short-lived legacy
    # spelling that may still exist in an already-open editor tab.
    editable_by_ai: bool = Field(
        validation_alias=AliasChoices("editableByAI", "editableByAi"),
        serialization_alias="editableByAI",
    )
    locked: bool
    style: SemanticElementStyle
    content: SemanticElementContent

    @model_validator(mode="after")
    def _type_content(self) -> "SemanticMasterElement":
        if self.type == "image" and (
            self.content.asset_id is None
            or self.content.digest is None
            or self.content.media_type is None
        ):
            raise ValueError("image elements require digest-pinned assetId, digest, and mediaType")
        if self.type == "placeholder" and self.content.placeholder_role is None:
            raise ValueError("placeholder elements require content.placeholderRole")
        if self.type == "shape" and self.style.shape is None:
            raise ValueError("shape elements require style.shape")
        if self.content.role == "ai-text-zone":
            if self.type != "text" or self.content.ai_binding is None:
                raise ValueError("ai-text-zone requires a text element with content.aiBinding")
        elif self.content.ai_binding is not None:
            raise ValueError("content.aiBinding requires role=ai-text-zone")
        return self


class CanvasContract(StrictCamelModel):
    width_px: int = Field(default=1280, ge=320, le=7680)
    height_px: int = Field(default=720, ge=180, le=4320)
    locale: str = Field(default="ko-KR", min_length=2, max_length=32)
    safe_area: Rect


class AssetReference(StrictCamelModel):
    asset_id: str = Field(pattern=r"^asset:[a-z0-9][a-z0-9._-]*$")
    digest: Digest
    alt_text: str = Field(min_length=1, max_length=500)


class BrandAssets(StrictCamelModel):
    logo: AssetReference | None = None
    wordmark: AssetReference | None = None


class FontTokens(StrictCamelModel):
    latin: str = Field(min_length=1, max_length=200)
    east_asian: str = Field(min_length=1, max_length=200)
    complex_script: str = Field(min_length=1, max_length=200)
    fallbacks: list[str] = Field(min_length=1, max_length=12)


class ColorTokens(StrictCamelModel):
    background: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    surface: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_primary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_secondary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    divider: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    warning: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class DesignTokens(StrictCamelModel):
    fonts: FontTokens
    colors: ColorTokens


class ThemeScripts(StrictCamelModel):
    latin: str = Field(min_length=1)
    hang: str = Field(min_length=1)
    jpan: str = Field(min_length=1)
    hans: str = Field(min_length=1)
    hant: str = Field(min_length=1)


class FontFileManifest(StrictCamelModel):
    style: Literal["regular", "bold", "semilight"]
    path: str = Field(min_length=1)
    version: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FontManifest(StrictCamelModel):
    primary: str = Field(min_length=1)
    fallbacks: list[str] = Field(min_length=1, max_length=12)
    windows_fallback: str = Field(min_length=1)
    theme_scripts: ThemeScripts
    files: list[FontFileManifest] = Field(default_factory=list, max_length=12)
    digest: Digest | None = None


class FurnitureElement(StrictCamelModel):
    id: str = Field(pattern=r"^furniture:[a-z0-9][a-z0-9._-]*$")
    kind: Literal["logo", "text", "line", "rect", "pageNumber"]
    bounds: Rect
    ownership: Ownership
    visibility: Literal["always", "exceptCover", "coverOnly", "hidden"] = "always"
    text: str | None = Field(default=None, max_length=500)
    asset_id: str | None = Field(
        default=None,
        pattern=r"^asset:[a-z0-9][a-z0-9._-]*$",
    )
    fill: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    stroke: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class FurnitureRegistry(StrictCamelModel):
    logo: FurnitureElement
    header: FurnitureElement
    footer: FurnitureElement
    divider: FurnitureElement
    page_number: FurnitureElement

    @model_validator(mode="after")
    def _roles_and_ids_are_fixed(self) -> "FurnitureRegistry":
        expected_kinds = {
            "logo": "logo",
            "header": "text",
            "footer": "text",
            "divider": "line",
            "page_number": "pageNumber",
        }
        elements = {
            name: getattr(self, name)
            for name in expected_kinds
        }
        if any(elements[name].kind != kind for name, kind in expected_kinds.items()):
            raise ValueError("furniture role and kind must match")
        if len({element.id for element in elements.values()}) != len(elements):
            raise ValueError("furniture IDs must be unique")
        if any(
            not element.ownership.locked or element.ownership.model_writable
            for element in elements.values()
        ):
            raise ValueError("furniture must be locked and not model-writable")
        return self


class PowerPointMargins(StrictCamelModel):
    left_pt: float = Field(default=7.2, ge=0, le=72)
    right_pt: float = Field(default=7.2, ge=0, le=72)
    top_pt: float = Field(default=3.6, ge=0, le=72)
    bottom_pt: float = Field(default=3.6, ge=0, le=72)


class TextStyle(StrictCamelModel):
    font_token: Literal["latin", "eastAsian", "complexScript"]
    color_token: Literal[
        "textPrimary",
        "textSecondary",
        "accent",
        "warning",
    ]
    font_pt: float = Field(ge=8, le=96)
    min_font_pt: float = Field(ge=8, le=96)
    line_height: float = Field(ge=0.8, le=2)
    font_weight: int = Field(ge=100, le=900, multiple_of=100)
    max_lines: int = Field(ge=1, le=12)
    margins: PowerPointMargins = Field(default_factory=PowerPointMargins)

    @model_validator(mode="after")
    def _font_range(self) -> "TextStyle":
        if self.min_font_pt > self.font_pt:
            raise ValueError("minFontPt cannot exceed fontPt")
        return self


class SemanticStyles(StrictCamelModel):
    eyebrow: TextStyle
    title: TextStyle
    body: TextStyle
    note: TextStyle
    right_callout: TextStyle
    warning: TextStyle
    caption: TextStyle
    source: TextStyle


class LayoutSlot(StrictCamelModel):
    id: str = Field(pattern=r"^slot:[a-z0-9][a-z0-9._-]*$")
    role: Literal["title", "body", "content"]
    placeholder_index: int = Field(ge=0, le=255)
    bounds: Rect
    ownership: Ownership
    style: Literal["title", "body", "note", "rightCallout"]


class MasterLayout(StrictCamelModel):
    id: LayoutId
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["cover", "section", "standard", "longTitle"]
    # Legacy placeholder metadata remains readable, but authored templates own
    # content geometry. New furniture-only masters may therefore omit slots.
    slots: list[LayoutSlot] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _slot_ids_and_indices_are_unique(self) -> "MasterLayout":
        if len({slot.id for slot in self.slots}) != len(self.slots):
            raise ValueError("layout slot IDs must be unique")
        if len({slot.placeholder_index for slot in self.slots}) != len(self.slots):
            raise ValueError("placeholderIndex values must be unique per layout")
        return self


class MasterCustomLayout(StrictCamelModel):
    id: LayoutId
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["custom"]
    slots: list[LayoutSlot] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _slot_ids_and_indices_are_unique(self) -> "MasterCustomLayout":
        if len({slot.id for slot in self.slots}) != len(self.slots):
            raise ValueError("custom layout slot IDs must be unique")
        if len({slot.placeholder_index for slot in self.slots}) != len(self.slots):
            raise ValueError("placeholderIndex values must be unique per custom layout")
        return self


class LayoutRegistry(StrictCamelModel):
    cover: MasterLayout
    section: MasterLayout
    standard: MasterLayout
    long_title: MasterLayout

    @model_validator(mode="after")
    def _kinds_match_keys(self) -> "LayoutRegistry":
        layouts = {
            "cover": self.cover,
            "section": self.section,
            "standard": self.standard,
            "longTitle": self.long_title,
        }
        if any(layout.kind != kind for kind, layout in layouts.items()):
            raise ValueError("layout registry keys must match layout kinds")
        if len({layout.id for layout in layouts.values()}) != len(layouts):
            raise ValueError("cover, section, standard, and longTitle IDs must differ")
        return self


class TitleFitPolicy(StrictCamelModel):
    preferred_font_pt: float = Field(default=38, ge=16, le=72)
    minimum_font_pt: float = Field(default=28, ge=12, le=72)
    standard_max_lines: int = Field(default=2, ge=1, le=3)
    long_title_max_lines: int = Field(default=3, ge=2, le=4)
    promotion_layout_id: LayoutId
    explicit_line_breaks: Literal[True] = True
    uax14_version: Literal["17.0.0"] = "17.0.0"
    tailoring: Literal["ko-space-first-with-id-fallback-v1"] = (
        "ko-space-first-with-id-fallback-v1"
    )
    kinsoku_policy: Literal[
        "cjk-no-leading-closing-no-trailing-opening"
    ] = "cjk-no-leading-closing-no-trailing-opening"


class TitleStressCase(StrictCamelModel):
    id: str = Field(pattern=r"^stress:[a-z0-9][a-z0-9._-]*$")
    category: Literal["ko", "en", "mixed", "numericParenthetical"]
    text: str = Field(min_length=1, max_length=1000)
    expected_lines: list[str] = Field(min_length=1, max_length=3)


class DeckMasterContract(StrictCamelModel):
    contract_version: Literal["deckmaster-contract/v1"] = "deckmaster-contract/v1"
    master_id: MasterId
    semantic_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    name: str = Field(min_length=1, max_length=200)
    canvas: CanvasContract
    brand_assets: BrandAssets
    tokens: DesignTokens
    font_manifest: FontManifest
    furniture: FurnitureRegistry
    semantic_styles: SemanticStyles
    layouts: LayoutRegistry
    custom_layouts: list[MasterCustomLayout] = Field(default_factory=list, max_length=64)
    semantic_elements: list[SemanticMasterElement] = Field(default_factory=list, max_length=2048)
    ai_edit_zones: list[AIEditZone] = Field(default_factory=list, max_length=256)
    fit_policy: TitleFitPolicy
    title_stress_corpus: list[TitleStressCase] = Field(max_length=100)
    digest: Digest | None = None

    @model_validator(mode="after")
    def _layout_and_semantic_integrity(self) -> "DeckMasterContract":
        custom_layout_ids = [layout.id for layout in self.custom_layouts]
        built_in_layout_ids = {
            self.layouts.cover.id,
            self.layouts.section.id,
            self.layouts.standard.id,
            self.layouts.long_title.id,
        }
        if len(set(custom_layout_ids)) != len(custom_layout_ids):
            raise ValueError("custom layout IDs must be unique")
        if built_in_layout_ids.intersection(custom_layout_ids):
            raise ValueError("custom layout IDs must differ from built-in layout IDs")
        element_ids = [element.id for element in self.semantic_elements]
        if len(set(element_ids)) != len(element_ids):
            raise ValueError("semantic element IDs must be unique")
        zone_ids = [zone.id for zone in self.ai_edit_zones]
        if len(set(zone_ids)) != len(zone_ids):
            raise ValueError("AI edit zone IDs must be unique")
        known_layout_ids = built_in_layout_ids.union(custom_layout_ids)
        all_layouts = [
            self.layouts.cover,
            self.layouts.section,
            self.layouts.standard,
            self.layouts.long_title,
            *self.custom_layouts,
        ]
        all_slot_ids = [slot.id for layout in all_layouts for slot in layout.slots]
        if len(set(all_slot_ids)) != len(all_slot_ids):
            raise ValueError("layout slot IDs must be globally unique")
        for element in self.semantic_elements:
            if (
                isinstance(element.scope, LayoutElementScope)
                and element.scope.layout_id not in known_layout_ids
            ):
                raise ValueError("layout-scoped semantic element references unknown layout")
        for zone in self.ai_edit_zones:
            if (
                isinstance(zone.scope, LayoutElementScope)
                and zone.scope.layout_id not in known_layout_ids
            ):
                raise ValueError("layout-scoped AI edit zone references unknown layout")
            if (
                zone.bounds.right > self.canvas.width_px
                or zone.bounds.bottom > self.canvas.height_px
            ):
                raise ValueError("AI edit zone bounds must stay inside the canvas")
        shared_groups: dict[str, list[SemanticMasterElement]] = {}
        for element in self.semantic_elements:
            group_id = element.content.shared_group_id
            if group_id is None:
                continue
            if element.type != "image" or not isinstance(element.scope, LayoutElementScope):
                raise ValueError("shared logo elements must be layout-scoped images")
            shared_groups.setdefault(group_id, []).append(element)
        for members in shared_groups.values():
            layout_ids = [member.scope.layout_id for member in members]
            if len(set(layout_ids)) != len(layout_ids):
                raise ValueError("shared logo groups allow at most one element per layout")
            asset_references = {
                (
                    member.content.asset_id,
                    member.content.digest,
                    member.content.media_type,
                )
                for member in members
            }
            if len(asset_references) != 1:
                raise ValueError("shared logo groups must reference one pinned asset")
            inherited = [
                member
                for member in members
                if member.content.shared_override is not True
            ]
            if inherited:
                representative = inherited[0]
                representative_content = representative.content.model_dump(
                    exclude={"shared_group_id", "shared_override"}
                )
                for member in inherited[1:]:
                    if (
                        member.bounds != representative.bounds
                        or member.rotation != representative.rotation
                        or member.style != representative.style
                        or member.content.model_dump(
                            exclude={"shared_group_id", "shared_override"}
                        )
                        != representative_content
                    ):
                        raise ValueError("non-overridden shared logos must stay synchronized")
        logo_reference = self.brand_assets.logo
        if (logo_reference is None) != (self.furniture.logo.asset_id is None):
            raise ValueError("logo asset reference and furniture binding must match")
        if (
            logo_reference is not None
            and self.furniture.logo.asset_id != logo_reference.asset_id
        ):
            raise ValueError("logo asset reference and furniture binding must match")
        return self


class VisualDirection(StrictCamelModel):
    """Deck-specific expression; intentionally cannot contain master geometry."""

    tone: list[str] = Field(default_factory=list, max_length=12)
    imagery: str | None = Field(default=None, max_length=1000)
    composition: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)
