"""Reconstruct the in-render fitted document on the API side.

The in-page fitter (``utils/slide_capture_fit_script.py``) edits the DOM with
``setAttribute('style', original + ';' + declarations)`` -- a plain string
append, never a CSSOM property assignment -- and reports every append it made
as ``{qid, declarations}``.  This module replays those appends over the source
string, so the document the API persists and the document Chromium measured are
the same bytes as far as the parser and the cascade are concerned.

It is deliberately a string transform over start tags, reusing the scanner of
``utils/authored_qid_namespace.py`` (``utils/authored_dom_tags``): authored HTML
is never reserialized.  It refuses a qid that is missing or duplicated, because
an unresolvable qid means the report describes a different document, and that is
exactly the divergence this module exists to prevent.
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
    """A fit edit cannot be replayed onto the source HTML."""


def _locate_start_tag(html: str, qid: str) -> tuple[int, str]:
    """Return the (offset, verbatim token) of the single start tag with ``qid``."""
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
        if attrs_dict(raw_attrs).get("data-qid", "").strip() == qid:
            found.append((index, raw_tag))
    if not found:
        raise FitEditError(f"fit edit targets an unknown qid: {qid}")
    if len(found) > 1:
        raise FitEditError(f"fit edit targets a duplicated qid: {qid}")
    return found[0]


def _escape_attribute_value(value: str) -> str:
    # The appended declarations become a double-quoted attribute value.  A quote
    # inside them would end the attribute; ``&quot;`` reparses to the same
    # character, which is what the browser's own serialization produces.
    return value.replace('"', "&quot;")


def _appended_style(original: str, declarations: str) -> str:
    """The exact string the in-page fitter passed to ``setAttribute``."""
    return (original + ";" if original else "") + declarations


def _already_applied(original: str, declarations: str) -> bool:
    return original == declarations or original.endswith(";" + declarations)


def _apply_one(html: str, qid: str, declarations: str) -> str:
    index, raw_tag = _locate_start_tag(html, qid)
    match = _STYLE_PATTERN.search(raw_tag)
    if match is None:
        end = _TAG_END_PATTERN.search(raw_tag)
        if end is None:
            raise FitEditError(f"fit edit cannot address the start tag of {qid}")
        insert_at = end.start()
        value = _escape_attribute_value(declarations)
        replacement = f'{raw_tag[:insert_at]} style="{value}"{raw_tag[insert_at:]}'
        return html[:index] + replacement + html[index + len(raw_tag) :]
    original = next(
        (group for group in match.groups()[1:] if group is not None),
        "",
    )
    if _already_applied(original, declarations):
        return html
    value = _escape_attribute_value(_appended_style(original, declarations))
    replacement = (
        raw_tag[: match.start()] + f' style="{value}"' + raw_tag[match.end() :]
    )
    return html[:index] + replacement + html[index + len(raw_tag) :]


def apply_fit_edits(html: str, edits: Sequence[Mapping[str, object]]) -> str:
    """Replay the fitter's style appends onto ``html``, in report order.

    Idempotent: an edit whose declarations already terminate the ``style``
    attribute is a no-op, so applying a report twice yields the same bytes.
    """
    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    result = html
    for edit in edits or ():
        if not isinstance(edit, Mapping):
            raise FitEditError("fit edit must be a mapping")
        qid = edit.get("qid")
        declarations = edit.get("declarations")
        if not isinstance(qid, str) or not qid.strip():
            raise FitEditError("fit edit is missing a qid")
        if not isinstance(declarations, str) or not declarations.strip():
            raise FitEditError(f"fit edit for {qid} is missing declarations")
        result = _apply_one(result, qid.strip(), declarations)
    return result


def fitted_source(html: str, report: Mapping) -> str:
    """Return the fitted document described by a fit ``report``.

    ``html`` is returned unchanged when the report carries no edits, so callers
    can hand any report through without branching on its shape.
    """
    edits = report.get("edits") if isinstance(report, Mapping) else None
    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes)) or not edits:
        return html
    return apply_fit_edits(html, edits)


__all__ = ["FitEditError", "apply_fit_edits", "fitted_source"]
