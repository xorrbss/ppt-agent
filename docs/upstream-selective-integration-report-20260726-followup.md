# Upstream selective-integration follow-up — 2026-07-26

## Scope and decision rule

- Upstream reviewed: `presenton/presenton` `upstream/main` at
  `57b194b234b42c8b28f8a507a30322de200e3e83`.
- Fork baseline used by the four read-only reviews:
  `9a8947ec9adf38c0172133f5d411e3a7f5310893`.
- Merge base: `c11f34ba24f4e7064234d632a832ed29dbc0a625`.
- The histories contain 850 upstream-only commits at the review point, but the
  count is not an integration plan. Decisions below are based on current file
  contents and product contracts.
- Wholesale merge, file replacement, and blind cherry-pick are prohibited.
  A recommended item means a separately tested, bounded manual port into the
  fork implementation. This review did not expand the current implementation
  slice.

## Executive decision

The fork is already materially ahead in Template V2 persistence, strict schema
and OOXML ingestion, PostgreSQL lease safety, malware lifecycle, release
fail-closed gates, and visual-fidelity testing. The remaining upstream value is
concentrated in small helpers:

1. bounded text-run selection transforms, contract-driven chart controls, and
   numeric image crop/focus controls;
2. EOT embedded-font offset validation and width-family normalization;
3. Uvicorn handler reuse, shared temp-file confinement, and legacy preview PPTX
   package preflight;
4. an explicit opt-in gate for presentation-export version overrides.

Each must be manually ported and tested in isolation. No upstream commit is
approved for direct cherry-pick.

## 1. Template V2 frontend and editing

### 반영 권장

| Candidate | Upstream evidence | Fork target | Expected effect | Conflict/regression risk and recommended method |
| --- | --- | --- | --- | --- |
| Bounded text-run selection transforms | `c513e612`, current path at `dabe968c`; `servers/nextjs/components/slide-editor/text/text-runs.ts` (`applyTextRunFontToSelection`, `replaceTextRunsContent`) | `servers/nextjs/lib/template-v2-studio-content.ts`; `servers/nextjs/app/template-v2-studio/[templateId]/TemplateV2ContentInspector.tsx` | Clamp/split/patch/adjacent-merge selected runs and distribute replacement text without replacing every run | Konva/Tiptap UI must not be ported. Upstream uses UTF-16 offsets and a key-order-sensitive `JSON.stringify` font comparison and has no focused helper tests. Manually implement pure helpers while preserving unknown run metadata and fail-safe target semantics; define surrogate/grapheme behavior in tests. |
| Contract-driven chart controls | `4bedd0e3` `ChartToolbar.tsx`; `47f1c805`, `c10b18d0`, `da946fee`, `91345910` `ChartEditorContent.tsx`; `d7d469a9` `chart-data.ts` | `servers/nextjs/lib/template-v2-render-plan.d.ts`; Template V2 content inspector | Safely expose chart type, compatible axes/title/grid, legend, and data-label fields already supported by the strict fork plan | Upstream omits bubble, exposes invalid radar axis controls, and defaults missing axes to true while the fork fails closed. Use fork chart-type and axis-compatible sets as the only source of truth; manually add small controls and parity tests. |
| Numeric asset crop/focus controls | `31a6af10` `ImageToolbar.tsx` (base upload/size in `b57cbe9c`) | Fork image schema/render plan and Template V2 inspector | Expose existing `focus_x/y`, `crop_scale`, fit, opacity, and radius contracts without new asset trust paths | Do not port the Konva crop overlay. Add bounded numeric/slider parsing and clamp tests only; retain browser/export crop fixtures. |

### 이미 동등/상위 구현

- Table cell fill/alignment from upstream `30cd2204`, `72fe882a`, and
  `eb804680` is superseded by fork commit `c4b1c798`: safe-color validation,
  immutable cell updates, and unknown metadata preservation.
- Upstream general-editor autosave (`08f863ce` `useAutoSave.tsx`,
  `b746a03a` `autoSaveDiff.ts`) has useful fingerprint/diff mechanics, but it
  lacks Template V2 revision CAS, journal recovery, conflict blocking/rebase,
  keepalive, and immutable queued snapshots already present in
  `useTemplateV2StudioPersistence.ts` and the fork autosave modules. Replacing
  the fork would be a correctness regression.

