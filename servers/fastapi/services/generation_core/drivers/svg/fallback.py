"""The deterministic page an SVG deck publishes when a page is out of attempts.

This is the SVG driver's counterpart to the authored driver's placeholder page
(``services/authored_author_stage_placeholder.py``): the page the terminal
rescue (``generation_core/drivers/svg/terminal.py``) publishes, flagged, once a
slide's SVG failed the contract on every attempt and its last render either
does not exist or lost content. The outline lines are read the way the
authored placeholder reads them (``_lines``, with markdown syntax shown as
plain text), but nothing is cut: every line
is published, whole. Content that does not fit at the regular size is set
smaller and, if needed, in more columns -- a flagged page that is hard to read
is reviewable; a page that silently dropped part of the slide is not.

Nothing here calls a model provider or a browser: the same inputs always
produce the same bytes, so a flagged deck is reproducible from its receipt.
Layout is computed directly against the SVG contract's static subset (no DOM,
no font metrics service) using a fixed, deterministic character-width
estimate, because that subset has no text wrapping or reflow of its own.

The page carries no failure reason or error code -- that lives in receipts,
not in customer-facing content. It only ever shows the slide's own outline
text. A title longer than one line is shortened with "…" and repeated in full
as the first body line, so the shortening loses nothing.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Mapping, Sequence
import xml.etree.ElementTree as ET

from services.svg_contract import SVG_NS, SVG_CONTRACT_VERSION, finalize_svg

FALLBACK_GROUP_ID = "svg-fallback-page"

_MARGIN_X = 64
_TOP_MARGIN = 64
_BOTTOM_LIMIT_PX = 72
_ACCENT_BAR_WIDTH = 56
_ACCENT_BAR_HEIGHT = 6
_EYEBROW_FONT_PX = 14
_EYEBROW_MAX_CHARS = 24
_TITLE_FONT_PX = 34
#: Body sizes tried in order, regular first. The tail is below any readability
#: floor on purpose: the page is flagged either way, and a smaller complete page
#: is the rescue's whole point.
_BODY_FONT_STEPS_PX = (20, 18, 16, 14, 12, 11, 10, 9, 8, 7, 6, 5, 4)
_BODY_COLUMNS = (1, 2, 3)
_COLUMN_GAP_PX = 32
#: The authored placeholder's 1.6 line height. Tighter lines (1.4 was tried)
#: make adjacent glyph boxes intersect, which the renderer measures as
#: ``text_overlap`` on every body line.
_BODY_LINE_HEIGHT = 1.6
_GAP_BAR_TO_LABEL_PX = 24
_GAP_EYEBROW_TO_TITLE_PX = 16
_GAP_TITLE_TO_BODY_PX = 30
_BODY_COLOR = "#0F172A"
_BULLET_PREFIX = "• "
#: What an empty slide shows instead of no visible text at all (the SVG
#: contract rejects a page with none). Not a failure reason: an untitled page
#: shows this the same way an empty document shows a blank-title marker.
_EMPTY_TITLE_PLACEHOLDER = "—"

# CJK/Hangul/fullwidth blocks: treated as one full em wide. Everything else
# (Latin, punctuation, digits) is treated as a narrower ~0.55em average. This
# is the same two-bucket estimate used for both the title budget and the
# bullet-wrapping width below.
_WIDE_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3040, 0x30FF),  # Hiragana / Katakana
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7A3),  # Hangul Syllables
    (0xD7B0, 0xD7FF),  # Hangul Jamo Extended-B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),  # Halfwidth / Fullwidth Forms
)
_NARROW_EM = 0.55
_WIDE_EM = 1.0


def _char_em(ch: str) -> float:
    code = ord(ch)
    for lo, hi in _WIDE_RANGES:
        if lo <= code <= hi:
            return _WIDE_EM
    return _NARROW_EM


def _text_em(text: str) -> float:
    return sum(_char_em(ch) for ch in text)


def _title_budget(*, available_width_px: float, font_px: float) -> int:
    """Characters a title keeps, using the conservative (all-wide-char) width.

    This never overestimates how much fits: even an all-Hangul/CJK title is
    guaranteed to fit the available width at this length.
    """

    return max(12, int(available_width_px / (font_px * _WIDE_EM)))


def _wrap(text: str, *, max_em: float) -> list[str]:
    """Greedy wrap against a fixed em budget per line, breaking at spaces.

    A word wider than a whole line (a long URL, an unspaced run) is the only
    thing broken mid-word; Korean words are never split across lines otherwise.
    """

    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if _text_em(candidate) <= max_em:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for ch in word:
            if current and _text_em(current + ch) > max_em:
                lines.append(current)
                current = ch
            else:
                current += ch
    lines.append(current)
    return lines


def _text_node(parent: ET.Element, *, x: float, y: float, text: str, font_px: int,
                font_family: str, fill: str, weight: str | None = None) -> None:
    attrib = {
        "x": str(int(round(x))), "y": str(int(round(y))),
        "font-family": font_family, "font-size": str(font_px), "fill": fill,
    }
    if weight is not None:
        attrib["font-weight"] = weight
    node = ET.SubElement(parent, f"{{{SVG_NS}}}text", attrib)
    node.text = text


_HEADING_MARKER = re.compile(r"^#{1,6}(?:\s+|$)")
_QUOTE_MARKER = re.compile(r"^>+\s*")
#: A list marker is a bullet glyph, or ``-``/``*``/``+`` followed by a space, so
#: ``**bold**`` and a leading ``-5%`` keep their characters.
_LIST_MARKER = re.compile(r"^(?:[•·▪●◦]\s*|[-*+]\s+)")
#: Lines that carry no words at all: thematic breaks and code fences.
_DECORATION_LINE = re.compile(r"^(?:[-*_=]{3,}|`{3,}[\w-]*|~{3,}[\w-]*)$")
_LINK = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD_STAR = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_UNDERSCORE = re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_HTML_BREAK = re.compile(r"(?i)<br\s*/?>")


def _plain_line(raw: str) -> str:
    """One outline line as the plain text a reader should see.

    Markdown syntax that would render literally on an SVG page is dropped:
    heading and quote markers, list markers (the page draws its own bullet),
    emphasis and code markers, and link syntax (``[label](url)`` keeps both the
    label and the address). Only syntax characters are removed, never words.
    """

    line = _HTML_BREAK.sub(" ", raw).strip()
    if _DECORATION_LINE.match(line):
        return ""
    line = _HEADING_MARKER.sub("", line, count=1).strip()
    line = _QUOTE_MARKER.sub("", line, count=1)
    while True:
        stripped = _LIST_MARKER.sub("", line, count=1)
        if stripped == line:
            break
        line = stripped
    line = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(1).strip() else m.group(2), line)
    for pattern in (_BOLD_STAR, _BOLD_UNDERSCORE, _STRIKE, _ITALIC_STAR, _INLINE_CODE):
        line = pattern.sub(r"\1", line)
    return " ".join(line.split())


def _lines(content: str) -> list[str]:
    """The outline's own lines as plain text, blank and decoration-only lines removed."""

    output: list[str] = []
    for raw in str(content or "").splitlines():
        line = _plain_line(raw)
        if line:
            output.append(line)
    return output


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def _complete_split(content: str, *, title_budget: int) -> tuple[str, list[str]]:
    """``(title, body lines)``: every outline line, none shortened or dropped.

    The one line that is not repeated is an exact duplicate of the title
    directly under it: the title already shows those words.
    """

    lines = _lines(content)
    if not lines:
        return "", []
    head, rest = lines[0], lines[1:]
    if rest and _same_text(head, rest[0]):
        rest = rest[1:]
    if len(head) <= title_budget:
        return head, rest
    # The untruncated line is still the slide's own text; it leads the body.
    return head[:title_budget].rstrip() + "…", [head, *rest]


