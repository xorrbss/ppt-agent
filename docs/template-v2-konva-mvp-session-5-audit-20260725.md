# Template V2 Konva Studio MVP — 5/6 session audit

Date: 2026-07-25

Branch baseline: `feat/pptx-template-studio @ c5b169535ce263ddbb8f692c9340ad85c350c622`

Upstream reference: `presenton/presenton main @ 57b194b234b42c8b28f8a507a30322de200e3e83`

This audit was recorded before the 5/6 implementation changes. Scores are readiness
scores out of 100 for the bounded Studio MVP, not claims of parity with the upstream
49K LOC editor.

## Pre-change scores

| Area | Score | Evidence already present | Defects and priority |
| --- | ---: | --- | --- |
| Command model, history, keyboard | 78/100 | Typed geometry/reorder/group commands; 40-snapshot bound; coalesced text transactions; selection is excluded from content history; Ctrl/Cmd+Z, Shift+Z, and Y variants; input and contenteditable guards. | **P0:** geometry/text no-ops can still allocate layouts and pollute history. **P1:** several mutations still bypass the command facade. **P1:** keyboard policy is embedded in the React component and lacks focused unit coverage, including Alt-modified chords and composed targets. |
| Autosave, API, error, conflict | 51/100 | `/api/v1` GET/PATCH only; additive `expected_revision`; 409 conflict is explicit; failed responses are not reported as success; save snapshots do not erase later edits; synchronous single-flight gate. | **P0:** no autosave debounce. **P0:** no unmount/navigation/page-hide flush boundary. **P0:** an edit made during an in-flight save is retained but not queued for a follow-up save. **P1:** lifecycle and transient-error behavior lack deterministic unit tests. |
| Multi-select, alignment, grouping, layers, serialization | 72/100 | Modifier selection is ordered/deduplicated and constrained to siblings; atomic multi-drag; front/back/forward/backward ordering; lock-aware group/ungroup; path remapping; unknown layout fields round-trip. | **P0:** no align or distribute commands/UI. **P1:** grouping only promises the current sibling/local-coordinate subset and must not imply arbitrary transformed-group parity. **P1:** interaction coverage does not yet exercise the new alignment/serialization boundary. |
| UX, accessibility, focus | 63/100 | Action buttons expose disabled states; save/error/conflict status is visible; pointer selection and modifier selection are supported; controls have labels/titles. | **P1:** shortcut names and platform behavior are not centralized. **P1:** save state is not announced as an autosave lifecycle. **P1:** navigation-loss protection and deterministic focus-safe keyboard behavior need coverage. |
| Strategy and regression isolation | 88/100 | Feature flag is strictly default-off; Studio has a separate route and does not replace authored/adaptive rendering; API stays on v1; export strategy is not rewritten; baseline compatibility tests preserve unknown fields. | **P1:** regression proof must cover the selected 13 Cypress component specs, Next lint/build, authored/adaptive/export suites, and offline compatibility. Full-Konva features must remain explicitly out of scope. |

Pre-change mean: **70.4/100**.

## Baseline verification

- `node --test --test-concurrency=1 lib/template-v2-upstream-compat.test.ts lib/template-v2-studio.test.ts lib/template-v2-studio-ui.test.ts`
  - 32 passed, 0 failed, 0 skipped.
- The CI-selected Cypress gate contains 13 component specs. The repository contains
  22 Cypress specs in total; this session will run the exact 13-spec gate plus the
  Studio interaction spec.

## Implementation order derived from the audit

1. Prevent no-op history entries and isolate/test keyboard command policy.
2. Add a debounced, single-flight autosave scheduler with queued follow-up and
   lifecycle flush semantics over the existing revision-aware `/api/v1` contract.
3. Add a lossless, sibling-scoped alignment/distribution command slice and expose
   only capabilities the current canvas/schema can preserve.
4. Verify feature-flag, authored/adaptive/export, Windows sync, and API boundaries;
   do not import the upstream editor wholesale.

## Explicitly excluded from this slice

- Cross-parent arbitrary selections, arbitrary nested transform matrices, snapping,
  guides, comments, collaboration presence, clipboard/media pipelines, animation,
  charts/tables authoring, and wholesale upstream editor modules.
- Automatic conversion of existing presentations to Template V2.
- API v2 or replacement of authored HTML, adaptive rendering, or authored-hybrid
  export.

## Post-change scores

