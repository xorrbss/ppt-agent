# Provider-free SVG runtime replay preparation — 2026-11-08

## Prepared scope and execution status

New standalone entrypoint: `scripts/svg_freeze_runtime_replay.py`.
Only this entrypoint and this preparation note were authored for this task.
No replay, API, DB, principal resolution, publication, server, provider, export,
PowerPoint/COM or verification command was executed. Root retains the single
syntax/check selection and all actual execution after accepted captures exist.

This is a developer service harness, not a new endpoint or a generation driver.
It does not claim generation HTTP admission, model generation, picture-provider
behavior, adversarial RLS coverage, visual acceptance or release readiness.

## Explicit modes

Default `validate-only` reads local synthetic inputs and invokes existing pure
builder/consumer/classification checks. It does not resolve authentication,
open a DB session, render, publish, export, fetch URLs or call providers.

Explicit `--resolve-identity --identity-output <new-private-file>` invokes the
unchanged `SessionAuthMiddleware.dispatch` around a read-only callback, under
the caller's actual managed PostgreSQL launch environment. It writes the genuine
owner/tenant and original privilege/auth claims only to the caller's new private
identity file. Standard output contains only `resolved` and an identity digest.
There is no account-fence UPDATE or other DB mutation in this callback.

Explicit `--mode publish --manifest <capture-manifest> --identity-file <private-identity>`
validates everything first, resolves the normal product principal again, matches
the exact expected owner/tenant and then calls existing `_publish(..., job=None)`
with a fresh random presentation UUID. It uses existing active-account fencing,
owner scope, normal PostgreSQL authority/RLS and normal revision publication.
It never synthesizes SQL rows, jobs, admissions, review outcomes or gate seals.
Original admin/default-tenant claims are allowed when genuinely resolved; no
privilege elevation, role switching, skip-owner flags or RLS bypass is performed.

The root observed an existing `DISABLE_AUTH=true` launch setting. The product's
actual middleware in that mode selects its existing active primary bootstrap
administrator and configured service tenant. The harness calls that exact code;
it neither copies the principal constructor nor changes authentication settings.
Missing/inactive/mismatched genuine principals fail closed. In ordinary auth
mode, a separate `--auth-file <private-existing-cookie-file>` supplies an existing
`sessionCookie`. Use this same separate file for both identity resolution and
publication; never merge credentials into the resolved identity output. No
cookie/token is accepted in command arguments, minted, printed or copied to identity
output. The current configured no-auth middleware path needs no cookie file.

The private publication audit is reserved and flushed before `_publish`, retaining
the new presentation UUID and input provenance if the outcome becomes uncertain.
The private audit is append-only JSONL, opened with exclusive creation and
`O_APPEND`. Its first `publication_started` record is fsynced before publication.
After successful owner-scoped readback, a separate completion record with the
actual revision and `startedRecordDigest` is appended and fsynced. No seek,
truncate or overwrite can destroy the original record; a partial completion line
still leaves its complete UUID/input record available. Never retry
an uncertain publication without reconciling this private UUID first. Standard
output after success contains only the synthetic UUID/revision and input hashes.
Private directories must already have suitable Windows ACLs; POSIX mode `0600`
does not itself establish Windows directory privacy.

## Required capture manifest

Top-level schema is `svg-freeze-runtime-replay/v1`, with literal `synthetic=true`.
File references contain exactly relative `path` and `sha256` (prefixed SHA-256 of
the exact file bytes), remain below the manifest directory, and cannot fetch URLs.
Required referenced JSON files are `request`, `outline`, `styleSnapshot`,
`fixture` and `qualitySummary`; `pages` contains the per-page references below.

- `request` must validate as the actual `GeneratePresentationRequest`: strategy
  SVG, actual count/canvas/instructions, no template/source-restyle/references,
  files or master. `outline` validates as `PresentationOutlineModel`.
