"""Replay browser-approved authored fit edits without reserializing HTML.

The in-page fitter only appends declarations to an element's existing ``style``
attribute and addresses that element by ``data-qid``.  Replaying those exact
appends over the source string keeps authored HTML byte-for-byte intact outside
the approved start tags.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from utils.authored_dom_tags import StartTagCollector, attrs_dict

_STYLE_PATTERN = re.compile(
    r"(\sstyle)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?(?=\s|/?>)",
    re.IGNORECASE,
)
_TAG_END_PATTERN = re.compile(r"\s*/?>\s*$")


class FitEditError(ValueError):
    """Raised when a browser fit edit cannot be bound to the source HTML."""


def _locate_start_tag(html: str, qid: str) -> tuple[int, str]:
    parser = StartTagCollector()
    parser.feed(html)
    parser.close()
    cursor = 0
    found: list[tuple[int, str]] = []
    for _tag, raw_attrs, raw_tag in parser.tags:
        index = html.find(raw_tag, cursor)
        if index < 0:
            continue
        cursor = index + len(raw_tag)
        if attrs_dict(raw_attrs).get("data-qid", "") == qid:
            found.append((index, raw_tag))
    if not found:
        raise FitEditError(f"fit edit targets an unknown qid: {qid}")
    if len(found) != 1:
        raise FitEditError(f"fit edit targets a duplicated qid: {qid}")
    return found[0]


def _append_declarations(raw_tag: str, declarations: str) -> str:
    match = _STYLE_PATTERN.search(raw_tag)
    if match is None:
        end = _TAG_END_PATTERN.search(raw_tag)
        if end is None:
            raise FitEditError("fit edit cannot address malformed start tag")
        escaped = declarations.replace('"', "&quot;")
        return f'{raw_tag[:end.start()]} style="{escaped}"{raw_tag[end.start():]}'
    existing = next((value for value in match.groups()[1:] if value is not None), "")
    if existing == declarations or existing.endswith(";" + declarations):
        return raw_tag
    separator = ";" if existing else ""
    for group, escape in ((2, lambda value: value.replace('"', "&quot;")),
                          (3, lambda value: value.replace("'", "&#39;")),
                          (4, lambda value: value)):
        if match.group(group) is None:
            continue
        end = match.end(group)
        return raw_tag[:end] + separator + escape(declarations) + raw_tag[end:]
    escaped = declarations.replace('"', "&quot;")
    return raw_tag[: match.start()] + match.group(1) + f'="{escaped}"' + raw_tag[match.end() :]


def apply_fit_edits(html: str, edits: Sequence[Mapping[str, object]]) -> str:
    """Apply ordered ``{qid, declarations}`` edits to authored source bytes."""
    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    result = html
    for edit in edits:
        if not isinstance(edit, Mapping):
            raise FitEditError("fit edit must be a mapping")
        qid = edit.get("qid")
        declarations = edit.get("declarations")
        if not isinstance(qid, str) or not qid:
            raise FitEditError("fit edit is missing a qid")
        if not isinstance(declarations, str) or not declarations:
            raise FitEditError(f"fit edit for {qid} is missing declarations")
        offset, raw_tag = _locate_start_tag(result, qid)
        replacement = _append_declarations(raw_tag, declarations)
        result = result[:offset] + replacement + result[offset + len(raw_tag) :]
    return result


def fitted_source(html: str, report: Mapping[str, object] | None) -> str:
    edits = report.get("edits") if isinstance(report, Mapping) else None
    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes)):
        return html
    return apply_fit_edits(
        html, [edit for edit in edits if isinstance(edit, Mapping)]
    )


__all__ = ["FitEditError", "apply_fit_edits", "fitted_source"]
