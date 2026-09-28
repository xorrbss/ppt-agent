# Refactoring audit — 2026-09-29

Branch `serve/main-20260902`, working tree as of 2026-09-29 (HEAD `692c13036` plus the
uncommitted canvas-expansion work). Read-only; no source file was changed. Six parallel
auditors with disjoint scopes (authored services, other services, utils/api/models, Next.js,
scripts/CI/config, prior-audit re-verification). Every top finding below was spot-checked by
the coordinating session.

Predecessor: `docs/dead-and-duplicate-source-audit-20260912.md` (538 commits ago). Of its
~120 concrete items, ~20 are gone — almost all as a side effect of the template_v2 excision and
the export-task refactor, not targeted cleanup — and ~100 remain exactly as documented. No
STILL-DEAD item gained a production caller, so every deletion it recommended is still safe.

## 1. Size baseline

| Metric | Value |
|---|---|
| FastAPI non-test, non-vendor, non-migration Python | 410,626 lines |
| FastAPI test Python | 228,750 lines |
| Next.js non-test TS/TSX | 122,447 lines |
| Python files > 500 lines (policy limit) | 138 (60 of them > 1,000) |
| TS/TSX files > 500 lines | 41 |
| `scripts/file-size-ratchet-baseline.json` frozen exceptions | 197 |
| Ruff `per-file-ignores` entries | 273 (17 point at deleted files) |
| `services/` flat namespace | 440 entries; prefixes `authored_` 170, `workspace_` 60, `svg_` 32, `provider_` 30, `public_` 29, `slidespec_` 28 |
| Direct `os.getenv`/`os.environ` outside `utils/get_env*.py` (services, utils, api, models) | 375 sites in 137 files |
| Env names read in code but absent from `constants/settings_registry.json` | ~170 |

## 2. Scores (0 = worst, 10 = clean)

| Area | Duplication | Dead code | Hardcoding | Architecture |
|---|---|---|---|---|
| `services/authored_*`, `authored_vnext/` (302 files, ~115k lines) | 2 | 6 | 4 | 2 |
| other `services/` (557 files, ~156k lines) | 4 | 6 | 5 | 3 |
| `utils/`, `api/`, `models/`, `enums/`, `constants/` (~506 files) | 4 | 6 | 4 | 3 |
| Next.js (655 files, ~58k lines) | 3 | 4 | 4 | 6 |
| scripts / CI / config / deps | unused scripts 7 · deps 7 · config drift 4 · CI 7 | | | |
| **Weighted overall** | **3** | **5.5** | **4** | **3** |

Composite: **4 / 10**. Dead code is the least severe axis (the two big removals did land);
duplication and architecture are the worst, and both are driven by one mechanism: the 500-line
ratchet was satisfied by mechanical splits (globals injection, partially-initialised module
aliasing, copy-paste of small helpers) rather than by extracting real modules.

## 3. Confirmed bug found on the way

`services/generation_core/style/build.py:41` — the Korean-language test reads
`if "korea" in s or "?쒓뎅" in s or s.startswith("ko")`. The middle literal is a CP949→UTF-8
mojibake of `"한국"` (committed in `ac0091982`); it can never match real input. Replace with
`"한국"` and add a test. Effort S.

## 4. Top findings, ranked by impact

### Architecture

1. **`bind_runtime` globals injection to satisfy the ratchet (L).** 10 split modules copy the
   caller's `vars()` into their own `globals()` on every call (`authored_generation_repair.py:394-399`
   and siblings); `authored_presentation_generation.py` keeps 22 `_call_extracted` forwarders.
   This defeats static analysis, makes import order load-bearing, and is the origin of 17 lazy
   import cycles. Same pattern by module aliasing: `authored_quality_review_service` ↔ its 8
   `_prepare/_clean/_revise/…` split modules import each other at module top.
2. **`utils/` imports `services/` 90 times in 41 modules (L).** `utils/llm_calls/author_*` are
   orchestration, not utilities (`author_deck.py:32-77`, `author_slide.py:69-89`); even
   `utils/get_env.py:7,51` imports `services.provider_settings_values`. 29 of the 90 are lazy
   function-body imports papering over cycles. Fix: move `author_*` under
   `services/generation_core/`, invert `get_env` ↔ provider settings by passing settings in.
3. **Services import the API layer: 20 sites in 15 modules (M).** `api.v2.contracts` types
   (6 modules), `api.v1.auth.internal`, `content_brief_policy` from an endpoint module, and
   the private endpoint symbol `_request_from_durable_payload` (`workspace_timeline_projection.py:214`).
   Fix: move `api/v2/contracts.py` types to `models/`, the two policies into services.
