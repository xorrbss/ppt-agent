from __future__ import annotations

from copy import deepcopy

from templates.v2.pptx.models import PresentationCandidates
from templates.v2.pptx.assembler import assemble_template_v2_draft
from templates.v2.pptx.repeat_suggestions import (
    build_repeat_block_suggestions,
)
from templates.v2.pptx.repeat_application import (
    resolve_repeat_suggestion_decisions,
)
from templates.v2.generation import build_generated_slide


def _candidates(order: tuple[str, ...]) -> PresentationCandidates:
    positions = {"left": 100.0, "middle": 300.0, "right": 500.0}
    return PresentationCandidates.model_validate(
        {
            "source_sha256": "c" * 64,
            "slides": [
                {
                    "source_part": "ppt/slides/slide1.xml",
                    "relationship_id": "rId1",
                    "width": 1280.0,
                    "height": 720.0,
                    "shapes": [
                        {
                            "source_id": source_id,
                            "name": source_id.title(),
                            "kind": "container",
                            "x": positions[source_id],
                            "y": 120.0,
                            "width": 160.0,
                            "height": 80.0,
                            "rotation": 0.0,
                            "fill_color": "#3366FF",
                            "confidence": 0.95,
                        }
                        for source_id in order
                    ],
                    "external_relationships": [],
                }
            ],
        }
    )


def test_repeat_suggestions_are_deterministic_and_non_mutating() -> None:
    candidates = _candidates(("right", "left", "middle"))
    original = deepcopy(candidates.model_dump(mode="json"))

    first = build_repeat_block_suggestions(candidates)
    second = build_repeat_block_suggestions(
        _candidates(("middle", "right", "left"))
    )

    assert first == second
    assert first == [
        {
            "id": first[0]["id"],
            "kind": "repeat_block_merge",
            "status": "suggested",
            "slide": 1,
            "source_part": "ppt/slides/slide1.xml",
            "axis": "horizontal",
            "source_ids": ["left", "middle", "right"],
            "confidence": 0.95,
        }
    ]
    assert candidates.model_dump(mode="json") == original


def test_repeat_suggestions_do_not_merge_irregular_sequences() -> None:
    candidates = _candidates(("left", "middle", "right"))
    candidates.slides[0].shapes[2].x = 560.0

    assert build_repeat_block_suggestions(candidates) == []


def test_accepted_repeat_suggestion_becomes_one_editable_group() -> None:
    candidates = _candidates(("left", "middle", "right"))
    suggestion = build_repeat_block_suggestions(candidates)[0]

    draft = assemble_template_v2_draft(
        candidates,
        accepted_repeat_suggestions=[suggestion],
    )

    layout = draft.layouts.layouts[0]
    assert len(layout.components) == 1
    group = layout.components[0].elements[0]
    assert group.type == "group"
    assert len(group.children) == 3
    assert [child.position.x for child in group.children] == [0, 200, 400]
    assert draft.manifest["slides"][0]["repeat_blocks"] == [
        {
            **suggestion,
            "status": "applied",
        }
    ]


def test_repeat_suggestion_decisions_reject_unknown_and_duplicate_ids() -> None:
    suggestions = build_repeat_block_suggestions(
        _candidates(("left", "middle", "right"))
    )

    cases = (
        (("missing",), "unknown_repeat_suggestion_id"),
        (
            (suggestions[0]["id"], suggestions[0]["id"]),
            "duplicate_repeat_suggestion_id",
        ),
    )
    for accepted_ids, message in cases:
        try:
            resolve_repeat_suggestion_decisions(suggestions, accepted_ids)
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError("invalid repeat selection must be rejected")


def test_accepted_text_repeat_group_preserves_editable_nested_content() -> None:
    candidates = _candidates(("left", "middle", "right"))
    for shape in candidates.slides[0].shapes:
        shape.kind = "text"
        shape.text = shape.name
    suggestion = build_repeat_block_suggestions(candidates)[0]

    draft = assemble_template_v2_draft(
        candidates,
        accepted_repeat_suggestions=[suggestion],
    )
    generated = build_generated_slide(
        draft.layouts.layouts[0],
        draft.contents[0],
    )

    group = generated.ui["components"][0]["elements"][0]
    assert group["type"] == "group"
    assert [child["runs"][0]["text"] for child in group["children"]] == [
        "Left",
        "Middle",
        "Right",
    ]