def _body_layout(bullets: Sequence[str], *, available_width: float,
                 available_height: float) -> tuple[int, int, list[list[tuple[bool, str]]]]:
    """``(font_px, columns, per-column [(is_first_line, text)])`` holding every line.

    The first configuration (largest size, then fewest columns) whose lines fit
    the body box wins; when none fits, the last one is used and its lines run
    past the box -- still every word, measured by the renderer and flagged.
    """

    choice = None
    for font_px in _BODY_FONT_STEPS_PX:
        line_height = font_px * _BODY_LINE_HEIGHT
        for columns in _BODY_COLUMNS:
            column_width = (available_width - (columns - 1) * _COLUMN_GAP_PX) / columns
            prefix_width_px = _text_em(_BULLET_PREFIX) * font_px
            max_em = max(1.0, column_width - prefix_width_px) / font_px
            physical = [(position == 0, line) for bullet in bullets
                        for position, line in enumerate(_wrap(bullet, max_em=max_em))]
            per_column = max(1, int((available_height - font_px) // line_height) + 1)
            choice = (font_px, columns, physical, per_column)
            if len(physical) <= per_column * columns:
                break
        else:
            continue
        break
    font_px, columns, physical, per_column = choice
    # Overflowing lines stay in the last column rather than being dropped.
    split = [physical[i * per_column:(i + 1) * per_column] for i in range(columns - 1)]
    split.append(physical[(columns - 1) * per_column:])
    return font_px, columns, split


def _body_top(style_snapshot: dict | None, header_page: dict | None) -> int:
    """Where a rescue page's own content may start.

    A deck with a common header reserves the band above ``content_top_px``
    for the server-rendered header; a rescue page drawn from the canvas top
    would sit under it.
    """

    contract = (style_snapshot or {}).get("common_header")
    if not isinstance(contract, dict) or not isinstance(header_page, dict) or not header_page.get("applied"):
        return _TOP_MARGIN
    content_top = contract.get("content_top_px")
    if isinstance(content_top, (int, float)) and not isinstance(content_top, bool):
        return max(_TOP_MARGIN, int(content_top) + _GAP_TITLE_TO_BODY_PX)
    return _TOP_MARGIN


def fallback_page_svg(*, content: str, role: str, index: int, count: int,
                       width: int, height: int, style_snapshot: dict | None,
                       header_page: dict | None = None) -> str:
    """Deterministic contract-valid SVG for one page, finalized (page number added).

    Same inputs always produce the same bytes: no randomness, no clock, no
    provider or browser call. Raises nothing for any ``content`` -- including
    empty content, very long lines, CJK text, or ``<``/``&``/quote characters
    -- because the whole point of a fallback page is that it always publishes.
    Every outline line appears in full (see the module docstring).
    """

    tokens = (style_snapshot or {}).get("tokens", {})
    accent = tokens.get("primary") or "#2563EB"
    from services.svg_trusted_fonts import DEFAULT_FONT_FAMILY
    body_font = tokens.get("body_font") or DEFAULT_FONT_FAMILY
    title_font = tokens.get("heading_font") or body_font

    available_width = max(1, width - 2 * _MARGIN_X)
    title_budget = _title_budget(available_width_px=available_width, font_px=_TITLE_FONT_PX)
    title, bullets = _complete_split(content, title_budget=title_budget)
    if not title:
        title = _EMPTY_TITLE_PLACEHOLDER

    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
    })
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": FALLBACK_GROUP_ID})
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#FFFFFF",
    })
    top = _body_top(style_snapshot, header_page)
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": str(_MARGIN_X), "y": str(top),
        "width": str(_ACCENT_BAR_WIDTH), "height": str(_ACCENT_BAR_HEIGHT), "fill": accent,
    })

    cursor = top + _ACCENT_BAR_HEIGHT + _GAP_BAR_TO_LABEL_PX
    eyebrow = str(role or "").strip().upper()[:_EYEBROW_MAX_CHARS]
    if eyebrow:
        _text_node(group, x=_MARGIN_X, y=cursor + _EYEBROW_FONT_PX, text=eyebrow,
                   font_px=_EYEBROW_FONT_PX, font_family=body_font, fill=accent)
        cursor += _EYEBROW_FONT_PX + _GAP_EYEBROW_TO_TITLE_PX

    _text_node(group, x=_MARGIN_X, y=cursor + _TITLE_FONT_PX, text=title,
               font_px=_TITLE_FONT_PX, font_family=title_font, fill=_BODY_COLOR, weight="700")
    cursor += _TITLE_FONT_PX + _GAP_TITLE_TO_BODY_PX

    if bullets:
        max_y = height - _BOTTOM_LIMIT_PX
        font_px, columns, split = _body_layout(
            bullets, available_width=available_width, available_height=max(1, max_y - cursor))
        column_width = (available_width - (columns - 1) * _COLUMN_GAP_PX) / columns
        prefix_width_px = _text_em(_BULLET_PREFIX) * font_px
        for column, lines in enumerate(split):
            left = _MARGIN_X + column * (column_width + _COLUMN_GAP_PX)
            y = cursor + font_px
            for first, line in lines:
                _text_node(group, x=left if first else left + prefix_width_px, y=y,
                           text=(_BULLET_PREFIX if first else "") + line, font_px=font_px,
                           font_family=body_font, fill=_BODY_COLOR)
                y += font_px * _BODY_LINE_HEIGHT

    raw = ET.tostring(root, encoding="unicode")
    return finalize_svg(raw, index=index, n=count, width=width, height=height,
                        style_snapshot=style_snapshot, header_page=header_page)


