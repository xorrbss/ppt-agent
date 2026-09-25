"""Numbers with a unit word written right after them ("약 12.5억원", "120회", "~3배").

Glyphs are drawn as dark rectangle outlines on a white page (a decimal point is a
small box on the baseline); the Tesseract reads are replaced by fixed answers per
read tag, so these tests need no OCR engine.
"""

from __future__ import annotations

import pickle

import numpy as np
import pytest
from PIL import Image, ImageDraw

from services.source_content import raster_text_guards as guards
from services.source_content.raster_number_guard import guard_numbers
from services.source_content.raster_number_match import text_numbers
from services.source_content.raster_number_suffix import SplitRead, glyph_shape, suffix_splits, tail_suffix
from services.source_content.raster_number_units import NumberUnit, UnitRead, number_units
from services.source_content.raster_page_plan import PagePlan, PlannedRegion
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_text_guards import NumberReads, numbers_supported


def _word(text, box, confidence=0.95):
    return RedesignWord(text, box, confidence, "test", True)


def _page(glyphs, size=(800, 400)):
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x, y, w, h in glyphs:
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=(20, 20, 20), width=max(1, min(w, h) // 5))
    return image


def _gray(image):
    return np.asarray(image.convert("L"), dtype=np.int16)


def _unit(text="12.5억원", wrapped=False):
    return NumberUnit((0,), (0, 0, 120, 40), text, wrapped)


def _split_read(head, suffix, shape, restricted=None, *, whole=(), text="12.5억원", wrapped=False):
    split = SplitRead(tuple(head), tuple(head if restricted is None else restricted), suffix, shape)
    return NumberReads([UnitRead(_unit(text, wrapped), tuple(whole), tuple(whole), (split,))])


# "약 12.5억원": "약" apart, "1" "2" "." "5" then the unit word, kerned tight.
DIGITS = [(100, 100, 20, 40), (124, 100, 20, 40), (147, 132, 6, 8), (156, 100, 20, 40)]
UNIT_WORD = (180, 100, 70, 40)
APPROX = (40, 100, 40, 40)


# -- matching: a split confirms the number only with the reading's own unit word ----


@pytest.mark.parametrize(
    "text,head,suffix,shape",
    [
        ("약 12.5억원", "12.5", "억원", "dd.d"),
        ("3.2만원", "3.2", "만원", "d.d"),
        ("120회", "120", "회", "ddd"),
        ("~3배", "3", "배", "?d"),  # a prefix symbol before the number is not a digit glyph
        ("9,860시간", "9,860", "시간", "d,ddd"),
    ],
)
def test_split_head_and_unit_word_confirm_the_number(text, head, suffix, shape):
    assert numbers_supported(text, (), _split_read([head], suffix, shape, text=text))


def test_whole_percent_number_needs_no_split():
    unit = NumberUnit((0,), (0, 0, 60, 40), "45%", False)
    assert numbers_supported("45%", (), NumberReads([UnitRead(unit, ("45%",), ("45%",))]))


def test_dropped_decimal_point_is_restored_only_where_the_glyphs_show_it():
    assert numbers_supported("약 12.5억원", (), _split_read(["125"], "억원", "dd.d"))
    assert not numbers_supported("약 125억원", (), _split_read(["125"], "억원", "dd.d"))  # the page shows a point
    assert not numbers_supported("약 12.5억원", (), _split_read(["125"], "억원", "ddd"))  # no point on the page


@pytest.mark.parametrize(
    "text,reads",
    [
        ("약 12.5억원", _split_read(["12.6"], "억원", "dd.d")),  # a misread digit in the head
        ("약 12.5억원", _split_read(["12.5"], "억원", "dd.d", ["12.6"])),  # the head readers disagree
        ("약 12.5억원", _split_read(["12.5"], "원", "dd.d")),  # the tail is not the reading's unit word
        ("약 12.5억원", _split_read(["12.5"], "", "dd.d")),  # the tail did not read as Hangul only
        ("약 12.5억원", _split_read(["12.5"], "억원", "dd.d", wrapped=True)),  # wrapped over rows
        ("약 12.5억원", _split_read(["125"], "억원", "ddd.d")),  # a lost point, and another digit before it
        ("12.5", _split_read(["12.5"], "억원", "dd.d")),  # the reading has no unit word there
        ("120회", _split_read(["128"], "회", "ddd", text="120회")),
        ("~3배", _split_read(["8"], "배", "?d", text="~3배")),
        ("3.2만원", _split_read(["3.2", "1"], "만원", "d.d?d", text="3.2만원")),  # two numbers in the head
    ],
)
def test_misread_numbers_in_the_same_shapes_stay_unconfirmed(text, reads):
    assert not numbers_supported(text, (), reads)


def test_text_numbers_carry_the_unit_word_after_each_number():
    (number,) = text_numbers("약 12.5억원")
    assert number == ("12.5", True) and number.suffix == "억원"
    assert text_numbers("~3배, 45%")[1].suffix == "" and text_numbers("~3배")[0].suffix == "배"
    assert pickle.loads(pickle.dumps(number)).suffix == "억원"


def test_split_numbers_are_the_patch_tokens_when_the_whole_read_confirms_nothing():
    assert tuple(_split_read(["12.5"], "억원", "dd.d")) == ("12.5",)


# -- geometry: cuts at glyph gaps, head glyph shape, tail coverage --------------------


def test_decimal_and_unit_word_split_across_ocr_words_form_one_unit_with_a_cut_before_the_unit_word():
    words = [_word("약", APPROX), _word("12", (100, 100, 44, 40)), _word(".", (147, 132, 6, 8))]
    words += [_word("5", (156, 100, 20, 40)), _word("억원", UNIT_WORD)]
    gray = _gray(_page([APPROX, *DIGITS, UNIT_WORD]))
    (unit,) = number_units(gray, words, [1, 2, 3])
    assert unit.text == "12.5억원" and unit.words == (1, 2, 3, 4)
    splits = suffix_splits(gray, unit, words)
    last = max(splits, key=lambda split: split.head[2])
    assert last.head == (100, 100, 78, 40) and last.tail == (178, 100, 72, 40)
    assert glyph_shape(gray, last.head) == "dd.d"


def test_a_number_and_its_unit_word_read_as_one_ocr_word_are_cut_after_the_number_glyphs():
    words = [_word("12.", (100, 100, 53, 40)), _word("5억원", (156, 100, 94, 40))]
    gray = _gray(_page([*DIGITS, UNIT_WORD]))
    (unit,) = number_units(gray, words, [0, 1])
    assert any(split.tail[0] == 178 for split in suffix_splits(gray, unit, words))


def test_tail_read_must_be_hangul_only_and_span_the_tail_ink():
    ink = [(180, 250)]
    origin, factor = 166, 1.2  # crop of (178, 100, 72, 40): pad 12, scaled to 48 px glyphs
    assert tail_suffix([("억원", 17, 84)], origin, factor, ink, 40) == "억원"
    assert tail_suffix([("억", 17, 40), ("원", 60, 41)], origin, factor, ink, 40) == "억원"
    assert tail_suffix([("5억원", 17, 84)], origin, factor, ink, 40) == ""  # a digit in the tail
    assert tail_suffix([("억원", 45, 56)], origin, factor, ink, 40) == ""  # a glyph at the left edge unread
    assert tail_suffix([], origin, factor, ink, 40) == ""


# -- product entry points with a stubbed OCR engine ------------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"")
    answers: dict = {}

    def fake_read(paths, folder, extra, tag, *, executable, tessdata, lang="eng"):
        return list(answers[tag][: len(paths)])

    def fake_read_words(paths, folder, extra, tag, *, executable, tessdata, lang="eng"):
        return list(answers[tag][: len(paths)])

    monkeypatch.setattr(guards, "_read", fake_read)
    monkeypatch.setattr(guards, "_read_words", fake_read_words)
    return executable, answers


def _approx_line():
    words = (
        _word("약", APPROX),
        _word("12", (100, 100, 44, 40)),
        _word(".", (147, 132, 6, 8)),
        _word("5", (156, 100, 20, 40)),
        _word("억원", UNIT_WORD),
    )
    return words, _page([APPROX, *DIGITS, UNIT_WORD])


def _answer_splits(answers, head_reads, tail_spans):
    # Cuts left to right: before ".", before "5", before the unit word.
    answers.update(eng=["1259"], digits=["1259"])
    answers["split-eng"] = [head_reads[0], head_reads[1], head_reads[2]]
    answers["split-digits"] = list(answers["split-eng"])
    answers["split-kor"] = [[(".5억원", 5, 120)], [("5억원", 5, 110)], tail_spans]


def test_targeted_reads_confirm_a_number_with_its_unit_word(engine):
    executable, answers = engine
    words, image = _approx_line()
    lines = (RedesignLine((0, 1, 2, 3, 4), (40, 100, 210, 40), 40.0),)
    _answer_splits(answers, ["12", "12.", "12.5"], [("억원", 17, 84)])
    (targeted,) = guards.targeted_number_reads(
        image, words, lines, executable=executable, tessdata=executable.parent
    ).values()
    assert numbers_supported("약 12.5억원", (), targeted)
    assert not numbers_supported("약 12.6억원", (), targeted)
    assert not numbers_supported("약 12.5만원", (), targeted)


def test_targeted_reads_reject_a_tail_whose_left_glyph_went_unread(engine):
    executable, answers = engine
    words, image = _approx_line()
    lines = (RedesignLine((0, 1, 2, 3, 4), (40, 100, 210, 40), 40.0),)
    _answer_splits(answers, ["12", "12.", "12.5"], [("억원", 45, 56)])
    (targeted,) = guards.targeted_number_reads(
        image, words, lines, executable=executable, tessdata=executable.parent
    ).values()
    assert not numbers_supported("약 12.5억원", (), targeted)


def test_guard_numbers_keeps_a_native_table_whose_cells_carry_unit_words(engine):
    executable, answers = engine
    words, image = _approx_line()
    table = PlannedRegion("table", (30, 90, 230, 60), (0, 1, 2, 3, 4), None, table_cells=(("약", "12.5억원"),))
    _answer_splits(answers, ["12", "12.", "12.5"], [("억원", 17, 84)])
    (kept,) = guard_numbers(PagePlan((table,), ()), words, image, executable, executable.parent).regions
    assert kept.role == "table" and kept.table_cells is not None and not kept.editable_fallback
    _answer_splits(answers, ["12", "12.", "12.8"], [("억원", 17, 84)])
    (pixels,) = guard_numbers(PagePlan((table,), ()), words, image, executable, executable.parent).regions
    assert pixels.table_cells is None and pixels.editable_fallback


def test_a_split_read_withdraws_the_appended_digit_allowance_of_the_whole_read():
    # "2.0억원" read whole as "204": the appended-digit allowance alone would confirm "20억원".
    split = SplitRead(("2.0",), ("2.0",), "억원", "d.d")
    reads = NumberReads([UnitRead(_unit("2.0억원"), ("204",), ("204",), (split,))])
    assert numbers_supported("약 2.0억원", (), reads)
    assert not numbers_supported("약 20억원", (), reads)


def test_small_type_with_touching_glyphs_still_confirms_an_equal_head_read():
    # "1,284건" at 11 px: "84" is one ink run and the comma is a speck; the reads agree.
    assert numbers_supported("1,284건", (), _split_read(["1,284"], "건", "d?dd", text="1,284건"))
    assert not numbers_supported("1,284건", (), _split_read(["1284"], "건", "d?dd", text="1,284건"))


def test_glyph_shape_tells_points_from_commas():
    dot = [(100, 100, 20, 40), (124, 132, 6, 8), (134, 100, 20, 40)]
    comma = [(100, 100, 20, 40), (124, 132, 6, 16), (134, 100, 20, 40)]
    assert glyph_shape(_gray(_page(dot)), (100, 100, 54, 40)) == "d.d"
    assert glyph_shape(_gray(_page(comma)), (100, 100, 54, 40)) == "d,d"
