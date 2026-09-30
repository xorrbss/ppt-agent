# Canvas bench: pooled candidate tier for 1:1 and 21:9 (2026-09-29)

Third live run of the canvas bench, and the first under the pooled decision rule
(`docs/canvas-bench-decision-rule-design-20260929.md`). Earlier runs:
`docs/canvas-bench-pilot-20260929.md`, `docs/canvas-bench-square-ultrawide-20260929.md`.

**Outcome:** both `square-1x1` and `ultrawide-21x9` earned `candidate` for style `default`.
Visual review found no broken slides. `promote` was run for both, so `constants/canvas_stages.json`
now reads `{"1:1": {"default": "candidate"}, "21:9": {"default": "candidate"}}`. `verified` is on
`hold` (insufficient sample) for both.

## Setup

- **Backend:** isolated, pinned to `e8475ee6ff9b64c6405708686e167a5d5713bd86` (contains
  `e8475ee6f`, the svg text_overlap glyph-ink fix, and `806219dfa`, background Mem0 writes and
  outline sub-span logging). The backend ran from a detached worktree, with the API on `:8091` and
  its own generation and export controllers. In that worktree only, `canvas_stages.json` set
  `default` to `candidate` for 1:1 and 21:9 so the requests were admitted. That edit was never
  committed.
- **Database:** a disposable PostgreSQL 15 cluster on `:22600`, set up with the recipe from the
  earlier runs (`scripts.provision_public_v2_local_db_roles`, then
  `scripts.sync_existing_provider_settings`). Provider settings were verified in the database after
  the import and again after the API started: 16 keys, `LLM=codex`,
  `IMAGE_PROVIDER=codex_image`. The env came from the shared DPAPI store, with the DB, app-data,
  artifact, staging, capability-key and port paths replaced. `EXPORT_RUNTIME_DIR` pointed at the
  repo-root `presentation-export`, and Next.js rendering used the shared `:5000`.
- **Matrix:** one `matrix` invocation (serial, seed 20260929, interleaved blocks), resumed twice
  (see below). Settings: style `default`, product-default strategy (resolved to `svg`), 6 slides,
  manuscripts 01 to 03 (`--limit 3`), `wide-16x9=18`, `square-1x1=12`, `ultrawide-21x9=12`,
  `--ink-fix-commit e8475ee6f`, batch `20260929`.
- **Metric epoch:** `svg-overlap-ink-v1` on every deck. It was resolved through the ancestry path.
  `overlapBasis` was not found in the operation result (see "Bench findings").
- Artifacts (gitignored): `artifacts/canvas-bench-20260929/pooled-candidate/`. They include the
  per-deck receipts, `deck.pptx` files, `slides/` (PPTX renders), `svg/` (stored-SVG renders),
  `compare/`, the `contact-sheet-svg.png` and `contact-sheet/` for each canvas, and
  `backend-logs/`.

## Results

All 42 planned decks completed. Every generation succeeded, every export succeeded, and every PPTX
`p:sldSz` matched the registry (16:9 12192000 x 6858000, 1:1 6858000 x 6858000, 21:9 16002000 x
6858000).

| canvas | decks (completed / infra_lost / failed) | pages | flagged pages | flag rate | retries per slide | s per slide, mean (sd) | client wall, median s |
|---|---|---|---|---|---|---|---|
| 16:9 (baseline) | 18 / 0 / 0 | 108 | 2 | 0.019 | 0.065 | 53.4 (8.4) | 329.7 |
| 1:1 | 12 / 0 / 0 | 72 | 1 | 0.014 | 0.028 | 46.7 (11.4) | 273.6 |
| 21:9 | 12 / 0 / 0 | 72 | 1 | 0.014 | 0.042 | 46.8 (4.1) | 284.3 |

One 1:1 deck (`01-smart-factory-rollout-r04`) was first lost to the environment and later rerun.
Its receipt keeps the record under `previousInvalid`. One 21:9 deck (`03-retail-loyalty-renewal-r01`)
was recovered from its finished task after a bench-client race (see "Bench findings"). No deck
failed for a product reason.

**All four flagged pages are `required_text_missing`. None are `text_overlap`.** They are 1:1
`03-r03` s04, 21:9 `03-r04` s04, 16:9 `01-r01` s05 and 16:9 `03-r06` s04. Three of the four come
from manuscript 03, slide 4 or 5, which is the budget and investment slide. Under the line-box
epoch the 16:9 page flag rate was 0.27 (8/30), and it is now 0.019. The ink-box fix removed
essentially all of the false-positive overlap flags. Every page's `overlap_basis.pairs` is empty.

