"""Whole-visual-number verification (raster redesign number fidelity).

Glyphs are drawn as dark rectangle outlines on a white page; OCR words are given
directly, and the targeted Tesseract reads are replaced by fixed readings in
unit order, so these tests need no OCR engine.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from services.source_content import raster_text_guards as guards
from services.source_content.raster_number_guard import guard_numbers
from services.source_content.raster_number_units import (
    NumberUnit,
    UnitRead,
    number_units,
    sequence_confirmed,
    text_numbers,
)
from services.source_content.raster_page_plan import PagePlan, PlannedRegion
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_text_guards import NumberReads, numbers_supported


def _word(text, box, confidence=0.95):
    return RedesignWord(text, box, confidence, "test", True)


def _page(glyphs, size=(800, 400)):
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x, y, w, h in glyphs:
        # Glyph-like strokes (an outline), not filled blocks: filled bands are panels.
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=(20, 20, 20), width=max(1, min(w, h) // 5))
    return image


def _gray(image):
    return np.asarray(image.convert("L"), dtype=np.int16)


def _units(words, glyphs=None, size=(800, 400)):
    image = _page(glyphs if glyphs is not None else [w.bbox for w in words], size)
    return number_units(_gray(image), tuple(words), range(len(words)))


def _read(unit, plain, restricted=None):
    return UnitRead(unit, tuple(plain), tuple(plain if restricted is None else restricted))


# -- grouping: fragments of one visual number form one unit ---------------------


def test_split_01_is_one_unit_and_a_fragment_never_confirms_itself():
    words = [_word("0", (100, 100, 20, 40)), _word("1", (124, 100, 20, 40))]
    (unit,) = _units(words)
    assert unit.words == (0, 1) and unit.text == "01" and not unit.wrapped
    read = _read(unit, ["01"])
    reads = NumberReads([read])
    assert numbers_supported("01", (), reads)
    assert not numbers_supported("0", (), reads)  # the fragment reads "0" on its own box only
    assert not numbers_supported("0 1", (), reads)


@pytest.mark.parametrize(
    "texts,boxes,expected",
    [
        (["1", "2", "8", ".", "4"], [(100, 100, 20, 40), (124, 100, 20, 40), (148, 100, 20, 40),
                                    (171, 132, 6, 8), (180, 100, 20, 40)], "128.4"),
        (["18", ".", "6"], [(100, 100, 44, 40), (147, 132, 6, 8), (156, 100, 20, 40)], "18.6"),
        (["2", ",", "450"], [(100, 100, 20, 40), (123, 134, 5, 10), (131, 100, 66, 40)], "2,450"),
        (["83", "%"], [(100, 100, 44, 40), (148, 100, 30, 40)], "83%"),
    ],
)
def test_fragments_across_ocr_word_boundaries_form_one_unit(texts, boxes, expected):
    words = [_word(t, b) for t, b in zip(texts, boxes, strict=True)]
    (unit,) = _units(words)
    assert unit.text == expected and unit.words == tuple(range(len(words)))
    reads = NumberReads([_read(unit, [expected])])
    assert numbers_supported(expected, (), reads)
    fragment = texts[0]
    assert not numbers_supported(fragment, (), reads)


def test_a_true_single_digit_stays_publishable():
    # "Step 3": a word space (half a glyph height) separates the digit from the word.
    words = [_word("Step", (100, 100, 80, 40)), _word("3", (200, 100, 20, 40))]
    (unit,) = _units(words)
    assert unit.words == (1,) and unit.text == "3" and not unit.wrapped
    assert numbers_supported("Step 3", (), NumberReads([_read(unit, ["3"])]))


def test_unit_box_covers_a_glyph_ocr_dropped():
    # "60%" read as "0%" with a box that starts at the "0": the crop must show the "6".
    words = [_word("0%", (130, 100, 60, 40))]
    glyphs = [(104, 100, 22, 40), (130, 100, 24, 40), (160, 100, 30, 40)]
    (unit,) = _units(words, glyphs)
    assert unit.box[0] == 104
    assert not numbers_supported("0%", (), NumberReads([_read(unit, ["60%"])]))


# -- numbers that continue beyond their row -------------------------------------


def test_number_wrapped_over_stacked_rows_is_never_confirmed():
    # "01" wrapped as "0" above "1" (a narrow text box): each row alone.
    words = [_word("0", (100, 100, 20, 40)), _word("1", (100, 160, 20, 40))]
    first, second = _units(words)
    assert first.wrapped and second.wrapped
    reads = NumberReads([_read(first, ["0"])])
    assert not numbers_supported("0", ("0", "0"), reads)  # agreeing line reads do not substitute


def test_number_ending_in_a_separator_at_row_end_is_open():
    words = [_word("18.", (100, 100, 70, 40))]
    glyphs = [(100, 100, 20, 40), (124, 100, 20, 40), (148, 132, 8, 8)]
    (unit,) = _units(words, glyphs)
    assert unit.wrapped


def test_value_label_on_its_bar_may_hide_the_rest_of_the_number():
    image = _page([(100, 100, 20, 40), (124, 100, 20, 40)])
    ImageDraw.Draw(image).rectangle((80, 150, 200, 390), fill=(20, 20, 20))  # the bar, the label's colour
    words = (_word("53", (100, 100, 44, 40)), _word("53%", (300, 100, 44, 40)))
    (label,) = number_units(_gray(image), words, [0])
    assert label.wrapped  # "53%" with its "%" behind the bar reads as "53"
    complete = number_units(_gray(image), (_word("53%", (100, 100, 44, 40)),), [0])
    assert not complete[0].wrapped


def test_column_of_complete_counts_is_a_list_not_a_wrap():
    words = [
        _word("4", (100, 100, 20, 40)),
        _word("건", (124, 100, 34, 40)),
        _word("1", (100, 160, 20, 40)),
        _word("건", (124, 160, 34, 40)),
    ]
    units = _units(words)
    assert len(units) == 2 and not any(unit.wrapped for unit in units)


def test_label_rows_of_other_size_do_not_make_a_wrap():
    words = [_word("62", (100, 100, 60, 60)), _word("+7 points", (100, 175, 120, 16))]
    units = _units(words)
    assert [u.text for u in units] == ["62", "+7points"] and not units[0].wrapped


# -- reading rules ----------------------------------------------------------------


def _unit(text):
    return NumberUnit((0,), (0, 0, 40, 20), text, False)


def test_digit_whitelist_read_never_confirms_alone_and_disagreement_confirms_nothing():
    assert UnitRead(_unit("64"), (), ("64",)).readings() == ()
    assert UnitRead(_unit("64"), ("4",), ("64",)).readings() == ()  # "G4" read as "64" by the whitelist
    assert UnitRead(_unit("8396"), ("8396",), ("83%",)).readings() == (("83%",),)
    assert UnitRead(_unit("128.4B"), ("128.4",), ("128.48",)).readings() == (("128.4",),)
    assert UnitRead(NumberUnit((0,), (0, 0, 40, 20), "3", True), ("3",), ("3",)).readings() == ()


def test_hangul_glyph_after_a_number_may_add_digits_to_the_read():
    read = UnitRead(_unit("40,000원"), ("40,0008",), ("40,0008",))
    assert sequence_confirmed(text_numbers("40,000원/시간"), (read,), allow_skip=False)
    counts = UnitRead(_unit("2,067시간"), ("2,067", "2", "4"), ())
    assert sequence_confirmed(text_numbers("2,067시간"), (counts,), allow_skip=False)
    latin = UnitRead(_unit("2g"), ("25",), ("25",))
    assert not sequence_confirmed(text_numbers("2g"), (latin,), allow_skip=False)


def test_text_numbers_keep_separate_numbers_apart():
    assert text_numbers("Q3 2025") == [("3", False), ("2025", False)]
    assert text_numbers("약 9, 860시간") == [("9,860", True)]


def test_legacy_plain_tuple_rules_still_hold():
    assert not numbers_supported("8396", ("8396", "8396"), ("83%",))
    assert numbers_supported("40,000원/시간", ("40,000원/시간",), ("40,0008",))
    assert not numbers_supported("0", (), ("04",))  # extension only for a Hangul glyph


# -- product entry points with a stubbed OCR engine ------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"")
    answers: dict[str, list[str]] = {}

    def fake_read(paths, folder, extra, tag, *, executable, tessdata, lang="eng"):
        return list(answers[tag][: len(paths)])

    monkeypatch.setattr(guards, "_read", fake_read)
    return executable, answers


def test_targeted_number_reads_give_every_line_its_units(engine):
    executable, answers = engine
    words = (_word("0", (100, 100, 20, 40)), _word("1", (124, 100, 20, 40)), _word("Core", (200, 110, 60, 20)))
    lines = (RedesignLine((0, 1), (100, 100, 44, 40), 40.0), RedesignLine((2,), (200, 110, 60, 20), 20.0))
    answers.update(eng=["01"], digits=["01"])
    image = _page([w.bbox for w in words])
    targeted = guards.targeted_number_reads(image, words, lines, executable=executable, tessdata=executable.parent)
    assert isinstance(targeted[0], NumberReads) and tuple(targeted[0]) == ("01",)
    assert tuple(targeted[1]) == () and isinstance(targeted[1], NumberReads)
    assert numbers_supported("01", (), targeted[0]) and not numbers_supported("0 1", (), targeted[0])
    assert not numbers_supported("Core 7", (), targeted[1])  # a number with no unit behind it


def test_guard_numbers_keeps_a_fragmented_number_as_pixels(engine):
    executable, answers = engine
    words = (
        _word("2", (100, 100, 20, 40)),
        _word("0", (124, 100, 20, 40)),
        _word("%", (148, 100, 26, 40)),
        _word("Step", (100, 200, 80, 40)),
        _word("3", (200, 200, 20, 40)),
    )
    plan = PagePlan(
        (
            PlannedRegion("body", (100, 100, 74, 40), (0, 1, 2), "2 0 %"),
            PlannedRegion("body", (100, 200, 120, 40), (3, 4), "Step 3"),
        ),
        (),
    )
    answers.update(eng=["20%", "3"], digits=["20%", "3"])
    image = _page([w.bbox for w in words])
    guarded = guard_numbers(plan, words, image, executable, executable.parent)
    first, second = guarded.regions
    assert first.editable_fallback and first.review_reason == "unconfirmed_number"  # "2 0 %" is not "20%"
    assert not second.editable_fallback and second.role == "body"


def test_guard_numbers_turns_a_table_with_an_unconfirmed_number_into_pixels(engine):
    executable, answers = engine
    words = (_word("45", (100, 100, 40, 20)), _word("1", (300, 100, 12, 20)))
    table = PlannedRegion("table", (90, 90, 300, 40), (0, 1), None, table_cells=(("45", "1"),))
    answers.update(eng=["45", "14"], digits=["45", "14"])
    image = _page([w.bbox for w in words])
    (guarded,) = guard_numbers(PagePlan((table,), ()), words, image, executable, executable.parent).regions
    assert guarded.role == "table" and guarded.table_cells is None and guarded.editable_fallback


def test_guard_numbers_without_an_engine_publishes_no_number(tmp_path):
    words = (_word("7", (100, 100, 20, 40)),)
    plan = PagePlan((PlannedRegion("body", (100, 100, 20, 40), (0,), "7"),), ())
    guarded = guard_numbers(plan, words, _page([(100, 100, 20, 40)]), None, None)
    assert guarded.regions[0].editable_fallback
