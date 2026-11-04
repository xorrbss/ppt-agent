"""Immutable server-owned evidence for SVG template frames.

Extraction is deliberately distinct from preservation approval. These models
are persisted artifacts, never fields trusted directly from generation clients.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from models.document_canvas import DocumentCanvasMetadata
from models.svg_template_glyph import SvgTemplateFixedGlyphPreview


class FrameModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FrameRect(FrameModel):
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)


class FrameIssue(FrameModel):
    reason_code: str
    severity: Literal["review-required", "blocked"] = "review-required"
    source_part: str = ""
    shape_id: str = ""


class SvgTemplateAsset(FrameModel):
    asset_id: str
    digest: str
    media_type: Literal["image/png", "image/jpeg"]
    data_base64: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SvgTemplateFontAsset(FrameModel):
    asset_id: str
    digest: str
    family: str
    face: str
    weight: int = Field(ge=100, le=900)
    data_base64: str
    collection_index: int = Field(default=0, ge=0, le=255)
    family_aliases: tuple[str, ...] = ()


class SvgTemplateFontManifest(FrameModel):
    schema_version: Literal["svg-template-font-manifest/v1"] = "svg-template-font-manifest/v1"
    assets: tuple[SvgTemplateFontAsset, ...] = ()
    required_families: tuple[str, ...] = ()
    digest: str


class SvgTemplateNativeFixedText(FrameModel):
    schema_version: Literal["svg-template-native-fixed-text/v1"] = "svg-template-native-fixed-text/v1"
    source_digest: str
    source_xml_digest: str
    source_canvas_digest: str
    svg_text_id: str
    svg_text_digest: str
    text_digest: str
    font_asset_ids: tuple[str, ...]
    font_digests: tuple[str, ...]
    sp_pr_xml: str = Field(max_length=16384)
    tx_body_xml: str = Field(max_length=32768)
    digest: str


class SvgTemplateFontSubstitution(FrameModel):
    """A source font replaced by a server font the person chose at registration."""
    source_family: str = Field(min_length=1, max_length=128)
    target_family: str = Field(min_length=1, max_length=128)
    target_weight: int = Field(ge=100, le=900)


class SvgTemplateUnavailableFont(FrameModel):
    """A source face the server cannot use, with the substitute review suggests."""
    source_family: str = Field(min_length=1, max_length=128)
    source_weight: int = Field(ge=100, le=900)
    suggested_family: str = Field(min_length=1, max_length=128)
    suggested_weight: int = Field(ge=100, le=900)


class SvgTemplateRasterPreservation(FrameModel):
    """Fixed decoration kept as an image cut from a source render (never text)."""
    schema_version: Literal["svg-template-raster-preservation/v1"] = "svg-template-raster-preservation/v1"
    renderer: str
    renderer_version: str
    scale: float = Field(gt=0)
    approximate: bool
    shape_refs: tuple[str, ...] = Field(min_length=1)
    reasons: tuple[str, ...] = Field(min_length=1)


#: Dynamic text that replaces a page's own header text (title, path) as a whole.
HEADER_TEXT_KEYS = frozenset({"title", "breadcrumb", "breadcrumb_number"})
#: Header slots a page may leave empty (the element is then not drawn).
#: The cover belongs to no chapter, so its chapter tab may be empty too.
OPTIONAL_HEADER_KEYS = frozenset({"breadcrumb", "breadcrumb_number", "chapter_number", "chapter_title"})


class SvgTemplateFrameElement(FrameModel):
    element_id: str
    source_part: str
    source_shape_id: str
    source_xml_digest: str
    kind: Literal["rect", "line", "image", "text"]
    layer: Literal["background", "foreground"] = "foreground"
    bounds: FrameRect
    svg: str
    anchor_x: Literal["left", "right", "center"] = "left"
    anchor_y: Literal["top", "bottom", "center"] = "top"
    size_policy: Literal["physical-fixed", "aspect-locked", "span-between-anchors"] = "physical-fixed"
    dynamic_key: Literal["title", "page_number", "section_number", "chapter_number", "chapter_title",
                         "breadcrumb", "breadcrumb_number"] | None = None
    font_asset_ids: tuple[str, ...] = ()
    text_region: FrameRect | None = None
    text_align: Literal["l", "ctr", "r"] = "l"
    native_fixed_text: SvgTemplateNativeFixedText | None = None
    raster_preservation: SvgTemplateRasterPreservation | None = None

    @model_serializer(mode="wrap")
    def serialize_native_layout(self, handler):
        value = handler(self)
        # Optional evidence is omitted when absent, so earlier digests hold.
        if self.native_fixed_text is None:
            value.pop("native_fixed_text", None)
        if self.raster_preservation is None:
            value.pop("raster_preservation", None)
        return value


class SvgTemplateBodyEvidence(FrameModel):
    source_page: int = Field(ge=0)
    source_part: str
    source_digest: str
    body_shape_ids: tuple[str, ...]
    body_bounds: tuple[FrameRect, ...]
    body_kinds: tuple[str, ...]
    # Geometry is design evidence, never a list of replacement slots.
    usage: Literal["design-evidence-only"] = "design-evidence-only"


_HEX = r"^(none|#[0-9A-F]{6})$"


class SvgTemplateTextStyle(FrameModel):
    """Text of a body component, or one level of the body type-size ladder."""
    size_px: float = Field(gt=0, le=400)
    weight: int = Field(ge=100, le=900)
    colors: tuple[str, ...] = Field(min_length=1, max_length=2)
    #: Share of the measured body characters (ladder levels only).
    share: float | None = Field(default=None, ge=0, le=1)


class SvgTemplateComponent(FrameModel):
    """How one recurring body component of the template looks. Style, never a position."""
    kind: Literal["number_chip", "label", "bar", "step", "card", "panel", "accent_bar", "tile", "divider"]
    shape: Literal["rect", "rounded", "pill", "circle", "chevron", "pentagon", "line"]
    fill: str = Field(pattern=_HEX)
    stroke: str = Field(pattern=_HEX)
    stroke_width_px: float = Field(ge=0, le=100)
    radius_px: float = Field(ge=0, le=1000)
    #: Typical size, given only for components whose size is part of their look (chips, tiles, marks).
    width_px: float | None = Field(default=None, gt=0)
    height_px: float | None = Field(default=None, gt=0)
    text: SvgTemplateTextStyle | None = None
    count: int = Field(ge=1)
    pages: int = Field(ge=1)


class SvgTemplateTableStyle(FrameModel):
    header_fill: str = Field(pattern=_HEX)
    header_text: SvgTemplateTextStyle | None = None
    body_fill: str = Field(pattern=_HEX)
    band_fill: str | None = Field(default=None, pattern=_HEX)
    #: The first column's fill when it differs from the body cells (a row-header column).
    row_header_fill: str | None = Field(default=None, pattern=_HEX)
    rule_color: str | None = Field(default=None, pattern=_HEX)
    body_text: SvgTemplateTextStyle | None = None
    count: int = Field(ge=1)
    pages: int = Field(ge=1)


class SvgTemplateComponentStyle(FrameModel):
    """The template's body component vocabulary (``services/svg_templates/component_style.py``)."""
    schema_version: Literal["svg-template-component-style/v1"] = "svg-template-component-style/v1"
    pages_sampled: int = Field(ge=1)
    type_ladder: tuple[SvgTemplateTextStyle, ...] = Field(default=(), max_length=6)
    components: tuple[SvgTemplateComponent, ...] = Field(default=(), max_length=16)
    tables: tuple[SvgTemplateTableStyle, ...] = Field(default=(), max_length=2)


