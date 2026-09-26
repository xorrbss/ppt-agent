# Generation core: one shared core, SVG driver forward, authored driver frozen (2026-09-25)

Status: APPROVED DESIGN, revision 2. Becomes `docs/generation-core-architecture-20260925.md`
as the first commit of the integration branch.

Revision 2 changes (from the review): authored is frozen; SVG PDF must be vector
before the default switch; a preparatory split of `svg_presentation_generate.py`
precedes Phase 1; authored regression is checked by offline replay, full run
only before a main merge; every session keeps touched files ruff-clean and
<= 350 lines.

## 1. Decision

The product keeps exactly two generation modes: `svg` (LLM-written SVG per
slide; raster reconstruction included) and `authored` (LLM-written HTML per
slide). `svg` becomes the default in the UI, the backend request model and the
public v2 API. Template V2 is removed entirely.

**authored is frozen.** It keeps working and stays selectable, but receives no
new features and no format-side adapters. It benefits from shared-core fixes
only through the modules it already calls. Its behaviour on a fixed bench must
be identical before and after every merge; the check is an offline readback
replay, with one full product run before each main merge.

Method: **reuse first, extract second.** The SVG path is wired to call the
existing authored modules through their current seams. A module moves into the
shared package only when both drivers call it; the authored import path stays
as a thin facade. A format-agnostic seam is introduced only where an authored
module turns out to depend on HTML. The driver protocol is written at the end,
from the seams that exist, not designed up front.

Ground truth this design rests on (worktree `wt-raster/dev`, branch
`quality/raster-redesign-20260926`):

- The endpoint already shares everything before the strategy branch (outline,
  brief, web search, job, checkpoints) and forks only at
  `presentation_generate.py:1127` into `generate_authored_presentation` or
  `generate_svg_presentation`.
- `svg_presentation_generate.py::_generate` runs one `page()` coroutine per
  slide (a single ~400-line function): author -> render/measure -> at most one
  model correction -> checkpoint; the first page error becomes `fatal_error`
  and cancels the deck (L420-448). Publish marks pages verified only for source
  reflow (L409-414), so every ordinary SVG deck is published "review required"
  (endpoint L1145-1153).
- Export: `svg_export_operation.py::execute_svg_workspace_export` rejects
  `release-v1` (L198) and font embedding (L201); the isolated worker
  (`workers/public_svg_export_worker.py`) converts with the vendored
  slide-master converter (`vendor/slide_master/svg_to_pptx`,
  `use_native_shapes=True`). **PDF is a per-page PNG composited by pypdfium2**
  (`public_export_worker.py::_build_pdf`); text is not selectable.
- Raster redesign already injects native tables by annotating an SVG group with
  `data-pptx-native="table"` + `data-pptx-json=<payload>` before conversion
  (`source_content/raster_redesign_pptx.py::prepare_redesign_pptx_inputs`),
  with `restore_grid()` fallback if the converter rejects it.
- Authored export recognizes tables/charts from `AuthoredNativeTablePayloadV1`
  / `AuthoredNativeChartPayloadV1` next to the DOM
  (`lib/authored-hybrid/native-table.ts`,
  `services/authored_vnext/semantic/editable_scene.py`). An HTML/SVG-independent
  table/chart node exists in `authored_vnext/deck_ir/contracts.py`
  (`SlideComponentV1`) but nothing outside deck_ir uses it.
- Capability audit (authored -> svg): shared today = outline/brief/web search,
  style presets via `svg_style_adapter`, job semaphore + rate limit, usage
  receipts, single-page remake preview/CAS publish, Chromium overflow probe.
  Not shared = vision advisory, flag classification, terminal slide fallback,
  request brand tokens, master pin / design pack (422), illustrations, icon
  pack, batch remake (`html_content` check), font embedding, rollout profile
  (SVG hard-codes `Semaphore(3/2)`), release quality policy. HTML-only =
  local-fit, slidespec repair, patch engine, vision *revise*.
