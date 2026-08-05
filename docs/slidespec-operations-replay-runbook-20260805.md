# SlideSpec priority replay operations runbook

Status: implementation-ready; live measurements are pending. This document does not treat fixture-contract output as production performance evidence.

## Scope and safety boundary

The priority set is slides 5, 8, 9, 10, 12, 13, 16, 19, 20, 26, 34, 35, 37, 46, 47, 52, 57, 60, 69, 71, 72, 82, and 83. The human-review fixture is [slidespec_human_labeled_replay.json](../servers/fastapi/tests/fixtures/slidespec_human_labeled_replay.json).

Codex performed a manual same-viewport review of the recorded 1280×720 montage. This is human-verifiable evidence, not external human signoff; `external_human_signoff=false` is recorded in the fixture. Eighteen hits are corrected false positives or intentional overlap. Owned-paint Chrome evidence establishes five true positives (slides 9, 19, 26, 47, and 72); each remains unresolved until canonical executor evidence proves candidate, apply, and post-verify. A correction must preserve the source: it is not an applied repair, it has no candidate, no changed attributes, no rollback, and no remaining defect.

The fixture stores no customer text. It records only a stable fixture element reference, raw detector metric, disposition, and human rationale. Live evidence includes only digests, timing, detector categories, and rendered PNGs in an operator-chosen private directory.

## Commands

Run from `servers/fastapi` after configuring the normal private `APP_DATA_DIRECTORY` and database environment.

```powershell
python scripts/slidespec_human_labeled_replayer.py --validate

python scripts/slidespec_human_labeled_replayer.py `
  --live-db `
  --presentation-id <presentation UUID> `
  --runs 10 `
  --output-dir C:\project\PPT-agent\.codex_tmp\slidespec-replay-<timestamp> `
  --executor-evidence-json C:\private-evidence\canonical-executor-result.json
