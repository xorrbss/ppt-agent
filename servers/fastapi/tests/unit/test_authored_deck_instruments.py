"""Unit tests for utils.authored_deck_instruments (H4, authored-quality-speed
programme design section 6). Every test builds synthetic per-element metrics
matching utils/slide_capture.py's payload shape (bbox, computed_style,
font_size, image, has_owned_text) rather than driving a real browser -- except
the footer-advisory tests, which replay the real bench run 33 payload.
"""

import json
from pathlib import Path

from utils.authored_deck_instruments import (
    FOOTER_INTRUSION_EXTREME_PX,
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


# ---------------------------------------------------------------------------
# The footer-zone advisory record
#
# ``text_intrudes_footer_zone`` is detected but does not block publication
# (``utils/authored_dom_categories.py``), so the receipt is the only place the
# calibration data survives. These tests pin that it gets there in px, with the
# offending qid, from the browser payload a deck actually publishes.
# ---------------------------------------------------------------------------

_TEXT_ESCAPE_FIXTURES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "authored_dom_text_escape"
)


def _bench_metrics(name: str) -> dict:
    return json.loads(
        (_TEXT_ESCAPE_FIXTURES / f"{name}.metrics.json").read_text(encoding="utf-8")
    )


def test_footer_intrusions_record_px_and_qid_from_the_run33_payload():
    metrics = _bench_metrics("run33_slide02_footer_zone")
    deck = compute_deck_instruments(
        [SlideInstrumentInput(index=0, html="<div></div>", metrics=metrics)]
    )

    by_qid = {entry["qid"]: entry for entry in deck.slides[0].footer_intrusions}
    assert set(by_qid) == {"s2-close", "s2-word"}
    # The real 57px intrusion, in px, with the owner that caused it.
    assert by_qid["s2-close"]["intrusion_px"] == 57.0
    assert by_qid["s2-word"]["intrusion_px"] == 47.0
    assert by_qid["s2-close"]["content_bottom_px"] == 648.0
    assert by_qid["s2-close"]["intersects_page_number"] is True
    # Below the extreme mark, so the record stays plainly advisory.
    assert {entry["severity"] for entry in by_qid.values()} == {"advisory"}


def test_footer_intrusion_aggregates_carry_the_deck_distribution():
    metrics = _bench_metrics("run33_slide02_footer_zone")
    clean = _bench_metrics("run33_slide03_footer_zone_clean")
    deck = compute_deck_instruments(
        [
            SlideInstrumentInput(index=0, html="<div></div>", metrics=metrics),
            SlideInstrumentInput(index=1, html="<div></div>", metrics=clean),
        ]
    )

    assert deck.slides[1].footer_intrusions == ()
    assert deck.footer_intrusion_count == 2
    assert deck.footer_intrusion_slide_count == 1
    # Sorted, so "the distribution on this deck" is readable straight from the
    # stored receipt without reassembling the per-slide lists.
    assert deck.footer_intrusion_px == (47.0, 57.0)
    assert deck.median_footer_intrusion_px == 52.0


def test_a_deck_with_no_footer_intrusion_records_an_empty_distribution():
    deck = compute_deck_instruments([_slide(0, [_element("a")])])

    assert deck.slides[0].footer_intrusions == ()
    assert deck.footer_intrusion_count == 0
    assert deck.footer_intrusion_slide_count == 0
    assert deck.footer_intrusion_px == ()
    assert deck.median_footer_intrusion_px is None


def test_an_extreme_intrusion_is_marked_high_without_gating_anything():
    """The one branch-only signal the replay found: 3 of 57 occurrences above
    90px, none on the deck the owner preferred. It marks the record only."""

    page_number = {
        "qid": "page",
        "tag": "div",
        "bbox": {"x": 0.0, "y": 690.0, "width": 1280.0, "height": 16.0},
        "font_size": 12.0,
        "rendered_text": "03",
        "text_fragments": [{"x": 600.0, "y": 689.0, "width": 20.0, "height": 17.0}],
    }
    owner = {
        "qid": "runaway",
        "tag": "p",
        "bbox": {"x": 100.0, "y": 714.0, "width": 200.0, "height": 26.0},
        "font_size": 26.0,
        "text_fragments": [{"x": 100.0, "y": 714.0, "width": 200.0, "height": 26.0}],
    }
    deck = compute_deck_instruments([_slide(0, [page_number, owner])])

    [entry] = deck.slides[0].footer_intrusions
    assert entry["qid"] == "runaway"
    assert entry["intrusion_px"] >= FOOTER_INTRUSION_EXTREME_PX
    assert entry["severity"] == "high"


