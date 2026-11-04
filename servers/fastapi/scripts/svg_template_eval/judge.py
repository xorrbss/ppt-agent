#!/usr/bin/env python3
"""Paired vision judge: does a generated page look like the template's own pages?

The counts in ``metrics.py`` say what a page drew, not whether it reads like the template. This asks a vision
model to compare each generated page with reference pages of the original template deck and score five axes
from 1 to 5, with a short reason each:

    structure  body composition: diagrams, tables, cards against plain text
    density    amount and balance of content
    hierarchy  title / key message / body levels
    tone       fonts, colours and manner of the template kept
    visuals    icons, figures, shapes

Reference pages are PNGs the operator renders from the template deck at run time (``references`` below, or
any renderer). They are never stored in the repository. Every generated page is judged against the same
references, chosen by one rule (``select_references``): the pages named with ``--ref-pages``; otherwise all
PNGs of the directory one by one when there are at most three; otherwise all of them downscaled into one
contact sheet. A score is a model's opinion and varies between calls, so judge a change on three or more decks.

    judge.py references --pdf template.pdf --pages 4,9,19,25 --out refs/
    judge.py deck --references refs/ --presentation-id <id> [--presentation-id <id> ...] --label base --out results/
    judge.py pages --references refs/ --generated pngs/ --out page-scores.json

``deck`` reads ``slides.svg_content`` from the database and renders each page with Playwright (Node). Its
output has the shape of a ``run_eval.py run`` result, so ``run_eval.py compare`` reads it. ``run_eval.py run
--judge-references refs/`` judges every generated deck in the same way. The API key is read from
``PRESENTON_ANTHROPIC_API_KEY``. Pure parts are unit-tested (tests/unit/test_svg_template_eval_judge.py).
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

AXES = ("structure", "density", "hierarchy", "tone", "visuals")
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "low"
API_KEY_ENV = "PRESENTON_ANTHROPIC_API_KEY"
DEFAULT_DSN = "postgresql://postgres@127.0.0.1:5433/chap"
#: At most this many references are sent one by one; more go into one contact sheet.
MAX_INDIVIDUAL_REFERENCES = 3
SHEET_WIDTH = 1568
#: USD per million tokens: input, output, cache read, cache write. A model not listed gets no cost estimate.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

SYSTEM_PROMPT = """You review slide design. A presentation was generated from a registered template deck. \
You see reference pages of that template deck, then one generated page. Judge how well the generated page \
keeps the template's design. The generated page carries different content than the references; do not judge \
the content, its language or its correctness, only how the page is designed.

Score each axis from 1 to 5 against the references:
- structure: body composition. 5 = the body is built from diagrams, tables, cards or grouped blocks the way \
the references are; 3 = some structure, but simpler than the references; 1 = plain text or bullets where the \
references would use structure.
- density: amount and balance of content. 5 = about as full as the references and evenly spread; 3 = clearly \
emptier or fuller, or lopsided; 1 = mostly empty, or crowded to the point of overlap or cut-off text.
- hierarchy: levels of title, key message and body. 5 = the same distinct levels as the references; 3 = levels \
exist but are weak or inconsistent; 1 = no readable hierarchy.
- tone: fonts, colours and manner. 5 = could pass as a page of the template deck; 3 = related but visibly \
different; 1 = a different design.
- visuals: icons, figures, shapes. 5 = visual elements of the kind and amount the references use; 3 = few or \
generic ones; 1 = none where the references would have them.

Use the whole scale. Give each axis a reason of one short sentence naming what you saw. Answer only with the \
JSON object asked for."""

INSTRUCTION = ("Score the generated page above on structure, density, hierarchy, tone and visuals "
               "(1 to 5 each, with a one-sentence reason), compared with the reference pages.")

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {axis: {"type": "object",
                          "properties": {"score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                                         "reason": {"type": "string"}},
                          "required": ["score", "reason"], "additionalProperties": False}
                   for axis in AXES},
    "required": list(AXES),
    "additionalProperties": False,
}


# ---- reference choice and prompt assembly (pure) --------------------------------------------------------

def select_references(available: list[str], chosen: list[str] | None = None,
                      limit: int = MAX_INDIVIDUAL_REFERENCES) -> dict:
    """Which references every page is judged against, and how they are sent.

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