4. **God modules (L).** `workspace_operation_runner.py` 4,638 lines (own env parsers, UTC
   helper, lease fence, main loop); `source_native_generation.py` 4,168;
   `slidespec_authored_executor.py` 3,489; `authored_presentation_generation.py` 3,413 with
   fan-out to 64 modules; `presentation_generate.py` endpoint 2,359; `create_presentation`
   endpoint function is 391 lines (`presentation_crud.py:362-752`), `stream_outlines` 297.
5. **Flat namespaces instead of packages (L).** `workspace_` 60 files / 26.5k lines with error
   definitions split across 7 modules; `public_` 29; `slidespec_` 28 (+22 in
   `slidespec_repair_context/`). Only `source_content/`, `generation_core/`,
   `trusted_executor/` are real packages.
6. **Parallel abstractions for one concern.** 7 lease/fence classes; 5 deadline-policy
   modules; 11 hand-rolled `start_*/stop_*` maintenance loops; 40 export-named modules with two
   attempt-identity services; 5 master-repository implementations
   (`generation/repository.py`, `generation/sql_repository.py`, `integration/sql_repository_composition.py`,
   `master/file_repository.py`, `master/repository.py` — the last has 0 production importers);
   three "release/readiness" generations under `authored_vnext/semantic/`; two quality-receipt
   generations (`authored_quality_receipt_v2.py` 2,546 + `_v3_*`); two `title_fit` modules.
7. **Owner-scope hook not trusted (S).** `services/database.py` installs the central
   `do_orm_execute` owner filter, yet 9 endpoint sites hand-roll `owner_id ==` predicates
   (`images.py:82,398`, `presentation_share.py:165`, `raster_reconstruction.py:97,110,187`,
   `presentation_generate.py:2318`, `design_packs.py:334`, `asset_gateway.py:582`) and 2 more in
   services. Delete them, or add a coverage test proving the hook is load-bearing.
8. **Next.js `app/api` forwarders bypass the shared proxy (M).** `app/api/v2/_proxy.ts` exists,
   but 8 routes (`decompose-documents`, `jobs/[jobId]`, `user-config`, `export-presentation`,
   `outline-stream/[id]`, `rasterize-icon`, `internal/export-progress`) re-copy header
   forwarding and their own 502 envelopes with differing timeouts.

### Duplication (behavioural forks first — these produce different results for the same input)

| Family | Copies | Fork | Fix |
|---|---|---|---|
| Canonical JSON digest / `"sha256:"` prefix | 40+ defs in authored scope, 13 in other services, 9 in models/utils, 20 in Next.js; `"sha256:"` literal in 199 backend + 47 more files | `allow_nan`, `separators`, `ensure_ascii`, NFC, prefix present/absent differ (`source_unitization.py:107` vs `generation/montage.py:41`; `project_style.py:24` vs `svg_export_quality.py:21`) | one `utils/hashing.py` (`canonical_json_bytes`, `sha256_prefixed`, `is_sha256_hex`); migrate only where bytes already match or version the digest — digests are persisted |
| positive-int env parser | 5 in utils, 9 in services, 4 in authored | 4 error contracts: warn+clamp, raise `ValueError`, raise `SourceLockedError`, silent `max(1,…)` — the same malformed `WORKSPACE_*` value crashes boot in one module and is clamped in another | `get_env.env_int(name, default, minimum, on_invalid=)` + `env_bool`; route through `RuntimeSettings` so admin can change them |
| `_number` float coercion | 11 in services, 8 in authored | default `0.0` vs `math.nan`; `slidespec_authored_importer.py:233` lacks the bool guard (`True`→1.0); `native_scene.py:129` raises | `utils/coerce.py` (`finite_float`, `as_mapping`); migrate slidespec first since repair stages disagree |
| `_mapping` | 12 | — | same module |
| Glyph-width / wrap heuristic | 4 | 3 different tables (`body_grammar.py:161`, `generation/title_fit.py:214`, `master/title_fit.py:39`) — title fit and body fit disagree on the same string | one `text_measure.py` |
| `_camel` | 6 | `design_pack/models.py:15` keeps tail case, the other 5 use `capitalize()` (`fooBAR`→`fooBar`) | one helper |
| `_hex_color`, luminance/contrast | 3 + 4 copies while `utils/color_contrast.py` has 0 importers | error types differ | import the existing util |
| `_linked_generation_job_id` | `workspace_operation_runner.py:810` vs `workspace_timeline_projection.py:54` | projection covers only `presentation.generate`; runner also `template.apply`/`design.apply` — timeline never links job ids for those | projection imports the runner's kind set |
| `normalize_font_family_name` | `pptx_slides.py:115` vs `templates/pptx_font_utils.py:141` | camel-case split differs | keep one |
| `_windows_hidden_subprocess_kwargs` | `document_conversion_service.py:67` vs `liteparse_service.py:534` | one sets `STARTUPINFO`, one only the flag | one helper |
| PBKDF2 / `_base64url_decode` | `api/v1/auth/config.py:18,48` vs `utils/simple_auth.py:36,88` | same algorithm, security-sensitive | import from `simple_auth` |
| FastAPI base-URL resolver (Next.js) | 7 | `rasterize-icon/route.ts:37` drops the `NEXT_PUBLIC_FAST_API` fallback; dead `NODE_ENV` branches in `proxy.ts:16`, `serverAuth.ts:30` | import `getFastApiBaseUrl` from `lib/fastapi-internal.ts` |
| `isRecord` / `record` (Next.js) | 36 | throw / `{}` / `null` / boolean guard | `lib/guards.ts` |
| XML escape (Next.js) | 7 (+4 unescape) | `pptx-assembler-backplate.ts:141` drops `>` and `'`; `pptx-archive.ts:160` encodes CR/LF | one `xml.ts` |
| `layout_group === "authored" \|\| "svg"` | helper exists (`isAuthoredPresentation.ts`) but inlined 6× | `PresentationContentRender.tsx:68` omits `"svg"` | use the helper |

