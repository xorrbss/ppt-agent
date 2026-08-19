"""Verify that one authored slide reproduced the deck's header contract.

The authoring prompt alone does not make a header uniform — a model that drifts
on slide 14 produces exactly the inconsistency this feature exists to remove.
This module is the deterministic check that catches the drift, and it also
enforces the one rule the model must never bend: every requirement identifier a
slide shows must be stated in that slide's own source text. It checks that by
literal membership, never by recognising an identifier's shape — shape matching
is what once promoted ``SHA-256`` to a requirement badge.

Since the band became a derived height there is a second half to the check: the
band each slide declares, the CSS it wrote for it, and where the body starts.
That arithmetic lives in ``authored_header_geometry`` so the presence loop here
can go on knowing nothing about CSS.

What is checked and what is left to the slide is the same distinction
throughout: the contract decides which parts EXIST for the deck, and the slide
decides what goes IN them. Subline, right slot and requirement group are each
optional on any given slide for that reason — demanding them is what once put
filler on 99 slides out of 99.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from models.authored_header_contract import (
    HEADER_BAND_ATTRIBUTE,
    HEADER_PART_VALUES,
    AuthoredHeaderContract,
)
from utils.authored_header_geometry import BandMarkup, band_geometry_violations

HEADER_PART_ATTRIBUTE = "data-header-part"
REQUIREMENT_IDS_ATTRIBUTE = "data-requirement-ids"
STYLE_ATTRIBUTE = "style"

#: Elements HTML never closes. Counting them as open depth left the requirement
#: scope open forever, so a chip carrying a status icon swallowed the divider and
#: the body markers that followed it — a violation the brief forbids nothing to
#: cause and the model has no way to avoid.
VOID_HTML_TAGS = frozenset(
    (
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    )
)


@dataclass
class _HeaderScan:
    parts: dict[str, int] = field(default_factory=dict)
    requirement_attribute: str | None = None
    requirement_text: str = ""
    unknown_parts: list[str] = field(default_factory=list)
    #: Raw ``data-header-band`` from the band root, kept unparsed so a missing
    #: declaration stays distinguishable from an unreadable one.
    band_declaration: str | None = None
    band_style: str = ""
    body_style: str = ""


class _HeaderParser(HTMLParser):
    """Collect header markers and the requirement group's own text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scan = _HeaderScan()
        self._requirement_depth = 0
        self._depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() not in VOID_HTML_TAGS:
            self._depth += 1
        if self._requirement_depth:
            # Adjacent chips are separate elements; without a boundary their
            # texts would concatenate into one unrecognisable token.
            self.scan.requirement_text += " "
            return
        values = {name.lower(): (value or "") for name, value in attrs}
        part = values.get(HEADER_PART_ATTRIBUTE)
        if part is None:
            return
        normalized = part.strip().lower()
        if normalized not in HEADER_PART_VALUES:
            self.scan.unknown_parts.append(normalized)
            return
        self.scan.parts[normalized] = self.scan.parts.get(normalized, 0) + 1
        if normalized == "requirements":
            self.scan.requirement_attribute = values.get(REQUIREMENT_IDS_ATTRIBUTE)
            self._requirement_depth = self._depth
        elif normalized == "band":
            self.scan.band_declaration = values.get(HEADER_BAND_ATTRIBUTE)
            self.scan.band_style = values.get(STYLE_ATTRIBUTE, "")
        elif normalized == "body":
            self.scan.body_style = values.get(STYLE_ATTRIBUTE, "")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_HTML_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in VOID_HTML_TAGS:
            return
        if self._requirement_depth:
            if self._depth <= self._requirement_depth:
                self._requirement_depth = 0
            else:
                self.scan.requirement_text += " "
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._requirement_depth:
            self.scan.requirement_text += data


def _scan(html: str) -> _HeaderScan:
    parser = _HeaderParser()
    parser.feed(html or "")
    parser.close()
    return parser.scan


def _expected_parts(contract: AuthoredHeaderContract) -> dict[str, bool]:
    """Parts whose presence follows from the contract alone.

    The subline, the right slot and the requirement group are absent from this
    map on purpose: what goes in each of them is the slide's own judgement, so
    each has its own function below.
    """
    return {
        "band": True,
        "title": True,
        "eyebrow": contract.eyebrow.enabled,
        "tick": contract.eyebrow.enabled and contract.eyebrow.tick.enabled,
        "divider": contract.divider.enabled,
        # Not part of the header, but the band is only half a contract without
        # it: an unmarked body leaves "content starts below the band" as prose.
        "body": True,
    }


def _declared_identifiers(scan: _HeaderScan) -> tuple[str, ...]:
    return tuple(
        token.strip()
        for token in (scan.requirement_attribute or "").split(",")
        if token.strip()
    )


