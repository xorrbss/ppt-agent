# Registering a real corporate deck as an SVG user template: design (2026-11-03)

Status: design, approved for implementation 2026-11-03 (decisions in section 1).
Builds on `svg-user-template-layout-design-20261029.md` (the template design) and
`svg-user-templates-linux-20261103.md` (what was fixed to make templates usable
off Windows). Evidence below comes from a real 101-page proposal deck
(`[NIPA] AI 컴퓨팅자원 활용기반 강화사업 … MSP 사업자 선정`, 2026-10-27, kept
outside the repository).

## 1. Decisions

| # | Decision | By |
|---|---|---|
| D-1 | Unsupported **fixed decoration** is preserved as an image cut from a source render, after the person confirms it in review. This implements the template design's "approved decorative derivative" (`…layout-design…` section on rasters), which allows decoration rasters with confirmation but was never built. | user, 2026-11-03 |
| D-2 | A text-free picture covering the slide may be a **background layer** under the body. The template design forbids "the whole page *including body* as background"; the compiler's `svg_template_fullslide_raster_forbidden` is stricter than that and is relaxed to it. | user |
| D-3 | Fonts the server cannot use are replaced **explicitly** by Pretendard, chosen at registration and recorded. Nothing is substituted silently. | user |
| D-4 | A `template-redesign` deck is generated only at the template's own canvas (the source slide size). `free-design` keeps every canvas. | user |
| D-5 | Text is never turned into an image (template design: "do not turn text into a picture and report font preservation"). Text inside a decorated shape or group stays vector text; only its paint goes into the image. | design rule, kept |

## 2. What the real deck needs

Measured with the current compiler (`services/svg_templates/frame.py`):

| Finding | Effect today |
|---|---|
| 101 slides | `svg_template_page_limit_exceeded` (limit 100) before any analysis |
| Canvas 10,907,713 × 7,775,575 EMU (303 × 216 mm, ratio 1.403, logical 1280 × 912) | supported as source canvas; generation needs `svg_canvas` 302.99 × 215.99 mm |
| Fonts: Paperlogy 3–8 (6 weights), embedded only as 47 KB subsets; not installed | `svg_template_font_unavailable` on every frame |
| Body layouts (6 chapter variants): gradient band, cropped photo with soft edge, round-top tab, group shapes, text with `normAutofit`, slogan, page-number field in a plain shape | `paint_unsupported`, `image_crop_unsupported`, `image_effect_unsupported`, `primitive_unsupported`, `geometry_missing` (groups), `transform_unsupported`, `text_autofit_unsupported`, `background_unsupported` |
| Cover and chapter-divider slides: a full-slide photo | `svg_template_fullslide_raster_forbidden` |
| Pretendard bundled at 400 and 700 only | Paperlogy's six weights collapse to two |

A layout-only copy (one empty slide per layout) is still blocked on every
frame, so the blocks come from the template furniture itself, not from slide
content.

## 3. Design

### 3.1 Representative pages (page limit)

A template is its furniture, not its 101 pages. Registration analyses a
**selection** of source pages:

* Request field `source_pages: list[int] | None` (1-based). Default when absent:
  page 1 plus the first page of every other distinct slide layout, in source
  order, at most 12.
* `MAX_TEMPLATE_PAGES` applies to the selected pages. The deck itself is still
  bounded by the existing package admission (`admit_pptx_package`) and size
  limits.
* Frames keep `source_page` as the original page index, so previews,
  evidence and frame maps point at real pages.
* For this deck the default is pages 1 (cover), 2 (TOC table), 3, 4
  (chapter divider), 5 (chapter I body) and the first page of each other
  chapter layout: 11 pages.

### 3.2 Raster-preserved decoration (D-1, D-2, D-5)

**Which failures qualify.** A fixed shape whose conversion fails with one of
these codes becomes a raster candidate instead of a blocked issue:
`paint_unsupported`, `effect_unsupported`, `image_crop_unsupported`,
`image_effect_unsupported`, `image_format_unsupported`,
`image_stretch_unsupported`, `primitive_unsupported`, `geometry_missing`
(groups), `transform_unsupported`, `line_style_unsupported`,
`color_transform_unsupported`, `style_unresolved`,
`text_shape_paint_unsupported` (paint only, see below), and for slide
backgrounds `background_unsupported` / `background_style_unsupported`. A
full-slide picture no longer raises `fullslide_raster_forbidden`; it becomes a
background candidate when it carries no text. Every other failure keeps
blocking.

