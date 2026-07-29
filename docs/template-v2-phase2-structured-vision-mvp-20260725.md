# Template V2 Phase 2 PPTX structured/Vision MVP audit

Date: 2026-07-25

Implementation baseline:
`feat/pptx-template-studio @ 5d2cb683a17578cd5b191ef164c9cfcc33d33202`

Upstream reference:
`presenton/presenton main @ 57b194b234b42c8b28f8a507a30322de200e3e83`
(`0.9.2-beta`, `presentation-export` `v0.4.2`)

This is a bounded, provider-free MVP. It does not claim production OCR,
computer-vision inference, rendered-preview fidelity, or parity with the
upstream Konva editor.

## Baseline and pre-change assessment

The implementation worktree started at the required commit. It was clean except
for the preserved, user-owned untracked files
`servers/nextjs/pnpm-lock.yaml` and `servers/nextjs/pnpm-workspace.yaml`.
PR #70 was open, mergeable and clean; its 11 successful and 2 intentionally
skipped checks were terminal.

| Area | Before | Evidence and gap |
| --- | ---: | --- |
| Security | 7.0/10 | Private, non-static source storage, streaming size/hash checks, strict PPTX media/extension checks, ZIP traversal/size/ratio/macro/XML defenses, durable leases and content-free failure codes already existed. Import records lacked an authenticated owner boundary and request idempotency contract. |
| Upstream compatibility | 9.0/10 | The feature was default-off, stayed under `/api/v1`, and retained distinct `template-v2`/`authored-html`, editor, and `template-v2-general`/`authored-hybrid` strategies. No wholesale upstream merge or editor import was present. |
| Bounded MVP completeness | 4.0/10 | Deterministic OOXML parsing and an honest provider-unavailable manifest existed, but successful analysis automatically materialized a Template V2 draft before confirmation. There was no provider-neutral validated analyzer output, canvas-bound enforcement, repeated-block suggestion contract, owner-safe confirm action, or connected review UI. |

Pre-change mean: **6.7/10**.

## Parallel analysis and integration boundary

Four independent workstreams covered non-overlapping changes and tests before
root integration:

1. upload/source inventory plus file and path security;
2. schema/canvas bounds plus the provider-neutral analyzer output contract;
3. deterministic repeat suggestions plus explicit confirmation and revision
   control;
4. content-free observability, feature-off rollback and regression coverage.

The integrated slice is limited to private PPTX intake, deterministic local
OOXML analysis, schema-validated candidates, repeated-block suggestions,
explicit review/confirmation, bounded lifecycle observability, and a small
review UI.

The work deliberately excludes external AI calls, OCR, R2, signing, deployment,
whole-editor import, automatic conversion of existing presentations, and API
v2.

## Implemented architecture

### Rollout and compatibility

- `ENABLE_TEMPLATE_V2` remains the single fail-closed, default-OFF creation
  switch, with the mandatory `TEMPLATE_V2_TEMPLATE_ALLOWLIST`.
- The dispatcher does not start while the rollout policy is disabled.
- Every create, retry, cancel, and confirm mutation checks the same policy.
- Existing readable data remains readable after rollback; disabled rollout
  never converts or mutates authored/adaptive presentations.
- `GenerationStrategy(template-v2/authored-html)`,
  `EditorCapability`, and
  `ExportStrategy(template-v2-general/authored-hybrid)` stay distinct.
  Authored-hybrid was not replaced by a general export path.

### Data and migration

The existing `template_v2_pptx_imports` lifecycle sidecar gained only additive
review-boundary columns:

- `owner_scope`, `request_key_hash`, `request_fingerprint`;
- integer `revision`;
- `analysis_result`, `repeat_suggestions`;
- `confirmed_at`, `cancelled_at`.

Migration `1b2c3d4e5f6a` has exact
`down_revision = 0a1b2c3d4e5f`, supports upgrade/downgrade, and is recorded in
the migration translation ledger as the sole new Alembic head. Legacy column
sets remain valid only as the exact known lifecycle schema; partial review
column sets fail closed.

