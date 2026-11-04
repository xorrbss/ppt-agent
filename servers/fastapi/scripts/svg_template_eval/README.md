# SVG template evaluation

Judge a change to SVG template generation on several runs of a fixed input, never on one: the same
input gave 3 to 7 fallback pages in a row. Three scripts, none of them part of the product:

| Script | Does |
|---|---|
| `register_template.py` | Registers a PPTX as a published template through the API and prints its pin (upload, snapshot, framed registration, automatic font substitution, publish). Takes 5 to 8 minutes on a 100-page deck |
| `run_eval.py run` | Sends one generation request N times and measures each deck (below) |
| `run_eval.py compare` | Prints two result files side by side |

`metrics.py` holds the pure metrics (unit-tested in `tests/unit/test_svg_template_eval_metrics.py`).

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

These are counts of what exists, not a judgment of how good it looks. Pair them with a look at the pages.

## Inputs

`inputs/structured_proposal_ko.txt` is a fictional proposal that states its sequences (numbered stages), tables
(Markdown), layers and numbers, so a rule that draws flows or tables has something to draw from. Flattened text
extracted from a real deck loses the sequences, which makes a connector rule look useless; do not judge one on it.
