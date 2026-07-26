"""Deterministic, fail-closed native PPTX compiler for Template V2.

This module intentionally starts with a small native subset.  It does not
replace the general HTML exporter yet; instead it provides a versioned OOXML
boundary whose output can be promoted only after the remaining Template V2
elements have native equivalents.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from io import BytesIO
import json
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.presentation import Presentation as PresentationType
from pptx.slide import Slide
from pptx.util import Pt

from templates.v2.generation import build_generated_slide
from templates.v2.models.elements import (
    Container,
    Font,
    Group,
    SlideElement,
    Text,
    TextList,
)
from templates.v2.models.layouts import SlideLayout

TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION = "presenton.template-v2-native-pptx/v1"
TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME = "presenton-template-v2-native-ooxml"
TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION = "1"
TEMPLATE_V2_CANVAS_WIDTH_PX = 1280
TEMPLATE_V2_CANVAS_HEIGHT_PX = 720
_EMU_PER_PX = Decimal("9525")
_FIXED_PACKAGE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_FIXED_CORE_TIMESTAMP = datetime(2000, 1, 1, tzinfo=timezone.utc)


class TemplateV2NativePptxCompileError(ValueError):
    """Stable, fail-closed compiler error."""

    def __init__(self, code: str, *, path: str):
        self.code = code
        self.path = path
        super().__init__(f"{code}: {path}")


@dataclass(frozen=True, slots=True)
class NativePptxCompilation:
    """Canonical structural result and its byte-identical PPTX package."""

    pptx: bytes
    manifest: dict[str, Any]
    structural_sha256: str
    package_sha256: str


def compile_template_v2_pptx(
    slides: Sequence[tuple[SlideLayout, Mapping[str, Any]]],
) -> NativePptxCompilation:
    """Compile validated Template V2 slides into deterministic native OOXML.

    Version 1 supports containers, text, text lists and groups.  Unsupported
    nodes or style capabilities raise a stable error rather than degrading to
    an image or silently dropping presentation semantics.
    """

    if not slides:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_empty",
            path="slides",
        )

    presentation = Presentation()
    presentation.slide_width = _emu(TEMPLATE_V2_CANVAS_WIDTH_PX)
    presentation.slide_height = _emu(TEMPLATE_V2_CANVAS_HEIGHT_PX)
    _set_stable_core_properties(presentation)

    layout_ids: list[str] = []
    for slide_index, (layout, raw_content) in enumerate(slides):
        generated = build_generated_slide(layout, dict(raw_content))
        content = deepcopy(generated.content)
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        layout_ids.append(layout.id)
        for component_index, component in enumerate(layout.components):
            component_path = f"slides.{slide_index}.components.{component_index}"
            component_content = content.get(component.id, {})
            if not isinstance(component_content, Mapping):
                raise TemplateV2NativePptxCompileError(
                    "template_v2_native_pptx_content_scope_invalid",
                    path=f"{component_path}.content",
                )
            for element_index, element in enumerate(component.elements):
                _compile_element(
                    slide,
                    element,
                    content=component_content,
                    offset_x=component.position.x,
                    offset_y=component.position.y,
                    path=f"{component_path}.elements.{element_index}",
                    shape_name=f"{component.id}/{_element_name(element, element_index)}",
                )

    raw = BytesIO()
    presentation.save(raw)
    package = _normalize_pptx_package(raw.getvalue())
    manifest = inspect_native_pptx_structure(package, layout_ids=layout_ids)
    canonical_manifest = _canonical_json(manifest)
    return NativePptxCompilation(
        pptx=package,
        manifest=manifest,
        structural_sha256=sha256(canonical_manifest).hexdigest(),
        package_sha256=sha256(package).hexdigest(),
    )


def inspect_native_pptx_structure(
    pptx: bytes,
    *,
    layout_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a canonical, provenance-free structural manifest."""

    presentation = Presentation(BytesIO(pptx))
    if layout_ids is not None and len(layout_ids) != len(presentation.slides):
        raise ValueError("layout_ids must match the PPTX slide count")
    slides: list[dict[str, Any]] = []
    for slide_index, slide in enumerate(presentation.slides):
        shapes: list[dict[str, Any]] = []
        for shape in slide.shapes:
            shape_type = MSO_SHAPE_TYPE(shape.shape_type).name.lower()
            entry: dict[str, Any] = {
                "name": shape.name,
                "type": shape_type,
                "x": int(shape.left),
                "y": int(shape.top),
                "width": int(shape.width),
                "height": int(shape.height),
                "rotation": float(shape.rotation or 0),
            }
            if shape.has_text_frame:
                entry["text"] = shape.text
                entry["text_frame"] = {
                    "margin_left": int(shape.text_frame.margin_left),
                    "margin_top": int(shape.text_frame.margin_top),
                    "margin_right": int(shape.text_frame.margin_right),
                    "margin_bottom": int(shape.text_frame.margin_bottom),
                    "vertical_anchor": (
                        shape.text_frame.vertical_anchor.name.lower()
                        if shape.text_frame.vertical_anchor is not None
                        else None
                    ),
                    "word_wrap": shape.text_frame.word_wrap,
                }
                entry["paragraphs"] = [
                    {
                        "text": paragraph.text,
                        "alignment": (
                            paragraph.alignment.name.lower()
                            if paragraph.alignment is not None
                            else None
                        ),
                        "runs": [
                            {
                                "text": run.text,
                                "font": _inspect_run_font(run.font),
                            }
                            for run in paragraph.runs
                        ],
                    }
                    for paragraph in shape.text_frame.paragraphs
                ]
            entry["fill"] = _inspect_fill(shape.fill)
            entry["line"] = _inspect_line(shape.line)
            shapes.append(entry)
        slides.append(
            {
                "index": slide_index,
                "layout_id": (
                    layout_ids[slide_index] if layout_ids is not None else None
                ),
                "shapes": shapes,
            }
        )
    return {
        "schema_version": TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION,
        "compiler": {
            "name": TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME,
            "version": TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION,
        },
        "canvas": {
            "width_px": TEMPLATE_V2_CANVAS_WIDTH_PX,
            "height_px": TEMPLATE_V2_CANVAS_HEIGHT_PX,
            "width_emu": int(presentation.slide_width),
            "height_emu": int(presentation.slide_height),
        },
        "slides": slides,
    }


