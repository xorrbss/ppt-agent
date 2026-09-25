"""One repair step for an exactly-once conflict (see ``raster_exactly_once``)."""

from __future__ import annotations

from dataclasses import replace

from services.source_content.raster_exactly_once import (
    _area,
    _box_of,
    _clusters,
    _cut,
    _ink_in,
    _is_pixels,
    _MIN_INK,
    _union,
    _word_crop,
)

_DUPLICATE_READ = 0.5
TEXT_CROP_REASONS = frozenset({"paragraph_pixels", "line_pixels", "low_confidence_text_pixels", "unreadable_glyphs"})


def _overlap_share(a, b) -> float:
    return _area(_cut(a, b)) / max(1.0, min(_area(a), _area(b)))


def _rebox(regions, p: int, keep: list[int], boxes, size) -> list:
    """Crop ``p`` re-boxed around ``keep`` as one crop (never split into fragments), or removed."""

    crop = regions[p]
    if not keep:
        return regions[:p] + regions[p + 1 :]
    ordered = [k for k in crop.word_indices if k in keep]
    reboxed = replace(crop, bbox=_box_of(ordered, boxes, size), word_indices=tuple(ordered))
    return regions[:p] + [reboxed] + regions[p + 1 :]


def _atomic(region) -> bool:
    """A pixel crop of text (paragraph or line): it may move or merge, never split."""

    return region.editable_fallback and bool(region.text) and region.reason in TEXT_CROP_REASONS


def _split_word_crop(regions, p: int, boxes, mask, size, unpromoted: set[int]) -> list | None:
    """A word crop yields words other regions already show (a second reading of the
    same pixels is unpromoted) or that lie inside another crop (moved to it)."""

    crop = regions[p]
    keep, moved = [], {}
    for k in crop.word_indices:
        if k in unpromoted:
            continue
        duplicate = any(
            _overlap_share(boxes[k], boxes[j]) >= _DUPLICATE_READ
            for o, other in enumerate(regions)
            if o != p
            for j in other.word_indices
            if j not in unpromoted
        )
        if duplicate:
            unpromoted.add(k)
            continue
        host = next(
            (
                o
                for o, other in enumerate(regions)
                if o != p
                and _is_pixels(other)
                and _area(_cut(other.bbox, boxes[k])) >= 0.3 * _area(boxes[k])
            ),
            None,
        )
        if host is None:
            keep.append(k)
        else:
            moved.setdefault(host, []).append(k)
    for host, tokens in moved.items():
        other = regions[host]
        regions[host] = replace(
            other,
            role="screenshot" if other.role == "decoration" else other.role,
            word_indices=other.word_indices + tuple(tokens),
            editable_fallback=other.editable_fallback or other.role == "decoration",
        )
    result = _rebox(regions, p, keep, boxes, size)
    return None if result == regions and not moved and len(keep) == len(crop.word_indices) else result


def _trim(crop, other_box, boxes, keep_share: float):
    """Largest box moving one edge of ``crop`` off ``other_box``, keeping its words."""

    x, y, w, h = crop.bbox
    ox, oy, ow, oh = other_box
    options = [
        (x, oy + oh + 1, w, y + h - (oy + oh + 1)),
        (x, y, w, oy - 1 - y),
        (ox + ow + 1, y, x + w - (ox + ow + 1), h),
        (x, y, ox - 1 - x, h),
    ]
    best = None
    for box in options:
        if box[2] <= 0 or box[3] <= 0 or _area(box) < keep_share * _area(crop.bbox):
            continue
        if not all(
            box[0] <= boxes[k][0] and box[1] <= boxes[k][1] and boxes[k][0] + boxes[k][2] <= box[0] + box[2]
            and boxes[k][1] + boxes[k][3] <= box[1] + box[3]
            for k in crop.word_indices
        ):
            continue
        if best is None or _area(box) > _area(best):
            best = box
    return None if best is None else tuple(int(v) for v in best)


def _real_capture(region) -> bool:
    return region.role == "screenshot" and not region.editable_fallback


