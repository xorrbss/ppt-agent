# SVG template evaluation

Per-deck `content_sizing` records retain each page's conservative card-space evidence and
`svg-content-sizing-v1` measurement policy. The driver uses the saved `svg_template_page.body_region`
when present; otherwise the helper explicitly reports page-fallback bounds. Unavailable measurements
remain unavailable, not zero. These are character-proxy envelope measurements, not glyph-ink or
visual-density scores, and never reject a page.

Judge a change to SVG template generation on several runs of a fixed input, never on one: the same
input gave 3 to 7 fallback pages in a row. Three scripts, none of them part of the product:

| Script | Does |
|---|---|
| `register_template.py` | Registers a PPTX as a published template through the API and prints its pin (upload, snapshot, framed registration, automatic font substitution, publish). Takes 5 to 8 minutes on a 100-page deck |
| `run_eval.py run` | Sends one generation request N times and measures each deck (below) |
| `run_eval.py compare` | Prints two result files side by side |
| `run_eval.py matrix` | The same as `run` over input x template cells, R runs per cell, for one variant label |
| `run_eval.py decide` | Paired verdict per metric of result B against result A (below) |
| `judge.py` | Scores generated pages against reference pages of the template with a vision model (below) |

`metrics.py` holds the pure metrics (unit-tested in `tests/unit/test_svg_template_eval_metrics.py`); the pure
parts of `judge.py` are tested in `tests/unit/test_svg_template_eval_judge.py`.

## Use

Run the backend as in `scripts/fake_llm_e2e` with a real provider key and the pricing catalog, plus
`SVG_USER_TEMPLATES_ENABLED=true` and `DISABLE_AUTH=true` (the bootstrap administrator must exist). Use the
backend's virtualenv, which has `httpx` and `psycopg`.

```bash
.venv/bin/python scripts/svg_template_eval/register_template.py --pptx deck.pptx --name eval --pin-out pin.json
# request.json: a generate/async body with "svgTemplatePin": <pin.json>, "svgTemplateMode": "template-redesign",
# "strategy": "svg", "svgCanvas": {...}, "n_slides": 10, "content": "<text>" (see inputs/ for a fixed sample)
.venv/bin/python scripts/svg_template_eval/run_eval.py run --payload request.json --runs 3 --label baseline \
    --source inputs/structured_proposal_ko.txt --pptx --out results/
```

A variant that is a server setting (for example `CONTENT_STRUCTURE_POLICY_SCOPE=template`) is measured by
restarting the backend with it and running again under another `--label`, then `run_eval.py compare`.

**Use a fresh database and app-data directory per batch.** A registered template's stored files can disappear
from the local `public-artifacts` store (`artifact.object_missing`, shown as HTTP 500 at admission). Observed:
every adaptation artifact of the newest template had its retention binding, yet 3 of 9 files were gone. Registering
the same deck again produces byte-identical material that shares one content-addressed file with the earlier
registration's rows, so deleting an older, unbound sibling's file is the suspected cause (not confirmed). A fresh
database and directory has no older siblings, so register once and run the whole batch there.

Do not run a large pytest at the same time: it has killed the local PostgreSQL and backend processes.

**Template body renders** (`SVG_TEMPLATE_BODY_RENDERS`, docs/template-body-renders-design-20261105.md): every framed
registration stores masked renders of the template's body exemplar pages, so one registration serves both arms; only the
generation knob differs between them. Start the backend that registers with `SVG_TEMPLATE_BODY_RENDER_EXCLUDED_PAGES` set
to the judge's score-5 anchor slides (1-based, e.g. `9,66`) so they are never shown to the author; the backend log line
`[svg-template] body renders: {...}` gives the rendered, excluded and rejected counts and the OCR path. A run's page
receipts carry `template_body_renders` (`authored_slide_checkpoints.svg_checkpoint -> attempts -> receipt`). Give the
template manifest `"template_text": "<the template .pptx>"` (a local file) to record `template_text_leak_ngrams` and
`template_text_leak_figures` per run.

## What one run records

- **Stability:** quality gate (passed / flagged), fallback pages (pages excluded from review), pages right on the
  first draft, and why the others needed a repair (`repair_causes`).
- **Structure** (from the page SVG, frame furniture and icons left out): `polygon` (the closest signal for an arrowhead
  or a chevron), `line`, `path`, `polyline` (rules and dividers count too), `rect`, `text`, `icons`, `pictures`,
  `arrow_glyphs` in body text, `flow_shapes` (sum of the four shape kinds) and `pages_with_flow_shapes`.