### Source, artifact, and candidate inventory

The manifest separates three inventories:

- one original private source with display name, media type, size and SHA-256;
- zero or more preview/render artifacts;
- deterministic candidate artifacts with their own SHA-256.

No private storage key, filesystem path, credential, signed URL, auth cookie,
or bearer value is returned. The current local analyzer reports preview as
`not_provided`, render as `not_run`, and visual fidelity as `not_evaluated`.

### Upload and package security

- Only `.pptx` and approved PPTX MIME types are accepted.
- Streaming upload is capped at 100 MiB and verifies size plus digest again
  before analysis.
- The stored display name is a normalized leaf name; traversal, absolute paths,
  NUL, separators and deceptive suffixes are rejected.
- Private storage is bound to a generated import UUID and confined beneath the
  configured root; symlink and containment escapes fail closed.
- The OOXML reader rejects duplicate canonical members, encrypted packages,
  macro-bearing parts, path traversal, oversized members/archives, excessive
  compression ratios, unsafe external relationships, DTD and entity input.
- Package members are read in place and are never extracted to the filesystem.
- Owner scope is a stable HMAC of the authenticated identity, or one explicit
  fixed local scope only when authentication is intentionally disabled.
  Cross-owner requests return 404 without disclosing existence.

### Analyzer and suggestions

`CandidateAnalyzer` is a provider-neutral protocol backed in this MVP by
`deterministic-ooxml-static`. Its Pydantic output contract is strict,
extra-field rejecting and deterministic:

- provider identifier, kind and truthful status;
- source/preview/render/candidate artifact metadata;
- explicit top-left pixel canvas per slide;
- finite, nonnegative geometry and exact slide bounds;
- supported element kinds (`text`, `container`, `unsupported`) with
  kind-specific validation;
- deterministic summary counts and candidate payload digest.

The analyzer does not call a network or AI provider. Repeat discovery groups
regularly aligned, same-kind/same-size candidates and emits deterministic
`repeat_block_merge` suggestions only. Suggestions never modify candidates and
are explicitly recorded as unapplied even after confirmation in this MVP.

### API and lifecycle

All additions remain under `/api/v1/ppt/structured-templates/imports`:

| Method | Path | Boundary |
| --- | --- | --- |
| POST | `/` | owner-scoped upload; mandatory `Idempotency-Key` |
| GET | `/{import_id}` | private status, inventories, candidate, suggestions |
| POST | `/{import_id}/retry` | failed-only retry with `expected_revision` |
| POST | `/{import_id}/cancel` | atomic cancel with `expected_revision` |
| POST | `/{import_id}/confirm` | the only Template V2 materialization action |

The upload fingerprint binds the idempotency key to template ID and source
digest. Concurrent mutations use compare-and-swap revision predicates.
Processing retains the existing lease, heartbeat, failed/retry, cancellation,
stalled-attempt recovery and private-source TTL cleanup paths.

Analysis now stops at `review_required`: `draft_template_id` and
`confirmed_template_id` remain null. Explicit owner confirmation assembles a
new Template V2 presentation in one transaction, verifies the strategy
boundary, and is idempotent under repeat/concurrent confirmation. No existing
template, authored HTML, adaptive presentation, or authored-hybrid export is
automatically converted.

### UI and observability

The existing Template V2 Studio has a compact
upload -> analysis polling -> suggestion review -> explicit confirm panel. It
states that analysis is deterministic local OOXML parsing, not AI/OCR, and
requires a separate confirmation button after review.

The analyzer event schema accepts exactly four bounded fields:
`provider`, `status`, `duration_ms`, and `count`. It has no entry point for
filename, owner, storage key, source text, document hash, prompt, exception
body, or arbitrary metadata. Persisted task failures contain stable codes, not
raw document content.

## Verification

