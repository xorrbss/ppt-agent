# Generation quality: root cause and the Composer architecture (2026-09-04)

Status: phase 0 implemented and measured (section 6a); decision 7.1 is
decided, 7.2 and 7.3 are still open.

Scope: the `authored` engine, including `source_content_restyle` (verbatim
restyle), which is the mode whose failures triggered this analysis. Evidence is
the test database on port 21000, the decision ledger in `.spec-memory`, the
receipts under `output/`, and the code as of commit `797b9115a`.

## 1. Summary

Every quality mechanism added to the authored pipeline since August inspects
HTML that the server does not control. The LLM writes the pixels; the server
measures them afterwards and asks the same LLM to fix what it measured. Twelve
structural fixes on 2026-09-03 moved a 35-slide verbatim deck from dying at
authoring 27/35 to dying at the final composition gate 35/35. Zero of 43
verbatim restyle operations since 2026-08-28 succeeded through that path.

The repository already contains three server-owned layout stacks, one of which
produced the same 43-page verbatim deck in 2 minutes 33 seconds with zero
provider calls and exported it to PPTX and PDF. Its output is structurally
sound and visually poor: a text flow with footer text set as body columns.

The proposal is to make the server the render authority for every authored
slide and to reduce the LLM to typed decisions. Content is data, layout is
code, fit is solved before rendering, and Chromium confirms instead of
repairs. The gates that fail today become assertions that cannot fail because
the renderer emits what they check. The design investment moves from prompt
rules and repair heuristics into an archetype library, which is the one place
the team's own experiment showed it pays off.

## 2. Symptom record

### 2.1 Operation outcomes since 2026-08-28 (`workspace_operations`, kind `presentation.generate`)

| Route | Requests | Succeeded | Dominant failures |
|---|---|---|---|
| authored reflow, `source_content_restyle` | 43 | 0 | composition safety gate 7, browser readback 7, deadline 4, worker crash 3, provider 5, capacity 4 |
| server-owned `source-native`, `source_content_restyle` | 27 | 5 | generation failed 5, document quality 4, browser quality 4, publication 3, capacity 3 |
| authored, ordinary | 21 | 4 | slide capacity 8, safety gate 2, owner capacity 2 |
| Deck IR, ordinary | 6 | 0 | browser closure 2, director/language/overlap/CSS 4 |

The 15 `presentation_generation_job_not_linked` dead letters are companion
operations of the restyle runs and are excluded from the counts above.

The same period seen from `presentation_generation_jobs` (115 jobs, 12
published): the 5 published restyle jobs are the 5 `source-native` successes
above; no verbatim deck has ever been published through the authored reflow
path. Failure families over the 103 failed jobs:

| Family | Share |
|---|---|
| source-content binding and readback | 34% |
| provider, LLM transport, attempt budget | 22% |
| capacity envelope (`*_capacity_exceeded`) | 15% |
| orchestration, cancellation, generic | 13% |
| DOM safety gate (overflow, clipping, off-canvas, overlap) | 10% |
| LLM output contract (sanitizer: `break-inside`, `hidden`, unknown `data-*`; QIDs) | 7% |

60 of the 103 failed jobs ended in the `author` stage, before any slide was
rendered; the median failed job persisted 0 slides. Replaying the composition
gate over the 568 slides with surviving browser evidence finds blocking
defects on 26 of 508 verbatim slides (5%), spread over 11 of 20 decks:
clipping 26, off-canvas 15 to 18, overlap 10 plus 9 occlusion probes,
overflow 4. A 5% per-slide defect rate with a deck-level kill is a
0.95^35 = 17% deck pass rate before any other failure family is counted.

Every one of the 963 stored authored slides uses `position:absolute` (median
5 absolutely placed elements per slide, 0 relative font sizes, 711 with five
or more pixel `left`/`top` declarations); defective and clean slides do not
differ in layout style. The bounded repair round ran 24 times in the receipts
since 2026-09-01 and succeeded 3 times; its provider leg made 0 calls in every
receipt, so the "LLM repair" has in practice been the deterministic repairs
alone.

### 2.2 The one successful 43-page verbatim run

Operation `a83178f8` (2026-09-02, route `source-native`, design pack
`devax-first-ten-authored-candidate`), from
`output/candidate-54892244-dual-lane/preserve-43-export-summary.json`:

