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
import math
from collections import Counter
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

#: Being titled with this slide's subject is the clearest evidence a picture
#: belongs to it, so heading-to-heading similarity carries the largest single
#: share. The second term covers the frequent case of a new slide whose heading
#: is terse ("현황") while its body names the subject, and is weighted lower
#: because a body mentions many things it is not mainly about.
_HEADING_WEIGHT = 0.4
_CONTENT_WEIGHT = 0.2

#: What the source slide actually says is the signal that survives a deck whose
#: headings are section markers, which is the common shape of a proposal deck.
#: It carries the same weight as the heading match. Measured across two real
#: proposal decks, one names its pictures ("3. 업무시스템 구축방안") and the other
#: does not ("02 · SOLUTION"); weighting the heading higher found nothing at all
#: in the second. Two decks are a thin calibration set, so treat these weights
#: as a starting point to re-measure, not as a tuned constant.
_SOURCE_TEXT_WEIGHT = 0.4

#: Below this the match is a coincidental syllable or two, and offering an
#: unrelated picture is worse than offering none.
#:
#: Calibrated against two real proposal decks rather than against headings
#: alone, because the body-text signal changed the scale. On a DevAX deck whose
#: pictures all sit under one "02 · SOLUTION" marker, the slide about an
#: orchestration feature scored 0.104 against the new slide that argues for
#: orchestration, while unrelated pictures on the same deck scored 0.04 or less;
#: on an OSB proposal the topical matches scored above 0.18. The floor sits
#: under the weaker population's real matches and above both decks' noise.
#:
#: A candidate that clears it is still only *offered*: the author sees the
#: picture itself and declines when it does not fit, which it did on every slide
#: of a deck whose pictures were screenshots of another project's tools.
_RELEVANCE_FLOOR = 0.09

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

_GROUP_VERSION = "source-content-group/v1"
# Conservative deterministic overlap/ambiguity heuristics, not a measured
# semantic accuracy guarantee or a limit on group or image counts.
_GROUP_MATCH_FLOOR = 0.2
_GROUP_AMBIGUITY_MARGIN = 0.05


