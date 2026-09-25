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
  wrong_number_filter.py        # page-number-footer false-positive classification
  mockup_regions.py             # mockup-region (screenshot/UI-mock) exclusion, from analysis_result.json
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
- `analysis_duration_seconds` (sum of `run_report.json` stage timings, when
  present)
- OCR-fixture decks only: `order_mapping` (per output slide: matched source
  page, text similarity, whether it matches its own position) and
  `order_mismatch_count_best_match` / `order_mismatch_output_slides_best_match`

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
doesn't exist -- true for every run captured before this change (deck1,
deck4, and the large external reference deck used for the original
verification all predate `save_analysis_result()`; a fresh `launch` run
will have it). Verified with a synthetic `analysis_result.json` against
deck1's real output PPTX: one genuine mockup region (21 OCR chars) is
counted and excluded, a second region with `editable_fallback: true` on the
same page is correctly left out of the mockup count, and every pre-existing
metric (`editable_text_ratio_overall`, `_body_mean`, `wrong_numbers`, ...)
came back byte-identical to the no-analysis-file run.

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