**Text stays vector (D-5).** For a candidate that contains text (a filled
shape with text, or a group with text children), the text is emitted as
vector text by the existing `text_svg` path and only the paint goes into the
image:

* group children are mapped through the group transform (`a:xfrm` with
  `chOff`/`chExt`, scale and offset only; a rotated or flipped group keeps
  blocking);
* a text child that the text path cannot convert keeps blocking with its own
  reason. It is never rasterized.

**Runs and layers.** Fixed shapes are visited in paint order (master →
layout → slide). Consecutive raster candidates form one **run**; a vector
element between them starts a new run, so z-order is kept. Each run becomes
one image element:

* `layer="background"` when the run precedes every foreground element and
  either is the slide background or covers at least 70 % of the canvas
  (the cover photo, the divider photo);
* otherwise `layer="foreground"`, and its bounds join the fixed bounds that
  shape the body region exactly like any other fixed element.

**Rendering an isolated run.** One temporary copy of the selected pages is
made per run index. In it, every shape that is not in the run is hidden
(`cNvPr hidden="1"`, on slide, layout and master parts alike), text inside
the run is emptied, and the slide background is removed unless the run is
the background. The copy is rendered **twice**, on a solid white and on a
solid black slide background, through the source renderer (LibreOffice →
PDF → pdfium, the same stack as the source preview). Alpha and colour are
recovered per pixel from the two renders:
`alpha = 1 − (white − black) / 255`, `colour = black / alpha`. Soft edges and
semi-transparent paint survive; a spike on
`decor-only.pptx` (the chapter-III layout) recovered the band, the tab box
and the page-number pill on transparency with text removed. The image is
cropped to its alpha bounding box at 2× the logical canvas, stored as a
template asset (PNG), and placed at its exact logical bounds.

**Evidence.** The element records
`raster_preservation = {renderer, renderer_version, scale, shape_refs,
reasons, approximate}`. The field is omitted when absent, so existing frame
and package digests do not change. Each frame with raster elements gets a
review-required issue `svg_template_raster_preservation_review_required`.
The review screen lists them ("이미지로 보존: PPTX에서 편집할 수 없음",
renderer, reason) next to the source/frame comparison, and publication
requires the existing explicit confirmation.

**Renderer.** LibreOffice on every platform in this version (it is already
required for the uploaded design reference). Without it, raster candidates
keep blocking with their original conversion reason, exactly as before this
change; `svg_template_raster_renderer_unavailable` is raised only when a
renderer was chosen but LibreOffice vanished before rendering. A PowerPoint
renderer for Windows is follow-up work; LibreOffice output is marked
`approximate`.

**As built (2026-11-03).** `services/svg_templates/raster_preservation.py`
(render deck, dual render, alpha recovery, crop) and the run collector in
`frame.py` (`_RasterRuns`, `_emit_fixed`, `_apply_raster_runs`). The
registration worker passes `libreoffice_raster_renderer()`. Rather than
hiding shapes in a copy of each page, the render deck carries one new slide
per run with `showMasterSp="0"` and copies of only the run's shapes
(relationships re-pointed), so all runs of all frames render in one
LibreOffice pass per background colour. A full-slide picture becomes a
background layer only when no foreground furniture is painted before it (a
layout band under a slide photo would otherwise be hidden by it); it keeps
`svg_template_fullslide_raster_forbidden` otherwise. Review lists the source
shapes a run stands for (`raster_preservation.shape_refs`), and a saved
review maps a group leaf to its group and ignores the slide background, so
saving a review unchanged reproduces the frame. On the real deck the nine
default frames compile with no blocked issue in about 70 s; the package is
12 MB (limit 64 MB).

### 3.3 Fixed text the deck needs

* `normAutofit` with `fontScale` / `lnSpcReduction`: run sizes and line
  spacing are scaled by the stored factors (what PowerPoint shows).
  `spAutoFit`: the stored box already fits its text; it is laid out as is
  and the existing overflow check still applies.
