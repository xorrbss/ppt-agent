# Template A density and visuals: progress (2026-11-05, in progress)

Branch `claude/template-a-visuals-20261105`, from `main` at `696be2ac`. Goal: raise the vision judge's density and
visuals axes for `template-redesign` decks on template A (the real customer deck) without lowering first-draft
acceptance. Previous state (`docs/svg-template-handoff-20261104.md`, "Results of the 2026-11-05 plan"): judge overall
3.00 on generated pages vs about 4.4 on the template's own pages; density and visuals are the lowest axes.
The customer deck, its renders and anything derived from its text stay out of the repository; only counts appear here.

## Done so far

* `1b78f2b1` the page number drawn by `finalize_svg` keeps the style primary only while it reads at 4.5:1 on the
  page background (topmost full-canvas `rect` fill, else the style `background` token, else white), else the stronger
  of white and `#0F172A`. Output bytes are unchanged when the primary already passes. The ink rule moved to
  `utils.color_contrast.readable_ink`; the fallback page builder's `_ink_on` uses it. Tests:
  `tests/unit/test_svg_page_number_contrast.py`. Closes the open item at the end of the 2026-11-04 handoff.
* `274f08ba` four stale tests in `test_owner_scope_postgres_principal.py`.
* Full pytest baseline and failure classification: `docs/test-failure-baseline-20261105.md` (285 pre-existing
  failures, deterministic). Real-bug candidates there, not fixed: no owner RLS policy for the four `svg_template*`
  tables (row 14), alembic drift for `workspace_generation_lineage_state` (row 13), 21 unregistered workspace error
  codes (row 16), source-policy gate breaks (row 21), `svg_partial_recovery_lineage_conflict` (row 3).

## Converter digest decision material (handoff open issue 9, branch `wip/s2n` `30ed896b`)

* `wip/s2n` records the two vendor edits (`chart-furniture.16`, `native-fixed-text.1`) as patches and regenerates
  `vendor/slide_master/MANIFEST.json`. Compared with `main`: the 87 vendored file hashes are identical; only manifest
  metadata changes (`patches`, `presentonPatches`, line endings). Converter behaviour does not change.
* `converter_digest()` (`workers/public_svg_pptx_convert.py`) hashes the whole manifest file, so the digest changes.
  A stored proof is compared with the current digest inside the export operation's quality receipt
  (`services/svg_export_quality.py`) and by the final PPTX review (`services/svg_final_pptx_review.py`): an export made
  before the change cannot get a final PPTX review until it is exported again. Downloads are not affected.
* The same digest also covers nine adapter files (`export_projection.py`, `export_scene.py`, `frame_native_text.py`,
  ...); 19 commits on `main` since 2026-10-01 changed them, each with the same effect.
* Options: (A) merge `wip/s2n` as is; (B) A plus an identity that hashes only the manifest's `files` and `version`, so
  later metadata-only edits keep the digest (one digest change, in the same commit); (C) keep holding. Recommended: A,
  after the current measurement batch so the digest does not change mid-batch. Decision is the user's.

## Measurement setup (this session)

* Linux VM, PostgreSQL 16 on port 5433, two independent backends (own database, app-data and artifact roots each),
  `claude-sonnet-5-5`, `DISABLE_IMAGE_GENERATION=true` as in the fake-LLM harness (icons only, no pictures), templates
  A and B registered on both. A matrix is split across the two backends by input, with the same input-to-backend
  mapping for every variant, and the per-backend result files are merged before `run_eval.py decide`.
* Fonts: Paperlogy is embedded in template A only as compressed subsets (EOT, MTX) that cannot be extracted here;
  references were rendered with a fontconfig alias Paperlogy -> Pretendard (weights 3-5 Regular, 6-8 Bold), the same
  substitution the registration uses.
* Judge sanity check with re-rendered references (judge version 2, template anchors):
  * Template A: original reference pages 4.0, 4.2, 4.6, 5.0 (slide 4, a sparse divider-like page: 1.4); plain
    low anchor 1.4. Consistent with the earlier calibration (originals about 4.4, plain about 1.2).
  * Template B: its own body pages 2.6-3.0, low anchor 1.4. The scale is compressed, so B's judge scores cannot
    separate a change; B is used for count metrics (first try, fallbacks, tables, charts, flow shapes) only.

## Template A composition (counts from the PPTX, 100 body slides)

Median / p25 / p75 / slides with any: shapes 56 / 27 / 94 / 100; flow shapes (arrow, chevron presets and arrowheads)
4 / 0 / 7 / 61; connectors 4 / 2 / 7 / 100; text characters without spaces 816 / 650 / 1071. Charts on 11 slides
(doughnut 13, line 5, bar 2, pie 1 chart parts), tables on 47. One picture recurs on 92 slides (furniture); pictures
used on at most three slides appear on 47 slides (median 0, p75 5), almost all PNG. Only 7 body slides have none of
chart, table, picture or flow shape. Of the judge's score-5 anchors, slide 9 has a chart, a table and 78 shapes, slide
66 has 14 pictures, 9 flow shapes and 96 shapes.

## Code map: what decides a template-redesign body page's visuals (phase 1, before measurement)

Hypotheses to test against the baseline measurement, not conclusions:

* The outline asks for bullet lists only (`utils/llm_calls/generate_presentation_outlines.py:113-126`), so a source's
  numbered sub-headings and key-message lines become plain bullets; body composition then sees about one section per
  page and picks single-section exemplars.
* `services/svg_templates/generation.py:117` adds "Prefer clear simple compositions. Decorative cards, icons or visual
  difference are not evidence of improvement." to the author's design block and rules, against the body composition
  grammar and the judge's visuals rubric.
