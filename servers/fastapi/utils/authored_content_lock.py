"""Content lock: measure the text an authored slide says that its brief did not.

The author renders the outline brief; it does not rewrite it. Bench run 48
measured the same fixture on two models: one reproduced the brief at 1.0x,
the other at 1.2x-1.4x, and the whole excess was ADDED text -- kicker and
summary sentences, extra labels -- that the brief never said. Short furniture
(a chapter word, ``01``, a three-word caption, the page number) is fine; added
sentences are not.

This module is the measurement and its repair vocabulary. It is a
repair-round trigger only: the pipeline's first-pass gate reports the
violation as one more measured defect, the existing bounded ladder repairs it
once, and the ladder's own policy (flagged publish, terminal fallback) decides
what happens after that. Nothing here blocks publication by itself.

Matching is deliberately forgiving about form and strict about substance:
NFKC, whitespace and punctuation dropped, bullet markers ignored, so a bullet
re-wrapped across lines or re-punctuated still counts as rendered. A fragment
is covered when it is a substring of the normalized brief, or when every
clause of it (split on ``·`` ``/`` ``,`` ``→`` ``|`` and sentence breaks) is.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from utils.get_env_programme import is_authored_content_lock_enabled

#: The violation string prefix the repair ladder routes on.
VIOLATION_PREFIX = "content_added:"
#: Added characters every slide may carry without a repair round.
MIN_ALLOWANCE_CHARS = 120
#: Added characters allowed as a share of the brief, when that is larger.
ALLOWANCE_SHARE = 0.25
#: Fragments at or under this many normalized characters are furniture.
SHORT_FRAGMENT_CHARS = 12
#: Fragments quoted in the violation string / instruction, longest first.
QUOTED_FRAGMENTS = 3
_VIOLATION_FRAGMENT_CHARS = 60
_INSTRUCTION_CHARS = 200

_CLAUSE_SPLIT = re.compile(r"[·/,→|;\n。]|\.(?=\s|$)")
_NON_WORD = re.compile(r"[^\w]|_")
_STEP_NUMBER = re.compile(r"^(step|no)?[0-9ivxlcdm]+(단계|장|절)?$")
_HEADING = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class AddedFragment:
    """One rendered text run the brief does not contain."""

    qid: str
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True, slots=True)
class ContentLockReport:
    brief_chars: int
    added_chars: int
    fragments: tuple[AddedFragment, ...] = ()

    @property
    def ratio(self) -> float:
        if self.brief_chars <= 0:
            return float(self.added_chars)
        return self.added_chars / self.brief_chars

    @property
    def allowance(self) -> int:
        # ``added`` is an integer, so ``added > x`` and ``added > floor(x)``
        # agree; the floor is what the violation string can print.
        return int(max(MIN_ALLOWANCE_CHARS, ALLOWANCE_SHARE * self.brief_chars))

    @property
    def violated(self) -> bool:
        return self.added_chars > max(MIN_ALLOWANCE_CHARS, ALLOWANCE_SHARE * self.brief_chars)


def collapse_text(value: Any) -> str:
    """NFKC and whitespace-collapsed display text; the unit chars are counted in."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def match_key(value: Any) -> str:
    """The comparison form: letters and digits only, case-folded.

    Dropping whitespace and punctuation makes a bullet marker, a line wrap, a
    trailing period or a ``·`` versus ``,`` difference invisible, which is
    exactly the freedom the author is allowed.
    """

    return _NON_WORD.sub("", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def _is_furniture_number(key: str) -> bool:
    return bool(key) and _STEP_NUMBER.fullmatch(key) is not None


def _covered(text: str, haystack: str) -> bool:
    key = match_key(text)
    if not key or key in haystack:
        return True
    clauses = [match_key(part) for part in _CLAUSE_SPLIT.split(text)]
    clauses = [clause for clause in clauses if clause]
    if len(clauses) < 2:
        return False
    # A clause of one or two characters is a connective, not a claim.
    return all(len(clause) <= 2 or clause in haystack for clause in clauses)


def brief_heading(brief: str) -> str:
    """The slide title the brief states as its ``## ...`` heading, or ``""``."""

    match = _HEADING.search(str(brief or ""))
    return collapse_text(match.group(1)) if match else ""


def leaf_texts(elements: Iterable[Any]) -> list[tuple[str, str]]:
    """``(qid, text)`` per rendered text owner, in element order.

    ``text_fragments`` carry the text when the probe reports it; the live
    probe reports fragment rectangles only, so the direct ``owned_text`` (the
    text nodes owned by this qid and by no nested qid) is the leaf text, and
    ``rendered_text`` stands in for telemetry that predates ``owned_text``.
    """

    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in elements:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "")
        texts: list[str] = []
        fragments = item.get("text_fragments")
        if isinstance(fragments, Sequence) and not isinstance(fragments, (str, bytes)):
            texts = [
                str(fragment["text"])
                for fragment in fragments
                if isinstance(fragment, Mapping) and isinstance(fragment.get("text"), str)
            ]
        if not texts:
            owned = item.get("owned_text")
            if isinstance(owned, str):
                texts = [owned]
            elif item.get("has_owned_text"):
                texts = [str(item.get("rendered_text") or "")]
        for raw in texts:
            text = collapse_text(raw)
            if text and (qid, text) not in seen:
                seen.add((qid, text))
                found.append((qid, text))
    return found