### 제품 구조 불일치로 제외

- Table merge/split: neither upstream nor the fork has `colspan`/`rowspan`
  semantics across schema, ingestion, and export. UI-only behavior would not
  round-trip.
- Upstream stock/generated-image discovery and deletion expands auth, quota,
  global asset deletion, and product scope.
- `009e66c4` `streamAssetMerge.ts` solves streaming-placeholder replay in the
  general editor; Template V2 Studio is not a streaming path.

### 추가 검증 필요

- Table row/column add, delete, and reorder from `4bedd0e3`/`5406e55e`: the
  upstream string-matrix conversion can move text while recreating styles and
  metadata and can silently promote a body row to header. A future fork
  implementation must transform whole cell objects, define header policy, and
  verify rectangularity and export parity.
- Chart category/series edits: upstream bounds categories to 24 and
  series/colors to 12, but focused tests are absent. A fork implementation must
  preserve category-series lengths, pie/donut single-series rules, and
  bubble/scatter/radar compatibility without silent truncation.
- Asset upload/replace from `9f646cc6`: the UI checks `image/*` and 5 MB, but
  the fork renderer rejects absolute HTTP(S) sources. Canonical app-relative
  sources, server magic/MIME/size validation, malware scanning, and retention
  must be agreed before exposing the control.

## 2. Backend ingestion, OOXML, SmartArt, corpus, and Vision

### 반영 권장

| Candidate | Upstream evidence | Fork target | Expected effect | Conflict/regression risk and recommended method |
| --- | --- | --- | --- | --- |
| Embedded EOT font offset and family-width normalization | `c4d426f76ef116e0e6812b5a44ce3ff5788671b9`; `servers/fastapi/templates/pptx_font_utils.py`, `templates/fonts_and_slides_preview.py`, `tests/test_pptx_font_utils.py` | Same fork modules, especially the embedded-font scan and family normalization helpers | Prefer validated EOT header offsets, fall back to the first valid SFNT signature, preserve `Condensed`/`Narrow` family tokens, and avoid duplicate style suffixes such as `Aileron Bold Bold` | Fork preview/font packaging and LibreOffice integration have diverged. Port only signature constants, offset/fallback logic, normalization semantics, and three focused tests; then run font units and Template V2 preview/export smoke. Never replace the full modules. |

### 이미 동등/상위 구현

- Upstream Template V2 model work (`91345910`, `691cf9ec`, `fa4f1cc4`,
  `456035af`) is covered or exceeded by the fork's strict
  `TemplateV2Model(extra="forbid")`, coordinate/count/grid/chart validation,
  bubble/vector support, and rejection of invalid pie/donut multi-series data.
  The fork pins upstream wire compatibility in
  `tests/fixtures/template_v2/upstream-elements-57b194b.json`.
- Upstream markdown-to-run changes (`39ce2dc5`, `daa68f93`, `db038c2b`) are not
  inbound OOXML parsing. The fork parser preserves `<a:r>`, `<a:fld>`, and
  `<a:br>` text-run styles and paragraph boundaries.
- Upstream contains no SmartArt/diagram relationship implementation. The fork
  allowlists inert diagram relationships, rejects active embedded content, and
  emits an explicit non-editable/source-preserved/manual-rebuild fallback.
- Upstream has no tracked Template V2 PPTX corpus. The fork uses deterministic,
  copyright-safe, in-memory OOXML ZIP builders plus golden analyzer fixtures.
- Upstream `utils/template_vision_errors.py` is content-identical. Fork commit
  `a93c8750` additionally supplies a strict, frozen, injectable Vision adapter
  contract with provider/credential/egress allowlists, cost/input/request
  bounds, timeout/cancellation, binding, and confidence review.

### 제품 구조 불일치로 제외

- There is no upstream ingestion service equivalent. The fork already splits
  analysis, dispatch, operations, storage, retention, workers, and
  observability; further facade extraction is a fork-local P2 refactor.
- `fd978f3e` duplicate mapping, `927dc83f` table normalization,
  `fa4f1cc4` chart application, and the markdown-run commits operate in
  upstream presentation/chat generation paths, not the strict importer.
