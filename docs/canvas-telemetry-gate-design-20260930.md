# Production telemetry canvas gate and SVG canvas-only stages

Date: 2026-09-30. Status: proposal, design only; no implementation or runtime change.
Scope: replace the offline SVG `verified` decision with production receipt evidence, retain
the offline pre-exposure `candidate` decision, and remove style from SVG canvas-stage keys.
Authored retains its style keys. Recommendations below are not accepted product contracts.

Evidence was read from the named documents and their directly connected backend paths at
`153b02691`. Line references below are relative to `servers/fastapi/` unless explicitly prefixed
with `docs/`. No shared database, stack, receipt directory, or production telemetry was queried.
The 42-deck observations are the prior report's observations, not a new reproduction.

## Recommendation and exact decision policy

Use two small PostgreSQL tables: compact numeric per-deck facts and append-only aggregate
evaluations. Run an hourly maintenance job, expose its last committed snapshot to admins, and
evaluate automatic stage transitions once daily at 00:00 UTC. Admission reads an in-memory,
revisioned effective-stage snapshot. Never rewrite the packaged stage file at runtime.
Automatic transitions manage seeded non-baseline canvases; 16:9 is the reference, never compared
against itself. A 16:9 geometry mismatch creates a critical alert and freezes all verification;
changing baseline exposure requires an explicit operational decision.

| Policy dimension | Recommended initial value |
|---|---|
| Comparison | SVG canvas versus `wide-16x9`, same explicit `metricEpoch`, same UTC window and eligible source mode |
| Window | Trailing 14 complete UTC days, `[T - 14 days, T)`, where T is the daily 00:00 UTC cutoff |
| Quality minimum, **each arm** | 48 independent generated decks and 288 measured published pages |
| Temporal spread, each arm | At least 4 UTC dates with at least 6 measured decks each; no date supplies more than 50% of measured decks |
| Account diversity, each arm | At least 4 account scopes; no account supplies more than 40% of measured decks |
| Traffic comparability | Same eligible source mode; baseline support for every candidate model/provider cohort; absolute provider/model and slide-bucket share differences <= 0.20 |
| Secondary minimum, each arm | 48 decks with valid retry and elapsed-time measurements; each metric must cover at least 90% of generated successful decks |
| Export minimum, each arm | 48 settled deck-level PPTX export requests bound to the generated revision; no missing geometry verification among successes |
| Unmeasured ceiling | At most 10% of published decks for quality/composition and at most 10% of requested exports unresolved after their settlement deadline |
| Page flag non-inferiority | Upper Newcombe bound for `p_canvas - p_16x9` **<= 0.05** absolute |
| Retries non-inferiority | Upper deck-mean difference bound **<= 0.10 additional retry per slide** |
| Seconds non-inferiority | Upper deck-mean difference bound **<= 0.15 * baseline mean seconds per slide** |
| Export failure non-inferiority | Upper Newcombe bound for deck export-failure difference **<= 0.05** absolute, with design effect 1 |
| Generation failure non-inferiority | Upper Newcombe bound for final deck generation-failure difference **<= 0.05**, minimum 48 terminal generation operations each arm |
| Composition initial status | Advisory, not a certification gate; empty-band increase alert at point difference >= 0.10 and lower deck-mean bound > 0 |
| Correlation/confidence | `rho = 0.15`; one-sided 95%, `z = 1.645`, reusing `scripts/canvas_bench_stats.py` |
| Recovery hysteresis | Minimum 7 complete days after demotion, plus at least 24 new measured decks **per arm** since demotion |

Promotion rule: **candidate -> verified** on the next daily evaluation if the offline candidate
authorization exists for this SVG canvas, every sample/completeness/mix condition below holds,
and every primary, retry, seconds, generation-failure and export-failure upper bound is at or
below its margin. Also require zero confirmed export canvas-size mismatches, no active severe
failure alert, no unresolved receipt-binding conflict, and the recovery condition if previously
demoted. Equality at the upper-bound threshold passes. Composition advisories appear in the
decision receipt but do not block promotion until calibrated. A manual offline `verified` receipt
can no longer produce this transition.

Degradation rule: on a daily evaluation with adequate samples, a metric is FAIL when page flags
have `point difference >= 0.05 AND lower bound > 0`; retries or seconds have `lower > margin`;
generation or export failures have `point difference >= 0.05 AND lower > 0`. A FAIL immediately
produces **verified -> candidate**, an append-only transition receipt, an active admin alert and
a structured warning. A candidate stays candidate and receives the same alert. The page/export
failure rules deliberately preserve the bench's asymmetric fail definition; `lower > margin`
is the existing continuous-metric fail definition. No consecutive overlapping-window failures
are required or described as independent evidence.

For adequate samples, neither PASS nor FAIL is HOLD. HOLD keeps the current stage and publishes
its reason; it does not invent a pass or automatically demote. Insufficient samples, unsupported
measurement, mix imbalance or excessive missingness also HOLD, with an active warning when a
previously verified canvas loses adequate evidence. Two consecutive daily inadequate snapshots
mark verification `stale`; the stage stays unchanged with that visible warning. An epoch change
immediately resets any telemetry-earned verified stage to candidate, audits the reset and warns
that the new epoch has no certification. It cannot carry a previous epoch's bounds forward.

