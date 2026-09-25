"""Choose the page plan: model-structured when grounded, heuristic otherwise."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import numpy as np

from PIL import Image

from services.source_content.raster_boundary import enforce_boundaries
from services.source_content.raster_crop_edges import enforce_crop_edges, text_ink_clusters
from services.source_content.raster_exactly_once import enforce_exactly_once
from services.source_content.raster_exactly_once_repair import dissolve_composites
from services.source_content.raster_ink_coverage import cover_uncovered_ink
from services.source_content.raster_number_guard import guard_numbers
from services.source_content.raster_paragraphs import absorb_fragments
from services.source_content.raster_captures import PageContext, detect_captures
from services.source_content.raster_llm_tables import restructure_tables
from services.source_content.raster_llm_transcribe import request_transcripts
from services.source_content.raster_tight_boxes import tighten
from services.source_content.raster_title_reread import reread_headlines
from services.source_content.raster_text_guards import targeted_hangul_reads, targeted_number_reads
from services.source_content.raster_llm_structure import VisionJson, capture_candidates, request_structure
from services.source_content.raster_page_plan import PagePlan, line_needs_help, plan_page
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_structure_apply import apply_structure

logger = logging.getLogger(__name__)


def plan_is_lossless(plan: PagePlan, words: tuple[RedesignWord, ...]) -> bool:
    """Every token carried once (or unpromoted) and editable text equals its tokens."""

    carried = [i for region in plan.regions for i in region.word_indices]
    if len(carried) != len(set(carried)) or set(carried) & set(plan.unpromoted):
        return False
    if set(carried) | set(plan.unpromoted) != set(range(len(words))):
        return False
    for region in plan.regions:
        if region.role in ("title", "body") and not region.editable_fallback:
            source = "".join("".join(words[i].text.split()) for i in region.word_indices)
            if "".join((region.text or "").split()) != source:
                return False
    return True


def structured_plan(
    image: Image.Image,
    words: tuple[RedesignWord, ...],
    lines: tuple[RedesignLine, ...],
    structurer: VisionJson | None,
    executable: Path | None = None,
    tessdata: Path | None = None,
) -> PagePlan:
    """Never fails: any provider, parsing or grounding problem keeps the heuristic plan.

    Word boxes are first cut to their glyph ink; the returned plan always carries
    that inventory (``plan.words``)."""

    gray = np.asarray(image.convert("L"), dtype=np.int16)
    words, lines = tighten(words, lines, gray)
    plan = _structured_plan(image, gray, words, lines, structurer, executable, tessdata)
    plan = card_reading_order(plan)
    return plan if plan.words is not None else replace(plan, words=words)


def card_reading_order(plan: PagePlan) -> PagePlan:
    """Groups keep their order; inside a card, regions read top to bottom (then left
    to right), so a heading, whether text or pixels, precedes the card's body."""

    rank: dict[str, int] = {}
    keys = []
    for index, region in enumerate(plan.regions):
        group = region.group_id if region.group_id is not None else f"#{index}"
        rank.setdefault(group, len(rank))
        x, y = region.bbox[0], region.bbox[1]
        keys.append((rank[group], y, x, index))
    order = sorted(range(len(plan.regions)), key=lambda i: keys[i])
    return replace(plan, regions=tuple(plan.regions[i] for i in order))