def placeholder_page_svg(*, title: str, width: int, height: int, font_family: str,
                         index: int | None = None, count: int | None = None,
                         style_snapshot: dict | None = None, header_page: dict | None = None) -> str:
    """The last-resort page: the slide's title (or "—") on a plain page.

    Published only when no complete-content page could be rendered; the flag
    on the page, not its bytes, tells the reader what is missing. With
    ``index``/``count`` the page is finalized (page number) like
    :func:`fallback_page_svg`; without them it is validated as-is (a source
    deck carries no system page number).
    """

    from services.svg_contract import validate_svg

    available_width = max(1, width - 2 * _MARGIN_X)
    lines = _lines(title)
    text = lines[0] if lines else _EMPTY_TITLE_PLACEHOLDER
    budget = _title_budget(available_width_px=available_width, font_px=_TITLE_FONT_PX)
    if len(text) > budget:
        text = text[:budget].rstrip() + "…"
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": str(width), "height": str(height), "viewBox": f"0 0 {width} {height}",
    })
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": FALLBACK_GROUP_ID})
    ET.SubElement(group, f"{{{SVG_NS}}}rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#FFFFFF",
    })
    top = _body_top(style_snapshot, header_page)
    _text_node(group, x=_MARGIN_X, y=top + _TITLE_FONT_PX, text=text, font_px=_TITLE_FONT_PX,
               font_family=font_family, fill=_BODY_COLOR, weight="700")
    raw = ET.tostring(root, encoding="unicode")
    if index is not None and count is not None:
        return finalize_svg(raw, index=index, n=count, width=width, height=height,
                            style_snapshot=style_snapshot, header_page=header_page)
    validate_svg(raw, width=width, height=height, allow_system_page_number=False, source_manifest={})
    return raw


