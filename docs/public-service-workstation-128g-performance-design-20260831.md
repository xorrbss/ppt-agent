# Public PPT service — 128GB workstation capacity and performance design

Status: design only, not implemented, not performance-verified  
Target: public PPT generation and Fidelity PPTX/PDF export  
Hardware baseline: one high-performance Windows workstation with 128GB RAM  
Architecture authority: FastAPI PPT Core, role-specific same-codebase workers, PostgreSQL operation/outbox/lease authority

## 1. Outcome

The workstation is a single execution node, not an unlimited cluster. The runtime must use an explicit `workstation-128g/v1` profile whose admission limits are derived from executable worker slots and provider capacity. A database policy value such as `global_active_generation=20` must never admit more work than the node can execute before its deadline.

Four concurrent decks are a verification tier, not the service ceiling. Capacity advances by sealed evidence:

| Capacity | Verification | Service default | Normal target | Validated burst maximum |
|---|---:|---:|---:|---:|
| generation decks | 4 active | **8 active** | **12 active** | **16 active** |
| five-slide-equivalent queue | 16 | 32 | 48 | 64 |
| render/export operations | 4 active | 8 active | 8 active | 12 active |
| owner generation | 1 active, 3 queued | unchanged | unchanged | unchanged |
| owner export | 1 active | 2 active, 6 queued | unchanged | unchanged |
| PowerPoint COM | 1 active | 1 active | 1 active | 1 active |

Queue limits are slide/resource work units, not raw deck counts. A 30-slide deck cannot consume the same queue unit as a five-slide deck. Service default 8 becomes eligible after same-RC C5 evidence, normal target 12 after C10 evidence, and burst 16 only after a separate C16 peak observation plus 24-hour resource recovery. Sustained demand above 16 generation decks moves to a separate generation worker node instead of raising the single-node cap.

## 2. Binding improvement and release order

This sequence is authoritative. Performance workstreams in later sections may be implemented in parallel, but release evidence is collected in this order and cannot skip a failed predecessor.

### 2.1 Freeze a new clean RC and revalidate one five-slide product path

At design inspection time, repository HEAD was `2800b3cf1c6acdb8219b0b8db8dfe2eb7888e142`, while the retained successful run32 evidence was bound to source commit `0dbc8ea01c7512f8acddfed213be01c2d1d50e09`. That evidence cannot promote the current source.

The new RC procedure is:

1. finish only the scoped code changes required by priorities 2–5;
2. create a clean candidate worktree without deleting or resetting the shared dirty checkout;
3. seal source commit, source manifest digest, `workstation-128g/v1` profile digest, provider/model/pricing snapshot, font set, renderer/exporter identities, and DB policy revision;
4. run exactly one real five-slide path: API → provider → generation → Chromium review → publication → Fidelity PPTX/PDF → authorized 303 and 206 download;
5. retain success and failure timing, provider, worker, DB, artifact, and download evidence;
6. stop without a broad matrix if this path fails or any required field is null.

No historical success artifact, fixture, isolated seam, or different commit may fill this gate.

### 2.2 Improve deterministic title recovery without weakening gates

Protected words, numbers, units, and QID ownership remain exact. The recovery state machine is:

```text
capacity plan before provider call
  → one constrained author call
  → exact protected-token/QID and capacity validation
  → higher-capacity approved title layout substitution
  → deterministic shortening of non-protected text only
  → pass or terminal authored_title_capacity_unresolvable
```

Design requirements:

- compute `jointTitleBudget`, fixed title characters, non-title visible characters, and per-QID capacity before the first model request;
- freeze each protected token occurrence to its original QID and preserve exact numbers, signs, units, and normalized token order;
- include the exact remaining title budget and protected-token ledger in the author contract;
- never solve a mismatch by loosening the protected-token or numeric gate;
- if the first result does not fit, prefer a compatible layout with a larger title slot while preserving semantic role and reading order;
- if no layout fits, apply a deterministic Korean/English shortening table only to unprotected phrases; do not issue another probabilistic generation call;
- if the protected content itself cannot fit, fail before publication with a bounded diagnostic and no artifact;
- persist recovery step, input/output character counts, QIDs, protected-token digest, chosen layout, and terminal reason.

The focused acceptance corpus must cover protected Korean terms, English product names, dates, percentages, currency, version numbers, and mixed tokens. The title gate remains fail-closed with zero protected-token/QID mutation.

