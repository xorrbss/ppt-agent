"""Deck-wide header restyling: what a local patch can express, and what it cannot."""

from models.authored_header_contract import (
    HeaderDividerSpec,
    HeaderEyebrowSpec,
    HeaderRequirementSpec,
    HeaderRightSlotSpec,
    HeaderSublineSpec,
    HeaderTickSpec,
    HeaderTitleSpec,
    default_header_contract,
)
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_header_restyle import (
    apply_header_restyle,
    build_restyle_actions,
    header_qids,
    structural_changes,
)


def _chips(*identifiers: str) -> str:
    return "".join(
        f'<span style="background-color:#EEF3FA;height:22px">{value}</span>'
        for value in identifiers
    )


def _slide(*identifiers: str) -> str:
    chips = _chips(*(identifiers or ("POR-005", "POR-012")))
    # 180px is what this contract derives for the two-line title below: 32 + (18
    # + 12 + 2x45) + 28. The slide declares it as well as writing it, because a
    # restyle that leaves the two disagreeing is exactly what must not happen.
    return ensure_authored_qids(
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div data-header-part="band" data-header-band="180" style="height:180px">'
        '<span data-header-part="tick" style="width:24px;height:3px;'
        'background-color:#2563EB;margin-right:12px"></span>'
        '<span data-header-part="eyebrow" style="color:#2563EB;font-size:13px">'
        "대외 연계</span>"
        '<h1 data-header-part="title" style="color:#0F172A;font-size:34px;'
        'max-width:780px">단일 거래 흐름</h1>'
        '<div data-header-part="right" style="width:336px">'
        '<div data-header-part="requirements" data-requirement-ids="POR-005,POR-012" '
        'style="gap:6px">'
        f"{chips}"
        "</div></div>"
        '<div data-header-part="divider" style="height:1px;'
        'background-color:#E2E8F0"></div>'
        "</div>"
        '<div data-header-part="body" style="margin-top:20px">본문</div>'
        "</body></html>"
    )


def _with_requirements():
    return default_header_contract().model_copy(
        update={
            "requirements": HeaderRequirementSpec(enabled=True),
            "right_slot": HeaderRightSlotSpec(mode="requirements"),
        }
    )


# --- locating the header ------------------------------------------------------


def test_every_header_part_and_chip_is_addressable():
    qids = header_qids(_slide())
    assert set(qids.parts) == {
        "band",
        "tick",
        "eyebrow",
        "title",
        "right",
        "requirements",
        "divider",
        # The body root the band pushes down is addressable too, now that it
        # carries a marker of its own.
        "body",
    }
    assert len(qids.chips) == 2
    assert len(set(qids.chips)) == 2


def test_a_static_label_in_the_group_is_not_treated_as_a_chip():
    """A restyled label would turn the group's caption into a fake identifier."""
    html = ensure_authored_qids(
        _slide().replace(
            '<div data-header-part="requirements"',
            '<div data-header-part="requirements"',
        ).replace(
            "<span style=\"background-color:#EEF3FA;height:22px\">POR-005</span>",
            '<span class="label">요구사항</span>'
            '<span style="background-color:#EEF3FA;height:22px">POR-005</span>',
        )
    )
    qids = header_qids(html)
    assert len(qids.chips) == 2


def test_an_overflow_chip_still_counts_as_a_chip():
    html = _slide("POR-005", "+4")
    assert len(header_qids(html).chips) == 2


def test_a_void_element_inside_a_chip_does_not_capture_the_rest_of_the_slide():
    """An <img> HTML never closes must not leave the requirement group open.

    With the group still open at the divider, every element after it is offered
    as a chip candidate, and a restyle then writes chip colours onto the body.
    """
    html = ensure_authored_qids(
        _slide().replace(
            '<span style="background-color:#EEF3FA;height:22px">POR-005</span>',
            '<span style="background-color:#EEF3FA;height:22px">'
            '<img src="status.svg">POR-005</span>',
        )
    )
    qids = header_qids(html)

    assert len(qids.chips) == 2
    assert set(qids.parts) >= {"divider", "body"}