Safety override: one **confirmed exported-canvas-size mismatch** bound to the generated revision
immediately moves an exposed SVG canvas to **disabled**, without waiting for samples or the daily
cutoff, and records a critical admin alert. Confirmation means exporter/readback physical geometry
disagrees with the stored CanvasContract, not that a client requested a different size. Existing
decks remain readable/exportable under their saved contract. Re-exposure requires a documented
repair, a new successful offline candidate receipt and an explicit administrative re-enable;
telemetry cannot auto-enable disabled. Any first terminal product export/generation failure creates
a warning immediately; three product failures in the last ten eligible terminal operations create
a critical alert and block promotion even below the statistical minimum, but do not silently remove
exposure. Recovery clears that severe alert only after ten subsequent consecutive eligible successes.

This is an operational non-inferiority policy, not an anytime-valid 5% error guarantee. Daily looks
and multiple canvases reuse data. Record every look and its rule version; do not market nominal
95% bounds as a family-wise or sequential confidence claim. A future alpha-spending policy is an
explicit open decision, not a reason to multiply daily p-values or to claim independent confirmation.

Retain the offline **disabled -> candidate** pre-exposure gate. Adopt the closure plan's proposed
candidate rule: 12 candidate/18 baseline decks (72/108 measured pages for the six-slide fixture),
three manuscripts per arm, no manuscript above 40% of decks, one UTC day per arm, page upper
bound <= 0.10, retry upper bound <= 0.10, seconds upper bound <= 15% of baseline mean, and zero
unexcluded product generation/export failures or PPTX size mismatches. Keep its existing <= 10%
unmeasured ceiling, epoch matching and complete metric checks. Run it in a disposable benchmark
deployment with an isolated stage override; never temporarily expose disabled canvases on the
production server. Promotion records the receipt digest, epoch and rule. The two shipped candidates
retain their existing receipt provenance; tighter proposed margins do not manufacture new evidence.
Production epoch rollover preserves that structural exposure authorization, resets verification,
and requires fresh production bounds; a geometry safety disable specifically invalidates the
authorization until the repaired offline gate is rerun.

## Current behavior and evidence

| Boundary | Observed implementation and implication |
|---|---|
| Exposure | `services/authored_canvas.py:309-322` loads a cached ratio -> style -> stage document, validates exactly the gated ratios and defaults a missing style to disabled. `:333-336` includes **every stage except disabled** in supported ratios. Candidate already reaches users. `:358-361` admits baseline and rejects disabled gated ratios. There is no runtime telemetry promotion/demotion service here. |
| Shipped stages | `constants/canvas_stages.json:2-6`: 1:1/default and 21:9/default are candidate. Other styles are absent. `authored_canvas.py:61` gates only 1:1 and 21:9. Portrait is still hard-coded by `:38,325-330`; A4 has its separate branch at `:348-357`. This proposal does not silently fold portrait/A4 into the square/ultrawide migration. |
| Public capability | `utils/authored_style_catalogue.py:33-56` calls the same style-only functions for public `supportedAspectRatios` and `supportedCanvases`. A strategy-aware backend admission alone would leave the picker inconsistent. |
| Statistical code | `scripts/canvas_bench_stats.py:13-67` supplies rho 0.15, design effect, Wilson, Newcombe and deck-mean normal difference bounds. `scripts/canvas_bench_assess.py:133-173` implements secondary and primary verdicts; `:176-217` adds sample, missingness and hard-failure gates. |
| Offline policy | `docs/canvas-bench-decision-rule-design-20260929.md:13-38` documents the implemented 0.20/0.15 margins, epoch fallback and upward-only promotion. The closure plan proposes 0.10/0.075 instead; those proposed values are not the shipped rule. |
| Measured prior evidence | `docs/canvas-bench-pooled-candidate-20260929.md`, Compare and Caveats: 42 decks, one day, default only, 6 slides; 16:9 has 2 flags/108 pages, each candidate 1/72. All flags are required-text-missing. The measured ink rate is about 0.02, making 0.15 a weak production margin. |
| Page classification | `services/generation_core/drivers/svg/quality.py:21-41` counts non-local attempts and writes classification/verified into checkpoint quality. `services/generation_core/quality/page_outcome.py:139-151` owns published review-page selection; use that same rule for the telemetry numerator. |
| Publication | `services/generation_core/drivers/svg/publish.py:29-43` strips attempts/SVG bytes from the slide receipt; `:106-125` persists final and initial qualities in `theme.svg`; `:129-136` stores each SVG plus `content.svgReceipt`; `:144-152` commits only counts/status/paths to the operation result. Counts alone lose classification and retry attribution. |
| Geometry/epoch | `services/svg_renderer.py:265-300` reads browser element boxes and visibility; `:307-310,333-342` stores `source_digest`, `browser_metrics`, failures and object-form `overlap_basis`. Browser metrics include owned text; copying them wholesale is not receipt-only privacy. Renderer version alone cannot identify the ink transition. |
| Timing | `services/generation_core/drivers/svg/timing.py:1-28,48-110` records shared recorder stages; author/render durations sum concurrent page work. `services/presentation_generation_attempt_timing.py:133-180` seals terminal attempt timing, provider totals and elapsed wall time. `services/authored_generation_timing_receipt.py:247-279,398-444,945-1129` provides digest-bound generation and v2 attempt receipts; successful generation timing requires a QualityReceiptV2, which should not be fabricated for SVG. |
| Quality ledgers | `services/authored_quality_receipt_v2.py:46-83,1187,1396-1423,2236` defines local manifest paths and sealed V2/V3 ledgers with atomic persistence. `services/authored_quality_receipt_v3_pipeline.py:333-345,435-449` distinguishes public injected persistence from local manifests. `services/public_quality_receipt_v3_store.py:170-230` appends public V3 evidence under the operation attempt/fence. These are authored evidence stores, not proof that every SVG generation writes a QualityReceipt ledger. SVG authority is its published quality receipts. |
| Export settlement | `services/presentation_generation_export_chain.py:193-223,230-269` annotates an already-completed task with chained export outcome. A completed generation without that annotation is pending, not export failure or export success. The pooled report documents the bench poll race. |
| Maintenance | `api/lifespan.py:139-158,175-192,218-235` starts settings sync, retention and maintenance tasks and stops them on shutdown. `services/export_retention_service.py:149-206` provides startup-plus-periodic bounded work and explicit iteration errors; it is process-local, not a distributed leader election. `services/workspace_event_retention.py:35-45,80-98` provides bounded DB retention with validated settings. |
| Settings/observability | `services/runtime_settings_coordinator.py:58-119` publishes a monotonic DB revision and refreshes cached process settings, with retries and warning logs. It currently coordinates **provider** settings; canvas stages need their own revision stream, not an undocumented field in provider configuration. `services/authored_generation_observability.py:186-231` emits validated structured observations and refuses arbitrary error prose. `api/v1/admin/router.py:76-92` supplies admin dependency and mutability checks. |