- Authored offline replay: gates can be re-run from stored slide HTML without
  regeneration (memory `restyle-readback-verification`); this is the per-
  integration regression check.

## 2. Target shape

```
request ──► core.admission        strategy resolution (svg default | authored), canvas, policy
        ──► core.outline          outline, content brief, web search        (shared today)
        ──► core.style            StyleContext = preset + request brand + master/design pack
        ──► core.assets           AssetOffer: source images, illustrations, icons (format-neutral)
        ──► core.runtime          job, checkpoints, fence, wave scheduler, rollout profile, usage
        ──► driver.author_page    ┐
        ──► driver.render_page    │  SvgDriver (svg + raster)    HtmlDriver (authored, FROZEN)
        ──► driver.correct_page   │  author_slide_svg /          author_slide / Chrome /
        ──► driver.fallback_page  ┘  svg_renderer / svg fallback  local-fit / html fallback
        ──► core.quality          PageOutcome -> flag classification, vision advisory,
                                  terminal policy (fallback | flag-only | fail)
        ──► core.publish          slides + NativeObjects sidecar (svg) + receipts
        ──► core.export           worker dispatch, font embedding, vector PDF, native objects
                                  SvgDriver.export_scene  |  HtmlDriver.export_scene (as is)
```

Package: `servers/fastapi/services/generation_core/` with sub-packages
`admission/`, `style/`, `assets/`, `runtime/`, `quality/`, `native_objects/`,
`export/`, and `drivers/{svg,html}/`. Every file <= 350 lines. A module that
moves keeps a one-line re-export facade at its old path until the last caller
is migrated; the facade is deleted in the same phase.

`drivers/html/` is created only as the place where existing authored entry
points are *referenced* for the protocol; no authored code is edited except
facade lines produced by `git mv`.

### 2.1 Seams introduced (only these, only when needed)

| Seam | Type | Why | Consumers |
|---|---|---|---|
| `PageOutcome` | dataclass: index, source_format, source, png_path, quality, attempts, failures, verified | quality/fallback modules must not read `html_content` | flag classification, vision advisory, terminal policy, publish |
| `StyleContext` | dataclass: preset, brief lines (format-tagged), palette, fonts, wordmark, reference images, master/design-pack rules | brand tokens and master rules are data; only projection is per format | `SvgDriver.project_style` (attributes, `svg_template_rules`); HtmlDriver keeps its current CSS projection untouched |
| `AssetOffer` | slots: id, role, kind (source\|illustration\|icon), bytes/ref, bbox hint, alt | illustration stage and icon pack produce rasters/paths either driver can place | `SvgDriver.embed_assets` (icons flattened to the contract subset); HtmlDriver keeps its current path |
| `NativeObjects` | per-slide sidecar `{objects: [{id, kind: table\|chart, anchor, payload}]}`; payload = existing authored V1 table/chart payload shape | export needs semantic data the markup does not carry | `SvgDriver.export_scene` annotates `data-pptx-native` / `data-pptx-json` (generalized from raster). **No authored mapping** (frozen; it already has native tables/charts) |
| `TerminalPolicy` | enum fallback / flag_only / fail, from rollout profile | SVG hard-fails the deck on one page | SVG page collector; authored keeps its existing policy code path |

Storage: `PageOutcome` in the checkpoint payload (dict-shaped in both modes).
`NativeObjects` in `slide.content["nativeObjects"]` (additive JSON, no
migration), digested into the export bundle.

### 2.2 Rules (all sessions)

1. A module moves to `generation_core` only when both drivers call it. Move =
   `git mv` + facade; no rewrite in the same commit.
2. No format branch inside `generation_core`; format logic lives in a driver.
3. authored is frozen: no edits to `authored_*` / `authored_vnext/*` beyond
   `git mv` facades. Offline replay on the fixed bench must be identical
   (flags, receipts, native-run counts) at every integration; one full
   product run before each main merge.
