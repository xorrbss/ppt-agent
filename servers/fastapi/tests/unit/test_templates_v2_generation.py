from copy import deepcopy

import pytest
from jsonschema import ValidationError

from templates.v2.generation import build_generated_slide, generate_template
from templates.v2.models.layouts import RawSlideLayouts, SlideLayout


def _text_element(name: str = "title", text: str = "Original") -> dict:
    return {
        "type": "text",
        "position": {"x": 1, "y": 2},
        "size": {"width": 8, "height": 1},
        "runs": [{"text": text}],
        "decorative": False,
        "name": name,
        "min_length": 1,
        "max_length": 80,
    }


def _raw_layout(layout_id: str) -> dict:
    return {
        "id": layout_id,
        "description": f"Raw layout {layout_id}",
        "elements": [_text_element()],
    }


def _slide_layout(layout_id: str = "title-slide") -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": layout_id,
            "description": "Native title slide layout",
            "components": [
                {
                    "id": "hero",
                    "description": "Editable title component",
                    "position": {"x": 0, "y": 0},
                    "elements": [_text_element()],
                }
            ],
        }
    )


def test_generate_template_preserves_order_and_does_not_mutate_inputs():
    layouts = RawSlideLayouts.model_validate(
        {"layouts": [_raw_layout("first"), _raw_layout("second")]}
    )
    original = layouts.model_dump()
    fonts = {"Inter": "https://fonts.invalid/inter.woff2"}
    seen: list[tuple[str, int, str]] = []

    def generator(raw_layout, index, image_url, generated_fonts):
        seen.append((raw_layout.id, index, image_url))
        raw_layout.elements[0].runs[0].text = "mutated copy"
        generated_fonts["Added"] = "copy-only"
        return _slide_layout("duplicate")

    generated = generate_template(
        layouts,
        ["image://first", "image://second"],
        fonts,
        generate_slide_layout=generator,
    )

    assert seen == [
        ("first", 0, "image://first"),
        ("second", 1, "image://second"),
    ]
    assert [layout.id for layout in generated.layouts] == ["duplicate", "duplicate_2"]
    assert layouts.model_dump() == original
    assert fonts == {"Inter": "https://fonts.invalid/inter.woff2"}


@pytest.mark.parametrize(
    ("layouts", "images", "message"),
    [
        ({"layouts": []}, [], "at least one"),
        ({"layouts": [_raw_layout("one")]}, [], "one image"),
    ],
)
def test_generate_template_rejects_incomplete_inputs(layouts, images, message):
    with pytest.raises(ValueError, match=message):
        generate_template(
            RawSlideLayouts.model_validate(layouts),
            images,
            generate_slide_layout=lambda *_args: _slide_layout(),
        )


def test_build_generated_slide_validates_content_and_preserves_full_native_ui():
    layout = _slide_layout()
    original = deepcopy(layout.model_dump(mode="json"))

    generated = build_generated_slide(layout, {"hero": {"title": "Phase 1"}})

    assert generated.layout_id == "title-slide"
    assert generated.content == {"hero": {"title": "Phase 1"}}
    assert generated.ui == original

    layout.components[0].elements[0].runs[0].text = "changed later"
    assert generated.ui == original


def test_build_generated_slide_rejects_content_outside_layout_contract():
    with pytest.raises(ValidationError):
        build_generated_slide(_slide_layout(), {"unknown": "not editable"})
