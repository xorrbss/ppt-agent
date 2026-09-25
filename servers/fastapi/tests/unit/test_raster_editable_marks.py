"""Pixel grounding of punctuation marks the OCR dropped or misread."""

from __future__ import annotations

from PIL import Image, ImageDraw

from services.source_content.raster_captures import PageContext
from services.source_content.raster_editable_marks import grounded_view
from services.source_content.raster_paragraphs import starts_list_item
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_text_correction import adds_punctuation, normalized, transcription_agrees

HEIGHT = 30


def _page(texts: list[str], gaps: list[str], width: int = 70, gap: int = 40) -> tuple[PageContext, tuple[int, ...]]:
    """One line of word boxes; ``gaps[k]`` draws what sits between word k and k+1:
    "dot" (a middle dot), "colon", "blob" (a letter-sized glyph) or "" (nothing)."""

    image = Image.new("RGB", (1200, 200), "white")
    draw = ImageDraw.Draw(image)
    words, x, y = [], 40, 60
    for k, text in enumerate(texts):
        draw.rectangle((x, y, x + width - 1, y + HEIGHT - 1), fill="black")
        words.append(RedesignWord(text, (x, y, width, HEIGHT), 0.95, "test", k > 0))
        x += width
        kind = gaps[k] if k < len(gaps) else ""
        middle = x + gap // 2
        if kind == "dot":
            draw.ellipse((middle - 2, y + 13, middle + 2, y + 17), fill="black")
        elif kind == "colon":
            draw.rectangle((middle - 2, y + 6, middle + 1, y + 9), fill="black")
            draw.rectangle((middle - 2, y + 21, middle + 1, y + 24), fill="black")
        elif kind == "blob":
            draw.rectangle((middle - 8, y, middle + 8, y + HEIGHT - 1), fill="black")
        elif kind == "touching":
            draw.rectangle((x, y + 13, x + 3, y + 16), fill="black")
        elif kind == "sliver+dot":  # a word box one pixel short of its glyph, then a dot
            draw.rectangle((x, y, x, y + HEIGHT - 1), fill="black")
            draw.ellipse((middle - 2, y + 13, middle + 2, y + 17), fill="black")
        elif kind == "more":  # further content right after the last word
            draw.ellipse((x + 10, y + 13, x + 14, y + 17), fill="black")
            draw.rectangle((x + 30, y, x + 80, y + HEIGHT - 1), fill="black")
        x += gap
    line = RedesignLine(tuple(range(len(words))), (40, y, x - 40, HEIGHT), float(HEIGHT))
    return PageContext(image, tuple(words), (line,)), tuple(range(len(words)))


def _view(texts, gaps, transcript):
    page, carried = _page(texts, gaps)
    return grounded_view(page, carried, transcript)


def test_inserted_dot_is_grounded_only_by_an_isolated_dot_glyph():
    view = _view(["PART", "II", "GEOGRAPHY"], ["", "dot"], "PART II · GEOGRAPHY")
    assert view == normalized("PART II GEOGRAPHY")
    assert transcription_agrees(["PART II GEOGRAPHY"], view)
    assert not adds_punctuation(view, normalized("PART II GEOGRAPHY"))
    # No ink between the words: the model's dot is not in the source.
    ungrounded = _view(["PART", "II", "GEOGRAPHY"], ["", ""], "PART II · GEOGRAPHY")
    assert adds_punctuation(ungrounded, normalized("PART II GEOGRAPHY"))
    # A letter-sized glyph is not a dot; a piece touching the word box is a truncated glyph.
    assert "·" in _view(["PART", "II", "GEOGRAPHY"], ["", "blob"], "PART II · GEOGRAPHY")
    assert "·" in _view(["PART", "II", "GEOGRAPHY"], ["", "touching"], "PART II · GEOGRAPHY")


def test_word_box_slivers_are_ignored_and_edge_marks_need_a_clear_end():
    assert _view(["PART", "II", "GEOGRAPHY"], ["", "sliver+dot"], "PART II · GEOGRAPHY") == normalized(
        "PART II GEOGRAPHY"
    )
    # A dot after the last word followed by more ink within reach is not a lone mark.
    page, carried = _page(["항목", "설명"], ["", "more"], gap=40)
    assert "·" in grounded_view(page, carried, "항목 설명 ·")


