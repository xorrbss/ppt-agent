"""Canonicalize the semantic aliases a full re-author is allowed to emit.

Extracted from ``author_vision_qa`` unchanged: it is pure text work over one
slide's HTML with no dependency on the vqa pass, its caches or its provider
scope, and that module is far over the file-size limit.
"""

from __future__ import annotations

from html.parser import HTMLParser

from utils.llm_calls.author_source_tags import (
    _append_start_tag_attribute,
    _apply_source_replacements,
    _remove_start_tag_attribute,
    _source_start_tags,
)

class _SemanticAliasEndTagParser(HTMLParser):
    """Collect exact end-tag spans without matching strings in raw-text nodes."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0]
        self._line_offsets.extend(
            offset + 1 for offset, character in enumerate(source) if character == "\n"
        )
        self.tags: list[tuple[int, int, str]] = []

    def handle_endtag(self, tag: str) -> None:
        line, column = self.getpos()
        start = self._line_offsets[line - 1] + column
        end = self._source.find(">", start)
        if end >= 0:
            self.tags.append((start, end + 1, tag.lower()))


def _canonicalize_full_reauthor_semantic_aliases(source: str) -> str:
    """Map a safe semantic ``time`` alias onto the admitted phrasing surface.

    The shared author-stage source parser ensures replacements touch real tags,
    not quoted text. ``datetime`` is preserved as the span's inert ``title``
    value because the renderer does not admit a ``datetime`` attribute on span.
    Ambiguous attributes remain byte-for-byte unchanged so final renderer
    admission fails closed. Active/raw-content tags are never aliases here.
    """

    replacements: list[tuple[int, int, str]] = []
    time_tags = tuple(tag for tag in _source_start_tags(source) if tag.name == "time")
    for tag in time_tags:
        names = [name for name, _value in tag.attrs]
        datetime_values = [
            value for name, value in tag.attrs if name == "datetime"
        ]
        if len(names) != len(set(names)) or len(datetime_values) > 1:
            return source
        if datetime_values and (
            "title" in names
            or datetime_values[0] is None
            or not str(datetime_values[0]).strip()
        ):
            return source
        rewritten = "<span" + tag.raw[1 + len(tag.name) :]
        if datetime_values:
            rewritten = _remove_start_tag_attribute(rewritten, "datetime")
            rewritten = _append_start_tag_attribute(
                rewritten, "title", str(datetime_values[0])
            )
        replacements.append((tag.start, tag.end, rewritten))

    end_parser = _SemanticAliasEndTagParser(source)
    try:
        end_parser.feed(source)
        end_parser.close()
    except Exception:
        return source
    time_end_tags = tuple(
        (start, end) for start, end, name in end_parser.tags if name == "time"
    )
    non_void_time_tags = sum(
        not tag.raw.rstrip().endswith("/>") for tag in time_tags
    )
    if len(time_end_tags) != non_void_time_tags:
        return source
    replacements.extend(
        (start, end, "</span>")
        for start, end in time_end_tags
    )
    return _apply_source_replacements(source, replacements)
