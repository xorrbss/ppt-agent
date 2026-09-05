"""Guarantee exactly one ``data-semantic-role="title"`` text owner per slide.

The v2 quality gate requires a slide to expose at least one *text-owning*
element whose ``data-semantic-role`` is ``title`` (``capacity.required_failed:
title`` / ``title:semantic_role_missing`` in
``services/authored_quality_receipt_v2.py``).  ``ensure_authored_semantic_roles``
stamps that role deterministically, but it keys on **direct** text ownership,
so a cover authored as::

    <h1 data-qid="cover-title">
      <span data-qid="cover-title-line1">...</span>
      <span data-qid="cover-title-line2">...</span>
    </h1>

leaves the ``h1`` owning no text (the qid-bearing spans do) and therefore no
element carries the title role at all -- one attribute ended a 24-slide deck.
The mirror defect is over-labelling: ``_is_primary_title_candidate`` treats
every ``h2`` as a title, so an agenda slide reaches the gate with seven title
owners.

This module repairs both, deterministically and without a provider call:

* **Promotion** -- when no text owner carries the title role, exactly one is
  promoted: the first ``h1`` that owns text; else the largest heading
  (``h1``-``h3``) that owns text; else the text owner with the largest
  effective ``font-size`` (its own inline value or the nearest inline
  ancestor's, in px); else the first text owner in document order.  A slide's
  last ``body`` owner is skipped while another candidate exists, because the
  gate requires a body owner just as hard as it requires a title one.
* **Demotion** (``demote=True``) -- when several text owners carry the title
  role, the largest one is kept and the rest become ``supporting``.  Demotion
  is skipped when it would leave the slide without a ``body`` owner (the gate
  requires one too) and never touches a ``data-header-part="title"`` element.
  Only the pipeline's pre-render canonicalization asks for it: every other
  caller compares its document against a sibling attempt or a reducer result
  and must change nothing but a genuinely missing title role.
* **Body promotion** -- run unconditionally after the title step, on every
  caller.  A slide the model files entirely as ``title`` + ``supporting`` (no
  ``body`` at all) reaches the gate with ``capacity.required_failed:body`` /
  ``body:semantic_role_missing`` just as surely as a missing title blocks it.
  When no text owner carries ``body``, every ``supporting`` owner whose text
  reads like content -- 40+ characters, or containing sentence punctuation --
  is promoted to ``body``; when none qualifies, the single longest
  ``supporting`` owner is promoted instead.  ``title``, ``decoration``,
  ``image``, and any ``data-header-part`` owner are never candidates.

Like every other authored normaliser this edits exact byte spans located by
``html.parser`` and never reserializes the document; a slide that already has
exactly one title owner is returned byte-identical.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

LOGGER = logging.getLogger(__name__)

TITLE_PROMOTED_RULE_ID = "semantic_role_promoted"
TITLE_DEMOTED_RULE_ID = "semantic_role_demoted"

_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_HEADING_RANK = {"h1": 1, "h2": 2, "h3": 3}
_FONT_SIZE_PX = re.compile(r"(?i)(?:^|[;{])\s*font-size\s*:\s*([0-9]*\.?[0-9]+)\s*px")


class _Owner:
    """One qid-bearing start tag and everything the repair ranks it by."""

    __slots__ = ("tag", "raw", "attrs", "start", "owns_text", "font_px", "text_parts")

    def __init__(self, tag: str, raw: str, attrs: dict[str, str], start: int, font_px: float | None) -> None:
        self.tag = tag
        self.raw = raw
        self.attrs = attrs
        self.start = start
        self.font_px = font_px
        self.owns_text = False
        self.text_parts: list[str] = []

    @property
    def role(self) -> str:
        return self.attrs.get("data-semantic-role", "").strip().lower()


class _OwnerCollector(HTMLParser):
    """Attribute text to its nearest ``data-qid`` ancestor, as the policy does.

    Mirrors ``services.authored_composition_policy._AuthoredStartTagCollector``
    ownership (nearest qid ancestor wins, script/style/template text ignored),
    and additionally carries the inherited inline ``font-size`` so a text owner
    nested inside a sized heading can still be ranked by what it renders at.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self._source = source
        self._offsets = [0]
        self._offsets.extend(match.end() for match in re.finditer(r"\n", source))
        self._stack: list[tuple[str, int | None, float | None]] = []
        self.owners: list[_Owner] = []
        self.malformed = False

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._offsets[line - 1] + column

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> tuple[int | None, float | None]:
        raw = self.get_starttag_text()
        if raw is None:
            self.malformed = True
            return None, None
        attributes = {(name or "").lower(): (value or "") for name, value in attrs}
        inherited = self._stack[-1][2] if self._stack else None
        match = _FONT_SIZE_PX.search(attributes.get("style", ""))
        font_px = float(match.group(1)) if match else inherited
        if not attributes.get("data-qid", "").strip():
            return None, font_px
        self.owners.append(_Owner(tag, raw, attributes, self._offset(), font_px))
        return len(self.owners) - 1, font_px

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        index, font_px = self._open(tag, attrs)
        if tag not in _VOID_TAGS:
            self._stack.append((tag, index, font_px))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag.lower(), attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for position in range(len(self._stack) - 1, -1, -1):
            if self._stack[position][0] == tag:
                del self._stack[position:]
                return

    def handle_data(self, data: str) -> None:
        if not data.strip() or any(frame[0] in {"script", "style", "template"} for frame in self._stack):
            return
        for _tag, index, _font in reversed(self._stack):
            if index is not None:
                self.owners[index].owns_text = True
                self.owners[index].text_parts.append(data)
                return


