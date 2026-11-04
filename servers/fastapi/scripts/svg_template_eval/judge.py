#!/usr/bin/env python3
"""Paired vision judge: does a generated page look like the template's own pages?

The counts in ``metrics.py`` say what a page drew, not whether it reads like the template. This asks a vision
model to compare each generated page with pages of the original template deck. The model first counts what the
page has (component kinds, sections, text items, empty area, template components, defects), then scores five
axes from 1 to 5 against two kinds of full-size anchors:

    score 5 anchor  one or two original pages of the template deck (the operator picks them)
    score 1 anchor  a fallback page: the template frame with plain bullet text (``low-anchor`` builds one)

    structure  body composition: distinct component kinds and sections against one component or plain text
    density    amount of content and empty area
    hierarchy  title / key message / numbered sub-heading / body levels
    tone       the template's manner in the body (type size, colours, rules), not its copied frame
    visuals    icons, figures, charts, diagram shapes

``overall`` is the weighted mean in ``WEIGHTS`` (equal weights, chosen in calibration). Scores of judge version 1
(no anchors, plain mean, results without ``judge_version``) are not comparable with these; see ``JUDGE_VERSION``.

Reference and anchor pages are PNGs the operator renders from the template deck at run time (``references``
below, or any renderer). They are never stored in the repository. Every PNG of the references directory is also
sent once as context (one contact sheet, or one by one when there are at most three; ``--no-context`` drops it).
A score is a model's opinion and varies between calls, so judge a change on three or more decks.

    judge.py references --pdf template.pdf --pages 4,9,19,25 --out refs/
    judge.py low-anchor --frame refs/ref-045.png --font Pretendard-Regular.ttf --out anchors/low.png
    judge.py deck --references refs/ --anchor-high ref-009.png,ref-066.png --anchor-low anchors/low.png \
        --presentation-id <id> [--presentation-id <id> ...] --label base --out results/
    judge.py pages --references refs/ --anchor-high ... --anchor-low ... --generated pngs/ --out page-scores.json

``deck`` reads ``slides.svg_content`` from the database and renders each page with Playwright (Node). Its
output has the shape of a ``run_eval.py run`` result, so ``run_eval.py compare`` reads it. ``run_eval.py run
--judge-references refs/ --judge-anchor-high ... --judge-anchor-low ...`` judges every generated deck in the same
way. The API key is read from ``PRESENTON_ANTHROPIC_API_KEY``. Pure parts are unit-tested
(tests/unit/test_svg_template_eval_judge.py).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import summarize  # noqa: E402

#: Bumped whenever the prompt, anchors, rubric or the overall combination change the meaning of a score.
#: 1: no anchors, five axes scored against a contact sheet, overall = plain mean (results carry no version).
#: 2: full-size score-5 and score-1 anchors, counted evidence before the scores, weighted overall.
JUDGE_VERSION = 2
AXES = ("structure", "density", "hierarchy", "tone", "visuals")
#: overall = sum(weight * axis score) / sum(weights). Equal weights: weighting structure and density more
#: (0.3/0.3/0.15/0.1/0.15) was tried in calibration and followed the human scores less closely (README).
WEIGHTS = {"structure": 0.2, "density": 0.2, "hierarchy": 0.2, "tone": 0.2, "visuals": 0.2}
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "low"
API_KEY_ENV = "PRESENTON_ANTHROPIC_API_KEY"
DEFAULT_DSN = "postgresql://postgres@127.0.0.1:5433/chap"
#: At most this many context references are sent one by one; more go into one contact sheet.
MAX_INDIVIDUAL_REFERENCES = 3
SHEET_WIDTH = 1568
#: USD per million tokens: input, output, cache read, cache write. A model not listed gets no cost estimate.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

SYSTEM_PROMPT = """You review slide design strictly. A presentation was generated from a registered template \
deck. You see pages of that template deck, then one generated page. Judge how close the generated page comes to \
the template's own pages. Do not judge the content, its language or its correctness, only how the page is built.

Two kinds of anchor pages fix the scale:
- a SCORE 5 ANCHOR is an original page of the template deck: it scores 5 on every axis. Original pages \
typically combine a key-message bar under the header, two to four numbered sub-headings ("01 ...") each with a \
DIFFERENT component (table, flow or process diagram, org chart, chart, KPI cards, step chevrons), forty to \
eighty separate text items in small type, and almost no empty space.
- a SCORE 1 ANCHOR is a fallback page: the template frame with plain bullet text. It scores 1 on structure, \
density and visuals, and at most 2 on hierarchy and tone.
A page between the two scores between them. A page that is clean and tidy but simpler, emptier or larger-type \
than the score 5 anchors does NOT score 4 or 5: those are for pages that could be mistaken for an original page.

The header band and footer (chapter tab, breadcrumb, title band, logos, page number) are copied from the template \
onto every generated page. Give no credit for them: judge the body below the header.

A page label may give its measured blank share: the percent of the body area (below the header band, above the \
footer) made of small tiles of one flat colour, i.e. white space and blank interiors of cards and boxes. Compare \
the generated page's measure with the anchors' measures when you judge empty_share and density.

