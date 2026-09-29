# Canvas bench: 1:1 and 21:9 (2026-09-29)

Second live run of the canvas quality bench (`servers/fastapi/scripts/canvas_bench.py`), after the
pilot in `docs/canvas-bench-pilot-20260929.md`. It measures the two other gated canvases,
`square-1x1` and `ultrawide-21x9`, against `wide-16x9`, to decide their exposure in
`constants/canvas_stages.json`. Nothing was promoted: every measured compare failed or was not
run, and `canvas_stages.json` is unchanged in the repository.

## Setup

- **Backend:** isolated, pinned to commit `115b9263a` (detached worktree), API on `:8091`, with its
  own generation and export controllers. In that worktree only, `canvas_stages.json` was edited
  to move `default` from `default` to `candidate` for 1:1 and 21:9, so the canvases were
  admitted. That edit was never committed.
- **Database:** a disposable PostgreSQL 15 cluster on `:22600`, provisioned with the pilot recipe
  (`scripts.provision_public_v2_local_db_roles`; provider settings imported with
  `scripts.sync_existing_provider_settings`). Provider settings were verified in the database
  (16 keys, codex) after the import and again after the API started.
- **Recipe change vs the pilot:** only `EXPORT_RUNTIME_DIR`, which now points at the repo-root
  `presentation-export`. Next.js rendering again went to the shared `:5000`.
- **Matrix:** style `default`, the product-default strategy (omitted, resolved to `svg`),
  manuscripts `01-smart-factory-rollout` and `02-hybrid-work-policy`, 6 slides, canvases
  `wide-16x9` (reference), `square-1x1`, `ultrawide-21x9`, two rounds. Decks were run one at a
  time.

## Results

Per deck (`gate` = quality-receipt/v3 blockers; the three dimensions are always the same
triple, see below):

| round | canvas | manuscript | presentation | per-slide s | total s | outline s | retries | gate blockers | flagged pages | export | PPTX `p:sldSz` |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16:9 | 01 | `e314d3e8` | 63.62 | 381.7 | 181.0 | 3 | 3 | 3 | OK | 12192000 x 6858000 |
| 1 | 16:9 | 02 | `f5edebd7` | 49.84 | 299.1 | 180.6 | 1 | 0 | 0 | OK | 12192000 x 6858000 |
| 1 | 1:1 | 01 | `638d2f1a` | 49.44 | 296.6 | 150.3 | 4 | 3 | 3 | OK | 6858000 x 6858000 |
| 1 | 1:1 | 02 | `864bc673` | 55.82 | 335.0 | 153.9 | 5 | 3 | 4 | OK | 6858000 x 6858000 |
| 1 | 21:9 | 01 | `2e861a9f` | 55.04 | 330.3 | 165.9 | 2 | 3 | 2 | OK | 16002000 x 6858000 |
| 1 | 21:9 | 02 | `92dfbce7` | 46.09 | 276.5 | 130.3 | 2 | 3 | 1 | OK | 16002000 x 6858000 |
| 2 | 16:9 | 01 | `5af5f9a0` | 57.83 | 347.0 | 157.1 | 3 | 3 | 1 | OK | 12192000 x 6858000 |
| 2 | 16:9 | 02 | `4b5e2758` | 59.54 | 357.2 | 176.8 | 2 | 3 | 2 | OK | 12192000 x 6858000 |
| 2 | 1:1 | 01 | `b05dd4e6` | 41.77 | 250.6 | 141.5 | 1 | 0 | 0 | OK | 6858000 x 6858000 |
| 2 | 1:1 | 02 | `c1d6617d` | 54.20 | 325.2 | 161.7 | 5 | 3 | 4 | OK | 6858000 x 6858000 |
| 2 | 21:9 | 01 | `7842397d` | 60.36 | 362.2 | 163.1 | 2 | 3 | 1 | OK | 16002000 x 6858000 |
| 2 | 21:9 | 02 | not completed | | | | | | | | |

Every PPTX `p:sldSz` matches the registry. No generation or export failed.

## Compare

Each compare is candidate vs the 16:9 decks of the same round (2 decks per side, except where
noted).

| compare | gate blockers (base / cand / limit) | retries (base / cand / limit) | per-slide s (base / cand / limit) | flagged pages | verdict |
|---|---|---|---|---|---|
| 1:1, round 1 | 3 / 6 / 3 | 4 / 9 / 4.6 | 56.73 / 52.63 / 65.24 | 3 / 7 | **FAIL** (gate blockers, retries) |
| 1:1, round 2 | 6 / 3 / 6 | 5 / 6 / 5.75 | 58.69 / 47.98 / 67.49 | 3 / 4 | **FAIL** (retries) |
| 21:9, round 1 | 3 / 6 / 3 | 4 / 4 / 4.6 | 56.73 / 50.56 / 65.24 | 3 / 3 | **FAIL** (gate blockers) |
| 21:9, round 2 | not run | | | | manuscript 02 was not completed |

Per-slide seconds and PPTX size passed in every compare that ran. Local fit is not applicable
(svg decks do not run it).

**Round-2 21:9 was not completed.** Only manuscript 01 finished. Manuscript 02 was lost when
the runner was killed under memory pressure, and the machine then restarted, taking the bench
backend with it. No round-2 21:9 compare exists, and the backend was deliberately not relaunched.

