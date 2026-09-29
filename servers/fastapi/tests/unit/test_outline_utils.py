import pytest

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from utils.outline_utils import (
    _extract_outline_title,
    get_no_of_toc_required_for_n_outlines,
    get_presentation_title_from_presentation_outline,
)


def test_get_presentation_title_handles_prefixed_page_heading():
    outline = PresentationOutlineModel(
        slides=[SlideOutlineModel(content="## Page 1: Growth/Plan\\Roadmap\nBody")]
    )

    title = get_presentation_title_from_presentation_outline(outline)
    assert title == "GrowthPlanRoadmap Body"


def test_get_presentation_title_for_empty_outline():
    outline = PresentationOutlineModel(slides=[])
    assert get_presentation_title_from_presentation_outline(outline) == "Untitled Presentation"


@pytest.mark.parametrize(
    ("n_outlines", "title_slide", "target_total_slides", "expected"),
    [
        (0, True, None, 0),
        (12, True, None, 2),
        (12, False, None, 2),
        (8, True, 25, 3),
    ],
)
def test_calculate_no_of_toc_required_for_n_outlines(
    n_outlines: int,
    title_slide: bool,
    target_total_slides: int | None,
    expected: int,
):
    assert (
        get_no_of_toc_required_for_n_outlines(
            n_outlines=n_outlines,
            title_slide=title_slide,
            target_total_slides=target_total_slides,
        )
        == expected
    )


def test_extract_outline_title_uses_heading_then_sentence_then_fallback():
    assert _extract_outline_title("## Heading title\nBody") == "Heading title"
    assert _extract_outline_title("First sentence. Second sentence.") == "First sentence."
    assert _extract_outline_title(" \nline fallback\n") == "line fallback"
    assert _extract_outline_title("") == "Slide"