Non-forking families worth consolidating by volume: `_resolve_prompt_language` ×4
(byte-identical, `utils/llm_calls`), `_as_utc`/`_utc` ×9 while `utils/datetime_utils.as_utc`
exists, `sha256_file` ×4, `_safe_failure` ×2, 9 `HTMLParser` scaffolds, `_atomic_write_bytes`
copied inside an `except ImportError`, Mem0 client wrapper ×2, provider settings UI twins
(`PresentonMode.tsx` 1,263 lines ↔ `settings/ImageProvider.tsx`; `checkCurrentAuthStatus`
byte-identical; `codex/auth/status` fetched from 4 files), SSE retry scheduler ×2,
`fetchStatus` ×2, theme CSS-vars ×2, timeout constants (`PRESENTATION_FETCH_TIMEOUT_MS` ×3,
`POLL_INTERVAL_MS` ×4).

### Dead code (all verified: zero non-test references)

| Item | Lines |
|---|---|
| `source_content` restyle cluster: `pipeline.py::generate_source_content_restyle_presentation`, `build_source_content_staging`, `materializer.py`, `publication.py`, `export_readback.py` | ~2,200 |
| `authored_vnext/generation/retained_reference.py::_layout_library`, `_source_layout_library` | 501 |
| Next.js Template V2 studio i18n: `studio` (754 keys), `imageEditor` 66, `iconEditor` 23, `overlapMessages` 7, `editableAssets` 4 in both catalogs | 854 keys |
| `services/trusted_executor/chrome_runtime.py` + `launcher.py` (reachable only via the unused package facade) | 813 |
| Next.js `/community` page cluster (0 inbound links, `page.tsx` hard-redirects) | 782 |
| `services/pptx_import_conversions.py` → `export_task_runtime.py` → `export_task_contracts.py` (successor of the deleted `export_task_service`, never wired) | 702 |
| `utils/llm_calls/generate_slide_content.py`, `generate_presentation_structure.py`, `utils/layout_capacity.py`, `utils/authored_fit_verify.py`, `utils/ppt_utils.py`, `utils/internal_http.py` | ~1,165 |
| Dead functions ≥15 lines: 19 in authored scope (1,075), 32 in other services (1,003), 21 in utils/api/models (~640) — full lists in the per-scope reports (see §7) | ~2,700 |
| `semantic/editable_hybrid_libreoffice_readback.py`, `deck_ir/hybrid_admission.py`, `master/repository.py::InMemory…` (test doubles in prod) | ~350 |
| Next.js: 9 dead files (`has-required-key/route.ts`, `DashboardNav.tsx`, `useFontLoader.tsx`, `ThemeSelector.tsx`, `LanguageSelector.tsx`, `TemplateCatalog.tsx`, `ThemeGallery.tsx`, `ui/radio-group.tsx`, `ui/scroll-area.tsx`); 9 Redux reducers (`updateSlideContent`, `updateSlideChart`, … mutate the retired `slide.content` shape); 3 public assets | ~600 |
| 15 npm runtime deps with 0 imports: `@tiptap/*` ×4, `tiptap-markdown`, `recharts`, `mermaid`, `zod`, `chart.js`, `html2canvas`, `react-simple-code-editor`, `@paciolan/remote-component`, `@radix-ui/react-toast`, `@radix-ui/react-avatar`, `@babel/*` ×3 (CLAUDE.md still says "rich text via TipTap, charts via Recharts, diagrams via Mermaid") | — |
| Scripts: `scripts/managed-processes.mjs`, `scripts/watch-local-stack.ps1`, `servers/fastapi/scripts/{provider_usage_benchmark,run_public_v2_rc_migrations,seed_authored_vnext_master_catalog,sync_existing_provider_settings}.py`, `authored_deck_richness*` 6-file cluster | — |
| 10 `scripts/*.test.mjs` never run by any CI gate (false coverage): `public-service-*` ×7, `authored-release-readiness-operations`, `authored-vnext-controlled-load-retry-audit`, `verify-authored-release-required-checks` | — |
| Five `register_*_operation_handler` functions never called (`workspace_operation_catalog.py` registers by string) | — |
| Python deps: `nltk` declared, no import found | — |

