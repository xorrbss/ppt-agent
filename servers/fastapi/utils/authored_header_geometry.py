"""The arithmetic half of header enforcement: the band a slide declared.

Nothing here renders. A slide states its band three times — the number it
declared on the band root, the CSS it wrote for that root, and where it started
the body — and these checks are the only thing that can catch a slide whose
three statements disagree. What a slide cannot be caught at is a title it
mis-counted: that is a rendering fact, and the deliberate answer to it is
``title_gap_px``, not a measurement this module could not take anyway.

It is split from ``authored_header_validation`` because the part-presence checks
deliberately know nothing about CSS, and mixing the two would give that loop a
reason to start parsing style attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from models.authored_header_contract import AuthoredHeaderContract

_LENGTH_PX = re.compile(r"^(-?\d+(?:\.\d+)?)px$", re.IGNORECASE)
_FIRST_LENGTH_PX = re.compile(r"(-?\d+(?:\.\d+)?)px", re.IGNORECASE)

#: Rounding slack for every declared-versus-CSS comparison. A title line at the
#: default spec is 44.88px tall, so a model that rounds its own arithmetic in a
#: different direction than we do is off by less than a pixel — and each false
#: violation costs a full re-authoring retry.
TOLERANCE_PX = 1.0

#: A hairline rule on the band root costs a pixel per edge and moves nothing a
#: reader would see; the failure worth reporting is padding, which is spelled in
#: the same tens of pixels as the header's own offsets.
_BOX_TOLERANCE_PX = 2.0

_OUT_OF_FLOW = {"absolute", "fixed"}


@dataclass(frozen=True)
class BandMarkup:
    """What one slide wrote about its band and its body root."""

    #: Raw ``data-header-band``, kept unparsed so a missing declaration stays
    #: distinguishable from an unreadable one.
    declaration: str | None
    band_style: str
    body_style: str
    has_band: bool
    has_body: bool


def declarations(style: str) -> dict[str, str]:
    """Split one inline ``style`` attribute into property -> value.

    Quote- and parenthesis-aware because a band root may legitimately carry a
    ``url(data:...;base64,...)`` background, whose semicolons would otherwise
    swallow the height declaration that follows it.
    """
    parsed: dict[str, str] = {}
    buffer: list[str] = []
    quote = ""
    depth = 0
    for character in style or "":
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == ";" and not depth:
            _record(parsed, "".join(buffer))
            buffer = []
            continue
        buffer.append(character)
    _record(parsed, "".join(buffer))
    return parsed


def _record(parsed: dict[str, str], text: str) -> None:
    name, separator, value = text.partition(":")
    if separator:
        parsed[name.strip().lower()] = value.strip()


def _length_px(value: str | None) -> float | None:
    """Read a CSS length, accepting only ``px``.

    Anything else — ``em``, a percentage, ``calc()``, a keyword — is a length
    this module cannot resolve without rendering, so it reads as unreadable and
    is reported rather than assumed to fit.
    """
    match = _LENGTH_PX.match((value or "").strip())
    return float(match.group(1)) if match else None


def _leading_length_px(value: str | None) -> float | None:
    """Read the first px length of a shorthand such as ``1px solid #ccc``."""
    match = _FIRST_LENGTH_PX.search(value or "")
    return float(match.group(1)) if match else None


def _shorthand_edge(value: str | None, *, bottom: bool) -> str | None:
    """Pick the top or bottom component of a ``padding``/``margin`` shorthand."""
    parts = (value or "").split()
    if not parts:
        return None
    if not bottom:
        return parts[0]
    return parts[2] if len(parts) >= 3 else parts[0]


def _zero_or_px(value: str | None) -> float | None:
    """Read an edge length, accepting the unitless zero CSS also accepts."""
    if value is None:
        return 0.0
    text = value.strip().lower()
    if text in {"0", "none"}:
        return 0.0
    return _length_px(text)


def _padding_px(parsed: dict[str, str], side: str) -> float | None:
    longhand = parsed.get(f"padding-{side}")
    if longhand is not None:
        return _zero_or_px(longhand)
    return _zero_or_px(_shorthand_edge(parsed.get("padding"), bottom=side == "bottom"))