def _recovery_content_profile(content: str | Mapping) -> dict | None:
    """Describe existing content only; this is not a layout or page classifier."""
    from services.generation_core.drivers.svg.native_requirements import requested_native_data

    # Source members are not explanatory groups. A plain outline cannot prove
    # whether its asset offer has images, so unknown membership is excluded.
    if not isinstance(content, Mapping):
        return None
    text = content.get("text")
    image_members, groups = content.get("image_member_count"), content.get("image_groups")
    if (type(image_members) is not int or image_members < 0 or not isinstance(groups, list)
            or any(not isinstance(group, list) or not group
                   or any(not isinstance(member, str) or not member for member in group) for group in groups)):
        return None
    membership = [member for group in groups for member in group]
    if len(membership) != image_members or len(set(membership)) != image_members:
        return None
    image_groups = len(groups)
    if "image_group_count" in content and content["image_group_count"] != image_groups:
        return None
    if not isinstance(text, str):
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    requirements = requested_native_data(text)
    tables, charts = requirements.get("tables", []), requirements.get("charts", [])
    if any(chart.get("unsupportedReason") for chart in charts):
        return None
    return {
        "content_chars": len(text), "nonempty_lines": len(lines),
        "title_chars": len(lines[0]) if lines else 0, "image_group_count": image_groups,
        "image_member_count": image_members, "image_group_shapes": sorted(len(group) for group in groups),
        "table_shapes": [[len(table["rows"]), len(table["rows"][0])] for table in tables],
        "chart_shapes": [[len(chart["categories"]), len(chart.get("series", [chart]))] for chart in charts],
    }