* Infographic planning gives one archetype with 3-6 nodes per page (`models/authored_infographic_plan.py`); icon slots
  exist only on figure pages (`services/generation_core/assets/offer.py:215-261`). Template A pages combine two to four
  sections with different components.
* The author sees fixed reference renders of the template (pages 1, 2, middle, last;
  `services/svg_uploaded_design_reference.py`), not the dense body pages the judge uses as score-5 anchors.
* Native charts come only from `chart:` outline lines, which only the content structure policy's `tables` part
  enables (off by default); the slide contract has no arcs, so doughnuts can only be approximated.
* Docs vs code: `docs/template-body-composition-20261105.md` (opening section) still says the exemplars are off by default; the
  code default is on.

## Smoke data against the hypotheses (2026-11-05, template A, data_report_ko, N=1 per backend)

Two decks, both judge overall 2.74 (structure 3.1 / 2.8, density 2.5 / 2.6, hierarchy 2.8 / 3.0, tone 3.0 / 2.8,
visuals 2.3 / 2.5); first drafts 8 and 10 of 10, fallback 1 and 0. Per-page judge evidence and the stored decks say:

* Sections: the judge counted 0 sections on 13 of 20 pages (1-3 on the rest); every outline page held one `##`
  title and 4-7 bullets (`content_kinds` sections = 1 on all 20). The best page of both decks (structure 4,
  hierarchy 4) is the one where the author invented three numbered sub-headings on its own. Supports the outline
  hypothesis. The first two baseline decks agree (sections 0 on 5 and 4 pages of 10).
* Visuals: 0 polygons in both decks (no chevron or arrowhead), 0 pictures (icons only, image generation off),
  23-26 icons per deck, offered on 7-9 pages and left out entirely on 3-4 of them (the writing policy makes them
  optional). Pages that used 3-5 icons scored visuals 2-3, pages without 2-3: icons alone do not move the axis.
  Visuals never reached 4; the rubric's 5 needs the anchors' mix (step shapes, charts, icon tiles, tables).
* Density: text items 40-62 on 7 of 10 pages (the rubric's 5 needs 40 or more), but the judge's empty share was
  30-62 % (measured blank share 0.32-0.62 against 0.15-0.37 on the originals), which caps density at 3: pages use
  40-50 px figures and tall cards whose lower halves are empty. The grammar line "when the facts are few, enlarge
  the components" reads as "enlarge the type".
* The author sees no render of the template at all: the frozen snapshot's `references` is empty for this
  registration (`reference_examples: []`), so the only picture of the template it has is the exemplar outline text.
  The earlier note that it sees fixed pages 1, 2, middle and last was wrong for this registration.
* The cover scores 1.6-1.8 as a body page (KPI cards, 48-62 % empty) and costs the deck mean about 0.1; not changed.

## Knobs added (commit `ed925305`, both internal, default off, prompt only)

* `SVG_TEMPLATE_OUTLINE_SECTIONS=on`: the outline system prompt of a template-redesign deck ends with
  `TEMPLATE_SECTIONS_CLAUSE` (`utils/svg_template_outline.py`; wired in `generate_presentation_outlines.py` and
  `presentation_generation_pipeline.template_outline_sections`): a key-message line, then two to four `###`
  sub-sections numbered with a single digit (`### 1. ...`; `01` would become a required figure), each holding its
  own bullets, table rows or series, every source fact kept, never padding. `explicit_outline_title` still finds
  one `##` title; `content_kinds` sees the sections and matches multi-section exemplars. Expected: judge sections,
  hierarchy and structure up; density through more text items per section; risk: longer bodies (body-outside,
  overlap retries). Tests: `tests/unit/test_svg_template_outline_sections.py`.
* `SVG_TEMPLATE_VISUAL_GUIDANCE=on`: `services/svg_templates/generation.template_composition_guidance` freezes
  `TEMPLATE_VOCABULARY_LINE` instead of "Decorative cards, icons or visual difference are not evidence of
  improvement" (a snapshot digest input, so new decks only), and `body_composition.composition_rules` closes its
  density line with `FILL_BY_EXPLAINING` (full explanations, caption rows, icons beside labels, compact key figures
  at most about three times the body size) instead of `FILL_BY_ENLARGING`. Expected: visuals (icons used, step
  shapes where the source states a sequence), density (empty share down); risk: low, no new check. Tests:
  `tests/unit/test_svg_template_visual_guidance.py`.

### Review fixes and known limitations

* The sections clause names Markdown table rows as a sub-section's material only when the content structure
  policy's `tables` part is on (the writing policy's pipe rule forbids table pipes otherwise); with the policy off
  a sub-section holds bullets or a series of figures. The vocabulary line keeps "a table for attributes the source
  compares": the author draws tables under its own native-table rules, and baseline decks with the policy off
  already carry 5-8 native tables each at 9-10 first drafts of 10; the 5-of-5 fallback predates the 2026-11-04
  server fixes and came from outline pipe rows without the cell-text clause, which this line does not ask for.
* The density sentence follows the frozen snapshot (`snapshot_has_visual_guidance`), so a deck admitted with one
  setting and resumed or remade under the other keeps one text.
* `demote_extra_titles` turns a slide's second and later `##` headings into `###` when the knob is on, so a framed
  title field never fails on several titles; a slide the model returns without any `##` keeps failing
  `svg_template_title_required` (no title is invented).
* Not handled: the outline density floor and ceiling steps (`outline_density`) measure the whole content including
  headings, so a sectioned outline counts its headings as text; the UI `/outline` endpoint and the Mem0 outline
  prompt do not know the template mode and never get the clause.

## Next

Baseline matrix on template A (4 inputs x 3 runs), the change behind an internal knob (default off), independent
review, the same matrix with the knob on, `decide`; then template B count metrics; then the handoff update.
