"""Deterministic fixes for a header contract violation, before re-authoring.

Implements H2 (``docs/authored-quality-speed-programme-design-20260905.md``,
section 6): a slide that drifted from the deck's header contract on a purely
mechanical point -- an out-of-bounds band number, a part marker rendered
twice, an unrecognised ``data-header-part`` value, or a mis-pinned body
wrapper -- does not need a full re-authoring round trip; these are local
edits, the way ``authored_header_restyle`` turns a contract *change* into
local patches instead of N re-authoring calls. What this deliberately does
**not** fix: anything needing judgement about the slide's own content -- a
missing required part, an ungrounded requirement identifier, subline/right
text. Those stay in ``remaining`` for the caller (``author_deck.py``) to
route to re-authoring, as before this module existed.

Dropping an unknown part's attribute, de-duplicating a repeated part, and
clamping the band integer are attribute-level edits the patch engine has no
action for (it edits style/text/geometry by ``data-qid``, never a raw
attribute), so they use ``authored_html_normalize``'s technique: the exact
byte span of the real start tag via ``HTMLParser`` (never a reserialised
tree), spliced directly. Re-pinning the body's top offset is a style change
on a ``data-qid``-addressed element, so it goes through
``authored_patch_engine.apply_authored_patch`` with ``setStyle``, exactly as
``authored_header_restyle`` does for a contract-wide restyle.

A band clamp rewrites both the ``data-header-band`` attribute and, when
present, the paired ``height`` declaration to the same number -- moving only
the attribute would trade ``header_band_out_of_bounds`` for a freshly
introduced ``header_band_height_mismatch`` on every slide that (typically)
wrote the two consistently to begin with.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re

from models.authored_header_contract import (
    HEADER_BAND_ATTRIBUTE,
    AuthoredHeaderContract,
)
from utils.authored_header_geometry import declarations
from utils.authored_header_restyle import HeaderQids, header_qids
from utils.authored_header_validation import (
    HEADER_PART_ATTRIBUTE,
    HEADER_PART_VALUES,
    VOID_HTML_TAGS,
    validate_header_contract,
)
from utils.authored_patch_engine import AuthoredPatchPlan, apply_authored_patch

_STYLE_ATTRIBUTE_NAME = "style"

#: One declaration inside a ``style="..."`` value, anchored the same way
#: ``authored_css_strip``/``authored_html_normalize`` anchor one: start of the
#: text, or right after a ``;``.
_HEIGHT_DECLARATION = re.compile(r"(?:^|(?<=;))\s*height\s*:\s*[^;]*")


@dataclass(frozen=True, slots=True)
class HeaderRepairResult:
    """Result of :func:`repair_header_contract`. ``fixed`` is the subset of
    the input violation codes no longer present in ``remaining`` (a fresh
    ``validate_header_contract`` call against ``html``, never a filtered copy
    of the input); ``changed`` is true only when ``html`` differs
    byte-for-byte from the input."""

    html: str
    fixed: tuple[str, ...]
    remaining: tuple[str, ...]
    changed: bool


@dataclass(frozen=True, slots=True)
class _PartSpan:
    part: str
    known: bool
    start_tag_start: int
    start_tag_end: int
    start_tag_raw: str
    content_start: int
    content_end: int
    element_end: int


@dataclass
class _OpenFrame:
    tag: str
    part: str
    start: int
    tag_end: int
    raw: str


class _PartScanParser(HTMLParser):
    """Locate every ``data-header-part`` element's byte span. Mirrors
    ``authored_html_normalize``'s hidden-element scanner: exact tag spans via
    ``get_starttag_text()`` and line/column position, never a reserialised
    tree."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0, *(m.end() for m in re.finditer(r"\n", source))]
        self._stack: list[_OpenFrame] = []
        self.spans: list[_PartSpan] = []
        self.malformed = False

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    @staticmethod
    def _part_of(attrs: list[tuple[str, str | None]]) -> str | None:
        for name, value in attrs:
            if name.lower() == HEADER_PART_ATTRIBUTE:
                return (value or "").strip().lower()
        return None

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, void: bool) -> None:
        part = self._part_of(attrs)
        raw = self.get_starttag_text()
        if raw is None:
            self.malformed = True
            return
        start = self._offset()
        end = start + len(raw)
        if void:
            if part is not None:
                self.spans.append(_PartSpan(part, part in HEADER_PART_VALUES, start, end, raw, end, end, end))
            return
        self._stack.append(_OpenFrame(tag.lower(), part or "", start, end, raw))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=tag.lower() in VOID_HTML_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack or self._stack[-1].tag != tag:
            self.malformed = True
            return
        frame = self._stack.pop()
        content_end = self._offset()
        close = self._source.find(">", content_end)
        if close < 0:
            self.malformed = True
            return
        element_end = close + 1
        if frame.part:
            self.spans.append(
                _PartSpan(
                    frame.part,
                    frame.part in HEADER_PART_VALUES,
                    frame.start,
                    frame.tag_end,
                    frame.raw,
                    frame.tag_end,
                    content_end,
                    element_end,
                )
            )