def sheet_grid(count: int) -> tuple[int, int]:
    """(columns, rows) of a contact sheet of ``count`` pages: three columns up to nine pages, then four."""
    columns = min(count, 3 if count <= 9 else 4)
    return columns, math.ceil(count / columns)


def _image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(png).decode("ascii")}}


def build_request(references: list[tuple[str, bytes]], generated: bytes, sheet: bool) -> dict:
    """System prompt and user content for one page.

    ``references`` is a list of (label, PNG bytes): one contact sheet, or the pages one by one. They come
    first and are the same for every page, so the prefix up to them is marked for prompt caching.
    """
    content: list[dict] = []
    if sheet:
        label = references[0][0]
        content.append({"type": "text", "text": f"Reference: contact sheet of {label} pages of the template deck."})
        content.append(_image_block(references[0][1]))
    else:
        for number, (label, png) in enumerate(references, start=1):
            content.append({"type": "text", "text": f"Reference page {number} of the template deck ({label}):"})
            content.append(_image_block(png))
    content[-1]["cache_control"] = {"type": "ephemeral"}
    content.append({"type": "text", "text": "Generated page:"})
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
                return _score(value.get("score")), str(value.get("reason") or "")
            reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
            return _score(value), str(reasons.get(axis) or data.get(f"{axis}_reason") or "")
    return None, ""


def parse_judgement(text: str) -> dict:
    """Scores and reasons from a model answer: strict JSON, fenced JSON, JSON inside prose, or plain
    ``axis: 4`` lines as a last resort. An axis without a score in 1..5 is None; ``valid`` means all five."""
    candidates = [match.group(1) for match in _FENCE.finditer(text or "")] + [text or ""]
    best: dict = {}
    for candidate in candidates:
        for data in _json_objects(candidate):
            found = sum(_axis_entry(data, axis)[0] is not None for axis in AXES)
            if found > sum(value is not None for value in best.get("scores", {}).values()):
                best = {"scores": {}, "reasons": {}}
                for axis in AXES:
                    best["scores"][axis], best["reasons"][axis] = _axis_entry(data, axis)
        if best and all(value is not None for value in best["scores"].values()):
            break
    best = best or {"scores": {axis: None for axis in AXES}, "reasons": {axis: "" for axis in AXES}}
    for axis in AXES:  # truncated JSON or plain lines: "axis": {"score": 4 / axis: 4
        if best["scores"][axis] is None:
            match = re.search(rf"\b{axis}\b[\"']?\s*[:=\-]?\s*(?:\{{\s*[\"']?score[\"']?\s*:\s*)?"
                              rf"([1-5](?:\.\d+)?)\b", text or "", re.I)
            best["scores"][axis] = float(match.group(1)) if match else None
    missing = [axis for axis in AXES if best["scores"][axis] is None]
    return {**best, "valid": not missing, "missing": missing}


# ---- aggregation and cost (pure) ------------------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def page_overall(scores: dict) -> float | None:
    """Mean of a page's axis scores, or None unless all five are present."""
    values = [scores.get(axis) for axis in AXES]
    return None if any(value is None for value in values) else _mean(values)