4. No symptom fixes: an invariant is enforced in one place.
5. Every SVG seam is exercised by one real product-path run before merge.
6. Touched files: `ruff check` clean and <= 350 lines when the session
   finishes; the ratchet baseline may only shrink.
7. Own worktree + branch, local commits only, no push; report commit hash +
   numbers to the orchestrator; no repo-wide grep or git status; defects are
   reported only after reproduction on the live path with flags and gates.

## 3. Phases, sessions, models

Model tiers: **A** = Opus 5.5 (design-sensitive, cross-module), **B** =
Sonnet 5 (adapters, measurement, contract edits), **C** = Haiku 4.5
(mechanical, rule-driven). Orchestrator = this session. Two product
measurement stacks at most.

### Phase 0 — measure, finish raster, delete (parallel, ~2 h)

| Session | Model | Scope | Owns | Done when |
|---|---|---|---|---|
| M0 svg-baseline | B | Product path: 3 ordinary SVG decks (prompt, document, 30+ p) and the same inputs on authored. Measure editable ratio, native runs, flags, min font, time, **PDF text selectability per mode** | eval scripts only (general-deck driver under `servers/fastapi/scripts/raster_redesign_eval/`) | comparison table; defines the default-switch gate incl. PDF |
| R1 raster-quality (running, with its layout sub-agent) | A | editable ratio, mockup misclassification, layout (C), 49-min analysis regression cause | `source_content/raster_*` | 0 wrong numbers, mockup-excluded ratio reported, analysis time explained by commit or environment |
| R3 raster-verify | B | 5-deck measurement at each R1 integration | none (stack 8100) | table vs baseline |
| K1 template-v2-removal | B | delete the inventory: 4 endpoints + router entries, lifespan/bootstrap wiring, 16 services, 4 models, `templates/v2/`, ~150 frontend files, workflow, package scripts, `required-ci-contract.test.mjs`, `contracts/template_v2`, compatibility ledger keys, `presentation_export_route_inputs.py`, generation-job `template_v2_target` column (drop migration) | listed files only | app boots; focused node + pytest suites green; CI contract test updated |
| orchestrator prep | — | **P0 split**: `svg_presentation_generate.py` -> `drivers/svg/{author,render,collect,publish}.py` behind the existing facade, behaviour-identical (checked by focused tests + one raster deck run) | that file | committed before Phase 1 starts |

Merge 1: R1 (after R3 pass), K1, P0 split -> `main`.

### Phase 1 — SVG reuses the authored core (parallel, ~3.5 h)

Integration branch `integrate/generation-core` from post-merge-1 `main`.

| Session | Model | Gaps | Owns (moves into) | Done when |
|---|---|---|---|---|
| C1 quality | A | 1 review-required, 2 one-page-kills-deck | `authored_flag_classification`, `authored_vision_advisory*`, `authored_terminal_slide_*` -> `generation_core/quality/`; `PageOutcome`, `TerminalPolicy`; `drivers/svg/collect.py`, `publish.py` | ordinary SVG deck publishes passed/flagged per page with advisory receipt; one failed page yields an SVG fallback page and the deck completes |
| C2 style | B | 3 brand tokens, master pin / design pack | `resolve_brand`, `svg_style_adapter`, `svg_template_rules`, master/design-pack resolution -> `generation_core/style/`; `StyleContext`; `svg_generation_admission.py` | SVG deck honours request colour/font/wordmark; `master_pin` accepted for svg; no 422 |
| C3 assets | A | 4 illustrations, icons | `authored_illustration_stage`, `authored_infographic_assets`, `authored_icon_pack` -> `generation_core/assets/`; `AssetOffer`; icon flattening; `drivers/svg/author.py` prompt + `svg_presentation_prepare.py` offers | SVG deck contains planned illustrations/icons as `source-slot-*`; contract validation passes |
| C4 runtime+export | B | 5 font embedding, release-v1, batch remake, hard-coded concurrency, **vector PDF for svg** | `authored_rollout_profile`/`authored_wave_scheduler` consumption in svg; worker font embedding for svg pptx; svg PDF rendered from vector (SVG -> PDF, not PNG composite); `release-v1` review path; `authored_batch_remake_service` dispatch by `source_format` -> `generation_core/runtime/`, `generation_core/export/` | svg export with `font_embedding=true` embeds CJK fonts; svg PDF has selectable text; `release-v1` accepted with review receipt; batch remake on svg slides; concurrency from profile |
| V1 verify | B | regression watch | none (stack A) | authored offline replay identical at each integration; SVG 3 decks + raster 5 decks tables; one full authored run before Merge 2 |

