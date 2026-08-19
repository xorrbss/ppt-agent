"""Map a header-contract change onto CSS declarations and structural verdicts.

Splitting this from the patch application keeps one question in one place: given
two contracts, what exactly changed, and can a local style edit express it?
"""

from __future__ import annotations

from models.authored_header_contract import AuthoredHeaderContract


def _px(value: int) -> str:
    return f"{value}px"


def structural_changes(
    old: AuthoredHeaderContract,
    new: AuthoredHeaderContract,
) -> tuple[str, ...]:
    """Return the contract changes that a local patch cannot express."""
    changes: list[str] = []

    def note(condition: bool, name: str) -> None:
        if condition:
            changes.append(name)

    # The band is derived per slide, so every term of the derivation is beyond a
    # local patch, and for two independent reasons. First, a new band has to be
    # re-declared on the band root, and the patch vocabulary has no action that
    # writes an attribute — setStyle/move/resize/crop all write CSS — so a patched
    # slide would carry a height contradicting its own data-header-band and fail
    # validation. Second, the new height depends on how many title lines that
    # slide rendered, which restyling never measures because it never renders.
    #
    # That covers the gaps AND the type: a term of the derivation is a term of the
    # derivation whether it is spelled as a gap or as a font size. Raising the
    # title from 34px to 56px grows a two-line title block by 58px under a band
    # root still declaring and styling the old height, and every number the
    # validator can see still agrees — the only evidence is the title crossing
    # the divider, which nothing here renders to find out.
    note(old.header_top_px != new.header_top_px, "headerTopPx")
    note(old.eyebrow_gap_px != new.eyebrow_gap_px, "eyebrowGapPx")
    note(old.subline_gap_px != new.subline_gap_px, "sublineGapPx")
    note(old.title_gap_px != new.title_gap_px, "titleGapPx")
    # contentGapPx alone leaves the band untouched, and the body root did become
    # addressable when it gained a marker — but it is still not patchable: the
    # offset is spelled either as the body's own margin-top or as an absolute top
    # of band+gap, and which one a slide used is not knowable from the two
    # contracts this function compares. Writing the wrong spelling moves nothing
    # while reporting success. Every slide published before the marker existed
    # has no body qid at all, so a patchable verdict would silently no-op on
    # exactly the decks that need it.
    note(old.content_gap_px != new.content_gap_px, "contentGapPx")
    note(old.band_min_px != new.band_min_px, "bandMinPx")
    note(old.band_max_px != new.band_max_px, "bandMaxPx")
    note(old.margin_x_px != new.margin_x_px, "marginXPx")
    note(old.eyebrow.enabled != new.eyebrow.enabled, "eyebrow.enabled")
    note(old.eyebrow.tick.enabled != new.eyebrow.tick.enabled, "eyebrow.tick.enabled")
    note(old.eyebrow.max_chars != new.eyebrow.max_chars, "eyebrow.maxChars")
    # The eyebrow's wording, not its styling: changing it means rewriting text.
    note(old.eyebrow.text_style != new.eyebrow.text_style, "eyebrow.textStyle")
    note(
        old.eyebrow.font_size_px != new.eyebrow.font_size_px,
        "eyebrow.fontSizePx",
    )
    note(old.title.max_lines != new.title.max_lines, "title.maxLines")
    note(old.title.font_size_px != new.title.font_size_px, "title.fontSizePx")
    note(old.title.line_height != new.title.line_height, "title.lineHeight")
    # Not a term of the sum, but the input to the one term the sum cannot state:
    # a narrower column wraps the same title onto another line.
    note(old.title.max_width_px != new.title.max_width_px, "title.maxWidthPx")
    note(old.subline.enabled != new.subline.enabled, "subline.enabled")
    note(old.subline.max_lines != new.subline.max_lines, "subline.maxLines")
    note(
        new.subline.enabled
        and old.subline.font_size_px != new.subline.font_size_px,
        "subline.fontSizePx",
    )
    note(old.right_slot.mode != new.right_slot.mode, "rightSlot.mode")
    note(
        old.right_slot.summary_max_lines != new.right_slot.summary_max_lines,
        "rightSlot.summaryMaxLines",
    )
    note(
        new.right_slot.mode in {"summary", "requirementsAndSummary"}
        and old.right_slot.summary_font_size_px
        != new.right_slot.summary_font_size_px,
        "rightSlot.summaryFontSizePx",
    )
    note(old.requirements.enabled != new.requirements.enabled, "requirements.enabled")
    # Chip height is the right block's own line height, and the gap is what sits
    # between its rows when the group wraps.
    note(
        new.requirements.enabled
        and old.requirements.height_px != new.requirements.height_px,
        "requirements.heightPx",
    )
    note(
        new.requirements.enabled and old.requirements.gap_px != new.requirements.gap_px,
        "requirements.gapPx",
    )
    note(old.requirements.style != new.requirements.style, "requirements.style")
    note(old.requirements.label != new.requirements.label, "requirements.label")
    note(
        old.requirements.max_visible != new.requirements.max_visible,
        "requirements.maxVisible",
    )
    note(old.requirements.overflow != new.requirements.overflow, "requirements.overflow")
    # Numbering adds or removes a footer element on every slide, and the patch
    # engine can only restyle elements that already exist.
    note(
        old.page_number.enabled != new.page_number.enabled,
        "pageNumber.enabled",
    )
    note(old.divider.enabled != new.divider.enabled, "divider.enabled")
    note(old.divider.width != new.divider.width, "divider.width")
    note(tuple(old.excluded_roles) != tuple(new.excluded_roles), "excludedRoles")
    return tuple(changes)