def assign_source_image_groups(
    contents: Sequence[str],
    candidates: Sequence[SourceImageCandidate],
    *,
    assignment_report: list[dict] | None = None,
) -> list[list[SourceImageCandidate]]:
    """Connect local explanation groups to targets without a picture count cap.

    Source page titles and transcripts cannot establish an image connection.
    They repeat across unrelated pictures, so this path uses only extracted
    local explanations, captions and existing image analysis. A textual match
    assigns a group to one clear target; explicit references can intentionally
    assign the same group to several targets. Unresolved groups remain visible
    in the optional receipt rather than becoming a deck-wide offer.
    """
    result: list[list[SourceImageCandidate]] = [[] for _ in contents]
    groups: dict[str, list[SourceImageCandidate]] = {}
    metadata: dict[str, dict] = {}
    for candidate in candidates:
        locator = candidate.source_locator or {}
        supplied = locator.get("contentGroup")
        group = supplied if isinstance(supplied, dict) else {}
        valid = group.get("version") == _GROUP_VERSION and bool(group.get("groupId"))
        group_id = str(group["groupId"]) if valid else f"asset:{candidate.asset_id}"
        groups.setdefault(group_id, []).append(candidate)
        metadata.setdefault(group_id, group if valid else {})
    for members in groups.values():
        # Stable ties preserve original extraction order for several images in
        # one Word paragraph; opaque asset UUIDs must not scramble a procedure.
        members.sort(key=_group_member_order)

    group_fragments = {
        group_id: tuple(_grams(text) for text in _group_local_texts(members))
        for group_id, members in groups.items()
    }
    group_grams = {
        group_id: frozenset().union(*fragments)
        for group_id, fragments in group_fragments.items()
    }
    frequency = Counter(gram for grams in group_grams.values() for gram in grams)
    informative_groups = sum(bool(grams) for grams in group_grams.values())
    # Ubiquitous words carry no evidence about which local explanation applies.
    # A lone explanation still needs a useful target match, so it uses ordinary
    # weights instead of an all-zero one-document IDF vector.
    weights = {
        gram: math.log((informative_groups + 1) / (count + 1))
        if informative_groups > 1 else 1.0
        for gram, count in frequency.items()
    }
    if weights and not any(weight > 0 for weight in weights.values()):
        # Several source pages/documents may contain separate screenshot groups
        # for the same proven local explanation. When every local term is
        # shared, corpus frequency cannot discriminate between those groups;
        # retain ordinary local overlap and let target ambiguity decide.
        weights = dict.fromkeys(weights, 1.0)
    targets = []
    for content in contents:
        heading, _, body = (content or "").partition("\n")
        targets.append((_grams(heading.strip().lstrip("#*- ")), _grams(body)))

    references = {
        group_id: _group_reference_identifiers(group_id, members)
        for group_id, members in groups.items()
    }
    reference_frequency = Counter(
        identifier for identifiers in references.values() for identifier in identifiers
    )

    for group_id in sorted(groups):
        members = groups[group_id]
        receipt = {
            "version": _GROUP_VERSION,
            "groupId": group_id,
            "groupKind": metadata[group_id].get("groupKind", "unknown"),
            "assetIds": [candidate.asset_id for candidate in members],
            "targetIndexes": [],
            "matchBasis": "none",
        }
        roles = {
            str(value)
            for member in members
            for value in (
                (member.source_locator or {}).get("sourceRole"),
                (member.source_locator or {}).get("referenceRole"),
                metadata[group_id].get("sourceRole"),
                metadata[group_id].get("referenceRole"),
            )
            if isinstance(value, str) and value
        }
        if roles:
            receipt["sourceRoles"] = sorted(roles)
        if any(member.mime_type not in SUPPORTED_SOURCE_IMAGE_MIME_TYPES for member in members):
            receipt["status"] = "unsupported_group_member"
            _append_assignment_receipt(assignment_report, receipt)
            continue

        identifiers = {
            identifier for identifier in references[group_id]
            if reference_frequency[identifier] == 1
        }
        ambiguous_context = any(
            isinstance((member.source_locator or {}).get("contentGroup"), dict)
            and (member.source_locator or {})["contentGroup"].get("referenceStatus") == "ambiguous"
            for member in members
        )
        if ambiguous_context:
            # Conflicting Markdown explanations cannot be resolved through
            # OCR or filename similarity. Only a literal asset/group identity
            # in the requested target provides a new, explicit connection.
            identities = {group_id} | {member.asset_id for member in members}
            for member in members:
                bound_asset_id = (member.source_locator or {}).get("workspaceAssetId")
                if isinstance(bound_asset_id, str) and bound_asset_id:
                    identities.add(bound_asset_id)
            identifiers &= identities
        explicit = [
            index for index, content in enumerate(contents)
            if any(_contains_reference(content or "", identifier) for identifier in identifiers)
        ]
        if explicit:
            assigned = explicit
            receipt.update(status="assigned", matchBasis="explicit_reference")
        else:
            if ambiguous_context:
                receipt["status"] = "ambiguous_source_context"
                _append_assignment_receipt(assignment_report, receipt)
                continue
            matches = [
                (
                    max(
                        (_group_match_score(fragment, heading, body, weights)
                         for fragment in group_fragments[group_id]),
                        default=0.0,
                    ),
                    index,
                )
                for index, (heading, body) in enumerate(targets)
            ]
            matches.sort(key=lambda item: (-item[0], item[1]))
            strongest = matches[0][0] if matches else 0.0
            receipt["score"] = round(strongest, _SCORE_PRECISION)
            if strongest < _GROUP_MATCH_FLOOR:
                receipt["status"] = "unresolved_local_evidence"
                _append_assignment_receipt(assignment_report, receipt)
                continue
            competing = [
                index for score, index in matches
                if score >= _GROUP_MATCH_FLOOR
                and strongest - score <= _GROUP_AMBIGUITY_MARGIN
            ]
            if len(competing) != 1:
                receipt.update(status="ambiguous_target", competingTargetIndexes=competing)
                _append_assignment_receipt(assignment_report, receipt)
                continue
            assigned = competing
            receipt.update(status="assigned", matchBasis="local_explanation")
        for index in assigned:
            result[index].extend(members)
        receipt["targetIndexes"] = assigned
        _append_assignment_receipt(assignment_report, receipt)
    return result


def _append_assignment_receipt(report: list[dict] | None, receipt: dict) -> None:
    if report is not None:
        report.append(receipt)


def _group_member_order(candidate: SourceImageCandidate) -> float:
    group = (candidate.source_locator or {}).get("contentGroup") or {}
    order = group.get("groupOrder") if isinstance(group, dict) else None
    return float(order) if isinstance(order, (int, float)) and math.isfinite(order) else math.inf


def _group_local_texts(members: Sequence[SourceImageCandidate]) -> tuple[str, ...]:
    values: dict[str, None] = {}
    for member in members:
        group = (member.source_locator or {}).get("contentGroup") or {}
        if not isinstance(group, dict) or group.get("version") != _GROUP_VERSION:
            group = {}
        caption = group.get("captionText")
        if caption == member.original_name:
            # Original upload names describe availability, not image contents.
            caption = None
        for value in (
            group.get("groupText"), caption,
            member.ocr_text, member.semantic_description,
        ):
            if isinstance(value, str) and value.strip():
                values[value.strip()] = None
    # Keep each explanation/caption as its own matching anchor: a verbose OCR
    # transcript must not dilute a short, source-proven explanatory caption.
    return tuple(values)


