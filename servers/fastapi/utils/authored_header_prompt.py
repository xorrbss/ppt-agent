"""Deterministic rendering of a header contract into authoring prompt text.

``build_design_system`` freezes this text once per deck and injects the identical
string into every slide's prompt, which is what makes the top of a deck uniform.
Rendering must therefore stay a pure function of the contract: the same contract
always produces the same bytes.

Every rule stated here has a counterpart in ``authored_header_validation``. That
pairing is the point: the defect this module was rewritten for was a brief that
permitted an omitted subline while the validator rejected it, so the model was
re-authored for obeying its own instructions. Nothing may be asked for here that
the validator would report, and nothing the validator reports may be left unsaid.
"""

from __future__ import annotations

from models.authored_header_contract import (
    HEADER_BAND_ATTRIBUTE,
    SLIDE_WIDTH_PX,
    AuthoredHeaderContract,
    label_row_px as _label_row_px,
)


def _px(value: int) -> str:
    return f"{value}px"


def _tick_line(contract: AuthoredHeaderContract) -> str:
    tick = contract.eyebrow.tick
    if not tick.enabled:
        return (
            "  - Accent tick: this deck has none. Never draw a rule, bar, or dot "
            "before the eyebrow."
        )
    return (
        '  - Accent tick: one real <span data-header-part="tick"> of exactly '
        f"{tick.width_px}x{tick.height_px}px filled {tick.color}, {tick.gap_px}px "
        "before the eyebrow text, vertically centred on it. Never a full-width "
        "rule and never a gradient bar."
    )


def _eyebrow_lines(
    contract: AuthoredHeaderContract,
    language: str,
) -> list[str]:
    eyebrow = contract.eyebrow
    if not eyebrow.enabled:
        return [
            "  - Eyebrow: this deck has none. Put nothing above the title inside "
            "the band."
        ]
    if eyebrow.text_style == "latinUpper":
        wording = (
            "Write it in UPPERCASE Latin letters on EVERY slide of this deck — "
            "never in "
            f"{language}, and never mixed."
        )
    else:
        wording = (
            f"Write it in {language} on EVERY slide of this deck — never in "
            "English or any other language, and never mixed. A deck whose eyebrow "
            "switches language between slides is wrong even if each slide reads "
            "well alone."
        )
    if eyebrow.form == "chapterBreadcrumb":
        content = (
            "It reads as this deck's breadcrumb: the chapter prefix supplied "
            "with THIS slide, followed immediately by the name of the slide's "
            "own section. Copy the supplied prefix character for character — "
            "its numeral, its punctuation, and its separator — and never "
            "invent, renumber, translate, or reformat one. A slide supplied "
            "with no prefix belongs to the deck's front matter: it writes the "
            "section name alone, with no separator and no borrowed chapter "
            "number. Never put the deck title in it."
        )
    else:
        content = (
            "It names this slide's own section — never the deck title, and "
            "never a separator-joined list of labels unless every slide of this "
            "deck uses that same form."
        )
    return [
        _tick_line(contract),
        f"  - Eyebrow: exactly one line of at most {eyebrow.max_chars} characters, "
        f"{_px(eyebrow.font_size_px)}, weight {eyebrow.weight}, letter-spacing "
        f"{eyebrow.letter_spacing_em}em, colour {eyebrow.color}. {content}",
        f"  - Eyebrow wording: {wording}",
    ]


def _title_lines(contract: AuthoredHeaderContract) -> list[str]:
    title = contract.title
    lines = [
        f"  - Title: {_px(title.font_size_px)}, weight {title.weight}, line-height "
        f"{title.line_height}, colour {title.color}, at most {title.max_lines} "
        f"line(s), maximum width {_px(title.max_width_px)}. Shorten the wording "
        "rather than shrinking the type or exceeding the line budget.",
    ]
    if title.max_lines > 1:
        # The line budget is a ceiling the band derives its height from, and it
        # was read as a target: live decks broke more than half their titles at
        # a second line the wording never needed, each costing every slide of
        # the deck a title line of content room.
        lines.append(
            f"  - The line budget is a ceiling, never a target. A title that fits "
            f"{_px(title.max_width_px)} is written on ONE line, and the band it "
            "derives is shorter for it — that room goes to the slide's content. "
            "Let a title reach a second line only when its own wording overflows "
            "the width, and never insert a manual line break to balance it, to "
            "split it at a phrase, or to make it look composed. Titles of the "
            "same deck are not expected to share a line count."
        )
    lines.append(
        "  - The title appears only inside the header band. Never repeat it "
        "below the band."
    )
    return lines