#: Content kinds of a body page, shared by source pages and outline pages
#: (``services/svg_templates/body_composition.py``).
BODY_CONTENT_KINDS = ("table", "chart", "flow", "hierarchy", "kpi", "cards", "picture", "list")
_ContentKind = Literal["table", "chart", "flow", "hierarchy", "kpi", "cards", "picture", "list"]


class SvgTemplateExemplarText(FrameModel):
    """A text's look and amount; never its characters."""
    size_px: float = Field(gt=0, le=400)
    weight: int = Field(ge=100, le=900)
    color: str = Field(pattern=_HEX)
    #: Paragraphs and characters of the source text (counts only).
    paragraphs: int = Field(ge=1, le=200)
    chars: int = Field(ge=1, le=20000)
    #: The source text is a bare item number ("01", "2.", "A"): drawn as a number, not words.
    numbered: bool = False
    #: The source text starts with an item number followed by words (a heading "01 ...").
    lead_number: bool = False


class SvgTemplateExemplarItem(FrameModel):
    """One element of a source body page: kind, box relative to the body region, paint, text amount."""
    kind: Literal["text", "bar", "label", "chip", "step", "card", "panel", "mark", "tile", "divider",
                  "line", "arrow", "shape", "table", "chart", "picture", "icon"]
    x: int
    y: int
    w: int = Field(ge=0)
    h: int = Field(ge=0)
    fill: str | None = Field(default=None, pattern=_HEX)
    stroke: str | None = Field(default=None, pattern=_HEX)
    text: SvgTemplateExemplarText | None = None
    #: Tables: rows, columns, header fill, the header and body cell text.
    rows: int | None = Field(default=None, ge=1, le=200)
    cols: int | None = Field(default=None, ge=1, le=60)
    header_fill: str | None = Field(default=None, pattern=_HEX)
    header_text: SvgTemplateExemplarText | None = None
    #: Charts: the plot type.
    chart: Literal["bar", "line", "pie", "doughnut", "area", "scatter", "radar", "other"] | None = None

    @model_serializer(mode="wrap")
    def serialize_compact(self, handler):
        # An outline has many items; fields that do not apply are left out.
        return {key: value for key, value in handler(self).items() if value is not None}


