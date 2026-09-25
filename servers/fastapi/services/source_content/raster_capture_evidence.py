"""Verify the structure model's screenshot claims against layout evidence.

The vision model names the lines (and capture candidates) that form a
screenshot; this check keeps the claim only where the page does not show
otherwise:

* a line lying within a capture frame is part of the capture and is never
  released. A frame is a capture candidate (detected capture, bordered panel,
  ruled grid) or an application window found by its dark tool bar, holding no
  more than a stray label of the text the model placed elsewhere on the slide;
* a claimed line outside every frame that continues a paragraph the model
  placed as slide text (same type size, same left edge, the ordinary line
  pitch) is that paragraph's next line, not part of a picture: a paragraph is
  never split between editable text and pixels. Continuation is transitive,
  and another OCR reading lying on a released line goes with it.

Released lines join the card of the paragraph they continue, so they pass
every text gate like any other slide text and are never published unverified.
A claim that releases lines is split into one screenshot per frame (and per
remaining run of loose lines) it uses, each frame clipped off any released
line its edge runs through, so no crop spans the released text.

Runs of claimed text with no such evidence stay pictures: a card of slide
text and a UI panel drawn in the same style (heading, icon bullets, badges)
cannot be told apart from pixels alone, and fidelity beats editability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from services.source_content.raster_captures import (
    Box,
    PageContext,
    _center_in,
    _clusters,
    _inside,
    _inter,
    _toolbar_windows,
)
from services.source_content.raster_llm_structure import Candidate
from services.source_content.raster_panels import panel_rectangles
from services.source_content.raster_redesign_ocr import line_text

FRAME_INSIDE = 0.9  # a capture's line lies within the capture's frame
STRAY_LABELS = 2  # slide-text lines a frame may hold (an overlay label or two)
STRAY_SHARE = 0.2
SLIDE_SCALE = 1.3  # x micro text height: slide type, not UI type (as in detect_captures)
MIN_PANEL_SHARE = 0.002  # smallest panel detect_captures considers, as a page share
PARAGRAPH_SIZE = 0.8  # lines of one paragraph: type size within 20%
PARAGRAPH_EDGE = 0.6  # x line height: the same left edge
PARAGRAPH_PITCH = 0.9  # x line height: the ordinary gap between two lines of a paragraph
REREAD_SHARE = 0.5  # a line this much on a released line is another reading of it
MIN_LETTERS = 3  # a released line holds a real word, never a lone icon glyph
_LETTER = re.compile(r"[0-9A-Za-z가-힣]")


@dataclass(frozen=True)
class ClaimReview:
    """How one screenshot claim was verified (for logs and tests)."""

    index: int
    framed: tuple[int, ...]
    # Runs of claimed lines outside every frame that stay pictures.
    kept: tuple[tuple[int, ...], ...]
    released: tuple[tuple[int, ...], ...]
    # Released line -> the placed slide-text line of the paragraph it continues.
    anchors: dict[int, int] = field(default_factory=dict)


def _line_ids(values, count: int) -> list[int]:
    result = []
    for value in values or ():
        text = str(value)
        if text.startswith("L") and text[1:].isdigit() and 1 <= int(text[1:]) <= count:
            result.append(int(text[1:]) - 1)
    return result


def _ids(lines) -> list[str]:
    return [f"L{i + 1}" for i in lines]


def placed_as_text(raw: dict, count: int) -> set[int]:
    """Lines the structure placed as slide text (title, cards, tables, footer)."""

    ids = _line_ids(raw.get("title"), count) + _line_ids(raw.get("subtitle"), count)
    ids += _line_ids(raw.get("footer"), count)
    for group in raw.get("groups") or ():
        ids += _line_ids(group.get("heading"), count) + _line_ids(group.get("body"), count)
    for table in raw.get("tables") or ():
        for row in table.get("rows") or ():
            for cell in row:
                ids += _line_ids(cell, count)
    return set(ids)


def _windows(page: PageContext) -> list[Candidate]:
    """Application windows recognised by a dark tool bar (the ``detect_captures`` rule)."""

    area = page.size[0] * page.size[1]
    panels = [box for box in panel_rectangles(page.gray) if box[2] * box[3] >= MIN_PANEL_SHARE * area]
    boxes = _toolbar_windows(page, panels, SLIDE_SCALE * page.micro_px)
    return [Candidate(f"W{n}", box, "window") for n, box in enumerate(boxes, 1)]


def capture_frames(page: PageContext, candidates: list[Candidate], slide_text: set[int]) -> list[Candidate]:
    """Candidates and windows that frame a capture: at most a stray slide-text label inside."""

    frames = []
    for candidate in [*candidates, *_windows(page)]:
        inside = [i for i, line in enumerate(page.lines) if _center_in(line.bbox, candidate.box)]
        text = [i for i in inside if i in slide_text]
        if len(text) <= STRAY_LABELS and len(text) <= STRAY_SHARE * max(1, len(inside)):
            frames.append(candidate)
    return frames


def _frame_of(page: PageContext, index: int, frames: list[Candidate]) -> int | None:
    """Position of the outermost frame the whole line lies in."""

    holding = [n for n, f in enumerate(frames) if _inside(page.lines[index].bbox, f.box) >= FRAME_INSIDE]
    return max(holding, key=lambda n: frames[n].box[2] * frames[n].box[3], default=None)


def _readable(page: PageContext, index: int) -> bool:
    line = page.lines[index]
    return not page.is_garbage(line) and len(_LETTER.findall(line_text(line, page.words))) >= MIN_LETTERS


def continues(page: PageContext, line: int, anchor: int) -> bool:
    """``line`` is the next (or previous) printed line of the paragraph holding ``anchor``."""

    a, b = page.lines[line], page.lines[anchor]
    tall = max(a.height, b.height, 1.0)
    if min(a.height, b.height) < PARAGRAPH_SIZE * tall or abs(a.bbox[0] - b.bbox[0]) > PARAGRAPH_EDGE * tall:
        return False
    below = a.bbox[1] - (b.bbox[1] + b.bbox[3])
    above = b.bbox[1] - (a.bbox[1] + a.bbox[3])
    return 0 <= below <= PARAGRAPH_PITCH * tall or 0 <= above <= PARAGRAPH_PITCH * tall


def _rereads(page: PageContext, index: int, released) -> bool:
    box = page.lines[index].bbox
    return any(_inter(box, page.lines[i].bbox) >= REREAD_SHARE * box[2] * box[3] for i in released)


def review_claim(
    page: PageContext, index: int, members: list[int], frames: list[Candidate], slide_text: set[int]
) -> tuple[ClaimReview, dict[int, list[int]]]:
    """Split a claim's lines into framed lines, kept runs and released runs.

    Returns the review and the framed lines keyed by frame position in ``frames``."""

    by_frame: dict[int, list[int]] = {}
    loose: list[int] = []
    for i in members:
        position = _frame_of(page, i, frames)
        if position is None:
            loose.append(i)
        else:
            by_frame.setdefault(position, []).append(i)
    anchors: dict[int, int] = {}
    changed = True
    while changed:
        changed = False
        for i in loose:
            if i in anchors or not _readable(page, i):
                continue
            anchor = next((j for j in [*sorted(slide_text), *anchors] if continues(page, i, j)), None)
            if anchor is not None:
                anchors[i] = anchors.get(anchor, anchor)
                changed = True
    for i in loose:
        if i not in anchors:
            twin = next((j for j in list(anchors) if _rereads(page, i, [j])), None)
            if twin is not None:
                anchors[i] = anchors[twin]
    released = [
        tuple(sorted((i for i in anchors if anchors[i] == root), key=lambda i: page.lines[i].bbox[1]))
        for root in dict.fromkeys(anchors.values())
    ]
    rest = [i for i in loose if i not in anchors]
    kept: list[tuple[int, ...]] = []
    if rest:
        heights = sorted(page.lines[i].height for i in rest)
        reach = max(8.0, 1.2 * heights[len(heights) // 2])
        for run in _clusters([page.lines[i].bbox for i in rest], reach):
            kept.append(tuple(sorted((rest[k] for k in run), key=lambda i: page.lines[i].bbox[1])))
    framed = tuple(i for ids in by_frame.values() for i in ids)
    return ClaimReview(index, framed, tuple(kept), tuple(released), anchors), by_frame


def clip_frame(box: Box, released: list[Box]) -> Box:
    """Shrink a frame off every released text line it overlaps (the cheaper side).

    The capture's extent is its frame; a text line the frame edge runs through
    is slide text beside the capture, not part of it."""

    x0, y0, x1, y1 = box[0], box[1], box[0] + box[2], box[1] + box[3]
    for lx, ly, lw, lh in released:
        if min(x1, lx + lw) <= max(x0, lx) or min(y1, ly + lh) <= max(y0, ly):
            continue
        cuts = {
            "top": (ly + lh - y0) * (x1 - x0),
            "bottom": (y1 - ly) * (x1 - x0),
            "left": (lx + lw - x0) * (y1 - y0),
            "right": (x1 - lx) * (y1 - y0),
        }
        side = min(cuts, key=lambda key: cuts[key])
        if side == "top":
            y0 = ly + lh
        elif side == "bottom":
            y1 = ly
        elif side == "left":
            x0 = lx + lw
        else:
            x1 = lx
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _place(groups: list[dict], run: tuple[int, ...], anchor: int, page: PageContext) -> None:
    """A released run joins the card of the paragraph it continues, else forms its own card."""

    key = f"L{anchor + 1}"
    below = page.lines[run[0]].bbox[1] > page.lines[anchor].bbox[1]
    for group in groups:
        heading = list(group.get("heading") or ())
        for part, values in (("body", group["body"]), ("heading", heading)):
            if key in values:
                at = values.index(key) + (1 if below else 0)
                values[at:at] = _ids(run)
                group[part] = values
                return
    groups.append({"heading": [], "body": _ids(run)})


def verify_screenshot_claims(
    page: PageContext, candidates: list[Candidate], raw: dict
) -> tuple[dict, list[Candidate], list[ClaimReview]]:
    """Return the structure with unevidenced screenshot lines released as slide text.

    A claim that releases nothing is returned unchanged. One that does is
    replaced by one screenshot per frame and per kept run it uses, so no crop
    spans the released text; frames it uses that are not candidates yet
    (windows, frames clipped off released lines) are returned as candidates."""

    count = len(page.lines)
    shots = list(raw.get("screenshots") or ())
    if not shots:
        return raw, candidates, []
    slide_text = placed_as_text(raw, count)
    frames = capture_frames(page, candidates, slide_text)
    by_id = {c.candidate_id: c for c in candidates}
    groups = [dict(group, body=list(group.get("body") or ())) for group in raw.get("groups") or ()]
    kept_shots: list[dict] = []
    reviews: list[ClaimReview] = []
    extra: dict[str, Candidate] = {}

    def framed_as(candidate: Candidate, released: list[Box], n: int) -> str:
        clipped = clip_frame(candidate.box, released)
        if clipped != candidate.box:
            candidate = Candidate(f"{candidate.candidate_id}-{n + 1}", clipped, candidate.kind)
        if candidate.candidate_id not in by_id:
            extra[candidate.candidate_id] = candidate
        return candidate.candidate_id

    for n, entry in enumerate(shots):
        members = list(dict.fromkeys(_line_ids(entry.get("lines"), count)))
        review, by_frame = review_claim(page, n, members, frames, slide_text)
        reviews.append(review)
        if not review.released:
            kept_shots.append(entry)
            continue
        released = [page.lines[i].bbox for run in review.released for i in run]
        for position, lines in by_frame.items():
            kept_shots.append({"captures": [framed_as(frames[position], released, n)], "lines": _ids(lines)})
        kept_shots.extend({"captures": [], "lines": _ids(run)} for run in review.kept)
        used = {frames[position].candidate_id for position in by_frame}
        for capture_id in dict.fromkeys(str(c) for c in entry.get("captures") or ()):
            if capture_id in by_id and capture_id not in used:
                kept_shots.append({"captures": [framed_as(by_id[capture_id], released, n)], "lines": []})
        for run in review.released:
            _place(groups, run, review.anchors[run[0]], page)
    if not any(review.released for review in reviews):
        return raw, candidates, reviews
    verified = dict(raw)
    verified["screenshots"] = kept_shots
    verified["groups"] = groups
    return verified, [*candidates, *extra.values()], reviews


def released_line_count(reviews: list[ClaimReview]) -> int:
    return sum(len(run) for review in reviews for run in review.released)
