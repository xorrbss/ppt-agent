"""Ground a model's slide structure in OCR evidence and build the page plan.

Deterministic rules (the model only ever names ids):
* every OCR line is placed exactly once; unknown or repeated ids are ignored
  and lines the model left out are placed by position; lines in the bottom
  band are footer; without a model title the largest top-band line is title;
* geometry comes from OCR lines and capture candidates only; a screenshot's
  crop is the union of its candidates and the lines assigned to it, a line
  that crop would cut through is moved into it, and one pixel box is emitted
  once even when both a card and the page name it;
* a line is published as text only with a grounded reading: the OCR reading
  when sound, a reading at least two independent passes agree on, a doubtful
  reading the model verified, or a model correction grounded in the OCR
  (``raster_text_correction``) that stays within 40% of the primary reading.
  A separator mark (middle dot, bullet, colon) the OCR dropped or read as another
  symbol counts as agreed only where the pixels show it (``raster_editable_marks``).
  Otherwise its full line box is kept as pixels.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from services.source_content.raster_captures import Box, PageContext, _center_in, _inside, _inter, _pad, _union
from services.source_content.raster_editable_marks import grounded_view
from services.source_content.raster_llm_structure import Candidate
from services.source_content.raster_page_plan import PagePlan, _unpromoted, line_needs_help
from services.source_content.raster_redesign_ocr import RedesignWord, line_text
from services.source_content.raster_structure_emit import Build, emit, is_noise
from services.source_content.raster_text_guards import (
    corner_fragment,
    glued_symbols,
    number_patched,
    numbers_supported,
    overlapping_line,
    symbol_only,
)
from services.source_content.raster_text_correction import (
    accept_correction,
    adds_punctuation,
    edge_glyphs_grounded,
    levenshtein,
    normalized,
    respace,
    script_swaps_only,
    transcription_agrees,
)

CORRECTED_CONFIDENCE = 0.9
MAX_JUMP_FROM_PRIMARY = 0.4
FOOTER_BAND = 0.93
TITLE_BAND = 0.22


def _ids(values, prefix: str, limit: int) -> list[int]:
    result = []
    for value in values or ():
        text = str(value)
        if text.startswith(prefix) and text[1:].isdigit() and 1 <= int(text[1:]) <= limit:
            result.append(int(text[1:]) - 1)
    return result


def _compact(value: str) -> str:
    return "".join(value.split())


def _majority(readings: tuple[str, ...]) -> str | None:
    """A reading that at least two independent passes produced."""

    counts = Counter(_compact(value) for value in readings if value.strip())
    best = next(iter(sorted(counts, key=lambda key: -counts[key])), None)
    if best is None or counts[best] < 2:
        return None
    return next(value for value in readings if _compact(value) == best)


def _publish(build: Build, index: int, text: str, carried: tuple[int, ...], current: str) -> None:
    line = build.page.lines[index]
    if _compact(text) == _compact(current) and carried:
        build.tokens[index], build.texts[index] = carried, current
        return
    build.unpromoted.update(carried)  # superseded readings of the same pixels
    build.words.append(RedesignWord(text, line.bbox, CORRECTED_CONFIDENCE, f"llm-structure:{build.model}", True))
    build.tokens[index], build.texts[index] = (len(build.words) - 1,), text
    build.corrections.append(f"{current} => {text}")


def _resolve_line(build: Build, index: int, proposed: str | None, verified: bool = False, title: bool = False) -> None:
    page = build.page
    line = page.lines[index]
    carried = [i for i in line.word_indices if i not in build.unpromoted]
    # Symbol-only tokens at a line edge are icon misreads, not text.
    while carried and symbol_only(page.words[carried[0]].text):
        build.unpromoted.add(carried.pop(0))
    while carried and symbol_only(page.words[carried[-1]].text):
        build.unpromoted.add(carried.pop())
    carried = tuple(carried)
    # Published text must equal the carried tokens, so noise tokens are left out.
    current = line_text(replace(line, word_indices=carried), page.words) if carried else ""
    full = line_text(line, page.words)
    build.readings[index] = current
    targeted = build.targeted.get(index)
    if overlapping_line(page.lines, page.words, index):
        build.tokens[index], build.texts[index] = carried, None  # two readings of the same glyphs: pixels
        return
    patched = number_patched(current, targeted)
    swapped = _hangul_patched(build, index, carried)
    candidates = (current, full, *line.alternates, *(c for c in (patched, swapped) if c))
    if build.transcripts:
        _resolve_by_agreement(build, index, candidates, carried, current, targeted)
        return
    corrected = accept_correction(candidates, proposed) if proposed else None
    if corrected is not None and not _publishable(corrected, line.alternates, targeted):
        corrected = None
    rejected = bool(proposed) and corrected is None
    if corrected is not None:
        jump = levenshtein(_compact(current), _compact(corrected)) / max(1, len(_compact(current)))
        # Titles have dedicated re-reads among their alternates: closeness to one suffices.
        if not title and jump > MAX_JUMP_FROM_PRIMARY and not script_swaps_only(_compact(current), _compact(corrected)):
            corrected, rejected = None, True  # too far from what the primary pass saw to be trusted
    if corrected is not None:
        _publish(build, index, corrected, carried, current)
        return
    build.tokens[index] = carried
    if not carried or (rejected and _compact(proposed or "") != _compact(current)):
        build.texts[index] = None  # the model says the reading is wrong and no fix is grounded
        return
    if (verified or not line_needs_help(page, line)) and _publishable(current, line.alternates, targeted):
        build.texts[index] = current
        return
    agreed = _majority(line.alternates)
    if agreed is not None and _publishable(agreed, line.alternates, targeted):
        _publish(build, index, agreed, carried, current)
        return
    build.texts[index] = None


def _resolve_by_agreement(build: Build, index: int, candidates, carried, current: str, targeted) -> None:
    """Two independent readers must agree; the model's transcription is published."""

    transcript = build.transcripts.get(index)
    build.tokens[index] = carried
    line = build.page.lines[index]
    # Marks the OCR dropped or misread count as agreed only where the pixels show them.
    view = grounded_view(build.page, carried, transcript) if carried and transcript else ""
    if (
        carried
        and transcript
        and transcription_agrees(candidates, view)
        and not adds_punctuation(view, normalized(current))
        and (spaced := respace(transcript, current)) is not None
        and edge_glyphs_grounded(spaced, candidates)
        and _publishable(spaced, line.alternates, targeted)
    ):
        _publish(build, index, spaced, carried, current)
    else:
        build.texts[index] = None  # the readers disagree: the line keeps its pixels