Merge 2: `integrate/generation-core` -> `main` after V1 shows authored
unchanged and SVG gaps 1-5 closed (PDF included).

### Phase 2 — native objects, default switch, debt (parallel, ~3 h)

| Session | Model | Scope | Owns | Done when |
|---|---|---|---|---|
| N1 native-objects | A | `NativeObjects` model; generalize raster `prepare_redesign_pptx_inputs` into `SvgDriver.export_scene`; SVG author prompt emits table groups with ids; verify slide-master chart support (unverified) — if absent, tables only for svg and charts stay vector, recorded as debt; raster table cell overlay (native table + per-cell source crop for unverified cells; notes in `handoff/raster-redesign-20260925/table-cell-overlay-notes.md`: contract `table_pixel_cells`, exactly-once exemption, layout dependency; measured potential deck1 0.83 / deck4 0.66 editable) | `generation_core/native_objects/`, `raster_redesign_pptx.py`, `svg_export_operation.py` bundle digest, `drivers/svg/author.py` table instruction | svg deck with a table exports a native `a:tbl`; readback counts in receipt |
| D1 default-switch | B | `presentationGenUpload.ts` default; request model `strategy` default + description; public v2 route default; `generationMode` route table pruned to two modes; `generation_engine` `deck-ir` literal removed; docs + CLAUDE.md; Cypress/node tests; `ppt-agent.mjs` `--strategy` | listed files | omitting strategy yields svg in UI, API, public v2; tests updated; **gate: M0/V1 PDF and editability parity met** |
| L1 lint | **B** (a haiku attempt on 2026-09-26 fixed 30 of 515 findings, committed scratch files at the repo root and edited frozen authored files; only 7 files were salvaged) | ruff leftovers -> 0 in files no other session owns (E501 319, F401 40, B905 35, B023 21, E402 17 …); ratchet baseline regenerate (must shrink) | files from the orchestrator's exclusion list, never `authored_*` / `authored_vnext/*` / `utils/llm_calls/author_*` | `ruff check .` clean; ratchet green |
| L2 tests | B | 43 pre-existing failures + 2 strict xfail: fix test or code, or delete tests of removed features, one-line reason each; authored test fixes must not change authored behaviour (replay identical) | test files + minimal code | focused pytest green; no new skips |

Merge 3: N1, D1, L1, L2 -> `main`; CI (GitHub + Bitbucket) green.

### Phase 3 — protocol and closure (~1 h)

| Session | Model | Scope |
|---|---|---|
| P1 protocol | B | write `generation_core/drivers/protocol.py` from the seams the SVG driver implements and the authored entry points it mirrors; delete facades whose last caller is gone; design doc DRAFT -> FINAL with the file map |
| orchestrator | — | shared worktree fast-forward to `main`; remove `integrate/*` refs and finished worktrees; report leftover untracked files for a delete decision; memory update |

## 4. File ownership (conflict avoidance)