- `services/documents_loader.py` and `liteparse_service.py` perform
  document-to-markdown OCR and do not satisfy the slide Vision trust contract.

### 추가 검증 필요

- Reproduce `fd978f3e`'s nested duplicate-name case in the fork native compiler
  before considering a minimal compiler/schema change.
- Expand deterministic exotic relationship builders for ActiveX, VBA,
  package, OLE, and SmartArt graphs with manifest/hash evidence; do not copy
  upstream or user decks.
- SmartArt structured parsing has no upstream basis. Retain the explicit
  fallback until relationship limits, security behavior, and round-trip
  evidence exist.
- The Vision adapter remains mock/contract only and is intentionally not wired
  to ingestion. Provider registry/DI, preview-raster input, credential
  resolution, and real calls remain blocked without keys and egress approval.

## 3. PostgreSQL operations, observability, and malware lifecycle

### 반영 권장

| Candidate | Upstream evidence | Fork target | Expected effect | Conflict/regression risk and recommended method |
| --- | --- | --- | --- | --- |
| Reuse a discovered Uvicorn terminal handler | `6413008bd51ba35feb8a09783f3f77dc63c53329`; `servers/fastapi/api/lifespan.py`, `tests/unit/test_app_logging.py` | Fork `api/lifespan.py`, `tests/unit/test_lifespan_logging.py` | Align application queue/health/malware logs with Uvicorn formatter and output when the root has no handler | Custom logger graphs can duplicate handlers. Port only `_configure_application_logging()` semantics and tests; preserve all fork worker, cleanup, database, and shutdown lifecycle code and add a packaged-server duplicate-log smoke. |
| Shared `TempFileService` confinement | `484b50e1709975e75193e821819b67963791c394`, `0f65ad6b05835b0daa1b410cd4de839113c91b5c`; `services/temp_file_service.py`, `tests/unit/test_temp_file_service_security.py` | Fork service plus existing guarded file endpoints | Close filename traversal, symlink escape, and out-of-base read/delete/write at the service layer | Existing fork callers may legitimately use app-data or explicit directories; Windows drive/UNC semantics differ. Inventory call sites, then manually port sanitization, realpath boundary, existing-path resolution, and guarded cleanup with fork-specific tests. Do not replace routers. |
| Legacy preview PPTX size/package preflight | `11ab0d7521a5a0e53b99cfecfd44ec9910a65a97`, `2238110c2a2dc8fc36d9af01e93b2d0b71d7d880`; `templates/fonts_and_slides_preview.py`, `tests/test_pptx_font_utils.py`, `nginx.conf` | Same legacy preview module; keep Template V2 storage unchanged | Reject oversized, corrupt, or structurally incomplete ZIP packages before expensive legacy font/preview extraction | This is not a ZIP-bomb or malware replacement. Port constants/helper/handler calls/tests only. Keep the fork's stronger Template V2 preflight, ClamAV lifecycle, and current global Nginx cap. |

### 이미 동등/상위 구현

- Upstream has no Template V2 multi-node lease implementation. The fork has
  atomic conditional claims, attempt-token fencing, expiry-bounded heartbeat
  and completion/failure, cleanup leases, and a four-node PostgreSQL
  competition/recovery test.
- Fork migrations add PostgreSQL advisory locking and Template V2 schema
  verification; lifespan shutdown preserves worker/cleanup teardown and engine
  disposal. Replacing these files would remove operational safety.
- Upstream has no equivalent queue aggregates, bounded health codes, rollback
  safety, alert thresholds, ClamAV required-mode promotion gate, or
  source/runtime retention cleanup.
- Local file and Nginx traversal defenses are already equivalent in the fork's
  Electron IPC, Next route, and static guard.

### 제품 구조 불일치로 제외

- Upstream generic async tasks (`a511a725`, `35ddab47`, later `840e165`) have
  only pending/completed/error states, background-process execution, and no
  owner predicate, lease, heartbeat, or recovery. Introducing them would
  create a second source of truth and weaken owner isolation.
