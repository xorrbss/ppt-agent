"""Deck-scoped authored slide header contract.

The contract is decided ONCE per deck (optionally by an LLM that reads the user's
own instructions) and then frozen.  Every slide of that deck receives the exact
same rendered contract text, which is what makes the top of the deck uniform.

Geometry is expressed in the authored canvas' absolute pixels (1280x720) so the
contract can be validated after rendering and later patched field-by-field
without re-authoring a slide from scratch.

The contract owns the header's *rhythm*, not its height. The band is derived by
each slide from what that slide actually rendered, and only the derived number
varies; every gap, font and colour that makes the rhythm stays frozen here, so
two slides that render the same parts remain pixel-identical. Pinning an
absolute height instead made every one-line title carry the empty space a
two-line title needs.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


HEADER_CONTRACT_SCHEMA_VERSION = "authored-header-contract/v2"

#: The fixed-band shape. Two geometry fields left and four arrived, so a payload
#: written under it describes a different header even where the field names match
#: — which is why it carries its own version instead of being read as a v2.
LEGACY_FIXED_BAND_SCHEMA_VERSION = "authored-header-contract/v1"

SLIDE_WIDTH_PX = 1280
SLIDE_HEIGHT_PX = 720

#: Line box of a one-line label (eyebrow, subline, right-hand note) as a multiple
#: of its font size. The contract fixes no line-height for those rows, yet the
#: band derivation needs one number per row or two slides could stack the same
#: parts differently. It lives here because the brief, the bounds check and the
#: tests must all read the same number.
LABEL_LINE_RATIO = 1.4

_HEX_PATTERN = r"^#[0-9A-Fa-f]{6}$"

# Roles whose slides are full-bleed compositions rather than ordinary content
# pages.  They opt out of the header band: a chapter divider announces rather
# than argues, exactly as a cover does.
DEFAULT_EXCLUDED_ROLES: tuple[str, ...] = ("COVER", "SECTION")

HeaderPart = Literal[
    "band",
    "tick",
    "eyebrow",
    "title",
    "subline",
    "right",
    "requirements",
    "divider",
    "body",
]

#: Value vocabulary for the ``data-header-part`` marker attribute. Every part is
#: individually addressable so a later restyle can target it without an LLM.
#: ``body`` is the one value that marks something *outside* the band: it is the
#: root the band pushes down, and without it the contract's reach below the
#: divider is prose that nothing can check.
HEADER_PART_VALUES: frozenset[str] = frozenset(
    (
        "band",
        "tick",
        "eyebrow",
        "title",
        "subline",
        "right",
        "requirements",
        "divider",
        "body",
    )
)

#: Attribute on the band root carrying the height that slide derived, as a bare
#: integer count of pixels. The band is per-slide, so the deck-wide contract can
#: only state the rhythm; this is how a slide reports what the rhythm produced.
HEADER_BAND_ATTRIBUTE = "data-header-band"

#: Fields of the superseded fixed-band contract, kept only so persisted decks
#: written against it still load. See ``_map_fixed_band``.
_LEGACY_FIXED_BAND_KEYS = frozenset(("band_height_px", "content_top_px"))

#: Bounds of the derived band's floor. A mapped legacy band must fall inside them
#: to be usable, and so must a floor lowered onto the rhythm's own smallest band.
BAND_FLOOR_PX = 96
BAND_CEILING_PX = 280

#: How tall the derived band's ceiling itself may be. It sits above
#: ``BAND_CEILING_PX`` because the ceiling has to be able to hold everything the
#: rest of the contract permits (see ``required_band_px``); past this the header
#: is no longer a header, and the contract is refused rather than stretched.
BAND_MAX_CEILING_PX = 360
_CONTENT_GAP_FLOOR_PX = 8
_CONTENT_GAP_CEILING_PX = 64

#: Shortest eyebrow budget a breadcrumb fits in: the chapter half alone spends
#: most of the sectionOnly default.
BREADCRUMB_MIN_EYEBROW_CHARS = 36


def round_px(value: float) -> int:
    """Round half up, so the brief's arithmetic is reproducible off a calculator."""
    return int(math.floor(value + 0.5))


