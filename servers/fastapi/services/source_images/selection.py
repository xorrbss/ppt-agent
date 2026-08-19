"""Rank the source images worth offering the slide author for one slide.

A 60-slide source deck yields hundreds of pictures. Handing all of them to the
authoring call makes the choice arbitrary and blows up the prompt, so this
module keeps only the best few. Every signal here is deterministic Python: the
same slide must always get the same offer, the ranking has to run per slide
without a network hop, and a reviewer has to be able to read the weights in a
diff. No LLM call belongs in this file.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from services.source_images.models import (
    SUPPORTED_SOURCE_IMAGE_MIME_TYPES,
    SourceImageCandidate,
)

#: Korean is compared on character n-grams rather than whitespace-delimited
#: words: eojeol carry postpositions ("매출이", "매출은", "매출의") and the spacing
#: of a source deck rarely matches a freshly written outline, so word overlap
#: between two headings about the same topic is almost always zero. Bigrams of
#: syllables survive both.
_NGRAM_SIZE = 2

#: Latin text is compared on whole words instead, because its word boundaries
#: are reliable and space-blind bigrams over English invent matches: "warehouse
#: safety rules" and "quarterly revenue growth" share "re", "ar", "es" and land
#: just under the relevance floor purely by accident.
_MIN_WORD_LEN = 2

#: Each longer latin word also contributes its prefix, so that English
#: inflections still meet: "revenue"/"revenues" and "quarter"/"quarterly" are
#: the same topic and would otherwise score a flat zero against each other.
_WORD_PREFIX_LEN = 5

#: Function words would otherwise let two unrelated headings match on "of" or
#: "the". Korean needs no such list: its particles are diluted by the bigrams
#: around them.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
        "is", "its", "of", "on", "or", "our", "that", "the", "this", "to",
        "was", "were", "with",
    }
)

#: The source heading is the strongest evidence we have that a picture belongs
#: to this slide's topic, so heading-to-heading similarity dominates. Body text
#: is long and repeats deck-wide vocabulary, which makes a hit there much weaker
#: evidence; it only breaks the frequent case of a slide whose heading is terse
#: ("현황") while the body names the subject.
_HEADING_WEIGHT = 0.65
_CONTENT_WEIGHT = 0.35

#: Below this the match is a coincidental syllable or two. On real Korean and
#: English headings the two populations separate cleanly: a candidate sharing
#: even one topic word scores upwards of ~0.21, an off-topic one scores ~0.
#: The floor sits in that gap, because offering an unrelated picture is worse
#: than offering none — the author will place whatever it is handed.
_RELEVANCE_FLOOR = 0.18

#: A picture the user uploaded directly has no source heading, so it cannot be
#: scored on text at all. It is still presumed relevant — the user handed it to
#: this deck on purpose — so it sits just above the floor: eligible when nothing
#: else qualifies, but always beaten by a genuine textual match.
_UNTITLED_BASELINE = _RELEVANCE_FLOOR + 0.02

#: Tie-breakers only. Their combined ceiling (0.10) is small enough that they can
#: reorder near-equal candidates but can never lift a weak match over a strong
#: one, and they are deliberately excluded from the floor test so that proximity
#: or a pleasing aspect ratio can never drag an off-topic picture in.
_PROXIMITY_WEIGHT = 0.06
_ASPECT_WEIGHT = 0.04

#: Beyond this many slides away the source position tells us nothing; decks are
#: usually chaptered in runs shorter than this.
_PROXIMITY_SPAN = 10

#: Scores are rounded before ordering so that two candidates which are equal in
#: every scored respect really do tie, instead of being separated by float noise.
_SCORE_PRECISION = 6

_TAG_RE = re.compile(r"<[^>]+>")

#: Hangul syllables and jamo, kana, and CJK ideographs — the scripts written
#: without dependable word spacing, and therefore the ones compared on n-grams.
_IDEOGRAPHIC_RE = re.compile(
    r"[가-힣ᄀ-ᇿ㄰-㆏"
    r"぀-ヿ㐀-䶿一-鿿]"
)
_WORD_RE = re.compile(r"[^\W_]+")


def select_source_images(
    candidates: Sequence[SourceImageCandidate],
    *,
    slide_heading: str,
    slide_content: str,
    slide_index: int,
    limit: int = 2,
) -> list[SourceImageCandidate]:
    """Return at most ``limit`` candidates for this slide, best first.

    Returns an empty list when nothing clears the relevance floor. The caller is
    expected to fall back to a generated illustration in that case rather than
    place a picture about something else.
    """
    if limit <= 0 or not candidates:
        return []

    heading_grams = _grams(slide_heading)
    content_grams = _grams(slide_content)

    scored: list[tuple[float, str, SourceImageCandidate]] = []
    for candidate in candidates:
        # The authoring call can only attach these formats, so an unsupported
        # one is not a ranking question — it simply cannot be offered.
        if candidate.mime_type not in SUPPORTED_SOURCE_IMAGE_MIME_TYPES:
            continue

        relevance = _relevance(candidate, heading_grams, content_grams)
        if relevance < _RELEVANCE_FLOOR:
            continue

        score = (
            relevance
            + _PROXIMITY_WEIGHT * _proximity_score(candidate, slide_index)
            + _ASPECT_WEIGHT * _aspect_score(candidate)
        )
        scored.append((round(score, _SCORE_PRECISION), candidate.asset_id, candidate))

    # Ties break on asset_id so the offer does not depend on whatever order the
    # extractor happened to walk the source deck in.
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [candidate for _, _, candidate in scored[:limit]]


def _relevance(
    candidate: SourceImageCandidate,
    heading_grams: frozenset[str],
    content_grams: frozenset[str],
) -> float:
    """Text-only relevance of one candidate, in ``0.0``-``1.0``."""
    source_grams = _grams(candidate.source_heading or "")
    if not source_grams:
        return _UNTITLED_BASELINE

    # Dice against the heading: both sides are short, so a symmetric measure is
    # right and a long source heading should be penalised for the parts it does
    # not share. Containment against the body: the body is far longer, so we ask
    # how much of the source heading appears in it, not the reverse.
    return _HEADING_WEIGHT * _dice(source_grams, heading_grams) + (
        _CONTENT_WEIGHT * _containment(source_grams, content_grams)
    )


def _proximity_score(candidate: SourceImageCandidate, slide_index: int) -> float:
    """Weak positional prior: a picture from a nearby source slide is a better
    guess, because both decks tend to keep one subject inside one chapter."""
    if candidate.source_slide_index is None:
        # A directly uploaded picture has no position; stay neutral rather than
        # punish it for belonging to no deck.
        return 0.5
    distance = abs(candidate.source_slide_index - slide_index)
    return max(0.0, 1.0 - distance / _PROXIMITY_SPAN)


def _aspect_score(candidate: SourceImageCandidate) -> float:
    """Weak shape prior, never a filter: content slides have wide picture wells,
    so a landscape crop drops in without cropping the subject."""
    ratio = candidate.aspect_ratio
    if ratio <= 0:
        return 0.5
    if ratio >= 1.2:
        return 1.0
    if ratio >= 0.8:
        return 0.6
    return 0.3


def _grams(text: str) -> frozenset[str]:
    """Fold text into the comparable units: n-grams for Korean, words for Latin.

    Markup is dropped first because slide content may arrive as HTML and tag
    names would otherwise contribute latin tokens of their own.
    """
    lowered = _TAG_RE.sub(" ", text).lower()

    # Every ideographic character in the text is joined into one run before
    # taking n-grams, so that inconsistent Korean spacing between the source
    # deck and this slide cannot change the grams.
    ideographic = "".join(_IDEOGRAPHIC_RE.findall(lowered))
    grams = set(_character_grams(ideographic))

    for token in _WORD_RE.findall(_IDEOGRAPHIC_RE.sub(" ", lowered)):
        if len(token) < _MIN_WORD_LEN or token in _STOPWORDS:
            continue
        grams.add(token)
        if len(token) > _WORD_PREFIX_LEN:
            grams.add(token[:_WORD_PREFIX_LEN])
    return frozenset(grams)


def _character_grams(run: str) -> frozenset[str]:
    if not run:
        return frozenset()
    if len(run) < _NGRAM_SIZE:
        # A one-character heading ("도") still deserves to match itself.
        return frozenset({run})
    return frozenset(
        run[index : index + _NGRAM_SIZE]
        for index in range(len(run) - _NGRAM_SIZE + 1)
    )


def _dice(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def _containment(needle: frozenset[str], haystack: frozenset[str]) -> float:
    if not needle or not haystack:
        return 0.0
    return len(needle & haystack) / len(needle)