def test_a_slide_without_markers_yields_nothing_to_patch():
    qids = header_qids("<!doctype html><html><body><h1>제목</h1></body></html>")
    assert qids.parts == {}
    assert qids.chips == ()


# --- structural vs. style-level changes ---------------------------------------


def test_style_level_changes_are_not_structural():
    old = _with_requirements()
    new = old.model_copy(update={"title": HeaderTitleSpec(color="#111827")})
    assert structural_changes(old, new) == ()


def test_toggling_a_part_is_structural():
    old = _with_requirements()
    new = old.model_copy(update={"subline": HeaderSublineSpec(enabled=True)})
    assert "subline.enabled" in structural_changes(old, new)


def test_every_term_of_the_band_derivation_is_structural():
    """No term of the derivation survives a patch, and for two separate reasons.

    A new band has to be re-declared on the band root, and the patch vocabulary
    has no action that writes an attribute; and the new height depends on how
    many lines that slide's title took, which restyling never measures because it
    never renders.
    """
    old = _with_requirements()
    for field, value, reported in (
        ("header_top_px", 40, "headerTopPx"),
        ("eyebrow_gap_px", 16, "eyebrowGapPx"),
        ("subline_gap_px", 12, "sublineGapPx"),
        ("title_gap_px", 36, "titleGapPx"),
        ("content_gap_px", 28, "contentGapPx"),
        ("band_min_px", 120, "bandMinPx"),
        ("band_max_px", 240, "bandMaxPx"),
    ):
        new = old.model_copy(update={field: value})
        assert structural_changes(old, new) == (reported,)


def test_a_gap_change_moves_nothing_rather_than_half_moving_it():
    """contentGapPx is the one gap whose element a patch can now reach.

    It still may not be patched: the offset is spelled either as the body's own
    margin-top or as an absolute top of band+gap, and which one a slide used is
    not knowable from the two contracts alone — so writing the wrong spelling
    would report success while moving nothing.
    """
    old = _with_requirements()
    new = old.model_copy(update={"content_gap_px": 28})

    assert build_restyle_actions(old, new, header_qids(_slide())) == []
    result = apply_header_restyle(_slide(), old, new)
    assert result.changed is False
    assert "contentGapPx" in result.structural


# --- applying a restyle --------------------------------------------------------


def test_a_colour_change_rewrites_every_marked_part():
    old = _with_requirements()
    new = old.model_copy(
        update={
            "eyebrow": HeaderEyebrowSpec(
                color="#B91C1C", tick=HeaderTickSpec(color="#B91C1C")
            )
        }
    )
    result = apply_header_restyle(_slide(), old, new)

    assert result.changed is True
    assert result.failure_reason is None
    assert result.structural == ()
    assert result.html.count("#B91C1C") == 2
    # Slide content is untouched.
    assert "단일 거래 흐름" in result.html


def test_a_title_colour_change_updates_only_the_title():
    old = _with_requirements()
    new = old.model_copy(update={"title": HeaderTitleSpec(color="#111827")})
    result = apply_header_restyle(_slide(), old, new)

    assert result.changed is True
    compact = result.html.replace(" ", "")
    assert "color:#111827" in compact
    assert "color:#2563EB" in compact


def test_type_that_changes_a_row_height_is_reported_rather_than_written():
    """A font size is a term of the band, whichever part it belongs to.

    Raising the title to 56px grows a two-line title block by 58px under a band
    root still declaring 180 and still styled 180px: the title crosses the
    divider and the body overlaps it, while every number the validator can read
    still agrees with every other. The size the deck's own line-height and
    wrapping width feed is beyond a patch for exactly that reason.
    """
    old = _with_requirements().model_copy(
        update={"subline": HeaderSublineSpec(enabled=True)}
    )
    for field, spec, reported in (
        ("title", HeaderTitleSpec(font_size_px=56), "title.fontSizePx"),
        ("title", HeaderTitleSpec(line_height=2.0), "title.lineHeight"),
        ("title", HeaderTitleSpec(max_width_px=520), "title.maxWidthPx"),
        ("eyebrow", HeaderEyebrowSpec(font_size_px=18), "eyebrow.fontSizePx"),
        (
            "subline",
            HeaderSublineSpec(enabled=True, font_size_px=20),
            "subline.fontSizePx",
        ),
    ):
        new = old.model_copy(update={field: spec})
        assert structural_changes(old, new) == (reported,)
        result = apply_header_restyle(_slide(), old, new)
        assert result.changed is False


