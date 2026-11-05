# SVG live measurement kit

Paired, budget-capped live runs of the default svg pipeline for the measurements in
`docs/svg-mode-improvement-plan-20261105.md` section 5. One deck = one throwaway backend started
through `scripts/fake_llm_e2e/run_backend.sh` in real-provider mode (`FAKE_LLM_CLIENTS=0`,
`claude-sonnet-5-5`, the `anthropic-55-20261102` pricing catalog), a fresh app-data directory,
one `/generate/async` request with chained PPTX export, then collectors, slide PNGs and a summary.
Nothing here touches product code; everything is stdlib Python except the PNG step.

## Prerequisites

```bash
cd servers/fastapi && uv sync --frozen            # the harness uses .venv/bin/python
export PRESENTON_ANTHROPIC_API_KEY=...            # real runs only; reaches the backend process only,
                                                  # never printed, logged or written
```

PNG rendering runs `uv run --no-project --with playwright==1.52.0 python render_pngs.py` (override
the version with `SVG_MEASURE_PLAYWRIGHT`). It drives the system Chrome (`CHROME_PATH`,
`google-chrome` or `chromium`); without one, Playwright's own Chromium must be installed
(`playwright install chromium`). If the PNG step fails the summary records why and the run is still
valid. A port must be free (`--port`, default 8732 for the driver, 8731 for `run_deck.py`); a
measurement takes the machine's load into account, so the summary stores `uptime` before and after
each deck -- do not run anything else heavy meanwhile.

## Files

| File | Purpose |
|---|---|
| `run_deck.py` | One deck: start backend (pid file, new session), `uptime` before/after, admit, poll, in-flight cost watchdog, wait for export, stop backend, collect, render PNGs |
| `paired_run.py` | Preset driver: arm order, per-deck and cumulative caps, resume, then `blind.py` and `summarize.py` |
| `presets.json` | `baseline`, `p8`, `s3` (arm = backend env + `unset` + request-body overrides; `null` deletes a body key) |
| `bodies/p1.json`, `bodies/p2.json` | The two reference requests: p1 = "중소기업을 위한 생성형 AI 도입 전략..." 6 slides `exec-report`; p2 = "2026년 국내 전기차 시장 분석..." with the figures, 6 slides, default style |
| `collect_run.py` | Reads the harness DB, backend log, receipt and PPTX into `summary.json` |
| `render_pngs.py` | SVG to `slideN.png` through Playwright |
| `blind.py` | Shuffled `blind/<code>.png`, `blind_key.json`, `ratings.csv` |
| `summarize.py` | Per-deck table, per-arm medians, the plan's decision rule |

## Commands and budgets

All commands run from `servers/fastapi`. Output goes where you point `--out` (outside the repo).
`--plan` prints the order and caps and runs nothing.

| Preset | Command | Decks | Caps (deck / total) | Needs |
|---|---|---:|---|---|
| `baseline` | `python3 scripts/svg_live_measurement/paired_run.py --preset baseline --out /var/tmp/svg-live/baseline` | 2 prompts x 3 = 6 | $2.50 / $16 | main as is |
| `p8` | `... --preset p8 --out /var/tmp/svg-live/p8` | 2 prompts x 2 arms x 2 = 8 | $2.50 / $11 | **branch `feat/svg-p8-reduced-header-default` merged** |
| `s3` | `... --preset s3 --out /var/tmp/svg-live/s3` | 2 prompts x 2 arms x 2 = 8 | $2.50 / $11 | **branch `feat/svg-s3-prompt-profile-v10` merged** |

Typical deck cost is about $1.2 for 6 slides (handoff `docs/handoff-svg-quality-20261103.md`), so the
caps leave room for roughly one retry-heavy deck. Flags: `--repeats N`, `--prompts p1,p2`,
`--per-deck-cap USD`, `--total-cap USD`, `--first N` (skip runs below N), `--port`,
`--body-override '{"k":v}'`, `--fake`. A finished run (`runs/NN-.../summary.json`) is skipped, so
rerunning the same command resumes after an interruption or a cap stop.

Caps: `run_deck.py` polls the logical-call cost every 3 s and kills the deck (state
`cost_cap_abort`) above `min(per-deck cap, remaining total)`; after each deck the driver stops
when the deck passed the per-deck cap, the total reached the cap, or a deck could not start.

Arms:

- **baseline**: one arm `main`, current defaults (body names no header mode, no env).
- **p8**: `A` = `common_header_mode: uniform`, `common_header_cover_exception: false`, and
  `SVG_COMMON_HEADER_DEFAULT` unset (main behaviour). `C` = body without a header mode and
  `SVG_COMMON_HEADER_DEFAULT=reduced`. **Meaningful only once `feat/svg-p8-reduced-header-default`
  is merged**: before that the knob is unknown to the backend and A and C are the same
  configuration. The UI always sends an explicit mode, so a server-default flip alone does not
  change UI-created decks (see the P8 notes in the plan).