def _compile_element(
    slide: Slide,
    element: SlideElement,
    *,
    content: Mapping[str, Any],
    offset_x: float,
    offset_y: float,
    path: str,
    shape_name: str,
) -> None:
    if isinstance(element, Text):
        _compile_text(
            slide,
            element,
            content=content,
            offset_x=offset_x,
            offset_y=offset_y,
            path=path,
            shape_name=shape_name,
        )
        return
    if isinstance(element, TextList):
        _compile_text_list(
            slide,
            element,
            content=content,
            offset_x=offset_x,
            offset_y=offset_y,
            path=path,
            shape_name=shape_name,
        )
        return
    if isinstance(element, Container):
        _compile_container(
            slide,
            element,
            content=content,
            offset_x=offset_x,
            offset_y=offset_y,
            path=path,
            shape_name=shape_name,
        )
        return
    if isinstance(element, Group):
        nested_content = content.get(element.name, content)
        if not isinstance(nested_content, Mapping):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_scope_invalid",
                path=f"{path}.content",
            )
        nested_x = offset_x + (element.position.x if element.position else 0)
        nested_y = offset_y + (element.position.y if element.position else 0)
        for index, child in enumerate(element.children):
            _compile_element(
                slide,
                child,
                content=nested_content,
                offset_x=nested_x,
                offset_y=nested_y,
                path=f"{path}.children.{index}",
                shape_name=f"{shape_name}/{_element_name(child, index)}",
            )
        return
    raise TemplateV2NativePptxCompileError(
        "template_v2_native_pptx_element_unsupported",
        path=f"{path}.type={element.type}",
    )


