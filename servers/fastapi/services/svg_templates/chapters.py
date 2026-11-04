"""Chapters for templates whose source deck changed a chapter tab per chapter.

The compiler marks a layout text box that names a chapter (the same place on
every chapter layout, a different text on each) as a dynamic field:
``chapter_number`` or ``chapter_title``
(docs/svg-template-real-deck-registration-design-20261103.md, section 11).
A generated deck fills them with its own chapters: after the outline, one
model call groups its consecutive pages into chapters and names them within
the tab's budget (``utils/llm_calls/plan_template_chapters.py``). Each name is
measured in every chapter tab with the frame's own font; a name that does not
fit is asked for once more with a smaller budget, and the deck fails with
``svg_template_chapter_title_too_long`` rather than clipping a tab. Numbers
follow the source's style (Ⅰ Ⅱ …, 1 2 …, 01 02 …).

The plan is stored on the outline pages (``SlideOutlineModel.chapter``), so a
resumed job and a saved deck reuse it.
"""
from __future__ import annotations

import math
import re

from models.svg_template_frame import SvgTemplateFrame, SvgTemplateFrameAdaptation
from models.svg_template_codec import unpack_adaptation_fonts

CHAPTER_KEYS = ("chapter_number", "chapter_title")
_UNICODE_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"
_ASCII_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")


def chapter_frames(selection: dict | None) -> list[SvgTemplateFrame]:
    """Adapted frames of a template-redesign selection that show a chapter field."""
    from services.svg_templates.selection_material import template_adaptations, template_package

    if not selection or selection.get("mode") != "template-redesign":
        return []
    package, adaptations = template_package(selection), template_adaptations(selection)
    frames = [SvgTemplateFrameAdaptation.model_validate(unpack_adaptation_fonts(value, package)).frame
              for value in adaptations.values()]
    return [frame for frame in frames if set(CHAPTER_KEYS) & set(frame.dynamic_fields)]


def _source_numbers(frames) -> list[str]:
    numbers = []
    for frame in frames:
        for element in frame.elements:
            if element.dynamic_key == "chapter_number":
                numbers.append("".join(re.findall(r">([^<>]+)<", element.svg)).strip())
    return numbers


def number_style(frames) -> str:
    numbers = [number.rstrip(".)") for number in _source_numbers(frames)]
    if any(number and number[0] in _UNICODE_ROMAN for number in numbers):
        return "roman-unicode"
    if any(re.fullmatch(r"0\d", number) for number in numbers):
        return "zero-padded"
    if numbers and all(number.isdigit() for number in numbers):
        return "arabic"
    return "roman-ascii" if numbers else "roman-unicode"


def format_number(index: int, style: str) -> str:
    if style == "roman-unicode":
        return _UNICODE_ROMAN[index] if index < len(_UNICODE_ROMAN) else str(index + 1)
    if style == "roman-ascii":
        return _ASCII_ROMAN[index] if index < len(_ASCII_ROMAN) else str(index + 1)
    if style == "zero-padded":
        return f"{index + 1:02d}"
    return str(index + 1)


def title_budget(frames) -> int:
    """Characters a chapter name may use: the narrowest chapter tab at its own size."""
    budgets = []
    for frame in frames:
        for element in frame.elements:
            if element.dynamic_key != "chapter_title":
                continue
            size = re.search(r'font-size="([\d.]+)"', element.svg)
            region = element.text_region or element.bounds
            if size:
                budgets.append(math.floor(region.width / float(size.group(1))))
    return max(2, min(budgets)) if budgets else 12


def overlong_titles(frames, chapters: dict[str, str]) -> set[str]:
    """Chapter names (by number) that do not fit some chapter tab."""
    from services.svg_templates.frame_composition import render_frame_svg

    failing = set()
    for number, title in chapters.items():
        for frame in frames:
            try:
                render_frame_svg(frame, dynamic_values={"chapter_number": number, "chapter_title": title})
            except ValueError as error:
                if str(error) != "svg_template_dynamic_text_overflow":
                    raise
                failing.add(number)
    return failing


def assign_chapters(outline, plan: list[tuple[int, str]], style: str) -> dict[str, str]:
    """Write ``{"number", "title"}`` on every outline page; returns ``{number: title}``."""
    starts = [start for start, _ in plan] + [len(outline.slides)]
    titles = {}
    for position, (start, title) in enumerate(plan):
        number = format_number(position, style)
        titles[number] = title
        for slide in outline.slides[start:starts[position + 1]]:
            slide.chapter = {"number": number, "title": title}
    return titles


