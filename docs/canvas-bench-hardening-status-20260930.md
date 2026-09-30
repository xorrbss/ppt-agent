# Canvas bench hardening and portrait gate status (2026-09-30)

Status: W4 implementation of closure-plan sections 2, 4 Phase A, and 5 is complete.
No live bench or shared-stack operation was run.

## Implemented contracts

- Portrait exposure is stage-file driven: 9:16 default, and 9:13 default / kbs-company-proposal,
  are seeded as candidate. The old portrait release flag and style-specific admission list are gone.
  RESTYLE_UNGATED_RATIOS explicitly preserves unrestricted portrait restyle; A4 remains on its own switch.
  Existing portrait admission error codes stay unchanged, while catalogue reasons become
  canvas_stage_disabled for disabled portrait entries.
- CANVAS_STAGES_PATH is an internal accessor-backed override for isolated backends. The admission-table
  command clears it and the stage cache so isolation settings cannot rewrite the public baseline.
- Metric epochs read object-form overlap_basis.version from the operation and persisted theme.svg.quality.
  Explicit --metric-epoch wins; mixed or partial page versions make the primary metric unmeasured.
  Git ancestry is a warning-bearing fallback. Runs without a fallback begin unmeasured and resolve from
  their actual page evidence, so no ancestry guess is required to start.
- Suspend detection compares biased and unbiased Windows interrupt time with a 30-second threshold.
  Poll gaps above 60 seconds exclude HTTP request duration. Backend overruns remain advisory; published
  timeoutSeconds takes precedence, with the existing content-brief default as a fallback cross-check.
  The client-wall cross-check uses three times the successful-deck median, with a 900-second floor.
- Clock loss records infra_lost plus clock evidence, waits up to 600 seconds for terminal backend work,
  probes backend health twice 10 seconds apart, and requeues the deck after the remaining plan.
  One automatic requeue per deck bounds persistent loss; transport loss still stops for manual resume.
  previousInvalid retains the lost-deck marker. PowerCreateRequest / PowerSetRequest protection covers
  run and matrix, falls back to SetThreadExecutionState, and releases in finally.

## Validation

- All canvas unit files, test_internal_env_names.py, and test_public_v2_admission_errors.py:
  1,275 passed. Simulated clocks replay a 3,171-second suspend; mock transports exercise terminal recovery,
  epoch extraction, health probing, and requeue behavior; mock power APIs cover exception cleanup.
- Ruff check on the 13 touched Python files: clean.
- canvas_admission_table.py --write regenerated 3,654 rows; --check reported 0 differences.
  Compared with the previous tracked fixture: 0 row differences, 0 portrait admit/reject flips,
  0 portrait outcome-code changes, and 0 widescreen row differences. The bytes are identical, so the
  fixture has no committed change and all 16:9 fixtures remain untouched.

## Implementation notes and boundaries

- The closure plan names /api/health, but the current FastAPI router mounts liveness at /health.
  The bench uses the actual backend route, without adding a product endpoint.
- This Windows installation exports QueryInterruptTime through its realtime API-set rather than the
  kernel32 facade. The implementation binds the realtime API-set and safely reports no kernel probe
  when unavailable. Interrupt-time semantics follow the
  [Windows API documentation](https://learn.microsoft.com/en-us/windows/win32/sysinfo/interrupt-time).
- New watch tests live in test_canvas_bench_watch.py because test_canvas_bench.py already exceeds
  500 lines. The path getter likewise lives in get_env_canvas.py and is re-exported through get_env.
- The public timing receipt does not currently carry every internal stage or deployed timeout override.
  Stage-overrun advisories can therefore inspect only the published stages; they never imply sleep or
  invalidate a deck by themselves. Per-page publication changes remain Phase B work.
- The initial all-canvas collection hit a pre-existing stale svg_style_adapter import. The coordinator
  repaired that outside W4 ownership before the successful complete selection.