### Reading the numbers

- **Gate blockers are bimodal and cannot separate decks.** An svg deck carries either 0 or 3
  blockers: any flagged page fails `authoring`, `geometryFont` and `visualReview` together. The
  16:9 baseline itself swings between 0 and 3 per deck (round 1: 3, 0; round 2: 3, 3), so the
  baseline total is 3 in one round and 6 in the next. With 2 decks per side, this metric is
  dominated by noise. The 1:1 round-1 "fail" and round-2 "pass" on the same metric show it.
- **1:1 retries may be a real signal.** 1:1 had 9 and 6 retries against 4 and 5 for 16:9, and
  flagged pages of 3, 4, 0, 4 against 3, 0, 1, 2 for 16:9. Both are consistently higher, but
  with 4 decks per side this is suggestive, not established. 21:9 looks like 16:9 on retries
  (4 vs 4 in round 1; 2 for the one round-2 deck) and flagged pages (2, 1, 1).
- **Most flags look like false positives.** The flagged pages we could inspect were
  `text_overlap` failures on multi-line titles, or on a label plus a big-number line box,
  where the line boxes overlap by 2 to 5 px and no defect is visible in the render. The
  slide-level evidence for this was read from the live backend during the session. Only counts
  are kept in the receipts (`gate.flags`), so the per-pair detail is not in the artifacts. The
  affected decks are the ones with flagged pages, for example round-1 1:1 `638d2f1a` and
  `864bc673`, and round-2 1:1 `c1d6617d`.
- **Outline is about half of wall time.** The outline stage took 130 to 181 s, which is 45 to
  60 % of each deck's total (250 to 382 s), on every canvas. The svg timing receipt is valid
  now (`generation-attempt-timing-receipt/v2`, stages observed).

## Visual observations

Reviewed from the SVG renders (`<deck>/svg/sNN.png`) of round 1.

- **1:1** fills the square and stays legible. Example: `638d2f1a` slide 2 (table of manual
  records, a "5일" / "68%" KPI pair, criteria block) uses the full height with no clipping or
  wrap.
- **21:9** uses the width with 3-column layouts. Example: `2e861a9f` slide 3 has three columns
  under a full-width title and a full-width callout bar. Text stays readable. On the cover of
  `92dfbce7` (`ultrawide-21x9/02-hybrid-work-policy/svg/s00.png`) the columns end near 68 % of
  the height and leave an empty band above the footer row. The cover is otherwise balanced
  (title left, direction panel right), and this is a layout preference, not a defect.
- **PPTX number wrap is not a canvas problem.** The PPTX renders show "68%" / "9 %" wrapping
  onto a second line. The SVG sources of the same slides are fine. This is pilot defect 4
  (svg to pptx text advance), which commit `0d7689863` fixes. The bench backend is pinned at
  `115b9263a`, which predates that fix and the provider-config fix `32e7a48fc`.

## Product problems found

1. **Client disconnects during status polling (bench client, fixed).** Uvicorn's keep-alive of
   5 s equals the bench's 5 s poll interval, so the httpx client reused connections the server
   was closing and raised WinError 10054. `canvas_bench_live.py` now sets
   `keepalive_expiry=1.0`. After that change round 2 ran 5 decks without a disconnect. Before
   it, deck `92dfbce7` (round 1, 21:9, manuscript 02) had to be recovered with a small helper
   that re-reads the finished task and runs the unmodified bench path (receipt marked
   `recoveredFromTask`). The retry after that disconnect orphaned two decks: `c5b30166`
   completed and went unused, and another failed with `svg_provider_attempt_uncertain`.
2. **svg gate blockers are not a usable defect count** (see above). The bench needs more decks
   per side, or a per-page defect metric (for example flagged pages per slide, or defects
   verified by a render check), before a canvas decision can rest on it.
3. **`text_overlap` in svg quality likely over-reports** 2 to 5 px line-box overlaps that have no
   visible effect, and this feeds the blocker triple.

## Bench caveats

- **Sample size.** 2 decks per side, 2 rounds, one style, 6 slides. The 16:9 baseline itself
  varies 0 to 3 blockers per deck, so a single compare is not a decision.
- **Backend version.** The backend predates `0d7689863` and `32e7a48fc`, so PPTX-side numbers
  (wrap, provider config) do not reflect the current tree. SVG-side results are unaffected.
- **Environment.** The `C:` drive hit 0 bytes free for about 15 to 20 minutes during the run, and
  memory pressure killed the runner. The machine later restarted. Timings from that window may
  be affected, and round 2 21:9 manuscript 02 was lost to it.
- **One deck was recovered** (`92dfbce7`) rather than run cleanly. Its numbers come from the
  same receipt path, but its client wall time is unset.
- Nothing was promoted. Promotion needs more decks per side, a metric that separates decks,
  and both rounds of 21:9.

Artifacts (gitignored) are in `artifacts/canvas-bench-20260929/sq-uw/round1` and `round2`: the
per-deck receipts, compare JSON and Markdown, `slides-hi/` and `svg/` renders, and (round 1) a
contact sheet per canvas.
