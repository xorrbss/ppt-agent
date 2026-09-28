# Canvas bench pilot (2026-09-29)

First live run of the canvas quality bench (`servers/fastapi/scripts/canvas_bench.py`), which
decides canvas × style exposure in `constants/canvas_stages.json` from measured receipts
(`docs/aspect-ratio-expansion-design-20260928.md` §8,
`docs/canvas-expansion-content-budget-spec-20260929.md` §6). The pilot's job was to prove the
tool end to end, not to promote anything: A4 is not a gated key, and `promote` was not run.

## Setup

- **Backend:** isolated, pinned to commit `28daa284e` (detached worktree), API on `:8091`, plus
  its own generation and export controllers. The shared stack (`:8000`) did not have this branch
  loaded (`/authored/styles` had no `supportedCanvases`), so it could not be used.
- **Database:** a disposable PostgreSQL 15 cluster (initdb in the session scratch directory),
  provisioned with `scripts.provision_public_v2_local_db_roles`, which is the same recipe
  `start-public-v2-rc.ps1` uses. The backend did **not** share the stack database: workspace
  operations are claimed from one queue with no node affinity (`claim_next_workspace_operation`),
  so a second backend on the shared database would race the shared stack's older controllers for
  its own jobs, and the results could not be attributed to this commit.
- **Environment:** the shared stack's DPAPI env store (`api`, `worker-generation`,
  `worker-export`), with only the database, app-data, artifact, staging, capability-key and port
  paths replaced. Provider settings came from a copy of `app_data/userConfig.json`, imported with
  `scripts.sync_existing_provider_settings`. Next.js rendering went to the shared `:5000` (its
  dev server runs the current tree).
- **Matrix:** style `default`, the product-default strategy (omitted, which resolved to `svg`),
  manuscript `01-smart-factory-rollout.txt`, 6 slides, canvases `wide-16x9` (reference) and
  `a4-landscape`, run one after the other.

## Results

| metric | wide-16x9 | a4-landscape |
|---|---|---|
| presentation | `d5bf4958-98e3-4d80-8f4b-240e16a3d56a` | `45337c62-5ff1-4b21-bcaf-75b213b0546d` |
| slides (source format) | 6 (svg) | 6 (svg) |
| generation wall s (timing receipt) | 335.4 | 266.9 |
| per-slide s | 55.9 | 44.5 |
| retries (attempt + provider retry calls) | 3 (0 + 3) | 1 (0 + 1) |
| local-fit unresolved | n/a (svg) | n/a (svg) |
| gate blockers (quality-receipt/v3) | 3 | 3 |
| flagged pages / `quality_status` | 2 / review_required | 0 / passed |
| export | OK | OK |
| PPTX `p:sldSz` (EMU) | 12192000 × 6858000 (matches) | 10692000 × 7560000 (matches) |

**Compare (A4 vs 16:9): PASS.** Every defect count is less than or equal to the baseline, per-slide
seconds (44.48 ≤ 64.28) and retries (1 ≤ 3.45) are within 1.15 × the baseline, and both PPTX sizes
match the registry. Local fit is not applicable on either side, because svg decks do not run it.

With n = 1 deck per canvas, the timing difference is provider noise, not a canvas effect. A
promotion decision needs both manuscripts and repeated runs.

A separate probe through `ppt-agent.mjs --page-size A4 --orientation landscape` produced A4 deck
`d2290278-…` (323.6 s, 5 pages passed and 1 flagged). This confirmed that the new CLI flags
reach the server as `page_size`/`orientation`.

Artifacts (gitignored) are in `artifacts/canvas-bench-20260929/`: the per-deck receipts, the
exported PPTX files and slide renders, `compare-a4-landscape-vs-wide-16x9.{json,md}`, a contact
sheet per canvas, and `contact-sheet-16x9-vs-a4/`.

## Product problems found

1. **Provider config silently read as empty on Windows.** `read_user_config_file` returns `{}`
   when the file has no `.bak` sibling. `_harden_existing_config_permissions` lets a pywin32
   `GetFileSecurity` "file not found" error escape its `FileNotFoundError` guard, and the outer
   `except Exception` swallows it. As a result, `sync_existing_provider_settings` exited 0 after
   importing nothing, and generation failed later with `provider_unknown`. Separately, API
   startup mirrored the empty database row over `USER_CONFIG_PATH`, which wiped that copy. Any
   fresh launcher or install that points at a lone `userConfig.json` hits this.
2. **The svg timing receipt is always `rc_invalid`.** Only the `provider` stage is observed.
   `outline`, `authoring`, `chromiumReview` and `publication` report
   `executed_duration_missing`, so svg stage timing cannot be benchmarked. Only `wallMillis` is
   usable.
3. **The svg quality-receipt/v3 blocks release on every deck.** `authoring`
   (`svg_authoring_evidence_missing`) and `geometryFont` (`svg_geometry_font_evidence_missing`)
   fail because no evidence is wired, so "gate blockers" is a constant 3 per svg deck and cannot
   tell decks apart. `visualReview` reports `svg_visual_review_flagged` even on the A4 deck, whose
   generation result shows 0 flagged pages.
4. **Number and unit wrap in the exported PPTX.** Values such as "68 %" and "80 %" break onto a
   second line and overlap neighbouring text. This appeared in the LibreOffice render on both
   canvases (16:9 slide 2, A4 probe slides 2 and 5). It has not been checked in PowerPoint.
5. **An A4 deck is withheld from older readers.** `GET /presentation/{id}` returns 409
   `document_canvas_client_upgrade_required` without `X-Document-Canvas-Contracts:
   document-canvas/v1`. This is by design, but every script that reads decks needs the header,
   and the bench sends it.
6. **The CLI probe exited with code 255.** The `ppt-agent.mjs --async` probe process exited 255
   with no message about 285 s into polling, while the server finished the deck normally. The
   cause was not determined: it may have been this session's background-task runner. The bench's
   Python client did not hit it.

## Bench caveats

- `run` attributes an authored deck's QualityReceipt v2 ledger by its write time, because the API
  does not expose the job id. Drive one deck at a time against a backend no one else is using.
  svg decks do not write that ledger; their gate evidence comes from the export's
  quality-receipt/v3 instead.
- The bench treats a metric as `not_applicable` only when it is absent from both the baseline and
  the candidate. If it is absent from one side only, the compare fails as `unmeasured`.