class SvgTemplateBodyExemplar(FrameModel):
    """A source body page as a structural outline: what it contains and how it fills the body."""
    source_page: int = Field(ge=0)
    region_width: int = Field(gt=0)
    region_height: int = Field(gt=0)
    kinds: tuple[_ContentKind, ...] = Field(min_length=1, max_length=8)
    sections: int = Field(ge=0, le=20)
    text_items: int = Field(ge=0)
    items: tuple[SvgTemplateExemplarItem, ...] = Field(min_length=1, max_length=160)


class SvgTemplateCompositionGrammar(FrameModel):
    """How the template's body pages are composed, as distributions over its content pages."""
    pages_sampled: int = Field(ge=1)
    #: Share of pages that open the body with a key-message bar across the content width.
    key_message_bar: float = Field(ge=0, le=1)
    #: Share of pages whose sections start with a numbered sub-heading (a chip or "01 ...").
    numbered_headings: float = Field(ge=0, le=1)
    #: (lower quartile, median, upper quartile) per page.
    sections: tuple[int, int, int]
    content_kinds_per_page: tuple[int, int, int]
    text_items: tuple[int, int, int]
    #: Median share of the body area under text, tables, charts, figures and step shapes.
    ink_coverage: float = Field(ge=0, le=1)
    #: Median lowest element bottom, as a share of the body height.
    bottom_reach: float = Field(ge=0, le=1.5)
    #: Share of pages showing each content kind, most common first.
    kind_shares: tuple[tuple[_ContentKind, float], ...] = ()
    #: Character-weighted body text sizes (90th percentile, median, 10th percentile), px.
    text_sizes: tuple[float, float, float]


class SvgTemplateBodyComposition(FrameModel):
    """Page composition grammar and exemplar outlines (``services/svg_templates/body_composition.py``)."""
    schema_version: Literal["svg-template-body-composition/v1"] = "svg-template-body-composition/v1"
    grammar: SvgTemplateCompositionGrammar
    exemplars: tuple[SvgTemplateBodyExemplar, ...] = Field(default=(), max_length=12)


