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
| Evaluation tool | `servers/fastapi/scripts/svg_template_eval/` (see its README); paired vision judge `judge.py` (`f452fd6bf`) |

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
* Later the same day (stage 1-2 of the backlog, all measured N >= 3 with the
  tool and the vision judge):
  * `aacef5817` worker cache never evicts the artifact authority (open issue 1).
  * `f452fd6bf` paired vision judge `scripts/svg_template_eval/judge.py`.
  * `5ff5983e4` automatic frame choice without a frame map (open issue 7).
  * `4cba6fb39` `CONTENT_STRUCTURE_POLICY_PARTS` (tables, connectors) splits
    the policy; connectors alone: flow pages 0.3 -> 3 of 10, no invented
    relations, an arrowhead reversed on 1 flow page in 9, cover font
    fallbacks 0 -> 0.33 per deck; stays off.
  * `b8f7df582` native tables and charts survive template composition (every
    declaration used to be dropped as stale); with the policy on, table-only
    pages fall back 1 in 33 (was 10 of 10) and decks carry 2-8 native tables,
    but pages with a table and a chart fail (1 of 9 passed), fallbacks 1.3
    per deck and the judge 3.3 vs 3.5; stays off.
  * `893a721f1` template component style (`SVG_TEMPLATE_COMPONENT_STYLE`,
    off; needs the template registered again): judge tone 4.20 -> 4.52,
    overall 3.49 -> 3.64, fallbacks 0, first drafts accepted 9.3 -> 8.0 of 10.
    `fd5e8d05d` registration of a large deck peaked at 11 GB (raster
    preservation), now under 1 GB.
  * Details: `docs/content-structure-policy-20261104.md`,
    `docs/template-component-style-20261104.md`.
  * First-draft failures (stage 3, item 9): 52 of 250 stored first drafts classified; fixes for the
    cover's only-bold face, whitespace spacers, zero-size shapes, generated id collisions, body edges,
    checked figures and the text minimum in the prompt, and an arrowhead direction check with the
    connector policy. Live only 2 decks (provider credit ran out): `docs/svg-template-first-draft-20261104.md`.

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

1. Resolved (`aacef5817`): published template files vanished from the local
   artifact store because the fake-LLM harness hands workers
   `PUBLIC_WORKER_OBJECT_STORE_ROOT` (the real `public-artifacts` root) and
   the worker's transport cache evicted every file there older than its TTL
   (3600 s). The trusted transport never passes that variable, so the default
   deployment was not affected. A store damaged before the fix needs a fresh
   database and app-data directory, or the template registered again.
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
7. Without a frame map every body page used frame-2 (see Measurements). Now page 1 takes the cover frame and every other page the body frame showing most of title / chapter number / chapter name (then most dynamic fields, then largest body region, then source order), `automatic_frame_id` in `services/svg_templates/generation.py`; one frame for all body pages (the chapter frames are not varied per chapter), section frames are used only through a frame map.
8. `test_source_publish_rejects_missing_receipt_before_database_access` and
   `test_feature_lifecycle_refcount_expiry_and_gc_fail_closed` fail on
   `'GuardSession' object has no attribute 'info'` in
   `services/svg_templates/retention.py`; not caused by this session's changes.

## Next, in order

1. Have the user judge the latest deck in PowerPoint against the original.
2. Stage 3 of the backlog: first-draft failures (cover
   `svg_template_font_unavailable`, body outside region, arrowhead direction,
   table plus chart on one page), generality on other decks, UI, fallback
   page design, the cover as chapter one, outline page-count retry, earlier
   residuals.
3. Component style follow-ups before turning it on: two text levels instead
   of forcing every small size up, gapped chevrons, one variant per kind,
   then a same-session knob-off control.

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
* Vision judge baseline (contact sheet of eight original body pages, three
  judgements of one baseline deck): overall 3.46, 3.48, 3.54 (each axis moved
  at most 0.2); the other two baseline decks 3.40 and 3.52; held-out original
  pages 4.7; a plain bullet page 1.2.
* To judge a generation change over several runs use
  `servers/fastapi/scripts/svg_template_eval/` (README there).
* LibreOffice renders of exported PPTX need the deck's font installed
  (fontconfig) to compare text; LibreOffice ignores embedded fonts.

## Constraints kept

The real customer PPTX and its outputs stay out of the repository; API keys
are never printed or committed; work stays on this branch.

## 2026-11-04 Fallback pages follow the deck style (item 12)

What the server-built fallback page did before this change (`services/generation_core/drivers/svg/fallback.py`,
called from `drivers/svg/terminal.py`): in a plain svg deck it already used the style's background, palette,
primary and fonts (`_page_colors`, `fallback_page_svg`) and had a cover shape; in template mode it
(`_template_fallback` -> `template_fallback_body_svg`) already composed the body into the template frame, so the
header band, title/path fields, footer and fonts came from the frame. Three gaps remained, seen on stored
fallback pages of template decks:

- a markdown table in the outline was printed as raw pipe lines (`| --- | --- |`) in both modes;
- a template cover was a top-left bullet list, and its white text sat directly on the bright parts of the cover
  picture;
- there was no rule for text over a picture.

Now: a run of markdown table lines becomes a grid (header row in the accent colour, banded rows mixed from the
page and text colours, size chosen so every row fits, the rule row dropped; a lone `|` line stays text). A
template cover keeps the cover shape (large title, subtitle lines) inside the body region. When the frame
paints a picture under the body (`_picture_under_body`), the text sits on a translucent panel opposite in tone
to the text (0.62 black for light text, 0.72 white for dark text), so the worst-case pixel stays at 4.5:1 or
better. All text on a filled shape takes the preferred colour only while it reads at 4.5:1, else the stronger of
white and near-black (`_ink_on`). No model call; same inputs give the same bytes; the page-number furniture and
all receipts are unchanged. Tests: `tests/unit/test_svg_fallback_deck_style.py`.
Open: the page-number text drawn by `finalize_svg` uses the style primary and can be low-contrast in a deck whose
primary is close to its background; not changed here.
