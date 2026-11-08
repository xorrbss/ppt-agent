"""Pure metrics for the SVG template evaluation: what a generated page is made of, and how much
of the source content it kept. No I/O, so it is unit-tested (tests/unit/test_svg_template_eval_metrics.py).
"""
from __future__ import annotations

import re
import math
import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal, InvalidOperation

#: Shapes that draw diagrams (arrowheads, chevrons, connectors). Lines are also used for table rules
#: and dividers, so ``polygon`` is the closest single signal for an arrowhead or a chevron.
FLOW_TAGS = ("polygon", "path", "line", "polyline")
_ARROW_GLYPHS = "→⇒➜➔▶▷►›»"
_WORD = re.compile(r"[가-힣A-Za-z]{2,}")
_NUMBER = re.compile(r"\d[\d,.]*\d|\d")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def svg_structure(svg: str) -> dict:
    """Counts of what the page's own body drew.

    Template frame furniture (``svg-system-template-*`` except the body group) is skipped, and an
    embedded icon or picture slot (``body-asset-*``) counts once as an icon instead of being
    counted by the strokes it is made of. Returns counts plus the body's texts.
    """
    root = ET.fromstring(svg)
    counts: Counter = Counter()
    texts: list[str] = []

    def visit(node) -> None:
        tag, node_id = _local(node.tag), node.get("id", "")
        if tag == "g" and node_id.startswith("svg-system-template") and node_id != "svg-system-template-body":
            return
        if tag == "g" and node_id.startswith("body-asset-"):
            counts["icons"] += 1
            return
        if tag == "image":
            counts["pictures"] += 1
            return
        if tag in FLOW_TAGS or tag == "rect":
            counts[tag] += 1
        elif tag == "text":
            counts["text"] += 1
            texts.append("".join(node.itertext()))
            return
        for child in node:
            visit(child)

    visit(root)
    arrows = sum(text.count(glyph) for text in texts for glyph in _ARROW_GLYPHS)
    return {"polygon": counts["polygon"], "path": counts["path"], "line": counts["line"],
            "polyline": counts["polyline"], "rect": counts["rect"], "text": counts["text"],
            "icons": counts["icons"], "pictures": counts["pictures"], "arrow_glyphs": arrows,
            "flow_shapes": sum(counts[tag] for tag in FLOW_TAGS), "texts": texts}


def retention(source: str, generated: str) -> dict:
    """Descriptive token overlap; numeric presence does not establish data truthfulness."""
    words, found_words = set(_WORD.findall(source)), set(_WORD.findall(generated))
    numbers, found_numbers = set(_NUMBER.findall(source)), set(_NUMBER.findall(generated))
    return {"words": len(words & found_words) / len(words) if words else None,
            "numbers": len(numbers & found_numbers) / len(numbers) if numbers else None}


_LEAK_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
_LEAK_FIGURE = re.compile(r"\d[\d,.]*\s?(?:%|[가-힣]|[A-Za-z]{1,3})?")
#: One-syllable particles a Korean word carries ("인력과", "인력은" are the same word for this metric).
_PARTICLES = frozenset("은는이가을를의에와과도로")


def _stem(word: str) -> str:
    return word[:-1] if len(word) >= 3 and word[-1] in _PARTICLES else word.casefold()


def _leak_bigrams(text: str) -> set[tuple[str, str]]:
    words = [_stem(word) for word in _LEAK_WORD.findall(text or "")]
    return set(zip(words, words[1:], strict=False))


def _leak_figures(text: str) -> set[str]:
    # A figure is a number of two or more digits with its unit, spaces removed ("194명", "33%", "2.5h").
    return {figure.replace(" ", "") for figure in _LEAK_FIGURE.findall(text or "")
            if sum(char.isdigit() for char in figure) >= 2}


def template_text_leaks(template_text: str, generated: str, source: str,
                        common_phrase_text: str | None = None) -> dict:
    """Template wording that reached a generated deck: counts only, never the words.

    ``ngrams`` counts word bigrams and ``figures`` numbers with their units that the template's own text
    and the generated deck share but the deck's source does not (so the source cannot explain them).
    Common phrases a deck may write on its own also count, so compare arms, not against zero.
    """
    source_bigrams, source_figures = _leak_bigrams(source), _leak_figures(source)
    candidates = (_leak_bigrams(template_text) & _leak_bigrams(generated)) - source_bigrams
    result = {"ngrams": len(candidates),
              "figures": len((_leak_figures(template_text) & _leak_figures(generated)) - source_figures)}
    if common_phrase_text is not None:
        common = candidates & _leak_bigrams(common_phrase_text)
        result.update(specific_ngrams=len(candidates - common), common_phrase_ngrams=len(common))
    # A zero count only means this bounded wording heuristic found no candidates.
    return result


_CHART_FRAME = re.compile(r"""graphicData\s+uri=["']http://schemas\.openxmlformats\.org/drawingml/2006/chart["']""")