## Receipt and aggregate data model

### Measurement identity and inclusion

Define a server-emitted measurement descriptor containing SVG contract/renderer versions,
overlap-basis version, classification-rule version, canvas-brief/composition algorithm versions,
and workload mode. Map that descriptor to an explicit versioned `metricEpoch`; include the
descriptor and epoch in each compact receipt. Do not infer production epochs from git ancestry
or reinterpret missing epochs as the current ink epoch. All published pages must agree on the
descriptor. Missing/mixed descriptors give `unmeasured: metric_epoch_missing_or_mixed`.
Store `ruleVersion` separately: changing margins/rho does not relabel the measured source data.

The initial certified population is **new free-design SVG generation**, excluding source-preserve,
raster reconstruction, source-template/master-backed generation and user-edited/remade revisions.
Keep their counts and modes observable, but they require separate epochs/populations before they
can certify the general generator. Authored never enters the SVG pool. Deduplicate by generation
operation and published revision; automatic operation retries/resume are one deck, not fresh samples.
User-requested regeneration creates a separately marked operation; the initial gate uses the first
published generation of a presentation to prevent repeated regeneration inflating sample counts.
Synthetic/bench jobs are stamped at admission and excluded from production decisions.

Every terminal admitted generation contributes to generation outcomes, including failure before
publication. Only complete published pages enter page-quality denominators. Capture the canvas,
epoch descriptor and eligible mode at admission so failures cannot disappear through missing theme
data. Cancelled jobs are counted separately and do not count as product failure; unexplained loss
of a terminal outcome remains pending/missing. Infrastructure exclusions require a bounded reason
enum, authenticated audit and receipt evidence; never trim latency outliers automatically.

Use the same 14-day generation cohort in both arms, timestamped at terminal generation time.
Export outcomes are joined back to that cohort and to its original published revision. A deck
with no requested export has `not_requested`, not succeeded; it cannot satisfy the export minimum.
PPTX certification is mandatory; PDF is a separately reported format and does not substitute.
If a deck requested both, keep both statuses; its PPTX status drives the gate. A product failure
on another format still alerts, but does not masquerade as a PPTX result.

### Compact page/deck facts

Publish an additive `canvasTelemetry` receipt (`canvas-telemetry-deck/v1`) alongside SVG quality:

- Binding: generated revision, internal operation/attempt identity, canvas registry ID and full
  canvas-contract digest, epoch descriptor, rule-independent schema version and receipt digest.
- Per page: index, role enum, final review-required boolean, classification enum, unique failure
  kind enums, verification completeness, non-local author calls, local repair count, provider
  retry calls attributed to this page, composition scalars and measurement status/reason enum.
- Per deck: planned/published page counts, flagged-page count, classification histogram, per-kind
  page counts, retry totals, terminal generation outcome, elapsed timing source/digest and seconds
  per slide, coarse model/provider/version and slide-count bucket, plus export settlement fields.

The quality flag numerator is exactly the published review-page set, not all historical failed
attempts or all non-passed classification labels. Histogram bins use the shared classification
enums; count each page once. Failure-kind counts are multi-label and need not sum to flagged pages.
Keep first-pass failures separately from final residual flags. Preserve zero versus unavailable:
missing attempts, missing duration or absent composition is null with a reason, never zero.

Retry cost is `(sum(max(non_local_author_calls_per_page - 1, 0)) + unique_page_provider_retry_calls)
/ published_pages`, with local repair attempts reported separately. Instrument provider retry
attribution at the existing slide usage scope and deduplicate invocation IDs before summing.
Deck-wide outline/brief/provider retries and operation restart counts are separate cost fields;
do not add `attemptNumber - 1` to every slide or infer retries from a fixed logicalCalls - 10.
Recovered checkpoints carry cumulative page attempts across the operation. An unresolved attribution
gap is unmeasured. This is intentionally more precise than the current bench's deck proxy; update
the offline adapter to the same retry definition before comparing its new receipts.

