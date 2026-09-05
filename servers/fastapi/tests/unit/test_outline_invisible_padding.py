"""An outline slide never carries zero-width padding, and the schema never asks for it.

Regression for 2026-09-05: with a 150-character schema floor under a prompt whose
honest agenda page ran 73 characters, the outline model appended a "페이지: N"
line and 700 U+200B characters to close the string. The padding then counted as
slide content downstream.
"""

from __future__ import annotations

from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
    strip_invisible_format_chars,
)
from utils.get_dynamic_models import (
    _OUTLINE_CONTENT_BOUNDS,
    get_presentation_outline_model_with_n_slides,
)

_PADDED = "## 목차\n- Ⅰ. 제안사 소개\n- 페이지: 목차-1" + "​" * 700


def test_zero_width_padding_is_removed_on_parse() -> None:
    slide = SlideOutlineModel(content=_PADDED)
    assert "​" not in slide.content
    assert slide.content == "## 목차\n- Ⅰ. 제안사 소개\n- 페이지: 목차-1"


def test_every_invisible_format_character_is_stripped() -> None:
    assert strip_invisible_format_chars("a​b‌c‍d⁠e﻿") == "abcde"


def test_ordinary_text_is_untouched() -> None:
    text = "- 법인·재무·신용정보 최근 3개년\n- PM·아키텍트 12개 역할"
    assert SlideOutlineModel(content=text).content == text


def test_the_schema_floor_cannot_ask_for_padding() -> None:
    # A minimum length is the only reason a decoder pads; one character means
    # "the slide exists" and nothing more.
    for verbosity, (floor, ceiling) in _OUTLINE_CONTENT_BOUNDS.items():
        assert floor == 1, verbosity
        assert ceiling >= 400, verbosity


def test_the_dynamic_model_accepts_an_honest_short_page() -> None:
    model = get_presentation_outline_model_with_n_slides(2, "standard")
    parsed = model.model_validate(
        {"slides": [{"content": "## 목차\n- Ⅰ. 제안사 소개\n- Ⅱ. 제안개요"}, {"content": _PADDED}]}
    )
    assert isinstance(parsed, PresentationOutlineModel)
    assert all("​" not in slide.content for slide in parsed.slides)