def label_row_px(font_size_px: int) -> int:
    """Height of one single-line label row at the given font size."""
    return round_px(font_size_px * LABEL_LINE_RATIO)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeaderTickSpec(_Spec):
    """The short accent rule before the eyebrow. OFF by default like page
    numbering: an accent bar nobody asked for is chrome, and readers mistake
    the rendered span for a dash in the eyebrow text."""

    enabled: bool = False
    width_px: int = Field(default=24, ge=8, le=120)
    height_px: int = Field(default=3, ge=1, le=8)
    color: str = Field(default="#2563EB", pattern=_HEX_PATTERN)
    gap_px: int = Field(default=12, ge=4, le=32)


class HeaderEyebrowSpec(_Spec):
    enabled: bool = True
    font_size_px: int = Field(default=13, ge=12, le=20)
    weight: int = Field(default=700, ge=400, le=900)
    letter_spacing_em: float = Field(default=0.06, ge=0.0, le=0.3)
    color: str = Field(default="#2563EB", pattern=_HEX_PATTERN)
    max_chars: int = Field(default=24, ge=6, le=60)
    #: ``deckLanguage`` writes the eyebrow in the deck's own language on every
    #: slide; ``latinUpper`` writes it in uppercase Latin on every slide. Either
    #: is fine — mixing them across slides is what breaks the deck's look.
    text_style: Literal["deckLanguage", "latinUpper"] = "deckLanguage"
    #: ``sectionOnly`` names the slide's own section; ``chapterBreadcrumb``
    #: opens it with that slide's chapter ("Ⅱ. 제안개요 > 목표 아키텍처").
    form: Literal["sectionOnly", "chapterBreadcrumb"] = "sectionOnly"
    tick: HeaderTickSpec = Field(default_factory=HeaderTickSpec)


class HeaderTitleSpec(_Spec):
    font_size_px: int = Field(default=34, ge=20, le=56)
    weight: int = Field(default=800, ge=400, le=900)
    line_height: float = Field(default=1.32, ge=1.0, le=2.0)
    color: str = Field(default="#0F172A", pattern=_HEX_PATTERN)
    max_lines: int = Field(default=2, ge=1, le=3)
    max_width_px: int = Field(default=780, ge=320, le=1152)

    @property
    def line_px(self) -> int:
        """Height of one rendered title line, the band's largest single term."""
        return round_px(self.font_size_px * self.line_height)


class HeaderSublineSpec(_Spec):
    """One optional supporting line directly under the title."""

    enabled: bool = False
    font_size_px: int = Field(default=15, ge=12, le=22)
    weight: int = Field(default=500, ge=300, le=800)
    color: str = Field(default="#64748B", pattern=_HEX_PATTERN)
    max_lines: int = Field(default=1, ge=1, le=2)


class HeaderRequirementSpec(_Spec):
    """How RFP requirement identifiers appear, when the deck has any at all."""

    enabled: bool = False
    label: str = Field(default="", max_length=24)
    style: Literal["chip", "text"] = "chip"
    max_visible: int = Field(default=6, ge=1, le=12)
    overflow: Literal["plusN", "wrap"] = "wrap"
    font_size_px: int = Field(default=12, ge=12, le=18)
    weight: int = Field(default=700, ge=400, le=900)
    text_color: str = Field(default="#1D4ED8", pattern=_HEX_PATTERN)
    background_color: str = Field(default="#EEF3FA", pattern=_HEX_PATTERN)
    border_color: str = Field(default="#D6E2F0", pattern=_HEX_PATTERN)
    radius_px: int = Field(default=4, ge=0, le=16)
    height_px: int = Field(default=22, ge=16, le=40)
    padding_x_px: int = Field(default=10, ge=4, le=24)
    gap_px: int = Field(default=6, ge=2, le=16)


class HeaderRightSlotSpec(_Spec):
    """The single right-hand slot of the header band.

    ``mode`` is what keeps decks uniform: the slot always plays the same role on
    every slide instead of being a summary here, a label there, and a badge
    somewhere else.
    """

    mode: Literal["none", "summary", "requirements", "requirementsAndSummary"] = "none"
    width_px: int = Field(default=336, ge=160, le=560)
    summary_font_size_px: int = Field(default=13, ge=12, le=20)
    summary_color: str = Field(default="#64748B", pattern=_HEX_PATTERN)
    summary_max_lines: int = Field(default=2, ge=1, le=3)


