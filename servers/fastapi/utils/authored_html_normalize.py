"""Normalise harmless authored-HTML admission findings before the renderer gate.

Implements design item A2 (``docs/generation-programme-master-design-20260904.md``,
section 4). ``utils.renderer_isolation.admit_authored_html`` is, and stays, the
security boundary: this module never widens what it accepts. It only rewrites a
small, explicit whitelist of findings that are harmless to the renderer and cost
real decks for a one-line CSS slip or a leftover ``hidden`` attribute, so those
documents reach admission already clean instead of failing it outright.

Whitelist (nothing more):

* CSS declarations for ``break-inside``, ``break-before``, ``break-after``,
  ``page-break-inside``, ``page-break-before``, ``page-break-after``,
  ``orphans`` and ``widows`` -- in both inline ``style="..."`` attributes and
  ``<style>`` blocks -- are stripped.
* A ``hidden`` attribute on an element that has no *bound source text* (the
  element or any descendant carries ``data-source-block-id`` or ``data-qid``,
  see ``services/source_content/source_stamp_dom.py`` and
  ``services/composer/render_text.py``) removes the whole element: the author
  meant it to disappear and admission does not accept ``hidden``.
* A ``hidden`` attribute on an element WITH bound source text only has the
  attribute stripped, so the gate re-measures the element instead of losing
  source content.
* Anything else the admission policy would reject is left untouched --
  admission still rejects it, exactly as before this module existed.

Parsing strategy
-----------------
``admit_authored_html`` validates the *original* source string byte-for-byte
(it never reserializes a parsed tree; see ``_inject_csp`` in
``utils/renderer_isolation.py``), so this module also never reserializes. It
edits the source text directly and, when a document has no findings, returns
it unchanged -- byte-identical, so a document that already admits cleanly is
never perturbed by this module.

The CSS whitelist is applied with a targeted regex over declaration text
(inline ``style`` values and the declaration blocks inside ``<style>``
elements), never a full DOM rewrite, so untouched markup is left exactly as
written.

The ``hidden`` whitelist genuinely needs tag structure -- whether a "hidden"
element has bound-text descendants -- so it uses ``html.parser.HTMLParser``,
the same parser ``renderer_isolation._PolicyParser`` and
``utils.llm_calls.author_slide._SourceSpanParser`` already use, exactly as
those do: to locate exact byte spans of real tags via ``get_starttag_text()``
and line/column position, never to reserialize. It runs only when the literal
substring ``hidden`` appears anywhere in the document, so a document without
it is provably untouched. On anything the parser cannot cleanly resolve
(unbalanced tags, an unmatched end tag) it leaves the document alone and lets
admission reject it as before -- normalisation is best-effort, never a reason
a still-invalid document looks valid.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

from utils.renderer_markup_policy import VOID_TAGS

#: Declarations normalised out of both inline ``style=""`` values and
#: ``<style>`` block rule bodies. Matches the A2 spec table exactly.
_FORBIDDEN_CSS_PROPERTIES: tuple[str, ...] = (
    "break-inside",
    "break-before",
    "break-after",
    "page-break-inside",
    "page-break-before",
    "page-break-after",
    "orphans",
    "widows",
)

#: A declaration for one of the forbidden properties, anchored to a real
#: declaration boundary (start of the text being scanned, right after a
#: ``;`` separator, or right after a rule's opening ``{``) so a value that
#: merely contains one of these words as a substring is never matched.
_FORBIDDEN_DECLARATION = re.compile(
    r"(?i)(?:^|(?<=[;{]))\s*(?:"
    + "|".join(re.escape(prop) for prop in _FORBIDDEN_CSS_PROPERTIES)
    + r")\s*:\s*[^;}]*"
)

#: A real ``style="..."``/``style='...'`` attribute -- required leading
#: whitespace excludes the ``<style`` tag name itself (never preceded by a
#: space in source markup).
_STYLE_ATTRIBUTE = re.compile(r"(?is)(\sstyle\s*=\s*)(\"[^\"]*\"|'[^']*')")

#: A ``<style>...</style>`` element, non-greedy so a document with several
#: style blocks is handled one at a time.
_STYLE_BLOCK = re.compile(r"(?is)(<style\b[^>]*>)(.*?)(</style\s*>)")

#: One CSS rule inside a ``<style>`` block's text: ``selector { declarations }``.
#: The policy itself refuses nested/at-rule CSS (``_UNSAFE_CSS`` /
#: ``_CSS_BLOCK`` in ``utils/renderer_isolation.py``), so blocks are flat.
_CSS_RULE_BLOCK = re.compile(r"([^{}]*)\{([^{}]*)\}")

#: The attribute marking bound source text (``services/source_content/`` and
#: ``services/composer/render_text.py``): present on the element itself or
#: any descendant.
_BOUND_TEXT_ATTRIBUTE = re.compile(r"(?i)\bdata-(?:source-block-id|qid)\s*=")

#: A ``hidden`` boolean attribute on a start tag, with or without a value.
#: Requires leading whitespace so ``aria-hidden`` is never matched.
_HIDDEN_ATTRIBUTE = re.compile(
    r"""(?i)\s+hidden\b(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]*))?"""
)


@dataclass(frozen=True, slots=True)
class NormalizedHtml:
    """Result of :func:`normalize_authored_html`.

    ``normalizations`` is empty, and ``html`` is byte-identical to the input,
    when no whitelisted finding fired.
    """

    html: str
    normalizations: list[dict] = field(default_factory=list)


def normalize_authored_html(html: str) -> NormalizedHtml:
    """Strip the whitelisted harmless findings from authored HTML.

    Returns the (possibly rewritten) document plus one entry per rule that
    fired, each ``{"rule": <id>, "detail": <short str>, "count": n}``. A
    document with no whitelisted findings is returned unchanged.
    """

    if not isinstance(html, str) or not html:
        return NormalizedHtml(html=html, normalizations=[])

    result = html
    normalizations: list[dict] = []

    result, css_count, css_properties = _strip_forbidden_css(result)
    if css_count:
        normalizations.append(
            {
                "rule": "css_break_control_property_stripped",
                "detail": ", ".join(sorted(css_properties)),
                "count": css_count,
            }
        )

    # Cheap short-circuit: never invoke the parser on a document that cannot
    # possibly carry a `hidden` attribute, so the common case stays a plain
    # string scan and the result stays byte-identical.
    if "hidden" in result.lower():
        result, hidden_findings = _normalize_hidden_elements(result)
        removed_tags = sorted({f.tag for f in hidden_findings if f.action == "remove"})
        removed_count = sum(1 for f in hidden_findings if f.action == "remove")
        if removed_count:
            normalizations.append(
                {
                    "rule": "hidden_element_without_bound_text_removed",
                    "detail": ", ".join(removed_tags),
                    "count": removed_count,
                }
            )
        stripped_tags = sorted({f.tag for f in hidden_findings if f.action == "strip"})
        stripped_count = sum(1 for f in hidden_findings if f.action == "strip")
        if stripped_count:
            normalizations.append(
                {
                    "rule": "hidden_attribute_stripped_bound_text",
                    "detail": ", ".join(stripped_tags),
                    "count": stripped_count,
                }
            )

    return NormalizedHtml(html=result, normalizations=normalizations)


# --- CSS declaration stripping ------------------------------------------------


def _strip_declarations(text: str) -> tuple[str, int, set[str]]:
    """Remove every forbidden declaration from one flat declaration list."""

    count = 0
    properties: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        # The matched text is `<property>: <value>`; recover just the name.
        name = match.group(0).split(":", 1)[0].strip().lower()
        properties.add(name)
        return ""

    new_text = _FORBIDDEN_DECLARATION.sub(_replace, text)
    return new_text, count, properties


def _strip_style_attributes(html: str) -> tuple[str, int, set[str]]:
    total = 0
    properties: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        nonlocal total
        prefix, quoted = match.group(1), match.group(2)
        quote = quoted[0]
        value = quoted[1:-1]
        new_value, count, found = _strip_declarations(value)
        if not count:
            return match.group(0)
        total += count
        properties.update(found)
        return f"{prefix}{quote}{new_value}{quote}"

    new_html = _STYLE_ATTRIBUTE.sub(_replace, html)
    return new_html, total, properties


def _strip_style_blocks(html: str) -> tuple[str, int, set[str]]:
    total = 0
    properties: set[str] = set()

    def _replace_block(match: re.Match[str]) -> str:
        nonlocal total
        open_tag, content, close_tag = match.group(1), match.group(2), match.group(3)

        def _replace_rule(rule_match: re.Match[str]) -> str:
            nonlocal total
            selector, declarations = rule_match.group(1), rule_match.group(2)
            new_declarations, count, found = _strip_declarations(declarations)
            if not count:
                return rule_match.group(0)
            total += count
            properties.update(found)
            return f"{selector}{{{new_declarations}}}"

        new_content = _CSS_RULE_BLOCK.sub(_replace_rule, content)
        if new_content == content:
            return match.group(0)
        return f"{open_tag}{new_content}{close_tag}"

    new_html = _STYLE_BLOCK.sub(_replace_block, html)
    return new_html, total, properties


def _strip_forbidden_css(html: str) -> tuple[str, int, set[str]]:
    html, count_attrs, properties_attrs = _strip_style_attributes(html)
    html, count_blocks, properties_blocks = _strip_style_blocks(html)
    return html, count_attrs + count_blocks, properties_attrs | properties_blocks


# --- `hidden` attribute normalisation -----------------------------------------


@dataclass(frozen=True, slots=True)
class _HiddenElementSpan:
    tag: str
    element_start: int
    element_end: int
    start_tag_start: int
    start_tag_end: int
    start_tag_raw: str
    bound_text: bool


@dataclass(frozen=True, slots=True)
class _HiddenFinding:
    tag: str
    action: str  # "remove" or "strip"


@dataclass
class _OpenFrame:
    tag: str
    start: int
    tag_end: int
    raw: str
    has_hidden: bool


class _HiddenScanParser(HTMLParser):
    """Locate byte spans of elements carrying a literal ``hidden`` attribute.

    Mirrors ``utils.llm_calls.author_slide._SourceSpanParser``'s technique
    (``get_starttag_text()`` plus line/column -> absolute offset) so tags are
    never reserialized, only sliced by exact byte offset.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0]
        self._line_offsets.extend(match.end() for match in re.finditer(r"\n", source))
        self._stack: list[_OpenFrame] = []
        self.elements: list[_HiddenElementSpan] = []
        self.malformed = False

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def _record_start(
        self, tag: str, attrs: list[tuple[str, str | None]], *, void: bool
    ) -> None:
        raw = self.get_starttag_text()
        if raw is None:
            self.malformed = True
            return
        start = self._offset()
        end = start + len(raw)
        has_hidden = any((name or "").lower() == "hidden" for name, _ in attrs)
        if void:
            if has_hidden:
                self.elements.append(
                    _HiddenElementSpan(
                        tag=tag.lower(),
                        element_start=start,
                        element_end=end,
                        start_tag_start=start,
                        start_tag_end=end,
                        start_tag_raw=raw,
                        bound_text=bool(_BOUND_TEXT_ATTRIBUTE.search(raw)),
                    )
                )
            return
        self._stack.append(
            _OpenFrame(tag=tag.lower(), start=start, tag_end=end, raw=raw, has_hidden=has_hidden)
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_start(tag, attrs, void=tag.lower() in VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_start(tag, attrs, void=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack or self._stack[-1].tag != tag:
            # Unbalanced structure: bail out. Admission will reject the
            # document on its own terms; this module must not guess.
            self.malformed = True
            return
        frame = self._stack.pop()
        close_start = self._offset()
        close = self._source.find(">", close_start)
        if close < 0:
            self.malformed = True
            return
        element_end = close + 1
        if frame.has_hidden:
            self.elements.append(
                _HiddenElementSpan(
                    tag=frame.tag,
                    element_start=frame.start,
                    element_end=element_end,
                    start_tag_start=frame.start,
                    start_tag_end=frame.tag_end,
                    start_tag_raw=frame.raw,
                    bound_text=bool(
                        _BOUND_TEXT_ATTRIBUTE.search(self._source[frame.start : element_end])
                    ),
                )
            )


def _scan_hidden_elements(html: str) -> list[_HiddenElementSpan]:
    parser = _HiddenScanParser(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []
    if parser.malformed:
        return []
    return parser.elements


def _strip_hidden_attribute(raw: str) -> str:
    return _HIDDEN_ATTRIBUTE.sub("", raw, count=1)


def _apply_edits(source: str, edits: Sequence[tuple[int, int, str]]) -> str:
    result = source
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def _normalize_hidden_elements(html: str) -> tuple[str, list[_HiddenFinding]]:
    elements = _scan_hidden_elements(html)
    if not elements:
        return html, []

    elements.sort(key=lambda element: element.element_start)
    edits: list[tuple[int, int, str]] = []
    findings: list[_HiddenFinding] = []
    # An outer hidden-and-removed element already deletes every descendant,
    # including any nested hidden element; skip anything that starts inside
    # an already-accepted removal so edits never overlap.
    removed_through = -1
    for element in elements:
        if element.element_start < removed_through:
            continue
        if element.bound_text:
            new_start_tag = _strip_hidden_attribute(element.start_tag_raw)
            if new_start_tag == element.start_tag_raw:
                continue
            edits.append((element.start_tag_start, element.start_tag_end, new_start_tag))
            findings.append(_HiddenFinding(tag=element.tag, action="strip"))
        else:
            edits.append((element.element_start, element.element_end, ""))
            removed_through = max(removed_through, element.element_end)
            findings.append(_HiddenFinding(tag=element.tag, action="remove"))

    if not edits:
        return html, []
    return _apply_edits(html, edits), findings


__all__ = ["NormalizedHtml", "normalize_authored_html"]
