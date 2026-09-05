# Authored quality and speed programme: design v2 (2026-09-05)

Amends `docs/generation-programme-master-design-20260904.md` (the programme
index) after the six-axis read-only diagnosis of the same day. It does not
reopen D9/D14/D15/D16; it adds workstreams F–K below them. Everything here is
for the ordinary `strategy=authored` deck with the 2026-09-05 defaults (free
composition: infographic plan, composition envelope and visual story plan all
off; one slide per authoring call).

v2 (same day) answers the user's review of v1 ("is this the best?"): v1 was
the safe version. v2 adds the levers v1 deferred or omitted — slides that can
see each other (G0), deck tokens in phase 1 (G10), an exemplar slide (G11),
input density (workstream K), an effort A/B (J), the vision advisory in phase
1 (H6) — and splits the prompt bundle in two so a regression reverts by half.
It also drops the personal-token 429 as a first-order risk: the user judged
the episode transient and rare.

Evidence: the diagnosis report ("Authored 품질·속도 진단", artifact published
from session ppt-agent-52), the bench series of session ppt-agent-d0 (runs
8–16, 24-slide prompt fixture, gpt-6-astra), and the file:line references in
each section, all verified against `origin/main` at `2c15e6532`.

## 1. Evidence baseline

### 1.1 Where the wall clock goes

| Fact | Where | Status |
|---|---|---|
| Real authoring concurrency is **2**, not 5. The token rate profile fixes `initial_wave=1`, `steady_wave=2`; `plan_authored_batch_wave` takes `min(state.concurrency, …)`; the 5-thread `_AUTHOR_EXECUTOR` and `author_gate(5)` are ceilings. `author_gate` is dead on the production path because `_preauthor_missing_slides` completes first. | `services/authored_provider_rate_profile.py:41-45,140-146`, `services/authored_batch_policy.py:151`, `services/authored_presentation_pipeline.py:648-671,757,1189` | verified |
| The 2026-09-05 revert to one slide per call did not widen the wave. A 20-slide deck went from 4 waves to 11. | same | verified structure, estimated seconds |
| Per-call author latency on gpt-6-astra: p50 **43 s at low, 63 s at medium/high** (bench runs 8–16). Wave width is the speed lever; effort is a quality lever to be measured. | ppt-agent-d0 bench | measured |
| Waves are `gather()` barriers; every slide's render, image and persistence waits for the last slide to be authored. The overlap code exists in `process_slide` and is bypassed. | `pipeline.py:648-671, 757, 1186-1189, 1420` | verified |
| Each slide is authored blind to its siblings. Deck-wide frozen inputs are only: header contract, chapter prefixes, page-number mode, style brief (prose). No shared CSS/token contract exists (`:root{--`, `css_tokens`, `deck_css`: zero hits). | `utils/llm_calls/author_deck.py:93-130` (`AuthoredDeckPlan`); grep | verified |
| Image generation is one process-wide `asyncio.Lock` unless `ENABLE_PARALLEL_IMAGE_GENERATION`, which removes *all* bounding. | `services/image_generation_service.py:80, 224-227`; `utils/get_env.py:161` | verified |
| Browser-metrics LRU is 32 entries / 32 MB; a long deck evicts its own slides and the deck tail re-renders them for telemetry only. Counter `metrics_recollection_requests` exists. | `utils/slide_capture.py:1094-1120, 1998-2002`; `services/authored_presentation_generation.py:1426, 1614, 2901` | verified |
| Bench SQLite lock retries cost 10–20 min per run. Bench comparisons use per-call latency and stage wall, never job wall. | ppt-agent-d0 bench | measured |

### 1.2 What the prompt does and does not say

Reconstructed effective prompt for a content slide, all default knobs off,
`hyundai-culture-center`, standard verbosity: ≈23.7k chars ≈5.9k tokens.
Style brief + header contract ≈48 %, technical constraints ≈28 %. Zero
exemplars.

| Gap | Where | Measured consequence |
|---|---|---|
| `_TEMPLATE_PAGE_NUMBER_RULES` has a 72 px safe zone but **no bottom-fill target**; `_NO_PAGE_NUMBER_RULES` has "finish close to y=688". The 9304e8e05 geometry brief adds the upper bound (`content_bottom_px`), not a fill direction. | `utils/llm_calls/author_slide_rules.py:36-50, 52-61`; `utils/authored_geometry_brief.py` | run7 s03: ~120 px dead band |
| Type scale states a floor (body ≥18, min 13) but **no ramp**, no mid-level heading tier. | `author_slide_rules.py:246-252` | run7: 311/389 body elements (80 %) below 18 px; one `title` element per slide |
| Chart/table rules exist only in `_STORY_RULES`, delivered by the story marker, which is off. | `author_slide_prompt.py:333-340`; `author_deck.py:_visual_story_plan_applies` | 82 fixture slides: 0 `table`, 0 `svg`, 0 `img`, 0 `ul/li` |
| The only content-fidelity sentence lives in `SlideCapacity.prompt_brief`, which returns `""` when relaxed. | `services/authored_composition_policy.py:149-151, 250-256` | no instruction to keep/expand/compress the outline |
| The two priority sentences both point away from ambition ("FIT IS CRITICAL … over visual ambition"; header prompt "Uniformity … outranks per-slide visual ambition"). | `author_slide_rules.py:136-139`; `utils/authored_header_prompt.py` | run7 "quiet" pages |
| `data-qid` demanded on every visible element incl. "simple shape" and "meaningful grouping element"; declared-qid validation is off (`AUTHORED_SEMANTIC_DUAL_OUTPUT_MODE=shadow`); `ensure_authored_qids` synthesises server-side. | `author_slide_prompt.py:508-511`; `author_slide.py:142-152`; `utils/authored_dom_quality.py:329-345` | ≈500 output tokens/slide (17–21 %) |
| Dead weight: INLINE SVG CONTRACT 1 560 chars (0 svg); ILLUSTRATION_SLOT_RULES 2 030 chars with `illustration_allowed=None` (0 img); registry sha256 digest in the highest-recency slot. | `author_slide_rules.py:113-129, 202-226`; `services/authored_vnext/capability_registry.py:134-142` | ≈900 input tokens/slide |
| Contradictions: uniform `border-radius` vs hyundai "folder-tab"; absolute-positioning rule vs page-number box and header body wrapper; font floor 13/12/15; `keep-all` + `overflow-wrap: anywhere`. | `author_slide_rules.py:131, 152-153, 157-158, 250`; `capability_registry.py:61` | 17–23 elements/run at 12 px |
| `build_design_system` uses `DEFAULT_STYLE_BRIEF` **or** the preset brief; presets lose "make one element dominant", margins, one accent, eyebrow spec, allowed variations. | `author_slide_prompt.py:84-90`; `author_slide_rules.py:165-175` | 37 of 38 styles |
| ROLE vocabulary vs style `SLIDE ARCHETYPES` keys; bridge exists only for reference images. | `author_deck.py:756-772`; `utils/authored_styles.py:418-444` | PROBLEM/PILLARS/OUTCOMES/ROADMAP unaddressed |
| `preview.palette` (6 hexes), `category`, `tags` never reach the model. | `utils/authored_styles.py:99, 127, 521` | run7 s03/s05 navy-dominant vs emerald-led PALETTE |
| Prompt order: slide text first, deck-invariant `design_system` last. | `author_slide_prompt.py:501-513` | prefix caching defeated if the backend caches (unverified) |
| COVER/SECTION/AGENDA/CLOSING author at 10k/8k/8k/6k output tokens and the lowest effort. | `author_slide.py:82-87, 897-912`; `docs/authored-richness-regression-analysis-20260905.md` | cover overlap defect attributed to it |
| Outline floor ≈500 Korean chars/slide; the bench fixture is a one-paragraph prompt (median 128–161 usable chars/slide vs ≈600 for the August reference). The author step expands input ×1.33; it cannot manufacture density. | `utils/llm_calls/generate_presentation_outlines.py:48-64`; `density-regression-root-causes-20260905` | August density is unreachable from a thin fixture |