- **s3**: `9` vs `10` through `SVG_AUTHORING_PROFILE_REVISION`, which makes fresh admissions freeze
  that revision. **Only after `feat/svg-s3-prompt-profile-v10` is merged**; revision 10 does not
  exist before. The summary stores `authoring_profile` per deck so a wrong arm is visible.

Arm order is repeat-major with prompts alternating and the arm order rotating each cell
(`p1 A,C` / `p2 C,A` / `p1 C,A` / `p2 A,C` for two arms).

## Rating and decision

1. `paired_run.py` ends by writing `<out>/blind/*.png`, `blind_key.json` (do not open before rating)
   and `ratings.csv`.
2. Look at `blind/*.png`, fill `ratings.csv` with 1-5 for layout, hierarchy, readability, richness,
   consistency, korean (one rater; Korean text quality counts).
3. `python3 scripts/svg_live_measurement/summarize.py <out>` prints the table and `DECISION:`.

Rules (plan section 5): `p8` is a *performance* rule -- adopt only if median wall time or median cost
improves and neither the gate pass rate nor the fallback page count gets worse, and the mean blind
rating does not drop (without ratings the verdict is `PENDING`). `s3` is a *quality* rule -- adopt
only if the mean blind slide rating rises and first-attempt failures and fallback pages do not rise;
the gate pass rate alone never decides. `baseline` prints statistics only. Medians over n = 2 per
cell are indicative; the plan asks for n >= 3 where the budget allows (`--repeats 3`).

## Single deck

```bash
python3 scripts/svg_live_measurement/run_deck.py --name try1 --out /var/tmp/svg-live/try1 \
    --body scripts/svg_live_measurement/bodies/p1.json --port 8731 --max-cost 2.5 \
    --env SVG_COMMON_HEADER_DEFAULT=reduced --unset OTHER_VAR --body-override '{"common_header_mode":null}'
```

Body paths are resolved to absolute paths; `--out` must not hold a finished run.

## What each run directory holds

`body.json` (effective request), `arm_env.json`, `admission.json`, `status.json`, `backend.log`,
`backend.pid`, `generation_timing_receipt.json`, `svg/sNN.svg` (0-based), `png/slideN.png` (1-based),
`deck.pptx`, `app/` (harness DB and artifacts) and `summary.json` with:

- wall: `wall_s_operation`, `wall_s_receipt`, `wall_s_client`, `queue_wait_s`; per stage
  `stage_wall_s` (elapsed) and `stage_sum_s` (summed across concurrent pages), `observation_s`
  (setup.*, render.*, quality.* spans), `header_stage_s` (header spans/stages when the header ran),
  `outline_spans` (the `svg_outline_span` log line), `export_s`, `uptime_before/after`;
- cost: `cost_usd` (actual, else estimated, microunits), `tokens` (input, output, cached input,
  cache write), per-`stages` calls/cost/latency/tokens, `receipt_calls` (logical, physical, retry
  from the timing receipt), `provider_call_rows`, `retry_calls`, `failed_calls`, `header_calls`;
- `grammar_400` (provider errors naming a grammar rejection; needs real-provider mode) and
  `provider_errors`;
- `repairs`, `first_attempt_failures` and per-page `pages[]` (attempt causes, first validation error);
- gate: `gate` (status, pages, passed, flagged, review pages), `quality_summary`,
  `flagged_slides`, `fallback_pages` (0-based slide indexes of `svg-fallback-page` slides),
  `vision_review` (`qualitySummary.visionReview`), `chart_advisories`, `header`, `authoring_profile`;
- `pptx`: native `charts`, `tables`, `text_boxes`, `slides`.

## Fake-LLM dry run (no key, no cost)

Proves the plumbing only (numbers are meaningless). The fake client cannot answer the designed
header model call, so a dry run needs the free header:

```bash
python3 scripts/svg_live_measurement/paired_run.py --preset baseline --out /var/tmp/svg-live/dry \
    --fake --repeats 1 --prompts p1 --body-override '{"common_header_mode":"free"}'
```

`scripts/fake_llm_e2e/fake_llm_backend.py` now also replaces `utils.llm_config.get_client`; without
that the Anthropic call sites bypass the `llmai.get_client` patch and hit the real API (401).

## Gotchas

- Do not use `pkill -f` to stop a backend (it matches the calling shell on this VM); the runner
  kills the process group from the pid file.
- A provider with no credit fails at admission or the first call (HTTP 400); the driver then stops
  with `STOP` and spends nothing further.
- Other work on the machine changes timings; compare the stored `uptime` load figures before
  trusting a wall-time difference.
