"""`build_runtime_slide_layouts` against payloads measured from the real runtime.

Every layout below was copied verbatim out of the bundled export runtime's
`pptx-to-json` output (runtime v0.4.2) for synthesised probe decks, so these pin
what the converter actually emits rather than what upstream documents. No binary
fixture is committed: the converter's JSON *is* the input to the unit under test.
"""

from __future__ import annotations

import pytest

from templates.v2.models.layouts import RawSlideLayouts, SlideLayouts
from templates.v2.pptx.runtime_layouts import build_runtime_slide_layouts


IMAGE_URL = (
    "http://localhost:5000/app_data/pptx-to-json/rich-probe-001/images/"
    "image1-3b042331b164.png"
)


def _styled_text_layout() -> dict:
    """Per-run bold / italic / size / colour, which the in-repo OOXML parser drops."""
    return {
        "id": "slide_1",
        "description": "Full slide layout converted from PPTX slide 1.",
        "elements": [
            {
                "type": "text",
                "position": {"x": 144.0, "y": 408.01},
                "size": {"width": 672.02, "height": 184.0},
                "font": {"size": 42.67, "family": "Calibri", "color": "#BFBFBF"},
                "alignment": {"horizontal": "center"},
                "runs": [
                    {
                        "text": "plain ",
                        "font": {
                            "size": 42.67,
                            "family": "Calibri",
                            "color": "#BFBFBF",
                        },
                    },
                    {
                        "text": "BOLD ",
                        "font": {
                            "size": 42.67,
                            "family": "Calibri",
                            "color": "#BFBFBF",
                            "bold": True,
                        },
                    },
                    {
                        "text": "italic ",
                        "font": {
                            "size": 42.67,
                            "family": "Calibri",
                            "color": "#BFBFBF",
                            "italic": True,
                        },
                    },
                    {
                        "text": "red24",
                        "font": {
                            "size": 32.0,
                            "family": "Calibri",
                            "color": "#C02030",
                        },
                    },
                ],
                "decorative": True,
                "name": "subtitle_2",
                "max_length": 23,
                "min_length": 12,
            }
        ],
    }


def _image_layout() -> dict:
    return {
        "id": "slide_2",
        "description": "Full slide layout converted from PPTX slide 2.",
        "elements": [
            {
                "type": "image",
                "position": {"x": 144.0, "y": 115.2},
                "size": {"width": 576.01, "height": 324.01},
                "rotation": 0.0,
                "data": IMAGE_URL,
                "fit": "fill",
                "decorative": True,
                "name": "picture_1",
                "is_icon": False,
            }
        ],
    }


def _vector_layout() -> dict:
    return {
        "id": "slide_5",
        "description": "Full slide layout converted from PPTX slide 5.",
        "elements": [
            {
                "type": "vector",
                "shape": "polygon",
                "points": [
                    {"x": 192.0, "y": 192.0},
                    {"x": 576.01, "y": 192.0},
                    {"x": 576.01, "y": 384.0},
                    {"x": 192.0, "y": 384.0},
                ],
                "closed": True,
                "corner_radii": [28.8, 28.8, 28.8, 28.8],
                "fill": {"color": "#2E86C1"},
            },
            {
                "type": "text",
                "position": {"x": 192.0, "y": 192.0},
                "size": {"width": 384.01, "height": 192.0},
                "font": {"size": 24.0, "family": "Calibri", "color": "#000000"},
                "alignment": {"horizontal": "left", "vertical": "middle"},
                "runs": [
                    {
                        "text": "rounded shape",
                        "font": {
                            "size": 24.0,
                            "family": "Calibri",
                            "color": "#000000",
                        },
                    }
                ],
                "decorative": True,
                "name": "rounded_rectangle_1",
                "max_length": 13,
                "min_length": 7,
            },
        ],
    }


