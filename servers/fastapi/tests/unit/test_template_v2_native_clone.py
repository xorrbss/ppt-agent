from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.native_clone import compile_template_v2_native_clone
from templates.v2.pptx.native_compiler import TemplateV2NativePptxCompileError


def _source_deck() -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    title = slide.shapes.add_textbox(
        Inches(0.6), Inches(0.4), Inches(7.2), Inches(0.8)
    )
    title.name = "Hero Title"
    title.text = "Original title"

    table = slide.shapes.add_table(
        2, 2, Inches(0.6), Inches(1.6), Inches(7.2), Inches(2.0)
    )
    table.name = "Data Table"
    table.table.cell(0, 0).text = "Old A"
    table.table.cell(0, 1).text = "Old B"
    table.table.cell(1, 0).text = "Old 1"
    table.table.cell(1, 1).text = "Old 2"

    list_box = slide.shapes.add_textbox(
        Inches(0.6), Inches(4.0), Inches(7.2), Inches(1.8)
    )
    list_box.name = "Key Points"
    list_box.text_frame.clear()
    for index, value in enumerate(("Old point A", "Old point B", "Old point C")):
        paragraph = (
            list_box.text_frame.paragraphs[0]
            if index == 0
            else list_box.text_frame.add_paragraph()
        )
        paragraph.level = 0
        run = paragraph.add_run()
        run.text = value
        run.font.bold = True

    image_stream = BytesIO()
    Image.new("RGB", (8, 8), "#1167B1").save(image_stream, format="PNG")
    image_stream.seek(0)
    picture = slide.shapes.add_picture(
        image_stream, Inches(8.5), Inches(0.5), Inches(0.8), Inches(0.8)
    )
    picture.name = "Brand Mark"

    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _nested_source_deck() -> bytes:
    presentation = Presentation(BytesIO(_source_deck()))
    slide = presentation.slides[0]
    for index, name in enumerate(("Card One", "Card Two")):
        shape = slide.shapes.add_textbox(
            Inches(0.6 + index * 4.0),
            Inches(6.0),
            Inches(3.5),
            Inches(0.7),
        )
        shape.name = name
        shape.text = f"Original {name}"
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _nested_layout(container_type: str) -> SlideLayout:
    container = {
        "type": container_type,
        "name": "cards",
        "position": {"x": 0, "y": 0},
        "size": {"width": 800, "height": 100},
        "min_children": 2,
        "max_children": 2,
        "children": [
            {
                "type": "text",
                "name": f"card_{name}",
                "position": {"x": index * 400, "y": 0},
                "size": {"width": 350, "height": 70},
                "runs": [{"text": f"Original {name}"}],
                "decorative": False,
                "min_length": 1,
                "max_length": 80,
            }
            for index, name in enumerate(("one", "two"))
        ],
        **(
            {"direction": "row", "wrap": False}
            if container_type == "flex"
            else {"columns": 2, "rows": 1}
        ),
    }
    return SlideLayout.model_validate(
        {
            "id": f"nested-{container_type}",
            "description": f"Nested editable {container_type} content",
            "components": [
                {
                    "id": "content",
                    "description": "Nested editable cards",
                    "position": {"x": 0, "y": 0},
                    "elements": [container],
                }
            ],
        }
    )


def _layout() -> SlideLayout:
    cell = {"runs": [{"text": "Original"}]}
    return SlideLayout.model_validate(
        {
            "id": "source-slide-one",
            "description": "Native source title and table layout",
            "components": [
                {
                    "id": "content",
                    "description": "Editable source title and table",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "text",
                            "position": {"x": 1, "y": 1},
                            "size": {"width": 8, "height": 1},
                            "runs": [{"text": "Original title"}],
                            "decorative": False,
                            "name": "hero_title",
                            "min_length": 1,
                            "max_length": 80,
                        },
                        {
                            "type": "table",
                            "position": {"x": 1, "y": 2},
                            "size": {"width": 8, "height": 2},
                            "columns": [cell, cell],
                            "rows": [[cell, cell]],
                            "decorative": False,
                            "name": "data_table",
                            "min_columns": 2,
                            "max_columns": 2,
                            "min_rows": 1,
                            "max_rows": 1,
                        },
                        {
                            "type": "text-list",
                            "position": {"x": 1, "y": 4},
                            "size": {"width": 8, "height": 2},
                            "items": [
                                [{"text": "Old point A"}],
                                [{"text": "Old point B"}],
                                [{"text": "Old point C"}],
                            ],
                            "decorative": False,
                            "name": "key_points",
                            "marker": "bullet",
                            "min_items": 0,
                            "max_items": 3,
                            "min_item_length": 0,
                            "max_item_length": 100,
                        },
                    ],
                }
            ],
        }
    )