def dissolve_composites(regions, boxes, size) -> list:
    """A "capture" holding several separate editable text blocks is a slide area,
    not a picture: it becomes pixel lines of its own words (the captures and text
    inside it stay; the coverage pass keeps any remaining ink)."""

    result = []
    for region in regions:
        if not _real_capture(region):
            result.append(region)
            continue
        inside = [
            other
            for other in regions
            if other is not region
            and not _is_pixels(other)
            and _area(_cut(region.bbox, other.bbox)) >= 0.5 * _area(other.bbox)
        ]
        lines = sum(len((other.text or "").split(chr(10))) for other in inside)
        if len(inside) < 2 or lines < 3:
            result.append(region)
            continue
        for group in _clusters(list(region.word_indices), boxes) if region.word_indices else []:
            ordered = [k for k in region.word_indices if k in group]
            result.append(
                replace(
                    region,
                    bbox=_box_of(ordered, boxes, size),
                    word_indices=tuple(ordered),
                    text=None,
                    editable_fallback=True,
                    reason="line_pixels",
                    review_reason="composite_dissolved",
                )
            )
    return result


def resolve(regions, conflict, words, mask, size, unpromoted: set[int]) -> list | None:
    boxes = [word.bbox for word in words]
    p, o, kind = conflict
    regions = list(regions)
    crop, other = regions[p], regions[o]
    for index in (p, o):  # a pixel-fallback line gives up what others already show
        if _is_pixels(regions[index]) and _word_crop(regions[index], boxes):
            result = _split_word_crop(regions, index, boxes, mask, size, unpromoted)
            if result is not None:
                return result
    if not _is_pixels(other):
        from services.source_content.raster_crop_edge_repair import _grown, _take_line

        hit = next((k for k in other.word_indices if _cut(crop.bbox, boxes[k]) is not None), None)
        hit_box = boxes[hit] if hit is not None else other.bbox
        trimmed = _trim(crop, _box_of([], boxes, size, hit_box), boxes, 0.3)
        if trimmed is not None:
            regions[p] = replace(crop, bbox=trimmed)
            return regions
        tokens, text, pieces = _take_line(other, hit, words)
        line_box = _box_of(list(tokens), boxes, size, other.bbox)
        if _area(_cut(crop.bbox, line_box)) < 0.5 * _area(line_box) and not _atomic(crop):
            # The line lies mostly outside the crop: the crop gives way around it
            # (split), so text never pulls a crop across the page.
            return _subtract_box(regions, p, line_box, boxes, mask)
        real = crop.role == "screenshot" and not crop.editable_fallback
        regions[p] = _grown(crop, line_box, tokens, [text], real)
        return regions[:o] + pieces + regions[o + 1 :]
    for index, host in ((p, o), (o, p)):
        region = regions[index]
        if region.word_indices or region.role != "decoration" and region.review_reason != "uncovered_ink":
            continue
        inside = _area(_cut(region.bbox, regions[host].bbox)) >= 0.5 * _area(region.bbox)
        if region.role == "decoration" or inside:
            return regions[:index] + regions[index + 1 :]  # the other region shows these pixels
    small, large = (p, o) if _area(crop.bbox) <= _area(other.bbox) else (o, p)
    nested = _area(_cut(regions[small].bbox, regions[large].bbox)) >= 0.9 * _area(regions[small].bbox)
    atomic = _atomic(regions[small]) or _atomic(regions[large])
    if kind == "word" and not nested and not atomic:
        settled = _settle_word(regions, p, o, boxes, mask)
        if settled is not None:
            return settled
    if _area(_cut(regions[small].bbox, regions[large].bbox)) >= 0.9 * _area(regions[small].bbox):
        # Nested crops: the container already shows the inner one's pixels.
        inner, outer = regions[small], regions[large]
        regions[large] = replace(outer, word_indices=outer.word_indices + inner.word_indices)
        if inner.word_indices and outer.role == "decoration":
            regions[large] = replace(regions[large], role="screenshot", editable_fallback=True)
        return regions[:small] + regions[small + 1 :]
    for index, against, share in ((small, large, 0.3), (large, small, 0.6)):
        trimmed = _trim(regions[index], regions[against].bbox, boxes, share)
        if trimmed is not None and _ink_in(mask, _cut(trimmed, regions[against].bbox)) < _MIN_INK:
            regions[index] = replace(regions[index], bbox=trimmed)
            return regions
    if _atomic(regions[small]) and _atomic(regions[large]):
        return last_resort(regions, (p, o, kind), words)  # two text crops merge, never split
    if atomic:
        # A text crop keeps its box; the picture crop beside it gives way (trimmed,
        # else split around it, its words under the text crop moving into it).
        text, picture = (small, large) if _atomic(regions[small]) else (large, small)
        trimmed = _trim(regions[picture], regions[text].bbox, boxes, 0.3)
        if trimmed is not None:
            regions[picture] = replace(regions[picture], bbox=trimmed)
            return regions
        hole = regions[text].bbox
        inside = tuple(
            k for k in regions[picture].word_indices if _area(_cut(hole, boxes[k])) >= 0.5 * _area(boxes[k])
        )
        regions[text] = replace(
            regions[text],
            bbox=_union(hole, _box_of(list(inside), boxes, size)) if inside else hole,
            word_indices=regions[text].word_indices + inside,
        )
        regions[picture] = replace(
            regions[picture], word_indices=tuple(k for k in regions[picture].word_indices if k not in inside)
        )
        return _subtract_box(regions, picture, regions[text].bbox, boxes, mask)
    return _subtract(regions, small, large, boxes, mask)