def test_footer_intrusions_survive_the_receipt_serialization():
    metrics = _bench_metrics("run33_slide02_footer_zone")
    receipt = compute_deck_instruments(
        [SlideInstrumentInput(index=0, html="<div></div>", metrics=metrics)]
    ).to_receipt()

    # ``asdict`` keeps tuples as tuples (so does ``radius_families``); JSON
    # serializes both as arrays, which is what the stored receipt carries.
    json.dumps(receipt)  # must not raise
    assert list(receipt["footer_intrusion_px"]) == [47.0, 57.0]
    assert json.loads(json.dumps(receipt))["footer_intrusion_px"] == [47.0, 57.0]
    assert receipt["slides"][0]["footer_intrusions"][0]["qid"] in {
        "s2-close",
        "s2-word",
    }


# --------------------------------------------------------------------------
# In-render fit aggregates (docs/authored-in-render-fit-design-20260906.md
# section 6). Two synthetic reports shaped like the fitter's own report
# (section 4): one card grown, one shrunk, one unfixable, plus a top-level
# error and a reconstruction mismatch on the other slide.
# --------------------------------------------------------------------------


def _fit_slide(index: int, fit_report) -> SlideInstrumentInput:
    return SlideInstrumentInput(
        index=index, html="<html></html>", metrics={"elements": []}, fit_report=fit_report
    )


def test_fit_aggregates_count_outcomes_across_the_deck():
    report_a = {
        "error": None,
        "cards": [
            {"qid": "s1-a", "outcome": "grown", "factor": 1.0, "escape_px": 21.0},
            {"qid": "s1-b", "outcome": "shrunk", "factor": 0.833, "escape_px": 52.0},
        ],
    }
    report_b = {
        "error": None,
        "reconstruction_mismatch": True,
        "cards": [
            {"qid": "s2-a", "outcome": "unfixable:floor", "factor": 0.75, "escape_px": 5.0},
        ],
    }
    deck = compute_deck_instruments([_fit_slide(0, report_a), _fit_slide(1, report_b)])

    assert deck.fit_cards_grown == 1
    assert deck.fit_cards_shrunk == 1
    assert deck.fit_cards_unfixable == 1
    assert deck.fit_errors == 0
    assert deck.fit_reconstruction_mismatches == 1
    assert deck.fit_factor_min == 0.75
    assert deck.fit_escape_px == (5.0, 21.0, 52.0)


def test_fit_aggregates_count_an_error_and_stay_zero_with_no_reports():
    deck_with_error = compute_deck_instruments(
        [_fit_slide(0, {"error": "TypeError: boom", "cards": []})]
    )
    assert deck_with_error.fit_errors == 1
    assert deck_with_error.fit_cards_grown == 0
    assert deck_with_error.fit_factor_min is None
    assert deck_with_error.fit_escape_px == ()

    deck_with_none = compute_deck_instruments([_slide(0, [_element("a")])])
    assert deck_with_none.fit_cards_grown == 0
    assert deck_with_none.fit_cards_shrunk == 0
    assert deck_with_none.fit_cards_unfixable == 0
    assert deck_with_none.fit_errors == 0
    assert deck_with_none.fit_reconstruction_mismatches == 0
    assert deck_with_none.fit_factor_min is None
    assert deck_with_none.fit_escape_px == ()


def test_fit_aggregates_survive_the_receipt_serialization():
    report = {
        "error": None,
        "cards": [{"qid": "a", "outcome": "shrunk", "factor": 0.9, "escape_px": 3.0}],
    }
    receipt = compute_deck_instruments([_fit_slide(0, report)]).to_receipt()

    json.dumps(receipt)  # must not raise
    assert receipt["fit_cards_shrunk"] == 1
    assert list(receipt["fit_escape_px"]) == [3.0]