### 2.3 Restore LibreOffice runtime, then retest the exact same PPTX

LibreOffice environment repair is separated from document repair:

1. resolve and record the actual `soffice` executable, version, architecture, font inventory, and runtime dependencies;
2. terminate only invocation-owned processes and use a unique isolated user profile per file;
3. run a known-good control PPTX to classify the environment as available before testing the RC artifact;
4. reopen the exact RC PPTX bytes without regeneration or PowerPoint save-copy substitution;
5. verify package readability, slide count, source digest unchanged, output creation, and supported visual/readback checks;
6. classify `environment_unavailable`, `invocation_failed`, and `document_invalid` separately.

Only the same-file successful readback can raise cross-renderer readiness. Repairing LibreOffice does not by itself prove visual parity.

### 2.4 Complete durable generation-stage observability

Both success and failure operations persist these stages as numeric durations:

| Stage | Start boundary | End boundary |
|---|---|---|
| outline | provider reservation for outline | validated outline receipt |
| provider | first external provider I/O | final provider receipt or terminal error |
| authoring | slide author dispatch | authored HTML accepted or rejected |
| repair | first bounded recovery action | repaired validation or terminal failure |
| Chromium review | isolated render/review claim | quality decision receipt |
| publication | fenced publication transaction begins | ArtifactRef and result event committed |

Every stage records attempt number, queue wait, wall time, provider logical calls, retry reason, cost/token availability, worker identity, and terminal error code. A stage that did not execute is explicit `not_started`; an executed stage with `null` timing fails the RC gate. Failed operations retain the timing collected before failure.

### 2.5 Separate artifact quality from service promotion and deployment

API and UI expose four independent states:

| State | Meaning | Can become true from one file? |
|---|---|---|
| `fidelityArtifactReady` | exact Fidelity artifact passed required file review and is authorized for download | yes |
| `nativeEditabilityReady` | Hybrid native coverage and PowerPoint/LibreOffice readback are sealed | yes, for that Hybrid artifact |
| `promotionReady` | same-RC product, matrix, fault, soak, provenance, and signature gates passed | no |
| `deploymentAuthorized` | an operator approved production activation | no |

Compatibility `releaseReadiness=ready` is scoped only to the individual Fidelity artifact and must carry `readinessScope="artifact:fidelity"`. It cannot imply native editability, service promotion, or deployment. UI renders four separate badges and never collapses them into one “ready” status.

### 2.6 Complete editable Hybrid separately if it is a product requirement

The current public v2 orchestrator rejects `editable-hybrid` and required capabilities with HTTP 422 `capability_not_supported`. Fidelity remains the public mode until Hybrid has its own completed contract.

Hybrid design requirements:

- separate capability/profile and promotion evidence from Fidelity;
- native text, shapes, tables, and charts retain QID, reading order, and semantic ownership;
- unsupported visual fragments use an explicit, reported raster island rather than silently converting the entire slide;
- PowerPoint first-open/save-copy/reopen and LibreOffice same-file readback are both sealed;
- native coverage, unexpected fallback, font substitution, repair dialog, and QID survival are explicit gates;
- `nativeEditabilityReady=false` does not block a valid Fidelity download, but the UI must not advertise editable output;
- no request may mix Fidelity readiness with Hybrid promotion evidence.

### 2.7 Run the operational matrix last

Only after the new RC five-slide path and same-file LibreOffice readback pass may the release lane run:

1. external role-separated PostgreSQL, S3-compatible artifact authority, and production worker identity;
2. C1 → C2 → C5 with the 4/4/8 generation tiers;
3. C10 with the 12-slot candidate and a separate C16 burst observation for the 16-slot maximum;
4. cold/warm 5/10/20/30-slide cells;
5. worker termination, DB outage, object-store outage, and provider failure recovery;
6. 86,400-second soak with at least 1,440 samples;
7. detached collector signatures and independent PowerPoint/LibreOffice readback evidence;
8. promotion decision, followed by a separate human deployment authorization.

Any product-path, LibreOffice, integrity, duplication, ownership, timing-null, or resource-recovery failure stops expansion and invalidates later-cell promotion claims.

## 3. Assumptions and boundaries

Confirmed:

- the production hardware baseline has 128GB RAM;
- Windows local supervised worker transport is used without Docker;
- PostgreSQL remains the durable queue and state authority;
- public browser export is Fidelity PPTX/PDF;
- generation, render, export, and API run as separate process roles.

