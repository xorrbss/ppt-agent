"""Strip CSS admission would refuse, instead of failing the whole document.

Implements design item H1
(``docs/authored-quality-speed-programme-design-20260905.md``, section 6):
a single non-allowlisted CSS property, or a `@media`/nested at-rule block,
currently rejects the entire authored document at
``utils.renderer_isolation.admit_authored_html`` and forces a re-author (or,
on a second failure, kills the deck). Most of what trips this is cosmetic
(``will-change``, ``@media print``) or a plain responsive query the single
static capture never needs conditionally applied. This module removes only
what the renderer would refuse anyway, so the document then admits cleanly
instead of round-tripping through re-authoring.

This is **not** a widening of the admission policy: ``renderer_isolation``
and ``renderer_css_policy`` stay byte-identical and remain the sole security
boundary. This module runs *before* admission and only ever deletes text a
correct admission call would already reject; it never lets through anything
admission would not otherwise accept.

Three property families are deliberately left alone even though they are not
in ``ALLOWED_CSS_PROPERTIES`` (see ``renderer_css_policy`` module docstring
for why each is refused): ``content-visibility``, ``contain``, and any ``--*``
custom property. Leaving them in place means admission still rejects a slide
that uses them for correctness reasons, not renderer-parser reasons --
stripping them here would silently launder a real defect.

``content`` is **not** in that list: every ``content`` declaration is stripped,
whatever its value. Generated pseudo-element content is never source text --
source text lives only in DOM nodes carrying ``data-source-block-id``, which a
``::before``/``::after`` rule cannot produce -- and a pseudo-element rule left
without a ``content`` declaration is simply not rendered. So deleting the
declaration cannot launder a visible-text defect, while keeping it killed whole
decks over decorative counters and numbering (`content: "1"`, `content: ""`).
The stripped record carries a ``value_class`` of ``empty`` (an exact empty
string, ``none`` or ``normal``) or ``text`` (anything else, including
``attr()`` and counters) so the receipt still says which kind fired.

Parsing strategy
-----------------
Same approach as ``utils.authored_html_normalize``: a targeted regex over
declaration text, never a full DOM/CSS reparse, and the original source
string is edited by byte span so ``admit_authored_html`` -- which validates
the *original* string, never a reserialised tree -- sees exactly this
module's output. A document with nothing to strip comes back byte-identical.

At-rules need one exception to "regex only": ``@media``/``@supports`` blocks
nest braces, which a flat declaration regex cannot balance. Those blocks are
located with a small brace-depth scan (comments and string literals masked
to spaces of equal length so they cannot skew the count, then sliced back out
of the *original* text) and either hoisted (a plain screen/all/feature query:
the inner rules are unwrapped, unconditionally applied) or removed whole (any
other at-rule: `@media print`, `@keyframes`, `@font-face`, `@import`, `@page`,
an at-rule inside another at-rule, or a query this module cannot prove is
plain). The pass repeats until a fixed point so a hoisted block's own nested
at-rules are handled too, capped so a pathological document cannot loop.
"""

from __future__ import annotations

import re

from utils.renderer_css_policy import ALLOWED_CSS_PROPERTIES

#: The earlier A2 normalizer gives these pagination declarations their own
#: receipt.  Keep the predicate here so the legacy source-span pass and the
#: tree pass cannot drift.
BREAK_CONTROL_PROPERTIES: tuple[str, ...] = (
    "break-inside",
    "break-before",
    "break-after",
    "page-break-inside",
    "page-break-before",
    "page-break-after",
    "orphans",
    "widows",
)

_BREAK_CONTROL_DECLARATION = re.compile(
    r"(?i)(?:^|(?<=[;{]))\s*(?:"
    + "|".join(re.escape(prop) for prop in BREAK_CONTROL_PROPERTIES)
    + r")\s*:\s*[^;}]*"
)

#: Refused by the renderer for correctness reasons, not because the parser
#: cannot handle them -- see ``renderer_css_policy`` module docstring. Never
#: stripped: a slide using them keeps failing admission on its own terms.
_EXEMPT_PROPERTIES: frozenset[str] = frozenset({"content-visibility", "contain"})