* A slide-number field (`a:fld type="slidenum"`) in a fixed non-placeholder
  shape becomes the `page_number` dynamic field, like the `sldNum`
  placeholder.
* Bullets, tabs, soft breaks and spacing rules keep blocking for fixed text.

### 3.4 Explicit font substitution (D-3)

* Registration request field `font_substitutions: list[{source_family,
  target_family, target_weight}]`. `target_family` must be a server font the
  embedding policy admits (today: bundled Pretendard 400/700).
* The analysis result lists every required face it cannot use with a
  suggested substitute; the review screen shows a picker per face. The
  default suggestion maps the weight in the family name (Paperlogy "3 Light"
  … "8 ExtraBold") to the nearest available weight: 300–500 → 400,
  600–800 → 700.
* The compiler resolves fixed text and the frame font manifest through the
  map, so generated bodies use the substitute as well. The package records
  `font_substitutions`; the review shows "Paperlogy 7 Bold → Pretendard Bold
  (대체)". The source preview still renders the original deck, so fixed-text
  regions are expected to differ and the comparison says why.
* As built: the package lists `unavailable_fonts` (source face, suggested
  Pretendard weight; omitted when empty, so existing digests are stable). The
  review screen (`SvgTemplateFontSubstitutions`) starts from the applied map
  plus one suggestion per unavailable family and sends the map through the
  draft revision (`PATCH …/draft` with `fontSubstitutions` and no frame
  review), which re-analyses the source. Substitution is per family: one
  family with several bold flags maps to one weight.
* Follow-up option, not in this version: vendor Pretendard 500/600/800 as
  trusted faces so Paperlogy's hierarchy keeps more than two weights.

### 3.5 Canvas lock (D-4)

* `template-redesign`: the request canvas must equal the template's source
  canvas; anything else is `422 svg_template_canvas_locked`. The upload
  screen sets the canvas from `template.sourceCanvas` (as `svg_canvas` in mm)
  and disables the selector, with a one-line explanation. The source-canvas
  adaptation is identity, so no preparation step is shown.
* `free-design`: unchanged.
* As built: a template-redesign request canvas must equal the template's
  source canvas exactly (`catalog.require_source_canvas`), checked in
  `capability` and at new admission (`prepare_template_request` without a
  frozen snapshot); decks admitted before the lock keep replaying and
  exporting. A source size the 0.01 mm selector cannot express (302.992 ×
  215.988 mm) is requested exactly in EMU: `svgCanvas` takes either
  `{widthMm, heightMm}` or `{widthEmu, heightEmu}` (never both), and the EMU
  form resolves through `document_canvas_from_source_slide_size`, the same
  function that gave the template its source canvas. Millimetre requests
  serialize as before, so existing digests and idempotency keys are stable.
  The upload screen derives the selector from `sourceCanvas`
  (`templateSourceCanvasSelector`: a preset when the source is one,
  otherwise the EMU form) and disables the canvas picker. Publication already
  stores each frame's identity adaptation at the source canvas, so the exact
  canvas needs no preparation step and the exported slide size equals the
  source EMU.
* Rejected alternative: admitting a canvas within half the 0.01 mm step of the
  source. The real deck then generated at 73 EMU narrower than the source and
  its preparation failed, because full-width furniture overran the page by
  0.0086 px.

## 4. Verification

Unit tests: page selection defaults; run splitting by z-order; layer choice;
alpha recovery on synthetic renders; group-transform text mapping; autofit
scaling; slidenum field; substitution resolution and evidence; canvas lock
admission; digest stability of existing packages.

Live test with the real deck, uploaded unmodified:

| Check | Pass condition |
|---|---|
| registration | succeeds without editing the file; every selected frame free of `blocked` issues except ones the person deselects |
| review | raster elements, renderer and font substitutions listed; approval publishes |
| generation | `template-redesign`, 303 × 216 mm, 10 slides of proposal content, completes |
| fixed regions | band, tab, logos and page number at their source positions (fixed-region pixel difference reported per frame) |
| export | PPTX slide size equals the source EMU; raster elements are pictures, text is text |
| quality | page pass rate in line with the 16:9 measurements; body text at or above the physical minimum |

