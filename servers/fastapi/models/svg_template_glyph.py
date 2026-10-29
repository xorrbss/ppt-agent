"""Closed, immutable source-PDF glyph authority; never a public request model."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]
ClipRect = tuple[float, float, float, float]
Segment = tuple[Annotated[int, Field(strict=True, ge=0, le=2)], float, float, Annotated[bool, Field(strict=True)]]
Channel = Annotated[int, Field(strict=True, ge=0, le=255)]


def glyph_authority_digest(value) -> str:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    data.pop("authority_digest", None)
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class GlyphModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always")


class FixedGlyphCharacter(GlyphModel):
    unicode: int = Field(ge=32, le=126, strict=True)
    text_offset: int = Field(ge=0, le=1999, strict=True)
    origin_pdf: Point
    font_size_pdf: float = Field(gt=0, le=400)
    matrix_pdf: Matrix
    fill_rgba: tuple[Channel, Channel, Channel, Channel]
    path_pdf: tuple[Segment, ...] = Field(max_length=10000)

    @model_validator(mode="after")
    def validate_supported(self):
        if self.matrix_pdf[:4] != (1, 0, 0, 1) or self.fill_rgba[3] != 255:
            raise ValueError("svg_template_glyph_state_unsupported")
        if any(type(v) is not int or not 0 <= v <= 255 for v in self.fill_rgba):
            raise ValueError("svg_template_glyph_paint_invalid")
        if bool(self.path_pdf) != (self.unicode != 32):
            raise ValueError("svg_template_glyph_outline_missing")
        # PDFium: LINETO=0, BEZIERTO=1, MOVETO=2. Cubics consume three points.
        cursor = 0
        while cursor < len(self.path_pdf):
            if self.path_pdf[cursor][0] != 2 or self.path_pdf[cursor][3]:
                raise ValueError("svg_template_glyph_path_invalid")
            cursor += 1
            closed = False
            while cursor < len(self.path_pdf) and self.path_pdf[cursor][0] != 2:
                kind = self.path_pdf[cursor][0]
                count = 3 if kind == 1 else 1
                group = self.path_pdf[cursor:cursor + count]
                if len(group) != count or any(v[0] != kind for v in group) or any(v[3] for v in group[:-1]):
                    raise ValueError("svg_template_glyph_path_invalid")
                cursor += count
                closed = group[-1][3]
                if closed:
                    break
            if not closed or cursor < len(self.path_pdf) and self.path_pdf[cursor][0] != 2:
                raise ValueError("svg_template_glyph_path_invalid")
        return self


class FixedGlyphTextObject(GlyphModel):
    source_part: str = Field(min_length=1, max_length=256)
    source_shape_id: str = Field(min_length=1, max_length=128)
    source_xml_digest: Digest
    svg_text_id: str = Field(min_length=1, max_length=256)
    svg_text_digest: Digest
    text_digest: Digest
    effective_clip_rects: tuple[ClipRect, ...] = Field(min_length=2, max_length=2)
    characters: tuple[FixedGlyphCharacter, ...] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_coverage(self):
        if [v.text_offset for v in self.characters] != list(range(len(self.characters))):
            raise ValueError("svg_template_glyph_character_coverage_invalid")
        text = "".join(chr(v.unicode) for v in self.characters)
        if "sha256:" + hashlib.sha256(text.encode()).hexdigest() != self.text_digest:
            raise ValueError("svg_template_glyph_text_digest_mismatch")
        for left, bottom, right, top in self.effective_clip_rects:
            if not left < right or not bottom < top:
                raise ValueError("svg_template_glyph_clip_invalid")
            for char in self.characters:
                if any(not (left < x < right and bottom < y < top) for _, x, y, _ in char.path_pdf):
                    raise ValueError("svg_template_glyph_clip_not_inert")
        return self


class SvgTemplateFixedGlyphPreview(GlyphModel):
    schema_version: Literal["svg-template-fixed-glyph-preview/v1"] = "svg-template-fixed-glyph-preview/v1"
    source_pptx_digest: Digest
    source_pdf_digest: Digest
    source_canvas_digest: Digest
    source_frame_projection_digest: Digest
    approved_font_digest: Digest
    embedded_font_digest: Digest
    capture_receipt_digest: Digest
    graphics_state_digest: Digest
    producer_version: str = Field(min_length=1, max_length=80)
    producer_build: str = Field(min_length=1, max_length=80)
    frame_id: str = Field(min_length=1, max_length=128)
    source_page: int = Field(ge=0, le=199, strict=True)
    physical_canvas_pt: Point
    source_canvas_px: tuple[int, int]
    materializer_version: Literal["svg-fixed-glyph-materializer/v1"] = "svg-fixed-glyph-materializer/v1"
    materializer_code_digest: Digest
    text_objects: tuple[FixedGlyphTextObject, ...] = Field(min_length=1, max_length=200)
    authority_digest: Digest

    @model_validator(mode="after")
    def validate_authority(self):
        if glyph_authority_digest(self) != self.authority_digest:
            raise ValueError("svg_template_glyph_authority_digest_mismatch")
        if any(v <= 0 for v in (*self.physical_canvas_pt, *self.source_canvas_px)):
            raise ValueError("svg_template_glyph_canvas_invalid")
        if len({v.svg_text_id for v in self.text_objects}) != len(self.text_objects):
            raise ValueError("svg_template_glyph_mapping_ambiguous")
        if sum(len(c.path_pdf) for t in self.text_objects for c in t.characters) > 200000:
            raise ValueError("svg_template_glyph_path_limit")
        page = (0, 0, *self.physical_canvas_pt)
        if any(v.effective_clip_rects[-1] != page for v in self.text_objects):
            raise ValueError("svg_template_glyph_page_clip_mismatch")
        return self
