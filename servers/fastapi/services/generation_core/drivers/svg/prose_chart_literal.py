"""Write a ``chart:`` literal for a numeric series a slide's prose already states.

``native_requirements.py`` turns only literal ``chart:`` lines into native chart
requirements, and the outline writes those only with the content structure policy on
(default off), so a default svg deck had no native chart even when a page said
"2023년 16.2만대, 2024년 14.7만대, 2025년 18.5만대". This module finds such a series in one
line of a slide's content and returns the ``chart:`` line the literal grammar reads,
built from the written text only:

* at least three ``label value unit`` items in one comma-separated run, every item in the
  same unit as written (a currency prefix counts as the unit);
* each label, value and unit is copied exactly; a ``(note)`` right after an item's unit,
  such as ``(전망)``, stays with its label (``2026년(전망)``); no label holds a comma;
* nothing is computed, converted or rounded. A series is refused when an item has no
  unit, when units or ratio measures differ, when a label names a current/target/before/
  after state, when a value is qualified (``약``, ``이상``, ``~``), a range, a compound amount
  (``1조 2,000억원``) or a calendar unit (a date is not a value), or when the first
  label cannot be told apart from the words before it.

The derivation runs once, at outline time (``services/outline_chart_grounding.py``), for
a deck whose frozen style snapshot pins ``svg-native-requirements/v3``; the derived line
is part of the pinned outline, so a resumed deck keeps exactly the lines it was authored
with. It never runs on a slide whose content already holds a chart literal.

Revision v4 additionally reads an explicit unit header and joins a series over
consecutive lines before validating it. An unreadable continuation refuses the
whole run; its earlier lines are never emitted as a partial chart.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from services.generation_core.drivers.svg.native_requirements import (
    REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4, _COMPOUND_AMOUNT_LABEL, _NUMBER,
    _UNIT_V3 as _UNIT, _split_outside_brackets,
    requested_native_data)

DERIVATION_ID = "svg-prose-chart-literal/v1"
MIN_ITEMS = 3

_SEPARATOR = r"(?:,|、)\s*(?!\d{3}(?:\D|$))"
_NOTE = r"\([^()]*\)"
#: One item: ``label value unit (note)`` with nothing after it.
_ITEM = re.compile(rf"(?P<label>\S(?:.*?\S)?)\s+(?P<prefix>[$€£₩¥]?)(?P<num>{_NUMBER})\s?"
                   rf"(?P<unit>{_UNIT})?(?P<note>\s?{_NOTE})?")
#: The last item of a run may be followed by the rest of the sentence.
_LAST_ITEM = re.compile(rf"(?P<label>\S(?:.*?\S)?)\s+(?P<prefix>[$€£₩¥]?)(?P<num>{_NUMBER})\s?"
                        rf"(?P<unit>{_UNIT})?(?P<note>\s?{_NOTE})?(?P<tail>.*)")
#: Korean particles and endings that may follow a unit at the end of a run.
_PARTICLE = re.compile(r"(?:으로|로|에서|에|까지|를|을|이|가|은|는|와|과|의|씩|였|이었|이다|입니다|이며|으며|며|고)")
#: Words after a value that make it inexact or a range.
_QUALIFIER_TAIL = re.compile(r"\s*(?:이상|이하|미만|초과|내외|안팎|가량|정도|남짓|수준|이내|대(?![가-힣])|선(?![가-힣])|[~〜∼\-–—]|to\b)")
#: Words inside a label that make its value inexact.
_QUALIFIER_LABEL = re.compile(r"(?:^|\s)(?:약|대략|최대|최소|최고|최저|평균|about|approx(?:\.|imately)?|around|~)(?:\s|$)", re.I)
#: A label that names a state to compare against another measure (current versus target).
_STATE_LABEL = re.compile(r"목표|현재|기존|개선\s*[전후]|도입\s*[전후]|이전|이후"
                          r"|\b(?:as-?is|to-?be|target|goal|current|before|after|baseline)\b", re.I)
#: Calendar units: a value with one of these is a date, not a quantity.
_CALENDAR_UNIT = re.compile(r"(?:년|년도|월|일|시|분기|주차|차년도|학년|호)")
#: A measure word ending a label (``성장률``, ``비중``, ``영업이익``): labels naming two different
#: measures (``매출 120억원, 영업이익 30억원, ...``) are not one series.
_MEASURE = re.compile(r"(\S*(?:률|율|비중|비율|매출|매출액|이익|비용|원가|예산|자산|부채|판매량|생산량|수출액|수입액|투자액)"
                      r"|\b(?:revenue|profit|cost|sales|margin|income|share|rate))$", re.I)
#: Words before a first label that end a preamble (``점유율은 현대 38%``, ``판매량: ...``).
_PREAMBLE_END = re.compile(r"(?:[:：]|[은는이가]|[-*•·]|\d+[.)])$")


@dataclass(frozen=True)
class DerivedChart:
    line_index: int
    literal: str
    categories: tuple[str, ...]
    values: tuple[str, ...]
    unit: str


def _shape(label: str) -> str:
    return re.sub(r"\d", "9", label)


def _items(segments: list[str]) -> list[dict] | None:
    """Read a run of segments as items; the first may carry a preamble, the last a tail."""
    middle = []
    for segment in segments[1:-1]:
        match = _ITEM.fullmatch(segment.strip())
        if not match:
            return None
        middle.append(match.groupdict())
    last = _LAST_ITEM.fullmatch(segments[-1].strip())
    if not last:
        return None
    last = last.groupdict()
    first_text = segments[0].strip()
    tokens = first_text.split()
    others = [*middle, last]
    widths = {len(item["label"].split()) for item in others}
    if len(widths) != 1:
        return None
    width = widths.pop()
    if len(tokens) < width + 1:
        return None
    head, candidate = tokens[:-(width + 1)], " ".join(tokens[-(width + 1):])
    first = _ITEM.fullmatch(candidate)
    if not first:
        return None
    first = first.groupdict()
    if head:
        # The words before the first label must end visibly (a colon, a topic particle or a
        # bullet), unless every label has the same digit shape (2023년, 2024년, ...).
        shapes = {_shape(item["label"]) for item in others}
        if not (_PREAMBLE_END.search(head[-1]) or (len(shapes) == 1 and re.search(r"\d", first["label"])
                                                  and _shape(first["label"]) in shapes)):
            return None
    return [first, *middle, last]


def _resolve_last_unit(last: dict, unit: str) -> bool:
    """Separate a particle the unit pattern swallowed (``18.5만대로``) and check the tail."""
    written = last["unit"] or ""
    tail = last["tail"] or ""
    if written != unit:
        if last["note"] or not unit or not written.startswith(unit) or not unit[-1:].isalpha():
            return False
        tail = written[len(unit):] + tail
        last["unit"] = unit
    if not tail.strip() or re.fullmatch(r"\s*[.。!?]\s*", tail):
        return True
    if _QUALIFIER_TAIL.match(tail):
        return False
    return bool(_PARTICLE.match(tail) or tail[:1].isspace() or tail[:1] in ".。")


def _series(items: list[dict]) -> tuple[list[str], list[str], str] | None:
    units = {(item["prefix"] or "") + (item["unit"] or "") for item in items[:-1]}
    if len(units) != 1:
        return None
    unit = units.pop()
    if not unit:
        return None
    last = items[-1]
    if last["prefix"] != items[0]["prefix"]:
        return None
    if not _resolve_last_unit(last, (unit[len(last["prefix"] or ""):])):
        return None
    if (last["prefix"] or "") + (last["unit"] or "") != unit:
        return None
    if _CALENDAR_UNIT.fullmatch(unicodedata.normalize("NFKC", items[0]["unit"] or "")):
        return None
    labels, values, measures = [], [], set()
    for item in items:
        label = item["label"]
        if (_QUALIFIER_LABEL.search(label) or _STATE_LABEL.search(label) or _COMPOUND_AMOUNT_LABEL.search(label)
                or re.search(r"[,;:：|]", label) or re.fullmatch(rf"{_NUMBER}", label) or len(label) > 30):
            return None
        measure = _MEASURE.search(label.split()[-1])
        if measure:
            measures.add(measure[0].casefold())
        labels.append(label + (item["note"] or "").strip())
        values.append(item["num"])
    if len(measures) > 1 or len(set(labels)) != len(labels):
        return None
    return labels, values, unit


#: A segment that starts like an item, or ends like one: a run next to it would be a subset.
_ITEM_START = re.compile(rf"\s*\S+\s+[$€£₩¥]?{_NUMBER}")
_ITEM_END = re.compile(rf"{_NUMBER}\S*(?:\s+\S+)?\s*$")


def _line_series(line: str) -> list[tuple[list[str], list[str], str]]:
    segments = _split_outside_brackets(line, _SEPARATOR)
    found, start = [], 0
    while start < len(segments):
        best = None
        for end in range(len(segments), start + MIN_ITEMS - 1, -1):
            # Never chart part of a series: a neighbouring item the run cannot read
            # (a range, a qualified value) refuses the run instead of being left out.
            if ((end < len(segments) and _ITEM_START.match(segments[end]))
                    or (start > 0 and _ITEM_END.search(segments[start - 1]))):
                continue
            items = _items(segments[start:end])
            series = _series(items) if items else None
            if series:
                best = (end, series)
                break
        if best:
            found.append(best[1])
            start = best[0]
        else:
            start += 1
    return found


def _literal(labels: list[str], values: list[str], unit: str) -> str:
    prefix = unit[:1] if unit[:1] in "$€£₩¥" else ""
    suffix = unit[len(prefix):]
    return "chart: " + ", ".join(f"{label} {prefix}{value}{suffix}" for label, value in zip(labels, values, strict=True))


# A bare parenthesis must name a recognisable unit, rather than a note such as
# (전망). An explicit 단위/unit marker can also name other literal-grammar units.
_HEADER_UNIT = re.compile(
    r"(?:[$€£₩¥]|[%％](?:p|포인트)?|(?:백|천|만|억|조)*(?:원|달러|명|대|건|개|회|배|시간|분|초|톤)"
    r"|USD|KRW|EUR|JPY|million|billion|thousand|people|units|kg|g|t|km|m|cm|mm|m²|m³|ha)", re.I)
_UNIT_MARKER = re.compile(r"(?:단위|\bunit)\s*[:：]?\s*(.+)", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•·]\s+|\d+[.)]\s+)")


def _header_unit(text: str, *, explicit: bool = False) -> str | None:
    marker = _UNIT_MARKER.fullmatch(text.strip())
    if marker:
        text, explicit = marker[1], True
    # Whitespace in a unit header (억 원) has no numerical meaning. Copy its
    # contiguous spelling, which is how the literal grammar spells that unit.
    unit = "".join(text.split())
    if (not re.fullmatch(rf"[$€£₩¥]?(?:{_UNIT})?", unit) or not unit
            or _CALENDAR_UNIT.fullmatch(unit)):
        return None
    return unit if explicit or _HEADER_UNIT.fullmatch(unit) else None


def _header_context(line: str) -> tuple[str, str | None, bool]:
    """Return data after a label header, its unit, and whether this is a header.

    Context is confined to a heading/standalone header or a label before a
    colon. Parentheses inside a data item's label never supply a unit.
    """
    text = _BULLET.sub("", line.strip())
    heading = bool(re.match(r"^#{1,6}\s+", text))
    text = re.sub(r"^#{1,6}\s+", "", text)
    split = _split_outside_brackets(text, r"[:：]")
    # A unit marker's own colon is part of its header, not the data boundary.
    standalone_marker = _UNIT_MARKER.fullmatch(text)
    if standalone_marker:
        unit = _header_unit(text)
        if unit:
            return "", unit, True
    if len(split) == 2:
        head, data = split
    else:
        head, data = text, ""
        leading_header = re.fullmatch(r"([^,]*?\([^()]+\))\s+(.+)", text)
        if leading_header and not _data_line(leading_header[1]) and _data_line(leading_header[2]):
            head, data = leading_header.groups()
        elif _data_line(text):
            return text, None, False
    candidates = [_header_unit(match[1]) for match in re.finditer(r"\(([^()]*)\)", head)]
    units = {unit for unit in candidates if unit}
    unit = next(iter(units)) if len(units) == 1 else None
    is_header = heading or bool(units) or text.endswith((":", "："))
    if data and is_header:
        return data.strip(), unit, True
    if is_header and not data:
        return "", unit, True
    return text, None, False


def _data_line(line: str) -> bool:
    """Include malformed neighbouring values so they cannot leave a valid subset."""
    return bool("|" not in line and re.search(
        r"\S+\s+(?:[$€£₩¥]?[-+]?\d|(?:약|대략|최대|최소|최고|최저|평균|about|approx(?:\.|imately)?|around|~)\s*\d)",
        line, re.I))


def _context_series(line: str, unit: str | None) -> tuple[list[str], list[str], str] | None:
    segments = _split_outside_brackets(line, _SEPARATOR)
    if len(segments) < MIN_ITEMS:
        return None
    items = _items(segments)
    if not items:
        return None
    if unit:
        prefix = unit[:1] if unit[:1] in "$€£₩¥" else ""
        suffix = unit[len(prefix):]
        for position, item in enumerate(items):
            written = (item["prefix"] or "") + (item["unit"] or "")
            # A sentence particle following a bare last value is prose, not a
            # conflicting unit. _series still checks its qualifier/range tail.
            if (position == len(items) - 1 and written != unit and not item["prefix"] and item["unit"]
                    and _PARTICLE.fullmatch(item["unit"]) and not item["note"]):
                item["tail"] = item["unit"] + (item["tail"] or "")
                item["unit"], written = None, ""
            if not written:
                item["prefix"], item["unit"] = prefix, suffix
            elif written != unit:
                # Keep the existing parser's treatment of a unit plus a final
                # Korean particle, while refusing every actual unit mismatch.
                if not (position == len(items) - 1 and item["prefix"] == prefix
                        and _resolve_last_unit(item, suffix)):
                    return None
    return _series(items)


def _v4_series(content: str):
    """Join an entire consecutive data run before attempting any derivation."""
    lines = content.split("\n")
    context, position = None, 0
    while position < len(lines):
        data, header_unit, header = _header_context(lines[position])
        if header:
            context = header_unit
        if not _data_line(data):
            if data.strip():
                context = None
            position += 1
            continue
        parts, end = [data.rstrip().rstrip(",、")], position
        while end + 1 < len(lines):
            following, _, next_header = _header_context(lines[end + 1])
            if next_header or not _data_line(following):
                break
            parts.append(following.rstrip().rstrip(",、"))
            end += 1
        series = _context_series(", ".join(parts), context)
        if series:
            yield end, series
        position = end + 1


def derive_chart_literals(content: str, *, revision: str = REQUIREMENTS_VERSION_V3) -> list[DerivedChart]:
    """The chart literals a slide's prose states; none when it already holds a chart literal."""
    if revision not in (REQUIREMENTS_VERSION_V3, REQUIREMENTS_VERSION_V4) or not isinstance(content, str):
        return []
    if requested_native_data(content, revision).get("charts"):
        return []
    derived = []
    if revision == REQUIREMENTS_VERSION_V4:
        candidates = _v4_series(content)
    else:
        candidates = ((index, series) for index, line in enumerate(content.split("\n"))
                      if "|" not in line and re.search(r"\d", line) for series in _line_series(line))
    for index, (labels, values, unit) in candidates:
        literal = _literal(labels, values, unit)
        # The line must read back as exactly this series, unit and all.
        charts = requested_native_data(literal, revision).get("charts", [])
        if (len(charts) != 1 or charts[0].get("unsupportedReason")
                or charts[0]["categories"] != labels
                or charts[0]["values"] != [value.replace(",", "") for value in values]
                or set(charts[0].get("units", [])) != {unit}):
            continue
        derived.append(DerivedChart(index, literal, tuple(labels), tuple(values), unit))
    return derived


def with_chart_literals(content: str, *, revision: str = REQUIREMENTS_VERSION_V3) -> tuple[str, list[DerivedChart]]:
    """``content`` with each derived literal on its own line right after its source line."""
    derived = derive_chart_literals(content, revision=revision)
    if not derived:
        return content, []
    lines = content.split("\n")
    by_line: dict[int, list[str]] = {}
    for item in derived:
        by_line.setdefault(item.line_index, []).append(item.literal)
    out = []
    for index, line in enumerate(lines):
        out.append(line)
        out.extend(by_line.get(index, []))
    return "\n".join(out), derived
