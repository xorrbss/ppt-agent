import json

import pytest

from utils.outline_utils import (
    OUTLINE_PARSE_FAILED,
    OUTLINE_SLIDE_COUNT_SHORT,
    OutlineResponseError,
    parse_outline_response,
)


def _answer(count: int) -> str:
    return json.dumps({"slides": [{"content": f"# Slide {i}"} for i in range(count)]})


def test_overshoot_is_trimmed_to_the_requested_count():
    outline = parse_outline_response(_answer(5), 3)
    assert [slide.content for slide in outline.slides] == ["# Slide 0", "# Slide 1", "# Slide 2"]


def test_auto_count_keeps_every_slide():
    assert len(parse_outline_response(_answer(4), None).slides) == 4


def test_undershoot_is_a_named_error_without_a_parse_cause():
    with pytest.raises(OutlineResponseError) as caught:
        parse_outline_response(_answer(2), 3)
    assert caught.value.detail == OUTLINE_SLIDE_COUNT_SHORT
    assert caught.value.cause is None


def test_unparseable_answer_keeps_the_parse_cause():
    with pytest.raises(OutlineResponseError) as caught:
        parse_outline_response("{not json", 3)
    assert caught.value.detail == OUTLINE_PARSE_FAILED
    assert caught.value.cause is not None