- `styleSnapshot` must be an actual input produced by the existing pin logic,
  carrying the exact v5 requirement revision and intact canonical digest. The
  harness never pins/bump-revisions itself or substitutes live environment policy.
  `pinnedInput.function` names `services.svg_generation_admission.pin_svg_style`;
  `snapshotDigest` matches the snapshot and `requestDigest` is the prefixed hash
  of compact, sorted UTF-8 JSON of the exact request input. This local provenance
  record requires root-supplied genuine input; it is not an independently signed
  admission receipt. Existing profile/agreement validators restore frozen choices.
- The snapshot's actual readability policy/canvas is mandatory. Actual captured
  physical-readability policy, normal font floor and publication requirements
  must pass unchanged. A capture obtained with a relaxed minimum font size cannot
  be relabeled as a normal-profile capture. Correct fixtures and recapture when
  genuine frozen font/style/page-number rules reject them.
- `fixture` is the actual `svg-chart-support-graph-examples/v1` bundle with
  `examples[{name, canonicalSvg, nativeDeclarations, manifest, requestedSource}]`.
  Each `requestedSource` is `synthetic-chart-request/v1`, authored before capture,
  explicitly records generation HTTP unmeasured, and has `content`, independent
  `payload` and `sourceDigest` over compact sorted JSON of `{content,payload}`.
  Outline content must equal this independent source. Bound declarations must
  equal the fixture's exact bound record; the fixture manifest is rebuilt through
  the current real builder and compared with its stored manifest.
- Each page contains `fixtureName`, `role=content`, and references `svg`, `payload`,
  `capture`, `consumer`. `svg` is UTF-8 final SVG. `payload` is the actual complete
  final classified page payload, including matching `svg_content`, bound
  `nativeDeclarations`, original quality and frozen `readability_policy`.
- `capture` is lossless actual `SvgRenderResult` serialization with
  `type=services.svg_renderer.SvgRenderResult`, `pngBase64`, `quality`,
  `wrapper_html`, `source_digest`. Preserve the actual admitted/materialized
  wrapper; never reconstruct a wrapper digest from the canonical SVG digest.
- Original `capture.quality` is mandatory and must contain all actual producer
  fields: `contract_version`, `renderer_version`, `source_digest`, `png_digest`,
  `wrapper_digest`, `metrics_digest`, `measurement_complete`, `geometry_passed`,
  `passed`, `verified`, `semantic_review`, `failures`, `body_font_floor_px`,
  `browser_metrics`, `overlap_basis`, `trusted_font_materialization` and
  `physical_readability`. Empty/omitted quality, incomplete metrics, failed
  producer geometry/measurement, absent trusted font materialization or relaxed
  frozen readability/floor fail closed. Identity, metrics digests, font floor
  and readability are verified against the preserved producer quality itself.
  Canonical JSON equality compares the full key sets and values in both
  directions with page quality, including unknown additional producer fields.
  The **only derived-field allowlist** is `verified`, `classification`,
  `requestedNativeObjects`. These are removed from the preserved producer copy
  and recomputed by existing post-render native-quality and page-classification
  functions, then compared with the supplied page quality and summary. `passed`,
  font floors, metrics, failures and physical-readability fields are never
  supplied, replaced or repaired by this derivation.
- `consumer` is the actual strict `validate_chart_content_paint` result. The
  harness rebuilds witnesses and reruns that consumer using the actual PNG,
  wrapper, browser metrics and source identity, requiring positive exact equality.
  It also reruns `apply_requested_native_quality` post-render on the independent
  source and requires exact existing quality agreement and complete native
  projection, positive required-chart count and `contentRetentionProven=true`.
  No supplied `passed` boolean replaces these consumers.
- Every SVG must already be final for its index/count and actual frozen style.
  Existing finalization must be byte-idempotent. Publication receives the verified
  bytes without edits. All six supported native chart types are required; expected
  object count comes from the exact fixture declarations, not a hardcoded six.