| Stage | Wall time |
|---|---|
| outline | 38.0 s |
| authoring | 4.2 s |
| chromiumReview | 102.3 s |
| publication | 5.1 s |
| provider calls | 0 |
| total accepted to succeeded | 153 s |

Exported `preserve-43.pptx` (5.4 MB) and `preserve-43.pdf` (3.8 MB). Rendered
pages show every source block present with no overflow, and also show the
quality ceiling of the current server renderer: two- and three-column text
flow on every page, running footers (`CONFIDENTIAL`, `12 / 43`, company name)
set as body text, source screenshots reduced to thumbnails, no hierarchy
beyond the heading.

### 2.3 Authored baseline cost

From `app_data/authored-quality-receipts` (2026-09-01 to 09-02): 89 attempts,
11 succeeded; author call p50 118 s, about 4.5k input and 2.4k output tokens
per call; authoring is 95% of wall time; a 35-slide verbatim deck runs 25 to
40 minutes before failing.

## 3. Root cause

### 3.1 The render authority is the LLM's raw HTML; everything else is a sidecar

`services/authored_vnext/generation/scene_envelope.py` states it in its first
line: "The LLM still emits HTML today." The vNext architecture
(`docs/authored-only-vnext-architecture-design-20260813.md`, section 8) placed
the product at stage T0, "Authored HTML: active visual source", with T1/T2
generating HTML and PPTX from a canonical spec. Three weeks later the product
is still at T0, and five intermediate representations have accumulated around
the HTML:

| IR | Location | Role today |
|---|---|---|
| SlideSpec v1 | `models/slidespec_v1.py`, `utils/slidespec_*` | imports HTML, solves x/y/w/h, repairs on `quality.review` only |
| Scene IR (SlideSpec v2) | `models/authored_vnext/deck/scene.py`, `generation/scene_compiler.py` | compiled from HTML for edit and export; has 12 composers and PIL text fitting that nothing on the generation path calls |
| Deck IR v1 | `services/authored_vnext/deck_ir/` | server-owned grid renderer, off by default, rejects source bindings at runtime |
| Narrative IR + design packs | `services/authored_vnext/narrative_ir/`, `source_native_generation.py` | server-owned flow renderer for verbatim, reachable only when a design pack resolves |
| Placement plan + materializer | `services/source_content/allocator.py`, `materializer.py` | the restyle design's own deterministic path; `generate_source_content_restyle_presentation` has no caller |

None of them renders the slide the user receives. `admit_source_content_request`
(`services/source_content/pipeline.py:170-217`) turns a restyle request into an
ordinary authored request: it appends a 1,197-character prose contract to
`instructions`, sets `slides_markdown` to the source text, sets
`request.source_mode = None`, and the comment reads "Fall through to the
ordinary authored outline/author/render pipeline." The restyle design of
2026-08-26 (`docs/source-content-template-restyle-design-20260826.md`,
section 6.2) lists that exact path under "does not reuse":
"`author_planned_slide` → `author_slide_html` 생성형 HTML authoring 경로".
The implementation two days later (`1fc2b169a`, 2026-08-28) went through it
anyway, and every failure this week is a failure of that path.

### 3.2 Layout is a constraint problem asked in prose and answered in pixels

The author prompt for one slide is 14 to 20 KB (`utils/llm_calls/
author_slide_rules.py` alone is 6.6 KB of geometry rules: "EXACTLY 1280x720",
"FIT IS CRITICAL", width arithmetic, below-fold arithmetic) and the required
output is a complete HTML document with its own `<style>`. The model is asked
to do pixel arithmetic in its head, and the failures are arithmetic: a wrapper
inset 64 px still given `width:1280px` (operation `02aaadec`, ten elements
carried off canvas), a 445 px column placed at y=366 on a 720 px canvas
(`e02f5c64`, slide 13), 30 underscores at 20 px in a 347 px box (`7787af14`).

The gate then measures. `inspect_authored_dom` reports eight categories, of
which `overflow`, `clipping`, `off_canvas`, `overlap` block
(`services/authored_composition_policy.py:2230`). The repair is a second prose
round with a budget of one per slide per scene digest
(`claim_scene_repair`), and any residual blocking defect ends the deck
(`authored_presentation_generation.py:2857-2891`). This week's fixes made the
deterministic repairs measured and made repair progress cumulative across
attempts; they cannot make a filter into a generator. A filter over a random
process has a pass rate; a 35-slide deck needs 35 consecutive passes.