def _compile_text(
    slide: Slide,
    element: Text,
    *,
    content: Mapping[str, Any],
    offset_x: float,
    offset_y: float,
    path: str,
    shape_name: str,
) -> None:
    _require_box(element, path=path)
    _reject_text_style_loss(element, path=path)
    shape = slide.shapes.add_textbox(
        _emu(offset_x + element.position.x),
        _emu(offset_y + element.position.y),
        _emu(element.size.width),
        _emu(element.size.height),
    )
    shape.name = shape_name
    shape.rotation = element.rotation or 0
    _apply_shape_fill_and_line(shape, element.fill, element.stroke, path=path)
    _apply_text_frame_box(shape.text_frame, element.alignment, None)

    if element.decorative:
        run_specs = [(run.text, run.font) for run in element.runs]
    else:
        value = content.get(element.name)
        if not isinstance(value, str):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_value_invalid",
                path=f"{path}.content.{element.name}",
            )
        fallback = element.runs[0].font if element.runs else None
        run_specs = [(value, fallback)]

    paragraph = shape.text_frame.paragraphs[0]
    _apply_paragraph_alignment(paragraph, element.alignment)
    for text, run_font in run_specs:
        run = paragraph.add_run()
        run.text = text
        _apply_font(run.font, _merge_font(element.font, run_font), path=path)


def _compile_text_list(
    slide: Slide,
    element: TextList,
    *,
    content: Mapping[str, Any],
    offset_x: float,
    offset_y: float,
    path: str,
    shape_name: str,
) -> None:
    _require_box(element, path=path)
    if element.rotation not in (None, 0):
        rotation = element.rotation
    else:
        rotation = 0
    shape = slide.shapes.add_textbox(
        _emu(offset_x + element.position.x),
        _emu(offset_y + element.position.y),
        _emu(element.size.width),
        _emu(element.size.height),
    )
    shape.name = shape_name
    shape.rotation = rotation
    shape.fill.background()
    shape.line.fill.background()
    _apply_text_frame_box(shape.text_frame, None, None)

    if element.decorative:
        items = [[(run.text, run.font) for run in item] for item in element.items]
    else:
        values = content.get(element.name)
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_content_value_invalid",
                path=f"{path}.content.{element.name}",
            )
        fallback = (
            element.items[0][0].font
            if element.items and element.items[0]
            else None
        )
        items = [[(value, fallback)] for value in values]

    for item_index, item in enumerate(items):
        paragraph = (
            shape.text_frame.paragraphs[0]
            if item_index == 0
            else shape.text_frame.add_paragraph()
        )
        paragraph.level = 0
        if element.marker is not None and element.marker.value != "none":
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_marker_unsupported",
                path=f"{path}.marker",
            )
        for text, run_font in item:
            run = paragraph.add_run()
            run.text = text
            _apply_font(run.font, _merge_font(element.font, run_font), path=path)


