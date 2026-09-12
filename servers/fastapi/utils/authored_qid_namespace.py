"""The authored ``data-qid`` namespace: token validity, stamping, validation.

Split out of ``utils/authored_dom_quality.py``, which inspects rendered DOM
quality. Deciding what a slide's element identifiers are is a separate
concern from judging how the slide rendered, and it is the concern the patch
engine depends on: a qid this module accepts must stay addressable by a
patch, otherwise a seemingly harmless preflight repair makes a later local
repair fail closed.

Two boundaries live here and they deliberately behave differently:

* :func:`ensure_authored_qids` is the legacy compatibility migration. It
  *repairs* a document -- inventing, replacing and de-duplicating qids --
  while preserving every other authored byte, and is idempotent.
* :func:`validate_declared_authored_qids` is the semantic dual-output
  boundary. It never repairs: a retry is safer than publishing a source
  whose semantic identity differs from what Authored declared.

Both walk start tags with the shared primitives in
``utils/authored_dom_tags.py`` and splice verbatim source tokens, so authored
HTML is never reserialized.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from utils.authored_dom_tags import (
    StartTagCollector,
    attrs_dict,
    is_edit_target,
    normalize_dom_text,
)

# This is deliberately shared with the patch engine.  A qid accepted by the
# migration must also be addressable by a patch, otherwise a seemingly harmless
# preflight repair can make a later local repair fail closed.
_QID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: Attributes that make an element's identity stable across regenerations, and
#: therefore seed its deterministic qid.
_IDENTITY_ATTRIBUTES = ("id", "class", "name", "role", "aria-label", "alt", "title")


def is_valid_authored_qid(value: object) -> bool:
    """Return whether a value is a patch-addressable authored qid token."""
    return isinstance(value, str) and _QID_PATTERN.fullmatch(value) is not None


def _qid_seed(tag: str, attrs: Mapping[str, str], occurrence: int) -> str:
    stable_attrs = "|".join(
        f"{name}={normalize_dom_text(attrs.get(name), limit=80)}"
        for name in _IDENTITY_ATTRIBUTES
        if attrs.get(name)
    )
    digest = hashlib.sha256(f"{tag}|{stable_attrs}|{occurrence}".encode()).hexdigest()[
        :12
    ]
    return f"q-{tag}-{digest}"


def _insert_attribute(raw_tag: str, name: str, value: str) -> str:
    # The input is an HTMLParser start-tag token.  Keep every user byte except the
    # one reversible attribute insertion, including quote choices and whitespace.
    match = re.search(r"\s*/?>\s*$", raw_tag)
    if match is None:
        return raw_tag
    insert_at = match.start()
    return f'{raw_tag[:insert_at]} {name}="{value}"{raw_tag[insert_at:]}'


def _replace_attribute(raw_tag: str, name: str, value: str) -> str:
    """Replace an existing valued or valueless attribute in one start token."""
    pattern = re.compile(
        rf"(\s{re.escape(name)})(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?(?=\s|/?>)",
        re.IGNORECASE,
    )
    return pattern.sub(lambda match: f'{match.group(1)}="{value}"', raw_tag, count=1)


def ensure_authored_qids(html: str) -> str:
    """Add deterministic ``data-qid`` attributes without rewriting authored HTML.

    Existing valid qids are retained verbatim.  Missing, invalid, and duplicate
    qids are deterministically replaced on the later element; normal ``id``
    attributes are never touched.  Calling this twice is idempotent.  We also
    normalize an invalid/duplicate qid on a non-editable element because the patch
    parser validates the document-wide qid namespace.
    """
    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    parser = StartTagCollector()
    parser.feed(html)
    parser.close()
    cursor = 0
    used: set[str] = set()
    occurrences: dict[tuple[str, str], int] = {}
    parts: list[str] = []
    for tag, raw_attrs, raw_tag in parser.tags:
        index = html.find(raw_tag, cursor)
        if index < 0:  # Defensive: malformed parser output must leave user HTML intact.
            continue
        parts.append(html[cursor:index])
        attrs = attrs_dict(raw_attrs)
        has_qid_attribute = "data-qid" in attrs
        raw_qid = attrs.get("data-qid", "")
        qid = raw_qid.strip()
        replacement = raw_tag
        needs_qid = is_edit_target(tag, attrs)
        if needs_qid or qid:
            signature = (
                tag,
                "|".join(f"{key}={attrs.get(key, '')}" for key in _IDENTITY_ATTRIBUTES),
            )
            occurrences[signature] = occurrences.get(signature, 0) + 1
            if (
                (needs_qid and not qid)
                or not is_valid_authored_qid(raw_qid)
                or qid in used
            ):
                generated = _qid_seed(tag, attrs, occurrences[signature])
                while generated in used:
                    occurrences[signature] += 1
                    generated = _qid_seed(tag, attrs, occurrences[signature])
                replacement = (
                    _replace_attribute(raw_tag, "data-qid", generated)
                    if has_qid_attribute
                    else _insert_attribute(raw_tag, "data-qid", generated)
                )
                qid = generated
            if qid:
                used.add(qid)
        parts.append(replacement)
        cursor = index + len(raw_tag)
    parts.append(html[cursor:])
    return "".join(parts)


def validate_declared_authored_qids(html: str) -> tuple[str, ...]:
    """Validate a model-declared QID namespace without repairing the source.

    New semantic dual-output candidates use ``data-qid`` as their element identity.
    Unlike the legacy compatibility migration above, this boundary must never invent,
    replace, or de-duplicate an identifier: a retry is safer than publishing a source
    whose semantic identity differs from what Authored declared.
    """

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    parser = StartTagCollector()
    parser.feed(html)
    parser.close()
    qids: list[str] = []
    used: set[str] = set()
    for tag, raw_attrs, _raw_tag in parser.tags:
        attrs = attrs_dict(raw_attrs)
        raw_qid = attrs.get("data-qid")
        needs_qid = is_edit_target(tag, attrs)
        if needs_qid and raw_qid is None:
            raise ValueError(f"authored_semantic_qid_missing:{tag}")
        if raw_qid is None:
            continue
        if not is_valid_authored_qid(raw_qid):
            raise ValueError("authored_semantic_qid_invalid")
        if raw_qid in used:
            raise ValueError("authored_semantic_qid_duplicate")
        used.add(raw_qid)
        qids.append(raw_qid)
    if not qids:
        raise ValueError("authored_semantic_qid_missing")
    return tuple(qids)


def declared_authored_qids(html: str) -> tuple[str, ...]:
    """Return the source-declared QIDs without completing the namespace."""

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    parser = StartTagCollector()
    parser.feed(html)
    parser.close()
    return tuple(
        qid
        for _tag, raw_attrs, _raw_tag in parser.tags
        if (qid := attrs_dict(raw_attrs).get("data-qid")) is not None
    )


__all__ = [
    "declared_authored_qids",
    "ensure_authored_qids",
    "is_valid_authored_qid",
    "validate_declared_authored_qids",
]