First count, then score. Fill "evidence" by looking at the body of the generated page:
- component_kinds: the distinct KINDS of body components (e.g. table, flow diagram, org chart, bar chart, KPI \
cards, card grid, step chevrons, timeline, icon list, callout box, plain bullets). Five cards of one design are ONE \
kind.
- sections: the body sections, i.e. numbered or titled sub-headings that each own a block (0 if none).
- key_message_bar: whether a key-message bar of the template's kind sits under the header.
- text_items: approximate count of separate text runs in the body (cell texts, labels, bullets, card lines).
- empty_share: approximate percent of the body area that carries no content: white space AND the blank \
interiors of cards, boxes and table cells (a tall card whose text fills its top third is two-thirds empty).
- template_components: components of the template deck the page reuses (key-message bar with flag icon, "01" \
numbered sub-headings, step chevrons, navy-header tables, ...).
- defects: errors a reader would notice: raw markup or Markdown table syntax shown as text, code-like labels \
(e.g. "chart:"), arrows pointing against the order of the sequence they connect or dangling arrows, overlapping \
or cut-off text, broken layout. Not stylistic preferences. Empty list when none.

Then score each axis from 1 to 5, its reason citing the counts:
- structure: 5 = like the score 5 anchors: two or more sections built with three or more distinct component kinds, \
with the template's key-message bar and numbered sub-headings; 4 = two sections or 2-3 distinct kinds with the \
template's furniture; 3 = two distinct kinds, or one rich component (a real table, a multi-lane diagram); \
2 = one component kind (a row or grid of cards, a list of boxes), however often it repeats; 1 = plain bullets or \
paragraphs, or raw markup.
- density: 5 = 40 or more text items in small type, empty share 10% or less; 4 = about 30-40 items, empty 20% or \
less; 3 = about 20-30 items, or empty 20-35%; 2 = about 10-20 items, large type, or empty 35-50%; 1 = under 10 \
items, or more than half of the body empty. A measured blank share well above the score 5 anchors' measures keeps \
density at 3 or below; one close to the score 1 anchor's keeps it at 2 or below.
- hierarchy: 5 = the levels of the score 5 anchors (title, key-message bar, numbered sub-headings, component \
headers, body, notes); 3 = title, lead line and body; 2 = title and body only; 1 = no readable levels.
- tone: the template's manner in the body. 5 = type sizes, colours, rules and table styles of the score 5 anchors; \
3 = right colours but visibly larger type, bigger plain boxes or another manner; 1 = a different design.
- visuals: 5 = icons, figures, charts and diagram shapes of the kind and amount of the score 5 anchors; 3 = a few \
generic icons or shapes; 2 = one repeated icon or shape kind; 1 = none.
Every listed defect lowers structure to at most 2 and costs tone one point. Answer only with the JSON object \
asked for."""

INSTRUCTION = ("Count the evidence on the generated page above, then score structure, density, hierarchy, tone and "
               "visuals (1 to 5 each, with a reason citing the counts) against the score 5 and score 1 anchors.")

_AXIS_SCHEMA = {"type": "object",
                "properties": {"reason": {"type": "string"},
                               "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]}},
                "required": ["reason", "score"], "additionalProperties": False}
EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {"component_kinds": {"type": "array", "items": {"type": "string"}},
                   "sections": {"type": "integer"},
                   "key_message_bar": {"type": "boolean"},
                   "text_items": {"type": "integer"},
                   "empty_share": {"type": "integer"},
                   "template_components": {"type": "array", "items": {"type": "string"}},
                   "defects": {"type": "array", "items": {"type": "string"}}},
    "required": ["component_kinds", "sections", "key_message_bar", "text_items", "empty_share",
                 "template_components", "defects"],
    "additionalProperties": False,
}
#: The evidence comes first, so the model counts before it scores.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"evidence": EVIDENCE_SCHEMA, **{axis: _AXIS_SCHEMA for axis in AXES}},
    "required": ["evidence", *AXES],
    "additionalProperties": False,
}


# ---- reference choice and prompt assembly (pure) --------------------------------------------------------

def select_references(available: list[str], chosen: list[str] | None = None,
                      limit: int = MAX_INDIVIDUAL_REFERENCES) -> dict:
    """Which context references every page sees, and how they are sent.

    ``available`` are the reference file names (sorted here). The named ``chosen`` ones win and are sent
    one by one; otherwise up to ``limit`` are sent one by one, and more are combined into one contact sheet.
    """
    names = sorted(available)
    if chosen:
        missing = [name for name in chosen if name not in names]
        if missing:
            raise ValueError(f"reference pages not found: {', '.join(missing)}")
        return {"mode": "individual", "pages": list(chosen)}
    if not names:
        raise ValueError("no reference pages")
    return {"mode": "individual" if len(names) <= limit else "sheet", "pages": names}


def resolve_anchor(value: str, references_dir: Path) -> Path:
    """An anchor given as a path, or as a file name inside the references directory."""
    for path in (Path(value), Path(references_dir) / value):
        if path.is_file():
            return path
    raise ValueError(f"anchor page not found: {value}")


def sheet_grid(count: int) -> tuple[int, int]:
    """(columns, rows) of a contact sheet of ``count`` pages: three columns up to nine pages, then four."""
    columns = min(count, 3 if count <= 9 else 4)
    return columns, math.ceil(count / columns)


def _image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(png).decode("ascii")}}


def build_request(generated: bytes, high: list[tuple[str, bytes]], low: list[tuple[str, bytes]],
                  context: list[tuple[str, bytes]] | None = None, sheet: bool = False,
                  generated_blank: float | None = None) -> dict:
    """System prompt and user content for one page.

    ``high`` and ``low`` are the score 5 and score 1 anchors and ``context`` the optional context references,
    each a list of (label, PNG bytes); a contact sheet is one entry labelled with its page count. All of them
    come first and are the same for every page, so the prefix up to the last anchor is marked for caching.
    ``generated_blank`` is the generated page's measured ``blank_share`` (the anchors carry theirs in the label).
    """
    if not high or not low:
        raise ValueError("at least one score 5 anchor and one score 1 anchor are needed")
    content: list[dict] = []
    if context:
        if sheet:
            content.append({"type": "text", "text": f"Context: contact sheet of {context[0][0]} pages of the "
                                                     "template deck, to show the range of its components."})
            content.append(_image_block(context[0][1]))
        else:
            for number, (label, png) in enumerate(context, start=1):
                content.append({"type": "text", "text": f"Context page {number} of the template deck ({label}):"})
                content.append(_image_block(png))
    for number, (label, png) in enumerate(high, start=1):
        content.append({"type": "text", "text": f"SCORE 5 ANCHOR {number} ({label}): an original page of the "
                                                 "template deck. Every axis scores 5 here."})
        content.append(_image_block(png))
    for number, (label, png) in enumerate(low, start=1):
        content.append({"type": "text", "text": f"SCORE 1 ANCHOR {number} ({label}): a fallback page, the "
                                                 "template frame with plain bullet text. Structure, density and "
                                                 "visuals score 1 here."})
        content.append(_image_block(png))
    content[-1]["cache_control"] = {"type": "ephemeral"}
    measured = f" (measured blank share {round(generated_blank * 100)}%)" if generated_blank is not None else ""
    content.append({"type": "text", "text": f"Generated page to score{measured}:"})
    content.append(_image_block(generated))
    content.append({"type": "text", "text": INSTRUCTION})
    return {"system": SYSTEM_PROMPT, "messages": [{"role": "user", "content": content}]}


# ---- response parsing (pure) ----------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _json_objects(text: str):
    """Every balanced ``{...}`` span in ``text``, outermost first, as parsed objects where they parse."""
    for start in [index for index, char in enumerate(text) if char == "{"]:
        depth, in_string, escape = 0, False, False
        for end in range(start, len(text)):
            char = text[end]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:end + 1]
                    for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
                        try:
                            value = json.loads(attempt)
                        except ValueError:
                            continue
                        if isinstance(value, dict):
                            yield value
                        break
                    break


def _score(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        value = float(match.group()) if match else None
    if isinstance(value, (int, float)) and 1 <= value <= 5:
        return float(value)
    return None


def _axis_entry(data: dict, axis: str) -> tuple[float | None, str]:
    """Score and reason of ``axis`` in any of the shapes a model tends to answer with."""
    for container in (data, data.get("scores") if isinstance(data.get("scores"), dict) else None):
        if not container:
            continue
        for key, value in container.items():
            if str(key).strip().lower() != axis:
                continue
            if isinstance(value, dict):
                return _score(value.get("score")), str(value.get("reason") or value.get("evidence") or "")
            reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
            return _score(value), str(reasons.get(axis) or data.get(f"{axis}_reason") or "")
    return None, ""


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        return int(match.group()) if match else None
    return int(round(value)) if isinstance(value, (int, float)) else None


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(item) for item in value] if isinstance(value, list) else []


def normalize_evidence(data) -> dict | None:
    """The counted evidence in a fixed shape (counts as int or None, lists of strings), or None when absent."""
    if not isinstance(data, dict):
        return None
    bar = data.get("key_message_bar")
    return {"component_kinds": _strings(data.get("component_kinds")), "sections": _int(data.get("sections")),
            "key_message_bar": bar if isinstance(bar, bool) else None,
            "text_items": _int(data.get("text_items")), "empty_share": _int(data.get("empty_share")),
            "template_components": _strings(data.get("template_components")),
            "defects": _strings(data.get("defects"))}


def parse_judgement(text: str) -> dict:
    """Scores, reasons and evidence from a model answer: strict JSON, fenced JSON, JSON inside prose, or plain
    ``axis: 4`` lines as a last resort. An axis without a score in 1..5 is None; ``valid`` means all five.
    ``evidence`` is None when the answer has none (it never decides ``valid``)."""
    candidates = [match.group(1) for match in _FENCE.finditer(text or "")] + [text or ""]
    best: dict = {}
    for candidate in candidates:
        for data in _json_objects(candidate):
            found = sum(_axis_entry(data, axis)[0] is not None for axis in AXES)
            if found > sum(value is not None for value in best.get("scores", {}).values()):
                best = {"scores": {}, "reasons": {}, "evidence": normalize_evidence(data.get("evidence"))}
                for axis in AXES:
                    best["scores"][axis], best["reasons"][axis] = _axis_entry(data, axis)
        if best and all(value is not None for value in best["scores"].values()):
            break
    best = best or {"scores": {axis: None for axis in AXES}, "reasons": {axis: "" for axis in AXES},
                    "evidence": None}
    if best["evidence"] is None:  # evidence alone, or an answer cut off after it
        for candidate in candidates:
            found = next((data["evidence"] for data in _json_objects(candidate)
                          if isinstance(data.get("evidence"), dict)), None)
            found = found or next((data for data in _json_objects(candidate)
                                   if "component_kinds" in data and "text_items" in data), None)
            if found:
                best["evidence"] = normalize_evidence(found)
                break
    for axis in AXES:  # truncated JSON or plain lines: "axis": {"reason": "...", "score": 4 / axis: 4
        if best["scores"][axis] is None:
            match = re.search(rf"[\"']?\b{axis}\b[\"']?\s*[:=\-]?\s*(?:\{{\s*(?:[\"']?reason[\"']?\s*:\s*"
                              rf"\"(?:[^\"\\]|\\.)*\"\s*,\s*)?[\"']?score[\"']?\s*:\s*)?([1-5](?:\.\d+)?)\b",
                              text or "", re.I)
            best["scores"][axis] = float(match.group(1)) if match else None
    missing = [axis for axis in AXES if best["scores"][axis] is None]
    return {**best, "valid": not missing, "missing": missing}


# ---- aggregation and cost (pure) ------------------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def combine(scores: dict, weights: dict | None = None) -> float | None:
    """The overall score: ``sum(weight * score)`` over the axes (weights summing to 1), or None unless every
    axis is present."""
    weights = WEIGHTS if weights is None else weights
    values = [scores.get(axis) for axis in AXES]
    if any(value is None for value in values):
        return None
    return round(sum(weights[axis] * scores[axis] for axis in AXES) / sum(weights[axis] for axis in AXES), 3)


def page_overall(scores: dict) -> float | None:
    """A page's overall score (``combine``), or None unless all five axes are present."""
    return combine(scores)