Seconds per slide is successful-operation **elapsed active attempt wall milliseconds summed across
all its attempts / published pages / 1000**. Exclude queue time between attempts, include authoring,
outline, render, repair and publication. Parallel stage-work sums are not elapsed wall time and are
not added together. Timing writer seals after publication, so publish binds a pending timing field;
the terminal handler attaches the sealed attempt receipts asynchronously/idempotently. Store both
this bench-comparable deck elapsed/page value and actual page-loop elapsed distributions as advisory
diagnostics. Never silently substitute process uptime or request-poll wall time for a missing receipt.

### Two tables and transaction boundaries

1. `canvas_telemetry_deck`: unique generation operation + initial published revision (revision 0
   sentinel for terminal pre-publish failure), canvas ID, epoch, terminal time, mode, compact page
   numeric JSON and receipt digest. Small mutable settlement columns hold timing and revision-bound
   export state/digest; updates are idempotent and reject changed immutable bindings. Index
   `(metric_epoch, canvas_id, terminal_at)` and unresolved export state. No SVG or text column.
   If the same operation is explicitly redriven after a final failure, reconcile its sentinel and
   published row to one operation outcome; aggregate the operation once, retaining attempt costs.
2. `canvas_telemetry_aggregate`: `rowKind` distinguishes control from append-only evaluation rows.
   Evaluation keys contain canvas, epoch, cutoff,
   rule version and monotonic evaluation revision. Store window, counts, sums/sums-of-squares,
   deck-size moments, fixed-bin histograms, estimates/bounds, completeness/exclusion counters,
   pass/fail/hold reasons, previous/effective stage, alert state and decision provenance.
   A unique partial key permits one control row per canvas, holding the current stage, offline
   authorization digest, epoch, policy revision, latest evaluation revision and recovery time. Update it
   by compare-and-swap in the same transaction that appends an evaluation/transition row.

The small aggregate table is the chosen aggregate storage; the fact sidecar makes reconstruction,
late settlement and meaningful uncertainty possible. Summary-only counters cannot estimate
deck-level variance, remove a late-invalidated deck or audit duplicate publications.

At fenced publication, write compact quality/composition facts in the **same transaction** as
slides/theme/revision; only committed publication can enter aggregates. Compute geometry before
ordered publication locks. Terminal failure/timing handlers and export finalizers settle their
facts using the existing attempt/revision authority. Do not amend sealed QualityReceipt manifests
or digests; bind the new sidecar to existing receipt digests. A missing telemetry write never changes
the user's quality verdict: authority evidence remains published; mark incomplete and reconcile
from committed compact receipts. Failed reconciliation emits a warning and prevents certification.

Use a PostgreSQL advisory transaction lock per maintenance shard plus control-row CAS so concurrent
lifespan tasks cannot both promote. Bound scan size and resume with a durable watermark; sweep late
settlements separately and recompute affected snapshots. The job runs hourly; a fixed daily cutoff
can create only one decision per canvas/epoch/rule. Safety overrides can append a transition anytime.
Keep facts 45 days and evaluations/transitions 365 days; retain the current control row and its last
authorizing transition even if older. A failed hourly run leaves the last snapshot readable with age
and job-error metadata; more than two missed hourly intervals warns and freezes auto-promotion.

Why DB rather than `app_data/canvas-telemetry/*.json`: lifespan proves that a maintenance loop is
already a normal integration point, but the export sweeper's process-local timer provides no
cluster-wide exclusivity. Public V3 receipts already use PostgreSQL fenced operation authority.
Files would require shared mounts, cross-host locking, crash-safe stage CAS and a new notification
path. Reuse DB authority and the settings coordinator's **pattern**, adding a separate stage-revision
coordinator; do not change the provider coordinator's responsibility. An on-demand JSON export is
allowed for debugging/offline CLI input, with numeric receipts only, not runtime authority.

## Composition computation at publication

Add `services/svg_composition_receipt.py`, a pure reducer over numeric element geometry measured
from the exact final SVG during its existing trusted Chromium render. The publisher calls the
reducer once before constructing slide receipts, writes `quality.composition` in both the
slide receipt and `theme.svg.quality[]`, and includes the scalars in the compact telemetry receipt.
If checkpoint geometry is missing or bound to different SVG bytes, request the same trusted
render measurement before publication; never parse/re-render SVG in the aggregate/admin query.
Extend the existing measurement probe to provide numeric leaf geometry if it lacks it. Raw XML
text lengths cannot determine font bounds or transformed paths reliably.

Version `svg-composition/v1` freezes the following algorithm:

1. Work in logical CanvasContract coordinates; transform each visible SVG leaf into viewport
   coordinates, including ancestor transforms, opacity and stroke extents. Wait for the existing
   font-ready condition. Use glyph fragment rectangles for owned text, falling back to measured
   text bounds with an explicit `bbox_fallback` bit; image bounds and numeric shape bounds come
   from the final render. Ignore hidden/zero-opacity/zero-area leaves, defs and system page-number
   elements. Store only geometry/scalars; remove `owned_text`, IDs containing arbitrary prose,
   image paths and data URLs before the reducer's output crosses the telemetry boundary.
2. Exclude pure background surfaces: filled non-text/non-image rectangles whose measured bounds
   cover at least 95% of both canvas width and height and contain the canvas center. Keep imagery,
   charts, tables and other visible components; report `background_filter_version`. This heuristic
   avoids a full-slide fill forcing 100% occupancy, but a full-bleed illustration can legitimately
   occupy the page. Keep a separate text/image-only coverage diagnostic so decorative geometry
   cannot completely hide sparse content. Small decoration and rules remain an acknowledged bias.