def test_hallucinated_trailing_colon_stays_ungrounded():
    view = _view(["구성해", "드리"], [""], "구성해 드리:")
    assert adds_punctuation(view, normalized("구성해 드리"))
    colon = _view(["항목", "설명"], ["colon"], "항목: 설명")
    assert colon == normalized("항목 설명")


def test_dot_read_as_another_symbol_is_a_substitution():
    view = _view(["자본", "-", "Capital"], ["", ""], "자본 · Capital")
    assert view == normalized("자본 - Capital")
    # A colon is never grounded by another OCR symbol (only by its own glyph).
    assert ":" in _view(["자본", "-", "Capital"], ["", ""], "자본 : Capital")


def test_marks_between_digits_are_left_to_the_number_gate():
    assert "·" in _view(["1", "5"], ["dot"], "1·5")
    assert "·" in _view(["1", ".", "5"], ["", ""], "1 · 5")


def test_list_item_lines_start_paragraphs():
    for text in ("• 1. 추진 배경", "* 5. 시스템 화면 소개", "3. 도입 시나리오", "2) Scope", "① 개요", "- item"):
        assert starts_list_item(text), text
    for text in ("12.5% 성장", "2026. 09. 25", "Revenue grew", "", None):
        assert not starts_list_item(text), text


def test_lone_edge_glyph_needs_an_ocr_reading_at_that_end():
    from services.source_content.raster_text_correction import edge_glyphs_grounded

    assert not edge_glyphs_grounded("$ NPS", ["S NPS", "S$ NPS"])  # an icon read as S / $
    assert not edge_glyphs_grounded("a OpenAI", ["읍 OpenAl", "| OpenAl"])
    assert edge_glyphs_grounded("약 2배", ["of 2배"])  # a syllable the OCR read as Latin
    assert edge_glyphs_grounded("약 60%", ["2k 60%", "약60%"])  # joined to its neighbour
    assert edge_glyphs_grounded("• 1. 추진 배경", ["* 1. 추진 배경"])  # marks: judged by pixels
    assert edge_glyphs_grounded("매출 현황", ["매출 현황"])


def test_paragraphs_split_at_list_items_and_type_size_steps():
    from types import SimpleNamespace

    from services.source_content.raster_paragraphs import paragraphs

    rows = [  # (reading, top, height): a figure, its label, then a tight list, then a wrapped line
        ("68", 100, 90),
        ("+2.1pp margin", 192, 20),
        ("• 1. 추진 배경", 230, 20),
        ("• 2. 처리 시간 비교와", 254, 20),
        ("moves", 278, 14),
    ]
    lines = [RedesignLine((n,), (40, top, 200, height), float(height)) for n, (_, top, height) in enumerate(rows)]
    build = SimpleNamespace(
        page=SimpleNamespace(lines=lines),
        tokens={n: (n,) for n in range(len(rows))},
        texts={n: None for n in range(len(rows))},
        readings={n: text for n, (text, _, _) in enumerate(rows)},
    )
    assert paragraphs(build, list(range(len(rows))), 0, lambda texts, i: False) == [[0], [1], [2], [3, 4]]


def test_model_may_not_collapse_unread_glyph_slots():
    from services.source_content.raster_text_correction import collapses_unread_glyphs

    # OCR saw two unreadable glyphs after "공"; the model wrote one: "공급자" lost a syllable.
    assert collapses_unread_glyphs(normalized("공 _— / Supplier Economics"), normalized("공급 / Supplier Economics"))
    assert not transcription_agrees(["공 _— / Supplier Economics"], "공급 / Supplier Economics")
    # One unreadable glyph read as one letter is a misread, not lost content.
    assert not collapses_unread_glyphs(normalized("거의 = 배."), normalized("거의 두 배."))
    assert transcription_agrees(["$1B+ 이벤트는 거의 = 배."], "$1B+ 이벤트는 거의 두 배.")