### 3.3 The verbatim contract is over-constrained and has no terminal

`SourceContentPolicy` (`models/generate_presentation_request.py:40-57`) fixes
four things at once: text is verbatim, slide count equals page count
(`preserve_source_slide_count: Literal[True]`), no slide expansion
(`allow_slide_expansion: Literal[False]`), and
`overflow_policy = "same_page_recompose_or_fail"`. The template adds chrome,
and the gate adds a 15 px floor (`SlideCapacity.min_font_px`). A source page
set at 10 pt with 400 characters in a dense layout has no solution under
those constraints, and no engine can produce one. Both engines confirm it
with different error codes: `authored_source_verbatim_capacity_exceeded`,
`authored_slide_capacity_exceeded` on the authored side;
`source_verbatim_same_page_capacity_exceeded`,
`source_native_sentence_capacity_exceeded` on the server side.

Nothing on any authored path splits a slide. The only splitter in the
repository is `utils/layout_capacity._split_content`, a character heuristic
on the legacy template pipeline. A fit ladder needs a last rung that always
succeeds, and the product contract currently forbids every candidate for
that rung.

### 3.4 Source binding is post hoc, so the gates re-derive what the renderer should have emitted

The LLM never sees block IDs. `_source_block_bindings`
(`authored_presentation_generation.py:1293`) is a server-side list of
`(block_id, text)`; `stamp_source_blocks` (`services/source_content/
text_stamping.py`) searches the returned HTML for each block's text and
inserts `data-source-block-id` afterwards. The prose contract given to the
model is a description of how the stamper will search. `source_runs.py`
exists to project one PPTX shape's text into the runs a template might split
it into, with tolerances for dropped bullets, attached pipes and arrows,
blank fields and marker-only lines, each added after a stored deck failed on
it (decisions `source-content.verbatim-presentation-tolerance`,
`verbatim-presence-contract`, `restyle-block-order`). When the renderer copies
the block text from the manifest itself, the binding is an attribute it
writes, and this entire layer becomes a test fixture.

### 3.5 The manifest discards the hierarchy signals both engines need

`services/source_content/pptx.py` captures shape geometry in EMU
(`_geometry`, `:290`) and never reads `a:rPr` (font size, weight, color).
`outline.py:1-7` then drops the geometry deliberately. Downstream, a running
footer at the bottom of the source page and a 24 pt heading are both
`kind=text, semantic_role=body`. The LLM engine guesses hierarchy from the
words; the server engine has no basis to guess and sets footers as body
columns (section 2.2). Tables and charts are extracted with full fidelity and
then dropped by `outline.py:136-140` because `preserve_all_table_cells` and
`preserve_all_chart_values` default to false; their text silently disappears
from the restyled deck.

### 3.6 Four engines, each partially built, none the product default

Engine work since August produced, in order: SlideSpec v1 with a solver and
sixteen closed repair operations (08-04), Scene IR with twelve composers and
PIL text fitting (08-13), the restyle allocator and materializer (08-26),
Deck IR with a twelve-column grid renderer and K=6 candidates (08-31), and the
source-native flow renderer with capacity admission (09-01/02). Each was
gated behind a flag, a design pack, or a route receipt, and each left the
LLM-HTML path as the default. The decision ledger records the intent to
compare before switching (`authored.generation.dual-engine-comparison`,
2026-09-01) but the comparison has not been run, and meanwhile fixes land on
the default path at a rate of several per day.

## 4. Target architecture: the Composer

### 4.1 Principles

1. Content is data. Source text, tables, charts and images come from the
   manifest; generated prose comes from the LLM as typed fields. Neither is
   ever embedded in model-written markup.
2. Layout is code. A slide is rendered from a typed plan by server code that
   emits `data-qid`, `data-semantic-role` and `data-source-block-id` on every
   element it creates.
3. Fit is solved before rendering and confirmed after. A deterministic fit
   ladder with a guaranteed last rung replaces the repair round.
