from __future__ import annotations

from copy import deepcopy

from templates.v2.pptx.models import PresentationCandidates
from templates.v2.pptx.repeat_suggestions import (
    build_repeat_block_suggestions,
)


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
