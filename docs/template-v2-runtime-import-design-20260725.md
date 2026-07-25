# Template V2 import via the bundled export runtime — design

Date: 2026-07-25 · Upstream reference: `presenton/presenton main @ 57b194b2`

## Why

This fork's PPTX import uses a hand-written deterministic OOXML parser
(`templates/v2/pptx/ooxml_parser.py`). Upstream feeds Template V2 from the bundled
`presentation-export` runtime instead. Measured on the same 5-slide fixture, the runtime
extracts strictly more:

| fixture slide | bundled runtime | this fork's parser |
|---|---|---|
| styled text | `text` ×2, **4 runs with size/family/color/bold/italic** | `text` ×2, runs flattened, **all styling dropped** |
| picture + caption | **`image`** + `text` | **`unsupported`** (`unsupported_ooxml:pic`) + `text` |
| table | `table` | `table` |
| chart | `chart` | `chart` |
| rounded shape + text | **`vector`** (polygon, fill, `corner_radii`) + `text` | `text` only — **shape lost** |

The runtime also resolves theme-inherited colours (a placeholder subtitle came back
`#BFBFBF`). Closing this gap by extending the local parser means re-implementing run
properties, media extraction, and preset-geometry conversion — work the pinned runtime
already does.

## What is already true (verified on Windows, 2026-07-25)

- **The runtime works on Windows.** `presentation-export/py/convert-win32-x64.exe` ran
  `pptx-to-json` (exit 0) and `json-to-image` (exit 0, faithful 1280×720 render).
  Upstream's loader is Linux-only (`run-bundled-presentation-export.ts:58-67`); this fork
  already replaced it with generic `convert-<platform>-<arch>[.exe]` resolution (`:55-63`),
  and `scripts/sync-presentation-export.cjs` already downloads the per-platform archive.
- **`ExportTaskService` already has the plumbing.** `_resolve_converter_path`,
  `_build_node_env` (which already sets `ASSETS_BASE_URL`, `BUILT_PYTHON_MODULE_PATH`,
  `APP_DATA_DIRECTORY`, `TEMP_DIRECTORY`, and auto-detects Chrome), `_run_task`,
  `_resolve_output_path`, and a working sibling method `convert_pptx_to_html`.
- Fork and upstream pin the same runtime, `v0.4.2`. No version skew.

## Measured constraints the wiring must respect

1. **`session_id` is required** on the `pptx-to-json` payload. Upstream's own caller
   (`export_task_service.py:648`) omits it and fails validation against v0.4.2 — do not
   copy it verbatim.
2. **Image assets must be local filesystem paths when rendering.** `json-to-image` given an
   unreachable `http://` URL renders the region blank, **exit 0, no warning**. Measured:
   187 563 non-white pixels with a local path vs 2 957 with the URL.
3. **Exit code is not success.** Both known failure modes above exit 0.
4. **`justify_content` distribute values are silently ignored** by the runtime renderer.
   `flex-start`, `center`, `flex-end` render correctly; `space-between`, `space-around`,
   `space-evenly` render byte-identically to `flex-start`. Only relevant once layout
   elements exist — see Phase B.

## Scope split

Phase A introduces no AI and no layout elements, so constraint 4 does not apply to it.
A plain PPTX through `pptx-to-json` yields only `text`, `image`, `table`, `chart`,
`vector` — no `flex`/`grid`.

### Phase A — replace the analyzer's extraction source

**Backend.** Add `convert_pptx_to_json` to `ExportTaskService`, modelled directly on the
existing `convert_pptx_to_html` (`:407-432`):

```python
async def convert_pptx_to_json(self, pptx_path: str, *, session_id: str) -> PptxToJsonDocument:
    # payload: {"type": "pptx-to-json", "pptx_path": ..., "session_id": session_id}
```

`session_id` is supplied by the caller and must be the import row's `id`, so runtime output
is already partitioned per import and traceable.