def aggregate(pages: list[dict]) -> dict:
    """Per-axis means over the pages that scored that axis, and ``overall`` = ``combine`` of the axis means."""
    axes = {axis: _mean([page["scores"][axis] for page in pages if page.get("scores", {}).get(axis) is not None])
            for axis in AXES}
    return {"axes": axes, "overall": combine(axes),
            "pages": len(pages), "pages_valid": sum(1 for page in pages if page.get("valid")),
            "per_axis_n": {axis: sum(1 for page in pages if page.get("scores", {}).get(axis) is not None)
                           for axis in AXES}}


def spread(decks: list[dict]) -> dict:
    """max - min of each axis mean (and overall) over repeated judgements of the same deck."""
    result = {}
    for key in (*AXES, "overall"):
        values = [deck["axes"][key] if key in AXES else deck["overall"] for deck in decks]
        values = [value for value in values if value is not None]
        result[key] = round(max(values) - min(values), 3) if len(values) >= 2 else None
    return result


def add_usage(total: dict, usage: dict) -> dict:
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key) or 0)
    return total


def cost_estimate(model: str, usage: dict) -> float | None:
    """USD for ``usage`` at the listed prices, or None for a model without one."""
    price = PRICES.get(model)
    if not price:
        return None
    fresh, output, read, write = price
    return round((usage.get("input_tokens", 0) * fresh + usage.get("output_tokens", 0) * output
                  + usage.get("cache_read_input_tokens", 0) * read
                  + usage.get("cache_creation_input_tokens", 0) * write) / 1_000_000, 4)