def test_chip_geometry_is_reported_rather_than_written():
    old = _with_requirements()
    for update, reported in (
        ({"height_px": 26}, "requirements.heightPx"),
        ({"gap_px": 12}, "requirements.gapPx"),
    ):
        new = old.model_copy(
            update={"requirements": old.requirements.model_copy(update=update)}
        )
        assert structural_changes(old, new) == (reported,)
        assert build_restyle_actions(old, new, header_qids(_slide())) == []


def test_chip_appearance_changes_reach_every_chip():
    old = _with_requirements()
    new = old.model_copy(
        update={
            "requirements": old.requirements.model_copy(
                update={"background_color": "#FEF3C7", "radius_px": 8}
            )
        }
    )
    result = apply_header_restyle(_slide(), old, new)

    assert result.changed is True
    assert result.html.count("#FEF3C7") == 2
    assert result.html.replace(" ", "").count("border-radius:8px") == 2


def test_divider_thickness_is_applied_as_a_resize():
    old = _with_requirements()
    new = old.model_copy(update={"divider": HeaderDividerSpec(thickness_px=3)})
    actions = build_restyle_actions(old, new, header_qids(_slide()))

    assert [action["action"] for action in actions] == ["resize"]
    assert actions[0]["height"] == "3px"


def test_an_identical_contract_changes_nothing():
    contract = _with_requirements()
    result = apply_header_restyle(_slide(), contract, contract)

    assert result.changed is False
    assert result.failure_reason is None


def test_an_unmarked_slide_is_left_alone_and_reports_no_change():
    old = _with_requirements()
    new = old.model_copy(update={"title": HeaderTitleSpec(font_size_px=40)})
    html = "<!doctype html><html><body><h1>제목</h1></body></html>"
    result = apply_header_restyle(html, old, new)

    assert result.changed is False
    assert result.html == html


def test_a_structural_change_is_reported_even_when_styles_also_apply():
    old = _with_requirements()
    new = old.model_copy(
        update={
            "title_gap_px": 36,
            "title": HeaderTitleSpec(color="#111827"),
        }
    )
    result = apply_header_restyle(_slide(), old, new)

    assert "titleGapPx" in result.structural
    assert result.changed is True
    # The band the slide declared is untouched by the half that did apply, so it
    # still agrees with the height beside it.
    assert 'data-header-band="180"' in result.html
    assert "height:180px" in result.html.replace(" ", "")


def test_a_large_restyle_is_applied_in_full_across_plan_chunks():
    old = _with_requirements()
    new = old.model_copy(
        update={
            "eyebrow": HeaderEyebrowSpec(
                color="#B91C1C",
                font_size_px=15,
                weight=800,
                letter_spacing_em=0.1,
                tick=HeaderTickSpec(color="#B91C1C", width_px=32, height_px=4, gap_px=16),
            ),
            "title": HeaderTitleSpec(weight=900, color="#111827"),
            "divider": HeaderDividerSpec(thickness_px=2, color="#CBD5E1"),
            "requirements": old.requirements.model_copy(
                update={
                    "background_color": "#FEF3C7",
                    "border_color": "#FCD34D",
                    "text_color": "#92400E",
                    "radius_px": 8,
                    "padding_x_px": 14,
                    "weight": 800,
                }
            ),
        }
    )
    # Fourteen identifiers push the action count past one plan's 16-action cap.
    html = _slide(*(f"POR-{index:03d}" for index in range(14)))
    actions = build_restyle_actions(old, new, header_qids(html))
    assert len(actions) > 16

    result = apply_header_restyle(html, old, new)
    assert result.changed is True
    assert result.failure_reason is None
    compact = result.html.replace(" ", "")
    assert "font-weight:900" in compact
    assert compact.count("#FEF3C7") == 14
    assert compact.count("border-radius:8px") == 14
    assert "background-color:#CBD5E1" in compact