def validated_recovery_candidates(snapshot: Mapping | None, content: str | Mapping, canvas: object, *,
                                  current_validation_context: Mapping | None = None,
                                  allowed_source_evidence=None) -> list[dict]:
    """Return only current-condition, content-compatible, quality-qualified SVGs.

    The snapshot is the server's trusted request pin, not a public payload. Missing
    real material or independently captured runtime conditions means no recovery.
    The adapted result still needs all normal source, render and quality checks.
    """
    from services.svg_authoring_profile import validate_quality_recovery_policy
    from utils.authored_style_svg_recovery import (recovery_candidate_valid, recovery_style_identity,
        recovery_source_authorized, recovery_digest)

    if not isinstance(snapshot, Mapping):
        return []
    try:
        policy = validate_quality_recovery_policy(snapshot.get("quality_recovery_policy"))
    except ValueError:
        return []
    if policy is None:
        return []
    # The server captures these current fingerprints independently of candidates.
    # An enrolled receipt cannot supply its own runtime compatibility witness.
    conditions = current_validation_context
    if not isinstance(conditions, Mapping) or conditions.get("contract_version") != SVG_CONTRACT_VERSION:
        return []
    if isinstance(canvas, Mapping):
        width, height = canvas.get("width_px", canvas.get("width")), canvas.get("height_px", canvas.get("height"))
    else:
        width, height = getattr(canvas, "width_px", None), getattr(canvas, "height_px", None)
    if type(width) is not int or type(height) is not int:
        return []
    profile = _recovery_content_profile(content)
    if profile is None:
        return []
    raw = snapshot.get("validated_recovery_candidates", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    identity, candidates = recovery_style_identity(snapshot), []
    for candidate in raw:
        if not recovery_source_authorized(candidate, allowed_source_evidence) or not recovery_candidate_valid(candidate):
            continue
        if (candidate["style_id"] != snapshot.get("style_id")
                or candidate["style_identity_digest"] != identity
                or candidate["canvas"] != {"width": width, "height": height}):
            continue
        evidence = candidate["verification"]
        if any(not conditions.get(key) or evidence.get(key) != conditions[key]
               for key in ("renderer_digest", "fonts_digest", "runtime_digest")):
            continue
        fit = candidate["content_fit"]
        if any(not fit[key][0] <= profile[key] <= fit[key][1]
               for key in ("content_chars", "nonempty_lines", "title_chars", "image_group_count", "image_member_count")):
            continue
        if any(profile[key] != fit[key] for key in ("table_shapes", "chart_shapes", "image_group_shapes")):
            continue
        from services.generation_core.drivers.svg.native_requirements import requested_native_data
        from services.svg_contract import source_digest
        scope = {"content_digest": source_digest(content["text"]), "profile_digest": recovery_digest(profile),
            "native_requirements_digest": recovery_digest(requested_native_data(content["text"])),
            "image_groups_digest": recovery_digest(sorted(sorted(group) for group in content["image_groups"]))}
        if scope not in candidate["tested_content_scopes"]:
            continue
        value = copy.deepcopy(candidate)
        value["current_content_profile"] = profile
        candidates.append(value)
    return sorted(candidates, key=lambda candidate: (candidate["id"], candidate["revision"]))


def select_validated_recovery_candidate(candidates: Sequence[Mapping], *, recent_pages: Sequence = (),
                                      recent_candidate_ids: Sequence = (), allowed_source_evidence=None) -> dict | None:
    """Prefer less repeated actual geometry inside the already qualified pool.

    Reservations are made by the caller in page-index order before provider I/O.
    Geometry similarity is an observation, never a design-quality pass score.
    """
    from services.generation_core.drivers.svg.composition_observation import _geometry
    from utils.authored_style_svg_recovery import recovery_candidate_valid, recovery_source_authorized

    geometries = []
    for page in recent_pages:
        if isinstance(page, tuple) and page:
            page = page[0]
        svg = page.get("svg_content", "") if isinstance(page, Mapping) else page
        if isinstance(svg, str):
            geometry = _geometry(svg)
            if geometry is not None and geometry[1] >= 3:
                geometries.append(geometry[0])
    uses = Counter(item for item in recent_candidate_ids if isinstance(item, str))
    ranked = []
    for candidate in candidates:
        if not recovery_source_authorized(candidate, allowed_source_evidence) or not recovery_candidate_valid(candidate):
            continue
        geometry = _geometry(candidate["svg_content"])
        if geometry is None:
            continue
        features, similarities = geometry[0], []
        for other in geometries:
            keys = features.keys() | other.keys()
            denominator = sum(max(features[key], other[key]) for key in keys)
            similarities.append(sum(min(features[key], other[key]) for key in keys) / max(1, denominator))
        similarity = max(similarities, default=0.0)
        count = uses[candidate["id"]]
        ranked.append(((similarity, count, candidate["id"], candidate["revision"]), candidate, geometry[2]))
    if not ranked:
        return None
    rank, selected, geometry_digest = min(ranked, key=lambda item: item[0])
    result = copy.deepcopy(selected)
    result["selection_evidence"] = {
        "candidate_id": result["id"], "candidate_revision": result["revision"],
        "qualified_pool_size": len(ranked), "single_qualified_candidate": len(ranked) == 1,
        "geometry_digest": geometry_digest, "maximum_observed_similarity": rank[0],
        "recent_use_count": rank[1], "compared_page_count": len(geometries),
        "selection_order": "page-index-reservation",
    }
    return result


__all__ = [
    "FALLBACK_GROUP_ID",
    "fallback_page_svg",
    "placeholder_page_svg",
    "validated_recovery_candidates",
    "select_validated_recovery_candidate",
]