4. Gates are assertions. Chromium measurement stays, as the acceptance oracle
   and as the fit measurement; a measured defect is a renderer bug to fix in
   code, never a reason to ask the model again.
5. One IR. Deck IR v1 is the canonical typed plan (it already carries
   `SourceBindingV1` with `useMode: verbatim`); Scene IR remains the edit and
   export projection compiled from the rendered HTML, as today.

### 4.2 Pipeline

```text
source manifest ──┐
                  ├─► Plan step (LLM, typed JSON, deck-level + batched) ─► DeckIR v1
LLM content ──────┘        │  archetype, roles, grouping, emphasis, density
                           ▼
                  Composer (server)
                    archetype library (CSS grid/flex, tokens, density variants)
                    fit ladder (metrics estimate → Chromium measure → next rung)
                    emits HTML with qid / semantic-role / source-block-id
                           ▼
                  Chromium acceptance (existing inspect_authored_dom, readback)
                           ▼
                  existing publication → Scene IR → authored-hybrid PPTX / PDF
```

The Plan step for verbatim restyle is optional. A deterministic classifier
using the enriched manifest (section 4.6) covers title, body, list, caption
and footer roles; the LLM call adds archetype and emphasis choices and can be
skipped entirely for a zero-call, reproducible deck.

### 4.3 Fit ladder

Decided 2026-09-04: one source page is one slide, always. The slide count
never changes, text is never hidden, truncated or shortened, and the ladder
buys room by shrinking the template's footprint before it shrinks the text.
Each rung is deterministic, bounded, estimated from font metrics and confirmed
once in Chromium before the next rung is tried. The rung reached is recorded
per slide in the quality receipt.

Canvas arithmetic: 1280×720 px is 13.333×7.5 in at 96 dpi, so 1 pt = 1.333 px.
The design floor of 15 px is 11.25 pt; a 10 pt source body is 13.3 px, 9 pt is
12 px.

| Rung | Change (verbatim restyle) | Typical content area | Type floor |
|---|---|---|---|
| 0 | template archetype as planned | 1152×544 | template scale |
| 1 | dense variant: more columns, line-height 1.6→1.4, gaps halved, decorative panels dropped | 1152×544 | template scale |
| 2 | type scale down to the design floor | 1152×544 | 15 px body, 12 px supporting |
| 3 | chrome compaction: title band becomes one title line, footer band becomes one line, margins 64→32→24 px; the content area grows about 40% | up to 1232×648 | 15 px, 12 px |
| 4 | source-relative type: one uniform factor applied to each block's own source size, stepping from the design floor down to 1.0× source; hierarchy is the source's own | 1232×648 | each block's source size |
| 5 (terminal) | source geometry: each block is set in its own source rectangle mapped onto the canvas; the template supplies colors, type family, accent and side bands. A box that still overflows from font-metric drift shrinks its text by at most 10%, then grows into free canvas (`composition_capacity._free_rect`); last, the whole page scales uniformly, never below 0.9 | full canvas | source size × page scale |

Rung 5 terminates because the source page laid this content out at this size
once already: a uniform scale of the source's own layout always fits. A 4:3
source maps with uniform scale and the side bands carry the template accent.
The allocator already names this mode (`WritableLayout.placement_strategy =
"sourceGeometry"`), and the manifest already carries the rectangles
(section 3.5); only the type sizes need capturing.

Deck consistency: title typography and position are identical at every rung.
Only band heights, margins and density change, so a rung-4 page beside a
rung-0 page reads as one deck. The receipt lists which pages were set at
source density so the user can see where the template gave way.

Ordinary authored decks (no source) use rungs 0 to 3 and then typed
abridgement: the plan step returns shorter text for named optional fields in
one call. They never reach rungs 4 and 5.

### 4.4 What the LLM does and does not do

Does: one deck-level call for art direction (rhythm, which slides carry a
figure, palette variant), then batched slide calls returning Deck IR slides.
Output is JSON validated by `contracts.py`; a schema failure is a cheap retry
of a few kilobytes, not a re-authoring of a 16k-token document.

Does not: write HTML, CSS, coordinates, widths, font sizes, or source text in
verbatim mode. Does not repair.

Prompt size drops from 14 to 20 KB per slide to a few KB per batch. Provider
time drops from about 90 seconds per slide to a few seconds per slide, and to
zero for verbatim restyle without the plan step.

