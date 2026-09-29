# SVG style references on non-16:9 canvases: experiment design (2026-09-29)

Status: design only. No code was changed. Facts below were read from the worktree on branch
`serve/main-20260902` on 2026-09-29; numbers marked "estimate" were not measured.

Related: `docs/canvas-bench-decision-rule-design-20260929.md` (pooled rule, implemented in
`servers/fastapi/scripts/canvas_bench*.py`), `docs/svg-text-overlap-false-positives-design-20260929.md`
(ink-box fix `e8475ee6f`, metric epoch), `docs/authored-style-samples-design-20260928.md`,
`docs/design-pack-canvas-family-design-20260929.md`.

## 0. Question and decided direction

Hypothesis H: the svg author is shown 16:9 style reference images on every canvas, and that anchors it to
16:9 compositions on 1:1, 21:9, A4 and portrait decks (content letterboxed into a 16:9 region, three
columns on a square page, an empty band on an ultrawide page).

Step 2 (this document) compares reference conditions on the same canvas:

- **A** current references (unchanged product behaviour);
- **B** no references;
- **C** a canvas-neutral "style board" per style (palette, type scale, component treatments, imagery
  tone; no slide composition).

Step 3 acts on the result (section 7). This experiment measures the reference effect only; it does not
touch the 16:9 default.

## 1. Facts: how references reach the model

All references are file:line into `servers/fastapi/`.

**Source of the images.** `authored_styles/*.yaml` key `reference_images: {role: [path,...]}`, paths relative to
`authored_style_assets/`. 45 YAML files exist; 39 have references, 6 have none (`atelier-monograph`,
`decision-atlas`, `exact-osb-last`, `product-lab`, `soft-editorial`, `strategic-insight`; for those A equals
B by construction, so they are out of scope). 39 of 45 briefs also contain the literal text `1280x720`
(see threat T1). Reference roles across the 39 styles: `content` 39, `cover`/`data`/`timeline`/`closing` 8
each, `architecture` 6, `section` 2, `agenda` 1. Typical style: exactly one image (the `content` role).
Eight styles carry role-specific images (`chroma-fold`, `cyber-ai`, `devax-si-proposal`,
`hyundai-culture-center`, `osb-smart-banking`, `screenshot-blue`, `tech-blueprint`, `kbs-company-proposal`).
Of 80 role entries, 73 have one image and 7 have two (`kbs-company-proposal` only). Image sizes (87 files
read): 1280x720 x49, 1123x718 x12 (about 1.56), 1600x900 x6, 1672x941 x6, 750x1083 x14 (0.69, all
`kbs-company-proposal`). Mean file size about 300 KB.

**Snapshot construction (once per deck, frozen).**
- `services/svg_generation_admission.py:158-205` `pin_svg_style` builds the snapshot once before durable
  admission and caches it on `request._svg_style_snapshot` ("never reselect on job takeover"). It calls
  `utils/svg_style_adapter.py:40` `adapt_svg_style(style, width, height, request=...)`.
- `svg_style_adapter.py:66-86` reads every referenced file, rejects anything over 1536 KiB
  (`svg_style_reference_size_limit`), accepts only PNG or JPEG magic bytes
  (`svg_style_reference_format_unsupported`; WebP is rejected), and stores `{digest, data_url}` per role in
  `references`.
- `services/generation_core/drivers/svg/project_style.py:55-71` puts `references` into the flat snapshot
  dict; `digest` (`svg_style_adapter.py:110`) hashes the whole dict including the image bytes.

**Per-slide call.** `services/generation_core/drivers/svg/author.py:145` calls
`utils/llm_calls/author_slide_svg.py:103` `author_slide_svg(content, style_snapshot, role, ...)` without
`reference_images`, so the snapshot path is used:
- Selection: `author_slide_svg.py:124-128`, `references.get(role) or references.get("content")`, first two
  entries (`[:2]`). Roles present in the deck plan select their own image; any other role falls back to
  `content`. So a call carries 0, 1 or 2 style images, then the offered source or icon images
  (`:129-131`). The preflight at `:96-101` validates the same selection.