### 1.3 Gates and repairs

Default per-slide cost: one authoring call, one Chromium render; no vision, no
OCR; receipts are CPU-only. The gates are not slow. They fail decks for things
no reader sees and re-author (which redesigns) where a deterministic rewrite
exists:

| Failure | Path today | Deterministic alternative that exists |
|---|---|---|
| One non-allowlisted CSS property or any `@media`/nested block → document rejected → recovery re-author → second failure kills the deck. | `utils/renderer_isolation.py:116-153`; A2 whitelist `utils/authored_html_normalize.py` | extend A2 to strip every property refused for measurement reasons only; tolerate nested blocks |
| Header-contract violation → re-author → `authored_header_contract_budget_exhausted` → deck fails. | `author_deck.py:1073-1078, 1272-1277, 1599-1624`; `utils/authored_header_validation.py:268` | `apply_header_restyle` (`utils/authored_header_restyle.py:201`) — no generation-path caller |
| Overflow/clipping → 9304e8e05 remake-first (run 10: patch 0/6, remake 4/8). | `services/authored_repair_round_order.py` | `scale_authored_text` (`utils/authored_text_scale.py:256`) — replay-script only |
| Telemetry exact-match on the qid set. | `utils/authored_dom_quality.py:615-644` | tolerate decoration qids |

Not detected although the readback returns the inputs
(`utils/slide_capture.py:893-993`): stretched images, computed font below the
floor, empty bands, deck-wide margin drift, Korean orphan lines. `image` and
`content` categories are telemetry-only. Nothing sees "flag-free but ugly";
only a picture of the deck does.

### 1.4 Export

| Fact | Where |
|---|---|
| `font_embedding` defaults `False` at all five FastAPI call sites; only the editor's main "Hybrid PPTX" button sends `true`. `font-policy.ts` requires embedding for every CJK run, the preflight is fail-closed, so without embedding **every Hangul run is rasterised**. | `utils/export_utils.py:282`; `services/presentation_export_router.py:342`; `presentation_export_adapters.py:90`; `workspace_quality_export_operation_handlers.py:319`; `public_export_operation.py:1832`; `PresentationHeader.tsx:246`; `header/ExportMenu.tsx:55-60` |
| Plain `<table>` is one raster picture; `<table data-native="table">` is a real `<a:tbl>`; the prompt never asks for the attribute. | `browser-source.ts:1239`; `native-table-observed.ts`; `renderer_markup_policy.py:75` |
| Native chart XML is complete but admission returns `false` (chart.dll reopen bug). | `native-chart.ts:81-85, 280-283` |
| Backplate and PDF at `deviceScaleFactor: 1` → 96 DPI; PDF is one PNG per page. | `chrome-runner.ts:338`; `contract.ts:477-478`; `pptx-assembler.ts:89`; `export.ts:454` |
| Prompt overstates export ("gradient … box-shadow … maps these natively"). | `author_slide_rules.py:132-133`; `native-plan.ts:452-454, 1113-1121` |
| Editor style form exposes 5 properties; the patch engine accepts ~50. | `AuthoredElementEditor.tsx:31-37`; `utils/authored_patch_engine.py:79-135` |

## 2. Goals and acceptance

Priority order unchanged: quality of the opened PPTX first, speed second;
nothing the user ruled off the generation path (D14: no best-of-N, no critique
loop) goes back on it.

| Goal | Metric | Source | Target |
|---|---|---|---|
| G-F1 speed | authoring stage wall, 24-slide fixture, medium, wave 5 vs 2 | receipts `authoring.wallMillis` | ≤ 55 % of run 16 |
| G-F2 speed | per-call latency at the wider wave (±10 %); retries | receipts `providerUsage` | no permanent serial downgrade |
| G-G1 typography | body text ≥18 px share; a sub-heading tier present | `slides/*.metrics.json` | ≥ 60 % (from 20 %) |
| G-G2 figures | `<table>`/CSS chart where CONTENT has ≥3 comparable numbers; every value verbatim | tag census + text diff | > 0, 0 invented values |
| G-G3 fill | largest empty rect below the content union on numbered pages | new instrument (H4) | median < 80 px |
| G-G4 coherence | palette conformance: share of colour declarations within the deck palette (±ΔE 6); radius families per deck | new instrument (G10/H4) | ≥ 90 % / ≤ 2 |
| G-G5 rating | the user's side-by-side of contact sheets, run 16 vs branch | user (D12: sole rater) | user accepts |
| G-H1 reliability | decks lost to CSS admission or header contract | `terminalError` histogram | 0 on the fixture |
| G-I1 export | Hangul runs native in a default API export | export quality receipt native/raster | equal to the editor's main button |
| G-K1 input | outline chars/slide on the document-grade fixture | outline receipt | ≥ 450 median |