**Ingestion.** `_analyze_import_source` (`template_v2_pptx_ingestion_service.py:251`) gains a
second extraction path selected by policy. The existing deterministic parser stays the
default; the runtime path is opt-in. Everything downstream — `CandidateAnalysis`,
`validate_cross_field_contract`, repeat suggestions, review boundary, confirm — is reused
unchanged, because both paths produce the same candidate shape.

**Flag.** `TEMPLATE_V2_PPTX_ANALYZER` = `deterministic` (default) | `runtime`, parsed in
`templates/v2/policy.py` with the same fail-closed discipline as the existing flags: any
unrecognised value is a configuration error, not a silent fallback.

**Element coverage.** The assembler currently emits 5 of 11 discriminators
(`assembler.py:8-21`). The runtime adds `image` and `vector`, so the assembler needs those
two cases. `TextList`, `Infographic`, `Flex`, `Grid` remain unreachable from import.

### Phase B — vision re-authoring (separate change, not designed here)

Adds the LLM component pass and the `previewSlide` self-critique loop, which is where
`flex`/`grid` and therefore constraint 4 appear. Deferred deliberately: Phase A already
closes the image/styling/shape gap and is verifiable offline.

## Open decisions

These are product/security calls, not implementation details. **They block Phase A.**

### D1 — where extracted images live

The runtime writes to `<APP_DATA_DIRECTORY>/pptx-to-json/<session_id>/images/` and emits
`data` as `ASSETS_BASE_URL + <relative path>`. `app_data` is served by a StaticFiles mount
(`api/main.py:69`), so **extracted customer imagery would be web-reachable by URL**. That
contradicts the existing posture for the source deck, which is deliberately stored outside
the mount (`template_v2_pptx_storage.py:75-93`).

| option | effect | cost |
|---|---|---|
| **A. Relocate after extraction** (recommended) | move `images/` into the existing private root, keep filesystem paths for rendering, serve to the browser through a new owner-scoped endpoint | +1 endpoint; Studio/export must resolve asset URLs through it |
| B. Leave in `app_data` | zero work | customer imagery publicly reachable if the path is known |
| C. Leave in `app_data`, unguessable session dir + TTL | small | still public; security by obscurity |

Rendering never needs a URL — `json-to-image` runs server-side and takes filesystem paths,
so option A costs nothing on the render path.

### D2 — URL ↔ path translation

Whichever D1 option is chosen, `pptx-to-json` emits URLs and `json-to-image` needs paths.
Upstream solves this with `_localize_json_image_assets`. This fork needs the equivalent, and
it should live next to the storage helpers that already own the private-root layout
(`template_v2_pptx_storage.py`) rather than inside the ingestion service.

### D3 — how render success is validated

Since exit 0 is meaningless, a render check needs a content assertion. Cheapest sufficient
rule: reject a render whose non-white pixel ratio is below a threshold when the source
layout contains at least one non-decorative element. Only needed once Phase B renders.

## Retention

Runtime output is derived data keyed by import id, so it follows the existing retention
rules rather than inventing new ones: cleaned up for `review_required`, `failed`, and
`cancelled`; retained for `confirmed` (the deliberate audit decision recorded in
`services/template_v2_pptx_retention_service.py:25-28`). `SOURCE_CLEANUP_STATES` already
expresses this; the extracted-asset directory must be removed by the same path that removes
the source file.

## Verification plan

- Unit: `convert_pptx_to_json` payload shape including `session_id`; analyzer selection and
  fail-closed parsing of `TEMPLATE_V2_PPTX_ANALYZER`; assembler cases for `image`/`vector`.
- Integration: import the 5-slide fixture through the runtime path and assert the three
  gaps close — an `image` element exists, text carries per-run `bold`/`italic`/`color`, and
  the rounded rectangle survives as `vector` with `corner_radii`.
- Contract: both analyzer paths satisfy `validate_cross_field_contract`, so the review and
  confirm boundaries are unchanged.
- Gate: `node scripts/verify-upstream-compatibility.mjs` stays at its pinned check count.

## Non-goals

Changing the default analyzer, generation emitting Template V2, upstream's editor, the
Studio, and any `/api/v2` surface. The rollout flag and allowlist keep their current
semantics.