def _subline_line(contract: AuthoredHeaderContract) -> str:
    subline = contract.subline
    if not subline.enabled:
        return (
            "  - Subline: this deck has none. Never add a supporting line under "
            "the title inside the band."
        )
    return (
        f"  - Subline: at most {subline.max_lines} line(s) directly under the "
        f"title, {_px(subline.font_size_px)}, weight {subline.weight}, colour "
        f"{subline.color}, {_px(contract.subline_gap_px)} below the title. "
        "Enabling it reserves the row for this deck; it does NOT oblige every "
        "slide to fill one. Omit it whenever the slide has nothing to add — that "
        "is expected on many slides and is never counted against you — and never "
        "replace it with other content or with a restatement of the title. A "
        "slide that omits it simply leaves that much out of its band height."
    )


def requirement_row_budget(contract: AuthoredHeaderContract) -> int:
    """How many chip rows the band's ceiling can hold on this deck.

    The right column is the one block the brief's escape hatch cannot reach: a
    slide whose own text carries twenty identifiers derives its band from the
    chips, and shortening the title changes nothing. Without a stated cap that
    slide is out of bounds on every retry with no legal move, so the cap is
    computed here and the overflow chip becomes the move.
    """
    spec = contract.requirements
    available = contract.band_max_px - contract.header_top_px - contract.title_gap_px
    if contract.right_slot.mode == "requirementsAndSummary":
        available -= contract.subline_gap_px + (
            contract.right_slot.summary_max_lines
            * _label_row_px(contract.right_slot.summary_font_size_px)
        )
    rows = (available + spec.gap_px) // (spec.height_px + spec.gap_px)
    return max(1, int(rows))


def _requirement_lines(contract: AuthoredHeaderContract) -> list[str]:
    spec = contract.requirements
    if not spec.enabled:
        return [
            "  - Requirement identifiers: this deck has none. Never render a "
            "requirement number, code, badge, or ticket reference in the band."
        ]
    label = (
        f'Prefix the group with the static label "{spec.label}". '
        if spec.label
        else ""
    )
    rows = requirement_row_budget(contract)
    if spec.style == "chip":
        appearance = (
            f"Render each identifier as its own real <span> chip: height "
            f"{_px(spec.height_px)}, horizontal padding {_px(spec.padding_x_px)}, "
            f"border-radius {_px(spec.radius_px)}, background {spec.background_color}, "
            f"1px solid {spec.border_color} border, {_px(spec.font_size_px)} weight "
            f"{spec.weight} {spec.text_color} text, {_px(spec.gap_px)} between chips."
        )
    else:
        appearance = (
            f"Render the identifiers as plain text separated by a middot, "
            f"{_px(spec.font_size_px)} weight {spec.weight} {spec.text_color}."
        )
    overflow = (
        f"Show at most {spec.max_visible}; when more are supplied, show the first "
        f"{spec.max_visible} and one final chip reading +N for the remainder. The "
        "whole group — visible chips and the +N chip together — must sit on ONE "
        "row; never let it wrap, because a second row makes this slide's header "
        "taller than the others. If it will not fit, show fewer identifiers and "
        "raise the +N count rather than wrapping."
        if spec.overflow == "plusN"
        else (
            f"Show at most {spec.max_visible} per row and wrap onto a second row "
            "when there are more. Keep the rows the same height on every slide. "
            f"The group may fill at most {rows} row(s): a taller one pushes the "
            "band past its ceiling, which shortening the title cannot undo. When "
            f"more identifiers remain, show the first {rows * spec.max_visible} "
            "and one final chip reading +N for the rest."
        )
    )
    return [
        f"  - Requirement identifiers: {label}{appearance} {overflow}",
        '  - Put the group in one container marked data-header-part="requirements" '
        "carrying data-requirement-ids with the comma-joined identifier list in "
        "the order the slide's own text gives them. Copy exactly what that text "
        "states — never invent, renumber, reformat, or drop one.",
        "  - A slide whose own text states no identifier renders no requirement "
        "group and leaves this slot empty. Many slides of this deck will look "
        "that way, and that is correct: the slot is reserved deck-wide so the "
        "header keeps one shape, not so every slide fills it. Never borrow an "
        "identifier from the deck topic or from another slide to fill it.",
    ]


