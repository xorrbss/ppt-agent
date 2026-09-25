# Raster-redesign eval suite

Durable, launchable eval suite for the raster-redesign feature (image-only
PPTX -> `analysis_mode=auto_redesign` -> editable A4 PPTX/PDF). Ships with 4
synthetic decks (`deck1`..`deck4`, no customer data); a large real-world
reference deck (e.g. a customer deck) can be plugged in via an external,
non-committed config -- see "Adding an external/customer deck" below.

This is the repo copy of a suite originally developed and run from
`C:\project\PPT-agent\handoff\raster-redesign-20260925\eval-suite\`. See
"What changed vs. the original eval-suite" at the bottom for the (small,
documented) differences.

## Layout

```
scripts/raster_redesign_eval/
  suite_config.json          # deck1-4: paths, checker kind, expected page counts
  run_suite.py                # thin CLI facade: launch / status / score / baseline / compare
  suite_paths.py               # shared config/decks-root resolution
  suite_launch.py               # launch / _scheduler (spawns generate_raster_deck.py per deck)
  suite_status.py               # status
  suite_score_cmd.py            # score (scores a `launch` RUN_DIR)
  suite_baseline_cmd.py          # baseline (scores arbitrary output dirs via --map, no launch needed)
  suite_compare.py               # compare (markdown delta table between two scored runs)
  deck_scoring.py               # the actual per-deck scoring (both checker kinds)
  deck_mockup_metrics.py        # editable_text_ratio_body_excl_mockup (both checker kinds)
  wrong_number_filter.py        # page-number-footer false-positive classification, number gate
  mockup_regions.py             # mockup-region (screenshot/UI-mock) exclusion, from analysis_result.json
  wrong_number_candidates.py    # checker-kind wrong-number candidates (+ --crops)
  pixel_text_causes.py          # pixel_text_by_cause: why pixel-kept text isn't editable
  generate_raster_deck.py     # product-path driver (thin facade)
  raster_deck_http.py / _upload.py / _analysis.py / _generate.py / _evidence.py / _run.py
  # raster_deck_analysis.py also has save_analysis_result() -> RUN_DIR/<deck_id>/analysis_result.json
  checker.py                  # OCR-fixture checker (thin facade)
  checker_text.py / _slide.py / _pptx_report.py / _pdf_report.py
  ground_truth_checker.py     # exact-source-text checker (deck1-4)
  exactly_once.py             # OCR duplicate/missing/word-cut detection
  imghash.py                  # perceptual-hash helpers
  decks/
    build_image_only_deck.py  # source.pptx -> ground_truth.json + source_pages/ + image_only.pptx
    build_deck4.py             # generates deck4's fictional Korean source.pptx from code
    deck1_executive_report/{ground_truth.json, source_pages/*.png}
    deck2_business_proposal/{ground_truth.json, source_pages/*.png}
    deck3_data_journalism/{ground_truth.json, source_pages/*.png}
    # deck4_korean_tables/ is NOT checked in -- fully regenerable, see below
```

## Running

Run scripts **by path**, from `servers/fastapi` -- this is the one supported
way, matching every other tool under `scripts/` in this repo (a flat
directory of standalone scripts, no `scripts/__init__.py`, no package):

```
python scripts\raster_redesign_eval\run_suite.py <subcommand> ...
python scripts\raster_redesign_eval\checker.py --pptx ... --fixtures ... --report-dir ... --label ...
python scripts\raster_redesign_eval\decks\build_deck4.py <out_dir>
```

`-m scripts.raster_redesign_eval.run_suite` is **not** supported: it would
need `scripts/__init__.py` (and a `raster_redesign_eval/__init__.py`), which
this repo's `scripts/` directory deliberately doesn't have anywhere else.

Every module in this directory imports its siblings by plain top-level name
(e.g. `import checker_text`), relying on Python putting the invoked script's
own directory on `sys.path[0]`. This matches the original eval-suite's
`sys.path.insert(0, ...)` pattern. Don't move a single module out of this
directory without moving the rest -- the sibling imports will break.

## Requirements

- `deck1`-`deck4` scoring (`ground_truth_checker`) needs only
  python-pptx/Pillow/numpy, all already project dependencies: the fastapi
  `.venv` python works directly.
- A deck configured with `"checker": "checker"` (OCR-based fixtures --
  e.g. an external/customer deck, see below) also needs PyMuPDF (`fitz`),
  which is **not** a project dependency (checked against
  `servers/fastapi/pyproject.toml` on 2026-09-25: absent). Run
  `score`/`baseline`/`compare` for such a deck via:

  ```
  cd C:\project\PPT-agent\<repo>\servers\fastapi
  uv run --with pymupdf python scripts\raster_redesign_eval\run_suite.py <subcommand> ...
  ```

  This does not run `uv sync` and does not mutate the project's lockfile; it
  only overlays `pymupdf` on top of the already-synced `.venv` for that one
  process. `launch`/`status`/`_scheduler` need no third-party deps at all
  (same as `generate_raster_deck.py`, which is stdlib-only urllib) and can be
  run with any `python.exe`, including the plain `.venv` one. deck1-4's own
  `ground_truth_checker` path never touches PyMuPDF.
- Exactly-once OCR checks (`exactly_once.py`, only reached via the
  `"checker": "checker"` path) need Tesseract with the `eng`+`kor`
  languages. Defaults: `RASTER_EVAL_TESSERACT_EXE` env var, else
  `C:\Program Files\Tesseract-OCR\tesseract.exe`; `RASTER_EVAL_TESSDATA_DIR`
  env var, else this repo's own `tessdata/` (resolved relative to this
  file's location, so it follows whichever checkout/worktree you're in).

## Usage

**Launch** (spawns a detached scheduler process; returns immediately, survives
the calling session ending):

```
python run_suite.py launch --base http://localhost:5000 --out C:\path\to\run1 \
    --decks deck1,deck2,deck3,deck4 --max-parallel 2
```

Each deck gets `RUN_DIR\<deck_id>\` with `generate_raster_deck.py`'s own
output (`manifest.json`, `run_report.json`, `slide_evidence_report.json`,
`<presentation_id>.pptx`, `<presentation_id>.pdf`, `driver_std{out,err}.log`).
The scheduler also writes `RUN_DIR\scheduler_state.json` (per-deck
queued/running/succeeded/failed) and its own `scheduler_std{out,err}.log`.

`launch` needs `image_only.pptx` to actually exist for each requested deck
(it's the upload payload) -- see "Rebuilding deck assets" below, since none
of the four decks ship that file in this repo.

**Status:**

```
python run_suite.py status --out C:\path\to\run1
```

**Score** (writes `RUN_DIR\summary.json` + `RUN_DIR\reports\<deck>_raw_report.json`):

```
python run_suite.py score --out C:\path\to\run1
```

**Baseline** (scores one-off output directories that don't follow the
`launch` `RUN_DIR/<deck_id>/manifest.json` convention -- e.g. a driver run
you kept from an earlier session -- via an explicit `deck_id -> directory`
map, so nothing is hardcoded to any one machine):

```
python run_suite.py baseline --out C:\path\to\summary.json --map C:\path\to\map.json
```

`map.json`: `{"deck1": "C:\\path\\to\\deck1_output_dir", ...}`.

**Compare two scored runs** (prints a markdown delta table; flags any
`wrong_numbers.count > 0` and any metric regression):

```
python run_suite.py compare --base-run C:\path\to\run1 --run C:\path\to\run2
```

`--base-run`/`--run` accept either a run directory (reads `summary.json`
inside) or a `baseline`-shaped JSON file directly.

## Decks root / adding an external or customer deck

Every deck-specific path in `suite_config.json` (`source_pptx`,
`ground_truth`, `source_pages_dir`, `fixtures_dir`) is relative to a **decks
root** directory, resolved in this order:

1. `--decks-root` on the CLI (any subcommand that touches deck files);
2. the `RASTER_EVAL_DECKS_ROOT` env var;
3. the built-in `decks/` folder shipped next to this script (only carries
   the non-regenerable deck1-3 fixtures -- see "Rebuilding deck assets").

`suite_config.json` in this repo ships with **no** customer/external-deck
entry. To evaluate against one (a large real-world reference deck, a
customer deck, anything you don't want in git):

1. Copy `suite_config.json` somewhere outside the repo (e.g. your own
   scratch dir) and append a deck entry, e.g.:
   ```json
   {
     "id": "acme",
     "name": "acme",
     "checker": "checker",
     "source_pptx": "C:\\wherever\\acme_source.pptx",
     "fixtures_dir": "C:\\wherever\\acme\\fixtures",
     "expected_page_count": 28,
     "note": "customer deck; absolute paths, kept outside the repo"
   }
   ```
   (Absolute paths in a deck entry bypass `--decks-root` entirely, same as
   `Path.is_absolute()` -> used as-is in `resolve_deck_path`.)
2. Run any subcommand with `--config C:\wherever\your_config.json`.
3. `fixtures_dir` needs `source_ground_truth.json` (OCR text per page) +
   `source_pages/NN.png`, in the shape `checker_pptx_report.py`'s
   `check_pptx` reads (`pages[*].{page_number, ocr_text_normalized,
   numeric_tokens, image_path}`).

Never point `--decks-root` or a deck entry's paths at anything inside this
repo checkout that isn't already tracked -- that would risk `git add`
picking it up later. Keep customer fixtures in a directory outside the repo
entirely.

## Rebuilding deck assets

`image_only.pptx` (the upload payload `launch` needs) is not checked in for
any of the four decks -- it's a trivial, fast, fully local rebuild from
already-checked-in assets, so shipping the binary would only be dead weight.

**deck1 / deck2 / deck3** (`ground_truth.json` + `source_pages/*.png` ARE
checked in -- their original source templates aren't part of this handoff,
so they can't be regenerated from scratch):

```
cd scripts\raster_redesign_eval\decks
python build_image_only_deck.py --from-images .\deck1_executive_report
python build_image_only_deck.py --from-images .\deck2_business_proposal
python build_image_only_deck.py --from-images .\deck3_data_journalism
```

No source pptx, LibreOffice, or PyMuPDF needed for `--from-images` -- it just
re-assembles the checked-in PNGs into a one-picture-per-slide PPTX using the
checked-in `ground_truth.json`'s `slide_width_emu`/`slide_height_emu`.

**deck4** (nothing under `decks/deck4_korean_tables/` is checked in -- fully
regenerable from code):

```
cd scripts\raster_redesign_eval\decks
python build_deck4.py .\_tmp_deck4_source            REM writes deck4_source.pptx
uv run --with pymupdf python build_image_only_deck.py ^
    .\_tmp_deck4_source\deck4_source.pptx .\deck4_korean_tables --slides 10
```

The second step needs LibreOffice (`soffice.exe`, default path
`C:\Program Files\LibreOffice\program\soffice.exe`) to render deck4_source
to PDF, and PyMuPDF to rasterize the PDF pages -- both only for this
from-scratch path, not for `--from-images`.

## What `score` computes, per deck

- `page_count`, `expected_page_count`, `page_count_ok`
- `canvas` / `a4_ok` (from the PPTX's own slide size; A4 landscape, 2%
  tolerance)
- `wrong_numbers` -- the **filtered, actionable** hard-fail view (deck1-4
  only; see "Wrong-number footer false positives" below). `wrong_numbers_raw`
  is the unfiltered `ground_truth_checker.py` output;
  `wrong_numbers_filtered_as_page_number` lists exactly what was excluded
  and why. Gate on `wrong_numbers`, not `_raw`.
- `wrong_number_candidates` -- OCR-fixture decks only (see "Wrong number
  candidates" below): human-reviewable, not a hard fail.
- `editable_text_ratio_overall` (sum editable chars / sum source chars
  across the deck) and `editable_text_ratio_body_mean` (mean of each
  slide's own ratio). For an OCR-fixture (`"checker": "checker"`) deck,
  these are **best-match** (see "Order-aware matching" below); the old
  positional numbers are kept as `*_positional`.
- `editable_text_ratio_body_excl_mockup` -- the same body-mean ratio, but
  with source text inside regions STRUCTURE classified as a genuine
  screenshot/UI mock/image removed from each page's denominator (see
  "Mockup exclusion" below). `None` when no `analysis_result.json` is
  available for that run (older runs never saved it -- see "Mockup
  exclusion"). Alongside it: `mockup_regions_count`, `mockup_text_chars`
  (total OCR-derived chars across all mockup regions), `mockup_regions`
  (per region: `page`, `region_id`, `bbox`, `reason`, `char_count`, for an
  auditor to crop and eyeball each one), and `mockup_analysis_available`
  (whether the file was found at all -- distinguishes "0 mockup regions"
  from "not measured").
- `char_recall_mean` / `char_precision_mean` (deck1-4, exact source text) or
  `char_recall_mean` / `numeric_recall_mean` (OCR-fixture decks, best-match;
  `*_positional` kept alongside)
- `table_cell_accuracy` (deck1-4 only; exact per-cell match against source
  tables)
- `full_page_copies`, `page_content_crops` (+ page list) --
  perceptual-hash-based, from `checker_pptx_report.py`'s picture matching
- `page_content_fallback_pages_product` -- the **product's own** evidence of
  a page-content fallback (see "Page-content crops: two different sources"
  below)
- `min_font_pt`
- `exactly_once_pptx_text_duplicate_pages` (deck1-4, text-only duplicate
  check) or `exactly_once` (OCR-fixture decks: OCR-based
  duplicate/missing/word-cut signals, needs a successful PDF render)
- `total_seconds`, `analysis_wait_seconds`, `generation_seconds`,
  `export_pptx_seconds`, `export_pdf_seconds` -- see "Timing fields" below
  (`total_seconds` was `analysis_duration_seconds`); `analysis_stage_totals_seconds`
  (currently always `None` -- see the same section)
- OCR-fixture decks only: `order_mapping` (per output slide: matched source
  page, text similarity, whether it matches its own position) and
  `order_mismatch_count_best_match` / `order_mismatch_output_slides_best_match`
- run-level (top of `summary.json`, not per-deck): `concurrent_harness` --
  see "Concurrent harness flag" below

## Wrong-number footer false positives (2026-09-25, fixed at the summarization layer)

**`ground_truth_checker.py`'s `numeric_exactness` over-reports "fabricated"
numbers for any deck whose auto_redesign output adds its own page-number
footer (e.g. "01", "02", ...) that wasn't already baked into the *original*
deck's text.** `ground_truth_checker.py` itself is left unmodified (scoring
logic stays identical); `wrong_number_filter.py`'s `classify_wrong_numbers()`
splits each page's raw `fabricated_numbers` into `wrong_numbers` (kept, real
hard fails) and `wrong_numbers_filtered_as_page_number` (excluded), applying
**all three** conditions together, never on value alone:

1. the token equals the *output slide's own* 1-based page number, zero-padded
   or not (`"4"` or `"04"` for slide 4);
2. that page's real source ground truth does **not** contain the token (so a
   deck whose original text legitimately included that number, e.g. as part
   of a real page range, is not filtered); and
3. the token appears as a standalone page-number-shaped shape/run on that
   slide -- either the *entire* text of some shape, or a short (<=4 char)
   run inside a shape sitting in the top/bottom 12% band of the slide (see
   `_footer_candidate_counter` in `wrong_number_filter.py`).

Verified baseline results (deck1/deck4, re-scored from this repo copy on
2026-09-25 and diffed byte-for-byte against the original eval-suite's
`baseline_prev_session.json` -- identical): `deck4`'s `wrong_numbers.count =
0` (10 raw fabrications, all exactly `"01"`..`"10"` on their own matching
page -- the layout's own footer stamp); `deck1`'s `wrong_numbers.count = 8`
(unchanged -- deck1's *original* text already had a page-number footer baked
in, so condition 2 fails for its own footer digits and nothing is filtered
there; the 8 real fabrications are genuine transcription errors, not footer
noise).

A more permanent fix belongs in `ground_truth_checker.py` itself, or better,
in the product: have it tag its own footer shape (name/role) so the checker
can skip it structurally instead of by value+position heuristics.

## Page-content crops: two different sources

- `page_content_crop_pages` (hash-based, `checker_slide.py`): a picture
  shape whose pixels hash-match a bottom-crop of some source page
  (`CONTENT_CROP_HASH_MAX_DIST=24`, `CONTENT_CROP_AREA_MIN_FRACTION=0.30`).
  Strict, visual, works from the PPTX alone.
- `page_content_fallback_pages_product` (from `generate_raster_deck.py`'s
  `fetch_slide_evidence()` -> `slide_evidence_report.json` ->
  `slides[*].has_page_content_fallback`, true iff the slide's own
  `rasterRenderAdvisories` include a `"page-content"` `groupId`): the
  **product's own record** of which slides it fell back to a page-content
  raster for, independent of what the picture ends up looking like in the
  exported PPTX.

These can disagree (the hash detector can under-count vs. the product's own
evidence, e.g. when the exported picture differs visually from the naive
bottom-crop the detector compares against); both numbers are reported side
by side per deck.

## Mockup exclusion (`editable_text_ratio_body_excl_mockup`, 2026-09-25)

`editable_text_ratio_body_mean` (and `_overall`) penalize a deck for keeping
a *genuine* screenshot/UI mock/image as a picture exactly the same as for
keeping actual sentence/paragraph text as pixels -- but the first is a
correct, deliberate STRUCTURE decision (there is no "editable text" for a
real screenshot to become), while the second is the thing this suite is
actually trying to measure. `editable_text_ratio_body_excl_mockup` removes
only the first kind's source text from each page's denominator.

**Where the classification comes from.** The raster analysis result (`GET
/api/v1/ppt/source-content/raster/analysis/{operation_id}`, defined in
`api/v1/ppt/endpoints/raster_reconstruction.py`) returns the full
`RasterAnalysis` (see `services/source_content/raster_contracts.py`):
per-page `regions`, each with `role`, `editable_fallback`, `review_reason`,
`bbox`, and `ocr_token_indices` into that page's own `ocr_inventory` (OCR
word boxes over the source image). `generate_raster_deck.py`'s
`save_analysis_result()` (in `raster_deck_analysis.py`) now writes this
payload to `RUN_DIR/<deck_id>/analysis_result.json` right after
`wait_for_analysis()` returns -- before review/approve can touch region
text, so it reflects STRUCTURE's own, unedited classification.

**The exclusion rule** (`mockup_regions.py`, `_is_mockup_region`):
`role == "screenshot" and not editable_fallback`. This is not a new
heuristic -- it is the exact condition already used pervasively across the
raster services to mean "a real capture" (see `raster_boundary.py`'s /
`raster_crop_edges.py`'s own `real_capture = crop.role == "screenshot" and
not crop.editable_fallback`), read here from the saved analysis instead of
computed. `_screenshot()` in `raster_structure_emit.py` is the only place
that creates a region this way, and it never sets `editable_fallback`, so
this reliably means "structure decided this box is not text, full stop" --
as opposed to:

- `editable_fallback == True` (regardless of role) -- text structure
  recognized as text but had to keep as pixels for a fidelity reason
  (`review_reason` values include `low_confidence_text_pixels`,
  `unreadable_text_pixels`, `unconfirmed_number`, `table_unstructured`,
  `crop_text_boundary*`, etc. -- the exhaustive "paragraph_pixels" /
  "line_pixels" family the coordinator's task named lives under this same
  flag). Stays IN the denominator.
- a `"page-content"` / `dense_group_pixel_fallback` render-time fallback
  (`services/source_content/raster_redesign_fallback.py`): a LATER,
  density-driven decision made during redesign layout, after analysis --
  these synthetic union-crop regions are never part of the analysis result
  at all (they're only visible as `rasterRenderAdvisories` on the exported
  SVG, i.e. `page_content_fallback_pages_product` above), so they can never
  be mistaken for a mockup region by this rule. Stays IN the denominator.

**Char counts are read, not guessed.** For each mockup region,
`mockup_regions.py` joins the OCR words at its `ocr_token_indices` (falling
back to the region's own `text` if it has no token indices) and counts
non-whitespace characters -- the same normalization `_norm_len` uses
elsewhere in this suite. This is exact per the analysis's own OCR word
boxes, not an area-proportion estimate.

**Availability.** `mockup_analysis_available: false` (and all four new
fields `None`/empty) whenever `RUN_DIR/<deck_id>/analysis_result.json`
doesn't exist. Verified (originally) with a synthetic `analysis_result.json`
against deck1's real output PPTX: one genuine mockup region (21 OCR chars)
is counted and excluded, a second region with `editable_fallback: true` on
the same page is correctly left out of the mockup count, and every
pre-existing metric (`editable_text_ratio_overall`, `_body_mean`,
`wrong_numbers`, ...) came back byte-identical to the no-analysis-file run.

**2026-09-25 bug fix -- envelope shape.** That synthetic test used the bare
`RasterAnalysis` dict directly, which hid a real bug: every file this suite
actually produces (and every file fetched straight from the endpoint) is
the FULL response envelope, `{"analysis": {...pages...}, "operation_id":
..., "status": ..., ...}` -- `mockup_regions.py`'s reader returned that
envelope unchanged, so `analysis.get("pages", [])` was always `[]` and
`mockup_regions_count` was silently `0` on every real run.
`load_analysis_result()` now unwraps `data["analysis"]`, and
`generate_raster_deck.py`'s `save_analysis_result()` now saves the whole
envelope (not just its `"analysis"` key) so what's on disk always matches
what a caller fetching the endpoint directly would see. Also matched
`mockup_audit/make_crops.py`'s own extraction: only a region with
`char_count > 0` counts, and `reason` is the region's raw `review_reason`
(usually `None` for a genuine screenshot), not a substituted label.
Confirmed on the real baseline: `role == "screenshot" and not
editable_fallback` finds 69 regions on the large external reference deck's
`analysis_result.json`, 65 of which have `char_count > 0` (13,152 chars
total) -- matching an independent audit script's count exactly (see
`eval-runs/baseline-2144f5034-r2/<external-deck>/mockup_audit/` in the handoff
tree). `editable_text_ratio_body_excl_mockup` can exceed 1.0 on a page
where most of its source OCR text was classified as mockup but the product
still rendered substantial editable content elsewhere on that page --
this produced a mean ratio of **1.193** on the real baseline (impossible;
see the next fix).

**2026-09-25 third fix -- internally inconsistent ratio (still >1.0).**
The fix above (subtracting mockup chars from a GT-derived denominator) was
not enough on its own: for checker-kind decks the numerator (editable
chars, measured on the OUTPUT pptx by `checker.py`) and the subtracted
mockup chars (from the analysis's OWN OCR pass over the SOURCE image) still
came from two different OCR passes / page-order correspondences, so the
denominator could still shrink below the numerator.
`mockup_regions.analysis_only_editable_ratio()` now computes BOTH sides of
the ratio from the exact same source for checker-kind decks: per analysis
page, numerator = chars of regions STRUCTURE published as editable text
(`role` in title/body/table/unknown, `editable_fallback` False);
denominator = chars of every OCR-text-bearing region on that page (any
role) minus that page's own mockup chars. Since both sides use the same
per-region char count, numerator is always a subset of denominator --
`0 <= ratio <= 1` by construction, not by clamping. Trade-off: this now
measures what STRUCTURE decided at analysis time, not what actually
survived to the exported pptx (a later page-content/dense-group fallback
isn't reflected). **Verified: the real baseline's mean dropped from the
impossible 1.193 to 0.610.**

For `ground_truth_checker` decks (deck1-4), the definition stays
GT-based, but the mockup subtraction is now `mockup_regions.matched_gt_chars()`:
a multiset (Counter) intersection between each mockup region's own OCR text
and the page's EXACT GT text, consumed once across all of a page's mockup
regions -- bounded to the page's own GT char count by construction, so it
can never go negative. The numerator (OUTPUT-measured editable chars) can
still in principle exceed that denominator for reasons unrelated to
mockups (see below); `mockup_regions.validate_ratio()` raises a `ValueError`
with the offending page/numbers rather than let an impossible ratio through
silently. `excl_mockup_metrics_ground_truth()` in `deck_mockup_metrics.py`
catches that per-page, and if ANY page fails, reports the WHOLE deck's
`editable_text_ratio_body_excl_mockup` as `None` with the violating pages
listed in `editable_text_ratio_body_excl_mockup_errors` (page, chars,
denominator, message) -- visible as data rather than aborting the rest of
that deck's scoring (`wrong_numbers`, the number gate below, etc., none of
which depend on this one metric). **Observed on the real baseline: deck1
page 2 (`editable_text_chars=256` vs `gt_chars=234`, ZERO mockup regions on
that page) -- a pre-existing characteristic of `editable_text_chars`
(counts every OUTPUT text shape, not cross-checked 1:1 against GT content)
that predates this fix and also silently affects the un-asserted
`editable_text_ratio_body_mean` there; out of scope for this fix to
correct without changing how `editable_text_chars` itself is measured,
which other metrics still rely on unchanged.** deck2/deck3 have no such
violation and score cleanly.

## Number gate (`source_numbers_total` / `correct_numbers_editable` /
`correct_numbers_pixel`, ground-truth decks, 2026-09-25)

How many of a page's real GT numbers (its own "N / total" page-footer
excluded, see `wrong_number_filter.gt_footer_token_counts` -- the same
"01 / 15" pattern documented above) made it into the output's editable
text vs. stayed as pixels (or are otherwise unaccounted for).
`source_numbers_total` is the footer-excluded GT number-token count for
the deck; `correct_numbers_editable` is how many of those are found, as a
multiset, in the matched (same-page-number) output slide's own editable
text (`wrong_number_filter.number_gate_counts`, the same matching
`ground_truth_checker.numeric_exactness` already uses); `correct_numbers_pixel
= source_numbers_total - correct_numbers_editable` (not-found-editable --
in practice dominated by numbers kept as pixels, but can't be cleanly told
apart from a genuinely dropped number without deeper region-level tracing,
so this is reported as the simpler difference per the task, not a fully
separate "missing" bucket).

## Pixel text by cause (`pixel_text_by_cause`, all decks, 2026-09-25)

Per deck: every pixel-kept text region's chars, split into WHY it's not
editable text -- to distinguish "product limitation we can fix" from
"structurally correct" pixel content. Computed entirely from
`analysis_result.json` (same single source and per-region char-counting as
`editable_text_ratio_body_excl_mockup`; see `pixel_text_causes.py`), plus
`slide_evidence_report.json` only for `page_or_group_fallback`'s region
count (see below). `pixel_text_by_cause.categories`: `mark_disagreement`,
`number_unconfirmed`, `table`, `mockup_screenshot`, `page_or_group_fallback`,
`other` -- each `{"chars": int|None, "region_count": int}`.
`pixel_text_by_cause.other_raw_reasons`: `{review_reason: chars}` for
whatever landed in `other`, so a new/unmapped reason code is never silently
absorbed. `compare` prints a compact `deck x category` table (base vs. new
chars/regions) after the main metrics table.

**Category rules** (`pixel_text_causes.py`'s `REASON_CATEGORY` +
`_categorize`): two ROLE-based rules take priority over any `review_reason`
lookup --

1. `role == "screenshot" and not editable_fallback` -> `mockup_screenshot`
   (STRUCTURE's own screenshot/UI-mock classification; the exact same set
   `editable_text_ratio_body_excl_mockup` excludes).
2. `role == "table"` -> `table` (a pixel-kept table, whatever the reason --
   observed on the real baseline: one region carries `role="table"` with
   `review_reason="crop_text_boundary_line"`, which would otherwise map to
   `mark_disagreement`).

Otherwise, `review_reason` is looked up in a mapping built from **every
review_reason actually observed on a pixel-kept region across the 5 real
baseline decks on 2026-09-25** (deck1-4 + a large external reference deck),
plus same-family codes from `services/source_content/*.py` not observed in
that specific run:

| raw `review_reason` | category | observed this run? |
|---|---|---|
| `low_confidence_text_pixels` | `mark_disagreement` | yes (dominant on every deck) |
| `unreadable_text_pixels` | `mark_disagreement` | no |
| `unreadable_glyphs` | `mark_disagreement` | no |
| `uncovered_ink` | `mark_disagreement` | yes |
| `crop_text_boundary` | `mark_disagreement` | yes |
| `crop_text_boundary_line` | `mark_disagreement` | yes |
| `crop_text_boundary_card` | `mark_disagreement` | yes |
| `crop_edge_line` | `mark_disagreement` | no |
| `unconfirmed_number` | `number_unconfirmed` | no (0 on all 5 decks) |
| `table_unstructured` | `table` | no |
| `table_cells_partially_unverified` | `table` | no |
| `table_cell_ocr_low_confidence` | `table` | yes |
| `page_structuring_failed` | `page_or_group_fallback` | no |
| *(anything else)* | `other` | `exactly_once_merge` observed once on the external reference deck (476 chars) |

**`page_or_group_fallback` needs a different source.** A page-content/dense-
group density fallback (`services/source_content/raster_redesign_fallback.py`)
is a RENDER-TIME decision made during redesign layout, AFTER analysis --
it never appears as a region in `analysis_result.json` at all (confirmed:
0 occurrences across all 5 real decks). So this category's `chars` is
always `None`; its `region_count` instead comes from
`slide_evidence_report.json`'s own `advisories_by_code["dense_group_pixel_fallback"]`
totals (the same advisory `page_content_fallback_pages_product` reads
elsewhere in this suite), when that file is available.

**Verified baseline breakdown (`baseline-2144f5034[-r2]`, 2026-09-25):**

| deck | mark_disagreement | number_unconfirmed | table | mockup_screenshot | page_or_group_fallback | other |
|---|---|---|---|---|---|---|
| deck1 | 405 chars / 23 | 0 / 0 | 313 / 2 | 217 / 4 | 0 / 0 | 0 / 0 |
| deck2 | 474 / 22 | 0 / 0 | 81 / 1 | 322 / 5 | 0 / 0 | 0 / 0 |
| deck3 | 1735 / 73 | 0 / 0 | 242 / 2 | 514 / 6 | None / 1 | 0 / 0 |
| deck4 | 605 / 12 | 0 / 0 | 0 / 0 | 180 / 2 | 0 / 0 | 0 / 0 |
| large external reference deck | 4224 / 147 | 0 / 0 | 961 / 9 | 13152 / 65 | None / 4 | 476 / 1 (`exactly_once_merge`) |

("chars / region_count" per cell.) `mark_disagreement` dominates every
deck by a wide margin -- consistent with HANDOFF.md's own finding that
paragraph/line-confidence pixelation, not tables or numbers, is the
biggest lever for the editable-ratio gap. Cross-checked: each deck's
`mark_disagreement` + `table` + `mockup_screenshot` + `other` chars sum
exactly matches an independent manual tally of every pixel-kept region
with `char_count > 0` grouped by `(role, editable_fallback, review_reason)`.

## Wrong number candidates (OCR-fixture decks, 2026-09-25)

`ground_truth_checker.py`'s `numeric_exactness` (deck1-4's `wrong_numbers`)
is a real hard-fail because its ground truth is exact python-pptx text: any
number in the output that isn't a real source number is unambiguously
fabricated. An OCR-fixture deck (`"checker": "checker"`) has no such exact
ground truth, so `wrong_number_candidates.py` flags CANDIDATES instead: any
number token in the output's own editable text absent from BOTH the
positional and best-match source page's OCR numbers (checking both reduces
false positives on a reordered deck). Raw candidates are then run through
the *same* page-number-footer filter as `wrong_numbers` (`role`==footer
position + shape-text match; see "Wrong-number footer false positives"
above) -- on the real large-external-deck baseline this cut 27 raw candidates to 8
actionable ones (19 were the deck's own page-number stamp). Fields:
`wrong_number_candidates.count`/`.candidates` (page, token, shape-text
context, bbox), `.raw_count`, `.filtered_as_page_number` (audit of what was
excluded and why). `score --crops` (or `baseline --crops`) additionally
saves, per candidate, a crop of the matched source page and a crop of the
output's own rendered page (reusing `check_pdf()`'s already-rendered PNGs)
around the same shape bbox, to `RUN_DIR/reports/<deck>_wrong_number_crops/`
-- for a human to eyeball source vs. output side by side.

## Timing fields (2026-09-25)

`analysis_duration_seconds` was misleadingly named: it was always the sum
of *every* driver stage (upload, snapshot, presentation create, analysis
wait, review, generate, evidence fetch, both exports), not just analysis.
Renamed to `total_seconds` (same formula, same value) and broken out via
`run_timing.py` into `analysis_wait_seconds` (the `raster_analysis_wait`
stage alone), `generation_seconds` (`generate_async`), `export_pptx_seconds`
/ `export_pdf_seconds` (each format's request/follow/download sub-stages
summed). `compare` reads an old summary's `analysis_duration_seconds` as
`total_seconds` so a stored pre-rename run still compares. `run_timing.py`
also exposes `analysis_stage_totals()`, which surfaces
`analysis["stage_timings"]` (an optional field another effort is adding to
the analysis result -- per page/stage seconds) as `analysis_stage_totals_seconds`
in the score dict when present; tolerant of three plausible shapes since
the producer's exact format wasn't available to verify against as of this
writing (every real `analysis_result.json` checked has no `stage_timings`
key yet, so this field is `None` on every current run -- unverified against
real data).

## Concurrent harness flag

`launch` now writes `RUN_DIR/run_config.json` (`{"concurrent_harness":
bool}`, default `true`) via `--concurrent-harness`/`--no-concurrent-harness`
-- a run-level record of whether the multi-deck `_scheduler` harness drove
this run (vs. a one-off manual `generate_raster_deck.py` call). `score`/
`baseline` read it from there by default, or accept the same
`--concurrent-harness`/`--no-concurrent-harness` flag as an override/supply
for a run this suite didn't itself launch (e.g. an older run dir). It's
written to the top level of `summary.json` (not per-deck) and shown by
`compare` next to every timing metric's label, since it materially affects
wall-clock duration.

## Driver fixes (2026-09-25)

- **Double PPTX export.** `generate_async()`'s request body used to omit
  `export_as` on the theory that omitting it disables the server's chained
  export. It does not: `GeneratePresentationRequest.export_as` defaults to
  `"pptx"` server-side (`models/generate_presentation_request.py`), so a
  chained PPTX export always fired regardless, and `main()` then ALSO fired
  its own explicit `export.pptx` command afterward -- every run exported
  PPTX twice. `generate_async()` no longer sends `export_as` at all (the
  default already does what this driver wants) and now always polls the
  legacy status endpoint once generation succeeds, to retrieve the chained
  export's own `export_operation_id`; `main()` follows THAT operation for
  the PPTX artifact instead of requesting a second one.
  `--force-explicit-pptx-export` opts back into always requesting one (e.g.
  if the chain itself failed and `export_operation_id` came back `None`).
  PDF is unaffected -- it's never chained, so it's always requested
  explicitly, same as before.
- **Failed-export error reporting.** `follow_export_to_artifact()` used to
  report a failed/dead_letter export operation via `item.get('result')`,
  which the timeline projection (`services/workspace_api_contract.py`)
  always sets to `null` on failure -- the message was always
  `"export operation failed: None"`. Now reports `item['error']`
  (public error code), `item['error_message']` (safe message),
  `item['retryCount']`, and `item['providerAttemptCount']` -- the fields
  that actually carry failure detail on a timeline operation item.
  `generate_async()`'s own generation-failure message gained
  `providerAttemptCount` for the same reason (it already had the other
  three).

## Order-aware matching (OCR-fixture decks)

`checker_pptx_report.py`'s own per-slide fidelity (`char_recall_mean`,
`numeric_recall_mean` in the raw report) and this suite's
`editable_text_ratio_*` compare output slide N against *positional* source
page N by default. For a reordered deck this understates fidelity.
`deck_scoring.py`'s `_one_to_one_best_match()` computes a **greedy
one-to-one** assignment of output slides to source pages (maximizing
per-pair char-recall similarity, with a bonus when a picture on the slide
was independently hash-matched to that source page as a full-page-copy/
page-content crop), and uses that match for the primary `char_recall_mean`,
`numeric_recall_mean`, and `editable_text_ratio_*` fields. The old
positional numbers are kept under `*_positional`. The full mapping is in
`order_mapping` (per output slide: `matched_source_page`, `char_recall`,
`matches_positional`).

This is **greedy, not globally optimal** (no Hungarian-algorithm solve), and
the hash bonus can pull an assignment away from the single best text match
to resolve a stronger picture-hash signal -- treat `order_mismatch_count`
as a diagnostic for "is this deck reordered and by how much", not as a
strictly better fidelity number than positional.

## What changed vs. the original eval-suite

Scoring logic (every number `score` computes) is **byte-for-byte identical**
to the original `eval-suite` copy this was moved from -- verified on
2026-09-25 by running `baseline` from this repo copy against the same
product-run output directories the original `eval-suite\
baseline_prev_session.json` was scored from (deck1, deck4, and a large
external reference deck used only for that one offline verification, never
committed here) and diffing the results field-by-field: identical except
the expected `pptx_path` string. Structural/packaging changes only:

- **File split.** The original single-file `checker.py` (559 lines),
  `run_suite.py` (824 lines), and `generate_raster_deck.py` (680 lines) each
  exceeded this repo's 500-line file-size limit; each is now a thin facade
  over several focused modules (see "Layout" above). No function's logic
  changed in the split, only its home file.
- **Decks root / external decks.** Deck paths now resolve through
  `--decks-root`/`RASTER_EVAL_DECKS_ROOT` instead of being fixed relative to
  this script's own directory, and `suite_config.json` ships with no
  customer/large-external-deck entry at all (add one via a private
  `--config`, see "Decks root" above). The original suite's customer-deck
  entry hardcoded both a `C:\Users\...\Downloads\...` source path and a
  sibling fixtures dir; neither pattern is reproduced here.
- **`baseline` is now generic.** The original `cmd_baseline` had a
  session-specific dict of `C:\Users\ibiz\AppData\Local\Temp\...` output
  directories hardcoded in the script. That's replaced by `--map`, an
  explicit JSON file the caller supplies (see "Usage" above) -- same
  function, no hardcoded machine/session path.
- **`expected_page_count` is now a parameter, default 28.** The original
  `checker.py`'s `check_pptx`/`check_pdf` hardcoded `28` (the one large
  reference deck's page count) as a literal inside the function body. It's
  now `deck_cfg.get("expected_page_count", 28)` / a `--expected-page-count`
  CLI flag on `checker.py`'s own `main()`, defaulting to the same `28` --
  same behavior for a 28-page deck, but no longer silently wrong for a
  differently-sized one configured externally.
- **Tesseract paths are now overridable.** `exactly_once.py`'s
  `TESSERACT_EXE`/`TESSDATA_DIR` were hardcoded (the latter to
  `C:\project\PPT-agent\ppt-agent\tessdata`, a specific worktree's path).
  Both now default sensibly (the invoking checkout's own `tessdata/`, the
  standard Windows Tesseract-OCR install path) and are overridable via
  `RASTER_EVAL_TESSERACT_EXE` / `RASTER_EVAL_TESSDATA_DIR`.
- **`build_image_only_deck.py` gained `--from-images`.** A small addition
  (not in the original) so deck1-3's checked-in `ground_truth.json` +
  `source_pages/*.png` are enough to rebuild `image_only.pptx` locally,
  without needing the (not-part-of-this-handoff) original source templates,
  LibreOffice, or PyMuPDF.
- **No customer data.** No customer fixtures, OCR text, page images, or a
  customer source PPTX path are anywhere in this directory.
  `deck1`-`deck4` are synthetic/fictional (see each deck's `note` in
  `suite_config.json`).