## 3. Decisions requested (pending the user's review)

| # | Decision | Recommendation |
|---|---|---|
| D17 | Wave width | Code constant stays 2 for the token profile; deployed `.env` sets `AUTHORED_BATCH_WAVE_CONCURRENCY=5`. The initial probe wave of 1 is repurposed as the anchor call (G0), not kept as a 429 probe. |
| D18 | Fast-role budgets | COVER/SECTION/AGENDA/CLOSING back to `AUTHOR_MAX_TOKENS` (16k) and the model's lowest *supported* effort (keep 39aaf45c5). |
| D19 | Prompt verification | Two bundles, each one bench pass and one contact-sheet side-by-side rated by the user: bundle A "density and typography" (G1, G2, G9, G11, K1), bundle B "contract and coherence" (G0, G3–G8, G10). |
| D20 | CSS admission | Strip-not-reject for measurement-only refusals; tag/attribute/URL policy untouched. |
| D21 | Korean export | `font_embedding` defaults to `True` for hybrid PPTX when the deck language is CJK; the compatibility button keeps `false` and says what it costs. |
| D22 | Vision advisory | One deck contact sheet to the configured vision-capable model, advisory only, never a gate, in phase 1 behind a knob that the bench turns on and the user decides for production. |
| D23 | Anchor inheritance | Slides see each other through one anchor slide authored first (the first content slide), whose `<style>` is handed verbatim to every other slide. Not per-slide layouts (that is the story plan the user rejected). |
| D24 | Exemplar | One exemplar slide per style, optional, checked in under `authored_styles/exemplars/`, images elided, ≤ 12k chars. The first one is taken from the August deck the user rated (`2c89713d`), slide chosen by the user; the design proposes slide 6 (bar chart + metric cards). |
| D25 | Content-slide effort | Measured, not assumed: bundle A at low and at medium (+20 s/call, absorbed by the wider wave). The user picks from the contact sheets. |
| D26 | Input density | A document-grade fixture joins the bench; the outline stage gains a density receipt and a soft floor (K1). Prompt fixtures stay for speed comparisons. |

## 4. Workstream F — throughput without changing what the model is asked to produce

### F1. Wave width (env only)
`AUTHORED_BATCH_WAVE_CONCURRENCY=5` in the deployed `.env`. Already registered
(`constants/settings_registry.json`, group `authored.throughput`) and read by
`get_authored_batch_wave_concurrency_env`; clamped to `author_concurrency` (5).
`public_generation_deadline_policy` shrinks in step; margin stays ≥ 2.9×.
Guard: `advance_authored_batch_policy` halves and cools down on 429/5xx/timeout,
restores after 3 successes.

### F2. Image generation: semaphore, not lock
`services/image_generation_service.py:80` → `_get_image_generation_gate()`
returning an `asyncio.Semaphore` sized by new knob
`IMAGE_GENERATION_CONCURRENCY` (integer, default 3, clamp 1–8).
`ENABLE_PARALLEL_IMAGE_GENERATION=true` keeps meaning unbounded.

### F3. Wave barrier → admission semaphore (phase 2)
`pipeline.py:648-671`: `Semaphore(state.concurrency)` + `as_completed`,
policy advanced per call, cooldown checked before each admission. Recovery
waves unchanged. Estimated −30 s / −90 s (10 / 30 slides).

### F4. Metrics cache (phase 2)
LRU to `max(64, 2 × slides)` entries / 96 MB, or read `browser_metrics` back
from the render checkpoint (`pipeline.py:1326-1335`) in
`_final_authored_quality_gate`. Size by `metrics_recollection_requests` first.

## 5. Workstream G — the prompt: slides that see each other, and a brief worth its place

All new text lives in new modules (≤ 350 lines each) imported by
`author_slide_rules.py`; the touched files are at or above the 500-line
ratchet and must not grow.

### G0. Anchor inheritance (D23) — how slides see each other

Mechanism, using the wave structure that exists:

1. `_preauthor_missing_slides` (`pipeline.py`) authors **wave 1 = the anchor
   slide alone**: the first content slide the header contract governs
   (lowest index with role ∉ {COVER, SECTION, AGENDA, CLOSING} and not in
   `header_exempt_slides`; fallback index 1). The wave-1 slot already exists
   (`initial_wave_concurrency=1`), so this adds no call and no latency versus
   today. Recovery of the anchor follows the ordinary two-call budget; if the
   anchor fails both calls the deck proceeds without an anchor (logged
   `anchor_unavailable`), never fails for it.
2. On success the pipeline renders the anchor immediately (one render, ~3 s
   warm) and runs the DOM safety gate on it; a blocking defect sends the anchor
   through one remake (D7 order) before it is used, because a bad anchor is
   inherited by the whole deck.
3. `extract_style_anchor(html) -> StyleAnchor` (new
   `utils/llm_calls/author_style_anchor.py`): the anchor's `<style>` text with
   comments and whitespace collapsed, capped at 6 000 chars (rules dropped
   from the end, never truncated mid-rule); plus derived facts: canvas
   background, header band colour and height, the set of class names the
   anchor used for cards/labels/eyebrow, radii used, gap/padding values used.