| Area | Owner |
|---|---|
| `services/source_content/raster_*` | R1 |
| `services/svg_presentation_generate.py` split | orchestrator (P0), then `drivers/svg/author.py` C3, `render.py` C1 (measure hooks only), `collect.py` C1, `publish.py` C1 |
| `svg_style_adapter.py`, `svg_template_rules`, `svg_generation_admission.py` | C2 |
| `svg_presentation_prepare.py` (asset offers) | C3 |
| `svg_export_operation.py`, `workers/public_svg_export_worker.py`, `public_export_worker.py::_build_pdf` | C4, then N1 (native objects) |
| `authored_batch_remake_service.py` dispatch line, `authored_rollout_profile.py`, `authored_wave_scheduler.py` (read-only consumption) | C4 |
| `authored_flag_classification.py`, `authored_vision_advisory*.py`, `authored_terminal_slide_*.py` (`git mv` + facade only) | C1 |
| `authored_illustration_stage.py`, `authored_infographic_assets.py`, `authored_icon_pack/` (`git mv` + facade only) | C3 |
| Template V2 footprint | K1 |
| Upload defaults, request model defaults, public v2 route, docs | D1 |
| Everything else for ruff | L1 by exclusion list |

## 5. Verification matrix

| Gate | Run by | When |
|---|---|---|
| Authored offline replay (fixed bench: prompt 10 p, document 30 p, exact-reference): flags, receipts, native-run counts identical | V1 | each Phase 1/2 integration |
| Authored full product run identical | V1 | once before Merge 2 and once before Merge 3 |
| SVG ordinary decks (prompt, document, 30 p+): editable ratio, native runs, flags, fonts, time, PDF text selectable | M0 baseline, V1 after | Phase 0, each integration |
| Raster 5 decks: wrong numbers 0, editable ratio, page-content crops, fallback-to-source-geometry count | R3 | each R1 integration |
| Focused pytest + node suites per session; full `uv run pytest` and `test:next-ci` before each main merge | owner / orchestrator | per commit / per merge |
| Merge reconciliation: bidirectional containment, AST duplicate-definition scan, security-file audit | orchestrator | each main merge |
| Touched files ruff-clean and <= 350 lines | owner | each commit |
| CI (GitHub `test-all`, Bitbucket) green | orchestrator | Merge 3 |

## 6. Known debts recorded, not in scope

- Native charts for svg: **supported** (N1, 2026-09-26). The vendored slide-master
  converter emits classic `c:chart` parts with an embedded workbook for every
  authored V1 chart type (column, bar, line, area, pie, donut); pinned by
  `tests/unit/test_svg_native_chart_probe.py`. The converter's key names differ
  from the V1 payload for legend, palette/series colour, axis titles and font
  size, so `drivers/svg/export_projection.py` maps them; a title/axis title/legend
  not drawn inside the marked group is dropped by the converter, so publish
  accepts only drawn labels. No debt remains here.
- Background operations fall back to the default tenant without a tenant in
  the command context; multi-tenant deployments verify before rollout.
- `PATCH /presentation/{id}/slides/order` exists API-only; the editor is
  read-only by design.
- authored PDF path is not changed even if M0 finds it raster (frozen).
- Public v2 restyle routing stays on authored reflow until D1 flips the
  default and prunes the route table to two modes.
- Raster analysis parallelism: per-page stage timings (74ad6813c/609b9627b)
  show the pure-Python `plan` stage dominates (deck1: plan 414 s of page
  stage time) and the 12-thread pool yields almost no speed-up (GIL). A
  per-page process pool is the largest speed lever; out of scope here.
- Workspace operation handler registration is hand-copied in three lists
  (embedded worker, external worker profiles, lifespan); they had drifted
  (batch preview/publish never dispatched; `raster.analysis` missing on the
  embedded side). C4 fixed the drift and added a parity test
  (`test_lifespan_handler_registration_parity.py`); unifying the three lists
  into one registry is Phase 3 (P1) scope.
