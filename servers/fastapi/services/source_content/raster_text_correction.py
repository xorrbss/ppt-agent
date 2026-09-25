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
    """Every difference between the OCR reading ``a`` and the model text ``b`` is one
    glyph read in the wrong script. Where the OCR saw Hangul, the model's Latin must be
    capitals: block-shaped capitals ("AI") are what Tesseract reads as a syllable ("시");
    a lowercase letter for a syllable ("읍" / "a") is an icon both readers guessed."""

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        left, right = a[i1:i2], b[j1:j2]
        hangul, latin = (left, right) if _HANGUL.fullmatch(left or "-") else (right, left)
        if tag != "replace" or not _HANGUL.fullmatch(hangul) or not _LATIN.fullmatch(latin):
            return False
        if hangul is left and not latin.isupper():
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


def collapses_unread_glyphs(read: str, text: str) -> bool:
    """The model text fills a run of OCR glyph slots that holds an unreadable symbol
    with FEWER characters ("공 _—" -> "공급" for "공급자"): the OCR saw more glyphs
    there than the model wrote, so the model dropped content, not a misread."""

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, read, text, autojunk=False).get_opcodes():
        if tag != "replace" or j2 - j1 >= i2 - i1:
            continue
        if any(not ch.isalnum() for ch in read[i1:i2]) and any(ch.isalnum() for ch in text[j1:j2]):
            return True
    return False


def transcription_agrees(candidates: Sequence[str], transcript: str | None) -> bool:
    """Two independent readers agree: the model transcript equals an OCR read after
    normalization, or differs by at most ``max(1, 10% of its length)`` edits (some
    read character surviving) or only by script swaps; numbers must be identical,
    and the model may not collapse unread OCR glyph slots into fewer letters."""

    text = normalized(transcript or "")
    if not _ALNUM.search(text):
        return False
    for candidate in candidates:
        read = normalized(candidate or "")
        if not read or _NUMBER.findall(read) != _NUMBER.findall(text):
            continue
        if read != text and collapses_unread_glyphs(read, text):
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
                # dropped ("제안은기존의") is kept from the model; so is one after a
                # separator mark the OCR dropped ("강화 · 고부가가치").
                if not space and j in model_spaces and j and _HANGUL.match(model[j]) and (
                    _HANGUL.match(model[j - 1]) or model[j - 1] in ",.)·ㆍ・•∙:;"
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


_EDGE_MARKS = "·:;"


def edge_glyphs_grounded(text: str, candidates: Sequence[str]) -> bool:
    """A lone one-character token at either end of the published line must be read
    at that end by some OCR reading too: the same glyph, the start (or end) of a
    token that joined it to its neighbour ("약60%"), or a script swap of it ("of" for
    "약"). A single glyph the readers disagree on ("S" / "$" before "NPS") is an icon,
    not text. Separator marks are judged by their pixels instead (raster_editable_marks)."""

    tokens = text.split()
    if len(tokens) < 2:
        return True
    readings = [candidate.split() for candidate in candidates if candidate.split()]
    for position in (0, -1):
        token = tokens[position]
        if len(token) != 1 or normalized(token) in _EDGE_MARKS:
            continue
        ends = [reading[position] for reading in readings]
        joined = [end.startswith(token) if position == 0 else end.endswith(token) for end in ends]
        if not any(joined) and not any(script_swaps_only(end, token) for end in ends):
            return False
    return True