def _right_slot_lines(contract: AuthoredHeaderContract) -> list[str]:
    slot = contract.right_slot
    geometry = (
        f"a {_px(slot.width_px)} column starting at x={_px(contract.right_slot_left_px)} "
        f"and ending at x={_px(SLIDE_WIDTH_PX - contract.margin_x_px)}, right-aligned, "
        "top-aligned with the eyebrow"
    )
    if slot.mode == "none":
        return [
            "  - Right slot: this deck leaves the right side of the band EMPTY. "
            "Never place a summary, label, badge, metric, logo, or icon there."
        ]
    summary = (
        f"a supporting note of at most {slot.summary_max_lines} line(s), "
        f"{_px(slot.summary_font_size_px)}, colour {slot.summary_color}"
    )
    if slot.mode == "summary":
        content = f"{summary}. Omit the note when the slide has nothing to add"
    elif slot.mode == "requirements":
        content = (
            "the requirement identifier group and nothing else. Never substitute "
            "a summary, label, or metric for it"
        )
    else:
        content = (
            f"the requirement identifier group on top and, beneath it, {summary}"
        )
    return [
        f"  - Right slot: {geometry}. It carries {content}.",
        '  - Mark the slot container data-header-part="right". The slot plays this '
        "same role on every slide of the deck; never repurpose it. A slide with "
        "nothing to put there leaves the slot empty — either as an empty marked "
        "container or with no container at all, both are correct and neither is "
        "counted against you. Never fill it to have it filled.",
    ]


def _divider_line(contract: AuthoredHeaderContract) -> str:
    divider = contract.divider
    if not divider.enabled:
        return (
            "  - Divider: this deck has none. Never draw a rule, border, or band "
            "edge under the header."
        )
    span = (
        "the full canvas width"
        if divider.width == "full"
        else (
            f"x={_px(contract.margin_x_px)} to "
            f"x={_px(SLIDE_WIDTH_PX - contract.margin_x_px)}"
        )
    )
    return (
        f"  - Divider: one real <div> of {_px(divider.thickness_px)} filled "
        f"{divider.color} at the bottom edge of the band, spanning {span}."
    )


def _left_block_line(contract: AuthoredHeaderContract) -> str:
    """Spell out the left column's height as a sum of contract-fixed terms."""
    terms: list[str] = []
    if contract.eyebrow.enabled:
        row = _label_row_px(contract.eyebrow.font_size_px)
        terms.append(
            f"the eyebrow row ({_px(row)}) + {_px(contract.eyebrow_gap_px)}"
        )
    terms.append(
        "(number of title lines this slide actually renders x "
        f"{_px(contract.title.line_px)})"
    )
    sentence = f"  - Left block = {' + '.join(terms)}"
    if contract.subline.enabled:
        subline_row = _label_row_px(contract.subline.font_size_px)
        sentence += (
            f", plus {_px(contract.subline_gap_px)} + (rendered subline lines x "
            f"{_px(subline_row)}) ONLY on a slide that actually renders a subline"
        )
    return sentence + "."


def _right_block_line(contract: AuthoredHeaderContract) -> str:
    """Spell out the right column's height, which is zero on most slides."""
    mode = contract.right_slot.mode
    if mode == "none":
        return (
            "  - Right block = 0. This deck's right slot is empty, so the band "
            "follows the left block alone."
        )
    chips = _px(contract.requirements.height_px)
    if contract.requirements.overflow == "wrap":
        # A wrapping group is the one right-hand element whose own height varies,
        # so the derivation has to count its rows rather than assume one.
        chips = (
            f"rendered chip rows x {chips}, plus {_px(contract.requirements.gap_px)} "
            "between rows"
        )
    summary_row = _label_row_px(contract.right_slot.summary_font_size_px)
    summary = f"(rendered note lines x {_px(summary_row)})"
    if mode == "requirements":
        content = f"the requirement chip row ({chips})"
    elif mode == "summary":
        content = f"the supporting note at its own line count: {summary}"
    else:
        # The two stacked pieces reuse the title-to-subline gap so both columns
        # of the band are built on one rhythm rather than two.
        content = (
            f"the requirement chip row ({chips}) + {_px(contract.subline_gap_px)} "
            f"+ {summary}"
        )
    return (
        f"  - Right block = {content}. It is 0 on a slide that renders nothing "
        "in the slot, and such slides are correct."
    )