async def plan_outline_chapters(request, outline) -> None:
    """Give every outline page its chapter when the template shows chapter tabs."""
    from utils.llm_calls.plan_template_chapters import plan_template_chapters
    from utils.outline_utils import explicit_outline_title

    frames = chapter_frames(getattr(request, "_svg_template_selection", None))
    if not frames or not outline.slides:
        return
    style = number_style(frames)
    existing = [getattr(slide, "chapter", None) for slide in outline.slides]
    if all(isinstance(chapter, dict) for chapter in existing):
        # A resumed job or a caller-supplied outline: keep it, but it must still fit.
        titles = {chapter["number"]: chapter["title"] for chapter in existing}
        if overlong_titles(frames, titles):
            raise ValueError("svg_template_chapter_title_too_long")
        return
    page_titles = [explicit_outline_title(slide.content) or slide.content.strip().splitlines()[0][:80]
                   for slide in outline.slides]
    budget = title_budget(frames)
    language = getattr(request, "language", None)
    for _attempt in range(2):
        plan = await plan_template_chapters(page_titles, max_title_chars=budget, language=language)
        titles = {format_number(position, style): title for position, (_, title) in enumerate(plan)}
        failing = overlong_titles(frames, titles)
        if not failing:
            assign_chapters(outline, plan, style)
            return
        # Ask again for names shorter than the shortest one that overflowed.
        budget = max(2, min(budget, *(len(titles[number]) for number in failing)) - 2)
    raise ValueError("svg_template_chapter_title_too_long")


def chapter_ordinal(number: str) -> int | None:
    """The 1-based position a chapter number names (Ⅲ, III, 03, 3 all give 3)."""
    text = str(number or "").strip().rstrip(".)")
    if text and text[0] in _UNICODE_ROMAN:
        return _UNICODE_ROMAN.index(text[0]) + 1
    if text.upper() in _ASCII_ROMAN:
        return _ASCII_ROMAN.index(text.upper()) + 1
    return int(text) if text.isdigit() else None


def header_values(frame: SvgTemplateFrame, title: str, chapter: dict | None) -> dict[str, str]:
    """The path and its number a frame's header shows (``3 | 수행 방안 › 수행 일정``).

    A page with no chapter has no path to show: both are then empty and the
    frame draws nothing for them.
    """
    fields = set(frame.dynamic_fields)
    values: dict[str, str] = {}
    chapter_title = str(chapter.get("title") or "").strip() if isinstance(chapter, dict) else ""
    if "breadcrumb" in fields:
        title = (title or "").strip()
        parts = [part for part in (chapter_title, title) if part]
        if len(parts) == 2 and parts[0] == parts[1]:
            parts = parts[:1]
        values["breadcrumb"] = " \u203a ".join(parts) if chapter_title else ""
    if "breadcrumb_number" in fields:
        ordinal = chapter_ordinal(chapter.get("number")) if isinstance(chapter, dict) else None
        values["breadcrumb_number"] = str(ordinal) if ordinal and chapter_title else ""
    return values


def chapter_values(frame: SvgTemplateFrame, chapter: dict | None) -> dict[str, str]:
    """The chapter fields a frame shows, from the page's chapter; empty when it shows none."""
    fields = set(CHAPTER_KEYS) & set(frame.dynamic_fields)
    if not fields:
        return {}
    if not isinstance(chapter, dict) or not chapter.get("number") or not chapter.get("title"):
        raise ValueError("svg_template_chapter_missing")
    return {"chapter_number": str(chapter["number"]), "chapter_title": str(chapter["title"])}


def chapter_from_context(context: dict | None) -> dict | None:
    """The chapter a saved page was published with (its template page context)."""
    values = (context or {}).get("dynamic_values") or {}
    if "chapter_number" in values and "chapter_title" in values:
        return {"number": values["chapter_number"], "title": values["chapter_title"]}
    return None


def page_chapter(ctx, index: int) -> dict | None:
    """The outline chapter of page ``index`` in a generation context."""
    chapters = getattr(ctx, "template_chapters", None)
    return chapters[index] if chapters and 0 <= index < len(chapters) else None