def _content(title: str, suffix: str) -> dict:
    return {
        "content": {
            "hero_title": title,
            "data_table": {
                "columns": ["Metric", "Value"],
                "rows": [["Preserved", suffix]],
            },
            "key_points": [
                "Native text",
                f"Value {suffix}",
                "Relationships preserved",
            ],
        }
    }


def _package_parts(payload: bytes) -> dict[str, bytes]:
    with ZipFile(BytesIO(payload)) as package:
        return {name: package.read(name) for name in package.namelist()}


_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _named_shape(payload: bytes, name: str) -> ET.Element:
    root = ET.fromstring(_package_parts(payload)["ppt/slides/slide1.xml"])
    for shape in root.iter(f"{{{_P_NS}}}sp"):
        properties = shape.find(f".//{{{_P_NS}}}cNvPr")
        if properties is not None and properties.get("name") == name:
            return shape
    raise AssertionError(f"shape not found: {name}")


def _text_list_style_fragments(payload: bytes) -> tuple[bytes | None, ...]:
    shape = _named_shape(payload, "Key Points")
    text_body = shape.find(f"{{{_P_NS}}}txBody")
    assert text_body is not None
    fragments: list[bytes | None] = []
    for tag in (f"{{{_A_NS}}}bodyPr", f"{{{_A_NS}}}lstStyle"):
        node = text_body.find(tag)
        fragments.append(ET.tostring(node) if node is not None else None)
    for paragraph in text_body.findall(f"{{{_A_NS}}}p"):
        paragraph_properties = paragraph.find(f"{{{_A_NS}}}pPr")
        fragments.append(
            ET.tostring(paragraph_properties)
            if paragraph_properties is not None
            else None
        )
        for run in paragraph.findall(f"{{{_A_NS}}}r"):
            run_properties = run.find(f"{{{_A_NS}}}rPr")
            fragments.append(
                ET.tostring(run_properties) if run_properties is not None else None
            )
    return tuple(fragments)


def _source_deck_with_complex_text_list_paragraph() -> bytes:
    parts = _package_parts(_source_deck())
    root = ET.fromstring(parts["ppt/slides/slide1.xml"])
    for shape in root.iter(f"{{{_P_NS}}}sp"):
        properties = shape.find(f".//{{{_P_NS}}}cNvPr")
        if properties is None or properties.get("name") != "Key Points":
            continue
        paragraph = shape.find(f"{{{_P_NS}}}txBody/{{{_A_NS}}}p")
        assert paragraph is not None
        ET.SubElement(paragraph, f"{{{_A_NS}}}br")
        break
    parts["ppt/slides/slide1.xml"] = ET.tostring(
        root, encoding="utf-8", xml_declaration=True
    )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        for part_name, part in parts.items():
            package.writestr(part_name, part)
    return output.getvalue()


def _source_deck_with_inherited_text_list_hyperlink() -> bytes:
    parts = _package_parts(_source_deck())
    root = ET.fromstring(parts["ppt/slides/slide1.xml"])
    shape = next(
        candidate
        for candidate in root.iter(f"{{{_P_NS}}}sp")
        if (properties := candidate.find(f".//{{{_P_NS}}}cNvPr")) is not None
        and properties.get("name") == "Key Points"
    )
    paragraph = shape.find(f"{{{_P_NS}}}txBody/{{{_A_NS}}}p")
    assert paragraph is not None
    paragraph_properties = paragraph.find(f"{{{_A_NS}}}pPr")
    if paragraph_properties is None:
        paragraph_properties = ET.Element(f"{{{_A_NS}}}pPr")
        paragraph.insert(0, paragraph_properties)
    default_run_properties = ET.SubElement(
        paragraph_properties, f"{{{_A_NS}}}defRPr"
    )
    hyperlink = ET.SubElement(default_run_properties, f"{{{_A_NS}}}hlinkClick")
    hyperlink.set(f"{{{_R_NS}}}id", "rId99")
    parts["ppt/slides/slide1.xml"] = ET.tostring(
        root, encoding="utf-8", xml_declaration=True
    )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        for part_name, part in parts.items():
            package.writestr(part_name, part)
    return output.getvalue()


