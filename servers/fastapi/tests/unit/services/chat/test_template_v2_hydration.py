from __future__ import annotations

import copy

import pytest

from services.chat.template_v2_hydration import (
    TemplateV2HydrationError,
    apply_hydrated_template_v2_state,
    hydrate_template_v2_slide,
    resolve_template_v2_layout,
)


def _image(
    name: str, *, fit: str = "fill", icon: bool = False, clip: str | None = None
):
    value = {
        "type": "image",
        "data": f"https://old.example/{name}.png",
        "fit": fit,
        "decorative": False,
        "name": name,
        "is_icon": icon,
    }
    if clip:
        value["clip_path"] = clip
    return value


def _layout():
    return {
        "id": "hero",
        "description": "A valid image hydration layout",
        "components": [
            {
                "id": "visuals",
                "description": "Generated visual content group",
                "position": {"x": 0, "y": 0},
                "elements": [
                    _image("photo"),
                    _image("vector"),
                    _image("clipped", clip="M0 0 L1 0 L1 1 Z"),
                    _image("icon", icon=True),
                ],
            }
        ],
    }


def test_hydration_clones_layout_and_preserves_image_prompt_metadata():
    source = _layout()
    before = copy.deepcopy(source)

    state = hydrate_template_v2_slide(
        source,
        {
            "visuals": {
                "photo": {
                    "image_url": "https://cdn.example/photo.jpg",
                    "image_prompt": "a quiet mountain lake",
                },
                "vector": {
                    "image_url": "https://cdn.example/mark.svg?rev=2",
                    "source": "brand asset library",
                },
                "clipped": {"image_url": "https://cdn.example/clipped.webp"},
                "icon": {
                    "icon_url": "https://cdn.example/icon.svg",
                    "icon_query": "arrow up",
                },
            }
        },
    )

    assert source == before
    photo, vector, clipped, icon = state.ui["components"][0]["elements"]
    assert (photo["data"], photo["fit"], photo["prompt"]) == (
        "https://cdn.example/photo.jpg",
        "cover",
        "a quiet mountain lake",
    )
    assert vector["fit"] == "fill"
    assert vector["prompt"] == "brand asset library"
    assert clipped["fit"] == "fill"
    assert icon["fit"] == "fill"
    assert icon["prompt"] == "arrow up"


def test_hydration_is_fail_closed_for_malformed_native_ui():
    source = _layout()
    source["unexpected"] = "must not be silently dropped"

    with pytest.raises(TemplateV2HydrationError, match="invalid native UI"):
        hydrate_template_v2_slide(source, {"visuals": {}})


def test_hydration_normalizes_generated_math_expression():
    source = {
        "id": "math",
        "description": "Generated equation",
        "components": [
            {
                "id": "content",
                "description": "Equation content",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "math",
                        "latex": "x",
                        "display_mode": True,
                        "decorative": False,
                        "name": "equation",
                        "min_length": 1,
                        "max_length": 4000,
                    }
                ],
            }
        ],
    }

    state = hydrate_template_v2_slide(
        source,
        {"content": {"equation": r"$$ E = mc^2 $$"}},
    )

    equation = state.ui["components"][0]["elements"][0]
    assert equation["latex"] == r"E = mc^2"
    assert source["components"][0]["elements"][0]["latex"] == "x"


def _merged_table_layout():
    covered = {"covered_by": {"row": 0, "column": 0}}
    return {
        "id": "merged-table",
        "description": "A valid merged table hydration layout",
        "components": [
            {
                "id": "content",
                "description": "Merged table content",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "table",
                        "name": "summary",
                        "decorative": False,
                        "position": {"x": 0, "y": 0},
                        "size": {"width": 600, "height": 200},
                        "max_columns": 8,
                        "min_columns": 1,
                        "max_rows": 8,
                        "min_rows": 1,
                        "columns": [
                            {
                                "runs": [{"text": "Header"}],
                                "rowspan": 2,
                                "colspan": 2,
                            },
                            {"runs": [{"text": "Hidden header"}], **covered},
                        ],
                        "rows": [
                            [
                                {"runs": [{"text": "Hidden body 1"}], **covered},
                                {"runs": [{"text": "Hidden body 2"}], **covered},
                            ]
                        ],
                    }
                ],
            }
        ],
    }


def test_table_hydration_preserves_spans_and_covered_objects_for_same_grid():
    state = hydrate_template_v2_slide(
        _merged_table_layout(),
        {
            "content": {
                "summary": {
                    "columns": ["Updated header", "Updated hidden header"],
                    "rows": [["Updated hidden body 1", "Updated hidden body 2"]],
                }
            }
        },
    )

    table = state.ui["components"][0]["elements"][0]
    assert table["columns"][0]["rowspan"] == 2
    assert table["columns"][0]["colspan"] == 2
    assert table["columns"][1]["covered_by"] == {"row": 0, "column": 0}
    assert table["rows"][0][0]["covered_by"] == {"row": 0, "column": 0}
    assert table["rows"][0][1]["covered_by"] == {"row": 0, "column": 0}
    assert table["rows"][0][1]["runs"][0]["text"] == "Updated hidden body 2"


def test_table_hydration_strips_all_spans_when_grid_shape_changes():
    state = hydrate_template_v2_slide(
        _merged_table_layout(),
        {
            "content": {
                "summary": {
                    "columns": ["H1", "H2", "H3"],
                    "rows": [["B1", "B2", "B3"]],
                }
            }
        },
    )

    table = state.ui["components"][0]["elements"][0]
    assert len(table["columns"]) == 3
    for row in [table["columns"], *table["rows"]]:
        for cell in row:
            assert cell.get("rowspan", 1) == 1
            assert cell.get("colspan", 1) == 1
            assert cell.get("covered_by") is None


def test_resolve_layout_returns_detached_copy():
    payload = {"layouts": [_layout()]}
    resolved = resolve_template_v2_layout(payload, "hero")
    resolved["description"] = "changed independently"
    assert payload["layouts"][0]["description"] != resolved["description"]


def test_apply_state_restores_all_fields_when_assignment_fails():
    state = hydrate_template_v2_slide(_layout(), {"visuals": {}})

    class Slide:
        def __init__(self):
            self.content = {"old": True}
            self.html_content = None
            self._ui = {"old": True}

        @property
        def ui(self):
            return self._ui

        @ui.setter
        def ui(self, value):
            if "components" in value:
                raise RuntimeError("database type rejected bind")
            self._ui = value

    slide = Slide()
    with pytest.raises(RuntimeError, match="rejected bind"):
        apply_hydrated_template_v2_state(slide, state)
    assert slide.content == {"old": True}
    assert slide.ui == {"old": True}
    assert slide.html_content is None