def aggregate(pages: list[dict]) -> dict:
    """Per-axis means over the pages that scored that axis, and ``overall`` = mean of the axis means."""
    axes = {axis: _mean([page["scores"][axis] for page in pages if page.get("scores", {}).get(axis) is not None])
            for axis in AXES}
    present = [value for value in axes.values() if value is not None]
    return {"axes": axes, "overall": _mean(present) if len(present) == len(AXES) else None,
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
    """Flat numeric fields for a ``run_eval`` run record (so summarize and compare pick them up)."""
    deck = judged["deck"]
    fields = {f"judge_{axis}": deck["axes"][axis] for axis in AXES if deck["axes"][axis] is not None}
    if deck["overall"] is not None:
        fields["judge_overall"] = deck["overall"]
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
    """Judges pages against one fixed set of references with one model."""

    def __init__(self, references_dir: Path, chosen: list[str] | None = None, model: str = DEFAULT_MODEL,
                 effort: str | None = DEFAULT_EFFORT, temperature: float | None = None, fallbacks: bool = True,
                 max_tokens: int = 8000, workers: int = 4):
        import anthropic

        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise SystemExit(f"{API_KEY_ENV} is not set")
        self._client = anthropic.Anthropic(api_key=key, max_retries=4)
        files = [path.name for path in Path(references_dir).iterdir() if path.suffix.lower() == ".png"]
        self.plan = select_references(files, chosen)
        pngs = [(Path(references_dir) / name).read_bytes() for name in self.plan["pages"]]
        self.sheet = self.plan["mode"] == "sheet"
        self.references = ([(str(len(pngs)), contact_sheet(pngs))] if self.sheet
                           else list(zip(self.plan["pages"], pngs, strict=True)))
        self.model, self.effort, self.temperature = model, effort, temperature
        self.fallbacks, self.max_tokens, self.workers = fallbacks, max_tokens, workers

    def settings(self) -> dict:
        return {"model": self.model, "effort": self.effort, "temperature": self.temperature,
                "fallbacks": "default" if self.fallbacks else None, "reference_mode": self.plan["mode"],
                "reference_pages": self.plan["pages"], "axes": list(AXES)}

    def judge_page(self, png: bytes) -> dict:
        request = build_request(self.references, png, self.sheet)
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
            return {"valid": False, "scores": {axis: None for axis in AXES}, "reasons": {},
                    "missing": list(AXES), "error": f"{type(error).__name__}: {str(error)[:300]}", "usage": {}}
        usage = response.usage.model_dump() if response.usage else {}
        text = "".join(getattr(block, "text", "") for block in response.content
                       if getattr(block, "type", None) == "text")
        parsed = parse_judgement(text)
        page = {**parsed, "overall": page_overall(parsed["scores"]), "served_model": response.model,
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
        """Judge every page; the first alone so its reference prefix is cached before the rest run in parallel."""
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
        return {"deck": aggregate(results), "pages": results, "usage": usage, "cost_estimate_usd": cost,
                "served_models": served, "mixed_models": len(served) > 1 or (bool(served) and served != [self.model])}


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
          f" cost ${judged['cost_estimate_usd']})", flush=True)
    if judged.get("mixed_models"):
        print(f"   warning: pages were served by {judged['served_models']}; scores are not comparable", flush=True)


# ---- commands -------------------------------------------------------------------------------------------

def _judge_from_args(args: argparse.Namespace) -> Judge:
    chosen = [name.strip() for name in args.ref_pages.split(",")] if args.ref_pages else None
    return Judge(Path(args.references), chosen, model=args.model, effort=args.effort or None,
                 temperature=args.temperature, fallbacks=not args.no_fallbacks, workers=args.workers)


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


def add_judge_options(parser: argparse.ArgumentParser, prefix: str = "") -> None:
    """Judge options; ``run_eval.py`` adds them with the ``judge-`` prefix."""
    parser.add_argument(f"--{prefix}model", default=DEFAULT_MODEL, help="vision model id")
    parser.add_argument(f"--{prefix}effort", default=DEFAULT_EFFORT, help="output effort ('' for the model default)")
    parser.add_argument(f"--{prefix}temperature", type=float, default=None,
                        help="sampling temperature, only for a model that accepts one")
    parser.add_argument(f"--{prefix}ref-pages", help="comma-separated reference file names to send one by one")
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
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