def test_native_clone_preserves_master_layout_theme_relationships_and_media():
    source = _source_deck()
    compilation = compile_template_v2_native_clone(
        source,
        [(_layout(), _content("Updated title", "42"), "ppt/slides/slide1.xml")],
    )

    original_parts = _package_parts(source)
    cloned_parts = _package_parts(compilation.pptx)
    assert cloned_parts["[Content_Types].xml"] == original_parts[
        "[Content_Types].xml"
    ]
    preserved_prefixes = (
        "ppt/slideMasters/",
        "ppt/slideLayouts/",
        "ppt/theme/",
        "ppt/media/",
    )
    preserved_names = {
        name
        for name in original_parts
        if name.startswith(preserved_prefixes)
    }
    assert preserved_names
    for name in preserved_names:
        assert cloned_parts[name] == original_parts[name]

    source_slide_rels = "ppt/slides/_rels/slide1.xml.rels"
    assert cloned_parts[source_slide_rels] == original_parts[source_slide_rels]

    presentation = Presentation(BytesIO(compilation.pptx))
    slide = presentation.slides[0]
    assert slide.shapes[0].text == "Updated title"
    assert slide.shapes[1].table.cell(0, 0).text == "Metric"
    assert slide.shapes[1].table.cell(1, 1).text == "42"
    list_shape = next(shape for shape in slide.shapes if shape.name == "Key Points")
    assert [paragraph.text for paragraph in list_shape.text_frame.paragraphs] == [
        "Native text",
        "Value 42",
        "Relationships preserved",
    ]
    assert all(
        paragraph.runs[0].font.bold
        for paragraph in list_shape.text_frame.paragraphs
    )
    assert any(shape.shape_type == 13 for shape in slide.shapes)
    assert _text_list_style_fragments(compilation.pptx) == (
        _text_list_style_fragments(source)
    )
    assert compilation.manifest["patched_slots"] == [
        ["hero_title", "data_table", "key_points"]
    ]


def test_native_clone_reuses_slide_as_a_relationship_preserving_copy():
    source = _source_deck()
    compilation = compile_template_v2_native_clone(
        source,
        [
            (_layout(), _content("First", "1"), "ppt/slides/slide1.xml"),
            (_layout(), _content("Second", "2"), "ppt/slides/slide1.xml"),
        ],
    )
    parts = _package_parts(compilation.pptx)

    assert b'<Types xmlns="' in parts["[Content_Types].xml"]
    assert b"ns0:" not in parts["[Content_Types].xml"]
    assert b'/ppt/slides/slide2.xml' in parts["[Content_Types].xml"]
    assert parts["ppt/slides/_rels/slide2.xml.rels"] == parts[
        "ppt/slides/_rels/slide1.xml.rels"
    ]
    presentation = Presentation(BytesIO(compilation.pptx))
    assert len(presentation.slides) == 2
    assert presentation.slides[0].shapes[0].text == "First"
    assert presentation.slides[1].shapes[0].text == "Second"
    assert len(presentation.slides[0].shapes) == len(presentation.slides[1].shapes)
    first_list = next(
        shape for shape in presentation.slides[0].shapes if shape.name == "Key Points"
    )
    second_list = next(
        shape for shape in presentation.slides[1].shapes if shape.name == "Key Points"
    )
    assert first_list.text_frame.paragraphs[1].text == "Value 1"
    assert second_list.text_frame.paragraphs[1].text == "Value 2"


def test_native_clone_text_list_remains_editable_after_powerpoint_round_trip():
    compilation = compile_template_v2_native_clone(
        _source_deck(),
        [(_layout(), _content("Title", "7"), "ppt/slides/slide1.xml")],
    )
    presentation = Presentation(BytesIO(compilation.pptx))
    list_shape = next(
        shape for shape in presentation.slides[0].shapes if shape.name == "Key Points"
    )
    list_shape.text_frame.paragraphs[0].text = "Edited after reopen"

    saved = BytesIO()
    presentation.save(saved)
    reopened = Presentation(BytesIO(saved.getvalue()))
    reopened_list = next(
        shape for shape in reopened.slides[0].shapes if shape.name == "Key Points"
    )
    assert reopened_list.text_frame.paragraphs[0].text == "Edited after reopen"
    assert reopened_list.text_frame.paragraphs[1].text == "Value 7"
    assert reopened_list.text_frame.paragraphs[2].text == "Relationships preserved"