- Attachment: `author_prompt_split.py:168-198` `author_messages` appends the images as `ImageContentPart`
  after one `TextContentPart` in a single user message. The bytes are sent as stored: no resize, no
  re-encode. Resolution is therefore the file's native size (mostly 1280x720, up to 1672x941).
- Text next to the images: the design JSON (`author_slide_svg.py:112`, everything except `references` and
  `diagnostics`) contains `canvas: {width, height}`, tokens, `common_guidance` (the YAML brief lines) and
  `provenance.references = "authored_style_role_images"`. The system rules
  (`author_slide_svg.py:35-49`, `svg_author_rules` `:88-92`) say only "References show art direction only.
  Do not ... copy reference-image text." Nothing states the images' size or aspect, and no image caption
  exists. For a non-baseline canvas `author_canvas_shape.composition_clause` appends an authoritative
  per-shape clause (SQUARE: focal column or 2x2, never three columns; ULTRAWIDE: 2-3 columns, no long
  empty band; PORTRAIT: one column; wide landscape such as A4: prefer one or two columns). The clause is
  text only and sits in the system prompt, not next to the images.
- The brief text is not canvas-adapted for svg: `apply_canvas_tokens`
  (`author_slide_design_system.py:140`) rewrites only the HTML author's rules. `common_guidance` reaches the
  svg prompt verbatim ("At 1280x720, ...", "12-column research grid", "two-column explanation").

**Receipts.** Each authoring attempt already records `input_image_count` and `input_image_bytes` and
`style_digest` (`author_slide_svg.py:145-150` of the returned receipt), so an arm can be verified per slide
(B must show 0 style images).

**Cost per call (estimate).** At most two extra images per call, usually one of about 300 KB. Vision-token
cost of one 1280x720 image is on the order of 1-2k tokens depending on provider, against a text prompt
that is several times larger, and an svg deck makes about 8 authoring calls plus retries
(`logicalCalls = 10 + retries` per the decision-rule doc). So the references are a small share of tokens
per deck; arm B saves little money and little time. The experiment cost is wall-clock (section 5), not
tokens. Exact figures come from `input_image_bytes` in the receipts and provider usage, not from this
estimate.

**Can a request switch references today without code?** No. There is no knob, env var, request field or
preset field for it: `reference_images` come from the YAML, the request model
(`models/generate_presentation_request.py`) has no such field, and `authored_style` selects the whole
YAML. `author_slide_svg` has a `reference_images=` parameter but the svg driver never passes it (only
`svg_page_remake.py:52` shares the call). Editing the YAML per arm is not acceptable: it changes the
default behaviour, the style digest and every concurrent session on the shared tree.

**The style samples the other session added.** `servers/fastapi/authored_style_samples/` does not exist in
this worktree (no manifest, no WebP; only the scripts `scripts/generate_authored_style_samples.py`,
`authored_style_samples_*.py`, `check_authored_style_samples.py` and `utils/authored_style_samples.py` are
tracked). By the design (`docs/authored-style-samples-design-20260928.md` D8) they are 12-slide 16:9
decks, 1280x720, WebP with 96 KB stage and 14 KB thumb caps. Their only consumer is the gallery API
(`api/v1/ppt/endpoints/authored.py:12,163` via `utils/authored_style_samples.py`); nothing in generation
reads them. They are unsuitable as arm C: they are full 16:9 slide compositions (the thing under
suspicion) and WebP is rejected by the reference path. Their use here is limited to giving human judges a
"how this style looks natively" view if wanted (open decision O6).

## 2. Experiment switch (bench-only, removable)

Requirement: switch the reference condition per request, freeze it with the snapshot, record it in
receipts, leave 16:9 default behaviour and prompt bytes unchanged, and be removable.

Recommended mechanism, three touch points plus one new module:

1. **New module** `services/generation_core/drivers/svg/reference_condition.py`, the only place with
   logic: `apply_reference_condition(references, condition, style_id, board_dir) -> (references, meta)`.
   - `current` or `None`: returns `references` unchanged, `meta=None`.
   - `none`: returns `{}`; `meta={"condition": "none"}`.
   - `board`: returns `{"content": [board]}` where `board` is `{digest, data_url}` for
     `<board_dir>/<style_id>/board.png` (PNG, at most 1536 KiB, so it passes the same validation as today);
     one image is sent for every role, matching the usual one-image call. Missing board is a hard
     `svg_reference_board_missing:<style>` (422), never a silent fallback to A.
2. **Hook** in `svg_style_adapter.adapt_svg_style` after the `references` dict is built (`:66-86`, before
   `build_style_context`): `references, meta = apply_reference_condition(...)`, condition read from
   `getattr(request, "_svg_reference_condition", None)`. Absent condition returns before anything else
   runs, so the default path executes the same statements as today. If `meta` is set, store it as
   `result["reference_condition"] = meta`.
3. **Prompt exclusion.** `author_slide_svg.py:112` dumps every snapshot key except `references` and
   `diagnostics`; add `reference_condition` to that exclusion set so the design JSON is byte-identical
   across arms and across the default path (the key exists only when the bench sets it). The rules text
   still contains the sentence "References show art direction only"; leave it in all arms so the only
   difference between arms is the images (an arm-B note is in T4).
4. **Request field, gated.** `GenerateRequest` gets a bench-only `bench_reference_condition:
   Optional[Literal["current","none","board"]] = Field(default=None, exclude=True)`; admission
   (`pin_svg_style`, before adapt) rejects any non-null value with 422 `bench_field_disabled` unless the
   backend env `CANVAS_BENCH_ENABLED=true` is set (the isolated bench backend sets it; production never),
   and sets `request._svg_reference_condition`. Board directory from env `SVG_STYLE_BOARD_DIR` (bench-only,
   outside the packaged assets).
5. **Receipts.** Author receipt (`author_slide_svg.py:145`) gets `reference_condition` from the snapshot
   (absent when default); the bench receipt schema gets `referenceCondition` and `boardDigest`. Because the
   condition rides in the frozen snapshot, job takeover keeps it, and `style_digest` differs per arm.

Why a request field and not an env-only knob: an env knob is process-wide, so three arms would need three
backends (three ports, three app-data dirs, three restarts), and arms would no longer be interleaved in
time, which is the main defence against provider drift (T3). The field lets one isolated backend run all
arms in randomized order. Fallback if the field is judged too invasive: env-only knob read at
`pin_svg_style`, three isolated backends run in lock-step.

Removal: delete the module, the hook lines (one call, one dict key), the exclusion entry, the request
field and the bench-enabled check. The default path is unchanged in behaviour before and after because
`None` short-circuits. Existing tests that pin prompt bytes must still pass unchanged (add one test that
the prompt for `current` equals the prompt without the feature).

Bench side: `canvas_bench_matrix.Deck` (`canvas`, `manuscript`, `repeat`) gets an `arm`; the request body
(`canvas_bench.py:_request_body`) adds `bench_reference_condition`. `plan_matrix` blocks by
(canvas, style, manuscript, repeat) with all arms inside one block and a seeded arm order (section 5).
Not built here.

## 3. Style board spec (arm C)

**Purpose.** Convey the style's visual vocabulary without conveying any slide composition, so the model
cannot copy a 16:9 layout from it.

**Format.** One PNG per style, 1280x1280 (1:1). Reason: on a log-aspect scale 1:1 is the centre of
{16:9 = +0.58, A4 landscape = +0.35, 21:9 = +0.85, 1:1 = 0, A4 portrait = -0.35, 9:16 = -0.58}, so no
target canvas is nearer the board than another. Long edge equals today's reference size, so per-call
image cost stays comparable (a 1280x1280 image costs about 1.7x a 1280x720 one in vision tokens,
estimate). At most 1536 KiB, PNG (the reference path accepts only PNG/JPEG). One board per style, sent for
every role (per-role boards are out of scope; today 31 of 39 styles use one image for every role anyway).

**Content, fixed 3x3 grid of tiles with equal gutters and no slide chrome** (no title bar, page number,
footer, hero-plus-columns arrangement; every tile the same size, so the sheet reads as a specimen sheet,
not a slide):

