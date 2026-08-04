"""Reproducible local benchmark for the mixed SlideSpec geometry solver.

Run from ``servers/fastapi``:

    .venv/Scripts/python scripts/slidespec_solver_benchmark.py --cases 48

The generated corpus is fixed by seed and intentionally mixes anchored layouts
with six-pair non-overlap constraints.  Wall-clock percentiles are accompanied
by deterministic branch counts and an UNKNOWN ratio so performance changes can
be separated from bounded-search coverage changes.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from time import perf_counter
from typing import Sequence


FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from models.slidespec_v1 import (  # noqa: E402
    SLIDESPEC_SCHEMA,
    AnchorConstraint,
    Bounds,
    ContentNode,
    LayoutConstraints,
    LayoutNode,
    NonOverlapConstraint,
    SlideDigests,
    SlideSpec,
)
from utils.slidespec_core import ZERO_DIGEST, seal_slidespec  # noqa: E402
from utils.slidespec_solver import solve_constraints  # noqa: E402


DEFAULT_SEED = 20260804


def observed_percentile(samples: Sequence[float | int], percentile: float) -> float:
    """Return an observed nearest-rank percentile without interpolation."""

    if not samples:
        raise ValueError("samples must not be empty")
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(samples)
    rank = max(1, int(len(ordered) * percentile + 0.999999999))
    return float(ordered[rank - 1])


def build_case(index: int, generator: random.Random) -> SlideSpec:
    """Build a deterministic feasible mixed/non-overlap case."""

    # Half of the corpus begins globally valid; the other half has one authored
    # overlap that must be repaired.  Jitter is fixed by the caller's seed.
    grid = ((0.08, 0.10), (0.57, 0.10), (0.08, 0.58), (0.57, 0.58))
    positions = list(grid)
    if index % 2:
        positions[1] = (0.25 + generator.uniform(-0.015, 0.015), 0.12)
    children = tuple(
        LayoutNode(
            layoutId=f"lay:{index}:{node_index}",
            kind="content",
            contentSemanticId=f"sem:{index}:{node_index}",
            bounds=Bounds(x=x, y=y, width=0.30, height=0.24),
            constraints=LayoutConstraints(
                lockSize=True,
                allowedOperations=("translateWithinParent",),
                maxTranslation=0.38,
            ),
        )
        for node_index, (x, y) in enumerate(positions)
    )
    first_id = children[0].layoutId
    return seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId=f"solver-benchmark-{index}",
            content=tuple(
                ContentNode(
                    semanticId=f"sem:{index}:{node_index}",
                    kind="text",
                    text=f"Benchmark {index}/{node_index}",
                    readingOrder=node_index,
                )
                for node_index in range(4)
            ),
            layout=LayoutNode(layoutId="lay:root", kind="canvas", children=children),
            constraints=(
                AnchorConstraint(
                    constraintId=f"con:{index}:anchor",
                    kind="anchor",
                    target=first_id,
                    container="lay:root",
                    targetEdge="left",
                    containerEdge="left",
                    offset=0.08,
                    tolerance=0.001,
                ),
                NonOverlapConstraint(
                    constraintId=f"con:{index}:non-overlap",
                    kind="nonOverlap",
                    targets=tuple(child.layoutId for child in children),
                    clearance=0.025,
                    tolerance=0.001,
                ),
            ),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )


def run_benchmark(*, cases: int = 48, seed: int = DEFAULT_SEED) -> dict[str, object]:
    if cases <= 0:
        raise ValueError("cases must be positive")
    generator = random.Random(seed)
    corpus = tuple(build_case(index, generator) for index in range(cases))
    durations_ms: list[float] = []
    branches: list[int] = []
    unknown = 0
    bounded_optimality = 0
    feasible = 0
    for spec in corpus:
        started = perf_counter()
        result = solve_constraints(spec, compute_unsat_core=False)
        durations_ms.append((perf_counter() - started) * 1000)
        branches.append(result.branches_evaluated)
        unknown += int(not result.search_complete)
        bounded_optimality += int(result.feasible and not result.optimality_proven)
        feasible += int(result.feasible)
    return {
        "seed": seed,
        "cases": cases,
        "feasible_ratio": round(feasible / cases, 6),
        "unknown_ratio": round(unknown / cases, 6),
        "bounded_optimality_ratio": round(bounded_optimality / cases, 6),
        "multi_branch_ratio": round(sum(value > 1 for value in branches) / cases, 6),
        "duration_ms": {
            "p50": round(observed_percentile(durations_ms, 0.50), 3),
            "p95": round(observed_percentile(durations_ms, 0.95), 3),
            "p99": round(observed_percentile(durations_ms, 0.99), 3),
            "max": round(max(durations_ms), 3),
        },
        "branches": {
            "p50": int(observed_percentile(branches, 0.50)),
            "p95": int(observed_percentile(branches, 0.95)),
            "p99": int(observed_percentile(branches, 0.99)),
            "max": max(branches),
            "total": sum(branches),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=48)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


if __name__ == "__main__":
    print(json.dumps(run_benchmark(**vars(_parser().parse_args())), indent=2, sort_keys=True))