Still required from the target workstation inventory:

- physical/logical CPU count and sustained all-core performance;
- GPU model and VRAM, if any local model or acceleration uses it;
- NVMe capacity, free-space floor, and measured sequential/random I/O;
- whether PostgreSQL runs on the same workstation;
- provider account RPM/TPM/concurrency and cost limits;
- expected peak arrival rate and average slide count.

Until these are measured, RAM is the only confirmed hardware input. The profile therefore starts conservatively and scales from evidence, not from theoretical memory capacity.

## 4. Workstation resource budget

The 128GB total is partitioned as a policy budget, not preallocated memory:

| Budget | GiB | Purpose |
|---|---:|---|
| Windows, security tools, PowerPoint | 20 | OS and non-service processes |
| PostgreSQL and DB filesystem cache | 16 | authority, indexes, WAL, connection work memory |
| FastAPI, controllers, metrics, local cache | 8 | control plane and bounded caches |
| generation/render/export workers | 64 | supervised process trees |
| emergency headroom | 20 | bursts, fragmentation, crash recovery |
| total | 128 | |

Pressure thresholds:

| Signal | Soft pressure | Hard pressure | Runtime action |
|---|---:|---:|---|
| available memory | below 24GiB for 60s | below 16GiB for 15s | stop slot expansion; then stop new active admission |
| CPU | above 80% for 5m | above 90% for 60s | reduce new claims; keep leases/heartbeats alive |
| system volume free | below 20% | below 10% or 100GiB | stop cache growth; reject artifact-producing work |
| worker recycle | RSS/handles do not return within 10% of baseline | process tree remains after deadline | quarantine and replace worker |

Hard pressure must not cancel healthy work merely to improve latency. It prevents new claims, preserves heartbeats, and lets the current bounded operations converge or reach their immutable deadlines.

## 5. Performance workstream A — versioned hardware-aware runtime profile

### Problem

Concurrency is currently spread across migration defaults, process constants, scheduler constants, and launcher environment values. A 128GB workstation cannot be tuned safely when these values drift independently.

### Design

Introduce a single immutable profile resolved before API/controllers start:

```json
{
  "profileVersion": "workstation-128g/v1",
  "minimumPhysicalMemoryGiB": 120,
  "apiProcessCount": 2,
  "generationControllerCount": 4,
  "generationConcurrencyPerController": 2,
  "exportControllerCount": 2,
  "exportConcurrencyPerController": 4,
  "generationVerificationActive": 4,
  "generationActive": 8,
  "generationNormalTarget": 12,
  "generationValidatedBurstMaximum": 16,
  "generationQueuedFiveSlideEquivalent": 32,
  "exportActive": 8,
  "exportQueued": 24,
  "renderPerPresentation": 2,
  "powerPointComConcurrency": 1,
  "workerMemoryBudgetGiB": 64,
  "minimumAvailableMemoryGiB": 20
}
```

Activation rules:

1. Require explicit `PPT_RUNTIME_PROFILE=workstation-128g/v1`; do not infer production from machine RAM.
2. Preflight observed RAM, CPU inventory, disk free space, DB role URLs, object-store authority, and provider-limit snapshot.
3. Fail closed if the profile cannot be satisfied; do not silently fall back to the historical global active value 20.
4. Seal the resolved profile digest into each operation receipt and release evidence.
5. Keep development/local defaults unchanged unless the profile is explicitly selected.

### Performance effect

- eliminates idle capacity caused by a worker cap lower than admission;
- prevents queue growth caused by admission higher than executable capacity;
- makes every performance result reproducible against one profile digest.

## 6. Performance workstream B — admission, backpressure, and scheduler alignment

### Problem

The database can allow 20 active generation/export operations while one runner process executes two operations and the trusted executor currently exposes four global slots. This creates active-but-not-running work, inflated deadlines, and misleading queue state.

### Design

Compute active capacity from the lowest real bottleneck:

```text
generation_slots = min(
  policy_generation_cap,
  generation_controller_count × concurrency_per_controller,
  provider_concurrency_budget,
  memory_pressure_cap,
  cpu_pressure_cap
)

export_slots = min(
  policy_export_cap,
  trusted_executor_global_cap,
  export_controller_count × concurrency_per_controller,
  memory_pressure_cap,
  cpu_pressure_cap
)
```

Queue admission also estimates whether an operation can start and finish before its immutable deadline:

```text
predicted_start = queue_work_units / observed_service_rate
admit only if predicted_start + p95_service_time <= 0.8 × deadline
```

Service scheduler caps:

| Cap | verification | service default | normal target | validated burst |
|---|---:|---:|---:|---:|
| generation global/node | 4 | 8 | 12 | 16 |
| generation tenant | 4 | 8 | 12 | 16 |
| generation owner | 1 | 1 | 1 | 1 |
| render/export global/node | 4 | 8 | 8 | 12 |
| render/export tenant | 2 | 4 | 4 | 6 |
| render/export owner | 1 | 2 | 2 | 2 |
| presentation | 1 | 2 | 2 | 2 |

Additional rules:

- retain owner-fair weighted scheduling and starvation prevention;
- return 429 with a measured `Retry-After` instead of unbounded queueing;
- keep generation and export resource units independent so Chrome pressure does not consume provider slots;
- treat soft memory/CPU pressure as a temporary capacity reduction, not as a policy migration;
- publish `configured_slots`, `effective_slots`, and each limiting reason.

### Performance effect

- bounds queue wait and deadline failures during bursts;
- uses eight service generation slots, with evidence-gated expansion to twelve and sixteen, without sacrificing owner fairness;
- turns 429 into controlled overload behavior instead of random timeouts.

## 7. Performance workstream C — generation and provider path acceleration

### Problem

Generation is expected to dominate end-to-end latency. More RAM cannot remove provider RPM/TPM limits, long model calls, retry storms, or repeated work.

### Design

1. Maintain one deck outline request and the accepted one initial author call plus at most one explicit recovery per slide.
2. Use a provider/model weighted semaphore based on immutable RPM, TPM, concurrency, and cost snapshots.
3. Use four decks only for initial verification, eight for service default, twelve after C10, and sixteen only after targeted peak/soak evidence; keep author concurrency within a deck at two.
4. Separate illustration calls from author calls so image latency cannot block text-only slide publication.
5. Apply bounded exponential backoff with jitter for retryable 429/5xx; do not retry validation, quota, or deterministic quality failures.
6. Reuse only immutable, owner-safe inputs such as template assets, fonts, and content-addressed static resources. Provider response caching is not an authority and cannot bypass receipt/cost accounting.
7. Persist non-null queue, outline, author, illustration, retry, token, and cost timing for every attempted operation.
8. Prefer quality-preserving prompt/context reduction before selecting a smaller model; model changes require a separate quality comparison.

Performance budget:

| Path | 5 slides p95 | 10 slides p95 |
|---|---:|---:|
| queue wait at C5 | 30s | 60s |
| outline + author + illustration | 120s | 250s |
| quality/publication | 15s | 25s |
| generation subtotal | 150s | 320s |

The budget is a target, not current evidence. Actual provider limits can reduce effective generation slots below the configured 8/12/16 tier.

## 8. Performance workstream D — render/export concurrency and warm-path optimization

### Problem

Chromium and export subprocesses create process trees, memory peaks, font initialization, and scratch I/O. PowerPoint COM is not safe to treat as an eight-way parallel worker.

### Design

- raise the trusted render/export global and node caps from four to eight only under the selected workstation profile;
- retain `PUBLIC_RENDER_MAX_CONCURRENCY=2` inside one presentation;
- allocate at most 2GiB, 32 PIDs, and the existing wall-clock/output limits to each isolated render job;
- prewarm bounded Chromium/font state in the worker, while keeping generated HTML network-none;
- recycle a worker after a configurable deck count or when RSS/handle recovery misses baseline;
- separate render, OOXML/PDF packaging, and PowerPoint COM verification queues;
- serialize COM at one operation and keep it out of the interactive export critical path unless the contract explicitly requires it;
- cache slide raster/font/static inputs only by digest with byte budget and TTL;
- avoid routing signed downloads through FastAPI after artifact publication.

Performance budget:

| Export path | warm p95 | cold p95 |
|---|---:|---:|
| five-slide render + PPTX | 20s | 35s |
| five-slide render + PDF | 25s | 40s |
| authorized redirect | 200ms | 500ms |

The observed single local render peak near 232MB does not justify multiplying slots by RAM alone. The 2GiB per-job limit remains the safety calculation, so eight concurrent jobs reserve up to 16GiB of the 64GiB worker budget.

## 9. Performance workstream E — PostgreSQL connection and queue-path tuning

### Problem

