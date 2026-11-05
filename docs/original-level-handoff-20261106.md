# Original-level template decks: loss map, plan and baseline (handoff, 2026-11-06)

Priority set by the user: generate template-redesign decks at the level of the registered template's own pages.
This note hands the work over (development continues in another tool). Everything measured so far is merged
on `main` (#136-#142); nothing is in flight. Earlier steps: `docs/template-a-visuals-progress-20261105.md`,
`docs/template-body-renders-design-20261105.md`, `docs/svg-template-handoff-20261104.md`.

Template A is the real customer proposal deck (101 slides). Its text, renders and anything derived from them stay
out of the repository; only counts appear here.

## Decisions in force

* **No source-skeleton reuse** (`.spec-memory/decisions/generation.no-source-skeleton-reuse.1.json`, CLAUDE.md):
  never copy an original page's body shapes/layout and swap text or data (the Deck IR / Design Pack / fixed-slot
  route that failed repeatedly). Reach the original level by composing page bodies.
* **Pictures come from LLM-drawn SVG** (vector illustration mode), not an image provider, for this work.
* **Measure with source-sized inputs** (`scripts/svg_template_eval/inputs/requests/*_sized.json`, #139-#142):
  one body slide per 650 source characters plus the cover (data 5, dense 9, structured 4, unstructured 6 slides).
  The 10-slide requests put ~386 characters on a page where the template carries ~816, capping judge density
  near 2.5 whatever the change.
* Knob results so far: `SVG_TEMPLATE_OUTLINE_SECTIONS` default **on** (hierarchy +0.38/+0.51, #136);
  `SVG_TEMPLATE_VISUAL_GUIDANCE`, `SVG_TEMPLATE_STEP_PICTURES`, `SVG_TEMPLATE_BODY_RENDERS` measured no effect, off.

## Loss map (24 template-A decks, 10-slide inputs; read-only analysis of the stored decks and logs)

### Charts: drawn by the author, discarded by validation

| Stage | Count |
|---|---|
| Outline lines with 3+ "label value" items | 53 |
| Rejected by prose chart derivation | 43 (33 because the unit is only in a sub-heading or parenthesis such as "(억 원)", `drivers/svg/prose_chart_literal.py:130-144` `if not unit: return None`; one 12-month series split into two lines, only half accepted) |
| `chart:` lines derived / dropped by grounding (model-computed values, correct) | 10 / 5 |
| Parsed as native requirements (revision v3) | 5 of 5 |
| Author declared the chart with the same data | 5 of 5 pages, 10 of 10 attempts (`receipt.native_declarations`) |
| **Accepted by projection** | **0 of 10**, all `native_chart_label_not_drawn` (`drivers/svg/native_chart.py:269-290, 577`) |
| Page outcome | 5 of 5 replaced by the text-only template fallback page (the drawn chart is lost too) |
| Self-declared charts on published pages | 21, **0 accepted** (label_not_drawn 17, line_geometry_invalid 2, value_geometry_mismatch 1, grid_category_conflict 1) |
| Native charts in the exported PPTX | 0 |

Why: a single-series chart draws only its title (no legend), and `_drawn_series_name` accepts a title as the
series name only in the form "name · 단위: X" (14 of 17 failures are single-series). The second attempt's hint
(`drivers/svg/render.py:1044`) carries the failure list but not the drop reason or the undrawn labels, so it
repeats the mistake. `native_requested_chart_missing` is a CONTENT failure (`quality/page_failure_kind.py:43-44`),
so a page with every value drawn still falls back. Only 5 of 24 outlines got `chart:` lines: the outline clause that
asks for them (`utils/content_structure_policy.py:56-67`) is only added with the structure policy on, and prose
derivation needs a unit on every item. The pipeline can export bar, column, line, area, pie and donut (PPTX
doughnut); all 21 declarations were column/bar/line, while template A's charts are mostly doughnuts (13 of 21).

### Pictures

Template-redesign decks take the normal asset offer (`drivers/svg/asset_slots.py:276-321`; the template pin only
skips the native-literal offer at `:187`). They have `authored_style=None`, so the `default` style without an
`illustration_prompt`; `deck_has_picture_source` (`assets/infographic_plan.py:36-47`) is then true only for
alpha-capable image providers (gpt-image-1.5/2/2.5 variants, codex_image; `assets/icon_pack/provider.py:57-63`),
and even then only "concrete object" scene pages get one, capped at max(1, round(0.25 x picture pages))
(`models/authored_infographic_plan.py:121,265`): 0-2 per deck, against page-specific pictures on 47 of
template A's 100 body pages. The evaluation harness also hard-exports `DISABLE_IMAGE_GENERATION=true`.
Vector mode (`SVG_ILLUSTRATION_MODE=vector`, parts cover/scene/spots/pairs/pictograph; `assets/vector_offer.py`,
`utils/llm_calls/draw_vector_illustrations.py`, validated by `assets/vector_illustration.py`: palette only, no
text, <= 300 elements, failures dropped and optional) therefore needs the template deck to plan pictures at all.

### Empty space (judge empty share 37 %, measured blank ~0.42 vs 0.15-0.37 on the originals)

1. Little text per page: median 386 characters vs 816 on the template (addressed by the sized inputs).
2. The text is spread over the whole body: text ink 13.7 % while content reaches the body bottom (median 0.99);
   the composition grammar's density line ends with `FILL_BY_ENLARGING` (`svg_templates/body_composition.py`
   ~759); 54 of 82 tables are >= 1,000 px wide; cards leave 20 % empty below their last line on average (20 of
   311 more than 40 %); empty share vs text amount r = -0.07.
3. Fallback pages (9, from native requirement failures) score 1.8.
Not causes: font size (text-weighted median 15 px), repairs.

### Other

Flow/step diagrams on 4 of 108 generated pages (template: flow shapes on 61 of 100); tables on 81 % of
generated pages (template 47 %); key-message bars and sections are already fine.

## Baseline with the sized inputs (current `main` defaults, template A, 3 runs per input, 12 decks)

| Input (slides) | First drafts accepted | Fallback pages | Judge overall | density | visuals | hierarchy |
|---|---|---|---|---|---|---|
| structured (4) | 2.67 of 4 | 0.33 | 3.07 | 3.25 | 2.08 | 3.42 |
| data (5) | 3.00 of 5 | 1.00 | 2.80 | 2.67 | 1.93 | 3.20 |
| unstructured (6) | 4.67 of 6 | 1.33 | 2.59 | 2.39 | 1.67 | 3.17 |
| dense (9) | 6.67 of 9 | 0.33 | 2.78 | 2.63 | 1.89 | 3.41 |
| all | 4.25 per deck (~71 %) | 0.75 | 2.81 | 2.73 | 1.89 | 3.30 |

Native charts 0, numbers kept 0.96, words kept 0.71. One deck of the 12 first failed with
`svg_source_quality_failed` (render stage) and was re-run. Fuller pages lower first-draft acceptance (~71 % vs
~84 % with 10 slides): watch it with every change. The original pages score ~4.45 on the same judge.

## Plan (each change behind an internal knob or a revision pinned at admission; stored decks replay byte-identically)

1. **Charts** (revision v4 of the native requirements, pinned for fresh decks): accept a single-series group's one
   title as its series name; put the drop reason and undrawn labels into the retry hint; keep a drawn page whose
   declared data equals the requirement and whose required values are drawn instead of falling back (content
   integrity checks stay strict); accept a unit from the sub-heading/parenthesis and join a series split over
   consecutive lines (never compute values; grounding stays); prefer donut/pie for composition series.
   Expected: charts 0 -> about the template's share, fewer fallback pages.
2. **Pictures (LLM SVG)** behind `SVG_TEMPLATE_PICTURES`: plan pictures for template decks (an illustration
   prompt from the template's palette/style), align the scene definition and share with the template's picture
   share (template A ~47 %), vector mode for scene/spots/pictograph (no cover picture: the template frame owns the
   cover), sizing rules inside the body region.
3. **Content sizing** behind `SVG_TEMPLATE_CONTENT_SIZING`: replace "enlarge the components" with content-sized
   components (remaining space in one block), narrow tables sized to their columns, cards sized to their content;
   add a card-empty-below-text metric to `scripts/svg_template_eval/metrics.py` before any rejecting check.
4. **Flow diagrams**: re-measure the existing connectors policy (`CONTENT_STRUCTURE_POLICY_SCOPE=template`,
   `CONTENT_STRUCTURE_POLICY_PARTS=connectors`) with N >= 3 (it cost -1.7 first drafts once).
5. **Measure** each lever paired against the sized baseline (`run_eval.py matrix` + `decide`), then the combination,
   template B for count metrics, then the user's PowerPoint review; default-on only what measured better.

Targets: judge overall 2.81 -> >= 3.8, visuals and density >= 3.5, first drafts and numbers kept not lower,
no template wording leak.

## How the measurements were run (Linux cloud VM)

* PostgreSQL 16 on 127.0.0.1:5433; two backends from `scripts/fake_llm_e2e/run_backend.sh` in real-provider mode
  (`FAKE_LLM_CLIENTS=0`, `LLM=anthropic`, `ANTHROPIC_MODEL=claude-sonnet-5-5`, pricing catalog as in
  `docs/handoff-svg-quality-20261103.md`, `SVG_USER_TEMPLATES_ENABLED=true`, `DISABLE_AUTH=true` after the admin
  bootstrap), each with its own database, app-data dir and port; a matrix is split by input across the two
  backends with a fixed input-to-backend mapping, and per-backend result files are merged before `decide`.
* The uv python-build-standalone `.venv` cannot `posix_spawn(setsid=True)`, so registration fails at the frame
  render on this VM; the backends ran from a venv built with the system Python 3.11
  (`UV_PROJECT_ENVIRONMENT=<dir> uv sync --frozen --python /usr/bin/python3.11`) and a copy of `run_backend.sh` with
  only its `PY=` line changed (`docs/svg-user-templates-linux-20261103.md` describes the same).
* Judge references for template A: original pages 4, 9, 19, 25, 45, 58, 66, 73 rendered with LibreOffice and a
  fontconfig alias Paperlogy -> Pretendard; score-5 anchors 9 and 66; score-1 anchor built with `judge.py
  low-anchor` from page 58. Sanity check: originals 4.0-5.0, plain page 1.4. Template B's judge does not separate
  its own pages (2.6-3.0) from a plain page (1.4): use count metrics only on B.
* Cost: a sized deck is about $1-1.5 of provider calls; judging about $0.1 per deck. Provider spend for the work of
  2026-11-05/06 was about $160.