3. Clip retained rectangles to the safe area `S=(sx,sy,sw,sh)` and take their union bounding box `B`.
   Define `top_gap = max(0,B.top-sy)/sh`, `bottom_gap = max(0,sy+sh-B.bottom)/sh`, and
   `empty_band_ratio = max(top_gap,bottom_gap)`. Also compute the longest internal vertical gap
   in the union of occupied y-intervals divided by sh. If there are no retained rectangles,
   empty-band is 1 and content bbox/columns are unavailable with reason `empty_content`; it must
   not be represented as a successful zero-area box. Persist top and bottom separately so the
   reported square-cover bottom-band issue is visible.
4. `content_bbox_aspect = B.width/B.height` for positive dimensions. Also persist
   `normalized_bbox_aspect = (B.width/B.height)/(sw/sh)` and `bbox_area_ratio = area(B)/area(S)`.
   A bbox is a coverage proxy, not ink area. Report raw and normalized values; a raw portrait bbox
   is expected to differ from 16:9 and is not itself a failure.
5. Column count is an explicitly geometric estimate. Use only body-role text fragment boxes,
   excluding title/footer/page-number boxes, spanning banners with width >= 70% of sw, and boxes
   outside the central 15%-85% safe-area height. Sweep eight equally spaced horizontal sample
   lines through that central region; at each line, merge x-intervals separated by < 4% of sw,
   remove merged intervals narrower than 5% of sw, and count remaining intervals. A supported
   line intersects at least one eligible body box. Column count is the modal count of supported
   lines, ties broken toward the smaller count, clipped to 1-6 with an overflow bit. Fewer than
   three supported lines gives null, `insufficient_body_geometry`; no text gives null, `image_only`.
   Body/title identity comes from the SVG plan/measurement role; when missing, emit unmeasured
   rather than guess from prose. This measures layout lanes, not semantic table columns.

Publish-time computation is linear except bounded interval sorting, uses already-captured
geometry, and adds no LLM call. It is a heuristic with a version and completeness counter, not a
new quality verdict. Aggregate page/deck means and histograms by canvas and coarse page role
(cover/content/closing), with deck means for confidence bounds. Empty-band alerts use comparable
role mixes; warn/hold an advisory if a role-share difference exceeds 0.20. Content bbox aspect and
column count remain descriptive until labeled acceptability evidence exists. A default ultrawide
three-column design must not be penalized for differing from baseline's two-column design.

## Pooling, uncertainty and confound controls

For each arm, let page counts per measured deck be m_i, flagged pages k and total pages N.
Use page-weighted effective cluster size `m_eff = sum(m_i^2)/sum(m_i)` and
`deff = 1 + (m_eff - 1)*0.15`. Call the existing `newcombe_diff_bounds` with each arm's
`k/deff, N/deff`. Equal six-slide decks give 1.75, exactly the bench. Unequal production deck
lengths require this size-weighted extension instead of pretending every deck has the mean size;
include `sum_m`, `sum_m_squared`, deff and rho in the aggregate. Use the same adjusted counts for
class-specific rates as diagnostic bounds. Do not sum correlated class flags into a new numerator.

Retries, elapsed seconds and composition use independent **deck means** and the existing
`mean_diff_bounds` function, not per-page independence. Store deck count, sum and sum of squares,
allowing the CLI to reconstruct mean/variance without receiving identifiers or page content.
Export/generation failure rates use one outcome per deck and Wilson/Newcombe with deff 1.
All bounds are computed at full precision; rounded displays cannot decide stages.

Production decks are not randomized manuscripts. Preserve the pooled 16:9 comparator but require
eligible mode equality and check model/provider shares and slide-count buckets (1-5, 6-10, 11-20,
21+) in the two arms. Each nonempty candidate model/provider cohort must have baseline support;
an absolute share difference > 0.20 in any provider/model or slide bucket gives HOLD with the
imbalance reason. Show stratified diagnostics and language-family/page-role distributions without
recording manuscript text. A provider/model rollout requires a new epoch when it changes the
measurement/generator contract materially; do not retrospectively mix a new model into an old
certification without recording the rollout. The global aggregate is a capability decision,
not proof that every style or every content class is non-inferior.

One account generating many near-duplicate decks is another cluster. Count one first generation
per presentation, require at least four independent account scopes per arm, and permit no account
to contribute more than 40% of the measured decks. Account IDs remain in an access-controlled
collector join solely for deduplication/diversity; store only diversity count/top share in aggregates.
Do not hash manuscript text to recreate the offline manuscript rule. Repeated content across
different presentations remains residual uncertainty; report it, and never assert independence
as a proven property. A single-user local deployment can collect diagnostics but HOLD verification
under this production policy; it can use the offline candidate gate for exposure.

Export pending: join the operation's settled result, not the early generation `completed` field.
Use the admitted operation's configured timeout/retry budget to derive its settlement deadline
from existing runtime-limit settings; do not create a bare timeout literal. At deadline an export
with no authoritative terminal result is `unmeasured_stale`, alerts and counts toward missingness.
A generation-chain submission failure is product failure; `not_requested` is explicitly excluded
from export denominators. For each format, the first settled operation for the generated revision
is the outcome; a user retry is diagnostic and cannot erase its failure. Geometry success needs
the stored CanvasContract plus actual exporter/readback dimensions; missing readback is unmeasured,
not a zero mismatch. No production export is triggered merely to fill the telemetry minimum.