def _compile_container(
    slide: Slide,
    element: Container,
    *,
    content: Mapping[str, Any],
    offset_x: float,
    offset_y: float,
    path: str,
    shape_name: str,
) -> None:
    _require_box(element, path=path)
    if element.alignment is not None:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_container_alignment_unsupported",
            path=f"{path}.alignment",
        )
    if element.padding is not None and any(
        value != 0
        for value in (
            element.padding.top,
            element.padding.right,
            element.padding.bottom,
            element.padding.left,
        )
    ):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_container_padding_unsupported",
            path=f"{path}.padding",
        )
    if element.child is not None and element.rotation not in (None, 0):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_nested_rotation_unsupported",
            path=f"{path}.rotation",
        )
    if element.shadow is not None:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_shadow_unsupported",
            path=f"{path}.shadow",
        )
    rounded = False
    if element.border_radius is not None:
        radii = {
            element.border_radius.tl,
            element.border_radius.tr,
            element.border_radius.bl,
            element.border_radius.br,
        }
        if len(radii) != 1:
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_border_radius_unsupported",
                path=f"{path}.border_radius",
            )
        rounded = next(iter(radii)) > 0
    shape = slide.shapes.add_shape(
        (
            MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE
            if rounded
            else MSO_AUTO_SHAPE_TYPE.RECTANGLE
        ),
        _emu(offset_x + element.position.x),
        _emu(offset_y + element.position.y),
        _emu(element.size.width),
        _emu(element.size.height),
    )
    shape.name = shape_name
    shape.rotation = element.rotation or 0
    _apply_shape_fill_and_line(shape, element.fill, element.stroke, path=path)

    if element.child is not None:
        _compile_element(
            slide,
            element.child,
            content=content,
            offset_x=offset_x + element.position.x,
            offset_y=offset_y + element.position.y,
            path=f"{path}.child",
            shape_name=f"{shape_name}/{_element_name(element.child, 0)}",
        )


def _apply_shape_fill_and_line(shape, fill, stroke, *, path: str) -> None:
    if fill is None:
        shape.fill.background()
    else:
        _reject_opacity(fill.opacity, path=f"{path}.fill.opacity")
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(fill.color, path=f"{path}.fill.color")
    if stroke is None:
        shape.line.fill.background()
    else:
        _reject_opacity(stroke.opacity, path=f"{path}.stroke.opacity")
        if stroke.dash is not None:
            raise TemplateV2NativePptxCompileError(
                "template_v2_native_pptx_dash_unsupported",
                path=f"{path}.stroke.dash",
            )
        shape.line.color.rgb = _rgb(stroke.color, path=f"{path}.stroke.color")
        shape.line.width = _emu(stroke.width)


def _apply_text_frame_box(text_frame, alignment, padding) -> None:
    text_frame.clear()
    text_frame.word_wrap = True
    text_frame.vertical_anchor = _vertical_anchor(alignment)
    if padding is not None:
        text_frame.margin_top = _emu(padding.top)
        text_frame.margin_right = _emu(padding.right)
        text_frame.margin_bottom = _emu(padding.bottom)
        text_frame.margin_left = _emu(padding.left)
    else:
        text_frame.margin_top = 0
        text_frame.margin_right = 0
        text_frame.margin_bottom = 0
        text_frame.margin_left = 0


def _apply_paragraph_alignment(paragraph, alignment) -> None:
    if alignment is None or alignment.horizontal is None:
        return
    paragraph.alignment = {
        "left": PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
    }[alignment.horizontal.value]


def _apply_font(target, font: Font | None, *, path: str) -> None:
    if font is None:
        return
    if font.letter_spacing not in (None, 0):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_letter_spacing_unsupported",
            path=f"{path}.font.letter_spacing",
        )
    if font.line_height is not None:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_line_height_unsupported",
            path=f"{path}.font.line_height",
        )
    if font.ellipsis:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_ellipsis_unsupported",
            path=f"{path}.font.ellipsis",
        )
    _reject_opacity(font.opacity, path=f"{path}.font.opacity")
    if font.size is not None:
        target.size = Pt(font.size)
    if font.family is not None:
        target.name = font.family
    if font.color is not None:
        target.color.rgb = _rgb(font.color, path=f"{path}.font.color")
    if font.bold is not None:
        target.bold = font.bold
    if font.italic is not None:
        target.italic = font.italic
    if font.underline is not None:
        target.underline = font.underline


def _merge_font(base: Font | None, override: Font | None) -> Font | None:
    if base is None:
        return override.model_copy(deep=True) if override is not None else None
    if override is None:
        return base.model_copy(deep=True)
    values = base.model_dump()
    values.update(
        {
            key: value
            for key, value in override.model_dump().items()
            if value is not None
        }
    )
    return Font.model_validate(values)