def _unsupported_identifiers(scan: _HeaderScan, content: str) -> tuple[str, ...]:
    """Identifiers the slide declared that its own source text never states.

    This is a literal, case-insensitive membership test against the slide's
    content — no pattern, no vocabulary, no denylist. It catches the failure the
    per-slide rule exists to prevent: a deck whose every slide showed the same
    identifier, lifted from the deck topic because that slide's own text carried
    none. A source range such as ``SER-001~008`` states only its endpoints, so a
    model that expands one is flagged; that costs a retry, never the slide,
    because authoring keeps its best attempt when the retries run out.
    """
    haystack = (content or "").upper()
    if not haystack:
        return ()
    return tuple(
        value for value in _declared_identifiers(scan) if value.upper() not in haystack
    )


def _requirement_violations(
    scan: _HeaderScan,
    contract: AuthoredHeaderContract,
    content: str,
) -> list[str]:
    """Check the requirement group's placement and its grounding in the slide.

    Whether a given code *is* a requirement identifier needs the sentence around
    it and is decided while authoring. What is checked here is narrower and
    fully mechanical: the slot exists, at most one group uses it, the group
    declares what it shows, and everything it declares came from this slide.
    """
    rendered = scan.parts.get("requirements", 0)
    if not contract.requirements.enabled:
        # No slot was reserved, so no badge may appear anywhere. This is the rule
        # that keeps a deck without identifiers free of invented badges.
        return ["header_requirements_unexpected"] if rendered else []
    if rendered > 1:
        return ["header_requirements_duplicated"]
    if not rendered:
        # Slides whose text carries no identifier correctly render nothing.
        return []
    if not _declared_identifiers(scan):
        # Without the declaration there is nothing to check the group against.
        return ["header_requirement_ids_attribute_missing"]
    if _unsupported_identifiers(scan, content):
        return ["header_requirement_id_not_in_content"]
    return []


def _right_slot_violations(
    scan: _HeaderScan, contract: AuthoredHeaderContract
) -> list[str]:
    """Check the right slot the way the requirement group is checked.

    Choosing a slot mode reserves the right side of the band for one role deck-
    wide; it does not oblige every slide to have something to put there. The
    brief says so in as many words ("it is 0 on a slide that renders nothing in
    the slot, and such slides are correct"), and requiring the container anyway
    is the subline defect one field over: the model obeys and is re-authored for
    it until it writes filler. A slide may equally keep the empty container, so
    both spellings are accepted and neither is preferred.
    """
    rendered = scan.parts.get("right", 0)
    if contract.right_slot.mode == "none":
        return ["header_right_unexpected"] if rendered else []
    if rendered > 1:
        return ["header_right_duplicated"]
    return []


def _subline_violations(
    scan: _HeaderScan, contract: AuthoredHeaderContract
) -> list[str]:
    """Check the subline the way the requirement group is checked.

    Enabling the subline reserves the row; it does not oblige every slide to
    fill it. Treating it as mandatory is what turned an optional supporting line
    into filler on 99 slides out of 99 — the model was told to omit it when the
    slide had nothing to add, and then re-authored for obeying.
    """
    rendered = scan.parts.get("subline", 0)
    if not contract.subline.enabled:
        return ["header_subline_unexpected"] if rendered else []
    if rendered > 1:
        return ["header_subline_duplicated"]
    return []


def _band_markup(scan: _HeaderScan) -> BandMarkup:
    """Hand the arithmetic checks exactly what the slide wrote, and nothing else."""
    return BandMarkup(
        declaration=scan.band_declaration,
        band_style=scan.band_style,
        body_style=scan.body_style,
        has_band=bool(scan.parts.get("band")),
        has_body=bool(scan.parts.get("body")),
    )


def validate_header_contract(
    html: str,
    contract: AuthoredHeaderContract,
    *,
    role: str,
    content: str = "",
) -> tuple[str, ...]:
    """Return violation codes for one authored slide; empty means conforming.

    ``content`` is the slide's own source text, which is what every identifier
    the slide shows must come from. Omitting it skips that grounding check.

    Roles excluded from the header band are checked only for stray requirement
    markers, since they own their composition entirely.
    """
    scan = _scan(html)
    if not contract.applies_to_role(role):
        return (
            ("header_requirements_unexpected",)
            if scan.parts.get("requirements")
            else ()
        )

    violations: list[str] = []
    for part, expected in _expected_parts(contract).items():
        count = scan.parts.get(part, 0)
        if expected and count == 0:
            violations.append(f"header_{part}_missing")
        elif not expected and count:
            violations.append(f"header_{part}_unexpected")
        elif count > 1:
            violations.append(f"header_{part}_duplicated")
    if scan.unknown_parts:
        violations.append("header_part_unknown")
    violations.extend(_right_slot_violations(scan, contract))
    violations.extend(_subline_violations(scan, contract))
    violations.extend(_requirement_violations(scan, contract, content))
    violations.extend(band_geometry_violations(_band_markup(scan), contract))
    return tuple(violations)


__all__ = [
    "HEADER_BAND_ATTRIBUTE",
    "VOID_HTML_TAGS",
    "HEADER_PART_ATTRIBUTE",
    "REQUIREMENT_IDS_ATTRIBUTE",
    "STYLE_ATTRIBUTE",
    "validate_header_contract",
]