1. Palette: swatches with hex labels and roles (background, surface, ink, primary, accent, muted), from
   `preview.palette` and tokens.
2. Type scale: heading, body and mono specimens in the style's own fonts, real Korean and Latin lines plus
   tabular numerals, with the size steps labelled as ratios of the body size, not as pixels at 1280x720.
3. Emphasis and rules: rule weights, underline or highlight treatment, tag or chip, divider.
4. Card and panel: fill, border, corner radius, shadow or none, header strip.
5. Table: header row, zebra or flat rows, numeric alignment.
6. Chart language: bar and line series colours, axis and label treatment, direct labels.
7. KPI or callout: number plus caption, quote or callout box.
8. List and step: bullet, numbered marker, connector style (only if the style uses them).
9. Imagery tone: icon style (stroke or fill, weight), image frame treatment (duotone tint, radius, border)
   as neutral placeholders, plus a one-line tone word set (for example "quiet, matte, high contrast").

**Forbidden in a board:** the words "slide", "16:9", any pixel canvas size, a page frame, more than one
tile larger than its neighbours, a title above the grid, anything laid out with a dominant left-column /
right-column split, and copied slide text from the reference images.

**Production for the pilot (recommended: deterministic render from a small reviewed spec).**
1. Per style, an LLM (or a human, for the pilot) fills a closed-vocabulary JSON `treatment spec`
   (corner radius, border weight, rule style, card fill, header style, table style, icon style, imagery
   tone words) from the YAML brief. Schema-validated; values reviewed by a human against the YAML.
2. A deterministic renderer `scripts/style_board_render.py` (new; not written here) builds the SVG board
   from `spec + YAML tokens + fonts` using one curated component sheet (the nine tiles above, parameterised),
   renders it to PNG with the same Chromium path as the svg renderer (`services/svg_renderer.py`), and
   fails if the PNG exceeds 1536 KiB or a tile overflows.
3. Human review of each board on a checklist: palette matches the YAML preview; fonts are actually the
   style's fonts (Korean glyphs render); no slide-like arrangement; nothing copied from the references; the
   style is recognisable next to its current reference. A board that fails is fixed in the spec or the
   renderer, not by hand-editing the PNG.

Why not the alternatives: an LLM drawing each board (through the svg author) is fast but brings back
composition bias and is not reproducible; cropping tiles out of the existing 16:9 reference images keeps
fragments of 16:9 layout and cannot be regenerated. The deterministic route makes a board a function of
`(YAML render keys, spec, renderer version)`, which also makes step 3 (39 boards) mostly mechanical.

**Pilot subset: 4 styles**, chosen for spread, not novelty:

| style | why | current refs |
|---|---|---|
| `default` | bench continuity (all earlier canvas benches use it) | 1 x `content` 1280x720 |
| `strategic-navy` | dense corporate, table-heavy | 1 x `content` |
| `luxury-editorial` | serif, airy, image-forward (opposite density) | 1 x `content` |
| `cyber-ai` | dark, role-specific refs, 1600x900 (different reference size and per-role selection) | 6 roles x 1 |

The small run (S) uses `default` and `strategic-navy`; the recommended run (M) uses `default`,
`strategic-navy`, `luxury-editorial`. `cyber-ai` is the spare and the "role-specific" probe in L.
`kbs-company-proposal` (portrait 750x1083 references, two per role) is a natural test of the portrait
question but is excluded from the pooled primary (its reference geometry is unique); include it as a single
descriptive style in L.

**Storage and versioning.** Pilot boards stay outside the packaged assets:
`servers/fastapi/authored_style_boards/<style_id>/{board.png,spec.json}` plus
`authored_style_boards/manifest.json` (`authored-style-boards/v1`): `style_id`, `board_version`,
`source_style_digest` (the render-relevant YAML keys, same idea as `style_render_digest` in
`utils/authored_style_samples.py`), `spec_digest`, `renderer_commit`, `png_sha256`, `bytes`,
`reviewed_by`, `reviewed_at`. The backend reads the directory only when `SVG_STYLE_BOARD_DIR` is set, so
nothing in the product path sees it until step 3 decides. A board is stale when `source_style_digest`
no longer matches the YAML; the check script (like `check_authored_style_samples.py`) fails then.

