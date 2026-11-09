# authored removal: execution (2026-11-09)

Owner decision 2026-11-09: remove the `authored` generation strategy. This
reverses, for authored, the 2026-11-07 freeze of non-SVG modes
(`docs/codex-brief-svg-freeze-quality-20261107.md`). The phase plan is
`docs/authored-removal-design-20261105.md` §5, with the changes below. Its
keep set (§4.2) and never-rename literals (§4.3) stand; line numbers in it
are stale and must be re-located. Decision record:
`.spec-memory/decisions/authored.generation.removal.1.json`.

## Changes to the 2026-11-05 design

| Item | 2026-11-05 design | Now |
|---|---|---|
| P0 production queries | Run, then pick D2 option A or C | Not run (no production access). D2 is **option C**: every authored deck gets a PPTX and a PDF snapshot before the driver is removed. |
| D3 sync `/generate` | Retire, or shim if an external caller exists | Keep the route. With authored gone every request is svg, which it already refuses with `422 svg_generation_requires_async_route`. No shim. |
| P1 rejection point | Pydantic validator on the request model | The admission boundary (each entry point and the staged-job replay). The authored code stays until P3, so internal authored tests keep running until then. |
| Order | P0 → P5 | P1 first. P2 and P3 follow after P1 lands. |

## Phase status

| Phase | Status |
|---|---|
| P0 decision record | Done (this document and the spec-memory record) |
| P1 stop new authored generation | In progress |
| P2 existing decks (option C) | Not started |
| P3 authored-only code | Not started |
| P4 database | Not started |
| P5 docs and guards | Not started (CLAUDE.md records the decision only) |

## What users lose

The element editor, whole-deck restyle and workspace chat exist only for
authored decks, so they disappear for new decks with P1 (svg decks never had
them). The svg editing roadmap in §7 of the design is the replacement and is
a separate track.