### 4.5 Reuse map

| Existing component | Keep | Change |
|---|---|---|
| `deck_ir/contracts.py`, `canonical.py`, `validation.py` | yes | admit `SourceBindingV1` at runtime; add `bullets` kind and ordered text runs |
| `deck_ir/grammar.py`, `component_renderer.py`, `zone_layout.py` | yes | become the Composer core; add archetype library (below); fix `shape_kind` enum and chart type coverage |
| `deck_ir/browser_closure.py` | yes | becomes the fit-ladder measurement loop instead of one-shot exhaustion |
| `utils/authored_infographic/` (archetype CSS, skeletons, checker) | yes | the archetype library the Composer renders into; the model no longer pastes skeletons |
| `source_native_generation.py` flow renderer, `source_render_geometry.py`, `composition_capacity.largest_fitting_text_scale` | yes | fold into the Composer's fit estimate; keep `_balanced_contiguous_ends` column balancing |
| `scene_compiler._fit_text_style` (PIL metrics) | yes | shared fit estimate primitive |
| `utils/slide_capture.py` instrumentation, `inspect_authored_dom` | yes | acceptance oracle and fit measurement, unchanged |
| `source_content/pptx.py`, `manifest_store.py` | yes | capture `a:rPr` size/weight/color; keep geometry through the outline |
| `source_content/source_runs.py`, `text_stamping.py`, `readback.py`, `browser_readback.py` | as tests | the renderer writes the bindings; these verify it and never repair |
| authored-hybrid export, publication, checkpoints, fencing, receipts | yes | unchanged |
| `author_slide.py`, `author_deck.py`, `author_batch.py`, `author_slide_rules.py`, `author_slide_prompt.py` | retire after A/B | replaced by the plan call |
| repair round, VQA repair, `author_slide_patch.py`, `authored_patch_engine.py`, capacity reducers, `authored_generation_preflight.py`, `authored_offcanvas_repair.py` | retire | no measured defect is repaired by prose; renderer bugs are fixed in code |
| SlideSpec v1 solver and cascade | retire from generation | keep for `quality.review` on legacy decks until they age out |
| `source_content/allocator.py`, `materializer.py`, `coverage.py` (dead path) | retire | superseded by Deck IR source bindings |

### 4.6 Verbatim restyle specifics

1. Manifest enrichment: capture run-level `sz`, `b`, `i`, color, and keep the
   shape rectangle in the outline. This is a change to `pptx.py` and
   `outline.py`, and it is what lets a classifier tell a footer from a body
   paragraph without a model.
2. Furniture classification by geometry and pattern: shapes in the bottom 8%
   of the page, page-number patterns (`12 / 43`), repeated identical text
   across pages, and placeholder types `ftr`, `sldNum`, `dt` become template
   furniture rendered in the template's bands, never body content.
3. Hierarchy from type size: the largest run on the page is the title
   candidate; sizes map onto the template's type scale by rank, not by value.
4. Tables and charts render as native tables and figures through the existing
   `data-native="table"` contract instead of being dropped.
5. Bindings by construction: the Composer writes
   `data-source-block-id` and the run identity from `source_runs` onto the
   element it creates for each block, and copies the text from the manifest.
   Exclusive ownership, source order and exact punctuation hold by
   construction; the existing readback gates run as assertions and their
   failure is a renderer bug.
6. Images: every occurrence placed by the plan into an image slot; the
   existing visibility seal stays.

### 4.7 Quality, not just validity

The successful server-owned run (section 2.2) shows that structural
guarantees alone produce a text dump. The team's own experiment
(`output/infographic-experiment-20260902/`, decisions recorded in
`NEXT-SESSION-PROMPT.md`) found that designed archetype components approach
image-model quality and that "the difference was design investment, not
technique". Under the Composer that investment compounds: an archetype
improved once improves every deck. Quality selection uses the machinery
already built for it, K candidates (`deck_ir/candidates.py`) scored by
deterministic metrics plus an optional vision critic that chooses, never
edits.

## 5. What this removes