## Removing the per-style axis for SVG

### What style-agnostic means, and the evidence limit

SVG canvas geometry/layout policy is style-agnostic: `project_style.py:55-69` supplies the requested
canvas and rewrites every applicable common brief line; `canvas_brief.py:33-60` replaces 16:9
anchors with actual shape/size; `authoring/author_slide_svg.py:77-94` adds the same shape composition
clause and serializes the design system. `canvas_brief.py:70-84` explicitly instructs references
to contribute palette/type/treatment/imagery, never proportions, column count or frame. References
are sampled by page role at `author_slide_svg.py:139-150`. The canvas feasibility mechanism does
not have a per-style renderer, safe-area implementation or export canvas.

It would be false to claim the resulting SVG layout is statistically independent of style.
`project_style.py:69-73` retains mapped guidance, overrides and references; `svg_style_adapter.py:24-39`
contains composition phrases, and `:54-59,96-100` preserves exact-reconstruction rejection and
mapping requirements. Explicit overrides are not rewritten (`canvas_brief.py:16-17`). Therefore
remove **style as a canvas-stage axis**, not style from the authoring prompt and not adapter
compatibility checks. The closure plan's proposed layout-signature inheritance is superseded for
SVG capability decisions; the observed anchors become a monitored risk rather than 45 independent
verification matrices. Authored keeps per-style gates because this task does not establish the
same invariants for its HTML/style/master behavior.

### Schema and migration

Version `canvas-stages/v2` contains `svg` keyed **only by canonical canvas ID**, plus `authored`
keyed by existing aspect-ratio/style pairs. Migrate the shipped file explicitly:

| Old record | New packaged seed |
|---|---|
| 1:1/default = candidate | svg/square-1x1 = candidate; authored/1:1/default = candidate |
| 21:9/default = candidate | svg/ultrawide-21x9 = candidate; authored/21:9/default = candidate |
| Missing authored style | Remains disabled |
| Baseline / portrait / A4 | Preserve existing admission and physical-canvas policy; do not invent bench evidence |

The old candidates were earned on SVG/default; preserving authored entries preserves current
exposure but is not a claim of newly measured authored quality. Add provenance stating that fact.
Future authored promotion needs authored evidence. Initialize production gate control rows from
these SVG seeds with `stageSource=offline_candidate`, no telemetry verification. A legacy verified
seed is not sufficient for production verification: migrate it to candidate with a visible audit.

For any future v1 file with divergent SVG style entries, do not take max/min silently: require an
explicit migration manifest selecting the global SVG candidate based on its offline evidence.
Support reading v1 for one compatibility release in authored mode; SVG requires the explicit
canvas seed/migration manifest. The writer emits v2 only and refuses ambiguous old inputs.

Extend `canvas_stage` and `supported_aspect_ratios_for_style` with an explicit keyword strategy,
default authored for public-import compatibility. For SVG, style ID is ignored by the canvas-stage
lookup, but style validation still runs in `svg_style_adapter`. Add a single canonical
`effective_canvas_stage(strategy, canvas_id, style_id)` resolver behind admission and capability
responses; it combines packaged offline candidate authority with the latest telemetry/control
snapshot for SVG. Do not duplicate fallback/inheritance in the catalogue or endpoint.

Update generation admission, public canvas capabilities, the catalogue's `supportedCanvases` and
the picker request to pass strategy. Preserve legacy catalogue fields as authored and add an
explicit strategy-specific capability projection; old clients must not suddenly treat global SVG
availability as authored availability. Capabilities include stage, freshness and degradation reason
so users/admins can distinguish candidate from production verified. Existing SVG-adapter mapping,
source-mode, master/design-pack and document-canvas restrictions still compose with the global gate.

Exposure consequence is intentional: supported SVG styles such as osb/hyundai receive square and
ultrawide candidate availability from the global seed. This is a real admitted/rejected change,
so the baseline fixture must enumerate strategy and approve those exact rows; do not call the
migration byte-identical. Unmapped styles remain rejected by adapter validation. Alias style IDs
cannot fork a SVG stage. For portrait/A4, remove SVG style-dependent availability only in the separate
closure-plan migration with explicit offline authorization; retain current behavior until that work
lands. Telemetry may collect all canvases while only globally seeded canvases can auto-promote.

Record coarse packaged style ID and style-projection version for diagnostics, never keys for stage
authority. A style subgroup with at least 12 decks/72 pages and a class-rate excess meeting the same
point/lower fail rule raises a subgroup warning. It cannot silently disable that style or certify
the entire pool; material subgroup degradation blocks promotion as an active severe alert after
operator confirmation. This surfaces Simpson's paradox without rebuilding a per-style stage matrix.

## Administration, CLI, settings and privacy

Add `GET /api/v1/admin/canvas-telemetry` and `GET /api/v1/admin/canvas-telemetry/{canvas_id}`,
guarded by `require_settings_admin`. Read precomputed aggregate/control rows only. Return window,
epoch/rule, counts, full-precision estimates/bounds, thresholds, verdict reasons, effective stage,
transition history, completeness, diagnostic histograms and last successful collector time.
No caller can request arbitrary owner slices, raw SVG or manuscript-bearing receipt payloads.
An admin stage action requires the existing mutability check, expected control revision and a
bounded reason; re-enable also requires the offline receipt binding. Ordinary reads never evaluate
or promote. A new small admin route module avoids growing `admin/router.py` beyond its size budget.