def find_added_text(
    brief: str,
    elements: Sequence[Mapping[str, Any]] | Iterable[Any],
    *,
    furniture: Iterable[str] = (),
) -> ContentLockReport:
    """Report the rendered text runs that ``brief`` does not say.

    ``furniture`` is deck text that is legitimately on every page without
    being in any brief: the chapter breadcrumb, the deck topic, a running
    header. The brief's own heading is furniture too.
    """

    collapsed_brief = collapse_text(brief)
    haystack = match_key(collapsed_brief)
    exempt = [match_key(item) for item in (*furniture, brief_heading(brief))]
    exempt = [key for key in exempt if key]
    added: list[AddedFragment] = []
    for qid, text in leaf_texts(elements):
        key = match_key(text)
        if len(key) <= SHORT_FRAGMENT_CHARS or _is_furniture_number(key):
            continue
        if any(key in item for item in exempt):
            continue
        if _covered(text, haystack):
            continue
        added.append(AddedFragment(qid=qid, text=text))
    return ContentLockReport(
        brief_chars=len(collapsed_brief),
        added_chars=sum(fragment.chars for fragment in added),
        fragments=tuple(added),
    )


def _longest_fragments(report: ContentLockReport) -> list[str]:
    ordered = sorted(report.fragments, key=lambda fragment: (-fragment.chars, fragment.text))
    unique: list[str] = []
    for fragment in ordered:
        if fragment.text not in unique:
            unique.append(fragment.text)
    return unique[:QUOTED_FRAGMENTS]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def content_lock_violation(report: ContentLockReport) -> str:
    """``content_added:<added>><allowance>|<fragment>|...`` for a violated report."""

    quoted = [
        _clip(text, _VIOLATION_FRAGMENT_CHARS).replace("|", "/")
        for text in _longest_fragments(report)
    ]
    return "".join(
        [f"{VIOLATION_PREFIX}{report.added_chars}>{report.allowance}", *(f"|{text}" for text in quoted)]
    )


def parse_content_lock_violation(violation: str) -> tuple[int, int, tuple[str, ...]] | None:
    """``(added, allowance, fragments)`` for a violation string, else ``None``."""

    text = str(violation or "").strip()
    if not text.startswith(VIOLATION_PREFIX):
        return None
    head, *fragments = text[len(VIOLATION_PREFIX):].split("|")
    match = re.fullmatch(r"(\d+)>(\d+)", head)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), tuple(f for f in fragments if f)


def is_content_lock_violation(violation: str) -> bool:
    return parse_content_lock_violation(violation) is not None


def content_lock_repair_instruction(violation: str, role: str = "SLIDE") -> str:
    """A <=200-char repair instruction in ``capacity_repair_instructions`` style."""

    parsed = parse_content_lock_violation(violation)
    slide_role = (role or "SLIDE").strip().upper() or "SLIDE"
    head = f"{slide_role} has text CONTENT never says"
    tail = (
        ". Remove it or replace it with CONTENT's own words; keep short labels; "
        "do not add explanation, summary or kicker sentences; do not shrink type."
    )
    fragments = parsed[2] if parsed else ()
    budget = _INSTRUCTION_CHARS - len(head) - len(tail) - len(': ""')
    quoted: list[str] = []
    for fragment in fragments:
        piece = fragment if not quoted else f'" / "{fragment}'
        if len(piece) > budget:
            if not quoted and budget >= 8:
                quoted.append(_clip(fragment, budget))
            break
        quoted.append(piece)
        budget -= len(piece)
    quote = f': "{"".join(quoted)}"' if quoted else ""
    return (head + quote + tail)[:_INSTRUCTION_CHARS]


def _elements(browser_metrics: Any) -> list[Any]:
    if not isinstance(browser_metrics, Mapping):
        return []
    for key in ("elements", "nodes"):
        value = browser_metrics.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return list(value)
    return []


def content_lock_gate_issue(
    brief: str,
    browser_metrics: Any,
    *,
    role: str = "SLIDE",
    furniture: Iterable[str] = (),
) -> dict[str, Any] | None:
    """The first-pass gate issue for one rendered slide, or ``None``.

    Shaped like the capacity issues ``_evaluate_slide_quality`` already emits
    (``category``/``severity``/``message``), so the violation travels the
    same road: ``message`` is what the repair ladder fingerprints and turns
    into a ``capacity_repair_instructions`` critique, ``detail`` is what the
    remake brief quotes, and the two measurement keys are the receipt.
    """

    if not is_authored_content_lock_enabled():
        return None
    elements = _elements(browser_metrics)
    if not elements:
        return None
    report = find_added_text(brief, elements, furniture=furniture)
    if not report.violated:
        return None
    violation = content_lock_violation(report)
    return {
        "category": "capacity",
        "severity": "error",
        "message": violation,
        "detail": content_lock_repair_instruction(violation, role),
        "qids": sorted({fragment.qid for fragment in report.fragments if fragment.qid}),
        "content_added_chars": report.added_chars,
        "content_added_ratio": round(report.ratio, 3),
    }


__all__ = [
    "ALLOWANCE_SHARE",
    "MIN_ALLOWANCE_CHARS",
    "SHORT_FRAGMENT_CHARS",
    "VIOLATION_PREFIX",
    "AddedFragment",
    "ContentLockReport",
    "brief_heading",
    "collapse_text",
    "content_lock_gate_issue",
    "content_lock_repair_instruction",
    "content_lock_violation",
    "find_added_text",
    "is_content_lock_violation",
    "leaf_texts",
    "match_key",
    "parse_content_lock_violation",
]