def _reject_text_style_loss(element: Text, *, path: str) -> None:
    if element.shadow is not None:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_shadow_unsupported",
            path=f"{path}.shadow",
        )


def _require_box(element, *, path: str) -> None:
    if element.position is None or element.size is None:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_geometry_required",
            path=path,
        )
    if element.size.width <= 0 or element.size.height <= 0:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_geometry_invalid",
            path=path,
        )


def _element_name(element: SlideElement, index: int) -> str:
    value = getattr(element, "name", None)
    return value if isinstance(value, str) and value else f"{element.type}-{index}"


def _vertical_anchor(alignment) -> MSO_ANCHOR:
    if alignment is None or alignment.vertical is None:
        return MSO_ANCHOR.TOP
    return {
        "top": MSO_ANCHOR.TOP,
        "middle": MSO_ANCHOR.MIDDLE,
        "bottom": MSO_ANCHOR.BOTTOM,
    }[alignment.vertical.value]


def _reject_opacity(value: float | None, *, path: str) -> None:
    if value not in (None, 1, 1.0):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_opacity_unsupported",
            path=path,
        )


def _rgb(value: str, *, path: str) -> RGBColor:
    normalized = value.strip().removeprefix("#")
    if len(normalized) == 3:
        normalized = "".join(character * 2 for character in normalized)
    if len(normalized) != 6:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_color_invalid",
            path=path,
        )
    try:
        return RGBColor.from_string(normalized.upper())
    except ValueError as error:
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_pptx_color_invalid",
            path=path,
        ) from error


def _emu(value: float | int) -> int:
    return int(
        (Decimal(str(value)) * _EMU_PER_PX).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )


def _set_stable_core_properties(presentation: PresentationType) -> None:
    core = presentation.core_properties
    core.title = "Template V2 native compilation"
    core.subject = TEMPLATE_V2_NATIVE_PPTX_SCHEMA_VERSION
    core.author = TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME
    core.last_modified_by = TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME
    core.comments = (
        f"{TEMPLATE_V2_NATIVE_PPTX_COMPILER_NAME}/"
        f"{TEMPLATE_V2_NATIVE_PPTX_COMPILER_VERSION}"
    )
    core.created = _FIXED_CORE_TIMESTAMP
    core.modified = _FIXED_CORE_TIMESTAMP
    core.revision = 1


def _normalize_pptx_package(value: bytes) -> bytes:
    source = BytesIO(value)
    target = BytesIO()
    with ZipFile(source, "r") as input_zip, ZipFile(
        target,
        "w",
        compression=ZIP_DEFLATED,
        compresslevel=9,
    ) as output_zip:
        for name in sorted(input_zip.namelist()):
            info = ZipInfo(name, date_time=_FIXED_PACKAGE_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            output_zip.writestr(info, input_zip.read(name), compress_type=ZIP_DEFLATED)
    return target.getvalue()


def _inspect_run_font(font) -> dict[str, Any]:
    color = None
    try:
        if font.color.type is not None and font.color.rgb is not None:
            color = str(font.color.rgb)
    except (AttributeError, TypeError):
        color = None
    return {
        "name": font.name,
        "size_pt": float(font.size.pt) if font.size is not None else None,
        "color": color,
        "bold": font.bold,
        "italic": font.italic,
        "underline": font.underline,
    }


def _inspect_fill(fill) -> dict[str, Any] | None:
    if fill.type is None:
        return None
    color = None
    try:
        if fill.fore_color.rgb is not None:
            color = str(fill.fore_color.rgb)
    except (AttributeError, TypeError):
        color = None
    return {"type": fill.type.name.lower(), "color": color}


def _inspect_line(line) -> dict[str, Any] | None:
    if line.fill.type is None:
        return None
    color = None
    try:
        if line.color.rgb is not None:
            color = str(line.color.rgb)
    except (AttributeError, TypeError):
        color = None
    return {
        "type": line.fill.type.name.lower(),
        "color": color,
        "width": int(line.width or 0),
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