Roughly 20k lines stop being on the product path: the repair round and its
preflight, VQA repair, patch engine, capacity reducers, deterministic
geometry repairs, the stamping and readback tolerances (kept as tests), the
SlideSpec v1 generation hooks, the dead allocator path, and the 20 KB of
prose rules. The failure classes `authored_composition_safety_gate_failed`,
`source_content_browser_readback_failed`, `authored_slide_capacity_exceeded`
and `authored_source_image_visibility_mismatch` become renderer assertions
with a stack trace, not operation outcomes.

## 6. Migration plan

Each phase is independently mergeable and leaves the authored engine
available for the matched A/B the ledger requires
(`authored.generation.dual-engine-comparison`).

| Phase | Work | Acceptance |
|---|---|---|
| 0. Verbatim Composer (2 weeks) | route `source_content_restyle` to the Composer behind `COMPOSER_RESTYLE_ENABLED`; manifest enrichment; furniture and hierarchy classifier; source bindings by construction; fit ladder rungs 1 to 3; rung 4 per decision 7.1 | the stored 35-page and 43-page manifests render 100% of source blocks with zero blocking DOM defects, offline, under 5 minutes, zero provider calls; readback gates pass as assertions |
| 1. Archetype library (3 weeks, parallel with 0) | port `utils/authored_infographic/` archetypes into Composer components with density variants; template tokens from authored styles; furniture bands | blind rating of the 43-page deck against the 09-02 server-owned output and against the best authored output on the same source |
| 2. Ordinary authored on Deck IR (3 weeks) | admit source bindings at runtime; add `bullets` and text runs; lift the 10-slide and 12-component caps or batch across them; fit ladder rung 4 as typed abridgement; fix renderer enum gaps | matched A/B on 20 prompts × 20 slides: success rate, wall time, provider cost, blind quality; Composer must win on the first three and tie on the fourth |
| 3. Retire (1 week) | flip defaults; delete repair machinery listed in 4.5; keep authored HTML for legacy decks read-only | file-size ratchet green; test count drops; no `authored_*_repair` module imported on the product path |

## 6a. Phase 0 implementation record (2026-09-04, same day)

Built by nine parallel agents against the contract in
`services/composer/plan.py` and `contracts.py`. Everything is behind
`COMPOSER_RESTYLE_ENABLED` (default false); the LLM authored path is untouched
when the flag is off.

| Module | Role |
|---|---|
| `services/source_content/pptx_typography.py`, `pptx_text_body.py` | run-level size/bold/italic/color with layout and master inheritance, placeholder type, `slideSize`; old manifests keep their digests |
| `services/composer/classify*.py` | deterministic plan: furniture, title (words only, never KPI numbers), cover, columns → comparison, arrows/ordinals → process, lists, tables, images; bindings partition every run exactly |
| `services/composer/render*.py` | flex-column page (bands grow, never clip), title fit independent of body scale, chrome levels 0–3, wide blocks span all columns, source-geometry mode, attributes written by construction |
| `services/composer/fit*.py`, `estimate*.py`, `measure.py` | the ladder of 4.3 with estimate-first, forced template render before rung 5, rung 5 capped at 3 renders |
| `services/composer/assets.py` | image markers resolved to bytes before every Chromium call, preloaded once per deck |
| `services/composer/pipeline*.py`, `publish*.py`, `receipt.py` | admission → classify → fit (threads, serialized progress) → assertions (stamping must not raise, readback must pass) → publication through the shared authored bundle publisher → `composer-receipt/v1` |
| `scripts/composer_offline_eval*.py` | one-command evaluation on stored decks: HTML, PNG, contact sheets, `report.json` with per-slide rung trail and acceptance |

Measured results (all `artifacts/composer-offline-eval/*`, zero provider calls):

| Deck | Slides | Rungs | Renders | Blocking defects | Source readback | Wall |
|---|---|---|---|---|---|---|
| 35-page (`final2-35`) | 35/35 | 0:31 1:4 | 35 | 0 | 289/289 blocks | 67 s (48 s alone) |
| 43-page (`r12-43`) | 43/43 | 0:2 1:10 2:12 3:13 5:6 | 150 | 5 slides with residual overlap at rung 5 (source geometry of genuinely dense pages) | 965/965 blocks | 223 s |

The same day's baseline through the LLM authored path was 0 of 43 verbatim
restyle operations succeeding, at 25 to 40 minutes each.

