"""Deterministic title planning for an immutable published deck master.

The implementation deliberately owns line breaking, while the master title-fit
service remains the final geometry checker.  Korean titles use spaces first and
only fall back to UAX #14 ID/grapheme opportunities when a token cannot fit.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from models.authored_vnext.master.contract import DeckMasterContract, MasterLayout
from models.authored_vnext.master.slide import ExplicitTitle, TitleFitResult
from services.authored_vnext.master.title_fit import fit_title


UAX14_VERSION = "17.0.0"
LINE_BREAK_POLICY = "canonical-explicit-uax14-v1"
TAILORING = "ko-space-first-with-id-fallback-v1"
KINSOKU_POLICY = "cjk-no-leading-closing-no-trailing-opening"
POINT_TO_PIXEL = 96 / 72

# JIS X 4051-style prohibition sets used by the master contract's CJK policy.
# They cover the punctuation encountered by Korean, Japanese, Chinese and Latin
# authored titles.  A legal boundary may not leave an opening mark at line end
# or a closing mark at the beginning of the next line.
_CLOSING = frozenset(
    "!%),.:;?]}\u00bb\u2010\u2013\u2014\u2019\u201d\u2025\u2026\u203c\u2047\u2048\u2049"
    "\u3001\u3002\u3005\u3009\u300b\u300d\u300f\u3011\u3015\u3017\u3019\u301b"
    "\uff01\uff05\uff09\uff0c\uff0e\uff1a\uff1b\uff1f\uff3d\uff5d\uff60\uff63"
)
_OPENING = frozenset(
    "([{\u00ab\u2018\u201c\u3008\u300a\u300c\u300e\u3010\u3014\u3016\u3018\u301a"
    "\uff08\uff3b\uff5b\uff5f\uff62"
)


class TitleFitPlanningError(ValueError):
    """The title cannot fit any layout permitted by the published master."""


@dataclass(frozen=True, slots=True)
class PlannedTitle:
    """A title whose explicit lines, font and final layout have been checked."""

    title: ExplicitTitle
    fit: TitleFitResult
    original_layout_id: str
    layout_id: str
    promoted: bool


def _layout(contract: DeckMasterContract, layout_id: str) -> MasterLayout:
    layouts = (
        contract.layouts.cover,
        contract.layouts.section,
        contract.layouts.standard,
        contract.layouts.long_title,
    )
    layout = next((item for item in layouts if item.id == layout_id), None)
    if layout is None:
        raise ValueError("layout is not registered by the master")
    return layout


def _title_slot(contract: DeckMasterContract, layout_id: str):
    slots = [slot for slot in _layout(contract, layout_id).slots if slot.role == "title"]
    if len(slots) != 1:
        raise ValueError("layout must contain exactly one title slot")
    return slots[0]


def resolve_windows_font(
    contract: DeckMasterContract,
    installed_fonts: set[str] | list[str] | tuple[str, ...] | None = None,
) -> str:
    """Resolve the sealed Windows font chain without consulting machine state.

    Production callers may pass the fonts discovered by their bounded font
    resolver.  With no discovery result, the published manifest's explicitly
    pinned Windows fallback is authoritative and therefore deterministic.
    """

    manifest = contract.font_manifest
    candidates = tuple(
        dict.fromkeys(
            (
                manifest.windows_fallback,
                manifest.primary,
                *manifest.fallbacks,
                *contract.tokens.fonts.fallbacks,
            )
        )
    )
    if installed_fonts is None:
        return manifest.windows_fallback
    installed = {name.casefold(): name for name in installed_fonts}
    for candidate in candidates:
        resolved = installed.get(candidate.casefold())
        if resolved is not None:
            return resolved
    raise TitleFitPlanningError("published master font chain is unavailable on Windows")


def _graphemes(text: str) -> list[str]:
    """Return stable extended-grapheme approximations without host libraries."""

    clusters: list[str] = []
    regional_count = 0
    join_next = False
    for character in text:
        codepoint = ord(character)
        is_mark = unicodedata.category(character).startswith("M")
        is_variation = 0xFE00 <= codepoint <= 0xFE0F or 0xE0100 <= codepoint <= 0xE01EF
        is_modifier = 0x1F3FB <= codepoint <= 0x1F3FF
        is_regional = 0x1F1E6 <= codepoint <= 0x1F1FF
        if clusters and (is_mark or is_variation or is_modifier or join_next):
            clusters[-1] += character
            join_next = character == "\u200d"
            continue
        if character == "\u200d" and clusters:
            clusters[-1] += character
            join_next = True
            continue
        if is_regional and clusters and regional_count % 2 == 1:
            clusters[-1] += character
            regional_count += 1
            continue
        clusters.append(character)
        regional_count = regional_count + 1 if is_regional else 0
        join_next = False
    return clusters


def _glyph_em_width(character: str) -> float:
    if character.isspace():
        return 0.32
    if unicodedata.east_asian_width(character) in {"F", "W"}:
        return 1.0
    category = unicodedata.category(character)
    if category.startswith("M") or character == "\u200d":
        return 0.0
    if category.startswith("P"):
        return 0.42
    if character.isupper():
        return 0.68
    if character.isdigit():
        return 0.58
    return 0.56


def measure_line_px(line: str, font_pt: float) -> float:
    """Conservative deterministic metric shared with the master-fit estimator."""

    return sum(_glyph_em_width(character) for character in line) * font_pt * POINT_TO_PIXEL


def _legal_boundary(left: str, right: str) -> bool:
    left_text = left.rstrip()
    right_text = right.lstrip()
    if not left_text or not right_text:
        return True
    return left_text[-1] not in _OPENING and right_text[0] not in _CLOSING


def _greedy_break(text: str, width_px: float, max_lines: int, font_pt: float) -> list[str] | None:
    clusters = _graphemes(text)
    lines: list[str] = []
    start = 0
    while start < len(clusters):
        while start < len(clusters) and clusters[start].isspace():
            start += 1
        if start >= len(clusters):
            break
        if len(lines) == max_lines - 1:
            remainder = "".join(clusters[start:]).strip()
            if measure_line_px(remainder, font_pt) <= width_px:
                lines.append(remainder)
                return lines
            return None

        last_space: int | None = None
        last_fallback: int | None = None
        end = start + 1
        while end <= len(clusters):
            candidate = "".join(clusters[start:end]).rstrip()
            if measure_line_px(candidate, font_pt) > width_px:
                break
            if end == len(clusters):
                lines.append(candidate)
                return lines
            left = "".join(clusters[start:end])
            right = "".join(clusters[end:])
            if _legal_boundary(left, right):
                last_fallback = end
                if clusters[end - 1].isspace() or clusters[end].isspace():
                    last_space = end
            end += 1

        boundary = last_space if last_space is not None else last_fallback
        if boundary is None or boundary <= start:
            return None
        line = "".join(clusters[start:boundary]).strip()
        if not line:
            return None
        lines.append(line)
        start = boundary
    return lines or None


def _font_sizes(preferred: float, minimum: float):
    current = preferred
    while current + 1e-9 >= minimum:
        yield round(current, 2)
        current -= 0.5


def _fit_on_layout(
    contract: DeckMasterContract,
    layout_id: str,
    text: str,
    *,
    installed_fonts: set[str] | list[str] | tuple[str, ...] | None,
) -> tuple[ExplicitTitle, TitleFitResult] | None:
    normalized = unicodedata.normalize("NFC", " ".join(text.split()))
    if not normalized:
        raise TitleFitPlanningError("title cannot be empty")
    layout = _layout(contract, layout_id)
    slot = _title_slot(contract, layout_id)
    style = contract.semantic_styles.title
    margins = style.margins
    width_px = slot.bounds.width - (margins.left_pt + margins.right_pt) * POINT_TO_PIXEL
    height_px = slot.bounds.height - (margins.top_pt + margins.bottom_pt) * POINT_TO_PIXEL
    line_limit = min(
        style.max_lines,
        contract.fit_policy.long_title_max_lines
        if layout.kind == "longTitle"
        else contract.fit_policy.standard_max_lines,
        3,  # ExplicitTitle v1 wire contract is capped at three explicit lines.
    )
    preferred = min(style.font_pt, contract.fit_policy.preferred_font_pt)
    minimum = max(style.min_font_pt, contract.fit_policy.minimum_font_pt)
    family = resolve_windows_font(contract, installed_fonts)
    for font_pt in _font_sizes(preferred, minimum):
        lines = _greedy_break(normalized, width_px, line_limit, font_pt)
        if lines is None:
            continue
        if len(lines) * font_pt * POINT_TO_PIXEL * style.line_height > height_px:
            continue
        # ExplicitTitle's canonical wire invariant treats line boundaries as
        # semantic spaces.  This is identical to ``normalized`` for preferred
        # word boundaries; ID/grapheme fallback adds only the canonical break
        # separator while the rendered explicit lines retain the exact glyphs.
        explicit = ExplicitTitle(text=" ".join(lines), lines=lines)
        checked = fit_title(contract, layout_id, explicit)
        if not checked.clipped:
            if checked.font_family != family:
                checked = checked.model_copy(update={"font_family": family})
            return explicit, checked
    return None


def break_title(
    contract: DeckMasterContract,
    layout_id: str,
    text: str,
    *,
    installed_fonts: set[str] | list[str] | tuple[str, ...] | None = None,
) -> ExplicitTitle:
    """Break and fit a title on exactly one master layout."""

    planned = _fit_on_layout(
        contract,
        layout_id,
        text,
        installed_fonts=installed_fonts,
    )
    if planned is None:
        raise TitleFitPlanningError("title exceeds the selected master layout")
    return planned[0]


def fit_and_promote_title(
    contract: DeckMasterContract,
    text: str,
    initial_layout_id: str | None = None,
    *,
    allow_promotion: bool = True,
    installed_fonts: set[str] | list[str] | tuple[str, ...] | None = None,
) -> PlannedTitle:
    """Fit a title, promoting standard to long-title before failing closed."""

    original = initial_layout_id or contract.layouts.standard.id
    initial_layout = _layout(contract, original)
    layout_ids = [original]
    if initial_layout.kind == "standard" and allow_promotion:
        layout_ids.append(contract.fit_policy.promotion_layout_id)
    for layout_id in layout_ids:
        result = _fit_on_layout(
            contract,
            layout_id,
            text,
            installed_fonts=installed_fonts,
        )
        if result is not None:
            title, checked = result
            return PlannedTitle(
                title=title,
                fit=checked,
                original_layout_id=original,
                layout_id=layout_id,
                promoted=layout_id != original,
            )
    raise TitleFitPlanningError("title does not fit the published master")


__all__ = [
    "KINSOKU_POLICY",
    "LINE_BREAK_POLICY",
    "POINT_TO_PIXEL",
    "PlannedTitle",
    "TAILORING",
    "TitleFitPlanningError",
    "UAX14_VERSION",
    "break_title",
    "fit_and_promote_title",
    "measure_line_px",
    "resolve_windows_font",
]