def _collect(html: str) -> list[_Owner]:
    parser = _OwnerCollector(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # a document the parser cannot resolve is left alone
        return []
    if parser.malformed:
        return []
    return [owner for owner in parser.owners if owner.owns_text]


def _rank(owner: _Owner) -> tuple[float, int, int]:
    """Largest effective font first, then the strongest heading, then order."""
    return (-(owner.font_px or 0.0), _HEADING_RANK.get(owner.tag, 9), owner.start)


#: Substrings that mark a run of text as a sentence rather than a short label.
#: ``.`` and ``。`` already cover the Korean sentence endings ``다.``/``요.``;
#: they are listed anyway so the rule reads the way the design names it.
_SENTENCE_MARKERS = (".", "。", "다.", "요.")
#: A comma only counts as sentence punctuation once the run is long enough
#: that it reads as a clause rather than a short "A, B" label pair.
_SENTENCE_COMMA_MIN_LENGTH = 25
_SENTENCE_MIN_LENGTH = 40


def _owned_text(owner: _Owner) -> str:
    """The owner's own text, whitespace-collapsed the way the gate reads it."""
    return re.sub(r"\s+", " ", "".join(owner.text_parts)).strip()


def _is_sentence_like_text(text: str) -> bool:
    """Content long or punctuated enough to be body copy, not a label."""
    if len(text) >= _SENTENCE_MIN_LENGTH:
        return True
    if any(marker in text for marker in _SENTENCE_MARKERS):
        return True
    return "," in text and len(text) >= _SENTENCE_COMMA_MIN_LENGTH


def _set_role(html: str, owner: _Owner, role: str) -> str:
    raw = owner.raw
    if html[owner.start : owner.start + len(raw)] != raw:
        return html
    if "data-semantic-role" in owner.attrs:
        pattern = re.compile(
            r"(\sdata-semantic-role)(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?(?=\s|/?>)",
            re.IGNORECASE,
        )
        replacement = pattern.sub(lambda match: f'{match.group(1)}="{role}"', raw, count=1)
    else:
        closing = re.search(r"\s*/?>\s*$", raw)
        if closing is None:
            return html
        replacement = f'{raw[:closing.start()]} data-semantic-role="{role}"{raw[closing.start():]}'
    return html[: owner.start] + replacement + html[owner.start + len(raw) :]


def _ensure_body_role(html: str) -> tuple[str, list[dict]]:
    """Promote a ``supporting`` text owner to ``body`` when the slide has none.

    Mirrors :func:`ensure_title_role`'s promotion for the gate's other
    required role: when every text owner is filed as ``title`` or
    ``supporting`` (see the module docstring's live-defect slide), the
    ``supporting`` owners that read like content are relabelled ``body``, or
    -- when none reads that way -- the single longest one is.  ``title``,
    ``decoration``, ``image``, and any ``data-header-part`` owner (an
    eyebrow, a page number, ...) are never touched: they are excluded by
    only ever drawing candidates from owners already marked ``supporting``.
    """
    owners = _collect(html)
    if not owners or any(owner.role == "body" for owner in owners):
        return html, []
    candidates = [
        owner
        for owner in owners
        if owner.role == "supporting" and not owner.attrs.get("data-header-part", "").strip()
    ]
    if not candidates:
        return html, []
    promoted = [owner for owner in candidates if _is_sentence_like_text(_owned_text(owner))]
    if not promoted:
        promoted = [max(candidates, key=lambda owner: (len(_owned_text(owner)), -owner.start))]
    for owner in sorted(promoted, key=lambda item: item.start, reverse=True):
        html = _set_role(html, owner, "body")
    LOGGER.info("promoted %d authored supporting owners to body", len(promoted))
    return html, [{"rule": TITLE_PROMOTED_RULE_ID, "detail": "body", "count": len(promoted)}]


def ensure_title_role(
    html: str, *, role: str = "", demote: bool = False
) -> tuple[str, list[dict]]:
    """Leave exactly one title text owner, and at least one body owner."""

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    owners = _collect(html)
    if not owners:
        return html, []
    titled = [owner for owner in owners if owner.role == "title"]
    findings: list[dict] = []
    if not titled:
        # ``body`` is a required gate check too: never buy a title owner by
        # spending the slide's last body owner when another candidate exists.
        body_owners = [owner for owner in owners if owner.role == "body"]
        pool = owners
        if len(body_owners) == 1:
            pool = [owner for owner in owners if owner is not body_owners[0]] or owners
        headings = [owner for owner in pool if owner.tag in _HEADING_RANK]
        first_h1 = next((owner for owner in pool if owner.tag == "h1"), None)
        chosen = first_h1 or min(headings or pool, key=_rank)
        html = _set_role(html, chosen, "title")
        findings.append({"rule": TITLE_PROMOTED_RULE_ID, "detail": "title", "count": 1})
        LOGGER.info(
            "promoted authored slide title role=%s qid=%s tag=%s",
            role or "unknown",
            chosen.attrs.get("data-qid", ""),
            chosen.tag,
        )
    elif len(titled) > 1 and demote and any(owner.role == "body" for owner in owners):
        # Demotion must not remove the slide's last body owner: the gate
        # requires a body text owner exactly as it requires a title one.
        keep = min(
            (owner for owner in titled if owner.attrs.get("data-header-part", "").strip().lower() == "title"),
            default=min(titled, key=_rank),
            key=_rank,
        )
        demoted = [
            owner
            for owner in titled
            if owner is not keep and owner.attrs.get("data-header-part", "").strip().lower() != "title"
        ]
        if demoted:
            for owner in sorted(demoted, key=lambda item: item.start, reverse=True):
                html = _set_role(html, owner, "supporting")
            findings.append({
                "rule": TITLE_DEMOTED_RULE_ID,
                "detail": "title->supporting",
                "count": len(demoted),
            })
            LOGGER.info(
                "demoted %d extra authored title roles role=%s keep=%s",
                len(demoted),
                role or "unknown",
                keep.attrs.get("data-qid", ""),
            )
    html, body_findings = _ensure_body_role(html)
    findings.extend(body_findings)
    return html, findings


def plan_slide_role(plan: object, index: int) -> str:
    """Best-effort role label for the log line; narrow test doubles omit it."""

    try:
        return str(plan.roles[index])  # type: ignore[attr-defined]
    except (AttributeError, TypeError, IndexError, KeyError):
        return ""


def canonicalize_authored_roles(
    html: str,
    *,
    role: str = "",
    demote: bool = False,
    receipt: list[dict] | None = None,
) -> str:
    """``ensure_authored_qids`` + role stamping + the single-title repair."""

    # Imported at call time: ``services`` already imports this module's callers.
    from services.authored_composition_policy import (
        ensure_authored_qids,
        ensure_authored_semantic_roles,
    )

    repaired, findings = ensure_title_role(
        ensure_authored_semantic_roles(ensure_authored_qids(html)),
        role=role,
        demote=demote,
    )
    if findings and receipt is not None:
        receipt.extend(findings)
    return repaired


__all__ = [
    "TITLE_DEMOTED_RULE_ID",
    "TITLE_PROMOTED_RULE_ID",
    "canonicalize_authored_roles",
    "ensure_title_role",
    "plan_slide_role",
]
