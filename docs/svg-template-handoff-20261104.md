# SVG user template work: handoff (2026-11-04)

Branch `claude/youthful-fermat-nbyq8i`. Decisions, designs and the earlier
measurements are in
`docs/svg-template-real-deck-registration-design-20261103.md` (sections 1-11);
this session's design notes and measurements are in
`docs/content-structure-policy-20261104.md`. This note says what is verified
and what to do next.

## State

| Area | State |
|---|---|
| Real-deck registration (the real customer proposal deck, 101 pages, 303 x 216 mm) | Works unmodified: representative pages, explicit font substitution, raster-preserved decoration, background layers, exact source canvas (`svgCanvas` in EMU), canvas lock |
| Generation quality on PostgreSQL | Verified live (see Measurements): gate 10/10 with a frame map, PPTX slide size equals the source EMU |
| Template material by reference (v2 selection) | Done; job rows 15 MB -> 13 KB (design doc section 10) |
| Per-page text slots ("요구사항 ID") | Done (`bcf3d9e99`) |
| Chapter tabs filled from generated chapters | Verified live on the real deck; `svg_template_chapter_missing` did not recur |
| Header title and path slots | Done (`98e41a0d9`, `7d80815e7`); checked on one real deck only |
| Content structure policy (tables, connectors) | Implemented, opt-in, **off by default** (`CONTENT_STRUCTURE_POLICY_SCOPE`) |
| Evaluation tool | `servers/fastapi/scripts/svg_template_eval/` (see its README) |

## What changed this session

* `98e41a0d9` header title and path slots: the template header band shows the
  page title and the "number | chapter > title" path in the source's
  face/size/colour. A long title shrinks to 60 % and then gets an ellipsis
  instead of failing the page. A picture wholly below the header band counts
  as body decoration.
* `7d80815e7` a header path group is dynamic only where the deck writes at
  least two different texts at its place.
* `aaede5be8` opt-in content structure policy `utils/content_structure_policy.py`
  (tables and connectors). `CONTENT_STRUCTURE_POLICY_SCOPE` is an internal
  knob, off by default; `9bd4f35ed` lists it in
  `servers/fastapi/tests/support/internal_env_names.py`.
* `b96ffb8b2` fill push-down (`push_down_container_fill` in
  `services/svg_text_defaults.py`).
* `4b4852323` the flow review keeps the rest of the review when one
  "intentional repetition" claim has no evidence.
* `694afc673` evaluation tool `servers/fastapi/scripts/svg_template_eval/`.
* `cbcc33716` comment fixes; `51f962d24` measurement notes.

## Measurements

Details and tables: `docs/content-structure-policy-20261104.md`.

* Chapter tab live test (PostgreSQL 16, real deck, `template-redesign`,
  `svgCanvas {widthEmu: 10907713, heightEmu: 7775575}`):
  * Without `svgTemplateFrameMap`: every body page used frame-2 (a frame with
    chapter_title only); tabs showed the right chapter name on all body
    pages; gate 9 passed / 1 flagged.
  * With a frame map to the chapter frames: gate 10/10, chapter number and
    name correct on all 9 body pages, PPTX slide size equal to the source EMU.
  * Without a frame map all body pages land on frame-2; whether that default
    is intended is open.
* Template analysis took 111 s and 253 s (after font substitution) on
  PostgreSQL in this session, later runs 128-152 s and 273-404 s. The old
  note's 9-11 minutes was not reproduced.
* Fill push-down removed the most common first-draft failure
  (`svg_contract_explicit_text_attributes_required`: 12 of the 30 pages of the
  three `text-heavy` decks made before it): 0 attribute failures in every run
  after, fallback pages 3-7 per deck before vs 0-2 after.
* Content structure policy ON: 10 of 10 pages with an outline table fell back
  (two runs), so it stays off. Outline-only: the writing policy's "avoid pipe
  separators" blocked Markdown tables (0 tables); a pipe exception gave 2-3.