def _structured_plan(image, gray, words, lines, structurer, executable, tessdata) -> PagePlan:
    clusters = text_ink_clusters(gray)
    heuristic = guard_numbers(plan_page(image, words, lines), words, image, executable, tessdata)
    heuristic = _settle(_dissolved(heuristic, words, gray), words, gray, clusters)
    heuristic = _covered(heuristic, words, image, gray, clusters)
    heuristic = _exactly_once(heuristic, words, gray, clusters, image)
    if structurer is None or not lines:
        return heuristic
    try:
        page = PageContext(image, words, reread_headlines(image, lines, executable=executable, tessdata=tessdata))
        candidates = capture_candidates(page, detect_captures(page))
        raw = None
        for attempt in range(2):  # one retry for a transient provider error
            try:
                raw = request_structure(page, candidates, structurer, lambda line: line_needs_help(page, line))
                break
            except Exception:
                if attempt:
                    raise
        raw = restructure_tables(page, raw, structurer) if raw else None
        targeted = targeted_number_reads(image, page.words, page.lines, executable=executable, tessdata=tessdata)
        transcripts = _transcripts(page, raw, structurer)
        hangul = targeted_hangul_reads(image, page.words, page.lines, executable=executable, tessdata=tessdata)
        model = str(getattr(structurer, "model", "vision"))
        plan = apply_structure(page, candidates, raw, model, targeted, hangul, transcripts) if raw else None
    except Exception as error:  # provider or model output problem: keep the heuristic page
        logger.warning("raster structuring failed: %s", type(error).__name__)
        return heuristic
    if plan is None:
        logger.warning("raster structuring rejected: unusable structure")
        return heuristic
    try:
        plan = _settle(_dissolved(plan, plan.words or words, gray), plan.words or words, gray, clusters)
    except Exception as error:  # a repair bug must never fail the page: keep the heuristic
        logger.warning("raster boundary repair failed: %s", type(error).__name__)
        return heuristic
    if not plan_is_lossless(plan, plan.words or words):
        logger.warning("raster structuring rejected: plan not lossless")
        return heuristic
    covered = _covered(plan, plan.words or words, image, gray, clusters)
    return _exactly_once(covered, plan.words or words, gray, clusters, image)


def _exactly_once(plan: PagePlan, words, gray, clusters, image) -> PagePlan:
    """Final hard invariant: no source pixel shown twice (then crop edges again)."""

    try:
        current = plan
        for _ in range(3):
            fixed = _settle(enforce_exactly_once(current, words, gray), words, gray, clusters)
            if fixed == current:
                break
            current = fixed
        covered = cover_uncovered_ink(current, words, image)  # ink freed by the repairs
        current = enforce_exactly_once(covered, words, gray)
        absorbed = replace(current, regions=tuple(absorb_fragments(current.regions, words)))
        if absorbed != current:
            current = enforce_exactly_once(absorbed, words, gray)
    except Exception as error:  # a repair bug must never fail the page
        logger.warning("raster exactly-once repair failed: %s", type(error).__name__)
        return plan
    if not plan_is_lossless(current, words):
        logger.warning("raster exactly-once repair rejected: plan not lossless")
        return plan
    return current


def _transcripts(page, raw, structurer) -> dict[int, str]:
    """Independent line transcriptions; empty (older grounding rules) if the call fails."""

    try:
        return request_transcripts(page, raw, structurer) if raw else {}
    except Exception as error:
        logger.warning("raster transcription failed: %s", type(error).__name__)
        return {}


def _dissolved(plan: PagePlan, words, gray) -> PagePlan:
    """Composite "captures" holding separate text blocks become pixel lines first,
    before any repair can pull those text blocks into them."""

    size = (gray.shape[1], gray.shape[0])
    return replace(plan, regions=tuple(dissolve_composites(list(plan.regions), [w.bbox for w in words], size)))


def _settle(plan: PagePlan, words, gray, clusters) -> PagePlan:
    """Boundary invariant, then crop-edge invariant, until both hold (bounded)."""

    for _ in range(3):
        fixed = enforce_crop_edges(enforce_boundaries(plan, words, gray), words, gray, clusters)
        if fixed == plan:
            break
        plan = fixed
    return plan


def _covered(plan: PagePlan, words, image: Image.Image, gray, clusters=()) -> PagePlan:
    """Ink-coverage invariant, then boundaries again for the crops it added."""

    try:
        covered = cover_uncovered_ink(plan, words, image)
        if covered is plan:
            return plan
        repaired = _settle(covered, words, gray, clusters)
        return repaired if plan_is_lossless(repaired, words) else covered
    except Exception as error:  # never fail a page over the coverage pass
        logger.warning("raster ink coverage failed: %s", type(error).__name__)
        return plan
