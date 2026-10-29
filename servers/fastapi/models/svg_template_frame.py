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
    dynamic_key: Literal["title", "page_number", "section_number"] | None = None
    font_asset_ids: tuple[str, ...] = ()
    text_region: FrameRect | None = None
    text_align: Literal["l", "ctr", "r"] = "l"
    native_fixed_text: SvgTemplateNativeFixedText | None = None

    @model_serializer(mode="wrap")
    def serialize_native_layout(self, handler):
        value = handler(self)
        if self.native_fixed_text is None:
            value.pop("native_fixed_text", None)
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