def _hangul_patched(build: Build, index: int, carried: tuple[int, ...]) -> str | None:
    """The reading with digit words replaced by their Korean-only re-reads."""

    reads = {i: text for i, text in build.hangul.get(index, {}).items() if i in carried}
    if not reads:
        return None
    words = [replace(word, text=reads[i]) if i in reads else word for i, word in enumerate(build.page.words)]
    return line_text(replace(build.page.lines[index], word_indices=carried), words)


def _publishable(text: str, reads: tuple[str, ...], targeted: tuple[str, ...] | None) -> bool:
    """Numbers confirmed by an independent read; no brackets glued to letters."""

    return numbers_supported(text, reads, targeted) and not glued_symbols(text)


def _fallback_title(page: PageContext, taken: set[int]) -> list[int]:
    """Largest-type line group in the top band (used when the model names no title)."""

    band = [i for i, line in enumerate(page.lines) if line.bbox[1] <= TITLE_BAND * page.size[1] and i not in taken]
    if not band:
        return []
    tallest = max(page.lines[i].height for i in band)
    anchor = min((i for i in band if page.lines[i].height >= 0.9 * tallest), key=lambda i: page.lines[i].bbox[1])
    top = page.lines[anchor].bbox
    return [
        i
        for i in band
        if page.lines[i].height >= 0.75 * tallest and abs(page.lines[i].bbox[1] - top[1]) <= 0.6 * tallest
    ]


def _fit_capture(page: PageContext, box: Box, lines: list[int], placed, candidates: list[Candidate]) -> Box | None:
    """A capture box may not swallow lines the structure placed as slide text.

    When it does, it shrinks to its own lines plus the candidate boxes inside it
    that hold no slide-text line (the actual pictures)."""

    def text_share(area: Box) -> tuple[int, float]:
        inside = [i for i, line in enumerate(page.lines) if _center_in(line.bbox, area)]
        text = [i for i in inside if placed.get(i, ("", 0))[0] in ("title", "subtitle", "group", "table", "footer")]
        return len(text), len(text) / max(1, len(inside))

    count, share = text_share(box)
    if count <= 2 and share <= 0.2:  # an overlay label or two on a capture is fine
        return box
    parts = [page.lines[i].bbox for i in lines]
    for c in candidates:
        if _inside(c.box, box) >= 0.9 and c.box != box:
            inner_count, inner_share = text_share(c.box)
            if inner_count <= 2 and inner_share <= 0.2:
                parts.append(c.box)
    return _union(parts) if parts else None


def _dedupe_shots(shots: list[tuple[Box, list[int], str | None]]) -> list[tuple[Box, list[int], str | None]]:
    kept: list[tuple[Box, list[int], str | None]] = []
    for box, lines, group in sorted(shots, key=lambda shot: shot[2] is None):
        twin = next(
            (n for n, other in enumerate(kept) if max(_inside(box, other[0]), _inside(other[0], box)) >= 0.8), None
        )
        if twin is None:
            kept.append((box, list(lines), group))
        else:
            other = kept[twin]
            kept[twin] = (
                _union([other[0], box]),
                other[1] + [i for i in lines if i not in other[1]],
                other[2] or group,
            )
    return kept