## 4. Metrics

Data source for all measures except M1: the published slide SVGs of each deck (`slides.svg_content`,
via `GET /presentation/{id}`, which the bench client already reads), rendered at logical canvas size
(registry `logicalSize`) by the Chromium svg renderer; safe area `S` from
`constants/canvas_registry.json` `safeArea`. Rendering is deterministic for a given renderer version and
font set; render each SVG twice in one pilot to confirm identical PNGs before relying on it.

A new script `scripts/svg_reference_metrics.py` (not written here) emits one JSON per slide and per deck.
Definitions:

**Grids.** Cell size 16 px (square). `full` mask: a cell is occupied if any pixel in it differs from the
slide background by more than a fixed threshold. Slide background = the most frequent colour of the
rendered PNG (with the reserved footer 64x36 area and the wordmark zone masked out). `text` mask: same on a
second render of the SVG with every element except `text` removed. Only cells inside `S` count.

**M1 (primary) page flag rate.** Flagged pages / pages per arm, pooled, with the existing
Wilson-on-design-effect and Newcombe machinery (`canvas_bench_stats.py`, rho 0.15). All decks must be in one
metric epoch (`svg-overlap-ink-v1`, see T5). Used as the safety gate and as one benefit test.

**M2 utilisation and empty space (from the `full` mask).**
- `U` = occupied cells / cells in `S`.
- `LER` = area of the largest empty axis-aligned rectangle in `S` / area of `S` (histogram method,
  linear in cell count). This is the "empty band" measure: high `LER` means a large void.
- `edge void` = the largest of: gap between the safe-area top and the first occupied row, bottom and last
  occupied row, left and first occupied column, right and last occupied column, each divided by the
  corresponding `S` dimension. It catches content crammed to one side (bottom void on A4 and portrait,
  side voids on 21:9).
Direction of "better": lower `LER` and `edge void`, `U` closer to that of the same deck's own median slide
(no absolute target, since sparse title pages are legitimate; comparisons are paired by cell).

**M3 "16:9-ness" of the composition (from the `full` mask).**
- `bbox aspect gap` = `|ln((bw/bh) / (W/H))|` where `bw, bh` are the width and height of the bounding box of
  occupied cells inside `S`. 0 means content fills the canvas shape; a 16:9 block on a square page gives
  about 0.58.
- `letterbox flag`: let `R` be the largest 16:9 rectangle inside `S`, anchored top-left, top-centre or
  centred (three anchors). The flag is true when at least 90% of occupied cells lie in some anchor's `R`
  and `area(R) <= 0.85 * area(S)`. The second condition keeps the test informative: for a canvas whose
  shape is close to 16:9 the flag would be true for any content. It is informative for 1:1, 21:9 and
  portrait, marginal for A4 landscape (`area(R)/area(S)` about 0.83), and the bbox aspect gap is the
  fallback there. Report the letterbox rate per arm-canvas.
- `column count`: on the `text` mask restricted to rows between the title band (topmost text block) and
  the bottom of `S`, mark grid columns active when at least 3 occupied cells fall in them; count runs of
  active columns separated by at least 2 empty columns (32 px). Compare to the clause for the shape:
  `column violation` = true when the count is at least 3 on 1:1 or portrait, or 1 on 21:9 with the body
  text spanning more than 60% of `S` width (the "one long band" case).
These three are what H predicts move; M1 need not.

**M4 guards (not hypotheses).** Body-text minimum font size vs the floor, text ink coverage from the `text`
mask (a drop under B or C relative to A signals dropped content), slide count, retries per slide, seconds
per slide, `input_image_count` (must be 0 for B, 1 for C).

**M5 blind human preference.** Unit: a cell (canvas x style x manuscript), three decks (one per arm).
- Presentation: a contact sheet per deck, all 6 slides in a 2x3 grid at identical display height, random
  file names, arm order randomised per cell (Latin-square balance), decks of one cell shown together on one
  page. Same canvas and style within a cell, so nothing in the sheet identifies the arm.
