# SVG template evaluation

Judge a change to SVG template generation on several runs of a fixed input, never on one: the same
input gave 3 to 7 fallback pages in a row. Three scripts, none of them part of the product:

| Script | Does |
|---|---|
| `register_template.py` | Registers a PPTX as a published template through the API and prints its pin (upload, snapshot, framed registration, automatic font substitution, publish). Takes 5 to 8 minutes on a 100-page deck |
| `run_eval.py run` | Sends one generation request N times and measures each deck (below) |
| `run_eval.py compare` | Prints two result files side by side |
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

Asks a vision model whether each generated page looks like the template's own pages. Every page gets a score
from 1 to 5 and a one-sentence reason on five axes: **structure** (body composition: diagrams, tables, cards
against plain text), **density** (amount and balance of content), **hierarchy** (title / key message / body
levels), **tone** (fonts, colours, manner of the template) and **visuals** (icons, figures, shapes). A deck
gets the mean of each axis over its pages and `overall`, the mean of the five axis means.

**A judge score is a model's opinion, not a measurement.** It moves with the model, the reference pages and the
prompt, and it varies between calls. Compare only results made with the same model, effort and reference
pages, and judge a change on three or more decks (N >= 3), as for the other metrics. Differences of a few
tenths are noise.

1. **Reference pages** are PNGs of the original template deck, chosen by the operator for each run and kept
   outside the repository (never commit a customer deck or its renders). Pick about eight body pages that show
   the deck's range (a table page, a diagram page, a card page, a chart page), plus a cover or section page if
   the generated decks have one: a page type the references lack scores low on tone and structure. Render the
   deck to PDF with LibreOffice with its fonts installed (a deck's embedded `ppt/fonts/*.fntdata` are EOT
   files; without the real fonts the references show substitutes and the tone axis is judged against them),
   then: `judge.py references --pdf deck.pdf --pages 4,9,19,25,45,58,66,73 --out refs/`.
2. **Which references a page sees** (one rule for every page, so the cached prefix and the basis are the same):
   the files named with `--ref-pages a.png,b.png` one by one; otherwise every PNG of the directory one by one
   when there are at most three; otherwise all of them downscaled into one contact sheet. The choice moves the
   scores (the same deck: 3.46 overall against a sheet of eight, 3.30 against three named pages), so keep it
   fixed across the results you compare.
3. **Judge.** Generated pages are read from `slides.svg_content` and rendered with Playwright's Chromium (Node,
   `require('playwright')`, scale 1). The key comes from `PRESENTON_ANTHROPIC_API_KEY`.

```bash
# stored decks (any presentation ids); the output has the run_eval shape, so run_eval.py compare reads it
.venv/bin/python scripts/svg_template_eval/judge.py deck --references refs/ --dsn "$DSN" \
    --presentation-id <id> --presentation-id <id> --presentation-id <id> --label baseline --out results/
# the same deck several times, to see how much the judge itself moves (judge_repeatability in the output)
.venv/bin/python scripts/svg_template_eval/judge.py deck --references refs/ --presentation-id <id> --repeat 3 ...
# page PNGs from anywhere
.venv/bin/python scripts/svg_template_eval/judge.py pages --references refs/ --generated pngs/ --out scores.json
# while generating: every run record gets judge_<axis> and judge_overall
.venv/bin/python scripts/svg_template_eval/run_eval.py run ... --judge-references refs/
```

Options (prefixed `--judge-` on `run_eval.py`): `--model` (default `claude-sonnet-5-5`), `--effort` (default
`low`), `--temperature` (only for a model that accepts one; the default model rejects a non-default value, so it
is left unset), `--ref-pages`, `--no-fallbacks` (by default a declined request may be served by another model;
the result lists `served_models` and warns when they differ, because those pages are not comparable),
`--workers`, and on `deck` `--renders <dir>` to keep the page PNGs. Each result records the settings, per-page
scores and reasons, token usage and `cost_estimate_usd`. A ten-page deck costs about $0.07 with the default model
(the reference prefix is cached after the first page).

Measured on one ten-page deck against a contact sheet of eight reference pages, three judgements: per-axis deck
means moved by at most 0.2 (overall 3.46, 3.48, 3.54). Held-out pages of the original deck scored 4.7 overall
and a plain black-on-white bullet page 1.2, so the scale has room on both sides of the generated decks (3.4 to 3.5).

## Inputs

`inputs/structured_proposal_ko.txt` is a fictional proposal that states its sequences (numbered stages), tables
(Markdown), layers and numbers, so a rule that draws flows or tables has something to draw from. Flattened text
extracted from a real deck loses the sequences, which makes a connector rule look useless; do not judge one on it.
