# Server-chart evaluation harness

Runs REAL-provider (Anthropic `claude-sonnet-5-5`) svg deck generations plus PPTX export and collects
chart metrics, to compare server-rendered charts (`SVG_SERVER_CHARTS=on`) against author-drawn charts.
It reuses `scripts/fake_llm_e2e/run_backend.sh` (worker dispatcher, local artifact store, pricing gate)
with `FAKE_LLM_CLIENTS=0`, and the generic collectors of `scripts/svg_live_measurement/collect_run.py`.
Svg export runs in the isolated export worker; the Next.js server is not needed.

## Prerequisites

```bash
cd servers/fastapi && uv sync --frozen          # backend uses servers/fastapi/.venv
export PRESENTON_ANTHROPIC_API_KEY=...           # reaches the backend process only; never printed or written
```

Chromium must be available to the backend as for any svg run (preinstalled on the cloud VM).

## Files

| File | Purpose |
|---|---|
| `run_backend_real.sh <app-data-dir>` | Real-provider backend on `$PORT` (default 8000). Knobs pass through env: `SVG_SERVER_CHARTS`, `SVG_NATIVE_CHART_INTEGRITY_V5` |
| `run_matrix.py` | prompts x arms x runs; one fresh backend + app-data dir per deck; `--resume`, `--max-cost-usd` |
| `metrics.py` | per-deck `metrics.json` and the per-arm summary table (also usable on its own) |
| `prompts.json` | 3 chart-heavy Korean prompts, 7 slides each (business review, consumer survey, library statistics) |
| `arms.json` | arm name -> env knob set: `server`, `author`, `author-v5`, `server-v5` |

## Usage

```bash
cd servers/fastapi
python3 scripts/server_chart_eval/run_matrix.py --plan --arms server,author --runs 3 --out /var/tmp/server-chart-eval/m1
python3 scripts/server_chart_eval/run_matrix.py --out /var/tmp/server-chart-eval/m1 \
    --arms server,author --runs 3 --max-cost-usd 60 --resume
python3 scripts/server_chart_eval/metrics.py --summarize /var/tmp/server-chart-eval/m1   # table again
python3 scripts/server_chart_eval/metrics.py /var/tmp/server-chart-eval/m1/<cell>        # recompute one deck
```

Useful flags: `--only-prompts biz-review,library-stats`, `--n-slides 6` (override), `--per-deck-cap-usd`,
`--first-deck-estimate-usd`, `--port`, `--timeout` (generation), `--export-timeout` (export + V5 chart
verification), `--retry-failed` (with `--resume`), `--fake` (fake LLM: plumbing only, no key, no cost).
Run from any directory; the matrix directory should be outside the repo.

### Design decisions

- **One backend per deck, fresh app-data dir.** Admission freezes the knobs into the deck's style snapshot,
  so an arm needs a restart anyway; a fresh DB also keeps every metric (provider calls, operations,
  PPTX under `public-artifacts/`) attributable to exactly one deck. Cost: ~20-40 s backend start per deck.
- **Arm order rotates** per (run, prompt) so no arm always runs first or on a warm machine.
- **Cost ceiling.** Before each deck: stop if `spent + estimate > --max-cost-usd`, with `estimate` = the largest
  completed deck cost so far (`--first-deck-estimate-usd`, default 1.5, before any). In flight a deck is
  killed (`cost_cap_abort`) above `min(--per-deck-cap-usd, remaining budget)`, polling the logical-call
  ledger every 3 s. Resumed decks count toward the ceiling.
- **Resume.** A cell directory with `metrics.json` is finished (a failed deck too, unless `--retry-failed`).
  `metrics.json` is written after every deck.
- **V5 arms** wait for the export plus the `svg.chart.verify` rounds and their chained refreshed export
  (each round up to 600 s, at most 5; bounded by `--export-timeout`, default 1800 s) and count a quiet
  period of 25 s.

### Pricing is an assumption