- Task per cell: rank the three decks 1-3 on "which deck looks designed for this page shape (uses the
  whole page, no letterboxing, no cramped or empty regions)", then rank on "which would you present".
  Ties allowed. Each ranking gives three pairwise outcomes (A vs B, A vs C, B vs C), so 3 pairs per cell.
- Judges: at least 2 humans who did not run the experiment or see arm labels (the user counts as one;
  Korean fluent, since text is Korean); a third for disagreements only. Report Cohen's kappa on the
  pairwise outcomes. A VLM judge may screen but never decides (unvalidated, and it could favour outputs
  resembling its own style).
- Volume: M has 48 cells, so 144 implied pairs but 48 ranking screens, about 1 minute each, so under an
  hour per judge per question. S has 24 cells, half that.
- Analysis: per cell and pair, average the judges' outcomes (+1, 0, -1); exact sign test on non-tied cells.
  With 48 non-tied cells, 31 or more wins is one-sided p < 0.05 (p = 0.030); with 24, 17 (p = 0.032);
  with 36, 24 (p = 0.033).

## 5. Sample plan and cost

**Unit and design.** A cell = (canvas, style, manuscript). Every cell gets one deck per arm, so arms are
paired by construction on canvas, style and manuscript (four manuscripts exist:
`tests/fixtures/canvas_bench_manuscripts/01..04`, 6 slides, Korean). Arms of a cell run back-to-back in a
seeded random order (each of the 6 permutations equally often), serial as in the bench rule, so provider
drift affects all arms alike. 6 minutes per deck (the bench figure, which includes about 2.7 minutes of
outline).

**Canvases.** `square-1x1`, `ultrawide-21x9`, `a4-landscape`. Portrait: yes, include `a4-portrait` in the
recommended run, but as its own stratum rather than pooled blindly with the others: H predicts the largest
mismatch there (16:9 versus 0.71) and it is the A4 document shape users meet, yet portrait has never been
benched, so a flag-rate difference could also come from portrait itself. `tall-9x16` and `tall-9x13` are
not included (no stage or user demand shown). The 16:9 canvas is a control only in L: A vs B at 16:9 shows
what references do at their native shape, which separates "references help everywhere" from "references
mis-anchor off-16:9".

| option | canvases | styles | manuscripts | cells | decks (3 arms) | serial hours |
|---|---|---|---|---|---|---|
| S (go / no-go) | 1:1, 21:9, A4L | default, strategic-navy | 4 | 24 | 72 | 7.2 |
| M (recommended) | 1:1, 21:9, A4L, A4P | default, strategic-navy, luxury-editorial | 4 | 48 | 144 | 14.4 |
| L | M + 16:9 control | 4 pilot styles | 4 | 80 | 240 | 24.0 |

Plus one smoke run before any option: 1 cell x 3 arms x 1 canvas (3 decks, 0.3 h) to verify receipts
(`input_image_count` 0/1/current), the metric script and the baseline flag rate under the ink epoch.
Boards for 3 to 4 styles must exist first. Run M in the S subset first (the S cells are a subset of M by
design); no efficacy stopping. Use the S readout only to decide whether to continue (continue unless one
arm is clearly harmful, see section 6) so that the final analysis remains one pass over the planned M.

Optional speed-up (needs verification, not assumed): `slides_markdown` in the request bypasses the outline
(`api/v1/ppt/endpoints/presentation_generate.py:910-927`). Generating one outline per cell and feeding the
same slides to all three arms would remove outline variance from the comparison and save about 2 x 2.7
minutes per cell (M about 14.4 h to about 9 h). It has not been checked that the svg path with
`slides_markdown` behaves like the product path; O5.

