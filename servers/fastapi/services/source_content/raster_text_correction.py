"""Grounding rule for model-proposed text corrections of OCR lines.

A proposed text is accepted only when it stays close to something the OCR
actually read, so a model cannot introduce content:

* its number tokens (``\\d[\\d,.]*%?``) equal, verbatim and in order, those of
  one OCR candidate reading of the same line; and
* it contains a letter or digit (pure symbols are icon guesses, not text);
* ignoring whitespace, its Levenshtein distance to that same candidate is at
  most ``max(1, ceil(0.2 * len(candidate)))`` and below the candidate's length
  (some read character survives), or every difference is a
  script swap of one Hangul syllable for at most two Latin letters or the
  reverse (a glyph read in the wrong script, e.g. "시" / "AI").

Otherwise the line keeps its source pixels.
"""

from __future__ import annotations

import difflib
import math
import re
from collections.abc import Sequence

_NUMBER = re.compile(r"\d[\d,.]*%?")
_ALNUM = re.compile(r"[0-9A-Za-z가-힣]")
_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]{1,2}")


def _compact(value: str) -> str:
    return "".join(value.split())


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def script_swaps_only(a: str, b: str) -> bool:
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        left, right = a[i1:i2], b[j1:j2]
        hangul, latin = (left, right) if _HANGUL.fullmatch(left or "-") else (right, left)
        if tag != "replace" or not _HANGUL.fullmatch(hangul) or not _LATIN.fullmatch(latin):
            return False
    return True


def grounded_in(candidate: str, text: str) -> bool:
    a, b = _compact(candidate), _compact(text)
    if not _ALNUM.search(b) or _NUMBER.findall(a) != _NUMBER.findall(b):
        return False
    if script_swaps_only(a, b):
        return True
    distance = levenshtein(a, b)
    # Something the OCR read must survive: replacing every character of a short
    # reading ("5" -> "리" over an icon) is a guess, not a correction.
    return distance <= max(1, math.ceil(0.2 * len(a))) and distance < len(a)


def accept_correction(candidates: Sequence[str], text: str | None) -> str | None:
    """Return the corrected text when grounded in some candidate, else None."""

    value = " ".join((text or "").split())
    if not value:
        return None
    return value if any(grounded_in(candidate, value) for candidate in candidates if candidate.strip()) else None


_VARIANTS = str.maketrans(
    {"ㆍ": "·", "・": "·", "•": "·", "∙": "·", "“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "〜": "~"}
)


def normalized(value: str) -> str:
    """Whitespace and separator bars removed, punctuation variants unified (a bar
    between title parts is often a drawn rule that one reader skips)."""

    return _compact(value).translate(_VARIANTS).replace("|", "")


def _edit_cost(a: str, b: str) -> int:
    """Edits between two readings, where a script swap (시/AI) costs nothing."""

    cost = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and script_swaps_only(a[i1:i2], b[j1:j2]):
            continue
        cost += levenshtein(a[i1:i2], b[j1:j2])
    return cost


def transcription_agrees(candidates: Sequence[str], transcript: str | None) -> bool:
    """Two independent readers agree: the model transcript equals an OCR read after
    normalization, or differs by at most ``max(1, 10% of its length)`` edits (some
    read character surviving) or only by script swaps; numbers must be identical."""

    text = normalized(transcript or "")
    if not _ALNUM.search(text):
        return False
    for candidate in candidates:
        read = normalized(candidate or "")
        if not read or _NUMBER.findall(read) != _NUMBER.findall(text):
            continue
        if read == text:
            return True
        cost = min(levenshtein(read, text), _edit_cost(read, text))
        if cost <= max(1, int(0.1 * len(read))) and cost < len(read):
            return True
    return False


_LONG_LATIN = re.compile(r"[A-Za-z]{13,}")


def _spaced(value: str) -> tuple[str, set[int]]:
    """Compact characters and the compact indices preceded by whitespace."""

    chars, before, pending = [], set(), False
    for ch in value:
        if ch.isspace():
            pending = bool(chars)
            continue
        if pending:
            before.add(len(chars))
        chars.append(ch)
        pending = False
    return "".join(chars), before


def respace(text: str, reading: str) -> str | None:
    """The model's characters with the OCR reading's word boundaries where both
    readers agree on the characters (the model's own spacing elsewhere, and also
    between agreed Hangul letters where the OCR lost a space). None when a Latin run
    longer than 12 letters has no space the OCR reading does not share."""

    model, model_spaces = _spaced(text)
    ocr, ocr_spaces = _spaced(reading)
    out = []
    for tag, i1, _i2, j1, j2 in difflib.SequenceMatcher(None, ocr, model, autojunk=False).get_opcodes():
        for offset, j in enumerate(range(j1, j2)):
            if tag == "equal":
                space = (i1 + offset) in ocr_spaces
                # Between two Hangul letters both readers agree on, a space the OCR
                # dropped ("제안은기존의") is kept from the model.
                if not space and j in model_spaces and j and _HANGUL.match(model[j]) and (
                    _HANGUL.match(model[j - 1]) or model[j - 1] in ",.)"
                ):
                    space = offset > 0 or (i1 > 0 and j1 > 0)
            else:
                space = j in model_spaces
            if space and out:
                out.append(" ")
            out.append(model[j])
    result = "".join(out)
    for run in _LONG_LATIN.findall(result):
        if run not in reading:
            return None
    return result


_ADDED_MARKS = ":·;"


def adds_punctuation(text: str, reading: str) -> bool:
    """The model text has a colon or middle dot the OCR reading does not ("구성해드리:")."""

    return any(text.count(mark) > reading.count(mark) for mark in _ADDED_MARKS)
