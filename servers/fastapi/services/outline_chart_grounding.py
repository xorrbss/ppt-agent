"""Keep a ``chart:`` outline line only when every value in it is written in the source.

A value counts as written when its number (thousands separators, decimals and units ignored,
digits otherwise exact) stands in one source sentence, line or Markdown table together with
the chart's label for it (table rows count with their header row). Number presence alone is not enough: a computed ``2`` is found
somewhere in almost any document.

The outline step is asked to copy a numeric series from the source into a
``chart: Label value unit, ...`` line, which the svg driver turns into a native chart
requirement (``generation_core/drivers/svg/native_requirements.py``). A model sometimes
writes values it computed instead (a month count from a date range), and the chart then
misstates the source. This guard is deterministic: a chart line is kept only if each of its
values appears as a number in the source text; otherwise the whole line is removed (values
are never altered or invented) and the reason is recorded.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import unicodedata

from services.generation_core.drivers.svg.native_requirements import _charts
from models.presentation_outline_model import PresentationOutlineModel

LOGGER = logging.getLogger(__name__)

_NUMBER_TOKEN = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_CHART_LINE = re.compile(r"(?:\bchart\b|차트)\s*[:：]", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+|(?<=다\.)")


def normalize_number(token: str) -> str:
    """``4,820`` -> ``4820``; ``12.50`` -> ``12.5``; ``3.0`` -> ``3``. Digits are otherwise exact."""
    token = token.replace(",", "")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    whole, _, fraction = token.partition(".")
    whole = whole.lstrip("0") or "0"
    return f"{whole}.{fraction}" if fraction else whole


def _compact(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _numbers(text: str) -> set[str]:
    return {normalize_number(m.group()) for m in _NUMBER_TOKEN.finditer(unicodedata.normalize("NFKC", text))}


_LABEL_BIGRAM_COVERAGE = 0.6


def _label_in(wanted: str, text: str) -> bool:
    """The label stands in the segment, allowing punctuation and small wording differences
    (``데이터와 통계`` for ``데이터·통계``): at least 60% of its character bigrams are present."""
    if not wanted or wanted in text:
        return True
    plain_wanted = "".join(ch for ch in wanted if ch.isalnum())
    plain_text = "".join(ch for ch in text if ch.isalnum())
    if plain_wanted in plain_text:
        return True
    grams = {plain_wanted[i:i + 2] for i in range(len(plain_wanted) - 1)}
    return bool(grams) and sum(g in plain_text for g in grams) / len(grams) >= _LABEL_BIGRAM_COVERAGE


@dataclass(frozen=True)
class SourceIndex:
    """Source segments (sentences, lines, Markdown table rows) as (compact text, numbers)."""
    segments: tuple[tuple[str, frozenset[str]], ...]

    def grounds(self, label: str, value: str) -> bool:
        """The value's number and the chart label stand together in one source segment."""
        number = normalize_number(unicodedata.normalize("NFKC", value))
        wanted = _compact(label)
        return any(number in numbers and _label_in(wanted, text) for text, numbers in self.segments)


def source_index(source: str) -> SourceIndex:
    text = unicodedata.normalize("NFKC", source or "")
    segments: list[str] = []
    table: list[str] = []

    def flush():
        # One segment per table row, and per row together with the table's header row
        # (labels may sit in the header while the numbers sit in the row).
        for row in table:
            segments.append(row)
            if row is not table[0] and not re.fullmatch(r"[\s|:\-]*", row):
                segments.append(table[0] + " " + row)
        table.clear()

    for line in text.splitlines():
        if "|" in line:
            table.append(line)
            continue
        flush()
        segments.append(line)
        segments.extend(part for part in _SENTENCE_END.split(line) if part and part != line)
    flush()
    return SourceIndex(tuple((_compact(seg), frozenset(_numbers(seg))) for seg in segments if seg.strip()))


def source_numbers(source: str) -> set[str]:
    """Every number the source writes, normalised; units and separators are ignored."""
    return _numbers(source or "")


def _chart_points(line: str) -> list[tuple[str, str]]:
    """(label, value) of every point of every chart the line declares, as the requirement parser reads it."""
    points: list[tuple[str, str]] = []
    for chart in _charts(line):
        categories = chart.get("categories", [])
        for series in chart.get("series", [chart]):
            points.extend(zip(categories, (str(v) for v in series.get("values", [])), strict=False))
    return points


@dataclass(frozen=True)
class DroppedChartLine:
    slide_index: int
    line: str
    ungrounded_values: tuple[str, ...]
    reason: str = "chart_values_not_in_source"


def ground_chart_lines(content: str, index: SourceIndex, slide_index: int = 0) -> tuple[str, list[DroppedChartLine]]:
    kept, dropped = [], []
    for line in content.split("\n"):
        points = _chart_points(line) if _CHART_LINE.search(line) else []
        missing = tuple(f"{label} {value}" for label, value in points if not index.grounds(label, value))
        if missing:
            dropped.append(DroppedChartLine(slide_index, line.strip(), missing))
        else:
            kept.append(line)
    return ("\n".join(kept) if dropped else content), dropped


def ground_outline_charts(outline: PresentationOutlineModel, source: str) -> tuple[PresentationOutlineModel, list[DroppedChartLine]]:
    index = source_index(source)
    slides, dropped = [], []
    for position, slide in enumerate(outline.slides):
        content, lines = ground_chart_lines(slide.content, index, position)
        dropped.extend(lines)
        slides.append(slide.model_copy(update={"content": content}) if lines else slide)
    for item in dropped:
        LOGGER.warning("outline chart line dropped (%s): slide=%d values=%s line=%r",
                       item.reason, item.slide_index, list(item.ungrounded_values), item.line[:200])
    return (PresentationOutlineModel(slides=slides) if dropped else outline), dropped