def part_styles(
    old: AuthoredHeaderContract,
    new: AuthoredHeaderContract,
) -> dict[str, dict[str, str]]:
    """Style declarations per header part, for the properties that changed.

    Only properties that leave the band's arithmetic alone appear here: colour,
    weight, letter-spacing, and the slot's width. Anything that changes how tall
    a rendered row is — every font size, the title's line-height and its wrapping
    width, the chip height and the gap between chip rows — is reported by
    ``structural_changes`` and deliberately left unwritten, because writing it
    would move the type inside a band root still declaring the old number.
    """
    styles: dict[str, dict[str, str]] = {}

    def put(part: str, prop: str, before: object, after: object, value: str) -> None:
        if before != after:
            styles.setdefault(part, {})[prop] = value

    tick_old, tick_new = old.eyebrow.tick, new.eyebrow.tick
    put("tick", "background-color", tick_old.color, tick_new.color, tick_new.color)
    put(
        "tick", "margin-right", tick_old.gap_px, tick_new.gap_px, _px(tick_new.gap_px)
    )

    eyebrow_old, eyebrow_new = old.eyebrow, new.eyebrow
    put("eyebrow", "color", eyebrow_old.color, eyebrow_new.color, eyebrow_new.color)
    put(
        "eyebrow",
        "font-weight",
        eyebrow_old.weight,
        eyebrow_new.weight,
        str(eyebrow_new.weight),
    )
    put(
        "eyebrow",
        "letter-spacing",
        eyebrow_old.letter_spacing_em,
        eyebrow_new.letter_spacing_em,
        f"{eyebrow_new.letter_spacing_em}em",
    )

    title_old, title_new = old.title, new.title
    put("title", "color", title_old.color, title_new.color, title_new.color)
    put("title", "font-weight", title_old.weight, title_new.weight, str(title_new.weight))

    subline_old, subline_new = old.subline, new.subline
    if new.subline.enabled:
        put("subline", "color", subline_old.color, subline_new.color, subline_new.color)
        put(
            "subline",
            "font-weight",
            subline_old.weight,
            subline_new.weight,
            str(subline_new.weight),
        )

    slot_old, slot_new = old.right_slot, new.right_slot
    put("right", "width", slot_old.width_px, slot_new.width_px, _px(slot_new.width_px))
    if slot_new.mode in {"summary", "requirementsAndSummary"}:
        put(
            "right",
            "color",
            slot_old.summary_color,
            slot_new.summary_color,
            slot_new.summary_color,
        )

    divider_old, divider_new = old.divider, new.divider
    put(
        "divider",
        "background-color",
        divider_old.color,
        divider_new.color,
        divider_new.color,
    )
    return styles


def chip_styles(
    old: AuthoredHeaderContract,
    new: AuthoredHeaderContract,
) -> dict[str, str]:
    """Style declarations shared by every requirement chip, when they changed."""
    if not new.requirements.enabled or new.requirements.style != "chip":
        return {}
    before, after = old.requirements, new.requirements
    styles: dict[str, str] = {}
    if before.background_color != after.background_color:
        styles["background-color"] = after.background_color
    if before.border_color != after.border_color:
        styles["border-color"] = after.border_color
    if before.radius_px != after.radius_px:
        styles["border-radius"] = _px(after.radius_px)
    if before.text_color != after.text_color:
        styles["color"] = after.text_color
    if before.weight != after.weight:
        styles["font-weight"] = str(after.weight)
    if before.padding_x_px != after.padding_x_px:
        styles["padding-left"] = _px(after.padding_x_px)
        styles["padding-right"] = _px(after.padding_x_px)
    return styles


__all__ = ["chip_styles", "part_styles", "structural_changes"]
