# Template A density and visuals: progress and results (2026-11-05)

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
  After the merge with `main`'s verifier revision ledgers it applies only to decks whose frozen style snapshot
  carries `page_number_ink: svg-page-number-ink/v1` (pinned at admission by `pin_svg_style`), so stored decks
  replay byte-identically and their evidence keeps validating (`606d702a`; ledger states reviewed under the
  current revisions with reasons in `services/verifier_revisions.py`).
* `274f08ba` four stale tests in `test_owner_scope_postgres_principal.py`.
* Full pytest baseline and failure classification: `docs/test-failure-baseline-20261105.md` (285 pre-existing
  failures, deterministic). Real-bug candidates there, not fixed: alembic drift for
  `workspace_generation_lineage_state` (row 13), 21 unregistered workspace error
  codes (row 16), source-policy gate breaks (row 21), `svg_partial_recovery_lineage_conflict` (row 3). Row 14 (the
  `svg_template*` RLS) was a stale test, see Next.

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
  after the current measurement batch so the digest does not change mid-batch. (Superseded: `main` resolved it
  with #109, see Next.)

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

## Batch 1: both knobs on against the baseline (template A, 4 inputs x 3 runs, paired)

`decide`: hierarchy 3.10 -> 3.48 (better, 4/4 cells), overall 2.74 -> 2.88 (+0.14, under the 0.2 threshold),
structure +0.19, tone +0.10, density 2.46 -> 2.50 and visuals 2.18 -> 2.15 (no difference); first drafts 8.92 ->
8.92, fallback 0.33 -> 0.25, gate 8.75 -> 9.33; native tables 5.9 -> 4.0 per deck. Per-page evidence (108 body
pages per arm):

* Sections: the judge's count went from a mean of 1.39 (45 pages with 0) to 2.47 (7 pages with 0); key-message bar
  present 55 % -> 77 %; "numbered sub-headings" entered the template components it recognised (0 -> 50 mentions).
  Outlines carry 18-31 `###` sub-headings per deck (0 before). The sections lever did what it was meant to.
* Visuals did not move because nothing new was drawn: polygons 1.1 -> 0.9 per deck, polylines 1.1 -> 0, native charts
  0.2 -> 0.2, icons 32.1 -> 32.8, icon pages offered/used unchanged (8.4/7.9 -> 8.4/7.9). The visuals reasons are the
  same words in both arms ("no charts, diagrams or figures like the anchors", "only small line icons": 74 and 80 of
  108 pages). The vocabulary line changed no drawing, so `SVG_TEMPLATE_VISUAL_GUIDANCE` is kept on for batch 2 only
  so that batch 2 differs from batch 1 by one knob; the data gives it no effect of its own.
* Density did not move because the empty area did not: judge empty share 37.2 % -> 37.2 % (measured blank 0.43 ->
  0.42; the originals 0.15-0.37), text items 35.2 -> 34.3 per page, "large type" named on 28 -> 26 pages. The reworded
  density sentence changed nothing either.
* Tables dropped through the sections clause, not the author: with the policy off the baseline outlines still held
  23-50 Markdown table rows per deck on three inputs (the model keeps a source's tables and tabulates even the
  unstructured report), and those became 5.9 native tables per deck at 8.9 first drafts of 10. The clause named
  "bullets or figures" as a sub-section's material and the unstructured input's rows fell from 42 to 0 per deck
  (tables 8.3 -> 1.0); the structured and data inputs kept theirs (7-9 and 6-9). The clause now keeps "a Markdown
  table where the source itself tabulates those facts" with the policy off (`utils/svg_template_outline.py`).

## Batch 2 lever: `SVG_TEMPLATE_STEP_PICTURES` (commit after `bce62345`, internal, default off)

The judge's visuals axis asks for "icons, figures, charts and diagram shapes of the kind and amount of the score 5
anchors"; slide 66 (an anchor) carries 14 small pictures, four of them above its four step boxes, and 47 of the
template's 100 body slides have page-specific pictures. The generated pages have none (image generation is off and
`SVG_ILLUSTRATION_MODE` is off). `SVG_TEMPLATE_STEP_PICTURES=on` runs the asset offer of a template-redesign deck in
vector mode with the `spots` part alone (`drivers/svg/asset_slots.resolve_offer`, `template_step_pictures`): a
process, cycle, timeline or roadmap page with three to six planned steps gets one 220x180 vector picture per step,
drawn by the model in the deck palette in one call per page (`assets/vector_offer.plan_jobs`, at most 12 calls per
deck), offered as optional slots ("place it above or beside that item's text"); no cover or scene picture is drawn,
and `build_asset_offer` now makes the plan as in off mode when the parts leave scenes out, so scene pages keep their
icons. Expected: visuals up on the step pages (3-5 of 10 on the proposal inputs, 0 on the data report), no change
to first drafts (the slots are optional; a drawing is validated before it is offered); cost about $0.02-0.04 and
10-18 s per step page, in parallel with icon selection.

The "source itself tabulates" wording may not bring the unstructured input's tables back: its baseline tables were
model-made from prose, not the source's own. Batch 2 is read per input for that.

Batch 2 environment: `SVG_TEMPLATE_OUTLINE_SECTIONS=on SVG_TEMPLATE_VISUAL_GUIDANCE=on SVG_TEMPLATE_STEP_PICTURES=on`
(compare with batch 1 for the pictures, with the baseline for the whole).