def _settle_word(regions, p: int, o: int, boxes, mask) -> list | None:
    """Crop ``p`` shows part of a word crop ``o`` carries: the word belongs to the
    crop holding most of it (which grows by that word only) and the other crop's
    edge moves off it. A pixel line that cannot move its edge is split around the
    word; a capture that cannot is left to the ledger (None)."""

    crop, other = regions[p], regions[o]
    k = next((k for k in other.word_indices if _cut(crop.bbox, boxes[k]) is not None), None)
    if k is None:
        return None
    share = _area(_cut(crop.bbox, boxes[k]))
    other_share = _area(_cut(other.bbox, boxes[k]))
    regions = list(regions)
    winner, loser = (p, o) if share > other_share else (o, p)
    if k not in regions[winner].word_indices:
        regions[winner] = replace(
            regions[winner],
            bbox=_union(regions[winner].bbox, boxes[k]),
            word_indices=regions[winner].word_indices + (k,),
        )
        regions[loser] = replace(regions[loser], word_indices=tuple(j for j in regions[loser].word_indices if j != k))
    trimmed = _trim(regions[loser], boxes[k], boxes, 0.3)
    if trimmed is not None:
        regions[loser] = replace(regions[loser], bbox=trimmed)
        return regions
    if not regions[loser].word_indices:
        return _subtract_box(regions, loser, boxes[k], boxes, mask)
    return None


def _subtract_box(regions, p: int, hole, boxes, mask) -> list:
    """Crop ``p`` minus ``hole`` (up to four crops); words go by centre, whole."""

    crop = regions[p]
    x, y, w, h = crop.bbox
    ox, oy, ow, oh = hole
    top, bottom = max(y, oy), min(y + h, oy + oh)
    pieces = [
        (x, y, w, oy - y),
        (x, oy + oh, w, y + h - (oy + oh)),
        (x, top, ox - x, bottom - top),
        (ox + ow, top, x + w - (ox + ow), bottom - top),
    ]
    pieces = [tuple(int(v) for v in piece) for piece in pieces if piece[2] > 0 and piece[3] > 0]
    placed: dict[int, list[int]] = {n: [] for n in range(len(pieces))}
    for k in crop.word_indices:
        cx, cy = boxes[k][0] + boxes[k][2] / 2, boxes[k][1] + boxes[k][3] / 2
        best = min(
            range(len(pieces)),
            key=lambda n: max(pieces[n][0] - cx, 0, cx - pieces[n][0] - pieces[n][2])
            + max(pieces[n][1] - cy, 0, cy - pieces[n][1] - pieces[n][3]),
            default=None,
        )
        if best is None:
            return regions  # nothing left around the hole: leave it to the ledger
        placed[best].append(k)
    parts = []
    for n, box in enumerate(pieces):
        if not placed[n] and _ink_in(mask, box) < _MIN_INK:
            continue
        parts.append(replace(crop, bbox=box, word_indices=tuple(placed[n]), text=crop.text if n == 0 else None))
    return regions[:p] + parts + regions[p + 1 :]