def _border_px(parsed: dict[str, str], side: str) -> float | None:
    for name in (f"border-{side}-width", f"border-{side}", "border-width", "border"):
        value = parsed.get(name)
        if value is None:
            continue
        if value.strip().lower() in {"none", "0"}:
            return 0.0
        return _leading_length_px(value)
    return 0.0


def _outer_extra_px(parsed: dict[str, str]) -> float | None:
    """Pixels a content-box band renders beyond its declared ``height``.

    ``header_top_px`` is exactly the value a model spells as ``padding-top``, so
    a band whose padding sits outside its height renders taller than the number
    it declared, and slide content is then drawn over the divider. Returns
    ``None`` when a padding or border it carries cannot be read in px.
    """
    if (parsed.get("box-sizing") or "").strip().lower() == "border-box":
        return 0.0
    total = 0.0
    for side in ("top", "bottom"):
        for value in (_padding_px(parsed, side), _border_px(parsed, side)):
            if value is None:
                return None
            total += value
    return total


def _margin_top_px(parsed: dict[str, str]) -> float | None:
    top = _length_px(parsed.get("margin-top"))
    if top is not None:
        return top
    # The shorthand is the common way to write a body inset, and rejecting it
    # would fail slides whose geometry is in fact correct.
    return _length_px(_shorthand_edge(parsed.get("margin"), bottom=False))


def _band_css_violations(markup: BandMarkup, declared: int) -> list[str]:
    parsed = declarations(markup.band_style)
    height = _length_px(parsed.get("height"))
    if height is None:
        return ["header_band_height_unreadable"]
    if abs(height - declared) > TOLERANCE_PX:
        return ["header_band_height_mismatch"]
    extra = _outer_extra_px(parsed)
    if extra is None or extra > _BOX_TOLERANCE_PX:
        return ["header_band_box_mismatch"]
    return []


def _is_pinned(parsed: dict[str, str]) -> bool:
    """Whether this element states an absolute offset rather than a flow margin.

    A ``top`` alone counts. The deck's own idiom puts layout in a ``<style>``
    block and only the offset inline, and a ``top`` on an element in normal flow
    does nothing at all — so reading one as a flow margin would report a slide
    whose geometry is exactly right.
    """
    position = (parsed.get("position") or "").strip().lower()
    if position:
        return position in _OUT_OF_FLOW
    return _length_px(parsed.get("top")) is not None


def _body_top_violations(
    markup: BandMarkup, contract: AuthoredHeaderContract, declared: int
) -> list[str]:
    """Check that the body starts exactly one content gap below the band.

    Two spellings say the same thing, and the deck's authoring rules permit
    both: a pinned body states its absolute ``top``, a body in normal flow
    states the gap as its own ``margin-top`` under a band of known height.
    """
    if not markup.has_body:
        # Already reported as a missing part; there is nothing to measure.
        return []
    parsed = declarations(markup.body_style)
    if _is_pinned(parsed):
        offset = _length_px(parsed.get("top"))
        expected: float = declared + contract.content_gap_px
    else:
        # A band taken out of flow occupies no space for a margin to follow, so
        # the body would render on top of the whole header while every number
        # below agreed with every other.
        if _is_pinned(declarations(markup.band_style)):
            return ["header_body_outside_band_flow"]
        offset = _margin_top_px(parsed)
        expected = float(contract.content_gap_px)
    if offset is None:
        return ["header_body_top_unreadable"]
    if abs(offset - expected) > TOLERANCE_PX:
        return ["header_body_top_mismatch"]
    return []


def band_geometry_violations(
    markup: BandMarkup, contract: AuthoredHeaderContract
) -> list[str]:
    """Check the band the slide derived against the rhythm the deck froze."""
    if not markup.has_band:
        # The presence loop reports the missing band; nothing declares anything.
        return []
    raw = (markup.declaration or "").strip()
    if not raw:
        return ["header_band_not_declared"]
    try:
        declared = int(raw)
    except ValueError:
        return ["header_band_declaration_invalid"]

    violations: list[str] = []
    if not contract.band_min_px <= declared <= contract.band_max_px:
        violations.append("header_band_out_of_bounds")
    violations.extend(_band_css_violations(markup, declared))
    violations.extend(_body_top_violations(markup, contract, declared))
    return violations


__all__ = ["BandMarkup", "TOLERANCE_PX", "band_geometry_violations", "declarations"]