## Compare (epoch `svg-overlap-ink-v1`, pooled, one-sided 95%, cluster-adjusted)

| candidate | tier | page flag rate D [lower, upper] (margin) | retries per slide D [lower, upper] | s per slide D [lower, upper] (margin) | verdict |
|---|---|---|---|---|---|
| 1:1 | candidate | -0.005 [-0.060, +0.069] (0.20) | -0.037 [-0.087, +0.013] | -6.70 [-13.03, -0.37] (8.02) | **pass** |
| 1:1 | verified | same (0.15) | same | same | **hold**: candidate decks 12/24, baseline 18/24, manuscripts 3/4, calendar days 1/2 |
| 21:9 | candidate | -0.005 [-0.060, +0.069] (0.20) | -0.023 [-0.076, +0.030] | -6.67 [-10.46, -2.88] (8.02) | **pass** |
| 21:9 | verified | same (0.15) | same | same | **hold**: same shortfall |

Earned stage for both is `candidate`. The compare outputs are in
`pooled-candidate/compare/compare-{square-1x1,ultrawide-21x9}-vs-wide-16x9.{json,md}`. Nothing was
excluded: there were no invalid decks and no other-epoch decks in the pool.

**Seconds per slide are confounded by time drift.** 16:9 had 18 decks against 12, so its repeats
r05 and r06 ran as an unpaired tail at the end of the run (15:00 to 15:32 KST). Those 6 decks
averaged 60.1 s per slide. The 12 interleaved 16:9 decks (r01 to r04) averaged 50.1 s, against
46.7 (1:1) and 46.8 (21:9). The real gap is about 3.4 s per slide, not 6.7. The gate passes either
way, because the candidates are not slower.

## Visual review

I reviewed the per-canvas contact sheets of the stored SVG renders and a sample of full-size
renders, both SVG and hi-res LibreOffice renders of the exported PPTX.

- **1:1** (`square-1x1/contact-sheet-svg.png`): every slide fills the square. Text is legible, and
  no clipping or overflow is visible. Covers and sparse closing slides leave a band of about 10 to
  20% empty at the bottom, which is a layout choice. Examples:
  `square-1x1/03-retail-loyalty-renewal-r03/svg/s04.png` (the flagged page, which renders cleanly)
  and `square-1x1/01-smart-factory-rollout-r04/svg/s05.png` with its PPTX render
  `.../slides-hi/s05.png` (KPI pair, schedule block, risk table, all within bounds).
- **21:9** (`ultrawide-21x9/contact-sheet-svg.png`): the slides use the width with 2- and
  3-column layouts, full-width titles and callout bars, and no clipping. Examples:
  `ultrawide-21x9/02-hybrid-work-policy-r02/svg/s05.png` (3 columns plus a decision bar) with its
  PPTX render `.../slides-hi/s02.png`, and `ultrawide-21x9/03-retail-loyalty-renewal-r04/svg/s04.png`
  (the flagged page, which is clean). Body text is about 15 to 17 px on a 1680 x 720 canvas, the
  same absolute size as on 16:9. It gets smaller relative to the viewer only when a 21:9 slide is
  letterboxed onto a 16:9 screen.
- **Minor authoring wart, not a canvas defect:** in 1:1 `01-r04` s05, the sentence "합계 … 36개월
  로," is set as separate runs with a wide gap before "36개월". It looks the same in the SVG source and
  in the PPTX, so it is model layout and not an export or canvas issue.

No slide was visibly broken on either candidate canvas, so the statistical pass stands.

## Outline sub-spans (`svg_outline_span`, 42 decks)

The timing receipt's `outline` stage has a median of 166 s (124 to 269), which is 58% of deck
wall time (median 285 s). The new sub-span lines break it down:

| sub-span | median | range |
|---|---|---|
| `content_brief_ms` | 82.7 s | 53.7 to 127.1 s |
| `plan_llm_ms` | 49.0 s | 32.5 to 81.2 s |
| `outline_total_ms` | 33.0 s | 19.9 to 75.0 s |
| `prepare_local_ms` | 1.3 s | 0.8 to 18.5 s |
| `mem0_pre_ms`, `job_semaphore_wait_ms`, `icon_sheets_ms`, `outline_post_ms` | 0 | 0 to 0.2 s |