def _group_reference_identifiers(
    group_id: str, members: Sequence[SourceImageCandidate]
) -> set[str]:
    identifiers = {group_id}
    for member in members:
        identifiers.add(member.asset_id)
        if member.occurrence_id:
            identifiers.add(member.occurrence_id)
        if member.original_name:
            identifiers.add(member.original_name)
        locator = member.source_locator or {}
        for key in ("sourceUnitId", "sourceBlockId", "objectLocator", "workspaceAssetId"):
            value = locator.get(key)
            if isinstance(value, str) and value:
                identifiers.add(value)
    return identifiers


def _contains_reference(content: str, identifier: str) -> bool:
    # Identifiers must be literal standalone references. Plain common words
    # (including short synthetic IDs) need a labelled attribute or quotation.
    escaped = re.escape(identifier)
    if len(identifier) < 4 or not re.search(r"[\d./:_-]", identifier):
        return bool(re.search(rf"[\"']{escaped}[\"']", content, re.IGNORECASE))
    return bool(re.search(rf"(?<![\w.-]){escaped}(?![\w.-])", content, re.IGNORECASE))


def _group_match_score(
    local: frozenset[str],
    heading: frozenset[str],
    body: frozenset[str],
    weights: dict[str, float],
) -> float:
    evidence = frozenset(gram for gram in local if weights.get(gram, 0) > 0)
    if not evidence:
        return 0.0
    scores = []
    for target in (heading, heading | body):
        shared = evidence & target
        # A lone syllable pair or English function-like word is insufficient
        # to connect a whole screenshot sequence to a new page.
        if len(shared) < 2:
            continue
        shared_weight = sum(weights[gram] for gram in shared)
        local_weight = sum(weights[gram] for gram in evidence)
        target_weight = sum(weights.get(gram, 1.0) for gram in target)
        if not local_weight or not target_weight:
            continue
        target_coverage = shared_weight / target_weight
        local_coverage = shared_weight / local_weight
        # Both sides need positive explanatory overlap; a long transcript
        # containing one target term cannot qualify through target brevity.
        if target_coverage < _GROUP_MATCH_FLOOR or local_coverage < _GROUP_MATCH_FLOOR:
            continue
        scores.append(0.6 * target_coverage + 0.4 * local_coverage)
    return max(scores, default=0.0)


def select_source_images(
    candidates: Sequence[SourceImageCandidate],
    *,
    slide_heading: str,
    slide_content: str,
    slide_index: int,
    limit: int | None = 2,
) -> list[SourceImageCandidate]:
    """Return candidates for this slide, best first.

    ``limit=None`` means no semantic count limit. Optional reference offers use
    a finite limit; preservation callers may request every relevant source slot.

    Returns an empty list when nothing clears the relevance floor. The caller is
    expected to fall back to a generated illustration in that case rather than
    place a picture about something else.
    """
    if (limit is not None and limit <= 0) or not candidates:
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
    return [candidate for _, _, candidate in (scored if limit is None else scored[:limit])]


def _relevance(
    candidate: SourceImageCandidate,
    heading_grams: frozenset[str],
    content_grams: frozenset[str],
) -> float:
    """Text-only relevance of one candidate, in ``0.0``-``1.0``.

    Two signals, each measured in the direction its lengths allow.

    The source heading against the new heading is short against short, so Dice
    is right: a source heading should be penalised for the parts it does not
    share. But a proposal deck's headings are often section markers — measured
    on a real one, nine of ten pictures were headed "02 · SOLUTION" — so that
    signal alone finds nothing.

    The source slide's body text carries the subject, and it is long. Asking how
    much of it appears in the new slide would punish it for its own length, so
    the question is reversed: how much of what the new slide talks about is
    present in the source slide. A long source can then answer a short slide.
    """
    source_heading_grams = _grams(candidate.source_heading or "")
    source_text_grams = _grams(candidate.source_text or "")
    if not source_heading_grams and not source_text_grams:
        return _UNTITLED_BASELINE

    return (
        _HEADING_WEIGHT * _dice(source_heading_grams, heading_grams)
        + _CONTENT_WEIGHT * _containment(source_heading_grams, content_grams)
        + _SOURCE_TEXT_WEIGHT
        * _containment(heading_grams | content_grams, source_text_grams)
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
