"""Rating sheets, medians, verdicts, and the style pre-filter.

Reads the filled-in rating sheets (CSV) plus ``key.json`` (letter -> real
deck) and evaluates the phase 1 acceptance criteria from
``docs/generation-quality-root-cause-and-composer-architecture-20260904.md``
section 7b::

    Composer >= floor on every slide, >= parity on legibility and
    consistency, >= 80% of the target deck's median craft score.

"Score" is a per-(deck, slide, axis) median across raters -- the protocol's
own definition. Two aggregate readings are needed that 7b does not spell out
further, so this module fixes reasonable defaults and states them here
rather than guessing silently:

- "every slide" (condition 1) is checked per slide as the mean of the five
  axis medians for that slide (an "overall" reading per slide, floor vs
  candidate on the same source page).
- deck-level axis scores (conditions 2 and 3) are the median, across the
  deck's slides, of the per-slide axis medians for that axis.

The style pre-filter (advisory, never this gate) lives in
``composer_blind_rating_pack_prefilter.py``.
"""

from __future__ import annotations

import csv
from pathlib import Path
from statistics import median as _median
from typing import Sequence

AXES: tuple[str, ...] = (
    "legibility",
    "information_hierarchy",
    "layout_craft",
    "deck_consistency",
    "reads_as_designed",
)
RATERS: tuple[str, ...] = ("rater-1", "rater-2", "rater-3")
SHEET_FIELDS: tuple[str, ...] = ("rater_id", "deck", "slide", *AXES, "notes")


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------


def write_sheet(
    path: Path,
    decks: Sequence[str],
    slide_count: int,
    *,
    raters: Sequence[str] = RATERS,
) -> None:
    """Write one row per (rater, deck, slide) with empty score cells."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        for rater in raters:
            for deck in decks:
                for slide in range(1, slide_count + 1):
                    row = {"rater_id": rater, "deck": deck, "slide": slide, "notes": ""}
                    row.update({axis: "" for axis in AXES})
                    writer.writerow(row)


def read_sheet(path: Path) -> list[dict]:
    """Read a filled sheet; blank score cells become ``None``."""

    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, object] = {
                "rater_id": (raw.get("rater_id") or "").strip(),
                "deck": (raw.get("deck") or "").strip(),
                "slide": int(raw["slide"]),
            }
            for axis in AXES:
                value = (raw.get(axis) or "").strip()
                row[axis] = float(value) if value else None
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Medians
# ---------------------------------------------------------------------------


def per_slide_axis_medians(
    rows: list[dict],
    decks: Sequence[str],
    slide_count: int,
    *,
    axes: Sequence[str] = AXES,
) -> dict[str, dict[int, dict[str, float | None]]]:
    result: dict[str, dict[int, dict[str, float | None]]] = {}
    for deck in decks:
        result[deck] = {}
        for slide in range(1, slide_count + 1):
            result[deck][slide] = {}
            for axis in axes:
                values = [
                    row[axis]
                    for row in rows
                    if row["deck"] == deck and row["slide"] == slide and row[axis] is not None
                ]
                result[deck][slide][axis] = _median(values) if values else None
    return result


def deck_axis_median(
    per_slide: dict[str, dict[int, dict[str, float | None]]],
    deck: str,
    axis: str,
    slide_count: int,
) -> float | None:
    values = [
        per_slide[deck][slide][axis]
        for slide in range(1, slide_count + 1)
        if per_slide[deck][slide][axis] is not None
    ]
    return _median(values) if values else None


def per_slide_overall(
    per_slide: dict[str, dict[int, dict[str, float | None]]],
    deck: str,
    slide: int,
    *,
    axes: Sequence[str] = AXES,
) -> float | None:
    values = [per_slide[deck][slide][axis] for axis in axes]
    if any(value is None for value in values):
        return None
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def evaluate(key: dict, main_rows: list[dict], target_rows: list[dict]) -> dict:
    """Evaluate the three phase 1 acceptance conditions from 7b."""

    assignment = key["assignment"]
    role_letter = {info["role"]: letter for letter, info in assignment.items()}
    floor_letter = role_letter["floor"]
    parity_letter = role_letter["parity"]
    candidate_letter = role_letter["candidate"]
    target_letter = role_letter["target_craft_calibration"]

    slide_count = assignment[floor_letter]["pageCount"]
    per_slide = per_slide_axis_medians(main_rows, (floor_letter, parity_letter, candidate_letter), slide_count)

    target_slide_count = assignment[target_letter]["pageCount"]
    per_slide_target = per_slide_axis_medians(target_rows, (target_letter,), target_slide_count)

    violations = []
    for slide in range(1, slide_count + 1):
        candidate_overall = per_slide_overall(per_slide, candidate_letter, slide)
        floor_overall = per_slide_overall(per_slide, floor_letter, slide)
        if candidate_overall is None or floor_overall is None:
            violations.append({"slide": slide, "reason": "incomplete ratings"})
        elif candidate_overall < floor_overall:
            violations.append(
                {"slide": slide, "candidateOverall": candidate_overall, "floorOverall": floor_overall}
            )

    candidate_legibility = deck_axis_median(per_slide, candidate_letter, "legibility", slide_count)
    parity_legibility = deck_axis_median(per_slide, parity_letter, "legibility", slide_count)
    candidate_consistency = deck_axis_median(per_slide, candidate_letter, "deck_consistency", slide_count)
    parity_consistency = deck_axis_median(per_slide, parity_letter, "deck_consistency", slide_count)
    candidate_craft = deck_axis_median(per_slide, candidate_letter, "layout_craft", slide_count)
    target_craft = deck_axis_median(per_slide_target, target_letter, "layout_craft", target_slide_count)
    craft_threshold = 0.8 * target_craft if target_craft is not None else None

    checks = {
        "floor_every_slide": {
            "description": "Composer >= floor on every slide (per-slide mean of the 5 axis medians)",
            "passed": len(violations) == 0,
            "violations": violations,
        },
        "parity_legibility": {
            "description": "Composer deck-level legibility median >= parity's",
            "passed": (
                candidate_legibility is not None
                and parity_legibility is not None
                and candidate_legibility >= parity_legibility
            ),
            "candidate": candidate_legibility,
            "parity": parity_legibility,
        },
        "parity_consistency": {
            "description": "Composer deck-level consistency median >= parity's",
            "passed": (
                candidate_consistency is not None
                and parity_consistency is not None
                and candidate_consistency >= parity_consistency
            ),
            "candidate": candidate_consistency,
            "parity": parity_consistency,
        },
        "target_craft_80pct": {
            "description": "Composer deck-level craft median >= 80% of the target deck's craft median",
            "passed": (
                candidate_craft is not None and craft_threshold is not None and candidate_craft >= craft_threshold
            ),
            "candidate": candidate_craft,
            "target": target_craft,
            "threshold": craft_threshold,
        },
    }
    return {
        "checks": checks,
        "overallPass": all(check["passed"] for check in checks.values()),
        "deckLetters": {
            "floor": floor_letter,
            "parity": parity_letter,
            "candidate": candidate_letter,
            "target": target_letter,
        },
    }


def format_verdict(verdict: dict) -> str:
    lines = ["Composer blind rating verdict (design bar, 7b)", "=" * 48]
    for name, check in verdict["checks"].items():
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(f"[{status}] {name}: {check['description']}")
    lines.append("-" * 48)
    lines.append(f"OVERALL: {'PASS' if verdict['overallPass'] else 'FAIL'}")
    return "\n".join(lines)
