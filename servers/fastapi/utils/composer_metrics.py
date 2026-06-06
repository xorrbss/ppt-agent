"""Composer-stability metrics (gate G8).

Pure functions over a composed deck's archetype sequence — no LLM, no I/O — so
the logic is unit-testable in CI. The real-codex stability harness
(scripts/measure_composer_stability.py) feeds live compositions through these
and aggregates the results.

Measured dimensions (design §12 / revision G8):
  - schema-valid rate : did compose_slides produce a parseable PresentationComposition?
  - n_slides adherence: does the composed count equal the requested count?
  - variety           : distinct-archetype ratio + adjacent-duplicate count.
"""
from typing import List, Optional


def evaluate_archetypes(
    archetypes: List[str], requested_n: Optional[int] = None
) -> dict:
    """Per-deck variety / n_slides metrics for one composed deck."""
    n = len(archetypes)
    adjacent_dups = sum(
        1 for i in range(1, n) if archetypes[i] == archetypes[i - 1]
    )
    distinct = len(set(archetypes))
    return {
        "n_slides": n,
        "n_match": requested_n is None or n == requested_n,
        "distinct_archetypes": distinct,
        "variety_ratio": (distinct / n) if n else 0.0,
        "adjacent_dups": adjacent_dups,
        "no_adjacent_dups": adjacent_dups == 0,
    }


# Proposed acceptance thresholds for the G8 gate. FINAL values are a product
# decision (escalation) — these are the defaults the harness reports against.
DEFAULT_THRESHOLDS = {
    "min_schema_valid_rate": 1.0,   # closed discriminated schema + retries → ~100%
    "min_n_match_rate": 1.0,        # n_slides pinned by the dynamic schema → ~100%
    "min_mean_variety_ratio": 0.6,  # >=60% distinct archetypes on average
    "min_no_adjacent_dup_rate": 0.9,  # >=90% of decks have zero adjacent duplicates
}


def summarize(
    results: List[dict], schema_valid_count: int, total_attempts: int
) -> dict:
    """Aggregate per-deck metric dicts (valid decks only) plus the schema-valid
    count over all attempts into a stability summary."""
    runs = len(results)
    summary = {
        "total_attempts": total_attempts,
        "schema_valid_count": schema_valid_count,
        "schema_valid_rate": (schema_valid_count / total_attempts)
        if total_attempts
        else 0.0,
        "valid_decks": runs,
    }
    if runs:
        summary.update(
            {
                "n_match_rate": sum(1 for r in results if r["n_match"]) / runs,
                "mean_variety_ratio": sum(r["variety_ratio"] for r in results) / runs,
                "no_adjacent_dup_rate": sum(
                    1 for r in results if r["no_adjacent_dups"]
                )
                / runs,
                "total_adjacent_dups": sum(r["adjacent_dups"] for r in results),
            }
        )
    return summary


def passes(summary: dict, thresholds: dict = DEFAULT_THRESHOLDS) -> bool:
    """Whether a stability summary meets the (proposed) acceptance thresholds."""
    return (
        summary.get("schema_valid_rate", 0.0) >= thresholds["min_schema_valid_rate"]
        and summary.get("n_match_rate", 0.0) >= thresholds["min_n_match_rate"]
        and summary.get("mean_variety_ratio", 0.0)
        >= thresholds["min_mean_variety_ratio"]
        and summary.get("no_adjacent_dup_rate", 0.0)
        >= thresholds["min_no_adjacent_dup_rate"]
    )