Add `scripts/canvas_telemetry.py show|compare` invoked from `servers/fastapi` through `uv run python`.
It reads the admin snapshot over the existing authenticated same-origin API or a numeric JSON
snapshot supplied with `--input`, and reuses `scripts/canvas_bench_stats.py` to independently
recompute bounds from sufficient statistics. Extract shared pure statistics if backend import
layering requires it, preserving the script's existing public import as a facade. Backend and CLI
must share the exact same decision evaluator. CLI compare is read-only; expose re-enable only as
an explicit admin action, never as automatic CLI `promote verified` against offline data.

Policy defaults are versioned server constants, exposed with every evaluation. Add only the
maintenance-enabled/interval settings needed for operations through `utils/get_env` or a dedicated
`utils/env_settings` module and `constants/internal_env_names.py`. Do not add per-margin arbitrary
environment switches initially. Disabling maintenance freezes automatic decisions with a visible
disabled collector status; it does not hide existing alerts or silently wipe stages. Revision sync
must reach API, embedded worker and standalone worker processes without request-time file scans.

Privacy is an allowlist boundary, not a promise that existing receipts contain no text. Keep only
numeric counts/geometry summaries, fixed histograms, finite category enums, contract/checker digests
and private internal join identities in facts. Never retain prompts, slide/source prose, owned-text
probe fields, missing required strings, reference image bytes, URLs, filenames or exception messages.
Do not copy full `browser_metrics`, full QualityReceipt evidence or source-bearing hashes as public
API identifiers. Source/receipt digests used privately for binding are omitted from admin exports.
Aggregate account-diversity guards are computed in privileged maintenance scope; normal owner-scoped
queries cannot accidentally act as cross-owner collectors. Suppress diagnostic subgroups below
12 decks in the admin API. Follow account deletion by deleting identifiable telemetry facts and
recomputing affected windows; do not retain recoverable account references in transition snapshots.
No external telemetry upload, notification service or manuscript ingestion is part of this design.

Every stage transition and severe degradation stores an audit before it is effective and emits
structured logs using stable codes, canvas, epoch, rule, revision and numeric counts. The admin
endpoint carries active alerts until resolved. Restart reconstructs alerts from DB; a process crash
cannot turn a demotion into an unreported stage change. Optional external paging is outside scope.

## Delivery stages: files, size, tests and acceptance

All figures are implementation estimates, not code changed by this document. Split new modules
below 500 LOC. Tests listed are future targeted checks; no product tests were run for this design.

| Stage | Files and estimated production LOC | Focused tests | Acceptance |
|---|---|---|---|
| 1. Numeric receipt contract | New `models/canvas_telemetry.py` (~160), `services/svg_composition_receipt.py` (~220); SVG measurement probe/render reducer (~50); `drivers/svg/publish.py` (+35), `quality.py` (+15), `timing.py` (+20), collector hooks in `presentation_generation_attempt_timing.py` (+25) | New receipt/composition tests; existing SVG quality/publish/timing tests | Known square bottom-gap, transform/stroke, full background, image-only, missing role and digest mismatch fixtures produce versioned numeric receipts; publication and theme agree; no text-bearing fields; resumed attempts do not reset retries. |
| 2. DB collector and aggregation | New `models/sql/canvas_telemetry.py` (~160), Alembic revision (~90), `services/canvas_telemetry_store.py` (~260), `services/canvas_telemetry_aggregate.py` (~280), terminal/export hooks (+40), `services/canvas_telemetry_maintenance.py` (~160); `api/lifespan.py` (+12), env access/internal registry (+20) | New store/aggregate tests and relevant terminal/export/lifespan tests | Duplicate/replayed/fence-lost writes counted once; late export attaches correct revision; failure before publication counted; account deletion recomputes; two workers produce one cutoff decision; no SVG parsing in the query path. |
| 3. Gate authority and revisions | New `services/canvas_telemetry_gate.py` (~220), stage cache/coordinator (~150); `services/authored_canvas.py` (+35/-10), `constants/canvas_stages.json` migrated, `utils/authored_style_catalogue.py` (+25), strategy-aware admission/capability clients (~50) | `test_canvas_stage_gate.py`, `test_canvas_admission_baseline.py`, new gate/revision tests and existing canvas/picker tests | Exact pass/fail/hold equality cases; 47 decks or 287 pages hold; missing exporter proof holds; epoch resets verification; regression alerts/demotes; geometry mismatch disables; authored outcomes preserved; intended SVG capability flips enumerated; multiworker cache converges. |
| 4. Admin and CLI | New admin route (~120), registration (~3), `scripts/canvas_telemetry.py` (~170); shared stats facade/evaluator integration (~60), observability event extension (~20) | New admin authorization/CLI tests plus existing bench statistical tests | Admin denies non-admin, exports no prose or identifiers; CLI recomputes bounds identical to DB; transition provenance survives restart; read-only compare cannot change stages. |
| 5. Controlled enablement | Offline candidate bench adapter/rules (~45), rollout runbook and policy version (~30) | `test_canvas_bench.py` narrowed to affected promotion/epoch/retry cases | Offline verified cannot grant production verified; existing offline candidates remain exposed; shadow aggregate runs show measured completeness and comparable pools before enabling automatic transitions; first promotion references real production receipt counts. |

