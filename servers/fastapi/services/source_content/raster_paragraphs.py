"""Paragraph atomicity for redesigned pages.

A paragraph (a title line group, a card heading, a list item, or a run of body
lines without a paragraph gap) is published either entirely as editable text,
when every line passed the two-reader agreement, or entirely as ONE pixel crop
covering the whole paragraph; never a mix of text and pixel pieces, which reads
as a shattered sentence ("업무 상:" / "황을 설" / "명하면").

A line that starts with a list marker (a bullet glyph, "1." / "2)", "①") starts
its own paragraph even without a vertical gap: tightly set list and table-of-
contents items are separate statements, so one unreadable item does not turn
the whole list into one picture. So does a line at twice (or half) the type size
of the line above: a large figure and its label ("68" over "+2.1pp margin") are
separate elements, not one paragraph (x-height-only lines such as "moves" measure
up to ~1.5x smaller than a line with ascenders, which stays one paragraph).
"""

from __future__ import annotations

import re

from services.source_content.raster_captures import _pad, _union
from services.source_content.raster_page_plan import PlannedRegion
from services.source_content.raster_redesign_ocr import line_text

PARAGRAPH_GAP = 1.2  # a vertical gap above this many line heights starts a paragraph
TYPE_SIZE_STEP = 2.0  # a line this many times larger or smaller starts a paragraph
# A bullet glyph (or its usual OCR misreads "*", "-"), or an enumerator not followed by
# a digit ("3. 도입", "2) Scope", but not "12.5%"), then the item's text.
_LIST_ITEM = re.compile(r"^\s*(?:[•·ㆍ・∙▪■□◆◇●○※*\-–—>▶►]|\(?\d{1,2}[.)](?!\d)|[①-⑳])\s*\S")


def starts_list_item(text: str | None) -> bool:
    return bool(text) and bool(_LIST_ITEM.match(text))


def _reading(build, index: int) -> str | None:
    text = build.texts.get(index)
    return text if text is not None else build.readings.get(index)


def paragraphs(build, members: list[int], headings: int, marker) -> list[list[int]]:
    """Member lines split into paragraphs: the heading block, then body runs split
    at list markers, at type-size steps and at paragraph-sized vertical gaps."""

    page = build.page
    lines = [i for i in members if build.tokens.get(i)]
    heading = [i for i in members[:headings] if i in lines]
    result: list[list[int]] = [heading] if heading else []
    current: list[int] = []
    for i in lines:
        if i in heading:
            continue
        if current:
            above = page.lines[current[-1]].bbox
            box = page.lines[i].bbox
            height = min(above[3], box[3])
            gap = box[1] - (above[1] + above[3])
            sizes = sorted((page.lines[current[-1]].height, page.lines[i].height))
            resized = sizes[1] >= TYPE_SIZE_STEP * max(1.0, sizes[0])
            item = marker(build.texts, i) or starts_list_item(_reading(build, i))
            if item or resized or gap > PARAGRAPH_GAP * height:
                result.append(current)
                current = []
        current.append(i)
    if current:
        result.append(current)
    return result


def pixel_paragraph(build, lines: list[int], group: str | None) -> PlannedRegion:
    page = build.page
    return PlannedRegion(
        "screenshot",
        _pad(_union([page.lines[i].bbox for i in lines]), 3, page.size),
        tuple(t for i in lines for t in build.tokens[i]),
        "\n".join(line_text(page.lines[i], page.words) for i in lines),
        confidence=0.4,
        review_reason="low_confidence_text_pixels",
        reason="paragraph_pixels",
        editable_fallback=True,
        group_id=group,
    )


def _compact_tokens(region, words) -> str:
    return "".join("".join(words[k].text.split()) for k in region.word_indices)


def is_fragment(region, words) -> bool:
    """A standalone 1-2 character piece of text (e.g. "02", "의"): never a region."""

    if not region.word_indices or region.role not in ("title", "body", "screenshot") or region.group_id == "footer":
        return False
    if region.role == "screenshot" and not region.editable_fallback:
        return False
    return len(_compact_tokens(region, words)) <= 2


def _gap(a, b) -> float:
    dx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]), 0)
    dy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]), 0)
    return max(dx, dy)


def absorb_fragments(regions, words) -> list:
    """Each fragment joins its nearest carrying region (same group first, within three
    line heights): text into a text paragraph beside it, otherwise one pixel crop."""

    from dataclasses import replace

    regions = list(regions)
    isolated: set[tuple] = set()
    for _ in range(len(regions)):
        index = next(
            (n for n, r in enumerate(regions) if is_fragment(r, words) and r.word_indices not in isolated), None
        )
        if index is None:
            break
        fragment = regions[index]
        reach = 3 * max(8, fragment.bbox[3])
        candidates = [
            (r.group_id != fragment.group_id, _gap(fragment.bbox, r.bbox), n)
            for n, r in enumerate(regions)
            if n != index and r.word_indices and r.role != "table" and _gap(fragment.bbox, r.bbox) <= reach
        ]
        if not candidates:
            isolated.add(fragment.word_indices)  # nothing near it: reported by callers
            continue
        host = regions[min(candidates)[2]]
        host_index = min(candidates)[2]
        box = _union([fragment.bbox, host.bbox])
        both_text = not fragment.editable_fallback and not host.editable_fallback and host.role in ("title", "body")
        fx, fy, fw, fh = fragment.bbox
        before = fx + fw <= host.bbox[0] + 2 or fy + fh <= host.bbox[1] + 2
        if both_text:
            # Beside the host's first (or last) line: one line; above/below: its own line.
            hy = host.bbox[1]
            same_row = hy < fy + fh and fy < hy + min(host.bbox[3], 2 * fh)
            joint = " " if same_row else "\n"
            text = f"{fragment.text}{joint}{host.text}" if before else f"{host.text}{joint}{fragment.text}"
            tokens = fragment.word_indices + host.word_indices if before else host.word_indices + fragment.word_indices
            merged = replace(host, bbox=box, word_indices=tokens, text=text)
        else:
            parts = (fragment, host) if before else (host, fragment)
            merged = replace(
                host,
                role="screenshot" if host.role in ("title", "body", "decoration") else host.role,
                bbox=box,
                word_indices=parts[0].word_indices + parts[1].word_indices,
                text="\n".join(r.text for r in parts if r.text) or None,
                table_cells=None,
                editable_fallback=True,
                reason="paragraph_pixels",
                review_reason=host.review_reason or "low_confidence_text_pixels",
                heading_line_count=0,
            )
        regions[host_index] = merged
        regions.pop(index)
    return regions
