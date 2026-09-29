"""Pure statistics for the canvas bench decision rule (stdlib only).

docs/canvas-bench-decision-rule-design-20260929.md section 4.2: page flag
rates use the Wilson score interval on design-effect-adjusted counts and the
difference bound is Newcombe's hybrid score interval (method 10).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Z_ONE_SIDED = 1.645
# Intra-deck correlation of page flags, estimated on 13 decks; re-estimate
# once 40+ decks exist (design section 3, open decision 7).
RHO = 0.15


def design_effect(pages_per_deck: float, rho: float = RHO) -> float:
    """Variance inflation of a pooled page rate from pages clustered in decks."""

    return 1.0 + max(pages_per_deck - 1.0, 0.0) * rho


def wilson(k: float, n: float, z: float = Z_ONE_SIDED) -> tuple[float, float, float]:
    """(point, lower, upper) Wilson score interval; ``k`` and ``n`` may be reals."""

    if n <= 0:
        raise ValueError("wilson needs n > 0")
    p = k / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    half = z / denominator * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return p, max(0.0, centre - half), min(1.0, centre + half)


def newcombe_diff_bounds(
    k_cand: float, n_cand: float, k_base: float, n_base: float, z: float = Z_ONE_SIDED
) -> tuple[float, float, float]:
    """(difference, lower, upper) of ``p_cand - p_base``, Newcombe hybrid score."""

    p1, l1, u1 = wilson(k_cand, n_cand, z)
    p2, l2, u2 = wilson(k_base, n_base, z)
    diff = p1 - p2
    lower = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    upper = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return diff, lower, upper


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def variance(values: Sequence[float]) -> float:
    """Sample variance; needs two values."""

    centre = mean(values)
    return sum((v - centre) ** 2 for v in values) / (len(values) - 1)


def mean_diff_bounds(
    base: Sequence[float], cand: Sequence[float], z: float = Z_ONE_SIDED
) -> tuple[float, float, float]:
    """(difference, lower, upper) of ``mean(cand) - mean(base)``, normal bound."""

    if len(base) < 2 or len(cand) < 2:
        raise ValueError("mean_diff_bounds needs two values per arm")
    diff = mean(cand) - mean(base)
    se = math.sqrt(variance(base) / len(base) + variance(cand) / len(cand))
    return diff, diff - z * se, diff + z * se
