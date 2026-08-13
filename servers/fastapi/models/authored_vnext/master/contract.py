"""Strict DeckMasterContract v1 schema."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

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
    visibility: Literal["always", "exceptCover", "coverOnly"] = "always"
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
    slots: list[LayoutSlot] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def _slots_are_disjoint_and_writable(self) -> "MasterLayout":
        if len({slot.id for slot in self.slots}) != len(self.slots):
            raise ValueError("layout slot IDs must be unique")
        if len({slot.placeholder_index for slot in self.slots}) != len(self.slots):
            raise ValueError("placeholderIndex values must be unique per layout")
        if any(slot.ownership.locked or not slot.ownership.model_writable for slot in self.slots):
            raise ValueError("layout slots must be unlocked and model-writable")
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
    fit_policy: TitleFitPolicy
    title_stress_corpus: list[TitleStressCase] = Field(min_length=4, max_length=100)
    digest: Digest | None = None

    @model_validator(mode="after")
    def _stress_and_promotion_contract(self) -> "DeckMasterContract":
        categories = {case.category for case in self.title_stress_corpus}
        required = {"ko", "en", "mixed", "numericParenthetical"}
        if categories != required:
            raise ValueError("titleStressCorpus must cover all four categories")
        if self.fit_policy.promotion_layout_id != self.layouts.long_title.id:
            raise ValueError("promotionLayoutId must identify longTitle")
        stress_ids = [case.id for case in self.title_stress_corpus]
        if len(set(stress_ids)) != len(stress_ids):
            raise ValueError("titleStressCorpus IDs must be unique")
        for case in self.title_stress_corpus:
            normalized_text = " ".join(case.text.split())
            normalized_lines = " ".join(" ".join(case.expected_lines).split())
            if normalized_text != normalized_lines:
                raise ValueError("stress text must equal expectedLines")
        if self.tokens.fonts.east_asian != self.font_manifest.primary:
            raise ValueError("eastAsian font token must match fontManifest.primary")
        if self.tokens.fonts.fallbacks != self.font_manifest.fallbacks:
            raise ValueError("font token fallbacks must match fontManifest.fallbacks")
        if self.font_manifest.windows_fallback != self.font_manifest.primary:
            raise ValueError("windowsFallback must match primary")
        logo_reference = self.brand_assets.logo
        if (logo_reference is None) != (self.furniture.logo.asset_id is None):
            raise ValueError("logo asset reference and furniture binding must match")
        if (
            logo_reference is not None
            and self.furniture.logo.asset_id != logo_reference.asset_id
        ):
            raise ValueError("logo asset reference and furniture binding must match")
        scripts = self.font_manifest.theme_scripts
        if {
            scripts.latin,
            scripts.hang,
            scripts.jpan,
            scripts.hans,
            scripts.hant,
        } != {self.font_manifest.primary}:
            raise ValueError("all theme scripts must resolve to the primary font")
        return self


class VisualDirection(StrictCamelModel):
    """Deck-specific expression; intentionally cannot contain master geometry."""

    tone: list[str] = Field(default_factory=list, max_length=12)
    imagery: str | None = Field(default=None, max_length=1000)
    composition: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)