- Pre-existing main defect found by the Samsung standalone run (2026-09-26):
  after the generation handler marks the operation succeeded, the runner's
  heartbeat can observe the lease gone, close the operation as success and
  cancel the handler task while `finalize_generation_export_chain` / webhook
  / timing receipt are still running; the CancelledError rolls back the
  uncommitted chained export. ~5 % of generations lose their chained export
  silently. Fix in the runner (post-publish work never depends on the lease;
  bounded wait instead of cancel) lands in merge 1 from raster-quality.
  authored shares the publish flow — likely affected, unverified. Moving the
  chain submit into the publish transaction is a Phase 2/3 review item.
- Correction (2026-09-26): M0's "export chain not attached for file-based
  generation" was a polling race in the eval driver, not a product defect
  (re-run on 1aa17a674 records `export_operation_id`). C4's 63f47ca73 stays
  as a robustness fix for a code-level hazard (second commit rollback erasing
  `async_status.data`) that was reproduced only with a fake session, never
  live.
- Session handoff notes committed at worktree roots (`C4-HANDOFF.md`, etc.)
  must be moved under `handoff/` or dropped before Merge 2.
- Design packs (`authored_vnext/design_pack`) are patch-handle based: every
  DesignFrame patches an element of a source-native document, so they cannot
  project onto from-scratch SVG authoring (C2, 2026-09-26). SVG accepts
  `master_pin` (projected via `master_projection.py`) but rejects
  `design_pack_pin` with `svg_design_pack_requires_source_native_patch_target`.
  D1 must keep `registered-template` + `design_pack_pin` routed to the
  authored/source-native path even after `svg` becomes the default.
- Bundled Korean template packages (8) were removed with Template V2 on
  2026-09-25 (they only seeded the removed store); restorable from git
  history, convertible to design packs later.

- C3 open items (2026-09-26): (1) the gpt-image-2 transparent cut-out / icon
  sheet path was only unit-tested (V has codex_image only) — verify once on
  the shared stack after merge; (2) with an alpha-less image provider, icons
  depend on exact library alias matches (a general deck matched 0/45) —
  better matching changes `library.py`, which authored also uses: product
  decision; (3) every page of general SVG decks carries `font_unresolved`
  (cause not yet traced; handed to C2 with its font-weight fix).

## 6a. M0 baseline (2026-09-26, `handoff/raster-redesign-20260925/m0-svg-baseline.md`)

| deck | strategy / commit | result | time | gate | editable text | PDF selectable |
|---|---|---|---|---|---|---|
| prompt10 | authored / main | ok | 25.6 min | blocked | 0 % | 0/10 |
| prompt10 + vision_qa | authored / main | ok | 13.9 min | passed | 0 % | 0/10 |
| prompt10 | svg / main | **fails** (principal) | — | — | — | — |
| prompt10 | svg / 8950f150a | ok | 12.1 min | review_required | 100 % | 0/10 |
| doc32 | authored / main | ok | 21.0 min | blocked | 0 % | 0/32 |
| doc32 | svg / 1aa17a674 | ok | 33.7 min | review_required | 100 % | 32/32 |
| longprompt30 | authored / main | ok | 20.1 min | blocked | 0 % | 0/30 |
| longprompt30 | svg / main | **fails** | — | — | — | — |
| longprompt30 | svg / 1aa17a674 | ok | 35.0 min | review_required | 100 % | 30/30 |

Facts for D1: authored's export on main is a full-slide raster in every run,
gate state and vision_qa notwithstanding (whether `quality_profile=release`
changes that is an open question, not tested); svg exports native text in
every run and vector PDF once C4's 4d3d36ce8 is in. svg is 1.4-1.7x slower
per deck than authored on the same inputs.

## 7. Estimate

Phase 0 ~2 h, Phase 1 ~3.5 h, Phase 2 ~3 h, Phase 3 ~1 h: **8-10 h** wall
clock, bounded by two measurement stacks and the two full authored runs.