Live worker-lane run with the flag on (operation `35bb622d`, job `bb9b2687`,
presentation `7fc147dd`): classify 0.1 s, fit 23 s for 35 slides, assertions
0.5 s, publication 12 s; operation `succeeded`, presentation `published` with
35 slides, receipt `composer-receipt/v1` with rung histogram 0:31 1:4. Three
live defects were found and fixed on the way: concurrent progress reports
tripping the operation version CAS (false `operation_lease_lost`), Chromium
renders blocking the worker event loop, and the composer completing the
operation through the chat-lane CAS instead of the generation lane's
`publish_workspace_generation_artifact` (`public_operation_fence_conflict`).

Known follow-ups: hybrid PPTX export of a Composer deck is not yet exercised
(the staged artifact is the image PPTX, as for Deck IR); thread
`context.extend_lease` into `run_composer_restyle` through the handler
signature; extractor revision in the `manifest_id` seed so re-analysing an
old snapshot does not 409; a "grow" step for sparse pages (the ladder only
shrinks, so light pages sit at the floor with empty canvas below);
`pipeline.py` at 404 lines needs one more split; the deck title falls back to
"Presentation" when the style carries none.

## 7. Product decisions required

1. Verbatim page count. **Decided 2026-09-04: one page is one slide.** The
   fit ladder in 4.3 makes that feasible by compacting chrome, then setting
   dense pages at their own source size, then falling back to source
   geometry. Two ledger entries need superseding: `overflow_policy` gains
   `same_page_fit_ladder` in place of `same_page_recompose_or_fail`, and the
   `template-restyle-contract` clause forbidding reuse of the source layout is
   narrowed to "not as the design basis; permitted as the terminal fit rung
   for pages that fit nothing else". The 15 px floor of
   `authored.quality-composition-first` is relaxed for restyle at rungs 4 and
   5 only, never below the source's own size × 0.9.
2. Engine switch. Confirm that the A/B in phase 2 is the promotion gate, and
   that phase 0 may ship for verbatim restyle before it, since the authored
   path has no successful verbatim run to compare against.
3. Design bar. Name the reference decks the blind rating is scored against.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Composer output looks templated compared with the best LLM layouts | archetype library with density and emphasis variants; K-candidate selection; a vision critic that chooses; the LLM keeps art direction |
| Korean typography metrics differ from PIL estimates | estimate then measure in Chromium (rungs are confirmed, never trusted); Pretendard is bundled |
| Export parity | the renderer emits the exact attributes `authored-hybrid` reads; `hybrid_admission` widens per component kind |
| A fifth engine instead of one | phase 3 deletes; the dual-engine decision names the comparison, not indefinite coexistence |
| Dense source pages with no feasible fit | decision 7.1 gives the ladder a terminal rung |

## 9. Evidence

- Operation tally: scratchpad `analysis/route_states.py` over `workspace_operations` since 2026-08-28, owners `admin` and `rc-admin`.
- Job outcomes, failure families, gate replay over 568 stored slides, layout statistics over 963 stored slides, receipt aggregation: scratchpad `analysis/report.md` (session `d00593c7`, 2026-09-04), scripts `collect.py`, `aggregate.py`, `receipts.py`.
- Successful server-owned run: `output/candidate-54892244-dual-lane/`.
- Design divergence: `docs/source-content-template-restyle-design-20260826.md` §6.2–6.3 versus `services/source_content/pipeline.py:170-217`.
- Render authority: `services/authored_vnext/generation/scene_envelope.py:1-7`; `docs/authored-only-vnext-architecture-design-20260813.md` §8.
- Deck IR source-binding rejection: `services/authored_vnext/deck_ir/integration.py:264-276`, `product_adapter.py:645`.
- Routing exclusion: `services/public_v2_orchestrator.py:305-317`, `services/authored_vnext/generation_route.py:129-188`.
- Gate inventory and deck-kill site: `services/authored_presentation_pipeline.py:758-1424`, `services/authored_presentation_generation.py:2857-2891`.
- Manifest gaps: `services/source_content/pptx.py:290`, `outline.py:1-7, 136-140`.
- Decision records: `.spec-memory/decisions/authored.generation.*`, `source-content.verbatim-*`, `authored.capacity.*`.