## Batch 2: all three knobs on (template A, 4 inputs x 3 runs, paired; code `6a259b9a`)

`SVG_TEMPLATE_OUTLINE_SECTIONS=on SVG_TEMPLATE_VISUAL_GUIDANCE=on SVG_TEMPLATE_STEP_PICTURES=on`. Batch 2 also carries
the sections clause's table wording fix, so against batch 1 it differs by the step pictures and by that wording. The
provider account ran out of credit after 4 of the 12 decks; the other 8 were run with `matrix --resume` after the key
was replaced (same code, same backends, same templates).

Against the baseline (`decide base svp`):

| Metric | Baseline | Batch 2 | B - A | Cells | Verdict |
|---|---|---|---|---|---|
| judge overall | 2.74 | 2.95 | +0.20 | 4/4 | better |
| judge hierarchy | 3.10 | 3.61 | +0.51 | 4/4 | better |
| judge structure | 3.00 | 3.25 | +0.25 | 4/4 | better |
| judge tone | 2.98 | 3.09 | +0.12 | 4/4 | no difference |
| judge density | 2.46 | 2.55 | +0.09 | 2/4 | no difference |
| judge visuals | 2.18 | 2.23 | +0.05 | 2/4 | no difference |
| first drafts accepted (of 10) | 8.92 | 8.50 | -0.42 | 4/4 lower | no difference (under 0.5, but every cell lower) |
| fallback pages | 0.33 | 0.17 | -0.17 | 2/4 | no difference |

Report-only: native tables 5.9 -> 6.4 per deck (batch 1 had 4.0: the table wording restored them, including the
unstructured input at 8-9), flow shapes 52 -> 70, icons 32 -> 30, words kept 0.72 -> 0.77, numbers kept 0.99 -> 0.98,
retried pages 1.1 -> 1.5.

Against batch 1 (`decide sv svp`): no metric beyond its threshold; visuals +0.07, density +0.05, first drafts -0.42
(3/4 cells lower). Step pictures were offered and drawn: 3-15 accepted drawings per deck (one deck 5 of 7: one invalid
XML, one missing), but neither visuals nor density moved.

## Template B generality (2 inputs x 3 runs, paired; sections + visual guidance, code `bce62345`)

First drafts 7.33 -> 8.00 (better, 2/2 cells), fallback 0.50 -> 0.33, gate passed 7.7 -> 9.2, words kept 0.67 -> 0.80,
tables 2.8 -> 3.5, flow shapes 39 -> 47; judge hierarchy +0.42 (2/2) and visuals -0.37 (2/2), but B's judge separates
its own originals (about 2.7) from a plain page (1.4) by only 1.3 points, so B's judge numbers are not used for a
decision. The sections clause did not lower acceptance on a second, unrelated template.

## Conclusions

* `SVG_TEMPLATE_OUTLINE_SECTIONS` works: hierarchy +0.38 (batch 1) and +0.51 (batch 2) on template A, structure and
  overall up with batch 2, acceptance unchanged on A in batch 1 and better on B. Proposed: default on (a decision for
  the user; not changed here). Caveat: the clause with the table wording fix was measured only together with the other
  two knobs (batch 2), and batch 2's first drafts were lower in every cell (-0.42, under the threshold).
* `SVG_TEMPLATE_VISUAL_GUIDANCE`: no measured effect on what is drawn, density or visuals. Keep off.
* `SVG_TEMPLATE_STEP_PICTURES`: drawings were made but visuals did not move and first drafts were lower. Keep off.
* Density and visuals, the goal, did not move with any prompt-level change. The remaining gap to the originals is
  pictures and doughnut/diagram figures (47 of 100 template body pages carry page-specific pictures, generated decks 0
  with image generation off), a body that is about 40 % empty against 15-37 % in the originals, and the author never
  seeing the template's pages (the registration keeps no reference renders for the author: `references` and
  `reference_examples` are empty). The next lever is structural: give the author renders of the template body pages
  that match the page's content kinds (stored at registration), then measure again.

## Cost

Provider calls for this work: about $104 (generation $100.47 in the two harness databases, judging about $4), of
which about $17 after the key change.

## Next

1. Done: `SVG_TEMPLATE_OUTLINE_SECTIONS` defaults to on (user decision, without a sections-only re-run; watch first-draft
   acceptance on template decks, see the caveat above).
2. Template page renders for the author (registration change, re-registration of templates).
3. Converter digest: resolved on `main` by #109 (`3d7302e4`: the two patches, the rebuilt manifest and a digest alias
   that accepts the superseded manifest digest while the executed converter bytes are identical); `wip/s2n` is obsolete.
4. Real-bug candidates in `docs/test-failure-baseline-20261105.md`. The `svg_template*` RLS item was a stale test (the
   tables have strict owner+tenant RLS since `d8f0a2b4c6e9`), fixed in `36fedb17`. Noted while checking it: the in-process
   retention sweep runs without an owner principal, so on PostgreSQL it cannot see binding rows and may treat bound
   assets as unbound (deletes are still blocked by the deferrable FK); it should run as the controller role.
5. The `.venv` Python (uv python-build-standalone) cannot `posix_spawn(setsid=True)`, so template registration fails at
   the frame preview render on this Linux VM; the measurement backends used a venv built from the system Python 3.11
   (`docs/svg-user-templates-linux-20261103.md` describes the same). The 2026-11-04 handoff's note that this was not
   reproduced does not hold here.