* Evaluation baseline (tool, fictional structured input
  `scripts/svg_template_eval/inputs/structured_proposal_ko.txt`, fresh
  database, policy off, `verbosity: text-heavy`, 3 runs): gate 9/1 in all 3,
  fallback pages 0, first-draft success 9, 9, 10 of 10, numbers kept 100 %,
  words kept 73-76 %, polygons (arrowheads/chevrons) 0, native tables 0,
  charts 0, about 420 s per deck.
* Gap to the original (the real deck's 97 body pages vs generated decks):
  flow elements (arrow or chevron shapes, arrowhead lines) on 58 pages (60 %)
  vs none; native tables on 46 pages (47 %) vs none; charts on 11 pages
  (11 %) vs none.

## Open issues

1. A published template's stored files can disappear from the local artifact
   store: all 9 adaptation artifacts of the newest template had retention
   bindings, yet 3 files were gone. Byte-identical material from
   re-registering the same deck shares one content-addressed object; deleting
   an older sibling's file is suspected, not confirmed. Possibly a production
   bug.
2. A broad pytest run killed the local PostgreSQL and backend processes
   several times.
3. The flow review fix (`4b4852323`) is unit-tested only; the rejection is
   rare.
4. Never checked in PowerPoint or through the UI. Header slot detection was
   checked on one real deck only.
5. Templates published before `98e41a0d9` must be registered again to get
   header slots.
6. An over-wide dynamic title used to fail the page; now it shrinks and is
   cut. Chapter tab fields still fail.
7. Without a frame map every body page uses frame-2 (see Measurements).

## Next, in order

1. Root-cause the template file loss (open issue 1; possible production bug).
2. Have the user judge the latest deck in PowerPoint against the original.
3. Split the policy into separate table and connector switches and measure
   connectors alone with the structured input (N >= 3 with the tool).
4. Fix native table authoring in template mode
   (`native_requested_table_missing` and others).
5. Pass the template's component styles (numbered chips, table header style,
   callouts, KPI cards) as style guidance, never as fixed layouts.
6. Then the scattered small first-draft failures: body outside region (5 of
   30 pages), required text missing (3 of 30), symbol policy (2), font
   glyphs (1).

## How to run a live test

* PostgreSQL 16: `initdb` and `pg_ctl start` as user `postgres` in a
  directory that user can reach (e.g. `/var/lib/postgresql/<name>`), port
  5433, then `create database`. Use a fresh database and app-data directory
  per measurement batch (see the tool README).
* Backend: `servers/fastapi/scripts/fake_llm_e2e/run_backend.sh <app-dir>`
  with `FAKE_LLM_CLIENTS=0`, a real provider key and pricing catalog (see
  that README), `SVG_USER_TEMPLATES_ENABLED=true`,
  `FAKE_LLM_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5433/<db>`.
  The backend venv's Python accepted `posix_spawn(setsid=True)` this session,
  so the earlier need for a separate system-Python venv was not reproduced.
* `DISABLE_AUTH=true` needs a bootstrap administrator and also blocks
  `/api/v1/auth/setup`: start once without it, create the admin, then restart
  with it.
* Do not run a broad pytest while the live stack is up (open issue 2).
* API flow: upload document -> reference snapshot -> `POST
  /api/v1/ppt/svg-templates/v1/register` (`framed`) -> poll operation ->
  `PATCH .../draft` with `fontSubstitutions` from `unavailable_fonts` ->
  `POST .../publish` -> `POST /api/v1/ppt/presentation/generate/async` with
  `svgTemplatePin`, `svgTemplateMode: template-redesign`, `svgCanvas` (EMU),
  optionally `svgTemplateFrameMap`, `export_as: pptx` -> download the export
  artifact from the workspace timeline.
* To judge a generation change over several runs use
  `servers/fastapi/scripts/svg_template_eval/` (README there).
* LibreOffice renders of exported PPTX need the deck's font installed
  (fontconfig) to compare text; LibreOffice ignores embedded fonts.

## Constraints kept

The real customer PPTX and its outputs stay out of the repository; API keys
are never printed or committed; work stays on this branch.