The content brief is about half of the outline stage and the single largest cost before
authoring. The plan LLM call comes second. Mem0 now costs nothing on the critical path. The
figures do not differ by canvas (1:1 `outline_total_ms` median 25 s against 34 to 35 s for the
other two, all other spans within noise).

## Bench findings

1. **Poll race: `completed` before the export annotation (bench client, fixed).**
   `record_generation_completion` commits `status=completed` with `export_operation_id: null`
   before the chained export annotates the task. It also commits before the generation operation
   publishes its result, including the timing receipt. `BenchClient.generate` returned on the
   first `completed`. As a result, 21:9 `03-r01` was recorded as `export_not_chained` with no
   timing, which the rule would have counted as a hard-gate export failure. The export had in fact
   been chained (`d3e77b16…`) and succeeded. Fix in `scripts/canvas_bench_live.py`:
   `BenchClient._settle` keeps polling for up to 120 s until `export_operation_id` or
   `export_error` is non-null and the generation operation card is terminal. It has two unit
   tests. The deck was recovered through the unmodified `run_one` path. Its receipt carries
   `recoveredFromTask`, and the raced original is kept in `backend-logs/`. The runner was restarted
   with the fix after deck 9, and no further race occurred. This is the same race as the
   2026-09-26 harness note, and it affects any client that stops at the first `completed`.
2. **`overlapBasis` never reaches receipts.** The backend records `overlap_basis` per page as an
   object (`{"version": "glyph-ink/v1", "fragments": {...}, "pairs": [...]}`) in
   `slides[].content.svgReceipt.quality` and `theme.svg.quality[]` of `GET /presentation/{id}`.
   It is not in the generation operation's published result on the timeline, and
   `canvas_bench_epoch.find_overlap_basis` accepts only a string. So `overlapBasis` is `null` on
   all 42 receipts, and the epoch came from the `--ink-fix-commit` ancestry path, as the design
   anticipated. To make path (1) work, the finder would need to read the presentation's svg quality
   and map `version` (for example `glyph-ink/v1`) to the ink epoch. That change was not made.
3. **Machine standby is an infra loss that the runner cannot see.** The machine entered Modern
   Standby from 13:18:51 to 14:12:00 KST (Kernel-Power 506/507, following a power-source change).
   1:1 `01-r04` was in its content-brief call at the time and finished with a 3,396 s wall
   (565 s per slide), and the backend logged the 600 s content-brief timeout as elapsed after
   3,226 s. No transport error occurred, so the runner recorded it as a normal deck. It was marked
   `invalidate --note …` (`infra_lost`) by hand and rerun on resume. For the rest of the run the
   session held a process-scoped `SetThreadExecutionState(ES_SYSTEM_REQUIRED)` request, which was
   released at the end. A wall-time outlier check, or a standby/resume check, in the runner would
   catch this automatically.

## Caveats

- One batch (one calendar day), one style, 6 slides, 3 manuscripts. `verified` needs 24 decks per
  side, 4 manuscripts and 2 days.
- With the ink fix, the baseline flag rate is about 0.02, so the 0.20 and 0.15 margins are now
  very loose relative to the baseline. Design open decision 3 (re-deriving margins from the new
  baseline rate) should be settled before the `verified` tier is run.
- The 16:9 unpaired tail inflates the seconds difference (see Compare).
- `gate.flags.flaggedSlides` is still empty in receipts. Per-page classifications were read from
  the live backend for this report.
- The shared `:5000` Next.js served rendering, as in the earlier runs.

## Changes committed with this report

- `servers/fastapi/constants/canvas_stages.json`: 1:1 and 21:9 `default` set to `candidate`
  (written by `promote`).
- `servers/fastapi/tests/fixtures/canvas_admission_baseline.json`: regenerated with
  `scripts/canvas_admission_table.py --write`. The only changes are the 204 rows for 1:1 and 21:9
  under `default` and `no-such-style`, which falls back to `default`. They move from `422
  authored_canvas_stage_disabled` to `admitted` (66) or to the next rejection in line,
  `authored_portrait_combination_not_supported` (138).
- `servers/fastapi/tests/unit/test_canvas_stage_gate.py`: the shipped-file test now asserts the
  promoted stages, and the disabled-stage test installs a disabled stage file instead of relying
  on the shipped one.
- `servers/fastapi/scripts/canvas_bench_live.py` plus two tests in
  `tests/unit/test_canvas_bench.py`: the poll-race fix.
