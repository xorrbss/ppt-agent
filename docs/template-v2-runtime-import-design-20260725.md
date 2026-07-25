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

`session_id` is required by the converter's task schema — omitting it fails validation, which
is what upstream's own caller does. It does **not** determine where output lands: the runtime
allocates its own `<APP_DATA_DIRECTORY>/pptx-to-json/<random-uuid>/` per run, holding
`presentation.json` and an `images/` directory. Anything that needs to reclaim or relocate
that output must use the returned `output_dir`, never a path derived from the session id.
Measured 2026-07-25; an earlier draft of this document assumed the opposite.

**Ingestion.** `_analyze_import_source` (`template_v2_pptx_ingestion_service.py:251`) gains a
second extraction path selected by policy. The existing deterministic parser stays the
default; the runtime path is opt-in. Everything downstream — `CandidateAnalysis`,
`validate_cross_field_contract`, repeat suggestions, review boundary, confirm — is reused
unchanged, because both paths produce the same candidate shape.

**Flag.** `TEMPLATE_V2_PPTX_ANALYZER` = `deterministic` (default) | `runtime`, parsed in
`templates/v2/policy.py` with the same fail-closed discipline as the existing flags: any
unrecognised value is a configuration error, not a silent fallback.

**Element coverage.** Both analyzers reach 5 of the 11 discriminators, but not the same
five. The runtime adds `image` and `vector` and never emits `group` (PowerPoint groups are
flattened, freeform and gradient autoshapes are rasterised to `image`, bullet paragraphs
collapse into one text run). `TextList`, `Infographic`, `Flex`, `Grid` stay unreachable from
either import path. Conversion lives in `templates/v2/pptx/runtime_layouts.py`, beside the
assembler rather than inside it, because the assembler is `ShapeCandidate`-driven and also
owns repeat-group application and manifest building — none of which applies to a payload
that arrives already element-shaped.

### Two findings that change what Phase A delivers

**Runtime-imported templates have no fillable slots.** The converter marks every
`text`/`image`/`table`/`chart` as `decorative: true`, and `templates/v2/schema.py:182-185`
skips any element whose `decorative is not False`, so `get_template_schema` yields nothing
to fill. The deterministic assembler instead sets `decorative=False` explicitly
(`assembler.py:72,99,118`). This is not a defect in either: upstream defers that semantic
judgement to the vision pass, which is told to classify an element as content when removing
it would change meaning. Overriding it here would mark branding as editable content, so the
runtime analyzer must **not** force `decorative=False`.

The trade-off is therefore real and should be stated wherever the flag is documented:

| analyzer | fidelity | fillable slots |
|---|---|---|
| `deterministic` | no images, no run styling, no shapes | **yes** |
| `runtime` | images, per-run styling, vector shapes | **no**, until Phase B classifies |

**The two analyzers converge at the draft, not at the analysis.** The deterministic pipeline
stores `analysis_result.candidates` and replays it through the assembler at confirm time
(`_assemble_confirmed_candidate`, ingestion `:393-407`). The runtime path has no candidates.
`AssembledTemplateV2Draft` is `{raw_layouts, layouts, contents, manifest}`, all four of which
the runtime path can produce directly — `contents` being one empty dict per layout, which
`build_generated_slide` accepts precisely because the schema is empty. So confirm branches on
which analysis shape was stored rather than forcing a lossy back-conversion to candidates.
Repeat-block suggestions are candidate-derived and are simply absent for the runtime path.

Also structural: `_analyze_import_source` is sync and runs under `asyncio.to_thread`, while
`convert_pptx_to_json` is async. The branch therefore belongs at the async caller
(`run_template_v2_pptx_import`), not inside the threaded function.

### Phase B — vision re-authoring (separate change, not designed here)

Adds the LLM component pass and the `previewSlide` self-critique loop, which is where
`flex`/`grid` and therefore constraint 4 appear. Deferred deliberately: Phase A already
closes the image/styling/shape gap and is verifiable offline.

## Open decisions

These are product/security calls, not implementation details. **They block Phase A.**

### D1 — where extracted images live

The runtime writes to `<APP_DATA_DIRECTORY>/pptx-to-json/<session_id>/images/` and emits
`data` as `ASSETS_BASE_URL + <relative path>`, i.e. under the `/app_data` StaticFiles mount
(`api/main.py:69`).

That mount is **not** public. `SessionAuthMiddleware._requires_auth`
(`api/middlewares.py:58-68`) requires auth for everything under `/app_data/` except one
deliberate carve-out: `/app_data/images/` with an image extension, commented *"PPTX export
may re-fetch slide images without session/basic headers."* Runtime output does not match
that carve-out, so it is already behind auth. Confidentiality is therefore **not** the
deciding factor between the options below.

| option | effect | cost |
|---|---|---|
| **A. Relocate after extraction** (recommended) | move `images/` into the existing private root, keep filesystem paths for rendering, serve to the browser through an owner-scoped endpoint | +1 endpoint; Studio/export resolve asset URLs through it |
| B. Leave in `app_data` | zero work; already auth-gated | loses the import's `owner_scope`; needs a **second** retention path outside the private root |
| ~~C. Unguessable session dir~~ | dominated by B — the path is already authenticated, so obscurity adds nothing | — |
| ~~D. Write into `/app_data/images/`~~ | **reject**: the one public carve-out. It is the path of least resistance for the export renderer and would genuinely publish customer imagery | — |

The deciding argument is retention, not secrecy. Extracted assets in `app_data` sit outside
the tree `SOURCE_CLEANUP_STATES` already manages, so they need a second cleanup path — the
exact shape of the bug fixed this session, where `cancel` wrote a retention deadline that no
cleanup query ever read. Option A keeps assets in the same tree as the source file, so the
existing cleanup covers them.

Rendering never needs a URL — `json-to-image` runs server-side and takes filesystem paths,
so option A costs nothing on the render path.

**Blocking verification — done 2026-07-25, PASSED.** Both options leave assets on an
auth-required path, and the carve-out's comment implies the export renderer sometimes
re-fetches images without credentials. Since `json-to-image` renders an unreachable asset as
a blank region at exit 0, a failure here would have produced image-less decks with no error.
It matters because every image the fork currently serves lives on the *public* carve-out
(`utils/asset_directory_utils.py:74` maps generated images to `/app_data/images/`), so the
protected path had never been exercised by an export.

Measured: `/app_data/images/probe.png` → 200 unauthenticated; `/app_data/pptx-to-json/probe/images/probe.png`
→ **401** unauthenticated and **200** with the session cookie. A real PPTX export of a slide
carrying one image from each path embedded **both** — two distinct media parts, navy from the
protected path and green from the public one, each bound to its own `<p:pic>`. The seeded
export session cookie therefore does cover image subresources, and option A's owner-scoped
endpoint is safe on the export path.

Re-run this probe if the export cookie seeding in `utils/export_utils.py:43-56` changes.

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
