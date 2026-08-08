# SlideSpec scoped-evidence operations

## Status vocabulary

Each Chrome/DOM observation is an evidence atom: `proven_clean`,
`proven_issue`, `unknown`, or `not_applicable`. `unknown` is scoped to its
element, constraint, metric, stage, and dependency references; it never removes
a `proven_issue`. The legacy `telemetry_complete` value is derived only for
backward compatibility and is not a detection gate.

The review result reports these counters separately:

- `confirmed_issue_count`: deduplicated physical defects with positive proof.
- `incomplete_measurement_count`: scoped unknown measurements.
- `unresolved_confirmed_issue_count`: proven issues not post-render resolved.
- `clean_slide_count`: slides with neither confirmed issue nor incomplete measurement.

Detection continues when an unrelated sensor is unknown. Publication fails
closed only when an unknown belongs to the mutation target or its declared
dependency cone. Every `sampled=0` record must include qid, metric, stage, and
a concrete reason such as `no_sample_on_owned_paint`.

## On-call checks

1. Open the quality-review lifecycle and compare Detected, Incomplete
   measurement, Candidates, Rejected, Applied, and Remaining independently.
2. For an incomplete result, read qid/constraint/metric/stage/reason. Do not
   relabel it clean and do not use a global telemetry flag to suppress a proven
   defect.
3. If publish is blocked, confirm that each blocker intersects the planned
   target/dependency cone. An unrelated footer measurement must not block an
   otherwise safe repair.
4. Preserve the before/after values, changed properties, apply/publish/
   post-verify state, CAS receipt, and rollback state in the result before
   retrying. The kill switch routes to legacy behavior without dropping the
   diagnostic record.

## SLO and release evidence

The release dashboard must retain measured cold and warm p50/p95/p99, the
branch/unknown/candidate funnel, LLM/Chrome/acceptance/post-verify time, and
cache/token/cost by stage. Alert on a rise in incomplete measurements, a
confirmed issue reported as clean, or any publish that bypasses dependency-cone
blocking. Run the exact fixture replayer before release:

```powershell
python servers/fastapi/scripts/slidespec_active_bundle_replayer.py
```

The fixture pins presentation `50907f60-ea13-404c-90da-57f821404d74`, slide 5
(zero-based 4), bundle `a6470d97-658a-46e2-bf8f-86062d13e09c`, the three nested
raw overlap targets, and the unrelated footer unknown. Its byte digest check
prevents a replay from silently drifting away from the active input.

## 2026-08-05 live incident closure

The immutable historic report replays as `confirmed=1`, `incomplete=1`,
`unresolved=1`, `clean=0`: the unrelated footer unknown is retained without
removing the three proven overlap atoms. Fresh sensor v4 guarantees owned-paint
samples for that footer, so the same active HTML now reports `1/0/1/0`; the
unknown was resolved to `proven_clean`, not hidden.

The focused slide-5 replay proposed/rendered/accepted/rejected `9/9/0/9` plans.
Publication remained fail-closed because the common physical owner
`q-div-f17e611a8d0f` needs `73.65625px` downward travel but has only
`37.359375px` before footer `q-div-c6f2820b2605` at y=`690px`. Its section
owner has only `59.359375px`. Both alternatives have
`candidateContained=false`; the reason is
`repair.geometry.ancestor.physical_owner_no_safe_translation`. No slide-5
apply, publish, post-verify, or rollback was claimed.

Chrome UI task
`task-7815c1c48bb05f5cc19c8a6dc4c3d3ea42a559b7309948a13b26b9603d314c66`
reviewed all 20 slides. It persisted `confirmed=21`, `incomplete=77`,
`unresolved=20`, `clean=8`; one independent safe repair on slide 6 completed
apply/CAS publish/post-verify with rollback zero. Slide 5 remains detected and
unresolved. The UI exposes qid, constraint, operation, stage, metric, physical
owner, required/available displacement, containment, lifecycle, and rollback
state separately.

Final post-solver live dry-run latency and funnels:

| replay | cold | warm p50 / p95 / p99 | funnel |
|---|---:|---:|---|
| focused slide 5 | 27.836 s | 8.144 / 8.338 / 8.356 s | 9 proposed, 9 rendered, 0 accepted, 9 rejected; confirmed/unresolved 1/1 |
| new 20-slide deck | 61.520 s | 18.048 / 20.892 / 21.144 s | 26 proposed, 19 rendered, 0 accepted in shadow; confirmed/incomplete/unresolved/clean 20/77/20/9 |
| original 90-slide deck | 266.461 s | 79.604 / 111.930 / 114.803 s | 20 proposed, 15 rendered, solver unknown 0 |

The 90-slide warm p95 remains below the 210-second operating SLO. Its final
cold run included seven failed model calls and 67.537 seconds of OCR/VQA; those
provider failures are retained as measured stage usage rather than hidden from
the latency result. Token and cost fields remain absent where the provider did
not expose them; they are not synthesized as zero.

The 90-slide human-labeled replay remains the release-quality disposition
source: 18 false-positive/intentional-overlap corrections, 5 canonical executor
verifications, 0 pending, and 90/90 source fingerprints unchanged. The raw
sensor benchmark is retained separately and must not overwrite those reviewed
dispositions.

The final live human-labeled replay repeated all 23 dispositions twice and
reported `release_passed=true`: 18 corrections, 5 executor verifications, 0
pending, and 90/90 source fingerprints unchanged. Its cold/warm deck totals
were 251.536/130.162 seconds.

After the successful slide-6 publication, a restored presentation version left
20 active-pointer JSON values whose immutable bundle rows had been cascade
removed. The standard importer recovery path was exercised with an online
SQLite backup first. It CAS re-imported 20/20 live HTML baselines, failed zero,
preserved all HTML/content fingerprints, and restored 20/20 loadable bundles.
The historic fixture continues to pin bundle
`a6470d97-658a-46e2-bf8f-86062d13e09c`; the recovered live slide-5 pointer is
`e73d7e98-a7c1-4528-a9e6-e2b86fa1576c`.

The seeded mixed solver now prefers the satisfied separation with the largest
slack. This removed infeasible 2048-sweep first branches without changing the
constraint set or bounded-search semantics: p50/p95/p99 is
7.774/17.581/17.581 ms, every fixed case uses one branch, and unknown remains
zero.

Operational rehearsal used a temporary SQLite copy outside the repository:
upgrade to `fc3d5e7f9a1b`, downgrade to `fb2c4d6e8a0b`, then re-upgrade to
`fc3d5e7f9a1b`. Repository-standard services are active on 3000/5000/8000;
`/dashboard` and direct/proxied `/api/v1/auth/status` return HTTP 200. Default
routing is `slidespec_active`; `SLIDESPEC_KILL_SWITCH=1` yields
`legacy/system.kill_switch_enabled` and disables publication.

Final validation: FastAPI `2468 passed, 16 skipped` (exit 0), the authored
quality Cypress component `9 passed`, Node/Chrome CI `277 passed`, ESLint and
the 31-route Next production build passed. The Windows pytest atexit cleanup
reported a pre-existing access warning for the shared `pytest-current` temp
link after the successful exit; no test failed because of it.
