"""Deterministic concept matching for the bundled icon library.

The library indexes each icon by its exact bilingual aliases, but a figure
label is a verbatim phrase from the slide ("데이터 수집", "고객 확보 비용"),
not an alias. This module maps such a phrase to a library icon from a curated,
hand-reviewed table (``concept_aliases.json``) without any model call.

Precision rules, because a missing icon is cheap and a wrong one is not:

* Only whole words of the table match. A label is split on spaces, and a
  space-less Korean compound is split only when every piece of it is a known
  word (optionally followed by a particle or a noun suffix), so "보안관"
  never becomes "보안" and "대기업" never becomes "대기" + "업".
* Raw library aliases are not used inside a label: "의사 표명" (to state an
  intent) must not turn into the physician icon.
* ``standalone`` entries are too broad to apply inside a compound ("고객 대응"
  is customer service, not risk response); they match only a bare label.
* A label that says something fell ("성장 둔화") never receives a directional
  (growth) icon, which would depict the opposite.
* The head noun (the last matched word) wins over its modifiers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

_TABLE = Path(__file__).with_name("concept_aliases.json")

#: Particles and noun suffixes that may follow the last word of a compound.
_ENDINGS = frozenset(
    ["의", "을", "를", "은", "는", "이", "가", "에", "로", "과", "와", "도", "만", "에서", "으로",
     "화", "성", "적", "별", "형", "용", "율", "률"]
)
#: Library categories whose icons depict a rise, so they must not illustrate a fall.
_DIRECTIONAL_PREFIX = "growth-"


def _words(value: str) -> list[str]:
    return re.sub(r"[\W_]+", " ", value.casefold()).split()


@dataclass(frozen=True)
class ConceptTables:
    concepts: dict[str, str]
    phrases: dict[tuple[str, ...], str]
    standalone: dict[str, str]
    filler: frozenset[str]
    trend: frozenset[str]
    reversal: frozenset[str]
    vocabulary: frozenset[str]
    longest_word: int


@lru_cache(maxsize=1)
def load_tables() -> ConceptTables:
    if not _TABLE.is_file():
        return ConceptTables({}, {}, {}, frozenset(), frozenset(), frozenset(), frozenset(), 0)
    data = json.loads(_TABLE.read_text(encoding="utf-8"))
    concepts = {key.casefold(): icon for key, icon in data.get("concepts", {}).items()}
    standalone = {key.casefold(): icon for key, icon in data.get("standalone", {}).items()}
    phrases = {tuple(_words(key)): icon for key, icon in data.get("phrases", {}).items()}
    filler = frozenset(word.casefold() for word in data.get("filler", []))
    trend = frozenset(word.casefold() for word in data.get("trend", []))
    reversal = frozenset(word.casefold() for word in data.get("reversal", []))
    vocabulary = {*concepts, *standalone, *filler, *reversal}
    for phrase in phrases:
        vocabulary.update(phrase)
    return ConceptTables(
        concepts, phrases, standalone, filler, trend, reversal, frozenset(vocabulary),
        max((len(word) for word in vocabulary), default=0),
    )


def referenced_icon_ids() -> frozenset[str]:
    """Every icon id the table points at, for validation against the manifest."""
    tables = load_tables()
    return frozenset([*tables.concepts.values(), *tables.standalone.values(), *tables.phrases.values()])


def _segment(token: str, tables: ConceptTables) -> Optional[list[str]]:
    """Split a space-less token into known words, or None if any part is unknown."""
    vocabulary = tables.vocabulary
    if token in vocabulary:
        return [token]
    size = len(token)
    best: list[Optional[list[str]]] = [None] * (size + 1)
    best[0] = []
    for end in range(1, size + 1):
        for start in range(max(0, end - tables.longest_word), end):
            head = best[start]
            piece = token[start:end]
            if head is None or piece not in vocabulary:
                continue
            candidate = [*head, piece]
            if best[end] is None or len(candidate) < len(best[end]):  # type: ignore[arg-type]
                best[end] = candidate
    for start in range(1, size):
        head = best[start]
        if head and token[start:] in _ENDINGS:
            return head
    return best[size] or None


def _units(label: str, tables: ConceptTables) -> list[Optional[str]]:
    """Known words of the label in order; None marks a part that is not understood."""
    units: list[Optional[str]] = []
    for token in _words(label):
        if token.isascii() and token.isalpha():
            singular = token[:-1] if token.endswith("s") else token
            units.append(token if token in tables.vocabulary else singular if singular in tables.vocabulary else None)
            continue
        if any(char.isdigit() for char in token):
            units.append(None)
            continue
        pieces = _segment(token, tables)
        units.extend(pieces if pieces else [None])
    return units


def match_concept(label: str) -> Optional[str]:
    """The library icon id a business label stands for, or None when none fits."""
    tables = load_tables()
    units = _units(label, tables)
    blocked = any(unit in tables.reversal for unit in units if unit)
    core = [unit for unit in units if unit is None or unit not in tables.filler]
    if not core:
        return None
    if len(core) == 1 and core[0] is not None and core[0] in tables.standalone:
        icon = tables.standalone[core[0]]
        if not (blocked and icon.startswith(_DIRECTIONAL_PREFIX)):
            return icon
    # A direction word ("증가", "향상") only qualifies the noun before it, so it
    # names the figure only when no other word of the label does.
    for allow_trend in (False, True):
        for end in range(len(core), 0, -1):
            for start in range(end):
                window = core[start:end]
                if None in window:
                    continue
                key = tuple(window)  # type: ignore[arg-type]
                icon = tables.phrases.get(key)
                if icon is None and len(key) == 1 and (allow_trend or key[0] not in tables.trend):
                    icon = tables.concepts.get(key[0])
                if icon is None or (blocked and icon.startswith(_DIRECTIONAL_PREFIX)):
                    continue
                return icon
    return None