def _band_lines(contract: AuthoredHeaderContract) -> list[str]:
    """State the derivation, the declaration, and the body offset.

    These are the lines the validator checks arithmetic against, so each number
    named here is one the validator reads back out of the slide's own markup.
    """
    return [
        f"  - Header band: the top of the {SLIDE_WIDTH_PX}x720 canvas, with "
        f"{_px(contract.margin_x_px)} left and right margins. Its HEIGHT is not "
        "fixed for the deck: each slide DERIVES it from what that slide actually "
        "rendered, so a one-line title does not carry the space a two-line title "
        "needs. Everything else about the band is fixed and identical on every "
        "slide — two slides that render the same parts must come out at the same "
        "height, and the derived number is the only thing allowed to differ.",
        _left_block_line(contract),
        _right_block_line(contract),
        f"  - Band height = {_px(contract.header_top_px)} + max(left block, right "
        f"block) + {_px(contract.title_gap_px)}, as a whole number of pixels, "
        f"clamped to {contract.band_min_px}-{contract.band_max_px}px. If the "
        "title would push it past the ceiling, SHORTEN THE TITLE — never shrink "
        "the type, never drop the gaps, and never exceed the ceiling.",
        f'  - Declare that number: the band root is <div data-header-part="band" '
        f'{HEADER_BAND_ATTRIBUTE}="N" style="height:Npx; ...">, where N is the '
        "same whole number in both places — a bare integer with no unit in the "
        "attribute, and an inline px height on that same element. The two must "
        "agree; a height that lives in a CSS class or is written in %, em, or "
        "calc() cannot be read back and counts as wrong. N is the band's whole "
        "rendered height, so put no padding or border on that element — or add "
        "box-sizing:border-box, which keeps them inside N. Space above the first "
        f"row is already in the {_px(contract.header_top_px)} the derivation "
        "starts from; spelling it again as padding makes the band taller than the "
        "number it declares.",
        "  - Wrap ALL ordinary slide content in exactly one element marked "
        'data-header-part="body", and give that element an inline style saying '
        f"where it starts: margin-top:{_px(contract.content_gap_px)} when it "
        "follows the band in normal document flow, or "
        f"position:absolute;top:(N + {contract.content_gap_px})px when it is "
        "pinned. Use px in "
        "either form, and match the band: a band pinned with position:absolute "
        "takes up no room for a margin to follow, so its body must be pinned too "
        "or the whole header renders underneath the content. No slide content may "
        "enter the band, and the band's own parts stay outside this element.",
        '  - Mark each part with its own data-header-part value: band, tick, '
        "eyebrow, title, subline, right, requirements, divider, body. Emit each "
        "marker at most once per slide and use no value outside that list. On the "
        "band's own parts use no data attribute other than "
        f"{_allowed_attributes(contract)}; attributes the slide is asked for "
        "elsewhere in this prompt are unaffected by this rule.",
    ]


def _allowed_attributes(contract: AuthoredHeaderContract) -> str:
    """Name the attributes this deck permits — and only the ones it permits.

    A deck without requirement identifiers must not read the name of the
    attribute that carries them, or the brief offers the model a slot the
    validator then reports as an invention.
    """
    allowed = ["data-header-part", HEADER_BAND_ATTRIBUTE]
    if contract.requirements.enabled:
        allowed.append("data-requirement-ids")
    return f"{', '.join(allowed[:-1])} and {allowed[-1]}"


def render_header_contract(
    contract: AuthoredHeaderContract,
    *,
    language: str = "the deck's language",
) -> str:
    """Return the frozen header brief injected into every slide of one deck."""
    excluded = ", ".join(contract.excluded_roles) or "none"
    lines = [
        "- SLIDE HEADER SYSTEM (decided once for THIS deck — reproduce it exactly on "
        "every slide):",
        "  - This is the deck's single top-area design. Do not redesign, re-order, "
        "restyle, or 'improve' it per slide, and do not invent an alternative top "
        "treatment. Uniformity across slides outranks per-slide visual ambition.",
        f"  - Roles that skip the header entirely: {excluded}. Those slides use "
        "their own composition and none of the rules below. They must also carry "
        "NO requirement identifier, badge, chip, or code anywhere on the slide, and "
        "must not imitate this header with an eyebrow-and-tick row of their own.",
    ]
    lines.extend(_band_lines(contract))
    lines.extend(_eyebrow_lines(contract, language))
    lines.extend(_title_lines(contract))
    lines.append(_subline_line(contract))
    lines.extend(_right_slot_lines(contract))
    lines.extend(_requirement_lines(contract))
    lines.append(_divider_line(contract))
    return "\n".join(lines)


__all__ = ["render_header_contract"]