- **Native objects** (`--pptx`): `tables`, `charts`, `pages_with_table` in the exported PPTX.
- **Content kept** (`--source`): `words_kept` and `numbers_kept`, the share of the source's words and numbers
  that appear in the deck.

These are counts of what exists, not a judgment of how good it looks. Pair them with a look at the pages, or
with the judge below.

## Vision judge (`judge.py`)

Asks a vision model how close each generated page comes to the template's own pages. The model first fills
**evidence** it can count on the page body (distinct component kinds, sections, whether a key-message bar is present,
approximate text items, empty share, template components reused, defects such as raw markup, code-like labels or
arrows pointing against their sequence), then scores five axes from 1 to 5, each with a reason citing those counts:
**structure** (distinct component kinds and sections against one repeated component or plain text), **density**
(text items and empty area), **hierarchy** (title / key-message bar / numbered sub-headings / body levels), **tone**
(the template's manner in the body: type sizes, colours, rules; the header and footer frame copied onto every page
earns nothing) and **visuals** (icons, figures, charts, diagram shapes). Every listed defect caps structure at 2 and
costs tone a point. The rubric with its count thresholds is `SYSTEM_PROMPT` in `judge.py`.

**`overall` = the weighted mean of the axis scores in `WEIGHTS`, currently equal weights (0.2 each)**, per page and
per deck (the deck's `overall` combines the deck's axis means). Weighting structure and density more
(0.3/0.3/0.15/0.1/0.15) was tried in calibration and followed the human scores less closely (on the same answers of
the final prompt Spearman 0.66 against 0.75, Pearson 0.92 against 0.94), so it was not kept.

**A judge score is a model's opinion, not a measurement.** It moves with the model, the anchors, the reference pages
and the prompt, and it varies between calls. Compare only results made with the same `judge_version`, model, effort
and anchors, and judge a change on three or more decks (N >= 3), as for the other metrics.

### `judge_version`: scores before version 2 are not comparable

Every result records `judge_version` (in `judge_settings`, in each judged deck, and as a run field next to
`judge_<axis>` / `judge_overall`). Version 1 (results without the field: a contact sheet of references, no anchors,
plain mean) rated generated pages about 1.3 points higher than version 2 and compressed the range, so a version 1
score cannot be set against a version 2 one. `run_eval.py decide` marks every `judge_*` metric `not comparable` when
the judged runs of the two results do not all carry one version, and `compare` warns. Bump `JUDGE_VERSION` whenever
the prompt, the anchors' role, the rubric or the combination change what a score means.

### Anchors and references

The judge sees, in this order (the same prefix for every page, cached after the first page):

1. **Context** (optional): every PNG of `--references` as one downscaled contact sheet (one by one when there are at
   most three, or the files named with `--ref-pages`), to show the range of the template's components.
   `--no-context` drops it.
2. **Score 5 anchors** (`--anchor-high a.png,b.png`): one or two original pages at full size, which score 5 on every
   axis. Choose dense body pages that each combine several **different** components in numbered sections (for example
   an org chart + chart + table page and a process + table + icon page), not a cover, a section divider or a one-table
   page. A path, or a file name inside `--references`.