```

The live command reads active SlideSpec artifact bundles for all 23 positions, captures a Chrome baseline, records the 18 human false-positive corrections without changing their sources, and captures a fresh Chrome post-render. Without `--executor-evidence-json`, all five true positives are reported as executor-evidence-pending, never as synthetic applied repairs. It writes `benchmark.json` and baseline/post-render PNG pairs under the explicit output directory. Do not use a repository-tracked directory or `.local-runtime-logs` for evidence.

`--execute-repairs --allow-write` is deliberately double-gated because the production executor can publish candidates. With both flags and `--live-db`, the replayer performs the standard `userConfig` bootstrap, re-imports every active-pointer slide in the deck with importer v4, then calls the canonical executor with `dry_run=false` for only the five true-positive positions. It writes the resulting read-only canonical export to `<output-dir>/canonical-executor-evidence.json`, then validates that export as part of `benchmark.json`. Do not use those flags without a change-authorized run.

Use `--user-config <private userConfig.json>` only for this write path when the normal `USER_CONFIG_PATH` environment is not already set. Configuration and credentials are never written to replay evidence.

## Canonical executor evidence input

The replayer accepts only this content-free schema: `slidespec-canonical-executor-evidence/v1`. It validates every true positive separately; a partial file leaves only the unproven slides unresolved.

```json
{
  "schema": "slidespec-canonical-executor-evidence/v1",
  "slide_results": [{
    "slide_index": 8,
    "candidate_count": 1,
    "accepted": true,
    "applied": true,
    "publish_status": "active",
    "post_verify_status": "passed",
    "remaining_issues": [],
    "target_evidence": [{
      "qid": "fixture-element-id",
      "constraint_id": "canonical-constraint-id"
    }]
  }]
}
```

For each of slides 9, 19, 26, 47, and 72, the entry must have a positive `candidate_count`, `accepted=true`, `applied=true`, `publish_status="active"`, `post_verify_status="passed"`, an empty `remaining_issues` array, and a nonempty `constraint_id` for the fixture target qid. Where owned-paint evidence names additional physically occluded text or glyph elements, each of those qids also needs a target-evidence entry. `benchmark.json.executor_evidence` lists validated and unresolved slide indices with reasons. The replayer never infers these facts from expected fixture fields, an executor path, or aggregate counters.

## Result contract for the review UI

Each slide result must distinguish these lifecycle facts; they must never be inferred from a path alone.

| UI bucket | Required evidence |
|---|---|
| Detected | raw detector reason / metric and target element reference |
| Corrected false positive | `corrected_false_positive=true`, human rationale, `source_unchanged=true` |
| Candidate | `candidate_count > 0`, proposal was attempted, path, before/after, changed properties |
| Rejected | acceptance reason and candidate evidence |
| Applied | apply, publish, post-verify, and rollback state |
| Remaining | named unresolved issue; corrected false positives must not appear here |

For candidate repairs the API should supply `candidate_count`, `candidate_rejections[].reason_codes`, `rejection_reason` (including no-candidate/preflight cases), `detected_issues`, `element_changes[].before`, `.after`, and `.changed_properties`, plus `apply_status`, `publish_status`, `post_verify_status`, and `rolled_back`. For corrected detector hits it should supply `corrected_false_positive`, `correction_reason`, and zero remaining issues. This is implemented as backward-compatible frontend normalization; the backend contract should adopt these field names rather than relying on UI inference.

## Observability and dashboard contract

`metrics.latency_percentiles` must contain a separate `cold` and `warm` object. Each uses named stages such as `deck_total`, `llm`, `chrome`, `acceptance`, and `postverify`, with `p50_ms`, `p95_ms`, `p99_ms`, and `samples`. `metrics.stage_usage` is keyed by the same operational stage and may include `input_tokens`, `output_tokens`, `total_tokens`, `cost_usd`, `cache_hits`, and `cache_misses`.

The UI renders this data only when measured. Missing LLM/token/cost values for the current false-positive correction run are expected, because it does not call an LLM. It must show no invented zero-cost or latency value.

Live SLO values are pending the ten-run replay report. Set or revise an SLO only from `benchmark.json` measurements. Until then, the release gates are qualitative: 23/23 human dispositions captured, 18 corrected false positives treated as source-preserving (not applied) outcomes, all five true positives either explicitly pending evidence or validated by the canonical contract, no source mutation for corrections, and a successful fresh Chrome post-render for every active-bundle slide.

## Evidence review checklist

1. Confirm `benchmark.json` has 23 slide results, one cold and at least one warm sample.
2. For every result, compare baseline and post-render PNG digest/evidence; a correction should have `source_unchanged=true` and no publication state. For every true positive marked verified, inspect its `qid` + `constraint_id` entries and lifecycle fields in `executor_evidence`. Obtain external human signoff separately if it is required for release.
3. Inspect `detector_categories` together with the human disposition. A raw detector count alone is not a confirmed defect.
4. Verify all expected image pairs exist in the private output directory and no content-bearing JSON field was emitted.
5. Use a separate, change-authorized pipeline run before enabling a repairable fixture's executor path.

## Measured production replay — 2026-08-05

The authorized write replay completed against presentation
`15f91f1d-66d7-491d-abbb-c02481358b96` with `release_passed=true`. Canonical executor
evidence validates all five true positives; all 18 false positives are source-preserving
corrections, and unresolved is zero. The authored source changed only on one-based slides
9, 19, 26, 47, and 72.

The read-only evidence replay ran ten times. Cold deck total was 57.151 s; warm
p50/p95/p99 were 57.147/87.917/94.786 s. A second whole-deck benchmark ran all 90 slides
once cold and nine times warm: cold 113.734 s and warm p50/p95/p99
11.291/12.472/12.725 s. It made zero LLM calls, reported zero remaining issues and zero
solver unknowns, launched Chrome once for the cold run and reused that session 89 times.

Private evidence is intentionally outside the repository under
`C:\project\PPT-agent\.codex_tmp\slidespec-production-20260805`. Do not copy its PNGs,
database snapshots, user configuration, or canonical evidence JSON into a commit.

The final local service check used the repository-standard topology: Next production on
3000, the local proxy on 5000, and FastAPI on 8000. `/dashboard` and
`/api/v1/auth/status` returned HTTP 200. Default SlideSpec routing was
`slidespec_active`; the kill switch produced `legacy/system.kill_switch_enabled` and
disabled publication.