class HeaderDividerSpec(_Spec):
    enabled: bool = True
    thickness_px: int = Field(default=1, ge=1, le=6)
    color: str = Field(default="#E2E8F0", pattern=_HEX_PATTERN)
    width: Literal["full", "content"] = "content"


class DeckPageNumberSpec(_Spec):
    """Whether this deck prints a page number at the bottom of every slide.

    It lives in the header contract because it is the same kind of thing: one
    decision taken once and reproduced identically on every slide. It defaults
    OFF — an unrequested footer number is chrome nobody asked for, so it appears
    only when the deck's own request calls for it.
    """

    enabled: bool = False


class AuthoredHeaderContract(_Spec):
    """The frozen, deck-wide description of every slide's fixed chrome.

    That is the top area on every slide, plus the one bottom-area decision
    (page numbering) that has to stay identical deck-wide for the same reason.

    The band each slide renders is derived, never dictated::

        left   = eyebrow row + eyebrowGapPx + rendered title lines
                 (+ sublineGapPx + subline block, only when one is rendered)
        right  = the requirement chip row, or the summary at its own line count
        band   = headerTopPx + max(left, right) + titleGapPx   clamped to
                 bandMinPx..bandMaxPx
        body   = band + contentGapPx

    Every term but ``max(left, right)`` is fixed here, which is the line between
    a header that adapts and a header that drifts.
    """

    schema_version: Literal["authored-header-contract/v2"] = (
        HEADER_CONTRACT_SCHEMA_VERSION
    )
    #: Bounds on the derived band. The floor keeps a one-line title from
    #: collapsing the deck's chrome; the ceiling is what forces a runaway title
    #: to be shortened instead of eating the slide.
    band_min_px: int = Field(default=BAND_FLOOR_PX, ge=BAND_FLOOR_PX, le=BAND_CEILING_PX)
    #: The ceiling ranges higher than the floor because it has to be able to hold
    #: ``minimum_band_px``, which at the outer edge of every other field's bounds
    #: is 348px. A ceiling below that floor is a contract no slide can satisfy.
    band_max_px: int = Field(
        default=BAND_CEILING_PX, ge=BAND_FLOOR_PX, le=BAND_MAX_CEILING_PX
    )
    #: Where the header's first row starts. The eyebrow row's own height comes
    #: from the eyebrow spec, so it needs no field of its own.
    header_top_px: int = Field(default=32, ge=16, le=96)
    eyebrow_gap_px: int = Field(default=12, ge=4, le=48)
    subline_gap_px: int = Field(default=8, ge=4, le=32)
    #: Slack between the last header row and the band's bottom edge. Roughly
    #: half a title line, so a title the model mis-counted crowds the divider
    #: rather than colliding with it.
    title_gap_px: int = Field(default=28, ge=8, le=64)
    #: Band bottom to the top of ordinary slide content.
    content_gap_px: int = Field(default=20, ge=8, le=64)
    margin_x_px: int = Field(default=64, ge=32, le=120)
    eyebrow: HeaderEyebrowSpec = Field(default_factory=HeaderEyebrowSpec)
    title: HeaderTitleSpec = Field(default_factory=HeaderTitleSpec)
    subline: HeaderSublineSpec = Field(default_factory=HeaderSublineSpec)
    right_slot: HeaderRightSlotSpec = Field(default_factory=HeaderRightSlotSpec)
    requirements: HeaderRequirementSpec = Field(default_factory=HeaderRequirementSpec)
    divider: HeaderDividerSpec = Field(default_factory=HeaderDividerSpec)
    page_number: DeckPageNumberSpec = Field(default_factory=DeckPageNumberSpec)
    excluded_roles: tuple[str, ...] = DEFAULT_EXCLUDED_ROLES

    @property
    def right_slot_left_px(self) -> int:
        return SLIDE_WIDTH_PX - self.margin_x_px - self.right_slot.width_px

    @property
    def minimum_band_px(self) -> int:
        """The smallest band this rhythm can ever derive.

        One title line, no subline, nothing in the right slot: the shortest slide
        the contract permits. A ceiling under this number, or a floor above it,
        is not a design decision — the first makes every slide of the deck
        violate its own contract with no legal move left (a title cannot be
        shortened below one line), and the second is fixed-band padding under a
        new name, charged to exactly the one-line slides this rhythm exists to
        give their space back.
        """
        return self.header_top_px + self._left_block_px(1) + self.title_gap_px

    @property
    def required_band_px(self) -> int:
        """The tallest band this contract's own permissions can force.

        Everything the contract says a slide MAY render: the title at its line
        budget, a subline at its own, and a right slot carrying what it was
        given a role for. The ceiling has to hold all of it or the contract
        contradicts itself — telling a slide two title lines are allowed and
        then reporting the band those two lines derive.
        """
        return (
            self.header_top_px
            + max(
                self._left_block_px(self.title.max_lines, subline=True),
                self._right_block_px(),
            )
            + self.title_gap_px
        )

    def _left_block_px(self, title_lines: int, *, subline: bool = False) -> int:
        block = 0
        if self.eyebrow.enabled:
            block += label_row_px(self.eyebrow.font_size_px) + self.eyebrow_gap_px
        block += title_lines * self.title.line_px
        if subline and self.subline.enabled:
            block += self.subline_gap_px + self.subline.max_lines * label_row_px(
                self.subline.font_size_px
            )
        return block

    def _right_block_px(self) -> int:
        """One chip row plus the note, which is what a full slot costs.

        How many rows a wrapping group takes depends on the slide's own text and
        is capped against this same ceiling while the brief is rendered, so only
        the first row belongs to a bound the ceiling has to hold.
        """
        block = self.requirements.height_px if self.requirements.enabled else 0
        if self.right_slot.mode in {"summary", "requirementsAndSummary"}:
            if self.right_slot.mode == "requirementsAndSummary":
                block += self.subline_gap_px
            block += self.right_slot.summary_max_lines * label_row_px(
                self.right_slot.summary_font_size_px
            )
        return block

    def applies_to_role(self, role: str) -> bool:
        return (role or "").strip().upper() not in {
            value.strip().upper() for value in self.excluded_roles
        }

    @model_validator(mode="before")
    @classmethod
    def _map_fixed_band(cls, payload: object) -> object:
        """Carry a contract written before the band became derived onto the new shape.

        Every published deck stores ``band_height_px``, and ``extra="forbid"``
        would reject those payloads — which ``load_header_contract`` reports as
        "no contract at all", quietly restyling old decks against the default.

        The stored pair is not meaningless under the new rhythm: it is exactly
        recoverable. ``content_top_px - band_height_px`` IS the content gap, and
        pinning the floor and the ceiling to the stored height reproduces the
        band those slides were authored with. Dropping it instead would let a
        remade or repaired page derive its own band and land 40px above its 63
        siblings' divider — a deck no check downstream would catch.
        """
        if not isinstance(payload, dict):
            return payload
        legacy = _LEGACY_FIXED_BAND_KEYS & payload.keys()
        if not legacy and payload.get("schema_version") != (
            LEGACY_FIXED_BAND_SCHEMA_VERSION
        ):
            return payload
        mapped = {
            key: value
            for key, value in payload.items()
            if key not in _LEGACY_FIXED_BAND_KEYS
        }
        mapped["schema_version"] = HEADER_CONTRACT_SCHEMA_VERSION
        band = payload.get("band_height_px")
        if not isinstance(band, int) or not (
            BAND_FLOOR_PX <= band <= BAND_CEILING_PX
        ):
            return mapped
        mapped.setdefault("band_min_px", band)
        mapped.setdefault("band_max_px", band)
        top = payload.get("content_top_px")
        if isinstance(top, int) and (
            _CONTENT_GAP_FLOOR_PX <= top - band <= _CONTENT_GAP_CEILING_PX
        ):
            mapped.setdefault("content_gap_px", top - band)
        return mapped

    @model_validator(mode="after")
    def _slots_are_consistent(self) -> "AuthoredHeaderContract":
        # A breadcrumb spends part of its budget on a prefix the slide does not
        # choose; raising the floor keeps every other decision the model made.
        if self.eyebrow.form == "chapterBreadcrumb":
            self.eyebrow.max_chars = max(
                self.eyebrow.max_chars, BREADCRUMB_MIN_EYEBROW_CHARS
            )
        if self.title.max_width_px > self.right_slot_left_px - self.margin_x_px:
            raise ValueError(
                "title maxWidthPx overlaps the right slot; reduce it or narrow "
                "the right slot"
            )
        wants_requirements = self.right_slot.mode in {
            "requirements",
            "requirementsAndSummary",
        }
        if wants_requirements and not self.requirements.enabled:
            raise ValueError(
                "rightSlot.mode requests requirement identifiers but "
                "requirements.enabled is false"
            )
        if self.requirements.enabled and not wants_requirements:
            raise ValueError(
                "requirements.enabled is true but rightSlot.mode does not place them"
            )
        if self.band_min_px > self.band_max_px:
            raise ValueError("bandMinPx is above bandMaxPx; the band can never fit")
        # A ceiling under what this contract itself permits leaves slides of the
        # deck permanently out of bounds, and the one escape the brief offers —
        # shorten the title — cannot reach a floor of one line, a subline the deck
        # enabled, or a right slot the deck gave a role. Raising the ceiling to
        # what the contract already allows is the only reading that keeps it
        # satisfiable, and it costs a sane deck nothing: 280 already clears it.
        # A contract whose floor and ceiling meet is a deliberately fixed band —
        # what every deck published before this rhythm carried — and there the
        # clamp is the design: a two-line title takes the same band a one-line
        # title does. Lifting the ceiling would break exactly the decks the
        # mapping above exists to keep uniform.
        if self.band_min_px != self.band_max_px:
            if self.required_band_px > BAND_MAX_CEILING_PX:
                raise ValueError(
                    "the header this contract permits is taller than a header may "
                    "be; reduce the title size, the line budget, or the gaps"
                )
            self.band_max_px = max(self.band_max_px, self.required_band_px)
        if self.band_max_px + self.content_gap_px >= SLIDE_HEIGHT_PX:
            raise ValueError("header band leaves no room for slide content")
        return self