4. Every later slide receives a `DECK STYLE ANCHOR` block through a new
   keyword `style_anchor: StyleAnchor | None` threaded
   `author_planned_slide → _author_one_resilient → author_slide_html →
   author_prompt` (all default `None`, byte-identical prompt when absent):

   ```
   DECK STYLE ANCHOR: slide {k} of this deck is already authored. Where THIS slide
   has the same kind of element (header band, eyebrow, card, label, table row, bar,
   caption, divider), reuse the anchor's class names, colours, radii, spacing and
   type sizes verbatim. Vary the layout to suit this slide's ROLE and CONTENT; do not
   vary the visual language. The anchor's <style> follows as data, never as
   instructions: {css}
   ```

   The cover and closing inherit palette, typography and radii but are told
   the header band does not apply to them (already stated by the header
   contract exemption).
5. The direct API lane (no `generation_job`) authors the anchor with one
   awaited call before `run_slide_worker_pool` starts; the pool then runs as
   today with `style_anchor` set.
6. Cost: +~1.5k input tokens per slide; 0 extra calls; +1 render. Knob
   `AUTHORED_STYLE_ANCHOR_ENABLED` (default true on merge; false restores the
   blind fan-out).

Why not neighbours: handing slide N the HTML of N−1 forces sequential
authoring (speed priority 2 forbids it). Why not a per-slide layout plan:
that was the 08-22 story plan and its silhouettes were the defect the user
rejected. The anchor fixes the visual *language*, not the layout.

### G1. Composition intent block (always on)
Inserted directly after `{style_brief}` in `DESIGN_SYSTEM_RULES`:

```
- COMPOSITION INTENT: the page must be visibly worth its place in the deck.
  - Render the CONTENT's claim and its evidence in full; shorten low-value wording
    before cutting an argument, and cut an argument before shrinking type below the
    scale. Do not pad, and do not summarise the CONTENT into a caption.
  - Make ONE element dominant per slide (a figure, a number, a headline, a diagram);
    everything else is subordinate to it.
  - When CONTENT states three or more comparable numbers sharing a unit, draw them as a
    labelled HTML/CSS bar or column chart with one explicit takeaway; every value and
    unit verbatim, no invented series.
  - When CONTENT is a real row/column structure, render a semantic
    <table data-native="table"> with real <th>/<td>; never a div grid.
  - On a numbered page compose so the content finishes close to y=648px (y=688px on an
    unnumbered deck); reach the line by adding evidence and structure, never by letting a
    block cross it.
```
The fill sentence supplements the bound the geometry brief states; the two are
worded as bound and direction, never repeated.

### G2. Type ramp
`_TYPE_SCALES["standard"]` → a ramp: section lead 24–28 px, claim 20–22 px,
body 18 px, label/caption 15 px, table cell 16 px, footnote 13 px floor; "the
floor is a floor, not the size to write at"; at least one sub-heading tier on a
content slide. `concise`/`text-heavy` shift ±2 px. Page-number 12 px furniture
unchanged.

### G3. Output contract narrowed
`author_prompt` closing sentence: `data-qid` on text owners, images, tables,
charts and cards that carry text; drop "simple shape" and "meaningful grouping
element". `ensure_authored_qids` unchanged.

### G4. Dead weight
- INLINE SVG CONTRACT → 4 lines; the validator's rejection message carries the
  detail.
- `prompt_capability_clause()` drops the digest.
- `ILLUSTRATION_SLOT_RULES` appended only when `illustration_allowed` is not
  `None`. A deterministic per-deck share rule in
  `services/authored_deck_plans.py` sets it: every k-th content slide, roles
  COVER/SECTION/AGENDA/CLOSING excluded, knob `AUTHORED_ILLUSTRATION_SHARE`
  (float, default 0.25; 0 disables and removes the block). A bounded budget,
  not a plan.

### G5. Contradictions and inaccuracies
- `border-radius`: one radius family per deck; per-corner radii allowed when
  the style names a tab/folder shape (export demotes those to raster;
  acceptable).
- Positioning: "never pin a *text* block to a fixed `top` under a heading that
  may wrap; absolute positioning is for decorative layers, the page-number box
  and the header body wrapper".
- Font floor 12 px everywhere.
- Korean: `word-break: keep-all` + `overflow-wrap: break-word`; Hangul body
  `line-height` 1.5–1.7; numerals and units non-breaking.
- Export accuracy sentence replaces the "maps these natively" claim.

### G6. Shared universals for presets
Move from `DEFAULT_STYLE_BRIEF` into the shared rules: margins ~64 px unless
full-bleed, one accent hue per slide, eyebrow spec, allowed-variation list.
`DEFAULT_STYLE_BRIEF` keeps palette and fonts only.

### G7. ROLE → archetype bridge
One line in `author_prompt` using the mapping in
`utils/authored_styles.py:418-444`.

### G8. Order
Deck-invariant text first (brand → style brief → composition intent → type
ramp → PPTX safety → fit → header → page number → **deck tokens** → **exemplar**
→ **anchor**), then `direction_block`, then slide blocks, then `CONTENT`,
`page_marker`, closing sentence. Prefix-cache benefit measured, not assumed.

### G9. Fast-role budgets (D18)
`_FAST_ROLE_OUTPUT_BUDGETS` values → `AUTHOR_MAX_TOKENS`;
`_authoring_output_profile` returns the lowest supported effort via
`_normalized_authoring_reasoning_effort`.

### G10. Deck tokens, literal (phase 1, no admission change)
New `utils/llm_calls/author_deck_tokens.py`: from `design_tokens` +
`preview.palette` build a `DECK TOKENS` block of **literal values** the model
must use: `ink`, `accent`, `accent-2`, `surface`, `hairline`, `radius`,
`gutter`, `pad`, `t-display`, `t-h2`, `t-body`, `t-label`, each as a hex/px,
and the sentence "use these exact values for colour, radius and spacing; any
other hue is an error". No CSS custom properties, so `renderer_css_policy.py`
is untouched (its docstring reserves `--*` as a design change; that stays
phase 2, optional). Palette conformance becomes measurable (G-G4).

### G11. Exemplar (D24)
`authored_styles/exemplars/<style-id>.html`, optional per style. Loader in
`utils/authored_styles.py` (one function, cached), injected as
`EXEMPLAR (one slide from a deck rated excellent for this style; reuse its
visual language and density, never its text): {html}` after the deck tokens.
Images elided to `<img data-elided>`; ≤ 12k chars; must pass
`admit_authored_html`. First exemplar: the August `2c89713d` slide the user
picks (proposed slide 6), read from the operational PostgreSQL read-only.
Cost ≈ 2.5–3k input tokens per call; measured in bundle A.