def apply_structure(
    page: PageContext,
    candidates: list[Candidate],
    raw: dict,
    model: str,
    targeted: dict | None = None,
    hangul: dict | None = None,
    transcripts: dict | None = None,
) -> PagePlan | None:
    """Build a plan from the model's id structure; None when it is unusable."""

    count, by_id = len(page.lines), {c.candidate_id: c for c in candidates}
    placed: dict[int, tuple[str, int]] = {}  # line -> (slot kind, slot index)
    footer_band = {
        i
        for i, line in enumerate(page.lines)
        if line.bbox[1] >= FOOTER_BAND * page.size[1]
        or corner_fragment(line.bbox, line_text(line, page.words), page.size)
    }

    def claim(ids: list[int], kind: str, slot: int) -> list[int]:
        taken = [
            i for i in dict.fromkeys(ids) if i not in placed and (kind in ("footer", "shot") or i not in footer_band)
        ]
        for i in taken:
            placed[i] = (kind, slot)
        return taken

    title = claim(_ids(raw.get("title"), "L", count), "title", 0) or claim(
        _fallback_title(page, footer_band), "title", 0
    )
    if not title:
        return None
    subtitle = claim(_ids(raw.get("subtitle"), "L", count), "subtitle", 0)
    tables = []  # tables come from the dedicated table calls: they own their lines first
    for n, entry in enumerate(raw.get("tables") or ()):
        rows = [[claim(_ids(cell, "L", count), "table", n) for cell in row] for row in entry.get("rows") or ()]
        rows = [list(row) for row in rows if any(row)]
        if rows:
            tables.append(rows)
    shots: list[tuple[Box, list[int], str | None]] = []
    for n, entry in enumerate(raw.get("screenshots") or ()):
        lines = claim(_ids(entry.get("lines"), "L", count), "shot", n)
        boxes = [by_id[c].box for c in entry.get("captures") or () if c in by_id] + [page.lines[i].bbox for i in lines]
        if boxes:
            shots.append((_union(boxes), lines, None))
    groups = []
    for n, entry in enumerate(raw.get("groups") or ()):
        heading = claim(_ids(entry.get("heading"), "L", count), "group", n)
        body = claim(_ids(entry.get("body"), "L", count), "group", n)
        icons = claim(_ids(entry.get("icons"), "L", count), "icon", n)
        shots.extend((by_id[c].box, [], f"g{n + 1}") for c in entry.get("captures") or () if c in by_id)
        groups.append((heading, body, icons))
    footer = claim(_ids(raw.get("footer"), "L", count), "footer", 0)
    footer += claim(sorted(footer_band, key=lambda i: page.lines[i].bbox[0]), "footer", 0)
    decoration = claim(_ids(raw.get("decoration"), "L", count), "decoration", 0)
    # Screenshots own every line they contain or would cut through.
    final_shots = []
    owned = {i for _, lines, _ in shots for i in lines}
    for box, lines, group in _dedupe_shots(shots):
        box = _fit_capture(page, box, lines, placed, candidates)
        if box is None:
            continue
        box = _pad(box, 2, page.size)
        for i, line in enumerate(page.lines):
            if i in owned or not _inter(line.bbox, box) or placed.get(i, ("", 0))[0] == "title":
                continue
            if _center_in(line.bbox, box) or _inside(line.bbox, box) >= 0.5:
                lines.append(i)
                owned.add(i)
        final_shots.append((_union([box, *[page.lines[i].bbox for i in lines]]), lines, group))
    shot_lines = {i for _, lines, _ in final_shots for i in lines}
    leftovers = [i for i in range(count) if i not in placed and i not in shot_lines]
    corrections = {}
    for entry in raw.get("corrections") or ():
        ids = _ids([entry.get("line")], "L", count)
        if ids and isinstance(entry.get("text"), str):
            corrections[ids[0]] = entry["text"]
    verified = set(_ids(raw.get("verified"), "L", count))
    build = Build(
        page,
        list(page.words),
        model,
        _unpromoted(page),
        targeted=dict(targeted or {}),
        hangul=dict(hangul or {}),
        transcripts=dict(transcripts or {}),
    )
    kept_as_text = {i for i, (kind, _) in placed.items() if kind in ("title", "table")}
    noise = [i for i in range(count) if i not in shot_lines and i not in kept_as_text and is_noise(page, i)]
    groups = [tuple([i for i in part if i not in noise] for part in group) for group in groups]
    for i in range(count):
        if i not in shot_lines and i not in noise:
            _resolve_line(build, i, corrections.get(i), i in verified, i in title)
    for i in noise:
        build.unpromoted.update(page.lines[i].word_indices)
    for i in shot_lines:
        build.tokens[i] = tuple(t for t in page.lines[i].word_indices if t not in build.unpromoted)
    rest = [i for i in decoration + leftovers if i not in noise]
    footer = [i for i in footer if i not in noise]
    emit(build, title, subtitle, groups, tables, final_shots, footer, rest, noise)
    return PagePlan(
        tuple(build.regions), tuple(sorted(build.unpromoted)), tuple(build.words), tuple(build.corrections), model
    )