def default_header_contract(
    *, primary: str = "#2563EB", chapter_breadcrumb: bool = False
) -> AuthoredHeaderContract:
    """The deterministic fallback used when contract generation is unavailable.

    It is intentionally the plain, safe header: eyebrow, two-line title, hairline
    divider, no accent tick and no requirement slot. Reserving the slot is a
    judgement about the source document that only the contract call can make,
    so the fallback omits it rather than guessing; the eyebrow's form it keeps.
    """
    accent = primary if _is_hex(primary) else "#2563EB"
    return AuthoredHeaderContract(
        eyebrow=HeaderEyebrowSpec(
            color=accent,
            form="chapterBreadcrumb" if chapter_breadcrumb else "sectionOnly",
            tick=HeaderTickSpec(color=accent),
        ),
    )


def _is_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(character in "0123456789abcdefABCDEF" for character in value[1:])
    )


__all__ = [
    "AuthoredHeaderContract",
    "BREADCRUMB_MIN_EYEBROW_CHARS",
    "DEFAULT_EXCLUDED_ROLES",
    "HEADER_BAND_ATTRIBUTE",
    "HEADER_CONTRACT_SCHEMA_VERSION",
    "HEADER_PART_VALUES",
    "BAND_CEILING_PX",
    "BAND_FLOOR_PX",
    "BAND_MAX_CEILING_PX",
    "LABEL_LINE_RATIO",
    "LEGACY_FIXED_BAND_SCHEMA_VERSION",
    "label_row_px",
    "round_px",
    "DeckPageNumberSpec",
    "HeaderDividerSpec",
    "HeaderEyebrowSpec",
    "HeaderPart",
    "HeaderRequirementSpec",
    "HeaderRightSlotSpec",
    "HeaderSublineSpec",
    "HeaderTickSpec",
    "HeaderTitleSpec",
    "SLIDE_HEIGHT_PX",
    "SLIDE_WIDTH_PX",
    "default_header_contract",
]
