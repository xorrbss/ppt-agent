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
* A ``<br>`` inside a title-role element is replaced with one space so a
  Korean title wraps where the renderer puts it, not where the model forced
  it (rule and title detection in ``utils/authored_html_normalize_rules_title.py``).
* A ``name`` attribute is always stripped, never the element: it is refused
  outright by ``renderer_markup_policy`` yet carries no behaviour on the
  allowed tag surface (the tags that would make it load-bearing -- ``form``,
  ``input``, ``iframe`` -- are already in ``DROP_TAGS``) (rule in
  ``utils/authored_html_normalize_rules_name.py``).
* Closing tags for HTML void elements (for example ``</img>`` and ``</br>``)
  are deleted. Browsers ignore these parse-error tokens, so this preserves the
  rendered DOM while avoiding a needless provider retry.
* Any other CSS property not in ``renderer_css_policy.ALLOWED_CSS_PROPERTIES``,
  and any ``@media``/``@supports``/other at-rule, is handled by
  ``utils.authored_css_strip`` (H1,
  ``docs/authored-quality-speed-programme-design-20260905.md`` section 6):
  disallowed declarations are removed, a plain screen/all/feature-query
  `@media`/`@supports` block is hoisted, and every other at-rule
  (`@media print`, `@keyframes`, `@font-face`, `@import`, ...) is removed
  whole.
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

The ``hidden`` and ``name`` whitelists genuinely need tag structure (whether
a "hidden" element has bound-text descendants; the exact span of a ``name``
attribute on its own tag), so each uses ``html.parser.HTMLParser``, the same
parser ``renderer_isolation._PolicyParser`` and
``utils.llm_calls.author_slide._SourceSpanParser`` already use, exactly as
those do: to locate exact byte spans of real tags via ``get_starttag_text()``
and line/column position, never to reserialize. Each runs only when its
literal substring (``hidden``, ``name``) appears anywhere in the document, so
a document without it is provably untouched. On anything the parser cannot
cleanly resolve (unbalanced tags, an unmatched end tag) it leaves the
document alone and lets admission reject it as before -- normalisation is
best-effort, never a reason a still-invalid document looks valid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from utils.authored_html_normalize_edits import apply_edits
from utils.authored_html_normalize_rules_name import apply_name_attribute_rule
from utils.authored_html_normalize_rules_title import apply_title_line_break_rule
from utils.authored_css_strip import (
    strip_break_control_declarations,
    strip_disallowed_css,
)
from utils.authored_html_tree import NormalizationProfile, normalize_authored_html_tree
from utils.authored_void_end_tag_normalize import drop_void_element_end_tags
from utils.renderer_markup_policy import VOID_TAGS

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


def normalize_authored_html_legacy(html: str) -> NormalizedHtml:
    """Strip the whitelisted harmless findings from authored HTML.

    Returns the (possibly rewritten) document plus one entry per rule that
    fired, each ``{"rule": <id>, "detail": <short str>, "count": n}``. A
    document with no whitelisted findings is returned unchanged.
    """

    if not isinstance(html, str) or not html:
        return NormalizedHtml(html=html, normalizations=[])

    result = html
    normalizations: list[dict] = []

    result, void_end_tag_records = drop_void_element_end_tags(result)
    normalizations.extend(void_end_tag_records)

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

    result, title_break_entry = apply_title_line_break_rule(result)
    if title_break_entry is not None:
        normalizations.append(title_break_entry)

    result, name_entry = apply_name_attribute_rule(result)
    if name_entry is not None:
        normalizations.append(name_entry)

    # H1: strip any CSS admission would otherwise refuse outright, instead of
    # failing the whole document over it (see ``authored_css_strip``).
    result, css_strip_records = strip_disallowed_css(result)
    normalizations.extend(css_strip_records)

    return NormalizedHtml(html=result, normalizations=normalizations)


# --- CSS declaration stripping ------------------------------------------------


def _strip_declarations(text: str) -> tuple[str, int, set[str]]:
    """Remove every forbidden declaration from one flat declaration list."""

    return strip_break_control_declarations(text)


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
    return apply_edits(html, edits), findings


def normalize_authored_html(
    html: str,
    *,
    profile: NormalizationProfile = "document",
    mode: str | None = None,
) -> NormalizedHtml:
    """Normalize through the selected ``legacy`` or HTML5 ``tree`` path."""

    # Lazy: ``services`` sits above ``utils``, and the attempt's frozen rollout
    # profile is the only reader of the normalization switch there is.
    from services.authored_rollout_profile import current_rollout_profile

    selected = (mode or current_rollout_profile().html_normalization).strip().lower()
    if selected == "legacy":
        return normalize_authored_html_legacy(html)
    if selected == "tree":
        normalized, receipts = normalize_authored_html_tree(html, profile=profile)
        return NormalizedHtml(html=normalized, normalizations=receipts)
    raise ValueError(f"unknown authored HTML normalization mode: {selected!r}")


__all__ = [
    "NormalizedHtml",
    "normalize_authored_html",
    "normalize_authored_html_legacy",
    "normalize_authored_html_tree",
]