_TAG_STRIP = re.compile(r"<[^>]*>")


def _is_empty(source: str, span: _PartSpan) -> bool:
    inner = source[span.content_start : span.content_end]
    return _TAG_STRIP.sub("", inner).strip() == ""


def _scan_part_elements(html: str) -> list[_PartSpan]:
    parser = _PartScanParser(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []
    if parser.malformed or parser._stack:
        return []
    return sorted(parser.spans, key=lambda span: span.start_tag_start)


def _attribute_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf'(?is)(\s{re.escape(name)}\s*=\s*)("[^"]*"|\'[^\']*\')')


def _read_attribute(tag_raw: str, name: str) -> str | None:
    match = _attribute_pattern(name).search(tag_raw)
    if match is None:
        return None
    return match.group(2)[1:-1]


def _set_attribute(tag_raw: str, name: str, value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        quote = match.group(2)[0]
        return f"{match.group(1)}{quote}{value}{quote}"

    return _attribute_pattern(name).sub(_replace, tag_raw, count=1)


def _remove_attribute(tag_raw: str, name: str) -> str:
    return _attribute_pattern(name).sub("", tag_raw, count=1)


def _apply_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    result = source
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def _drop_unknown_parts(html: str) -> str:
    edits = [
        (span.start_tag_start, span.start_tag_end, _remove_attribute(span.start_tag_raw, HEADER_PART_ATTRIBUTE))
        for span in _scan_part_elements(html)
        if not span.known
    ]
    return _apply_edits(html, edits) if edits else html


def _dedupe_parts(html: str, parts_to_fix: frozenset[str]) -> str:
    spans = _scan_part_elements(html)
    groups: dict[str, list[_PartSpan]] = {}
    for span in spans:
        if span.part in parts_to_fix:
            groups.setdefault(span.part, []).append(span)

    edits: list[tuple[int, int, str]] = []
    for group in groups.values():
        for span in group[1:]:
            if _is_empty(html, span):
                edits.append((span.start_tag_start, span.element_end, ""))
            else:
                new_tag = _remove_attribute(span.start_tag_raw, HEADER_PART_ATTRIBUTE)
                edits.append((span.start_tag_start, span.start_tag_end, new_tag))
    return _apply_edits(html, edits) if edits else html


def _clamp_band(html: str, contract: AuthoredHeaderContract) -> str:
    band_spans = [span for span in _scan_part_elements(html) if span.part == "band"]
    if not band_spans:
        return html
    span = band_spans[0]
    raw_value = _read_attribute(span.start_tag_raw, HEADER_BAND_ATTRIBUTE)
    if raw_value is None:
        return html
    try:
        declared = int(raw_value.strip())
    except ValueError:
        return html
    target = min(max(declared, contract.band_min_px), contract.band_max_px)
    if target == declared:
        return html
    new_tag = _set_attribute(span.start_tag_raw, HEADER_BAND_ATTRIBUTE, str(target))
    style_value = _read_attribute(new_tag, _STYLE_ATTRIBUTE_NAME)
    if style_value is not None and _HEIGHT_DECLARATION.search(style_value):
        new_style = _HEIGHT_DECLARATION.sub(f"height:{target}px", style_value, count=1)
        new_tag = _set_attribute(new_tag, _STYLE_ATTRIBUTE_NAME, new_style)
    return html[: span.start_tag_start] + new_tag + html[span.start_tag_end :]


def _is_body_pinned(parsed: dict[str, str]) -> bool:
    position = (parsed.get("position") or "").strip().lower()
    if position:
        return position in {"absolute", "fixed"}
    return bool((parsed.get("top") or "").strip())


def _repin_body_top(html: str, contract: AuthoredHeaderContract, qids: HeaderQids) -> str:
    spans = {span.part: span for span in _scan_part_elements(html)}
    band_span, body_span = spans.get("band"), spans.get("body")
    body_qid = qids.parts.get("body")
    if band_span is None or body_span is None or not body_qid:
        return html
    raw_band = _read_attribute(band_span.start_tag_raw, HEADER_BAND_ATTRIBUTE)
    try:
        declared = int((raw_band or "").strip())
    except ValueError:
        return html

    body_style = _read_attribute(body_span.start_tag_raw, _STYLE_ATTRIBUTE_NAME) or ""
    if _is_body_pinned(declarations(body_style)):
        prop, expected = "top", declared + contract.content_gap_px
    else:
        prop, expected = "margin-top", contract.content_gap_px
    result = apply_authored_patch(
        html,
        AuthoredPatchPlan.model_validate(
            {"actions": [{"action": "setStyle", "qid": body_qid, "styles": {prop: f"{expected}px"}}]}
        ),
    )
    return result.html if result.ok else html


_DUPLICATED_SUFFIX = "_duplicated"


def repair_header_contract(
    html: str,
    contract: AuthoredHeaderContract,
    *,
    role: str,
    content: str = "",
) -> HeaderRepairResult:
    """Fix the mechanical header violations in one slide, in order: drop an
    unrecognised ``data-header-part``; keep a duplicated part's first
    occurrence and strip the attribute (or the element, if empty) from the
    rest; clamp an out-of-bounds band (attribute and CSS height together);
    re-pin the body's top to the (possibly just-clamped) band. Everything
    else is left in ``remaining`` for the caller to route to re-authoring."""

    violations = validate_header_contract(html, contract, role=role, content=content)
    if not violations:
        return HeaderRepairResult(html=html, fixed=(), remaining=(), changed=False)

    current = html
    if "header_part_unknown" in violations:
        current = _drop_unknown_parts(current)

    duplicated_parts = frozenset(
        code[len("header_") : -len(_DUPLICATED_SUFFIX)]
        for code in violations
        if code.startswith("header_") and code.endswith(_DUPLICATED_SUFFIX)
    ) & HEADER_PART_VALUES
    if duplicated_parts:
        current = _dedupe_parts(current, duplicated_parts)

    if "header_band_out_of_bounds" in violations:
        current = _clamp_band(current, contract)

    # Re-check before touching the body: the band clamp above may have just
    # changed the number the body's offset is measured against.
    interim = validate_header_contract(current, contract, role=role, content=content)
    if "header_body_top_mismatch" in interim or "header_body_top_unreadable" in interim:
        current = _repin_body_top(current, contract, header_qids(current))

    remaining = validate_header_contract(current, contract, role=role, content=content)
    fixed = tuple(code for code in violations if code not in remaining)
    return HeaderRepairResult(
        html=current, fixed=fixed, remaining=remaining, changed=current != html
    )


__all__ = ["HeaderRepairResult", "repair_header_contract"]