Stage 1 needs the export producer's actual geometry receipt integration agreed before stage 2:
if readback is not currently persisted, add the exporter/readback hook in its owning module and
one focused mismatch test. Do not certify with expected dimensions copied into an observed field.
An implementation must locate that producer before claiming the export minimum is achievable.
The document does not claim live acceptance, measured runtime cost or a green full suite.

## Risks and unresolved decisions

- Sample economics: 48/288 per arm in 14 days and 48 revision-bound exports may be unreachable for
  uncommon canvases. HOLD with an explanation is acceptable; lengthening the window changes drift
  sensitivity and must produce a new rule version. Never auto-request user exports to manufacture data.
- Tight margins: the closure plan's simulations motivate increasing from 24 to 48 decks for a 0.05
  page margin; they do not prove power for heterogeneous production traffic or export failures.
  With zero export failures at 48 per arm, the Wilson candidate upper bound is about 0.053, so
  export non-inferiority may still HOLD until roughly 52+ exports. The minimum is eligibility, not
  a promise that all zero-event samples pass. More data is preferable to quietly loosening a margin.
- Confounding and clustering: provider, content budgets, customer and role mix can dominate aspect
  ratio. The mix/account guards reduce obvious imbalance but are not randomization. Decide whether
  stratified standardization or customer-cluster bootstrap is needed after shadow data exists.
- Composition validity: boxes include decorative content; column inference fails on diagrams/tables
  and sparse pages. Calibrate empty-band, bbox and columns against labeled acceptable/unacceptable
  stored SVG layouts before making them promotion blockers. No universal ideal column count exists.
- Exposure semantics: verified -> candidate preserves availability. If users need automatic exposure
  removal for statistical degradation, choose candidate -> disabled explicitly with recovery semantics;
  this initial design instead reserves automatic disablement for confirmed geometry corruption.
- Global SVG gate can mask a bad style override. Monitoring makes that visible but does not establish
  style independence. Decide whether certain source-template/master styles should stay a separate
  capability domain rather than weaken adapter validation or restore per-style canvas stages.
- Privacy/diversity: four-account minimum prevents single-user local verification. Decide whether a
  separately named local certification should exist; do not call it production telemetry verification.
- Authored seeds currently inherited SVG/default bench exposure without authored measurement. Preserve
  behavior in migration, label provenance, and leave authored certification to its own evidence policy.
- Epoch ownership: settle the exact descriptor mapping and who approves generator/provider changes
  requiring epoch rollover. The proposed rule starts with the ink classifier but never infers it from
  the renderer version alone. Decide whether official classification-rule version constants already
  exist or must be introduced at their source.
- Operational policy: accept the 14-day window, 0.05 page/export margins, 48/288 minima, diversity and
  daily nominal bounds, or revise them explicitly before implementation. This design chooses defaults;
  they are not yet operator-approved thresholds. Decide external alert delivery separately.

## Alternatives rejected

| Alternative | Why not recommended |
|---|---|
| Keep offline verified as release authority | Measures synthetic default decks once; cannot detect later provider/runtime degradation. Keep it as a diagnostic artifact, not production verification. |
| Enable candidate first and use telemetry as the only gate | Candidate is already public. New disabled canvases still need offline candidate evidence before exposure; telemetry cannot observe an unexposed canvas safely by magic. |
| Rewrite packaged canvas_stages.json from each maintenance process | Creates divergent files/caches and unreviewable deployment changes; DB authority plus process revision cache gives atomic, audited transitions. |
| Periodic shared app_data JSON authority | Familiar local receipts, but public multiworker CAS, fencing and cross-host convergence require additional file infrastructure without existing authority benefits. |
| Full receipt/QualityReceipt-ledger scan on every admin query | Text-bearing payloads, disk/DB cost, hidden epoch ambiguity and nondeterministic latency. Compute compact composition at publish, aggregate periodically. |
| Preserve 0.15 verified margin at a 0.02 baseline | Admits large regressions relative to current ink-epoch failure prevalence; sample-driven tighter 0.05 recommendation is explicit. |
| Zero product failures required forever in a growing production pool | A single common infrastructure/provider incident permanently blocks a large healthy population. Use relative deck failure bounds, immediate visible alerts and a geometry safety override. |
| 45 style-by-canvas certification matrices or layout-signature inheritance | Multiplies sparse samples and encodes prompt equivalence as capability authority. Canvas policy is shared for SVG; preserve style diagnostics and mapping safeguards instead. |
| Declare palette/reference/override changes layout-neutral | Contradicted by mapped composition text and explicit overrides. Removing a gate axis cannot justify deleting those authoring inputs or claiming causal independence. |
| Universal composition failure thresholds | Penalizes intended sparse covers and ultrawide columns without acceptability labels. Start with numeric diagnostics and an explicit empty-band warning. |

## Self-score

**8.5/10 for implementation readiness.** The recommendation has concrete minima, window, thresholds,
promotion/demotion/staleness rules, transaction authority, privacy boundary and compatibility migration.
Evidence distinguishes shipped behavior, prior measurements and proposed policy. Remaining gaps are
production sample/power validation, composition-label calibration, exact export geometry producer
integration and explicit operator approval of statistical/exposure defaults. No score implies the
gate is implemented, that SVG layout is style-independent, or that live verification has passed.