#: ``content`` values that generate nothing at all. Every other value is still
#: stripped; this only classifies the record's ``value_class``.
_NON_RENDERING_CONTENT_VALUE = re.compile(
    r"(?ix)^\s*(?:''|\"\"|none|normal)\s*(?:!\s*important\s*)?$"
)

_MAX_AT_RULE_PASSES = 8

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

#: A real ``style="..."``/``style='...'`` attribute.
_STYLE_ATTRIBUTE = re.compile(r"(?is)(\sstyle\s*=\s*)(\"[^\"]*\"|'[^']*')")

#: A ``<style>...</style>`` element, non-greedy so multiple blocks are each
#: handled on their own.
_STYLE_BLOCK = re.compile(r"(?is)(<style\b[^>]*>)(.*?)(</style\s*>)")

#: One flat CSS rule: ``selector { declarations }``, no nested braces. Only
#: valid once at-rules (which nest) have already been hoisted or removed.
_CSS_RULE_BLOCK = re.compile(r"([^{}]*)\{([^{}]*)\}")

#: One declaration, anchored to a real boundary (start of the text, right
#: after ``;``, or right after a rule's opening ``{``) so a property name
#: appearing only inside a value is never matched.
_DECLARATION = re.compile(r"(?:^|(?<=[;{]))\s*(-{0,2}[a-zA-Z][a-zA-Z0-9-]*)\s*:\s*([^;}]*)")

#: A media/supports feature clause such as ``(min-width: 768px)``.
_FEATURE_CLAUSE = r"\(\s*[a-zA-Z-]+\s*:\s*[^()]+\)"

#: A "plain" ``@media`` query: an optional ``screen``/``all``/``only screen``
#: keyword, and/or one or more feature clauses joined by ``and``. Anything
#: with ``not``, ``print``, ``speech``, a comma-separated list, or a feature
#: this pattern cannot parse is left unmatched (and therefore removed).
_PLAIN_MEDIA_QUERY = re.compile(
    r"(?i)^\s*(?:only\s+)?(?:screen|all)?\s*"
    rf"(?:(?:and\s*)?{_FEATURE_CLAUSE}\s*)*$"
)

#: A "plain" ``@supports`` query: one or more feature clauses joined by
#: ``and``, no ``not``/``or``.
_PLAIN_SUPPORTS_QUERY = re.compile(rf"(?i)^\s*(?:{_FEATURE_CLAUSE}\s*(?:and\s*)?)+$")


#: String literals are masked to this filler rather than to spaces: a value is
#: matched as ``[^;}]*`` right after ``\s*``, so a blanked-to-whitespace
#: ``content: ""`` would be swallowed by that ``\s*`` and read as an empty
#: value, cutting the wrong span. The filler is neither whitespace nor a CSS
#: delimiter and cannot start a property name, so every scan in this module
#: sees a literal as one opaque, harmless run of the right length.
_MASK_FILLER = chr(0)


def _mask(text: str) -> str:
    """Neutralise comments and string literals, preserving length/offsets."""

    text = _CSS_COMMENT.sub(lambda m: " " * len(m.group(0)), text)
    text = _STRING_LITERAL.sub(lambda m: _MASK_FILLER * len(m.group(0)), text)
    return text


def _is_plain_query(keyword: str, prelude: str) -> bool:
    query = _CSS_COMMENT.sub("", prelude).strip()
    if keyword == "media":
        return bool(_PLAIN_MEDIA_QUERY.match(query)) and query != ""
    if keyword == "supports":
        return bool(_PLAIN_SUPPORTS_QUERY.match(query))
    return False


def _find_top_level_at_rules(content: str) -> list[dict]:
    """Locate top-level ``@rule`` statements/blocks by brace-depth scanning."""

    masked = _mask(content)
    n = len(content)
    results: list[dict] = []
    depth = 0
    i = 0
    while i < n:
        ch = masked[i]
        if depth == 0 and ch == "@":
            match = re.match(r"@([A-Za-z-]+)", content[i:])
            if not match:
                i += 1
                continue
            keyword = match.group(1).lower()
            j = i + match.end()
            brace_depth = 0
            body_start: int | None = None
            end = n
            k = j
            while k < n:
                c = masked[k]
                if c == "{":
                    if brace_depth == 0:
                        body_start = k + 1
                    brace_depth += 1
                elif c == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        end = k + 1
                        break
                elif c == ";" and brace_depth == 0:
                    end = k + 1
                    body_start = None
                    break
                k += 1
            prelude_end = (body_start - 1) if body_start is not None else end - 1
            results.append(
                {
                    "start": i,
                    "end": end,
                    "keyword": keyword,
                    "prelude": content[j:prelude_end],
                    "body": content[body_start : end - 1] if body_start is not None else None,
                }
            )
            i = end
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        i += 1
    return results