def _subtract(regions, small: int, large: int, boxes, mask) -> list:
    """The smaller crop minus the larger crop, as up to four crops. Words of the
    smaller crop that the larger one shows (even partly) move to it, and the larger
    crop grows just enough to hold them whole (one pass, so growth never chains)."""

    inner, outer = regions[small], regions[large]
    grown = outer.bbox
    moved = [k for k in inner.word_indices if _cut(outer.bbox, boxes[k]) is not None]
    for k in moved:  # one pass over the original box: growth never chains
        grown = _union(grown, boxes[k])
    x, y, w, h = inner.bbox
    ox, oy, ow, oh = grown
    top, bottom = max(y, oy), min(y + h, oy + oh)
    pieces = [
        (x, y, w, oy - y),
        (x, oy + oh, w, y + h - (oy + oh)),
        (x, top, ox - x, bottom - top),
        (ox + ow, top, x + w - (ox + ow), bottom - top),
    ]
    pieces = [tuple(int(v) for v in piece) for piece in pieces if piece[2] > 0 and piece[3] > 0]
    placed: dict[int, list[int]] = {n: [] for n in range(len(pieces))}
    for k in inner.word_indices:
        if k in moved:
            continue
        cx, cy = boxes[k][0] + boxes[k][2] / 2, boxes[k][1] + boxes[k][3] / 2
        home = next(
            (n for n, b in enumerate(pieces) if b[0] <= cx <= b[0] + b[2] and b[1] <= cy <= b[1] + b[3]), None
        )
        if home is None:
            moved.append(k)
            grown = _union(grown, boxes[k])
        else:
            placed[home].append(k)
    regions = list(regions)
    regions[large] = replace(outer, bbox=grown, word_indices=outer.word_indices + tuple(moved))
    if moved and outer.role == "decoration":
        regions[large] = replace(regions[large], role="screenshot", editable_fallback=True)
    parts = []
    for n, box in enumerate(pieces):
        if not placed[n] and _ink_in(mask, box) < _MIN_INK:
            continue
        own = placed[n]
        if own:  # a piece holds its words whole
            for k in own:
                box = _union(box, boxes[k])
        parts.append(replace(inner, bbox=box, word_indices=tuple(own), text=inner.text if n == 0 else None))
    return regions[:small] + parts + regions[small + 1 :]


def last_resort(regions, conflict, words) -> list:
    """Guaranteed repair after the exact ones are exhausted: two crops sharing a
    word or ink become one crop; a crop showing a text line takes that line.
    Every step removes a region or a text line, so the loop ends."""

    from services.source_content.raster_crop_edge_repair import _grown, _take_line

    p, o, _ = conflict
    crop, other = regions[p], regions[o]
    boxes = [word.bbox for word in words]
    if not _is_pixels(other):
        tokens, text, pieces = _take_line(other, None, words)
        real = _real_capture(crop)
        box = other.bbox
        for k in tokens:
            box = _union(box, boxes[k])
        regions = list(regions)
        regions[p] = _grown(crop, box, tokens, [text], real)
        return regions[:o] + pieces + regions[o + 1 :]
    both_real = _real_capture(crop) and _real_capture(other)
    big, small = (crop, other) if _area(crop.bbox) >= _area(other.bbox) else (other, crop)
    tokens = big.word_indices + small.word_indices
    text = "\n".join(r.text for r in (big, small) if r.text and r.editable_fallback) or None
    merged = replace(
        big,
        role="screenshot" if big.role == "decoration" and tokens else big.role,
        bbox=_union(crop.bbox, other.bbox),
        word_indices=tokens,
        text=None if both_real else text,
        table_cells=None,
        editable_fallback=not both_real and big.role != "decoration",
        reason="merged_crop",
        review_reason=big.review_reason or "exactly_once_merge",
        heading_line_count=0,
    )
    low, high = sorted((p, o))
    return regions[:low] + [merged] + regions[low + 1 : high] + regions[high + 1 :]