class SvgTemplateFrame(FrameModel):
    schema_version: Literal["svg-template-frame/v1", "svg-template-frame/v2", "svg-template-frame/v3"] = "svg-template-frame/v1"
    frame_id: str
    source_page: int = Field(ge=0)
    source_digest: str
    source_canvas: DocumentCanvasMetadata
    role: Literal["cover", "section", "body"] = "body"
    elements: tuple[SvgTemplateFrameElement, ...]
    body_region: FrameRect
    forbidden_regions: tuple[FrameRect, ...] = ()
    dynamic_fields: tuple[str, ...] = ()
    font_manifest: SvgTemplateFontManifest
    issues: tuple[FrameIssue, ...] = ()
    digest: str
    fixed_glyph_preview: SvgTemplateFixedGlyphPreview | None = None

    @model_serializer(mode="wrap")
    def serialize_glyph_authority(self, handler):
        value = handler(self)
        if self.fixed_glyph_preview is None:
            value.pop("fixed_glyph_preview", None)
        return value

    @model_validator(mode="after")
    def validate_native_version(self):
        for element in self.elements:
            owned = element.native_fixed_text is not None
            if (self.schema_version.endswith("/v1") and owned) or (
                self.schema_version.endswith(("/v2", "/v3")) and owned != (element.kind == "text")
            ):
                raise ValueError("svg_template_native_layout_version_mismatch")
        if (self.schema_version.endswith("/v3")) != (self.fixed_glyph_preview is not None):
            raise ValueError("svg_template_glyph_version_mismatch")
        if self.fixed_glyph_preview is not None and (self.dynamic_fields or any(e.dynamic_key for e in self.elements)):
            raise ValueError("svg_template_glyph_dynamic_text_unsupported")
        return self


class SvgTemplatePackage(FrameModel):
    schema_version: Literal["svg-template-package/v1", "svg-template-package/v2", "svg-template-package/v3"] = "svg-template-package/v1"
    compiler_version: Literal["ooxml-frame/1", "ooxml-frame/2", "ooxml-frame/3"] = "ooxml-frame/1"
    source_digest: str
    source_canvas: DocumentCanvasMetadata
    frames: tuple[SvgTemplateFrame, ...]
    body_evidence: tuple[SvgTemplateBodyEvidence, ...]
    assets: tuple[SvgTemplateAsset, ...] = ()
    font_manifest: SvgTemplateFontManifest
    issues: tuple[FrameIssue, ...] = ()
    registration_state: Literal["review-required", "failed"] = "review-required"
    font_substitutions: tuple[SvgTemplateFontSubstitution, ...] = ()
    unavailable_fonts: tuple[SvgTemplateUnavailableFont, ...] = ()
    component_style: SvgTemplateComponentStyle | None = None
    body_composition: SvgTemplateBodyComposition | None = None
    digest: str

    @model_validator(mode="after")
    def validate_native_version(self):
        version = self.schema_version.rsplit("/v", 1)[1]
        if self.compiler_version != "ooxml-frame/" + version or any(
            frame.schema_version != "svg-template-frame/v" + version for frame in self.frames
        ):
            raise ValueError("svg_template_native_layout_version_mismatch")
        return self

    @model_validator(mode="before")
    @classmethod
    def hydrate_font_references(cls, value):
        if isinstance(value, dict) and isinstance(value.get("font_manifest"), dict) and "frames" in value:
            from models.svg_template_codec import unpack_package_fonts
            return unpack_package_fonts(value)
        return value

    @model_serializer(mode="wrap")
    def serialize_font_references(self, handler):
        from models.svg_template_codec import pack_package_fonts
        value = handler(self)
        if not self.font_substitutions:
            value.pop("font_substitutions", None)
        if not self.unavailable_fonts:
            value.pop("unavailable_fonts", None)
        if self.component_style is None:
            value.pop("component_style", None)
        if self.body_composition is None:
            value.pop("body_composition", None)
        if "frames" in value and "font_manifest" in value:
            return pack_package_fonts(value)
        return value


class SvgTemplateFrameAdaptation(FrameModel):
    schema_version: Literal["svg-template-frame-adaptation/v1", "svg-template-frame-adaptation/v2", "svg-template-frame-adaptation/v3"] = "svg-template-frame-adaptation/v1"
    adaptation_id: str
    revision: int = 1
    source_frame_digest: str
    target_canvas: DocumentCanvasMetadata
    policy_digest: str
    frame: SvgTemplateFrame
    digest: str

    @model_validator(mode="after")
    def validate_native_version(self):
        version = self.frame.schema_version.rsplit("/v", 1)[1]
        native = version in {"2", "3"}
        if not self.schema_version.endswith("/v" + version) or (native and self.target_canvas != self.frame.source_canvas):
            raise ValueError("svg_template_native_layout_version_mismatch")
        return self
