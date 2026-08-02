from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.native_clone import compile_template_v2_native_clone


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
        }
    }


def _package_parts(payload: bytes) -> dict[str, bytes]:
    with ZipFile(BytesIO(payload)) as package:
        return {name: package.read(name) for name in package.namelist()}


def test_native_clone_preserves_master_layout_theme_relationships_and_media():
    source = _source_deck()
    compilation = compile_template_v2_native_clone(
        source,
        [(_layout(), _content("Updated title", "42"), "ppt/slides/slide1.xml")],
    )

    original_parts = _package_parts(source)
    cloned_parts = _package_parts(compilation.pptx)
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
    assert any(shape.shape_type == 13 for shape in slide.shapes)


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

    assert parts["ppt/slides/_rels/slide2.xml.rels"] == parts[
        "ppt/slides/_rels/slide1.xml.rels"
    ]
    presentation = Presentation(BytesIO(compilation.pptx))
    assert len(presentation.slides) == 2
    assert presentation.slides[0].shapes[0].text == "First"
    assert presentation.slides[1].shapes[0].text == "Second"
    assert len(presentation.slides[0].shapes) == len(presentation.slides[1].shapes)