Total deletable without design decisions: roughly **10k lines of source + 854 i18n keys + 15 deps**.

### Hardcoding

1. **Canvas `1280×720` literal**: 52 occurrences across `services/` + `utils/` and 8 sites in
   Next.js, while `authored_canvas.py:24-26` and `lib/authored-hybrid/schema.ts:6-7` already
   name them. With the uncommitted canvas-expansion work in flight, each is a place a non-16:9
   deck silently takes the wrong branch. Fix first: `authored_canvas.is_default_landscape()` /
   `canvas_or_default()`, then grep-delete.
2. **Env knobs bypass the registry**: 375 direct reads, ~170 names unregistered
   (`AUTHORED_VNEXT_TENANT_ID` alone read at 43 sites). CLAUDE.md requires registration first;
   these knobs are invisible to the admin endpoint. Fix: accessors in `get_env.py` per cluster
   (`get_vnext_tenant_id()` removes 43 sites), register or mark internal.
3. **Model/effort tables in three Python files** (`utils/generation_model.py:19-33`,
   `author_slide_effort.py:44-50`, `llm_config.py:149`) while `provider_registry.json` carries
   no chat model IDs; gpt-image size map duplicated in `image_generation_service.py:154` and
   `icon_pack/provider.py:55`. Fix: `models[]` block in the registry, generate the tables.
4. **Korean user-facing strings in backend logic**: ~40 in authored services
   (`authored_generation_progress_poller.py:48-120`, `authored_page_remake_service.py:405-729`),
   148 lines in 12 other service files, plus `slide.py:57`, `files.py:52`,
   `workspace_operation_kinds.py:87`. The frontend cannot localise them. Fix: message keys
   resolved by next-intl. (Korean inside prompts and linguistic heuristics is by design.)
5. **Next.js brand palette**: `#5141e5` / `#5146e5` / `#7a5af8` 161 hits in 40+ files,
   `#edeeef` 112 hits; no Tailwind brand token. Fix: `brand`/`surface` tokens.
6. **Timeouts buried in logic**: 19 literals in 9 utils/api files, 14 more service files
   (`timeout=60` ×4 for local-fit capture, `sleep(0.01)` retry loops).
7. **Hosts/ports**: `localhost:11434` ×3 in backend and in `PresentonMode.tsx`; `127.0.0.1:8000`
   ×7 Next.js server files; `127.0.0.1:5000` in `utils/api.ts:70,102`, `slide_capture.py:1515`.
8. **Flag defaults contradict docs**: `WorkspaceChatPanel.tsx:59-60` gates the SlideSpec canary
   UI with `!== "false"` (default on; CLAUDE.md says default-off);
   `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` is documented but never read.

### Config / CI / ledgers

- 17 ruff `per-file-ignores` entries name deleted files; 10 stale paths across
  `compatibility/upstream-compatibility.json`, `upstream-test-contracts.json`,
  `migration-translation-ledger.json` (all template_v2 leftovers); the `export_utils`
  byte-parity ledger entry is stale.
- `settings_registry.json` 182 keys vs `docker-compose.yml` ~120 vs `README.md` 23 rows.
- `public_operation_admission.py` ratchet exception is 3,021 while the file is 2,757 — never
  tightened.
- Alembic `82abdbc476a7` is a literal no-op duplicate of `f42ad4074449` (comment it; do not delete).
- CLAUDE.md drift: names a "template-v2 contract check" CI step that no longer exists; lists
  endpoint modules that do not exist (`chat.py`, `layouts.py`, `prompts.py`, `slide_to_html.py`,
  `structured_templates.py`, `template_v2_compat.py`); still describes `template-v2-studio`,
  `lib/template-v2-html/`, `test:template-v2-export-fidelity`, TipTap/Recharts/Mermaid.