## 6. Workstream H — gates that protect what the reader sees

### H1. CSS admission: strip, don't reject (D20)
`utils/authored_css_strip.py` (new) called from `authored_html_normalize.py`:
declarations whose property is not in `ALLOWED_CSS_PROPERTIES` and is not
`content`, `content-visibility`, `contain` or `--*` are stripped (inline and
`<style>`), receipt `css_property_stripped:<name>`; `@media`/`@supports` with
a plain screen/all query are hoisted, otherwise removed
(`css_at_rule_removed`). Admission is byte-identical afterwards. Golden
tests: each stripped property; clean document byte-identical; `@media print`
removed; `@media screen` hoisted.

### H2. Header contract: deterministic repair before re-author
New `utils/authored_header_repair.py` (`repair_header_contract(html,
contract) -> (html, codes_fixed)`): clamps the band integer into `[min,max]`,
de-duplicates parts (first wins), drops unknown parts' attribute, re-pins the
body wrapper top; re-validates. Called at `author_deck.py:1073` and `:1604`
before the re-author branch, only for geometry/part codes; semantic codes
(missing required part, requirement identifiers) still re-author. Receipt
`header_repair:<codes>`. Knob `AUTHORED_HEADER_DETERMINISTIC_REPAIR` default
true.

### H3. Text-scale rung before remake (phase 2, with 9304e8e05)
`scale_authored_text` bounded by `fit_summary.worst_overflow_ratio` as step 0
of the remake-first order for slides whose only blocking defects are
overflow/clipping. Dropped if it rescues < 20 % on the bench.

### H4. Detectors and deck instruments (phase 1b: instruments; phase 2: flags)
Phase 1b adds **instruments only** (receipt fields, no gate): largest empty
rect below the content union; palette conformance (declared colours vs deck
palette, ΔE ≤ 6); radius families per deck; margin deviation from the deck
median; computed font < 12 px count; stretched-image count. These give G-G3/
G-G4 their numbers. Phase 2 promotes the ones that prove useful to flags and
adds the `object-fit: cover` and dead-`<img>` auto-fixes.

### H5. Telemetry exact-match (phase 2)
`_telemetry_status` tolerates missing qids on `decoration` roles.

### H6. Vision advisory (D22, phase 1)
New `services/authored_vision_advisory.py`: after the deck tail, build one
contact sheet (existing tooling from fbbfd3894; 4×N thumbnails, ≤ 2048 px
wide) from the evidence-ledger PNGs and ask the configured vision-capable
model, once, for `{"slides":[{"index":n,"issues":["…"]}]}` restricted to a
closed vocabulary (empty band, cramped, unbalanced, illegible, off-palette,
broken figure). Written to the receipt as `advisory.vision`; never a gate,
never a repair trigger. Knob `AUTHORED_VISION_ADVISORY_ENABLED` default false;
the bench runs with true. Cost: one call, ~5–15 s per deck.

## 7. Workstream I — export

### I1. Font embedding default (D21)
Default `True` when `pptx_mode == "hybrid"` and the presentation language is
CJK, at the resolver level (`presentation_export_router.py`,
`workspace_quality_export_operation_handlers.py:_resolve_workspace_export_options`),
knob `EXPORT_FONT_EMBEDDING_DEFAULT_CJK` default true; explicit `false` wins.
`ExportMenu.tsx` compatibility copy: "글꼴 미포함: 한글 텍스트는 그림으로
내보내져 PowerPoint에서 편집할 수 없습니다".

### I2. Native tables — covered by G1 (`data-native="table"`); verify with
`native-table-observed` tests plus one authored fixture slide.

### I3. Backplate DPR 2 (phase 2) — `chrome-runner.ts:338`, `contract.ts`,
`pptx-assembler.ts:89`, `raster-pdf.ts`; native geometry must be identical.

### I4. Editor style form and export copy (phase 2, frontend).

## 8. Workstream K — input density