**Power (estimate; deff 1.75 for 6 pages, 48 decks per arm = 165 effective pages).** SE of the difference
in page flag rate is 0.044 at rates 0.27 vs 0.15 and 0.030 at 0.12 vs 0.04, so a true drop from 0.27 to
0.15, or 0.12 to 0.04, is detectable at about 80% power (needs z of about 2.5) in M; S (24 per arm) has
that power only for larger drops (0.27 to 0.10; 0.12 to 0.04 is about 55%). Per canvas M has 12 decks per
arm (about 41 effective pages), enough only for a difference of about 0.22, so per-canvas results are
descriptive. The baseline rate under the ink epoch is not known; the smoke run and the first S decks
measure it. If it is near 0.05, M1 has no room to improve and the fit metrics and human preference carry
the decision.

## 6. Decision rule

Pool each arm over cells (canvases stratified in the report). Compare X in {B, C} with A on the same cells.
Reuse the pooled statistics with a variant: same canvas and style on both sides, cells complete by
construction (no manuscript-balance check needed), margins below instead of the 0.20/0.15 tiers, plus a
paired sensitivity check (sign-flip permutation on per-cell differences, stdlib only). One epoch only.

- **Safe:** upper bound of `D_flag = p_X - p_A` (one-sided 95%) at most +0.10, and no guard (M4) worse than
  A by more than the bench's secondary margins; text ink coverage not more than 10% below A; hard gates
  (generation and export failures) not above A. Margin 0.10 is tighter than the canvas tiers because the
  question is replacing a default.
- **Fit benefit:** the letterbox rate falls by at least 0.15 absolute, or the column-violation rate falls by
  at least 0.15 absolute, and mean `LER` falls by at least 20% relative (per-cell paired permutation test,
  one-sided p < 0.05 on the composite of the two).
- **Human benefit:** X preferred over A on "designed for this page shape" by exact sign test one-sided
  p < 0.05 on non-tied cells (section 4), agreeing with the direction of the fit metrics.
- **Flag benefit:** upper bound of `D_flag` below 0.
- X **wins over A** = Safe and (Human benefit, or Fit benefit and no Human loss). Flag benefit alone
  (without fit or human support) is reported but does not decide, because the flag rate does not measure the
  hypothesis. X **loses to A** = fails Safe, or is significantly worse on Human (one-sided p < 0.05 for A).

