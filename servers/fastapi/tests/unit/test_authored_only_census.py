from __future__ import annotations

from services.compatibility_migration.census import classify_presentation


def _presentation(identifier: str, **changes):
    value = {
        "id": identifier,
        "owner_id": "owner-a",
        "generation_mode": "standard",
        "mode": None,
        "version": "v1-standard",
        "theme": None,
        "deck_plan": None,
        "community_design_ids": None,
        "profile_driver_id": None,
        "profile_schema_version": None,
        "file_paths": None,
        "reference_snapshot_ids": None,
    }
    value.update(changes)
    return value


def _slide(presentation: str, index: int = 0, **changes):
    value = {
        "id": f"slide-{presentation}-{index}",
        "owner_id": "owner-a",
        "presentation": presentation,
        "index": index,
        "layout_group": "general",
        "layout": "title",
        "content": {"title": "x"},
        "html_content": None,
        "ui": None,
    }
    value.update(changes)
    return value


def _native_ui():
    return {
        "id": "title",
        "description": "Native editable title slide",
        "components": [
            {
                "id": "hero",
                "description": "Editable hero title component",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "text",
                        "position": {"x": 1, "y": 1},
                        "size": {"width": 8, "height": 1},
                        "runs": [{"text": "Title", "font": {"size": 18}}],
                        "decorative": False,
                        "name": "title",
                        "min_length": 1,
                        "max_length": 80,
                    }
                ],
            }
        ],
    }


def test_canonical_census_assigns_every_required_classification_once():
    """Live formats can be described; everything else is reported, not guessed.

    A deck in one of the retired React-template formats -- including
    template-v2, whose driver id was removed from the registry alongside its
    studio -- resolves no profile, so the census reports it invalid and
    quarantines it rather than inventing a cohort for a deck nothing can
    render.
    """
    presentations = [
        _presentation("template", mode="template", version="v2-standard"),
        _presentation("authored", mode="authored", theme={"mode": "authored"}),
        _presentation("mixed", mode="svg", theme={"mode": "svg"}),
        _presentation("retired"),
        _presentation("invalid", generation_mode="mystery"),
    ]
    slides = [
        _slide("template", layout_group="native", ui=_native_ui()),
        _slide(
            "authored",
            layout_group="authored",
            layout="authored:0",
            content={"__authored__": True},
            html_content="<section>authored</section>",
        ),
        _slide("mixed"),
        _slide("retired"),
        _slide("invalid"),
    ]

    result = [
        classify_presentation(
            presentation,
            [slide for slide in slides if slide["presentation"] == presentation["id"]],
        )
        for presentation in presentations
    ]

    # The authored driver was removed on 2026-11-10 (option A): an authored
    # deck is reported and quarantined exactly like a Template V2 deck.
    assert [entry.classification for entry in result] == [
        "invalid",
        "invalid",
        "mixed",
        "invalid",
        "invalid",
    ]
    assert len(result) == len(presentations)
    assert all(entry.quarantine for entry in result)
    assert result[1].quarantine_reason == "unsupported_presentation_driver"
    assert [entry.quarantine_reason for entry in result[-2:]] == [
        "unsupported_presentation_driver",
        "unsupported_generation_mode",
    ]
    assert result[0].quarantine_reason == "unsupported_presentation_driver"


def test_malformed_slide_is_invalid_and_profile_evidence_conflict_is_mixed():
    svg = _presentation("svg", mode="svg", theme={"mode": "svg"})

    malformed = classify_presentation(
        svg,
        [_slide("svg", html_content=42)],
    )
    mixed = classify_presentation(svg, [_slide("svg")])

    assert malformed.classification == "invalid"
    assert malformed.quarantine_reason == "slide_html_invalid"
    assert mixed.classification == "mixed"
    assert mixed.quarantine_reason == "canonical_profile_slide_evidence_conflict"
