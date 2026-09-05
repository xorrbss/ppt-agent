"""Unit tests for utils.authored_deck_instruments (H4, authored-quality-speed
programme design section 6). Every test builds synthetic per-element metrics
matching utils/slide_capture.py's payload shape (bbox, computed_style,
font_size, image, has_owned_text) rather than driving a real browser.
"""

import json

from utils.authored_deck_instruments import (
    DeckInstruments,
    SlideInstrumentInput,
    SlideInstruments,
    compute_deck_instruments,
)


def _element(qid: str, **overrides) -> dict:
    base = {
        "qid": qid,
        "tag": "div",
        "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
        "computed_style": {},
        "has_owned_text": True,
        "font_size": 16,
    }
    base.update(overrides)
    return base


def _slide(index: int, elements: list[dict], html: str = "<html></html>", palette=()) -> SlideInstrumentInput:
    return SlideInstrumentInput(index=index, html=html, metrics={"elements": elements}, palette=palette)


def test_empty_band_px_measures_gap_to_the_content_line():
    element = _element("body", bbox={"x": 40, "y": 300, "width": 400, "height": 100})
    deck = compute_deck_instruments([_slide(0, [element])])

    assert deck.slides[0].empty_band_px == 248.0  # 648 - (300 + 100)


def test_content_inset_matches_the_single_element_envelope():
    element = _element("body", bbox={"x": 40, "y": 20, "width": 200, "height": 100})
    deck = compute_deck_instruments([_slide(0, [element])])

    inset = deck.slides[0].content_inset
    assert inset == {"left": 40.0, "top": 20.0, "right": 1040.0}


def test_missing_metrics_falls_back_to_safe_defaults():
    deck = compute_deck_instruments([_slide(0, [])])
    slide = deck.slides[0]

    assert slide.empty_band_px == 648.0
    assert slide.content_inset == {"left": 0.0, "top": 0.0, "right": 0.0}
    assert slide.palette_conformance is None
    assert slide.radius_families == ()
    assert slide.small_font_count == 0
    assert slide.stretched_image_count == 0


def test_palette_conformance_counts_matches_and_ignores_pure_white_and_transparent():
    element = _element(
        "a",
        computed_style={
            "color": "#2563EB",
            "backgroundColor": "#ffffff",
            "borderColor": "transparent",
        },
    )
    deck = compute_deck_instruments([_slide(0, [element], palette=["#2563EB"])])

    # Only `color` is a countable declaration (white/transparent are ignored),
    # and it matches the palette exactly (delta-E 0).
    assert deck.slides[0].palette_conformance == 1.0


def test_palette_conformance_is_a_fraction_when_only_some_colours_match():
    near = _element("a", computed_style={"color": "#2563EB"})
    far = _element("b", computed_style={"color": "#FF00AA"})
    deck = compute_deck_instruments([_slide(0, [near, far], palette=["#2563EB"])])

    assert deck.slides[0].palette_conformance == 0.5


def test_decoration_role_element_is_excluded_from_content_and_colour_but_not_stretch():
    html = '<div data-qid="deco" data-semantic-role="decoration"></div>'
    owned = _element("body", bbox={"x": 10, "y": 10, "width": 100, "height": 20})
    decoration = _element(
        "deco",
        bbox={"x": 900, "y": 600, "width": 300, "height": 300},
        computed_style={"color": "#FF00AA"},
        image={"naturalWidth": 200, "naturalHeight": 100},
    )
    deck = compute_deck_instruments([_slide(0, [owned, decoration], html=html, palette=["#2563EB"])])
    slide = deck.slides[0]

    # The decoration element's huge bbox never widens the content envelope.
    assert slide.content_inset == {"left": 10.0, "top": 10.0, "right": 1170.0}
    # Its colour never enters the palette-conformance denominator.
    assert slide.palette_conformance is None
    # But it is still counted for a stretched-image defect.
    assert slide.stretched_image_count == 1


def test_radius_families_collapses_duplicates_and_ignores_zero():
    element = _element(
        "a",
        computed_style={
            "borderTopLeftRadius": "8px",
            "borderBottomRightRadius": "8px",
            "borderTopRightRadius": "0px",
        },
    )
    other = _element("b", computed_style={"borderTopLeftRadius": "16px"})
    deck = compute_deck_instruments([_slide(0, [element, other])])

    assert deck.slides[0].radius_families == (8.0, 16.0)
    assert deck.radius_family_count == 2


def test_small_font_count_only_counts_owned_text_below_the_floor():
    tiny = _element("a", font_size=10, has_owned_text=True)
    normal = _element("b", font_size=14, has_owned_text=True)
    tiny_but_unowned = _element("c", font_size=8, has_owned_text=False)
    deck = compute_deck_instruments([_slide(0, [tiny, normal, tiny_but_unowned])])

    assert deck.slides[0].small_font_count == 1


def test_stretched_image_count_requires_both_aspect_mismatch_and_fit():
    stretched = _element(
        "img1",
        bbox={"x": 0, "y": 0, "width": 100, "height": 100},
        image={"naturalWidth": 200, "naturalHeight": 100},
        computed_style={"objectFit": ""},
    )
    covered = _element(
        "img2",
        bbox={"x": 0, "y": 0, "width": 100, "height": 100},
        image={"naturalWidth": 200, "naturalHeight": 100},
        computed_style={"objectFit": "cover"},
    )
    deck = compute_deck_instruments([_slide(0, [stretched, covered])])

    assert deck.slides[0].stretched_image_count == 1


def test_margin_deviation_px_is_measured_against_the_deck_median_inset():
    slide_a = _slide(0, [_element("a", bbox={"x": 10, "y": 10, "width": 100, "height": 20})])
    slide_b = _slide(1, [_element("b", bbox={"x": 50, "y": 10, "width": 100, "height": 20})])
    slide_c = _slide(2, [_element("c", bbox={"x": 30, "y": 10, "width": 100, "height": 20})])
    deck = compute_deck_instruments([slide_a, slide_b, slide_c])

    # Median left inset is 30 (from slide_c); slide_a and slide_b each deviate
    # by 20px on the left, with top/right identical across all three slides.
    by_index = {slide.index: slide for slide in deck.slides}
    assert by_index[0].margin_deviation_px == 20.0
    assert by_index[1].margin_deviation_px == 20.0
    assert by_index[2].margin_deviation_px == 0.0


def test_deck_level_medians_ignore_slides_with_no_palette_conformance():
    with_palette = _slide(0, [_element("a", computed_style={"color": "#2563EB"})], palette=["#2563EB"])
    without_palette = _slide(1, [_element("b")])
    deck = compute_deck_instruments([with_palette, without_palette])

    assert deck.median_palette_conformance == 1.0


def test_to_receipt_is_a_plain_json_able_dict():
    deck = compute_deck_instruments(
        [_slide(0, [_element("a", bbox={"x": 10, "y": 10, "width": 50, "height": 50})])]
    )

    receipt = deck.to_receipt()
    assert isinstance(receipt, dict)
    json.dumps(receipt)  # must not raise


def test_public_dataclasses_are_the_documented_shape():
    slide_input = SlideInstrumentInput(index=0, html="<html></html>", metrics={"elements": []})
    assert slide_input.palette == ()

    deck = compute_deck_instruments([slide_input])
    assert isinstance(deck, DeckInstruments)
    assert isinstance(deck.slides[0], SlideInstruments)