The default SQLAlchemy pool is five persistent plus ten overflow connections per process. Role-specific API/controllers multiply this total, and the default 30-second pool wait can hide saturation as request latency.

### Design

Initial role budgets:

| Role | processes | pool | overflow | maximum |
|---|---:|---:|---:|---:|
| API | 2 | 4 | 4 | 16 |
| generation controller | 4 service / 8 burst | 2 | 1 | 12 service / 24 burst |
| export controller | 2 | 3 | 2 | 10 |
| observation/maintenance | 1 | 2 | 2 | 4 |
| reserved migration/operator | — | — | — | 10 |
| total budget | | | | 52 service / 64 burst |

Rules:

- set pool timeout to 5–10 seconds and emit an explicit saturation metric;
- keep API and controller PostgreSQL roles separated as already required;
- use PgBouncer transaction pooling only when process/connection measurements justify it; PostgreSQL remains operation authority;
- index ready-operation selection, owner/tenant queue position, lease expiry, and artifact identity using actual query plans;
- add bounded jitter to one-second controller polling to avoid synchronized scans;
- preserve lock order and fencing; performance tuning cannot weaken admission → operation → resource lease order;
- consider a broker only at the existing thresholds: claim p95 above 100ms, ready queue persistently above 10,000, metadata transaction degradation, or multi-region deployment.

Targets:

- operation claim p95 below 100ms;
- DB pool checkout p95 below 50ms and timeout rate 0%;
- API status/event query p95 below 200ms at C10;
- total application connection use below 70% of the configured PostgreSQL limit.

## 10. Performance workstream F — durable artifact and download fast path

### Problem

A high-performance workstation is still one failure domain. Local artifact authority makes worker restart, disk pressure, multiple API processes, and large downloads compete with generation/export.

### Design

1. Use S3-compatible content-addressed object storage as production authority; keep local files as a bounded cache only.
2. Publish with digest, owner, source revision, operation fence, media type, and byte length in one fenced DB transition.
3. Return authorized 303 signed redirects and support 206 range download; do not proxy complete PPTX/PDF bodies through the API by default.
4. Use multipart upload only above a measured size threshold; small deck artifacts remain single PUTs.
5. Keep an 8GiB local cache with a 15-minute TTL initially, separated from worker scratch.
6. Apply cache single-flight by artifact digest and never use cache presence as publication authority.
7. Place DB WAL/data, worker scratch, and artifact cache on measured storage paths so one workload cannot consume all I/O or free space.

Targets:

- object-store PUT p95 below 2s for normal PPTX/PDF artifacts;
- signed redirect p95 below 200ms;
- cache-hit lookup p95 below 20ms;
- API egress bytes for artifact bodies approximately zero in the redirect path;
- duplicate publication/export identity zero.

## 11. Performance workstream G — observability, stepped qualification, and activation

### Problem

The retained successful public evidence is a development C1 product path from a different source commit with `promotionReady=false`; historical isolated or one-slide C2 results do not prove the new RC or this profile.

### Design

Every operation must retain:

- profile digest, configured/effective slots, and limiting reason;
- queue depth, position, wait, and oldest age;
- provider calls, retries, token/cost usage, and each stage latency;
- DB pool wait, claim latency, lock retry, and polling load;
- Chrome/worker peak RSS/private bytes, CPU, PID, handles, recycle result;
- artifact/cache/object-store/download latency and bytes;
- `fidelityArtifactReady`, `nativeEditabilityReady`, `promotionReady`, `deploymentAuthorized`, and compatibility readiness scope;
- title budget, protected-token/QID ledger, recovery step, and terminal reason;
- LibreOffice executable/runtime identity and same-file digest outcome;
- terminal DB state, fence, publication identity, and immutable evidence digest.

Qualification proceeds on one clean frozen RC, one source manifest, and one profile digest:

1. **Gate A — title and stage contract:** focused title corpus passes with zero protected-token/QID mutation and all executed stage timings non-null.
2. **Gate B — C1 correctness:** with the four-slot verification tier, one real five-slide API → PostgreSQL → provider → generation → Chromium review → Fidelity PPTX/PDF → artifact → 303/206 path succeeds.
3. **Gate C — same-file cross-renderer:** the Gate B PPTX passes the restored LibreOffice runtime and independent PowerPoint readback without changing source bytes.
4. **Gate D — state separation:** API and UI independently project Fidelity, native editability, promotion, and deployment states; `releaseReadiness` is artifact-scoped only.
5. **Gate E — C2/C5 service capacity:** run the eight-slot candidate and prove at least 95% success, no starvation, five-slide generation→PPTX p95 at or below 180s, and queue wait p95 at or below 30s. Passing makes eight the service default.
6. **Gate F — C10 normal capacity:** run the twelve-slot candidate over cold/warm 5/10/20/30-slide cells. Passing makes twelve the normal target.
7. **Gate G — burst, fault, soak, and activation:** observe C16 separately before enabling the sixteen-slot maximum, run four fault classes and the 86,400-second soak with at least 1,440 samples, collect signatures, derive promotion, then require separate human deployment authorization.

If editable output is included in the promoted product claim, its separate Hybrid PowerPoint/LibreOffice/native-coverage gate must pass before Gate G promotion. A Fidelity-only release may leave Hybrid rejected with `nativeEditabilityReady=false`, but must not advertise it.

Stop conditions for each step:

- success rate below 95%;
- title protected-token, number, or QID mutation is non-zero;
- a required executed stage timing is null;
- the control PPTX or same RC PPTX cannot be read by the restored LibreOffice runtime;
- provider 429 above 1% of logical calls outside an intentional fault run;
- admitted operation reaches deadline;
- available memory below 20GiB;
- worker process tree or handles fail to recover;
- DB claim p95 exceeds 100ms or pool timeout occurs;
- publication/export duplication or cross-owner access is non-zero.

## 12. Implementation boundaries

The implementation should touch only the following bounded areas:

- a versioned public runtime-profile resolver and schema;
- `scripts/start-public-v2-rc.ps1` profile preflight and role process counts;
- workspace runner concurrency resolution;
- trusted executor scheduler caps and per-presentation limits;
- title planning/recovery contracts and deterministic layout/shortening policy;
- generation execution receipt timing for success and failure paths;
- the LibreOffice readback collector/runtime preflight, without changing artifact bytes;
- public v2 API readiness contracts and four independent UI status projections;
- a separately gated Hybrid capability path if editable output is in scope;
- DB-backed public policy revision for workstation limits;
- role-specific DB pool settings;
- metrics/evidence fields required by this document;
- production object-store/cache/download configuration.

Historical migrations are not edited in place. A new policy revision/migration carries changed production defaults, and local/test profiles retain their existing behavior.

## 13. Delivery order

| Order | Deliverable | Narrow completion evidence |
|---:|---|---|
| 1 | finish scoped changes, resolve `workstation-128g/v1`, and freeze a clean RC | profile resolution plus exact source/profile manifest |
| 2 | pre-call title budget, protected QID binding, layout substitution, deterministic shortening | one focused title recovery corpus |
| 3 | LibreOffice environment recovery and same-file reopen | one control file and exact RC PPTX readback |
| 4 | durable outline/provider/author/repair/review/publication timings | one success and one bounded failure receipt |
| 5 | four readiness states in API and UI | one focused contract/UI projection check |
| 6 | separate Hybrid completion only when editable output is required | one native text/table/chart plus PowerPoint/LibreOffice sealed path |
| 7 | eight/twelve/sixteen capacity tiers, DB/S3/worker, fault, matrix, soak, signatures | actual same-RC product-path evidence only |

No broad matrix is run while Gate A has a product-path code blocker. Each implementation boundary receives the narrowest directly relevant verification first.

## 14. Expected impact

If the service profile passes C5:

- generation execution capacity rises from one default runner's two operations to eight service deck operations;
- same-RC C10 evidence can activate twelve normal slots, while sixteen remains a separately validated burst maximum;
- render/export scheduling rises from four to eight node slots while retaining per-owner and per-presentation caps;
- bursts become measured queue/429 behavior rather than uncontrolled deadlines;
- downloads stop competing with API and generation process bandwidth;
- DB connection use remains bounded despite role-specific processes;
- performance claims become tied to a hardware/profile/RC digest.

Planning throughput at 150 seconds per five-slide deck and 70% target utilization is approximately:

| Active generation decks | Stable planning throughput |
|---:|---:|
| 8 | 134 decks/hour |
| 12 | 202 decks/hour |
| 16 | 269 decks/hour |

These values are capacity planning arithmetic, not measured service results. Provider RPM/TPM/cost limits can lower them even when CPU and memory remain available.

These are design outcomes, not measured results. The current service score does not increase until the implementation and corresponding same-RC evidence exist.