def _process_at_rules(content: str) -> tuple[str, dict[str, int], dict[str, int]]:
    """Hoist plain `@media`/`@supports` blocks, remove every other at-rule.

    Repeats to a fixed point (bounded) so at-rules nested inside a hoisted
    block are handled too. Returns the rewritten content plus per-keyword
    hoist/removal counts.
    """

    hoisted: dict[str, int] = {}
    removed: dict[str, int] = {}
    for _ in range(_MAX_AT_RULE_PASSES):
        at_rules = _find_top_level_at_rules(content)
        if not at_rules:
            break
        edits: list[tuple[int, int, str]] = []
        for rule in at_rules:
            keyword = rule["keyword"]
            if keyword in ("media", "supports") and rule["body"] is not None and _is_plain_query(
                keyword, rule["prelude"]
            ):
                edits.append((rule["start"], rule["end"], rule["body"]))
                hoisted[keyword] = hoisted.get(keyword, 0) + 1
            else:
                edits.append((rule["start"], rule["end"], ""))
                removed[keyword] = removed.get(keyword, 0) + 1
        content = _apply_edits(content, edits)
    return content, hoisted, removed


def _apply_edits(source: str, edits: list[tuple[int, int, str]]) -> str:
    result = source
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


#: One stripped-property tally key: the property name plus, for ``content``
#: only, the class of the value that was removed.
_PropertyKey = tuple[str, str | None]


def _content_value_class(value: str) -> str:
    return "empty" if _NON_RENDERING_CONTENT_VALUE.fullmatch(value) else "text"


def strip_break_control_declarations(text: str) -> tuple[str, int, set[str]]:
    """Remove the A2 pagination declarations from one declaration list."""

    count = 0
    properties: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        properties.add(match.group(0).split(":", 1)[0].strip().lower())
        return ""

    return _BREAK_CONTROL_DECLARATION.sub(_replace, text), count, properties


def _strip_declarations(text: str) -> tuple[str, dict[_PropertyKey, int]]:
    """Remove every declaration whose property is not allowed or exempt.

    Declarations are located over the *masked* text so a quoted value holding
    a ``;`` or ``}`` -- ordinary in ``content`` -- cannot end the declaration
    early and leave half a string literal behind. Spans are then cut out of
    the original text, which is what admission validates.
    """

    counts: dict[_PropertyKey, int] = {}
    masked = _mask(text)
    edits: list[tuple[int, int, str]] = []
    for match in _DECLARATION.finditer(masked):
        name = match.group(1).strip().lower()
        if name.startswith("--") or name in _EXEMPT_PROPERTIES or name in ALLOWED_CSS_PROPERTIES:
            continue
        value_class = (
            _content_value_class(text[match.start(2) : match.end(2)])
            if name == "content"
            else None
        )
        key = (name, value_class)
        counts[key] = counts.get(key, 0) + 1
        edits.append((match.start(), match.end(), ""))
    if not edits:
        return text, counts
    return _apply_edits(text, edits), counts


def _merge_counts(target: dict, found: dict) -> None:
    for name, count in found.items():
        target[name] = target.get(name, 0) + count


def _strip_style_attributes(html: str, property_counts: dict[_PropertyKey, int]) -> str:
    def _replace(match: re.Match[str]) -> str:
        prefix, quoted = match.group(1), match.group(2)
        quote = quoted[0]
        value = quoted[1:-1]
        new_value, found = _strip_declarations(value)
        if not found:
            return match.group(0)
        _merge_counts(property_counts, found)
        return f"{prefix}{quote}{new_value}{quote}"

    return _STYLE_ATTRIBUTE.sub(_replace, html)