`run_backend_real.sh` registers `claude-sonnet-5-5` at an ASSUMED $3 / $15 per million input / output
tokens (cached read $0.30, cache write $3.75) under pricing revision `server-chart-eval-assumed-20261109`.
The pricing gate requires a priced model; all `cost_usd` figures are only as accurate as these numbers.
(`svg_live_measurement` uses $2 / $10; costs of the two kits are not comparable.)

## What a deck directory holds

`body.json`, `arm_env.json`, `admission.json`, `status.json`, `backend.log` (provider errors are logged
with `[provider-error]`), `app/` (DB and artifacts), `deck.pptx` (newest export), `svg/sNN.svg`
(0-based, final slide SVGs), `generation_timing_receipt.json`, `summary.json` (generic collector output),
`metrics.json`.

## `metrics.json` fields

Generic: `cost_usd`, `tokens`, `stages` (per-stage calls/cost/latency), `retry_calls`, `failed_calls`,
`wall_s_generate` (operation started->completed), `wall_s_client`, `wall_s_cell` (incl. backend start and
follow-up wait), `followup_wait_s`, `queue_wait_s`, `stage_wall_s`, `gate`, `flagged_slides`,
`first_attempt_failures`, `repairs`, `grammar_400`, `provider_errors`.

Chart view (`charts`, with `chart_totals` hoisted):

- `snapshot`: frozen `server_chart_render` and `native_requirements_revision` (proves the arm took effect).
- `totals.declared_final`: chart objects in the published slides' `content.nativeObjects`; `server` vs
  `author` = drawn by the server renderer (bound declaration carries a `render` marker) vs bound as a native
  chart the author drew. **Charts the author draws as plain shapes without a declaration are not visible
  here** (in the author arm under V4 this is the usual case: `declared_final` is 0 even though the slide
  shows a chart); compare them through the PPTX counts and the PNG/SVG, not these.
- `totals.dropped_in_attempts` / `dropped_by_reason`: declarations dropped at author time with the reason
  (published record and every attempt's receipt; attempt-level drops are prefixed `(attempt)`). A server chart
  that cannot be drawn is dropped with a `server_chart_*` reason; a grounding failure instead surfaces as the
  failure kind `native_requested_chart_missing:server_chart_value_ungrounded`.
- `totals.fallback_pages`: pages whose published SVG is the `svg-fallback-page` (attempts exhausted).
  `flagged_published_pages`: pages published with a failing last attempt (`remakeOutcome`
  `verbatim_flag_only`, ...). Per page `outcome` is `clean`, `svg_fallback_page` or the `remakeOutcome`.
- `first_attempt_failure_kinds`, `all_attempt_failure_kinds`, `published_failure_kinds`: counters of
  quality failure kinds (`server_chart_occluded`, `undeclared_chart_drawn`, `native_requested_chart_missing:<reason>`,
  `text_overlap`, ...). Per page the same lists per attempt (`attempt_failure_kinds`).
- `pptx`: native chart graphic frames per slide (1-based) and by chart type, and the `chart_parts` count;
  `totals.exported_as_drawing` = declared charts that did not become a native PPTX chart.
- `v5` (per page, and `totals.v5_gated` / `v5_paint_proven`): author-drawn native charts that need paint
  evidence and those the sealed `svgNativeChartPaint` evidence proved. Server (v3) charts are trusted and
  not gated. `ops`: every `export.pptx` and `svg.chart.verify` operation with state and seconds.

## Validation record (2026-11-09, 6 slides, biz-review prompt)

| arm | cost | generate | export | native charts declared / in PPTX | fallback pages | flagged-published |
|---|---:|---:|---:|---|---:|---:|
| server | $1.52 | 172 s | 5 s | 2 server / 2 bar | 2 | 1 |
| author | $1.97 | 207 s | 4 s | 0 / 0 | 1 | 3 |

One deck per arm is an existence proof of the plumbing, not a comparison.