## 5. Out of scope

PowerPoint as the raster renderer; editable vector gradients, crops and
effects; text as image; SmartArt, charts and tables as furniture; more
Pretendard weights (3.4 follow-up).

## 6. Order and size

| Step | Work | Size |
|---|---|---|
| 1 | 3.1 page selection | 2 h |
| 2 | 3.3 fixed text (autofit, slidenum, group transform for text) | 3 h |
| 3 | 3.2 raster preservation (runs, isolated dual render, evidence, review) | 1 day |
| 4 | 3.4 font substitution (compiler, API, review picker) | 3 h |
| 5 | 3.5 canvas lock | 2 h |
| 6 | 4 live test and record | 3 h |

## 7. Live test result (2026-11-04)

The real deck, uploaded unmodified, through the API on Linux (LibreOffice
renderer, real LLM, SQLite profile):

| Check | Result |
|---|---|
| registration | 9 default frames (pages 1, 2, 3, 5, 19, 25, 50, 55, 101). First analysis: only `svg_template_font_unavailable` blocks, 1–5 decoration images per frame. With the suggested substitutions (Paperlogy 4/5 → Pretendard 400, 6/7/8 → 700, both Latin and Hangul family names), 0 blocked issues. Two analyses took 4.6 min. |
| review | raster elements and substitutions listed; publication of all 9 frames succeeds |
| canvas lock | 16:9, A4 and 302.99 × 215.99 mm are `svg_template_canvas_locked`; `{widthEmu: 10907713, heightEmu: 7775575}` is `available` with no preparation step |
| generation | 10 pages, completed in 517 s, quality gate `review_required` (4 passed, 6 flagged) |
| export | PPTX slide size 10,907,713 × 7,775,575 EMU, equal to the source; preserved decoration exported as pictures, all body and fixed text as text |

Defects the live test found and fixed on the way (each with a regression
test unless noted): raster pixels mapped with one scale (912.5 high on a
912 canvas); only `p:pic` could be a background layer; a rasterized shape's
own box stayed reserved; distant consecutive decoration merged into one
page-sized image that reserved the body; the source canvas could not be
requested exactly (section 3.5); a template page with an asset manifest lost
its furniture receipt on the way to the export worker (verified live only:
the builder has no unit harness).

Open, not fixed here:

* **Job payload size.** The template selection snapshot carries the whole
  package: 15 MB for this template (font bytes 7.2 MB, preserved images twice,
  in the frames and again in the adaptations). On SQLite one run failed with
  `database is locked` (retryable) while heartbeats competed with these
  writes. A structural fix is to store the selection by immutable version
  reference and digests and load the material on replay.
* **Fixed text with several runs.** The export writes each run of a fixed
  text as its own text box at the measured x. LibreOffice, even with
  Pretendard installed, draws "에스넷시스템이" wider than measured and it
  overlaps the next run in the header slogan; the SVG itself does not
  overlap. PowerPoint has not been checked. Writing such text as one text box
  with several runs would not depend on the viewer's metrics.
* **Body quality.** Four pages fell back to plain bullet lists in the upper
  body, and the cover's body text over the dark photo has low contrast. One
  of ten outline requests came back with nine pages and failed the whole
  deck (`OUTLINE_SLIDE_COUNT_SHORT`).

## 8. Follow-up fixes and re-measurement (2026-11-04)

| Fix | Commit |
|---|---|
| A fixed line with several runs is one SVG text with tspans, so PPTX gets one text box with several runs instead of one box per run (the header slogan no longer overlaps in LibreOffice) | `8791e5a79` |
| Template body rules name the stroke extent; a page that leaves its region gets a move-only repair hint | `37ab5b306` |
| The template page context carries `body_background` (mean colour, tone, text colour) from the frame's background layers; the rules require 4.5:1 contrast and the template fallback page uses it (the cover body is now white on the dark photo) | `233bdf79d` |
| A short outline is requested once more with the exact count | `591a2955c` |
| The author gets the body region inset by 4 px (forbidden regions widened by 4 px); validation keeps the frame's own regions | `94fb4299e` |

Measured on the same template (real LLM, SQLite profile):