| outcome | action |
|---|---|
| C wins over A, and C not worse than B | Step 3: build the 39 boards; use them on non-16:9 canvases only; keep A on 16:9 unless the L control says otherwise. |
| B wins over A and C does not beat B (C not better on Human or Fit by the same tests) | Ship B on non-16:9 canvases (no boards; zero asset cost). B is preferred over C whenever C does not beat it, since it is simpler. |
| Both B and C win; C beats B on Human or Fit | C. |
| A wins, or no arm wins and none loses | Keep A. H is rejected as the main driver; the next suspect is the canvas-unadapted brief text (T1): design a text-neutralised arm E. |
| B and C both lose to A (flag or human) | References carry value off-16:9 that boards do not replace. Test per-canvas references (arm D), first for one canvas (A4 portrait, where `kbs-company-proposal`'s 0.69 refs give a native example), before any production of them. |
| B and C are safe but fit metrics do not move (letterbox and column rates within 0.05 of A) | Same as "no arm wins": the anchor is not the images. Per-canvas references only if the human check still shows a preference for A. |
| Insufficient sample or S/M split | Continue to the next size (S to M to L) up to L; then stop and record as unresolved. |

## 7. Threats to validity

- **T1 text anchor (largest).** 39 briefs say "At 1280x720" and describe column grids and two-column
  layouts in words, unadapted for svg. Removing images (B) or replacing them (C) leaves this text, so a
  weak reference effect may be masked. A null result here does not clear 16:9 anchoring; it clears the
  images only. Arm E (canvas-adapted brief text, `apply_canvas_tokens`-style) is the follow-up, not part
  of this experiment, to keep it a three-arm design.
- **T2 style mix.** Styles differ in reference count, roles, and size (1280x720 versus 1600x900, one image
  versus per-role images). Fixed by using the same styles in all arms and a per-style table; the pooled
  result is an average over 3 styles, not a claim about all 39. The 6 no-ref styles are outside the claim.
- **T3 provider drift and ordering.** Interleaved arms in each cell, randomised order (Latin square),
  serial; record model id and provider per deck; refuse to pool decks from different backend commits.
  A shared-stack incident (machine restart, as lost a deck earlier) invalidates a cell entirely, not one
  arm.
- **T4 arm B prompt residue.** The rules still say "References show art direction only" when no image is
  attached. Left as is to keep the only difference the images; if B underperforms, a variant without the
  sentence is a cheap re-run.
- **T5 metric epoch.** The ink-box fix (`e8475ee6f`) changed `text_overlap` without bumping
  `SVG_RENDERER_VERSION`; all decks here must carry `svg-overlap-ink-v1` (via `overlap_basis` or
  `--ink-fix-commit`/`--backend-commit`, per `canvas_bench_epoch.py`). All earlier canvas data (rates
  0.27 for 16:9, 0.46 for 1:1) is line-box epoch and is not a baseline for this experiment.
- **T6 board quality.** Arm C measures "our board" not "boards". A poor board loses to A for reasons
  unrelated to the idea. Mitigation: the review checklist; if C loses but B wins, do not conclude that
  boards cannot work without a second board iteration.
- **T7 metric validity.** M2/M3 are new and not calibrated: thresholds (16 px cell, 90% inside `R`, 3 active
  cells) are judgement. Mitigation: validate on the 13 existing bench decks plus a hand-labelled set of 20
  slides (letterboxed or not) before the run, and freeze thresholds before looking at arm labels. If the
  metrics do not agree with human labels on that set, drop them and rely on M5.
- **T8 outline noise.** Outlines differ per deck, so slides differ across arms; deck-level metrics and
  cell-level pairing absorb this; the `slides_markdown` option (O5) removes it.
- **T9 human judging.** Judges know the canvas shape but not the arm; fatigue after
  about 50 screens; the first-shown position bias is handled by randomisation.
- **T10 pooled-canvas heterogeneity.** The pooled result can hide opposite effects on different canvases;
  report per canvas, decide on the pool, and let a per-canvas reversal (an arm significantly worse on one
  canvas) block that canvas only.

## 8. Open decisions for the user

- **O1** Option S, M or L (recommended M, 14.4 h serial, preceded by a 0.3 h smoke run).
- **O2** Include `a4-portrait` (recommended yes, as its own stratum) and whether `tall-9x16` belongs later.
- **O3** Board production: the recommended deterministic renderer plus a reviewed typed spec (about a day
  to build the renderer and 4 boards) versus an LLM-drawn board (hours, less reliable).
- **O4** Bench-only request field with env gate (recommended) versus env-only knob with three isolated
  backends; both leave the default path untouched.
- **O5** Whether to pin one outline per cell with `slides_markdown` (saves about 5 h in M; needs a check that
  the svg path treats it like the product path) or keep the full pipeline (matches the product exactly).
- **O6** Human judges: who, how many, and whether the 12-slide native samples (once present) are shown for
  reference.
- **O7** Are decision margins (safe 0.10, fit 0.15, sign test at 0.05) acceptable, and is B allowed to win
  outright (no boards at all) if it does.
- **O8** Add arm E (text-neutralised brief) now, since T1 could make the whole experiment null, or only if
  this one is null.

## 9. Self-score and top problems

Score: 7 / 10 (design complete and grounded in the code paths; nothing measured or built).

1. T1 could swallow the result: the brief text repeats the 1280x720 anchor, and the design accepts that a
   null outcome is not a full refutation. A four-arm design would answer more but costs a third more decks.
2. M2/M3 are unvalidated new metrics with judgement thresholds; the human preference protocol is the only
   measure that does not depend on them, and it depends on two available judges.
3. The ink-epoch baseline flag rate is unknown; if it is low, M1 cannot show a benefit and the decision
   rests on the fit metrics and human votes.
4. Arm C's fairness depends on the board renderer, which does not exist; a poor board contaminates the
   comparison. The pilot spec, the checklist and the "C loses, B wins" caveat mitigate but do not remove it.
5. Not verified: that the svg driver honours `slides_markdown` like the product path (O5), that
   `overlap_basis` appears in the operation result (the epoch label may need the ancestry path), and that
   the vision-token estimates hold; all are flagged where used.
