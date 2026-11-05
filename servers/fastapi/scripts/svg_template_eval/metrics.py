"""Pure metrics for the SVG template evaluation: what a generated page is made of, and how much
of the source content it kept. No I/O, so it is unit-tested (tests/unit/test_svg_template_eval_metrics.py).
"""
from __future__ import annotations

import re
import statistics
import xml.etree.ElementTree as ET
from collections import Counter

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
    """Share of the source's words and numbers that appear in the generated text."""
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


def template_text_leaks(template_text: str, generated: str, source: str) -> dict:
    """Template wording that reached a generated deck: counts only, never the words.

    ``ngrams`` counts word bigrams and ``figures`` numbers with their units that the template's own text
    and the generated deck share but the deck's source does not (so the source cannot explain them).
    Common phrases a deck may write on its own also count, so compare arms, not against zero.
    """
    source_bigrams, source_figures = _leak_bigrams(source), _leak_figures(source)
    return {"ngrams": len((_leak_bigrams(template_text) & _leak_bigrams(generated)) - source_bigrams),
            "figures": len((_leak_figures(template_text) & _leak_figures(generated)) - source_figures)}


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
    keys = sorted({key for run in runs for key, value in run.items()
                   if isinstance(value, (int, float)) and not isinstance(value, bool)})
    for key in keys:
        values = [run[key] for run in runs if isinstance(run.get(key), (int, float))
                  and not isinstance(run.get(key), bool)]
        summary[key] = {"mean": round(statistics.fmean(values), 2), "min": min(values), "max": max(values),
                        "n": len(values)}
    return summary