def give_way(regions, conflict, words, mask, unpromoted: set[int]) -> list | None:
    """Exact repair without growth: the crop that is not a text crop gives way (split
    around the other region's box); None when both are text crops (they merge)."""

    p, o, _ = conflict
    boxes = [word.bbox for word in words]
    crop, other = regions[p], regions[o]
    if not _is_pixels(other):
        if _atomic(crop):
            return None
        hole = other.bbox
        regions = list(regions)
        for k in crop.word_indices:
            if _area(_cut(hole, boxes[k])) >= 0.5 * _area(boxes[k]):
                unpromoted.add(k)  # the text region already shows these glyphs
        regions[p] = replace(crop, word_indices=tuple(k for k in crop.word_indices if k not in unpromoted))
        return _subtract_box(regions, p, hole, boxes, mask)
    if _atomic(crop) and _atomic(other):
        return None
    if _atomic(crop) or _atomic(other):
        text, picture = (p, o) if _atomic(crop) else (o, p)
        hole = regions[text].bbox
        inside = tuple(k for k in regions[picture].word_indices if _area(_cut(hole, boxes[k])) >= 0.5 * _area(boxes[k]))
        regions = list(regions)
        regions[text] = replace(regions[text], word_indices=regions[text].word_indices + inside)
        regions[picture] = replace(
            regions[picture], word_indices=tuple(k for k in regions[picture].word_indices if k not in inside)
        )
        return _subtract_box(regions, picture, hole, boxes, mask)
    small, large = (p, o) if _area(crop.bbox) <= _area(other.bbox) else (o, p)
    return _subtract(regions, small, large, boxes, mask)


def word_hole(regions, conflict, words, mask, unpromoted: set[int]) -> list | None:
    """Word-level exact repair: the word another region carries leaves this crop.
    A picture crop gets a hole the size of that word (its own second reading of the
    same glyphs is unpromoted); a text crop never splits: it takes the word (or the
    whole text block) instead, and the other crop gets the hole."""

    from services.source_content.raster_crop_edge_repair import _grown, _take_line

    p, o, kind = conflict
    if kind != "word":
        return None
    boxes = [word.bbox for word in words]
    crop, other = regions[p], regions[o]
    k = next((j for j in other.word_indices if j not in unpromoted and _cut(crop.bbox, boxes[j]) is not None), None)
    if k is None:
        return None
    regions = list(regions)
    if not _atomic(crop):
        for j in crop.word_indices:
            if _area(_cut(boxes[j], boxes[k])) >= 0.5 * min(_area(boxes[j]), _area(boxes[k])):
                unpromoted.add(j)  # a second reading of the same glyphs
        regions[p] = replace(crop, word_indices=tuple(j for j in crop.word_indices if j not in unpromoted))
        return _subtract_box(regions, p, boxes[k], boxes, mask)
    if not _is_pixels(other):
        tokens, text, _ = _take_line(other, k, words)
        regions[p] = _grown(crop, _box_of(list(tokens), boxes, (10**6, 10**6), other.bbox), tokens, [text], False)
        return regions[:o] + regions[o + 1 :]
    if _atomic(other):
        return None  # two text crops: merged by the caller
    regions[p] = replace(crop, bbox=_union(crop.bbox, boxes[k]), word_indices=crop.word_indices + (k,))
    regions[o] = replace(other, word_indices=tuple(j for j in other.word_indices if j != k))
    return _subtract_box(regions, o, boxes[k], boxes, mask)
