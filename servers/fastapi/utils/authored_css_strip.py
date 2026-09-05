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

Four property families are deliberately left alone even though they are not
in ``ALLOWED_CSS_PROPERTIES`` (see ``renderer_css_policy`` module docstring
for why each is refused): ``content``, ``content-visibility``, ``contain``,
and any ``--*`` custom property. Leaving them in place means admission still
rejects a slide that uses them for correctness reasons, not renderer-parser
reasons -- stripping them here would silently launder a real defect.

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

#: Refused by the renderer for correctness reasons, not because the parser
#: cannot handle them -- see ``renderer_css_policy`` module docstring. Never
#: stripped: a slide using them keeps failing admission on its own terms.
_EXEMPT_PROPERTIES: frozenset[str] = frozenset({"content", "content-visibility", "contain"})

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


def _mask(text: str) -> str:
    """Blank out comments and string literals, preserving length/offsets."""

    text = _CSS_COMMENT.sub(lambda m: " " * len(m.group(0)), text)
    text = _STRING_LITERAL.sub(lambda m: " " * len(m.group(0)), text)
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


def _strip_declarations(text: str) -> tuple[str, dict[str, int]]:
    """Remove every declaration whose property is not allowed or exempt."""

    counts: dict[str, int] = {}

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1).strip().lower()
        if name.startswith("--") or name in _EXEMPT_PROPERTIES or name in ALLOWED_CSS_PROPERTIES:
            return match.group(0)
        counts[name] = counts.get(name, 0) + 1
        return ""

    return _DECLARATION.sub(_replace, text), counts


def _merge_counts(target: dict[str, int], found: dict[str, int]) -> None:
    for name, count in found.items():
        target[name] = target.get(name, 0) + count


def _strip_style_attributes(html: str, property_counts: dict[str, int]) -> str:
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


def _strip_flat_rules(content: str, property_counts: dict[str, int]) -> str:
    def _replace_rule(match: re.Match[str]) -> str:
        selector, declarations = match.group(1), match.group(2)
        new_declarations, found = _strip_declarations(declarations)
        if not found:
            return match.group(0)
        _merge_counts(property_counts, found)
        return f"{selector}{{{new_declarations}}}"

    return _CSS_RULE_BLOCK.sub(_replace_rule, content)


def _strip_style_blocks(
    html: str,
    property_counts: dict[str, int],
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


def strip_disallowed_css(html: str) -> tuple[str, list[dict]]:
    """Strip CSS declarations/at-rules ``admit_authored_html`` would refuse.

    Returns the (possibly rewritten) document plus one record per rule that
    fired: ``{"rule": "css_property_stripped", "property": <name>, "count": n}``,
    ``{"rule": "css_at_rule_hoisted", "at_rule": <keyword>, "count": n}``, or
    ``{"rule": "css_at_rule_removed", "at_rule": <keyword>, "count": n}``. A
    document with nothing to strip is returned unchanged -- byte-identical.
    """

    if not isinstance(html, str) or not html:
        return html, []

    property_counts: dict[str, int] = {}
    hoisted_counts: dict[str, int] = {}
    removed_counts: dict[str, int] = {}

    result = _strip_style_blocks(html, property_counts, hoisted_counts, removed_counts)
    result = _strip_style_attributes(result, property_counts)

    records: list[dict] = []
    for name in sorted(property_counts):
        records.append(
            {"rule": "css_property_stripped", "property": name, "count": property_counts[name]}
        )
    for keyword in sorted(hoisted_counts):
        records.append(
            {"rule": "css_at_rule_hoisted", "at_rule": keyword, "count": hoisted_counts[keyword]}
        )
    for keyword in sorted(removed_counts):
        records.append(
            {"rule": "css_at_rule_removed", "at_rule": keyword, "count": removed_counts[keyword]}
        )
    return result, records


__all__ = ["strip_disallowed_css"]