def _ellipse_layout() -> dict:
    """The converter encodes an oval as an 8-point smooth curve tagged `ellipse`.

    The fork's `Vector` model *and* its render plan both require an ellipse to be a
    two-point bounding pair, so this payload is genuinely unrepresentable here.
    """
    return {
        "id": "slide_1",
        "description": "Full slide layout converted from PPTX slide 1.",
        "elements": [
            {
                "type": "vector",
                "shape": "ellipse",
                "points": [
                    {"x": 384.01, "y": 192.0},
                    {"x": 341.83, "y": 259.88},
                    {"x": 240.0, "y": 288.0},
                    {"x": 138.18, "y": 259.88},
                    {"x": 96.0, "y": 192.0},
                    {"x": 138.18, "y": 124.12},
                    {"x": 240.0, "y": 96.0},
                    {"x": 341.83, "y": 124.12},
                ],
                "closed": True,
                "curve": {"type": "smooth", "tension": 1.0, "segments": 8},
                "fill": {"color": "#22AA66"},
                "stroke": {"color": "#112233", "width": 5.33},
            }
        ],
    }


def _only_element(result, layout_index: int, element_index: int = 0):
    layout = result.layouts.layouts[layout_index]
    return layout.components[element_index].elements[0]


def test_text_element_keeps_per_run_bold_italic_size_and_colour():
    result = build_runtime_slide_layouts([_styled_text_layout()])

    runs = _only_element(result, 0).runs
    assert [run.text for run in runs] == ["plain ", "BOLD ", "italic ", "red24"]
    assert [run.font.bold for run in runs] == [None, True, None, None]
    assert [run.font.italic for run in runs] == [None, None, True, None]
    assert [run.font.size for run in runs] == [42.67, 42.67, 42.67, 32.0]
    assert [run.font.color for run in runs] == [
        "#BFBFBF",
        "#BFBFBF",
        "#BFBFBF",
        "#C02030",
    ]


def test_image_element_survives_with_its_runtime_data_url_untouched():
    result = build_runtime_slide_layouts([_image_layout()])

    image = _only_element(result, 0)
    assert image.type == "image"
    # Relocating and rewriting asset references is owned elsewhere; this
    # conversion must hand the URL over exactly as the runtime emitted it.
    assert image.data == IMAGE_URL
    assert image.fit.value == "fill"
    assert image.position.x == 144.0 and image.position.y == 115.2


def test_vector_keeps_points_fill_and_corner_radii():
    result = build_runtime_slide_layouts([_vector_layout()])

    vector = _only_element(result, 0)
    assert vector.type == "vector"
    assert [(point.x, point.y) for point in vector.points] == [
        (192.0, 192.0),
        (576.01, 192.0),
        (576.01, 384.0),
        (192.0, 384.0),
    ]
    assert vector.fill.color == "#2E86C1"
    assert vector.corner_radii == [28.8, 28.8, 28.8, 28.8]


def test_output_revalidates_against_the_strict_layout_models():
    result = build_runtime_slide_layouts(
        [_styled_text_layout(), _image_layout(), _vector_layout()]
    )

    # Round-tripping the JSON dump proves the persisted payload is acceptable to
    # the fork's own `extra="forbid"` models, not merely to the objects we built.
    revalidated = SlideLayouts.model_validate(result.layouts.model_dump(mode="json"))
    assert [layout.id for layout in revalidated.layouts] == [
        "slide_1",
        "slide_2",
        "slide_5",
    ]
    assert RawSlideLayouts.model_validate(
        result.raw_layouts.model_dump(mode="json")
    ).layouts[2].elements[0].type == "vector"

    vector_layout = revalidated.layouts[2]
    assert [component.id for component in vector_layout.components] == [
        "slide_5_component_1",
        "slide_5_component_2",
    ]
    for component in vector_layout.components:
        assert 10 <= len(component.description) <= 300
        # A zero component origin keeps the runtime's absolute slide coordinates,
        # because the render plan adds this offset to every wrapped element.
        assert (component.position.x, component.position.y) == (0.0, 0.0)
    assert 10 <= len(vector_layout.description) <= 300
    assert "1x text" in vector_layout.description
    assert "rounded shape" not in vector_layout.description


def test_unrepresentable_ellipse_vector_fails_closed():
    with pytest.raises(ValueError) as error:
        build_runtime_slide_layouts([_vector_layout(), _ellipse_layout()])

    message = str(error.value)
    assert message.startswith("runtime_element_not_representable:slide_1:0:")
    assert "ellipse vectors require exactly two bounding points" in message
