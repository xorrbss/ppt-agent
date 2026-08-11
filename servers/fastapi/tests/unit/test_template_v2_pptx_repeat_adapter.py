from __future__ import annotations

import pytest

from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.repeat_adapter import (
    TemplateV2PptxRepeatAdapterError,
    prepare_repeatable_pptx_slide,
)


def _positioned_layout() -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": "positioned-repeat",
            "description": "Irregular positioned callouts for native export.",
            "components": [
                {
                    "id": "callouts",
                    "description": "Three positioned editable callout anchors.",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "group",
                            "name": f"callout_{index}",
                            "position": {"x": index * 180, "y": (index % 2) * 70},
                            "size": {"width": 160, "height": 60},
                            "children": [
                                {
                                    "type": "text",
                                    "position": {"x": 0, "y": 0},
                                    "size": {"width": 160, "height": 60},
                                    "runs": [{"text": "Original"}],
                                    "decorative": False,
                                    "name": f"item_title_{index}",
                                    "min_length": 1,
                                    "max_length": 40,
                                }
                            ],
                        }
                        for index in range(1, 4)
                    ],
                }
            ],
        }
    )


def test_native_clone_adapter_rejects_structural_cardinality_change() -> None:
    with pytest.raises(
        TemplateV2PptxRepeatAdapterError,
        match="native_clone_repeat_cardinality_unsupported",
    ):
        prepare_repeatable_pptx_slide(
            _positioned_layout(),
            {
                "callouts": {
                    "items": [
                        {"item_title": "Center"},
                        {"item_title": "Upper"},
                    ]
                }
            },
            preservation_mode="native_clone",
        )


def test_native_clone_adapter_maps_fixed_anchors_to_exact_ooxml_slot_names() -> None:
    prepared = prepare_repeatable_pptx_slide(
        _positioned_layout(),
        {
            "callouts": {
                "items": [
                    {"item_title": "One"},
                    {"item_title": "Two"},
                    {"item_title": "Three"},
                ]
            }
        },
        preservation_mode="native_clone",
    )

    assert prepared.materialization == "native-clone-fixed"
    assert prepared.content["callouts"] == {
        "callout_1": {"item_title_1": "One"},
        "callout_2": {"item_title_2": "Two"},
        "callout_3": {"item_title_3": "Three"},
    }
