"""Exact-span start-tag parsing and in-place attribute edits for authored HTML.

Every rewrite here replaces a start tag's own source span and leaves every
other byte untouched, so admission sees exactly what the model wrote apart
from the one attribute a caller meant to change.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Sequence


@dataclass(frozen=True)
class _SourceStartTag:
    start: int
    end: int
    name: str
    attrs: tuple[tuple[str, str | None], ...]
    raw: str


class _SourceSpanParser(HTMLParser):
    """Collect exact spans for real start tags, excluding comments and raw text."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_offsets = [0]
        self._line_offsets.extend(match.end() for match in re.finditer(r"\n", source))
        self.tags: list[_SourceStartTag] = []

    def _record(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text()
        if raw is None:
            return
        line, column = self.getpos()
        start = self._line_offsets[line - 1] + column
        self.tags.append(
            _SourceStartTag(
                start=start,
                end=start + len(raw),
                name=tag.lower(),
                attrs=tuple((name.lower(), value) for name, value in attrs),
                raw=raw,
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(tag, attrs)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._record(tag, attrs)


def _source_start_tags(source: str) -> tuple[_SourceStartTag, ...]:
    parser = _SourceSpanParser(source)
    try:
        parser.feed(source)
        parser.close()
    except Exception:
        # Leave malformed source byte-for-byte intact so renderer admission rejects it.
        return ()
    return tuple(parser.tags)


def _apply_source_replacements(
    source: str, replacements: Sequence[tuple[int, int, str]]
) -> str:
    result = source
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def _remove_start_tag_attribute(raw: str, target: str) -> str:
    """Remove a real tag attribute without matching inside another quoted value."""
    spans: list[tuple[int, int, str]] = []
    length = len(raw)
    cursor = 1
    while cursor < length and not raw[cursor].isspace() and raw[cursor] not in "/>":
        cursor += 1
    while cursor < length:
        whitespace_start = cursor
        while cursor < length and raw[cursor].isspace():
            cursor += 1
        if cursor >= length or raw[cursor] in "/>":
            break
        name_start = cursor
        while (
            cursor < length
            and not raw[cursor].isspace()
            and raw[cursor] not in "=/>"
        ):
            cursor += 1
        name = raw[name_start:cursor].lower()
        while cursor < length and raw[cursor].isspace():
            cursor += 1
        if cursor < length and raw[cursor] == "=":
            cursor += 1
            while cursor < length and raw[cursor].isspace():
                cursor += 1
            if cursor < length and raw[cursor] in "\"'":
                quote = raw[cursor]
                cursor += 1
                while cursor < length and raw[cursor] != quote:
                    cursor += 1
                if cursor < length:
                    cursor += 1
            else:
                while (
                    cursor < length
                    and not raw[cursor].isspace()
                    and raw[cursor] != ">"
                ):
                    cursor += 1
        if name == target:
            spans.append((whitespace_start, cursor, ""))
    return _apply_source_replacements(raw, spans)


def _append_start_tag_attribute(raw: str, name: str, value: str) -> str:
    """Append one quoted attribute to a parsed start tag without reserializing it."""

    closing = raw.rfind("/>")
    if closing < 0:
        closing = raw.rfind(">")
    if closing < 0:
        return raw
    prefix = raw[:closing]
    separator = "" if prefix.endswith((" ", "\t", "\r", "\n")) else " "
    escaped = _html.escape(value, quote=True)
    return f'{prefix}{separator}{name}="{escaped}"{raw[closing:]}'


__all__ = [
    "_SourceSpanParser",
    "_SourceStartTag",
    "_append_start_tag_attribute",
    "_apply_source_replacements",
    "_remove_start_tag_attribute",
    "_source_start_tags",
]