3. **Score 1 anchor** (`--anchor-low low.png`): a fallback-style page in the template frame with plain bullet text.
   Build it from scratch, never from a generated page under evaluation (that would leak the answer):
   `judge.py low-anchor --frame refs/ref-058.png --font Pretendard-Regular.ttf --out anchors/low.png` keeps the
   header band and footer of an original page (`--body-top 0.19 --body-bottom 0.94`, fractions of the height) and
   writes eight fictional bullet lines into the cleared body (`--lines file.txt` for others; the font must cover the
   deck's language).

Each anchor and the generated page also carry a **measured blank share** in their label (`blank_share`: the share of
the body area made of 24-pixel tiles of one flat colour, so white space and blank card interiors; `--body-top` /
`--body-bottom` set the body, `--no-measure` drops it). The model compares the page's measure with the anchors'
(original body pages measured 0.15 to 0.37, the generated pages below 0.45 to 0.58, fallback pages 0.71 to 0.79), because
asked alone it estimated the empty area poorly (a full page and a page of empty cards both "35 to 40%").

Reference and anchor pages are PNGs of the original template deck, rendered by the operator and kept outside the
repository (never commit a customer deck or its renders). Render the deck to PDF with LibreOffice with its fonts
installed (a deck's embedded `ppt/fonts/*.fntdata` are EOT files; without the real fonts the pages show substitutes),
then `judge.py references --pdf deck.pdf --pages 4,9,19,25,45,58,66,73 --out refs/`.

### Commands

Generated pages are read from `slides.svg_content` and rendered with Playwright's Chromium (Node,
`require('playwright')`, scale 1). The key comes from `PRESENTON_ANTHROPIC_API_KEY`.

```bash
A="--references refs/ --anchor-high ref-009.png,ref-066.png --anchor-low anchors/low.png"
# stored decks (any presentation ids); the output has the run_eval shape, so run_eval.py compare reads it
.venv/bin/python scripts/svg_template_eval/judge.py deck $A --dsn "$DSN" \
    --presentation-id <id> --presentation-id <id> --presentation-id <id> --label baseline --out results/
# the same deck several times, to see how much the judge itself moves (judge_repeatability in the output)
.venv/bin/python scripts/svg_template_eval/judge.py deck $A --presentation-id <id> --repeat 3 ...
# page PNGs from anywhere (prints every page's overall and axis scores)
.venv/bin/python scripts/svg_template_eval/judge.py pages $A --generated pngs/ --out scores.json
# while generating: every run record gets judge_<axis>, judge_overall and judge_version
.venv/bin/python scripts/svg_template_eval/run_eval.py run ... --judge-references refs/ \
    --judge-anchor-high ref-009.png,ref-066.png --judge-anchor-low anchors/low.png
```

Options (prefixed `--judge-` on `run_eval.py`): `--anchor-high`, `--anchor-low` (both required), `--no-context`,
`--ref-pages`, `--no-measure`, `--body-top`, `--body-bottom`, `--model` (default `claude-sonnet-5-5`), `--effort`
(default `low`; `medium` gave the same answers in calibration), `--temperature` (only for a model that accepts one; the
default model rejects a non-default value, so it is left unset), `--no-fallbacks` (by default a declined request may
be served by another model; the result lists `served_models` and warns when they differ, because those pages are
not comparable), `--workers`, and on `deck` `--renders <dir>` to keep the page PNGs. Each result records the
settings, per-page evidence, scores, reasons and `blank_share`, token usage and `cost_estimate_usd`. Ten pages cost
about $0.09 to $0.11 with the default model (four anchor and context images, cached after the first page).

### Calibration (version 2, 2026-11-04)

Ten generated pages of one framed template (seven pages that each repeat one component kind, three bullet-text
fallback pages) were scored by a person against the original deck (1 = unrelated or broken, 5 = fits the original
deck), and five held-out original pages that are neither anchors nor context were added. None of these pages is an
anchor. Settings: default model, effort `low`, anchors `ref-009` and `ref-066` (score 5) and a `low-anchor` page
(score 1), a contact sheet of eight reference pages as context. Two judgements of the ten pages:

| page | human | version 1 | version 2, run 1 | version 2, run 2 |
|---|---|---|---|---|
| 1 | 2.5 | 4.0 | 2.4 | 2.4 |
| 2 | 2.0 | 3.6 | 2.6 | 2.8 |
| 3 | 2.5 | 3.0 | 2.4 | 2.4 |
| 4 | 2.0 | 3.4 | 2.4 | 2.4 |
| 5 | 2.0 | 3.6 | 2.4 | 2.2 |
| 6 | 2.5 | 4.0 | 2.4 | 2.6 |
| 7 | 2.5 | 3.2 | 2.6 | 2.6 |
| 8 | 1.0 | 2.0 | 1.4 | 1.2 |
| 9 | 1.0 | 2.0 | 1.2 | 1.2 |
| 10 | 1.0 | 2.0 | 1.2 | 1.2 |

| | target | version 1 | version 2 run 1 | version 2 run 2 |
|---|---|---|---|---|
| mean (human 1.9) | | 3.2 | 2.1 | 2.1 |
| Spearman with human | >= 0.7 | 0.69 | 0.70 | 0.74 |
| Pearson with human | >= 0.7 | 0.88 | 0.93 | 0.92 |
| mean absolute error | <= 0.5 | 1.18 | 0.26 | 0.24 |
| held-out originals, mean of 5 | >= 4.3 | 4.7 (v1 run) | 4.44 (4.0 to 4.8) | |
| largest per-page spread, run 1 vs run 2 | <= 0.3 | | 0.2 | |

Version 2 fixes the level (fallback pages about 1.2, generated pages 2.2 to 2.8, originals 4.0 to 4.8), but it barely
orders the generated pages among themselves: the human split them into 2.0 and 2.5, and the judge's order within that
group does not follow it (page 2, many components in nearly empty cards, comes out highest). Spearman is held up by
the three levels and sits right at the target. Treat a difference of less than about half a point between generated
pages as noise. The calibration took five iterations (rubric wording, effort, the measured blank share, the weights) and about $0.84.

## Inputs

`inputs/structured_proposal_ko.txt` is a fictional proposal that states its sequences (numbered stages), tables
(Markdown), layers and numbers, so a rule that draws flows or tables has something to draw from. Flattened text
extracted from a real deck loses the sequences, which makes a connector rule look useless; do not judge one on it.

`inputs/unstructured_report_ko.txt` is a fictional long running-prose report with no headings or lists (its sequences,
comparisons and figures are implicit, so the outline step has to structure them), and `inputs/data_report_ko.txt` is a
fictional data-heavy report (monthly, quarterly and per-plant series, two plain-text tables, KPIs). All three have a
10-page target. `inputs/dense_proposal_ko.txt` is a fictional public-sector IT operations proposal with 2 to 3 sub-sections per page (tables, staged processes, an organisation hierarchy, monthly series, SLA levels, dated schedules, KPIs) at about 700 characters per page, because the other inputs carry 200 to 400 and cannot show whether a design approaches the density of the original proposal decks.

`inputs/requests/<input>.json` is the matching `generate/async` body without a template pin or canvas;
`request_builder.py` adds both (and an optional frame map) per template, with `inputs/<input>.txt` as the source of the
`content` field (a unit test keeps the two equal).

## Judging a change across inputs and templates (`matrix`, `decide`)

One input and N=3 unpaired runs cannot tell a change from noise: the same input moves by whole fallback pages between
runs and the judge moves about 0.2 per axis between calls. So a change is measured on a matrix of cells (input x
template), the same cells for both variants, and compared per cell.

A template manifest is a JSON file (relative paths resolve against it):
`{"id": "ministry", "pin": "pin.json", "canvas_emu": [w, h], "frame_map": "frame_map.json"}` (`pin` and `frame_map`
may also be inline objects; `canvas_emu` and `frame_map` are optional).

A manifest may also carry the template's own judge settings, because references and anchors belong to one template:
`"judge": {"references": "refs/", "anchor_high": ["ref-009.png", "ref-066.png"], "anchor_low": "anchors/low.png"}`
(paths relative to the manifest; `anchor_high` and `anchor_low` take a path or a list). `matrix` judges a cell with its
template's `judge` block where it gives a key and with the global `--judge-references`, `--judge-anchor-high`,
`--judge-anchor-low` flags for a key it omits; a template without a `judge` block uses the flags alone, and a cell with no
references from either source is not judged. Every judged run record carries `judge_anchors` (references, anchor names
and, per key, whether it came from the `template` or the `global` flags), and the result file lists
`judge_settings_by_template`. Compare variants only with the same manifests. `--dry-run` prints the selection per cell.

```bash
# variant A (backend as is), then restart the backend with the change and run B: same cells, same --runs, same judge
.venv/bin/python scripts/svg_template_eval/run_eval.py matrix --templates tpl-a.json tpl-b.json \
    --inputs structured_proposal_ko,unstructured_report_ko,data_report_ko --runs 3 --label A \
    --pptx --judge-references refs/ --out results/        # add --dry-run to print the requests only
.venv/bin/python scripts/svg_template_eval/run_eval.py matrix ... --label B --out results/
.venv/bin/python scripts/svg_template_eval/run_eval.py decide results/A.json results/B.json [--json-out report.json]
```

Every run record carries its `cell` (`<input>__<template id>`). `decide`:

1. reports every recorded attempt and its outcome, then averages available completed-deck scores within explicitly matched cells;
2. takes B - A per cell, which cancels the large differences between inputs and templates that unpaired means would carry
   into the comparison (a page-heavy input has more fallbacks for both variants);
3. takes the overall difference as the mean of the cell differences, so every cell weighs the same whatever its run count;
4. names a verdict only when `|overall| > threshold` **and** more than half of the cells have the overall's sign
   (`better` / `worse` after the metric's direction; `no difference` below the threshold; `inconsistent` when the mean
   comes from a minority of cells). Metrics without a rule are `report only`.

| Metric | Threshold | Better is | Why |
|---|---|---|---|
| `judge_<axis>`, `judge_overall` | 0.2 | higher | the judge's own movement between calls of the same deck |
| `fallback_rate` | 0.05 | lower | fallback pages divided by actual measured pages, pooled within each cell |
| `first_try_ok_rate` | 0.05 | higher | accepted first drafts divided by actual measured pages, pooled within each cell |
| everything else (`tables`, `flow_shapes`, `words_kept`, ...) | none | | counts of what exists, not a quality |

The thresholds are a floor against noise, not a significance test: with few cells (three inputs x one template) a
verdict is a strong hint to look at the pages, not proof. Use as many cells as the budget allows, keep the judge model,
effort and references fixed between A and B, and use a fresh database per batch (see above).

## Sized inputs (2026-11-06)

`inputs/requests/*_sized.json` are the four inputs with `n_slides` set to one body slide per 650 source characters
(whitespace excluded) plus the cover: data 5, dense 9, structured 4, unstructured 6. The 10-slide requests spread
about 386 characters over a page where the real template carries about 816, which caps the judge's density near 2.5
whatever the change (docs/template-a-visuals-progress-20261105.md). Measure density and visuals changes with the
sized set (`--inputs data_report_ko_sized,dense_proposal_ko_sized,structured_proposal_ko_sized,unstructured_report_ko_sized`);
results of the two sets are not comparable. A matrix without `--inputs` now runs both sets.

## Result integrity (schema 2)

The existing `runs`, `summary`, `completed_runs` and `requested_runs` fields remain readable.
`summary` describes completed decks only; `accounting` separately exposes all recorded attempts, failures,
unknown outcomes, authorized retries, completion rates and page-rate numerators/denominators. Missing checkpoints,
exports or judge observations remain unavailable. `compare` and `decide` expose comparison coverage and block a
global improvement claim when completion regresses or evidence is incomplete. Their threshold rule is a descriptive
heuristic, without statistical confidence. Raw first-draft/fallback counts and numeric-token retention are descriptive.
Structured category/value/unit/series evidence is compared only when supplied; no prose or geometry proof is inferred.
Template phrase matches are leakage candidates; a zero count does not prove absence of leakage.

Every dispatched attempt is saved before the POST, and its task ID is saved before polling. Generation completion
is saved before measurement. `matrix --resume` retains every prior record and fills only unattempted slots;
`--resume --retry-failed` explicitly authorizes failed-slot replacements and links each retry with `retry_of`.
Unknown outcomes block the cell until reconciled. `recover` retains existing records and every exact-request-matched
durable job, including failed jobs and decks with missing PPTX exports; it never dispatches a generation.
Without prior saved records, pre-job rejection/transport history cannot be reconstructed and is reported as unavailable.
Recovery does not infer historical runtime identity from the current invocation or relaunch failed measurements.

`--identity-manifest` on `run`/`matrix` accepts sanitized, operator-verified identities as
`{"default": {...}, "cells": {"<cell>": {...}}}`. Each identity can contain `code_revision`,
`generation_config_sha256`, `policy_revision`, `policy_sha256`, `input_sha256`, `request_sha256` and
`template_pin_sha256`. The first four describe the actual backend/admitted policy and are stored on each run as
`evaluation_identity`; they are never inferred from the harness checkout or live environment. The four condition
digests are stored as `condition_identity`. Matrix fixture/source/request/template digests are computed locally only
for repository-owned fictional inputs; arbitrary request/customer text and credentials are not hashed or stored.
For arbitrary `run` requests, the operator supplies their sanitized source/request/pin digests. Missing runtime or
condition identity blocks comparison verdicts, including for legacy files. Runtime code/policy revisions may differ
between A and B; generation configuration must match explicitly. Judge comparison additionally binds actual settings,
image-byte hashes, implementation identity, version and served models; mixed/fallback served-model evidence blocks it.
`runner_identity` hashes evaluator sources only and makes no claim that backend services loaded them.

The content-sizing metric is supplied by the separate `content_sizing_metrics.svg_content_sizing` module;
its per-SVG hook is reserved for root integration, retaining availability, units and body-bound provenance.