- Writeless fields W1–W12 from the prior audit are all still writeless; `services/deck_ir/`
  (the only `layoutCapacity` producer) was deleted, so the T1 trigger flags are further from firing.

## 5. Recommended execution order (revised 2026-09-29 after the ratchet removal)

The 500-line file-size ratchet and its baseline were removed on 2026-09-29. File length is
therefore no longer a defect on its own; only cohesion, layering, cycles and forks are. That
reverses one earlier recommendation: the mechanical splits made to satisfy the ratchet should be
folded back into their parents, not split further into packages.

Each phase is independently mergeable. Every deletion needs
`node scripts/verify-upstream-byte-parity.mjs --write --index`, the matching ruff line, and
(for Next.js) `npm run check:i18n`.

1. **Bug + zero-risk deletions + replacement gate (S).** Fix the mojibake literal. Delete the
   dead modules/functions/files/reducers/i18n namespaces/deps/scripts in §4 that need no product
   decision. Remove the 17 stale ruff ignores and 10 stale ledger paths; fix CLAUDE.md drift.
   Add one cheap structural test in place of the size gate: forbid `utils/ -> services/` and
   `services/ -> api/` imports, and forbid the `globals()[name] =` injection pattern, so phases
   2 and 6 cannot regress.
2. **Fold the artificial splits back (M).** Re-inline the 10 `bind_runtime` split modules into
   their parents and delete the 22 `_call_extracted` forwarders; re-inline the 8
   `authored_quality_review_*` split modules that alias the partially-initialised service. This
   removes the globals injection and most of the 17 lazy import cycles in one mechanical change
   covered by existing tests. Wait for the session editing `authored_generation_repair.py` and
   `authored_presentation_generation.py` to land first.
3. **Product decisions (ask first):** delete or wire the `source_content` restyle cluster
   (~2,200 lines); delete or link `/community`; keep or drop `nltk`.
4. **Canvas literal consolidation (S, before the canvas-expansion work merges).**
5. **Behavioural forks (M).** Create `utils/hashing.py`, `utils/coerce.py`,
   `get_env.env_int/env_bool`, one `text_measure.py`, Next.js `lib/guards.ts`, `lib/xml.ts`,
   one `getFastApiBaseUrl`. For each forking family in §4, pin the chosen behaviour with a test,
   then delete the copies; digests only where persisted bytes already match.
6. **Knob hygiene (M).** Per-cluster accessors in `get_env.py`
   (`get_vnext_tenant_id()` alone removes 43 sites); register the ~170 names or mark internal;
   align compose/README from the registry generator.
7. **Layering (L).** Move `utils/llm_calls/author_*` under `services/generation_core/`; move
   `api/v2/contracts.py` types to `models/`; delete the 11 hand-rolled owner predicates after
   adding a hook coverage test; generalise `app/api/v2/_proxy.ts`. Extract the 391-line
   `create_presentation` only if the extracted service gains a second caller or a unit test.
8. **Parallel abstractions (L, optional).** Leases, deadline policies, maintenance loops,
   master repositories, readiness generations — only after phase 5 has pinned their forks.
   Packaging the flat `workspace_`/`public_`/`slidespec_` namespaces is dropped: no behavioural
   defect, high collision risk with concurrent sessions. The one exception is the
   `slidespec_repair_*` 5-module cycle, which is a real import defect.
9. **Korean backend strings -> message keys; brand tokens; timeouts to named constants (M).**

## 6. Scope caveats

- `servers/fastapi/dist/` (PyInstaller output, ignored) and `vendor/slide_master/` were
  excluded from all counts.
- `services/authored_canonical/` and `authored_icon_pack/` fell between two auditors' scope
  rules and were not audited.
- Test-file quality (228k lines) was not audited beyond "does it reference production code".
- Files listed as IN-EDIT by other sessions (`authored_composition_policy.py`,
  `authored_generation_repair.py`, `authored_presentation_generation.py`, `workspace_service.py`,
  `author_batch.py`, `author_deck.py`, `author_slide_design_system.py`, `author_slide_svg.py`,
  `lib/authored-hybrid/{browser-source,extractor,html-instrumentation}.ts`) are modified in the
  working tree; coordinate before touching them.

## 7. Per-scope detail

The six auditor reports (each ≤2,500 words with full file:line tables for every category) are
summarised above; the coordinating session holds them in full. Re-run the same six prompts to
regenerate if this document is used as the baseline for a follow-up audit.