def run_fields(judged: dict) -> dict:
    """Flat numeric fields for a ``run_eval`` run record (so summarize and compare pick them up), with the
    ``judge_version`` that made them (``decide`` refuses to compare judge scores of different versions)."""
    deck = judged["deck"]
    fields = {f"judge_{axis}": deck["axes"][axis] for axis in AXES if deck["axes"][axis] is not None}
    if deck["overall"] is not None:
        fields["judge_overall"] = deck["overall"]
    if fields:
        fields["judge_version"] = judged.get("judge_version", JUDGE_VERSION)
    return fields


# ---- images ---------------------------------------------------------------------------------------------

def contact_sheet(pngs: list[bytes], width: int = SHEET_WIDTH) -> bytes:
    """All pages downscaled into one grid image, in the given order."""
    from PIL import Image

    images = [Image.open(BytesIO(data)).convert("RGB") for data in pngs]
    columns, rows = sheet_grid(len(images))
    gap = 8
    tile_w = (width - gap * (columns - 1)) // columns
    tile_h = round(tile_w * images[0].height / images[0].width)
    sheet = Image.new("RGB", (width, rows * tile_h + gap * (rows - 1)), "white")
    for index, image in enumerate(images):
        image.thumbnail((tile_w, tile_h))
        sheet.paste(image, ((index % columns) * (tile_w + gap), (index // columns) * (tile_h + gap)))
    out = BytesIO()
    sheet.save(out, format="PNG")
    return out.getvalue()


#: Fictional bullet lines of the default score 1 anchor (the look of a fallback page, not anyone's content).
LOW_ANCHOR_LINES = (
    "사업은 세 단계로 나누어 수행하며 단계마다 정해진 산출물을 남긴다.",
    "첫 단계에서는 현재 업무와 데이터 흐름을 조사한다.",
    "두 번째 단계에서는 공통 기준과 설계 원칙을 정한다.",
    "세 번째 단계에서는 구축한 결과를 시험하고 운영으로 넘긴다.",
    "각 단계는 승인을 받은 뒤에만 다음 단계로 넘어간다.",
    "위험은 매월 점검하고 대응 결과를 기록한다.",
    "교육은 부서별로 두 차례 진행한다.",
    "운영 전환 뒤 한 달 동안 현장 지원을 제공한다.",
)


def blank_share(png: bytes, body_top: float = 0.19, body_bottom: float = 0.94, tile: int = 24,
                flat: float = 3.0) -> float:
    """Share of the body area (between ``body_top`` and ``body_bottom``, fractions of the height) made of
    ``tile``-pixel squares of one flat colour (grey-level standard deviation below ``flat``): white space and
    blank card interiors. Original pages of a dense template measure about 0.25, text fallback pages 0.7 or more."""
    from PIL import Image, ImageStat

    image = Image.open(BytesIO(png)).convert("L")
    width, height = image.size
    top, bottom = round(height * body_top), round(height * body_bottom)
    total = flat_tiles = 0
    for y in range(top, bottom - tile + 1, tile):
        for x in range(0, width - tile + 1, tile):
            total += 1
            flat_tiles += ImageStat.Stat(image.crop((x, y, x + tile, y + tile))).stddev[0] < flat
    return round(flat_tiles / total, 3) if total else 0.0


def low_anchor(frame_png: bytes, font_path: str | None, lines: list[str] | tuple[str, ...] = LOW_ANCHOR_LINES,
               body_top: float = 0.19, body_bottom: float = 0.94, margin: float = 0.06,
               font_px: float = 0.0156, line_gap: float = 1.6) -> bytes:
    """A score 1 anchor built from scratch: the header and footer bands of ``frame_png`` (an original page)
    with the body between ``body_top`` and ``body_bottom`` (fractions of the height) cleared to white and
    filled with plain bullet lines, as a fallback page looks. Sizes are fractions of the page width. Without
    ``font_path`` Pillow's default font is used, which covers Latin text only."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.open(BytesIO(frame_png)).convert("RGB")
    width, height = image.size
    top, bottom = round(height * body_top), round(height * body_bottom)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, top, width, bottom), fill="white")
    size = max(8, round(width * font_px))
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size)
    x, y = round(width * margin), top + round(size * 1.2)
    for line in lines:
        if y + size > bottom:
            break
        draw.text((x, y), "•", font=font, fill=(40, 40, 40))
        draw.text((x + round(size * 0.9), y), line, font=font, fill=(20, 20, 20))
        y += round(size * line_gap)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


_RENDER_JS = r"""
const fs = require('fs');
const { chromium } = require('playwright');
(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch();
  try {
    for (const job of jobs) {
      const page = await browser.newPage({ viewport: { width: job.width, height: job.height }, deviceScaleFactor: 1 });
      await page.goto('file://' + job.svg, { waitUntil: 'load' });
      await page.screenshot({ path: job.png, clip: { x: 0, y: 0, width: job.width, height: job.height } });
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
"""


def svg_size(svg: str) -> tuple[int, int]:
    """Pixel size of an SVG page from its root ``width``/``height``, else its ``viewBox``."""
    head = svg[:2000]
    width, height = (re.search(rf'\b{name}="([\d.]+)', head) for name in ("width", "height"))
    if width and height:
        return round(float(width.group(1))), round(float(height.group(1)))
    box = re.search(r'viewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', head)
    if box:
        return round(float(box.group(1))), round(float(box.group(2)))
    return 1280, 720


def render_svgs(svgs: list[str], out_dir: Path) -> list[Path]:
    """Render SVG pages to PNGs (``page-01.png`` ...) with Playwright's Chromium in Node, at scale 1."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="svg-judge-") as work:
        jobs = []
        for number, svg in enumerate(svgs, start=1):
            svg_path = Path(work) / f"page-{number:02d}.svg"
            svg_path.write_text(svg, encoding="utf-8")
            width, height = svg_size(svg)
            jobs.append({"svg": str(svg_path), "png": str(out_dir / f"page-{number:02d}.png"),
                         "width": width, "height": height})
        (Path(work) / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        (Path(work) / "render.js").write_text(_RENDER_JS, encoding="utf-8")
        completed = subprocess.run(["node", str(Path(work) / "render.js"), str(Path(work) / "jobs.json")],
                                   capture_output=True, text=True, timeout=60 + 30 * len(jobs))
        if completed.returncode != 0:
            raise RuntimeError(f"rendering failed (needs node with playwright): {completed.stderr.strip()[-800:]}")
    return [Path(job["png"]) for job in jobs]


def deck_svgs(conn, presentation_id: str) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute("select svg_content from slides where presentation = %s order by index", (presentation_id,))
        return [row[0] for row in cursor.fetchall() if row[0]]


# ---- model calls ----------------------------------------------------------------------------------------

class Judge:
    """Judges pages against one fixed set of anchors (and context references) with one model."""

    def __init__(self, references_dir: Path, chosen: list[str] | None = None, *, anchor_high: list[str],
                 anchor_low: list[str], context: bool = True, model: str = DEFAULT_MODEL,
                 effort: str | None = DEFAULT_EFFORT, temperature: float | None = None, fallbacks: bool = True,
                 max_tokens: int = 8000, workers: int = 4, measure: bool = True,
                 body: tuple[float, float] = (0.19, 0.94)):
        import anthropic

        if not anchor_high or not anchor_low:
            raise SystemExit("the judge needs --anchor-high (one or two original pages) and --anchor-low "
                             "(a fallback-style page; build one with 'judge.py low-anchor')")
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise SystemExit(f"{API_KEY_ENV} is not set")
        self._client = anthropic.Anthropic(api_key=key, max_retries=4)
        references_dir = Path(references_dir)
        high = [resolve_anchor(value, references_dir) for value in anchor_high]
        low = [resolve_anchor(value, references_dir) for value in anchor_low]
        self.measure, self.body = measure, body
        self.high = [self._anchor(path) for path in high]
        self.low = [self._anchor(path) for path in low]
        self.anchor_names = {"high": [path.name for path in high], "low": [path.name for path in low]}
        files = [path.name for path in references_dir.iterdir() if path.suffix.lower() == ".png"]
        self.plan = select_references(files, chosen) if context else {"mode": "none", "pages": []}
        pngs = [(references_dir / name).read_bytes() for name in self.plan["pages"]]
        self.sheet = self.plan["mode"] == "sheet"
        self.context = ([(str(len(pngs)), contact_sheet(pngs))] if self.sheet
                        else list(zip(self.plan["pages"], pngs, strict=True)))
        self.model, self.effort, self.temperature = model, effort, temperature
        self.fallbacks, self.max_tokens, self.workers = fallbacks, max_tokens, workers

    def _blank(self, png: bytes) -> float | None:
        return blank_share(png, *self.body) if self.measure else None

    def _anchor(self, path: Path) -> tuple[str, bytes]:
        png = path.read_bytes()
        blank = self._blank(png)
        return (path.name if blank is None else f"{path.name}, measured blank share {round(blank * 100)}%"), png

    def settings(self) -> dict:
        return {"judge_version": JUDGE_VERSION, "model": self.model, "effort": self.effort,
                "temperature": self.temperature, "fallbacks": "default" if self.fallbacks else None,
                "anchor_high": self.anchor_names["high"], "anchor_low": self.anchor_names["low"],
                "anchor_labels": [label for label, _ in self.high + self.low],
                "measure_blank": self.measure, "body": list(self.body),
                "reference_mode": self.plan["mode"], "reference_pages": self.plan["pages"], "axes": list(AXES),
                "weights": WEIGHTS}

    def judge_page(self, png: bytes) -> dict:
        blank = self._blank(png)
        request = build_request(png, self.high, self.low, self.context, self.sheet, generated_blank=blank)
        output_config: dict = {"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}}
        if self.effort:
            output_config["effort"] = self.effort
        extra: dict = {}
        if self.temperature is not None:
            extra["temperature"] = self.temperature
        if self.fallbacks:
            extra["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            extra["extra_body"] = {"fallbacks": "default"}
        started = time.time()
        try:
            response = self._client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                                    output_config=output_config, **request, **extra)
        except Exception as error:  # one failed page must not lose the deck
            return {"valid": False, "scores": {axis: None for axis in AXES}, "reasons": {}, "evidence": None,
                    "missing": list(AXES), "error": f"{type(error).__name__}: {str(error)[:300]}", "usage": {},
                    "blank_share": blank}
        usage = response.usage.model_dump() if response.usage else {}
        text = "".join(getattr(block, "text", "") for block in response.content
                       if getattr(block, "type", None) == "text")
        parsed = parse_judgement(text)
        page = {**parsed, "overall": page_overall(parsed["scores"]), "blank_share": blank,
                "served_model": response.model,
                "stop_reason": response.stop_reason, "seconds": round(time.time() - started, 1),
                "usage": {key: usage.get(key) or 0 for key in ("input_tokens", "output_tokens",
                                                               "cache_read_input_tokens",
                                                               "cache_creation_input_tokens")}}
        if response.stop_reason != "end_turn":
            page["error"] = f"stop_reason {response.stop_reason}"
        if not parsed["valid"]:
            page["raw"] = text[:1000]
        return page

    def judge_pages(self, pngs: list[Path]) -> dict:
        """Judge every page; the first alone so its anchor prefix is cached before the rest run in parallel."""
        datas = [path.read_bytes() for path in pngs]
        results: list[dict] = [self.judge_page(datas[0])] if datas else []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results += list(pool.map(self.judge_page, datas[1:]))
        for path, result in zip(pngs, results, strict=True):
            result["page"] = path.name
        usage: dict = {}
        for result in results:
            add_usage(usage, result.get("usage") or {})
        served = sorted({result["served_model"] for result in results if result.get("served_model")})
        costs = [cost_estimate(result["served_model"], result["usage"]) for result in results
                 if result.get("served_model")]
        cost = None if None in costs else round(sum(costs), 4)
        return {"judge_version": JUDGE_VERSION, "deck": aggregate(results), "pages": results, "usage": usage,
                "cost_estimate_usd": cost, "served_models": served,
                "mixed_models": len(served) > 1 or (bool(served) and served != [self.model])}


def judge_presentation(conn, presentation_id: str, judge: Judge, render_dir: Path | None = None) -> dict:
    """Render a stored deck's pages and judge them; PNGs are kept under ``render_dir`` when given."""
    svgs = deck_svgs(conn, presentation_id)
    if not svgs:
        raise ValueError(f"presentation {presentation_id} has no svg pages")
    work = Path(render_dir) / presentation_id if render_dir else Path(tempfile.mkdtemp(prefix="svg-judge-png-"))
    try:
        result = judge.judge_pages(render_svgs(svgs, work))
    finally:
        if not render_dir:
            shutil.rmtree(work, ignore_errors=True)
    return {"presentation_id": presentation_id, **result}


def print_deck(label: str, judged: dict) -> None:
    deck = judged["deck"]
    axes = " ".join(f"{axis} {deck['axes'][axis]}" for axis in AXES)
    print(f"{label}: {axes} overall {deck['overall']} ({deck['pages_valid']}/{deck['pages']} pages valid,"
          f" cost ${judged['cost_estimate_usd']}, judge v{judged.get('judge_version', JUDGE_VERSION)})", flush=True)
    if judged.get("mixed_models"):
        print(f"   warning: pages were served by {judged['served_models']}; scores are not comparable", flush=True)


# ---- commands -------------------------------------------------------------------------------------------

def _names(value: str | None) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()] if value else []


def judge_from_options(references: str, options: dict) -> Judge:
    """A Judge from the options ``add_judge_options`` defines (``options`` keyed without the prefix)."""
    return Judge(Path(references), _names(options.get("ref_pages")) or None,
                 anchor_high=_names(options.get("anchor_high")), anchor_low=_names(options.get("anchor_low")),
                 context=not options.get("no_context"), model=options["model"], effort=options["effort"] or None,
                 temperature=options.get("temperature"), fallbacks=not options.get("no_fallbacks"),
                 workers=options.get("workers", 4), measure=not options.get("no_measure"),
                 body=(options.get("body_top", 0.19), options.get("body_bottom", 0.94)))


def _judge_from_args(args: argparse.Namespace) -> Judge:
    return judge_from_options(args.references, vars(args))


def cmd_deck(args: argparse.Namespace) -> None:
    from run_eval import _db  # the backend's virtualenv has psycopg

    judge = _judge_from_args(args)
    conn = _db(args.dsn)
    runs, repeats = [], []
    for number, presentation_id in enumerate(args.presentation_id, start=1):
        decks = []
        for repeat in range(1, args.repeat + 1):
            judged = judge_presentation(conn, presentation_id, judge, Path(args.renders) if args.renders else None)
            print_deck(f"deck {number} ({presentation_id[:8]}) judgement {repeat}", judged)
            decks.append(judged)
        record = {"run": number, "completed": True, "presentation_id": presentation_id, **run_fields(decks[0]),
                  "judge": decks[0]}
        if args.repeat > 1:
            record["judge_repeats"] = decks[1:]
            repeats.append({"presentation_id": presentation_id, "spread": spread([deck["deck"] for deck in decks]),
                            "means": [deck["deck"] for deck in decks]})
        runs.append(record)
    usage: dict = {}
    for record in runs:
        for deck in [record["judge"], *record.get("judge_repeats", [])]:
            add_usage(usage, deck["usage"])
    costs = [deck["cost_estimate_usd"] for record in runs for deck in [record["judge"], *record.get("judge_repeats", [])]]
    result = {"label": args.label, "runs": runs, "summary": summarize(runs), "completed_runs": len(runs),
              "requested_runs": len(runs), "judge_settings": judge.settings(), "judge_usage": usage,
              "judge_cost_estimate_usd": None if None in costs else round(sum(costs), 4)}
    if repeats:
        result["judge_repeatability"] = repeats
        for item in repeats:
            print(f"spread over {args.repeat} judgements of {item['presentation_id'][:8]}: {item['spread']}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"total cost estimate ${result['judge_cost_estimate_usd']}; wrote {out / (args.label + '.json')}")


def cmd_pages(args: argparse.Namespace) -> None:
    judge = _judge_from_args(args)
    pngs = sorted(path for path in Path(args.generated).iterdir() if path.suffix.lower() == ".png")
    judged = judge.judge_pages(pngs)
    print_deck(args.generated, judged)
    for page in judged["pages"]:
        print(f"   {page['page']}: overall {page.get('overall')} {page.get('scores')}")
    Path(args.out).write_text(json.dumps({"judge_settings": judge.settings(), **judged}, ensure_ascii=False,
                                         indent=2), encoding="utf-8")


def cmd_references(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for page in (int(value) for value in args.pages.split(",")):
        prefix = out / f"ref-{page:03d}"
        subprocess.run(["pdftoppm", "-png", "-singlefile", "-f", str(page), "-l", str(page), "-scale-to-x",
                        str(args.width), "-scale-to-y", "-1", args.pdf, str(prefix)], check=True)
        print(prefix.with_suffix(".png"))


def cmd_low_anchor(args: argparse.Namespace) -> None:
    lines = (Path(args.lines).read_text(encoding="utf-8").splitlines() if args.lines else LOW_ANCHOR_LINES)
    png = low_anchor(Path(args.frame).read_bytes(), args.font, [line for line in lines if line.strip()],
                     body_top=args.body_top, body_bottom=args.body_bottom)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(png)
    print(args.out)


def add_judge_options(parser: argparse.ArgumentParser, prefix: str = "") -> None:
    """Judge options; ``run_eval.py`` adds them with the ``judge-`` prefix."""
    parser.add_argument(f"--{prefix}anchor-high",
                        help="score 5 anchors: one or two original pages (paths or names in the references dir)")
    parser.add_argument(f"--{prefix}anchor-low", help="score 1 anchor(s): fallback-style pages (see low-anchor)")
    parser.add_argument(f"--{prefix}no-context", action="store_true",
                        help="do not send the references directory as context")
    parser.add_argument(f"--{prefix}no-measure", action="store_true",
                        help="do not tell the model each page's measured blank share")
    parser.add_argument(f"--{prefix}body-top", type=float, default=0.19,
                        help="where the body starts below the header band, fraction of the page height")
    parser.add_argument(f"--{prefix}body-bottom", type=float, default=0.94,
                        help="where the body ends above the footer, fraction of the page height")
    parser.add_argument(f"--{prefix}model", default=DEFAULT_MODEL, help="vision model id")
    parser.add_argument(f"--{prefix}effort", default=DEFAULT_EFFORT, help="output effort ('' for the model default)")
    parser.add_argument(f"--{prefix}temperature", type=float, default=None,
                        help="sampling temperature, only for a model that accepts one")
    parser.add_argument(f"--{prefix}ref-pages", help="comma-separated context reference names to send one by one")
    parser.add_argument(f"--{prefix}no-fallbacks", action="store_true",
                        help="do not let a declined request fall back to another model")
    parser.add_argument(f"--{prefix}workers", type=int, default=4, help="pages judged in parallel")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    deck = sub.add_parser("deck", help="judge stored decks (rendered from the database)")
    deck.add_argument("--references", required=True, help="directory of reference page PNGs")
    deck.add_argument("--presentation-id", action="append", required=True, help="repeatable")
    deck.add_argument("--label", required=True)
    deck.add_argument("--out", default="eval-results")
    deck.add_argument("--repeat", type=int, default=1, help="judge each deck this many times (repeatability)")
    deck.add_argument("--renders", help="keep the rendered page PNGs here")
    deck.add_argument("--dsn", default=os.environ.get("EVAL_DATABASE_DSN", DEFAULT_DSN))
    add_judge_options(deck)
    deck.set_defaults(handler=cmd_deck)
    pages = sub.add_parser("pages", help="judge a directory of generated page PNGs")
    pages.add_argument("--references", required=True)
    pages.add_argument("--generated", required=True)
    pages.add_argument("--out", required=True, help="result JSON file")
    add_judge_options(pages)
    pages.set_defaults(handler=cmd_pages)
    refs = sub.add_parser("references", help="render chosen pages of a template PDF to reference PNGs")
    refs.add_argument("--pdf", required=True, help="the template deck exported to PDF")
    refs.add_argument("--pages", required=True, help="1-based page numbers, comma-separated")
    refs.add_argument("--out", required=True)
    refs.add_argument("--width", type=int, default=1280)
    refs.set_defaults(handler=cmd_references)
    low = sub.add_parser("low-anchor", help="build a score 1 anchor: an original page's frame with bullet text")
    low.add_argument("--frame", required=True, help="an original page PNG whose header and footer are kept")
    low.add_argument("--font", required=True, help="a TTF/OTF font that covers the deck's language")
    low.add_argument("--out", required=True)
    low.add_argument("--lines", help="text file of bullet lines (default: built-in fictional lines)")
    low.add_argument("--body-top", type=float, default=0.19, help="body start, fraction of the page height")
    low.add_argument("--body-bottom", type=float, default=0.94, help="body end, fraction of the page height")
    low.set_defaults(handler=cmd_low_anchor)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