| Area | Before | After | Evidence and remaining boundary |
| --- | ---: | ---: | --- |
| Command model, history, keyboard | 78 | **93** | Align/distribute now enter the typed command facade; geometry and text no-ops preserve object identity and do not enter the bounded 40-snapshot history. The extracted keyboard policy covers Ctrl/Cmd, both redo variants, repeat/Alt rejection, availability, and editable targets including composed paths. History remains snapshot-based rather than operation-delta-based. |
| Autosave, API, error, conflict | 51 | **91** | An 800 ms scheduler now owns immutable latest-snapshot debounce, single-flight persistence, queued edits, explicit flush/retry, error retention, conflict blocking, and pagehide/beforeunload/unmount flush. It uses only the existing `/api/v1` PATCH `expected_revision` contract and treats non-2xx/409 as failures. There is no ETag, cross-tab merge, durable offline queue, or automatic retry/backoff. |
| Multi-select, alignment, grouping, layers, serialization | 72 | **91** | Six alignment and two distribution operations work atomically on unlocked siblings, account for rotated element bounds, preserve first/last outer bounds for even-gap distribution, and reject malformed geometry. Existing reorder/group/ungroup path remapping was hardened so unknown `position`/`size` metadata survives and malformed group children fail closed. Arbitrary cross-parent transforms remain out of scope. |
| UX, accessibility, focus | 63 | **87** | Disabled states now reflect command availability instead of blocking all editing during a save; shortcut names are exposed through labels/titles; save success is a polite status and failures are alerts; conflicts require an explicit destructive reload; native text undo remains isolated. The MVP has no focus-roving command palette or spatial keyboard movement. |
| Strategy and regression isolation | 88 | **94** | The default-off Template V2 route and v1 API boundary remain separate from authored HTML, adaptive rendering, and authored-hybrid export. The exact CI-selected 13 Cypress specs, shared Node suites, lint, production build, FastAPI, compatibility/intake, and export sync gates were exercised. Capability-gated LibreOffice/Electron/G4 rendering remains CI-authoritative on this Windows workstation. |

Post-change mean: **91.2/100** (up **20.8** points).

## Selected upstream-compatible slice

- Selected: explicit typed commands, bounded undo/redo behavior, platform-aware
  history shortcuts, debounced single-flight persistence, conflict-safe revision
  sequencing, sibling multi-selection alignment/distribution, layer ordering, and
  lossless schema round-tripping.
- Retained because it was already at least equivalent: the local 40-snapshot
  history, coalesced text transactions, modifier multi-selection, atomic multi-drag,
  reorder/group/ungroup commands, session locks, and upstream envelope adapter.
- Excluded: the upstream FreeFormCanvas/sidebar surface, wholesale editor stores and
  modules, arbitrary transformed groups, snapping/guides, media/clipboard pipelines,
  comments/collaboration, animation, and AI authoring. No upstream commit was merged
  or cherry-picked.

## Final verification evidence

- Narrow Node command/history/autosave/keyboard/serialization suite:
  **50 passed, 0 failed**.
- Exact CI-selected Cypress component gate, including the expanded Studio
  interaction spec: **13 specs, 72 passed, 0 failed**. The Studio spec itself is
  **10/10**, covering native/global undo, alignment/distribution losslessness,
  queued revision sequencing, and lifecycle flush.
- Shared Next CI Node groups: root **3/3**; adaptive **19 checks**; export app
  **9 tests**; authored-hybrid **83 tests**; Template V2 **70 tests**.
- FastAPI: **736 passed, 3 skipped, 0 failed**.
- Presentation-export sync contract: **16/16**; runtime sync/check retained
  `presentation-export` **v0.4.2** and the OS-aware Windows converter.
- Static compatibility: **1/1**; offline intake: **9/9**.
- Next lint: **0 errors, 216 existing warnings**; TypeScript `--noEmit`: passed;
  production build: passed, including all **28/28** static pages.
- `npm run test:local` executed every available default gate above. Its aggregate
  exit is intentionally non-zero only because the workstation has Node
  **24.16.0** while parity requires Node **22.x**; the CI Node 22 run is the
  authoritative parity result. Local capability checks did not offer
  LibreOffice/pdftocairo/Docker, so PyInstaller, visual-fidelity, Postgres,
  Electron, and G4 gates were not claimed as local passes.

## Residual risks

- History stores whole JSON snapshots (bounded to 40), so very large layouts can
  still carry a memory cost.
- Session locks are intentionally local editor state and are not serialized.
- Navigation persistence uses `fetch(..., { keepalive: true })`; browser keepalive
  payload limits and abrupt process termination cannot be eliminated client-side.
- The integer revision CAS prevents silent overwrite but does not merge concurrent
  edits. A conflict keeps the local snapshot and requires explicit server reload.
- Groups support the current move/local-coordinate subset, not rotation/resize of
  arbitrary nested transform matrices.
- Full-Konva parity still excludes guides/snapping, clipboard, numeric inspector,
  media/chart/table authoring, collaboration, animation, and the upstream editor
  shell.
