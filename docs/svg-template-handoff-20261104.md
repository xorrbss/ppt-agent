# SVG user template work: handoff (2026-11-04)

Branch `claude/youthful-fermat-nbyq8i`, last commit `943e32842`. Decisions,
designs and every measurement are in
`docs/svg-template-real-deck-registration-design-20261103.md` (sections 1-11);
this note says what is verified and what to do next.

## State

| Area | State |
|---|---|
| Real-deck registration (101-page proposal, 303 x 216 mm) | Works unmodified: representative pages, explicit Pretendard substitution, raster-preserved decoration, background layers, exact source canvas (`svgCanvas` in EMU), canvas lock |
| Generation quality on PostgreSQL | Two 10-page runs: quality gate 10/10, 0 fallback pages, PPTX at the source EMU size (design doc section 9) |
| Template material by reference (v2 selection) | Done; job rows 15 MB -> 13 KB (section 10) |
| Per-page text slots ("요구사항 ID") | Done (`bcf3d9e99`); verified offline on the real deck |
| Chapter tabs filled from generated chapters | Implemented (`878e036cc`, `943e32842`), unit tests pass. **Not yet verified live**: the first live run failed with `svg_template_chapter_missing` (preflight/publication checks lacked the chapter), fixed in `943e32842` but not re-run |

## Next, in order

1. **Re-run the chapter-tab live test** on PostgreSQL with the real deck
   (register -> substitute fonts -> publish -> 10-page `template-redesign`
   generation at `svgCanvas {widthEmu: 10907713, heightEmu: 7775575}` ->
   PPTX export). Check: every page's tab shows the generated chapter number
   and name, names fit the tab, quality gate, export slide size.
2. Template re-analysis took 9-11 minutes on PostgreSQL (vs about 3 on
   SQLite); find where the time goes.
3. SQLite only (local runs; production is PostgreSQL): a transaction holds
   the single write lock for over 30 s while pages author in parallel
   (`database is locked` on provider-call reconciliation). Not needed for
   the web service.

## How to run a live test

* PostgreSQL 16: `initdb` and `pg_ctl start` as user `postgres` in a
  directory that user can reach (e.g. `/var/lib/postgresql/<name>`), port
  5433, then `create database`.
* Backend: `servers/fastapi/scripts/fake_llm_e2e/run_backend.sh <app-dir>`
  with `FAKE_LLM_CLIENTS=0`, a real provider key and pricing catalog (see
  that README), `SVG_USER_TEMPLATES_ENABLED=true`,
  `FAKE_LLM_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5433/<db>`.
  The uv-managed Python lacks `POSIX_SPAWN_SETSID`; a system Python 3.11
  venv was used for the worker processes.
* API flow: upload document -> reference snapshot -> `POST
  /api/v1/ppt/svg-templates/v1/register` (`framed`) -> poll operation ->
  `PATCH .../draft` with `fontSubstitutions` from `unavailable_fonts` ->
  `POST .../publish` -> `POST /api/v1/ppt/presentation/generate/async` with
  `svgTemplatePin`, `svgTemplateMode: template-redesign`, `svgCanvas` (EMU),
  `export_as: pptx` -> download the export artifact from the workspace
  timeline.
* LibreOffice renders of exported PPTX need Pretendard installed (fontconfig)
  to compare text; LibreOffice ignores embedded fonts.

## Constraints kept

The real customer PPTX and its outputs stay out of the repository; API keys
are never printed or committed; work stays on this branch.