def _strip_flat_rules(content: str, property_counts: dict[_PropertyKey, int]) -> str:
    """Strip each flat rule's declaration list, leaving selector/braces alone.

    Rules are located over the masked text for the same reason declarations
    are: a ``}`` inside a quoted value must not be read as the rule's end.
    """

    masked = _mask(content)
    edits: list[tuple[int, int, str]] = []
    for match in _CSS_RULE_BLOCK.finditer(masked):
        start, end = match.start(2), match.end(2)
        new_declarations, found = _strip_declarations(content[start:end])
        if not found:
            continue
        _merge_counts(property_counts, found)
        edits.append((start, end, new_declarations))
    if not edits:
        return content
    return _apply_edits(content, edits)


def _strip_style_blocks(
    html: str,
    property_counts: dict[_PropertyKey, int],
    hoisted_counts: dict[str, int],
    removed_counts: dict[str, int],
) -> str:
    def _replace_block(match: re.Match[str]) -> str:
        open_tag, content, close_tag = match.group(1), match.group(2), match.group(3)
        content, hoisted, removed = _process_at_rules(content)
        _merge_counts(hoisted_counts, hoisted)
        _merge_counts(removed_counts, removed)
        content = _strip_flat_rules(content, property_counts)
        return f"{open_tag}{content}{close_tag}"

    return _STYLE_BLOCK.sub(_replace_block, html)


def _records(
    property_counts: dict[_PropertyKey, int],
    hoisted_counts: dict[str, int] | None = None,
    removed_counts: dict[str, int] | None = None,
) -> list[dict]:
    records: list[dict] = []
    for key in sorted(property_counts, key=lambda entry: (entry[0], entry[1] or "")):
        name, value_class = key
        record: dict = {"rule": "css_property_stripped", "property": name}
        if value_class is not None:
            record["value_class"] = value_class
        record["count"] = property_counts[key]
        records.append(record)
    for keyword in sorted(hoisted_counts or {}):
        records.append(
            {
                "rule": "css_at_rule_hoisted",
                "at_rule": keyword,
                "count": (hoisted_counts or {})[keyword],
            }
        )
    for keyword in sorted(removed_counts or {}):
        records.append(
            {
                "rule": "css_at_rule_removed",
                "at_rule": keyword,
                "count": (removed_counts or {})[keyword],
            }
        )
    return records


def strip_disallowed_declaration_list(css: str) -> tuple[str, list[dict]]:
    """Tree-pass adapter for one decoded inline ``style`` value."""

    rewritten, property_counts = _strip_declarations(css)
    return rewritten, _records(property_counts)


def strip_disallowed_stylesheet(css: str) -> tuple[str, list[dict]]:
    """Tree-pass adapter for the decoded text of one ``style`` element."""

    rewritten, hoisted_counts, removed_counts = _process_at_rules(css)
    property_counts: dict[_PropertyKey, int] = {}
    rewritten = _strip_flat_rules(rewritten, property_counts)
    return rewritten, _records(property_counts, hoisted_counts, removed_counts)


def strip_disallowed_css(html: str) -> tuple[str, list[dict]]:
    """Strip CSS declarations/at-rules ``admit_authored_html`` would refuse.

    Returns the (possibly rewritten) document plus one record per rule that
    fired: ``{"rule": "css_property_stripped", "property": <name>, "count": n}``
    (``content`` records carry an extra ``"value_class"`` of ``empty``/``text``),
    ``{"rule": "css_at_rule_hoisted", "at_rule": <keyword>, "count": n}``, or
    ``{"rule": "css_at_rule_removed", "at_rule": <keyword>, "count": n}``. A
    document with nothing to strip is returned unchanged -- byte-identical.
    """

    if not isinstance(html, str) or not html:
        return html, []

    property_counts: dict[_PropertyKey, int] = {}
    hoisted_counts: dict[str, int] = {}
    removed_counts: dict[str, int] = {}

    result = _strip_style_blocks(html, property_counts, hoisted_counts, removed_counts)
    result = _strip_style_attributes(result, property_counts)

    return result, _records(property_counts, hoisted_counts, removed_counts)


__all__ = [
    "BREAK_CONTROL_PROPERTIES",
    "strip_break_control_declarations",
    "strip_disallowed_css",
    "strip_disallowed_declaration_list",
    "strip_disallowed_stylesheet",
]