@pytest.mark.parametrize(
    "values",
    (["One", "Two"], ["One", "Two", "Three", "Four"]),
)
def test_native_clone_text_list_shape_change_is_fail_closed(values: list[str]):
    content = _content("Title", "7")
    content["content"]["key_points"] = values
    layout_payload = _layout().model_dump(mode="json")
    layout_payload["components"][0]["elements"][2]["max_items"] = 4
    layout = SlideLayout.model_validate(layout_payload)

    with pytest.raises(TemplateV2NativePptxCompileError) as rejected:
        compile_template_v2_native_clone(
            _source_deck(),
            [(layout, content, "ppt/slides/slide1.xml")],
        )

    assert rejected.value.code == (
        "template_v2_native_pptx_text_list_shape_change_unsupported"
    )


def test_native_clone_text_list_complex_paragraph_is_fail_closed():
    with pytest.raises(TemplateV2NativePptxCompileError) as rejected:
        compile_template_v2_native_clone(
            _source_deck_with_complex_text_list_paragraph(),
            [(_layout(), _content("Title", "7"), "ppt/slides/slide1.xml")],
        )

    assert rejected.value.code == (
        "template_v2_native_clone_text_list_paragraph_unsupported"
    )


def test_native_clone_text_list_inherited_hyperlink_is_fail_closed():
    with pytest.raises(TemplateV2NativePptxCompileError) as rejected:
        compile_template_v2_native_clone(
            _source_deck_with_inherited_text_list_hyperlink(),
            [(_layout(), _content("Title", "7"), "ppt/slides/slide1.xml")],
        )

    assert rejected.value.code == (
        "template_v2_native_clone_text_list_paragraph_unsupported"
    )


def test_native_clone_text_list_line_break_request_is_fail_closed():
    content = _content("Title", "7")
    content["content"]["key_points"][0] = "Line one\nLine two"

    with pytest.raises(TemplateV2NativePptxCompileError) as rejected:
        compile_template_v2_native_clone(
            _source_deck(),
            [(_layout(), content, "ppt/slides/slide1.xml")],
        )

    assert rejected.value.code == (
        "template_v2_native_clone_text_list_paragraph_unsupported"
    )


@pytest.mark.parametrize(
    ("element", "content", "expected_code"),
    (
        (
            {
                "type": "image",
                "position": {"x": 1, "y": 1},
                "size": {"width": 2, "height": 2},
                "data": "/static/images/replaceable_template_image.png",
                "decorative": False,
                "name": "hero_image",
                "is_icon": False,
            },
            {"hero_image": {"image_prompt": "Replacement image"}},
            "template_v2_native_clone_image_relationship_replacement_unsupported",
        ),
        (
            {
                "type": "chart",
                "position": {"x": 1, "y": 1},
                "size": {"width": 600, "height": 400},
                "chart_type": "bar",
                "decorative": False,
                "name": "sales_chart",
            },
            {
                "sales_chart": {
                    "chart_type": "bar",
                    "categories": ["A"],
                    "series": [{"name": "Sales", "values": [1]}],
                }
            },
            "template_v2_native_clone_chart_relationship_replacement_unsupported",
        ),
    ),
)
def test_native_clone_relationship_backed_edits_are_rejected_before_export(
    element: dict,
    content: dict,
    expected_code: str,
):
    """Rel-backed edits cannot preserve media/chart parts with a text-only patch."""

    layout = SlideLayout.model_validate(
        {
            "id": "unsupported-relationship-edit",
            "description": "Relationship-backed native edit must fail closed",
            "components": [
                {
                    "id": "content",
                    "description": "Unsupported relationship-backed content",
                    "position": {"x": 0, "y": 0},
                    "elements": [element],
                }
            ],
        }
    )

    with pytest.raises(TemplateV2NativePptxCompileError) as rejected:
        compile_template_v2_native_clone(
            _source_deck(),
            [(layout, {"content": content}, "ppt/slides/slide1.xml")],
        )

    assert rejected.value.code == expected_code
    assert "relationship_replacement_unsupported" in rejected.value.code


@pytest.mark.parametrize("container_type", ["flex", "grid"])
def test_native_clone_patches_every_editable_flex_and_grid_descendant(
    container_type: str,
) -> None:
    compiled = compile_template_v2_native_clone(
        _nested_source_deck(),
        [
            (
                _nested_layout(container_type),
                {
                    "content": {
                        "cards": {"card_one": "Alpha", "card_two": "Beta"}
                    }
                },
                "ppt/slides/slide1.xml",
            )
        ],
    )

    first = _named_shape(compiled.pptx, "Card One")
    second = _named_shape(compiled.pptx, "Card Two")
    assert "Alpha" in "".join(first.itertext())
    assert "Beta" in "".join(second.itertext())