| Scope | Command/result |
| --- | --- |
| Integrated schema/security/API units | focused matrix: **162 passed, 1 skipped** |
| Next/Cypress component gate | **14 specs, 73 passing**, including the new review/confirm flow |
| Existing strategy regressions | root **3**, adaptive **19**, authored-hybrid **83**, Template V2 **70**; all passed |
| Full FastAPI | first integration run found 3 stale Alembic-head/partial-column expectations; after fixing the exact-head validator and tests: **808 passed, 4 skipped, 60 warnings**, exit 0 |
| Migration-focused retry | **62 passed** after the migration fix |
| TypeScript | `npm exec tsc -- --noEmit`: exit 0 |
| ESLint | `npm run lint`: **0 errors**, 216 pre-existing warnings |
| Next production build | `npm run build`: exit 0 |
| Template V2 export fidelity | **11 passed, 1 skipped**; actual `/pdf-maker` to `presentation-export v0.4.2` path passed; visual comparison skipped because LibreOffice/Poppler were unavailable |
| presentation-export sync | **16 passed**; `node scripts/sync-presentation-export.cjs --check-only` retained v0.4.2 and the Windows converter |
| Offline intake | `node --test scripts/intake-upstream-main.test.mjs`: **9 passed** |
| Static compatibility | `node --test scripts/verify-upstream-compatibility.test.mjs`: **1 passed** after registering the additive head |
| Local gate plan | `node scripts/test-local.mjs --dry-run --all`: **28 planned**, exit 0 |
| PostgreSQL boundary | Dedicated PostgreSQL migration/integration coverage is wired into required PR CI. Local Docker was unavailable (`docker` command not found), so no local PostgreSQL success is claimed. |

The first full-suite failures were not ignored or retried unchanged: they
exposed a real migration-head integration gap, which was fixed before the
successful rerun. The Windows `pytest-current` cleanup PermissionError appeared
only as existing atexit noise after exit code 0.

The upstream report's separate Windows baseline was 607 passed / 7 failed:
four UUID-versus-slug expectations, one `/tmp` separator expectation, one
converter `.exe` expectation and one chmod-mode expectation. This branch's
successful 808-test run did not reproduce those as product regressions.

## Post-change assessment

| Area | After | Basis |
| --- | ---: | --- |
| Security | 9.3/10 | Owner isolation, hashed idempotency, path and package hardening, strict schema/bounds, secret-free inventory, content-free telemetry and concurrency tests close the identified MVP gaps. Production malware scanning and provider credential isolation remain outside the slice. |
| Upstream compatibility | 9.6/10 | Default-OFF allowlisted rollout, `/api/v1` only, additive exact-head migration, static compatibility ledger, unchanged authored/adaptive/hybrid and v0.4.2 boundaries, and no wholesale import. |
| Bounded MVP completeness | 8.4/10 | The end-to-end local flow is operational through explicit confirmation with UI, lifecycle, recovery and tests. Production OCR/CV/provider, rendered preview generation, confidence calibration and advanced structure inference remain intentionally incomplete. |

Post-change mean: **9.1/10**.

## Intentional upstream selection and residual production work

Selected manually from upstream-compatible concepts:

- strict Template V2 schema/canvas strategy separation;
- additive migration discipline and `/api/v1` surface;
- deterministic local candidate extraction as a provider adapter boundary.

Intentionally not selected:

- the 49K LOC Konva editor;
- API v2 traces;
- whole-branch merge or wholesale cherry-pick without a merge base;
- generic export replacement for authored-hybrid;
- upstream sync behavior that would regress v0.4.2 or Windows packaging.

Remaining production work is a separate phase:

1. sandboxed provider adapters with explicit credential egress policy,
   cancellation, rate limiting and cost controls;
2. OCR/CV accuracy evaluation and versioned fixtures;
3. safe rendered preview production plus fidelity thresholds;
4. richer table/chart/group/master-layout inference and user-editable
   suggestion acceptance;
5. malware scanning, retention operations, load/queue capacity and multi-node
   dispatcher validation;
6. production PostgreSQL observability and rollout drills.

The pushed commit, terminal PR check results and final worktree states are
reported in the session handoff after remote verification.