| Run | Code | Result | First drafts failing | Of which region by ~1 px | Fallback pages |
|---|---|---|---|---|---|
| r6-1 | up to `591a2955c` | completed, export ok, 5 passed / 5 flagged | 7 / 10 | 7 | 6 |
| r6-4, r6-5 | `94fb4299e` | both interrupted by `database is locked` after 8 and 9 pages | 4 / 17 | 1 | 0 |

Open:

* **Repair attempts on template pages are weak.** With the current authoring
  profile a repair gets the failure list but not the previous SVG (only
  profile v8 passes it), and on this deck the second drafts were invalid or
  unrelated (`junk after document element`, `svg_contract_invalid_id`, empty
  bodies). The margin above avoids most repairs; passing the previous SVG to
  template repairs is the structural fix.
* **Job payload size (section 7) now blocks runs.** Three of the last four
  generations on SQLite ended with `generation_service_interrupted` /
  `database is locked`. The 15 MB template selection is stored three times
  (`presentation_generation_jobs.request_payload` and `.svg_style_snapshot`,
  `workspace_operations.request_payload`) and compared for equality with the
  presentation's `theme.svg.styleSnapshot` in about ten places, so compacting
  one storage boundary would break those invariants. Options: store the
  selection by immutable template version reference and digests and hydrate
  it in one loader; or move heartbeats to a narrow table so a heartbeat does
  not rewrite the large row. PostgreSQL keeps unchanged large values out of
  line (TOAST), so the cluster profile is much less affected.

## 9. Repair input and PostgreSQL measurement (2026-11-04)

Correction to section 8: template repairs did receive the previous SVG
(authoring profile v8), but it was the composed page, frame included: about
0.7 MB of base64 decoration and `svg-system-template-*` IDs. Commit
`1f110f011` shows a template page's repair only its body group, as the
common header already is.

Measured on local PostgreSQL 16 (the cluster profile; no lock failures), same
deck and request, code at `097ab50d8`:

| Run | Result | Quality gate | First drafts failing | Fallback pages | Export |
|---|---|---|---|---|---|
| p2-1 | completed, 490 s | passed, 10 / 10 | 3 (all repaired) | 0 | PPTX at the source EMU size |
| p2-2 | completed, 520 s | passed, 10 / 10 | 2 (all repaired) | 0 | PPTX at the source EMU size |

Earlier in the day, before the margin, contrast and repair-input changes: 5
passed / 5 flagged and 6 fallback pages.

Observed, not fixed:

* That registration published 6 of 9 frames: the cover, page 2 and the
  divider failed their review render (`svg_template_review_render_unavailable`)
  during an 11-minute analysis, while the same frames render offline. Commit
  `097ab50d8` now logs the cause.
* Per-page source content became fixed furniture: every generated page shows
  the chapter tab "I 회사소개" and "요구사항ID COM-001" from the frame's source
  page. Review lets the person drop such elements; analysis does not yet tell
  chapter-specific text from furniture.

## 10. Template material by reference (2026-11-04)

Commit `a862b3715`: new admissions store a v2 template selection (pin, version
id, canvas, frame map, style projection, `material_key`) and load the package
and adaptations per process through the verified template loader
(`services/svg_templates/selection_material.py`). v1 selections stay valid.
The SVG unit suites (3,163 tests) fail identically before and after (209
pre-existing failures).

Live on SQLite with the same deck:

| Row | Before | After |
|---|---|---|
| `presentation_generation_jobs.request_payload` | 15.4 MB | 13 KB |
| `presentation_generation_jobs.svg_style_snapshot` | 15.4 MB | 10 KB |
| `workspace_operations.request_payload` | 15.4 MB | 15 KB |
| `presentations.theme` | 16.5 MB | 255 B (before publication) |

The first generation still ended with `database is locked`, now on
`UPDATE public_provider_logical_calls` (provider-call reconciliation) while
ten pages author in parallel. Connections wait 30 s
(`services/database.py`), so another transaction held SQLite's single write
lock for longer than that. Large rows made this worse but were not its only
cause; the long writer is not identified yet. The second run then failed with
`public_provider_terminal_replay_unsupported`: its retry reused provider calls
the interrupted run had already settled, which the non-idempotent provider
guard refuses by design. PostgreSQL (section 9) is unaffected.