def package_objects(pptx_parts: dict[str, str]) -> dict:
    """Native tables and charts in an exported PPTX, from its slide XML parts (name -> text)."""
    slides = [text for name, text in pptx_parts.items() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
    return {"tables": sum(text.count("<a:tbl>") for text in slides),
            # One graphicData per chart frame; its c:chart child repeats the namespace (counted twice before).
            "charts": sum(len(_CHART_FRAME.findall(text)) for text in slides),
            "pages_with_table": sum("<a:tbl>" in text for text in slides)}


def summarize(runs: list[dict]) -> dict:
    """mean / min / max of every numeric field the runs share (booleans and strings are skipped)."""
    summary: dict[str, dict] = {}
    keys = sorted({key for run in runs for key, value in run.items() if _finite_number(value)})
    for key in keys:
        values = [run[key] for run in runs if _finite_number(run.get(key))]
        summary[key] = {"mean": round(statistics.fmean(values), 2), "min": min(values), "max": max(values),
                        "n": len(values)}
    return summary


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalized_page_metrics(record: dict) -> dict:
    """Page rates from actual recorded pages, never an assumed ten-page deck.

    Missing/invalid counts stay unavailable; they are not zero observations. Counts remain
    descriptive and are retained on records by the caller.
    """
    pages = record.get("pages")
    if not _finite_number(pages) or pages <= 0 or int(pages) != pages:
        return {}
    result = {}
    for count_key, rate_key in (("first_try_ok", "first_try_ok_rate"), ("fallback_pages", "fallback_rate")):
        count = record.get(count_key)
        if _finite_number(count) and 0 <= count <= pages and int(count) == count:
            result[rate_key] = count / pages
    return result


def attempt_accounting(runs: list[dict], requested_runs: int | None = None) -> dict:
    """Describe all recorded attempts separately from completed-deck metric availability.

    ``retry_of`` links an authorized retry to its retained earlier attempt. Missing records
    cannot be reconstructed from completed decks, so requested slots are reported separately.
    """
    completed = [run for run in runs if run.get("completed") is True]
    failed = sum(run.get("completed") is False for run in runs)
    total = len(runs)
    result = {"attempted_runs": total, "completed_runs": len(completed), "failed_runs": failed,
              "unknown_outcome_runs": total - len(completed) - failed,
              "retry_attempts": sum(run.get("retry_of") is not None for run in runs),
              "completion_rate": len(completed) / total if total else None,
              "failure_rate": failed / total if total else None}
    if requested_runs is not None:
        result["requested_runs"] = requested_runs
    for count_key, rate_key in (("first_try_ok", "first_try_ok_rate"), ("fallback_pages", "fallback_rate")):
        available = [run for run in completed if rate_key in normalized_page_metrics(run)]
        denominator = sum(run["pages"] for run in available)
        numerator = sum(run[count_key] for run in available)
        result[rate_key] = {"value": numerator / denominator if denominator else None,
                            "numerator": numerator, "pages": denominator,
                            "available_runs": len(available), "missing_runs": len(completed) - len(available)}
    return result


def structured_data_integrity(expected: list[dict] | None, observed: list[dict] | None,
                              max_rows: int = 1000) -> dict:
    """Compare supplied category/value/unit/series associations, including duplicate rows.

    This does not extract facts from prose or prove drawing geometry. Missing structured
    evidence is unavailable, even when descriptive numeric-token retention is perfect.
    """
    base = {"scope": "supplied_structured_evidence_only"}
    if expected is None or observed is None:
        return {**base, "status": "unavailable", "reason": "missing structured evidence"}
    if (not isinstance(expected, list) or not isinstance(observed, list)
            or not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1
            or len(expected) > max_rows or len(observed) > max_rows):
        return {**base, "status": "unavailable", "reason": "invalid or oversized structured evidence"}
    if not expected and not observed:
        return {**base, "status": "unavailable", "reason": "no structured rows supplied"}

    def rows(items):
        normalized = []
        for row in items:
            if not isinstance(row, dict) or not all(key in row for key in ("category", "value", "unit", "series")):
                raise ValueError
            if not all(isinstance(row[key], str) and len(row[key]) <= 1000 for key in ("category", "unit", "series")):
                raise ValueError
            value = row["value"]
            if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 100:
                raise ValueError
            number = Decimal(str(value))
            if not number.is_finite():
                raise ValueError
            normalized.append((row["category"].strip(), number, row["unit"].strip(), row["series"].strip()))
        return Counter(normalized)

    try:
        wanted, found = rows(expected), rows(observed)
    except (ValueError, InvalidOperation):
        return {**base, "status": "unavailable", "reason": "invalid structured row"}
    matched = sum((wanted & found).values())
    return {**base, "status": "matched" if wanted == found else "mismatch", "expected_rows": len(expected),
            "observed_rows": len(observed), "matched_rows": matched,
            "missing_rows": sum((wanted - found).values()), "unexpected_rows": sum((found - wanted).values())}
