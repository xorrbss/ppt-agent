# Template V2 Chat parity characterization (2026-07-31)

This is a behavior audit against upstream `ca7d183ee50f4602c73b274e7346a145195d78d4`, not a file-presence comparison.  It records the local contract before the Chat tool integration is declared complete.  A capability moves to **implemented** only with a local entry point and a focused test; an in-progress tool must remain **pending integration** in the compatibility registry.

| Capability | Local evidence | Classification | Required conclusion |
| --- | --- | --- | --- |
| Native UI persistence, canonicalization, and UI/HTML exclusion | `models/sql/slide.py`, `templates/v2/persistence.py`, `test_template_v2_advanced_parity_contracts.py` | Implemented | `slide.ui` is canonical JSON and a snapshot containing both UI and authored HTML is rejected. No hydration migration is needed because `SlideModel.ui` already exists. |
| Per-run text content/style editing | `TemplateV2ContentInspector.tsx`, `toolbar/TextToolbar.tsx` | Implemented (run scope) | Existing runs can be changed and styled without dropping unrelated element fields. |
| Bounded selection inside a text run | `services/chat/slide_ui_content.py`, `test_slide_ui_mutations.py` | Implemented (Chat mutation) | Explicit offsets are bounds-checked; affected runs are split while selection-external content/style and existing font fields are preserved. |
| Chart categories, series, axes, legend, labels | `templates/v2/models/elements.py`, `services/chat/slide_ui_content.py`, `test_slide_ui_mutations.py`, `test_template_v2_pptx_charts.py` | Implemented | Chart data/style mutations preserve axes, legend, labels, categories, and series through native validation/export. |
| Table data and cell-object preservation | `elements.py`, `template-v2-import.ts`, `test_template_v2_pptx_tables.py` | Implemented (rectangular grid with spans) | Imported cells retain runs, font, fill, alignment, span anchors, and covered-cell objects; every row must match the column count. |
| Table row/column add, delete, reorder | `services/chat/slide_ui_content.py`, `test_slide_ui_mutations.py` | Implemented | Chat operations preserve complete cell objects and enforce rectangular bounds. Structural operations require all existing merged regions to be split first, preventing implicit data loss. |
| Table merge/split | `elements.py`, `slide_ui_content.py`, Studio/general HTML renderers, native PPTX parser/compiler, import adapter, hydration and round-trip tests | Implemented | Anchors own `rowspan`/`colspan`; every covered physical cell retains content/style and points to the anchor with `covered_by`. Overlap, orphan, out-of-bounds, and malformed rectangles fail closed. Studio/HTML/PDF render only anchors, native PPTX writes editable merged DrawingML, and split restores every covered cell object. |
| Image source/prompt/fit semantics | `services/chat/template_v2_hydration.py`, `test_template_v2_hydration.py` | Implemented (hydration) | Hydration clones the layout; generated raster content uses `cover`, while SVG, icon, and clipped assets retain their fit and prompt/source metadata. Malformed UI and assignment failures fail closed. |
| Asset/package security and retention | `test_template_v2_pptx_upload_security.py`, PPTX retention migrations/tests | Implemented | Untrusted PPTX packages are preflighted and private source retention has explicit cleanup/audit coverage. |
| Outline/layout/block/source-document Chat context | `services/chat/outline_context.py`, `services/chat/tools.py`, `test_chat_outline_context.py` | Implemented | Existing public tool names and upstream-compatible aliases share owner, size, MIME, max-slide, and truncation guards. |
| Default-template startup synchronization | `templates/v2/policy.py`, compatibility contracts | Safe local translation | Default-off rollout intentionally avoids destructive startup imports; admin/import behavior must be explicit and idempotent. |
| Generic async task reads | `api/v1/async_tasks/router.py`, `services/async_task_read_service.py`, `test_async_task_read_facade.py`, PostgreSQL integration coverage | Implemented local adapter | Owner-scoped list/check/get routes map the existing durable presentation generation records with status, terminal-state, filter, order, and pagination semantics. The guarded upstream-shaped `async_tasks` schema remains data compatibility only; no second execution path or persistence table was added, and legacy status routes remain intact. |
| `chat_scope` migration | Presentation/conversation ownership plus `SlideModel.ui` | Safe local translation | No association migration is justified until a real multi-chat/template association needs persistence. |
| `font_uploads` migration | `api/v1/ppt/endpoints/fonts.py`, `templates/fonts_and_slides_preview.py` | Safe local translation | Map to the established validated font surface; do not create a duplicate table. |
| Runtime/package prerequisites | `api/lifespan.py`, `export_task_service.py`, `utils/slide_capture.py`, Electron Chromium runtime | Implemented where local | LOG_LEVEL/root logging, Chrome discovery, ARM64 exporter selection, and package preflight exist. Signing/notarization remains release-credential dependent and cannot be asserted from source alone. |

## Contract transition rule

The two upstream Chat hydration entries were moved to `ported` only after `test_template_v2_hydration.py::test_hydration_clones_layout_and_preserves_image_prompt_metadata` passed. The same module also locks malformed-UI rejection and rollback on assignment failure. Future registry changes still require focused local test evidence; this matrix is not substitute evidence.

## Focused characterization coverage

`servers/fastapi/tests/unit/test_template_v2_advanced_parity_contracts.py` locks three non-negotiable boundaries:

- chart axis/legend/label/category/series metadata survives strict native validation;
- table span fields fail closed rather than being silently discarded; and
- version snapshots reject simultaneous native UI and authored HTML.