### K1. Outline density receipt and soft floor
`generate_presentation_outlines.py`: record per-slide usable chars (after
zero-width stripping, which 6769d3c2e added) into the outline receipt; when
the median is below 350 chars and the request carried a document, run **one**
targeted expansion call for the slides below the floor ("expand from the
source, never invent"), bounded to one call per deck. Prompt-only requests
get no expansion (there is no source to expand from); they get the receipt.
Knob `OUTLINE_DENSITY_FLOOR_CHARS` default 350, 0 disables.

### K2. Document-grade bench fixture
The bench adds one document-upload fixture (candidate: the source deck of
`output/hyundai-reference-90plus-20260901/`, or a 10–20 page Korean proposal
PDF the user names). Density and rating comparisons against August use this
fixture; the 24-slide prompt fixture stays for speed and reliability.

## 9. Workstream J — measurement protocol

1. Branch off `origin/main`; `uv run pytest` (targeted + full), `uv run ruff
   check .`, `npm run test:file-size-ratchet`, template-v2/export fidelity
   tests untouched-green.
2. Bench passes by ppt-agent-d0 on the 24-slide prompt fixture, gpt-6-astra,
   three plan knobs off, one slide per call, against run 16:
   - P0: phase 0 only, wave unset → confirms G9/F2/I1 are quality-neutral.
   - P1: phase 0 + wave 5 → G-F1/G-F2.
   - A-low, A-med: bundle A at effort low and medium (wave 5) → D25.
   - B: bundle A (chosen effort) + bundle B → G-G4, anchor coherence.
   - K: bundle A+B on the document-grade fixture → G-K1, density vs August.
3. Read: `authoring.wallMillis`, per-call `latencyMs` p50/p90, retries,
   `terminalError`; `slides/*.metrics.json` font histogram, tag census, qid
   count, output tokens; the new H4 instruments; the H6 advisory; contact
   sheets.
4. The user rates contact sheets side by side at each of A and B (G-G5).
5. Prefix-cache check across G8: per-call `latencyMs` and input-token
   accounting for slides 2..N at equal output length; no separation ⇒ the
   reorder stays for intent-first reasons only.

Bench job wall is never quoted (SQLite lock retries).

## 10. Sequence

| Phase | Items | Lands |
|---|---|---|
| 0 | F1 (env), F2, G9, I1 | branch 1 |
| 1 | bundle A: G1, G2, G11, K1 · bundle B: G0, G3–G8, G10 · H6 | branch 1, measured as two bundles |
| 1b | H1, H2, H4 instruments, K2 | branch 1 |
| 2 | F3, F4, G10 as CSS variables (optional), H3, H4 flags/auto-fixes, H5, I3, I4 | after the phase-1 ratings |
| 3 | streaming author→tail, checkpoint batching, polling backoff, native chart re-seal, `<img>` decoration split, OpenAI effort branch, per-call model tier, streaming author call, production renderer batch pipe | separate designs |

## 11. Files and ownership

| File | Change | Owner |
|---|---|---|
| `utils/llm_calls/author_slide_brief.py` (new) | G1, G2 text, G4 SVG text, G5, G6 | ppt-agent-52 |
| `utils/llm_calls/author_style_anchor.py` (new) | G0 extraction + block | ppt-agent-52 |
| `utils/llm_calls/author_deck_tokens.py` (new) | G10 | ppt-agent-52 |
| `utils/llm_calls/author_slide_rules.py` | import new modules; `_TYPE_SCALES`; `DESIGN_SYSTEM_RULES` order/placeholders | ppt-agent-52 |
| `utils/llm_calls/author_slide_prompt.py` | `author_prompt` order, closing sentence, G7, anchor/exemplar/tokens blocks, illustration gating | ppt-agent-52 |
| `utils/llm_calls/author_slide.py` | `_FAST_ROLE_OUTPUT_BUDGETS`; `style_anchor` kwarg pass-through; must not grow net | ppt-agent-52 |
| `utils/llm_calls/author_deck.py` | `style_anchor` kwarg; H2 call sites; must not grow net (extract helpers to `utils/authored_header_repair.py`) | ppt-agent-52 |
| `services/authored_presentation_pipeline.py` | anchor wave, anchor render+gate, `style_anchor` threading | ppt-agent-52 (coordinate: d0 owns the repair round in `authored_presentation_generation.py`) |
| `utils/authored_styles.py`, `authored_styles/exemplars/` | G11 loader, exemplar file | ppt-agent-52 |
| `services/authored_vnext/capability_registry.py` | digest out of the prompt | ppt-agent-52 |
| `services/authored_deck_plans.py` | illustration share | ppt-agent-52 |
| `utils/authored_css_strip.py` (new), `utils/authored_html_normalize.py` | H1 | ppt-agent-52 |
| `utils/authored_header_repair.py` (new) | H2 | ppt-agent-52 |
| `services/authored_vision_advisory.py` (new) | H6 | ppt-agent-52 |
| `utils/authored_deck_instruments.py` (new) | H4 instruments | ppt-agent-52 |
| `services/image_generation_service.py`, `utils/get_env.py`, `constants/settings_registry.json` | F2 + all new knobs | ppt-agent-52 |
| `utils/llm_calls/generate_presentation_outlines.py` (+ new `outline_density.py`) | K1 | ppt-agent-52 |
| `services/presentation_export_router.py`, `workspace_quality_export_operation_handlers.py`, `ExportMenu.tsx` | I1 | ppt-agent-52 |
| `authored_terminal_slide_fallback.py`, `authored_repair_round_order.py`, `authored_author_stage_rescue.py`, `authored_geometry_brief.py`, `authored_presentation_generation.py` repair round | untouched | ppt-agent-d0 |

Every new module ≤ 350 lines; no touched file above the ratchet baseline may
grow net (`author_slide.py` baseline 719 vs 1 258 today: the change there
must be net-negative — move the reasoning-effort table and
`_authoring_output_profile` into a new `author_slide_effort.py`).

## 12. Flags

| Knob | Default | Phase | Rollback |
|---|---|---|---|
| `AUTHORED_BATCH_WAVE_CONCURRENCY` | unset (=2) in code; 5 in the deployed `.env` | 0 | unset |
| `IMAGE_GENERATION_CONCURRENCY` (new) | 3 | 0 | 1 |
| `EXPORT_FONT_EMBEDDING_DEFAULT_CJK` (new) | true | 0 | false |
| `AUTHORED_STYLE_ANCHOR_ENABLED` (new) | true | 1 | false = blind fan-out |
| `AUTHORED_ILLUSTRATION_SHARE` (new) | 0.25 | 1 | 0 |
| `AUTHORED_EXEMPLAR_ENABLED` (new) | true (no-op for styles without a file) | 1 | false |
| `AUTHORED_VISION_ADVISORY_ENABLED` (new) | false | 1 | — |
| `OUTLINE_DENSITY_FLOOR_CHARS` (new) | 350 | 1 | 0 |
| `AUTHORED_HTML_NORMALIZE_ENABLED` (exists) | true | 1b | false |
| `AUTHORED_HEADER_DETERMINISTIC_REPAIR` (new) | true | 1b | false |

All registered in `constants/settings_registry.json`, read through
`utils/get_env.py`, never at request time from disk.

## 13. Risks

- A bad anchor is inherited by the deck: mitigated by rendering and gating the
  anchor first with one remake; measured by the H4 coherence instruments.
- The exemplar may be imitated too literally (layout copied): the block says
  "language and density, never text or layout", and bundle A's rating decides.
- Two bundles double the bench passes; that is the price of attribution.
- H1 strip could remove a declaration that changed geometry; the strip list
  excludes the measurer-read properties and fidelity tests must stay green.
- G4's illustration share reintroduces pictures removed with the archetype
  plan; bounded, deterministic, `0` disables.
- K1's expansion call adds one provider call to document decks whose outline
  is thin; bounded to one per deck.
- Wider waves may raise 429s on the personal token; the user judged the
  episode transient, the batch policy self-corrects, and the receipts keep the
  count so the judgement can be re-checked.

## 14. Related documents

- `docs/generation-programme-master-design-20260904.md` — programme index;
  this document is its workstreams F–K.
- `docs/authored-free-composition-default-20260905.md` — D16, the defaults
  this design assumes.
- `docs/authored-richness-regression-analysis-20260905.md` — the August
  baseline the ramp, chart rules and exemplar aim back at.
- `docs/authored-fit-magnitude-measurement-design-20260904.md` — M1/M2
  instruments reused by H3/H4.

## 15. Implementation status (2026-09-05, evening)

Branch `feat/authored-quality-speed-20260905`, built by eleven parallel
implementers and two verifiers after this design. Everything below is on the
branch and knob-gated; defaults are as listed unless a row says otherwise.

| Item | Module(s) | Default | Verified how |
|---|---|---|---|
| F1 wave width | env only (`AUTHORED_BATCH_WAVE_CONCURRENCY`) | set to 5 in the deployed `.env` at merge | bench P1 |
| F2 image semaphore | `image_generation_service.py`, `IMAGE_GENERATION_CONCURRENCY` | 3 | unit (overlap/wait) |
| F3 wave barrier removed + cooldown wait | `services/authored_wave_scheduler.py`, `AUTHORED_WAVE_BARRIER_DISABLED` | true | unit; `cooldown_wait_seconds` on the pipeline result, written to the attempt timing receipt as the authoring stage's `cooldownWaitSeconds` |
| F4 metrics cache | `utils/authored_metrics_cache_policy.py`, `services/authored_final_metrics.py` | 64 entries / 96 MB, checkpoint readback first | unit |
| author→tail streaming (phase 3) | `services/authored_tail_streaming.py`, `AUTHORED_TAIL_STREAMING_ENABLED` | true | unit on the real pipeline with stubs |
| streaming author call (phase 3) | `utils/llm_calls/author_slide_stream.py`, `AUTHORED_AUTHOR_STREAMING_ENABLED` | **false** | unit with a fake streaming client |
| G0 anchor inheritance | `utils/llm_calls/author_style_anchor.py`, `services/authored_style_anchor_stage.py`, `AUTHORED_STYLE_ANCHOR_ENABLED` | true | unit; bench B |
| G1–G8 prompt bundle | `utils/llm_calls/author_slide_brief.py`, `author_slide_rules.py`, `author_slide_prompt.py` (505 lines) | on | unit; bench A/B + user rating |
| G4 illustration share | `services/authored_deck_plans.py`, `AUTHORED_ILLUSTRATION_SHARE` | 0.25 | unit |
| G9 fast-role budgets | `utils/llm_calls/author_slide_effort.py` | 16k, lowest supported effort | unit (env semantics pinned) |
| G10 deck tokens (literal) | `utils/llm_calls/author_deck_tokens.py` | on | unit |
| G11 exemplar | `utils/authored_style_exemplars.py`, `authored_styles/exemplars/hyundai-culture-center.html` (August deck `2c89713d` slide 6, images elided) | `AUTHORED_EXEMPLAR_ENABLED=true` | unit (admission) |
| system-message hoist + prompt cache key | `utils/llm_calls/author_prompt_split.py`, `author_prompt_cache_key.py`, `AUTHORED_PROMPT_SYSTEM_HOIST_ENABLED`, `AUTHORED_PROMPT_CACHE_KEY_ENABLED` | true / true | unit; server-side caching measured via `cached_tokens` on the bench |
| H1 CSS strip + `name` attribute strip | `utils/authored_css_strip.py`, `authored_html_normalize.py` | on (`AUTHORED_HTML_NORMALIZE_ENABLED`) | golden tests |
| H2 header deterministic repair + eyebrow prefix | `utils/authored_header_repair.py`, `authored_header_eyebrow_repair.py`, `author_deck_header_repair.py`, `AUTHORED_HEADER_DETERMINISTIC_REPAIR` | true | unit + wiring tests |
| H3 text-scale rung | `services/authored_repair_round_order.py`, `AUTHORED_TEXT_SCALE_RUNG_ENABLED` | true | unit |
| H4 deck instruments | `utils/authored_deck_instruments.py`, `services/authored_deck_instruments_stage.py` → receipt `instruments.deck` | true | unit |
| H5 telemetry decoration tolerance | `utils/authored_dom_quality.py` | on | unit |
| H6 vision advisory | `services/authored_vision_advisory.py`, `authored_vision_contact_sheet.py` → receipt `advisory.vision` | **false** (bench turns it on) | unit (parse/skip paths); live call on the bench |
| I1 CJK font embedding default | `utils/export_font_embedding_default.py`, router + workspace resolver, `EXPORT_FONT_EMBEDDING_DEFAULT_CJK` | true | unit |
| I3 backplate/PDF DPR 2 | `lib/authored-hybrid/capture-scale.ts`, env `AUTHORED_HYBRID_BACKPLATE_SCALE` | 2 | node test: native XML byte-identical at 1 vs 2 |
| I4 editor allowlist, export copy | `authored-editable-style-properties.ts`, `ExportMenu.tsx` | on | tsc/lint |
| `<img>` decoration split (phase 3) | `lib/authored-hybrid/native-image-decoration.ts` | planner only, **not wired** (needs a classify→plan schema change) | node tests |
| native chart re-seal (phase 3) | `native-chart.ts`, env `AUTHORED_HYBRID_NATIVE_CHARTS` | **off**; chart.dll reopen bug unverified (needs PowerPoint COM) | node tests with the flag on |
| K1 outline density | `utils/llm_calls/outline_density.py`, `OUTLINE_DENSITY_FLOOR_CHARS` | 350; document decks buffer the outline stream when expansion is possible | unit |
| checkpoint batching, local-stage queue, poll backoff (phase 3) | `services/authored_checkpoint_stage_batch.py`, `authored_checkpoint_fence.py`, `local_stage_receipt_queue.py`, `workspace_poll_backoff.py`, `workspace_worker_lane_probe.py` | batching true, HTML dedupe **false**, async local stage true, idle ceiling 5 s | unit (commit count 7→6, sessions 9→6) |
| renderer lease + framed worker protocol + warm-up (phase 3) | `utils/slide_capture_worker_lease.py`, `renderer_frame_codec.py`, `authored_renderer_worker.py`, `contracts/authored-renderer/*`, `utils/renderer_warmup.py` | lease true, framed protocol **false** (deployed image must speak frames), warm-up true | unit incl. real subprocess session; Docker `serve` round trip unverified |
| provider parity (phase 3) | `utils/llm_config.py` OPENAI effort branch, `llm_provider.get_model_for`, `OPENAI_REASONING_EFFORT`, `LLM_LIGHT_MODEL` | unset | unit; no caller adopts the model tier yet |
| author-stage rescue reachable from the pre-author pass | `services/authored_author_stage_deferral.py` | on with `AUTHORED_PUBLISH_FLAGGED_SLIDES` | unit on both lanes |
| cached tokens in the provider usage receipt | `services/provider_usage_cached_tokens.py`, migration `b7c1d4e9f2a8` | on | unit + migration round trip |
| `cachedTokens` / `cooldownWaitSeconds` in the attempt timing receipt | `services/provider_operation_usage.py` (split out of `provider_usage_instrumentation.py`), `authored_generation_timing_receipt.py`, `presentation_generation_operation_handler.py` | on | unit (snapshot totals, writer accepts/omits/refuses) |
| outline expansion has its own admission slot | `utils/llm_calls/outline_density.py` (`outline-expansion` prefix), `utils/structured_semantic_slots.py`, `llm_utils.generate_structured_with_schema_retries` | on | unit (slot and durable key distinct from the outline stream's) |

Not done, by decision or blocker: `begin_author_stage` adoption (would spend
ordinal 1 before the call), CSS custom-property tokens (literal tokens
instead), a `runner_wake` row (read-only probe instead), wiring the image
decoration planner (schema change), qualifying native charts in PowerPoint.

### Reconciled on the rebase onto `serve/main-20260902`

The branch now sits on `af42f57ae`. Three things the concurrent session's work
made necessary, all resolved keeping both sides:

* The structured call's semantic slot takes BOTH a `prefix` (which call site)
  and a `suffix` (which run of that site). The suffix is the content brief's
  retry fix; the prefix is what lets the K1 outline expansion share the
  `outline` provider stage with the outline stream without being admitted as a
  replay of it.
* Alembic has one head again: `b7c1d4e9f2a8` is re-parented onto the owner
  row-level-security revision `b5a0d3e7c9f1`.
* `tests/integration/test_authored_worker_lane_e2e_overload.py` pins
  `AUTHORED_WAVE_BARRIER_DISABLED=false` for its wave-shape assertions: only
  the barrier scheduler plans waves, and the shipping default admits
  continuously. A second test covers the default, where the outcome (the
  overloaded slide takes exactly one recovery call and the deck publishes
  complete) is what must hold.

Bench protocol (§9) runs on ppt-agent-d0's bench against run 16 (prompt
fixture) and run 17 (document fixture).

### 15.1 Bench-driven fixes (2026-09-06)

| Run | What the bench found | Fix |
|---|---|---|
| 24 (P0) | Legacy barrier lane sealed slides evicted from the browser-metrics LRU into the evidence ledger with `metrics=None`; the final-metrics readback checked only the list length and the v2 gate blocked on `capacity.browser_observation_missing`. | `ea4f2bd5a`: per-slide `source_digest` identity before reuse, recollect otherwise; one resolved metrics set for every consumer; structured `authored_quality_gate_rejected` log and error message. |
| 25 (P1) | Speed goal met (authoring wall 51 % of parent) but body text median fell 18→16 px and the ≥18 px body share 74 %→29 %: the ramp read as a menu. | `c3164852a`: body copy "18px or larger", label size reserved for eyebrows/axis/units/captions. Run 27 restored the parent's 67 % ≥18 px with more text per slide and authoring wall 50 % (1091 s total vs 2577 s). |
| 26 (P0) | Cover h1 split into qid-bearing spans → no text-owning title → `title:semantic_role_missing` ended the deck after 37 min. Also: the stamper marks every h1/h2 as title, and bundle A's many h2 card headings made it fire up to seven times per body slide (parents 22/23: exactly one title owner per slide; the seven-title shape appears only on branch runs). | `c907f094a`: `utils/authored_semantic_role_repair.py` promotes exactly one text owner to title when none exists and, at the pre-gate site, demotes extra title owners to supporting; the prompt now teaches the `data-semantic-role` vocabulary and "exactly one title". |

Open after these: the stamper's "every h2 is a title" rule should become single-title
selection inside `ensure_authored_semantic_roles` itself (that file sits at its ratchet
baseline; needs an extraction first); slides that pass through gate repair or remake
after the pipeline site lose the demotion until then.

| 29 (A-med) | The gate admitted a remade slide whose card copy still overflowed: the escaping paragraph sat in an `overflow:visible` card, and the pairwise overlap check collapsed the paragraph×next-card pair to the two non-overlapping cards. The remake brief had never carried the measured defects either (`LOWER_DENSITY_HINT` was 203 chars against a 200-char detail cap). | `3881426ed`: `utils/authored_dom_text_escape.py` blocks glyph ink that leaves its nearest card-like ancestor (`text_escapes_card`), feeds the text-scale rung, and the measured defects now travel first in the remake brief. |
| 29 (A-med) | The attempt timing receipt was silently absent from one operation result: a single-shot CAS amend lost a version race under SQLite lock contention and returned without logging. | `8172fce55`: retry ×3 against a re-read row; on final loss log ERROR and write `timingReceiptError`. |
| 30 (B) | Slide 1 authored with `body=0` (everything `supporting`) → `body:semantic_role_missing` ended the deck. The anchor stage's gate never ran (sync context manager inside `async with` → TypeError swallowed as "render unavailable"; the anchor was inherited ungated). The vision advisory returned empty output. | `24636f954`: body-role mirror of the title repair + prompt teaches body vs supporting. `07db1d4a8`: anchor rendered and gated on the job lane with an `anchor_ready … gated=` log; advisory sheet ≤ 1600 px / 1.5 MB and the failure reason named in the receipt (still advisory; the empty output is a provider-side behaviour on image input, unverified). |
