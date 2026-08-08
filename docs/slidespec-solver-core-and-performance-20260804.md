# SlideSpec solver core and performance evidence (2026-08-04)

## Deterministic UNSAT-core contract

`solve_constraints` now separates three guarantees:

1. The non-overlap branch search may return UNSAT only after exhaustive branch
   enumeration. If the branch bound is reached without a witness, it returns
   `constraint.feasibility.search_limited`, an empty core, and
   `search_complete=false` (fail closed).
2. After proved UNSAT, deletion first derives an inclusion-minimal core. This
   phase is deliberately not charged to the cardinality budget, so a useful,
   verified fallback exists before exponential work begins.
3. Candidate subsets are then checked in `(cardinality, lexicographic tuple)`
   order. The first UNSAT subset is therefore cardinality-minimum with a stable
   deterministic tie break. At most 4096 new oracle calls are made by default.
   The caller may lower the bound with `cardinality_core_budget`.

If the cardinality budget is exhausted, the result retains the proved
inclusion-minimal core and reports:

- `unsat_core_kind=inclusion-minimal-fallback`
- `core_search_complete=false`
- `constraint.unsat_core.inclusion_minimal_fallback`

A completed search reports `unsat_core_kind=cardinality-minimum` and
`constraint.unsat_core.cardinality_minimum`. A negative budget is rejected.
`core_oracle_calls` exposes the total additional core oracle work.

SAT feasibility and minimum-change optimality are also distinct. A feasible
witness always proves SAT, even when the branch product is larger than the
budget. In that case the solver returns `search_complete=true` and
`optimality_proven=false`; repair-plan generation refuses that result, retaining
the existing fail-closed minimum-change guarantee.

## Performance changes

- Non-overlap products larger than five pairs use deterministic best-first
  lattice enumeration ranked by squared authored-geometry violation. This
  avoids lexicographic starvation of early pair dimensions.
- Cyclic-projection coefficient norms are precomputed once per branch rather
  than once per half-space per iteration.
- In a bounded product, the solver stops after the first SAT witness. Continuing
  to branch 1024 could not prove global movement optimality and only increased
  tail latency. The bounded-optimality state remains explicit and is rejected
  by automatic repair.

## Reproducible benchmark and thresholds

Command (Windows, from `servers/fastapi`):

```powershell
.\.venv\Scripts\python.exe scripts/slidespec_solver_benchmark.py --cases 48 --seed 20260804
```

Observed on 2026-08-04 local Windows run:

| Metric | Result |
|---|---:|
| Duration p50 | 1.879 ms |
| Duration p95 | 111.361 ms |
| Duration p99 | 116.776 ms |
| Branch p50 / p95 / p99 | 1 / 2 / 2 |
| Total branches | 56 |
| Feasible ratio | 100% |
| UNKNOWN ratio | 0% |
| Multi-branch ratio | 16.6667% |
| Bounded-optimality ratio | 50% |

The unit regression runs 16 fixed-seed cases and enforces p50 `<50 ms`, p95
`<400 ms`, p99 `<700 ms`, branch p95/p99 `<=4`, UNKNOWN `=0`, and feasible
ratio `=1`. Timing limits are intentionally tolerant of shared Windows load;
branch limits provide the deterministic algorithmic guard.

Verification commands:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_slidespec_solver.py tests/unit/test_slidespec_perf_harness.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit -k slidespec -q
```

Results: focused `14 passed`; SlideSpec-selected unit regression `91 passed`
(`2128 deselected`); top-level SlideSpec unit/integration regression
`105 passed`; full FastAPI regression `2332 passed, 16 skipped`. The final
full run used a repository-local `--basetemp` and completed without the former
temporary-directory ACL warning.