- Full upstream replacements of `migrations.py`, `lifespan.py`, or `nginx.conf`
  do not know the fork migration lineage, workers, cleanup, private routes, and
  authentication boundaries.

### 추가 검증 필요

- `79bbe84c` adds a FastAPI CLI `--log-level`; design a small fork-specific
  option only after checking Electron, Docker, packaged argv, and environment
  precedence. Do not port the associated large `start.js` refactor.
- Local file-access helper centralization reduces duplication but adds no
  immediate protection beyond current guards. Treat it as a P2 refactor after
  complete app-data/temp/packaged-path tests.
- Timezone-aware timestamp/index ideas from generic async tasks may inform a
  future schema audit, but production lease semantics must first be exercised
  against managed PostgreSQL.

## 4. CI, release, preview fidelity, dependencies, and docs

### 반영 권장

| Candidate | Upstream evidence | Fork target | Expected effect | Conflict/regression risk and recommended method |
| --- | --- | --- | --- | --- |
| Explicit opt-in exporter version override | `2702ed921cc62f0c01753dfc2db232f67c0eb5a1`; `scripts/sync-presentation-export.cjs` | Fork sync script and its 16-test suite | Prevent an ambient `EXPORT_RUNTIME_VERSION` from silently replacing pinned presentation-export v0.4.2 | Some internal workflows may intentionally use the environment override. Manually port only flag parsing, help text, and focused tests so an override requires `--allow-version-override`; preserve fork checksum, Sharp ABI, cross-platform, atomic-marker, and read-only-check behavior. |

### 이미 동등/상위 구현

- Upstream `f7536ba2` test workflow is superseded by fork SHA-pinned actions,
  Node 24 artifact actions, exporter sync checks, Docker G4, Windows release
  audit/preflight, and packaged-server smoke.
- Upstream `d98827ab` R2 sync is weaker than the fork credential preflight,
  release/tag/checksum verification, reversible write probe, and post-upload
  `rclone check`.
- Upstream has no equivalent three-OS Template V2 fidelity workflow, real
  LibreOffice/Poppler comparison, four product fixtures, or threshold/failure
  diagnostics.
- Upstream dependency files would downgrade Next, electron-builder, and Sharp
  while leaving presentation-export v0.4.2's bundled Sharp 0.34.4 unchanged.

### 제품 구조 불일치로 제외

- `b6beb7f7` Docker release targets upstream-owned image namespaces, publishes a
  moving tag, uses mutable actions, and lacks signing/provenance/SBOM and
  credential preflight. It must not mutate an upstream registry from this fork.
- `2702ed92` `docs/multi-docker-build.md` hardcodes upstream namespaces and
  release tags without digest/signing/rollback guidance.
- The full upstream exporter sync script is Linux-only, uses external `unzip`,
  lacks checksum/Sharp native-load validation, and mutates during check-only.
- Upstream package and lockfiles are not integration artifacts.

### 추가 검증 필요

- Linux ARM64 exporter selection from `2702ed92` requires Docker
  `TARGETARCH`, pinned manifest checksums, buildx ARM64, and real converter
  smoke on a separate branch.
- Bundled Chromium/ImageMagick work (`285e603e`, `452650cf`, `79555887`) may
  improve offline/first-run export but needs license and package-size review,
  AppX ACL/resource tests, macOS signing/notarization, Linux AppImage tests, and
  collision analysis with the existing on-demand cache.
- macOS signing/notarization (`e0ee1216`, `10d0ba80`) is outside the current
  Windows/R2 gate and cannot be validated without Apple identities.
- Cypress 15.18.1 from `20dde8fa` is newer than fork 14.5.4, but must be
  upgraded alone and pass the full 105 component tests on all supported
  platforms before adoption.

## Final selective-integration disposition

- No upstream commit is approved for direct cherry-pick or wholesale merge.
- The eight bounded candidates above are backlog inputs, not part of this
  follow-up implementation.
- Highest-value next manual ports are:
  1. exporter override opt-in gate;
  2. EOT font offset/family normalization;
  3. shared temp-file confinement;
  4. bounded text-run selection helpers.
- Managed PostgreSQL canary, real Windows signing, R2 upload, paid Vision calls,
  and Apple signing remain external-authority work and were not attempted.
