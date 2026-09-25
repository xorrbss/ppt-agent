"""Ink coverage: visible source text must never be silently dropped (footer bands, faint text, numbers)."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from services.source_content.raster_ink_coverage import uncovered_ink

DARK, FAINT = (15, 17, 20), (92, 94, 98)


def _dark_page():
    return Image.new("RGB", (1920, 1080), DARK)


def _text(image, xy, value, size, fill):
    ImageDraw.Draw(image).text(xy, value, font=ImageFont.load_default(size=size), fill=fill)


def test_small_faint_text_on_a_dark_page_is_ink():
    # Grey 12 px footer text: its strokes are one source pixel wide, so a
    # half-scale average dilutes them below the ink level.
    image = _dark_page()
    _text(image, (90, 1000), "Illustrative template data - editable PPTX", 13, FAINT)
    found = uncovered_ink(image, [], [], [])
    assert len(found) == 1 and found[0][0] < 100 and found[0][1] < 1010 < found[0][1] + found[0][3]
    assert uncovered_ink(image, [(80, 990, 400, 40)], [], []) == []


def test_text_in_the_footer_band_is_kept_even_without_an_ocr_word():
    image = _dark_page()
    _text(image, (90, 1052), "Illustrative template data - editable PPTX", 13, FAINT)
    _text(image, (1780, 1052), "09 / 15", 13, FAINT)
    assert len(uncovered_ink(image, [], [], [])) == 2


def test_footer_band_pictogram_without_words_is_template_decoration():
    image = _dark_page()
    ImageDraw.Draw(image).ellipse((1800, 1050, 1830, 1076), fill=(200, 60, 60))
    assert uncovered_ink(image, [], [], []) == []


def test_two_digit_number_is_content_not_a_pictogram():
    image = _dark_page()
    _text(image, (1470, 470), "82", 34, (240, 160, 96))
    found = uncovered_ink(image, [], [], [])
    assert len(found) == 1 and found[0][0] <= 1472 and found[0][0] + found[0][2] >= 1500


def test_single_blob_icon_stays_optional_decoration():
    image = Image.new("RGB", (800, 450), (246, 248, 252))
    ImageDraw.Draw(image).ellipse((300, 200, 340, 240), fill=(20, 90, 220))
    assert uncovered_ink(image, [], [], []) == []


def test_faint_card_outline_around_covered_text_is_not_ink():
    image = Image.new("RGB", (1672, 941), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((100, 200, 700, 520), radius=14, outline=(150, 190, 235), width=2)
    _text(image, (140, 260), "covered card text", 28, (20, 40, 80))
    assert uncovered_ink(image, [(130, 250, 320, 50)], [], []) == []


def test_solid_colour_bar_is_not_a_text_line():
    image = _dark_page()
    ImageDraw.Draw(image).rectangle((86, 300, 664, 308), fill=(230, 57, 70))  # a card's accent bar
    assert uncovered_ink(image, [], [], []) == []


def test_small_text_with_touching_glyphs_in_the_footer_band_is_kept():
    image = _dark_page()
    _text(image, (1784, 1062), "09 / 15", 12, FAINT)
    found = uncovered_ink(image, [], [], [])
    assert len(found) == 1 and found[0][1] >= 1050