- `qualitySummary` must be the actual classified summary; existing reclassification
  must leave both actual payloads and summary unchanged.
- `codeDigests` maps repository-relative backend source names to exact prefixed
  raw-file digests. At least `svg_renderer.py`, `native_chart_evidence.py`,
  `native_requirements.py`, and `chart_content_paint.py` are required at their
  existing paths. Include all actual capture/builder/consumer dependencies for
  complete root provenance. Mismatched current code fails validation.

At preparation time only seven fixture cases existed and covered column, line,
pie and donut. Bar/area plus the corresponding actual requested sources/captures
are still required for the complete replay. The default-timeout accepted full
capture manifest was not available. Root must also supply a genuine actual v5
snapshot; the inspected current fresh-admission constant still selected v4.
Do not hand-edit that constant or fabricate the required input for this harness.

## Root execution and actual export follow-through

Use the shared backend Python and preserve the managed-stack launch environment
through `Read-StackEnvironment` in an isolated child process. The script never
loads a different environment, changes flags, creates users or starts a server.
Resolve identity and validate captures are separate explicit actions. The new
identity file contains schema `svg-freeze-runtime-replay-private-identity/v1`,
`expectedOwnerId`, `expectedTenantId`, `privateAuditPath`, original principal
claims and the explicit adversarial-RLS-coverage limitation, without credentials.
Its default audit target is a new sibling `.publication.jsonl`; review that path
before publication. `--identity-file` or `SVG_FREEZE_REPLAY_IDENTITY_FILE` selects
this file for publication. A separate existing-cookie JSON file contains
`sessionCookie`, and is selected by `--auth-file` or
`SVG_FREEZE_REPLAY_AUTH_FILE` in explicit identity/publish modes when normal auth
requires it. No manual secret merge is needed. Default validation reads neither
private file handle and rejects identity/credential command arguments.

After actual publication, submit the normal authenticated workspace command at
`POST /api/v1/ppt/workspace/presentations/{id}/commands`: command type
`export.pptx`, nonempty command/idempotency key, exact current expected revision,
and existing `context.quality_policy=interactive-v1`. Keep all frozen admission,
quality-recovery, font and lineage gates intact; failures are evidence, not a
reason to change policy. Do not add authored `pptx_mode` to SVG requests.
Poll the presentation timeline and download the completed artifact through
`/api/v1/ppt/workspace/artifacts/{artifact_id}/download` with normal credentials.

The actual operation must pass through `execute_svg_workspace_export`, export
identity, `render_and_export_svgs`, the trusted worker, OOXML readback and artifact
publication. Retain operation/revision/owner-scope evidence privately, the final
artifact SHA, readback's native offered/native/fallback counts, coverage receipts,
native chart kinds/series/categories/values and editable chart workbook bindings.
Different public-artifact and workspace-artifact IDs are not interchangeable.

Render the exact downloaded PPTX using the existing owned Windows Job Object/COM
`workers.public_svg_pptx_render.render_final_pptx`. Retain final PNG hashes and
before/after PPTX hash invariance and inspect actual resulting pages. Source PNGs
alone do not prove this. Full `quality.review(target=final_pptx)` additionally
calls required vision advisory and is not provider-free; do not enqueue it here
or fabricate a visual-review/release receipt from deterministic rendering.

Actual API `/health` and `/ready`, plus Next/proxy `/api/health`, remain root's
managed-stack checks. No runtime loading, export, rendering or quality outcome is
claimed by this preparation note.

## Final-review corrections

The final static review requested three changes, implemented only in this script
and note: mandatory bidirectionally bound original capture quality with exactly
three independently recomputed derived fields; append-only publication completion
records preserving the initially reserved UUID through crashes; and separate
resolved-identity versus existing-cookie inputs for normal authenticated mode.
Static re-review by Astra remains required before execution. No syntax or runtime
check was performed as part of these corrections.
