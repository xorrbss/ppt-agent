"""Pure classification and summary arithmetic for the authored fit replay.

M3 of ``docs/authored-fit-magnitude-measurement-design-20260904.md`` (section
5.2 for the bucket table, section 7 for the *S* and *N* decision inputs).
Nothing here touches a browser, a database or the filesystem: every function
is a total function of already-measured per-slide results, which is what
makes the bucket rules testable without Chromium.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: Overflow element kinds a uniform text scale-down cannot fix (design 5.2).
NON_TEXT_KINDS = frozenset({"figure", "figure_part", "container", "image"})

#: A passing candidate whose floored share reaches this is ``floor_bound``:
#: it fits only by sitting on the 15px design floor (design 5.2).
FLOOR_BOUND_SHARE = 0.5

#: Buckets outside the design's seven. ``passes_at_1.0`` is the under-fill
#: population; ``non_geometry_flagged`` is a slide the gate flags for a
#: non-geometry reason only (contrast/font/image), which no scale step can
#: change and which design 6.4 excludes from the ladder population.
BUCKET_PASSES = "passes_at_1.0"
BUCKET_NON_GEOMETRY = "non_geometry_flagged"

BUCKET_FLOOR_BOUND = "floor_bound"
BUCKET_NON_TEXT = "non_text"
BUCKET_UNFIXABLE = "unfixable_by_scale"

#: Category order used when no overflow issue exists but the slide still has
#: geometry defects; the fallback is documented in ``worst_geometry_kind``.
_FALLBACK_ORDER = (
    ("off_canvas", ("overshoot_px_left", "overshoot_px_top", "overshoot_px_right", "overshoot_px_bottom")),
    ("clipping", ("hidden_fraction",)),
)

_HISTOGRAM_BINS = 10


def scale_label(scale: float) -> str:
    return f"{float(scale):.2f}"


def fits_bucket(scale: float) -> str:
    return f"fits_at_{scale_label(scale)}"


@dataclass(frozen=True)
class ScaleOutcome:
    """One rung of the ladder, already rendered and gated."""

    scale: float
    passed: bool
    scaled_declarations: int = 0
    floored_declarations: int = 0
    unscaled_declarations: int = 0


def is_floor_bound(outcome: ScaleOutcome) -> bool:
    """True when the candidate fits only because most body text hit the floor."""

    body = outcome.scaled_declarations + outcome.floored_declarations
    if body <= 0:
        # Nothing scalable at all: the pass cannot be attributed to the floor.
        return False
    return outcome.floored_declarations / body >= FLOOR_BOUND_SHARE


def _magnitude_number(magnitude: Mapping[str, object], key: str) -> float:
    value = magnitude.get(key)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def worst_geometry_kind(issues: Iterable[Mapping[str, object]]) -> str | None:
    """Return the element kind of the slide's worst geometry defect.

    The design names this "the worst overflow kind at 1.0". Overflow decides
    it whenever an overflow issue exists. A slide whose only geometry defect
    is an escaped or clipped element has no overflow ratio at all, so the
    worst off-canvas overshoot (then the worst clipped fraction) stands in;
    otherwise such a slide would be filed as a text problem it is not.
    """

    best_kind: str | None = None
    best_ratio = 0.0
    for issue in issues:
        if issue.get("category") != "overflow":
            continue
        magnitude = issue.get("magnitude")
        if not isinstance(magnitude, Mapping):
            continue
        ratio = max(
            _magnitude_number(magnitude, "ratio_x"),
            _magnitude_number(magnitude, "ratio_y"),
        )
        if ratio > best_ratio:
            best_ratio = ratio
            best_kind = str(magnitude.get("element_kind", "container"))
    if best_kind is not None:
        return best_kind

    for category, keys in _FALLBACK_ORDER:
        best_value = 0.0
        for issue in issues:
            if issue.get("category") != category:
                continue
            magnitude = issue.get("magnitude")
            if not isinstance(magnitude, Mapping):
                continue
            value = max(_magnitude_number(magnitude, key) for key in keys)
            if value > best_value:
                best_value = value
                best_kind = str(magnitude.get("element_kind", "container"))
        if best_kind is not None:
            return best_kind
    return None


#: The categories ``blocking_safety_issues`` treats as publication blockers.
BLOCKING_CATEGORIES = ("overflow", "clipping", "off_canvas", "overlap")


def blocking_categories(issues: Iterable[Mapping[str, object]]) -> list[str]:
    """The blocking geometry categories this slide raised, in a stable order."""

    raised = {str(issue.get("category")) for issue in issues}
    return [category for category in BLOCKING_CATEGORIES if category in raised]


def kind_label(row: Mapping[str, object]) -> str:
    """Label one row for the overflow-kind table.

    ``overlap`` carries no magnitude (M1 fills only overflow, off-canvas and
    clipping), so a slide blocked by overlap alone has no element kind. Naming
    the category is more honest than filing it as "none".
    """

    kind = row.get("worst_overflow_kind")
    if kind:
        return str(kind)
    categories = row.get("blocking_categories") or []
    if categories:
        return "no_element_kind:" + ",".join(str(item) for item in categories)
    return "none"


def classify_bucket(
    *,
    flagged: bool,
    in_ladder_population: bool,
    worst_kind: str | None,
    outcomes: Sequence[ScaleOutcome],
) -> str:
    """Bucket one slide per design 5.2, plus the two out-of-ladder buckets."""

    if not flagged:
        return BUCKET_PASSES
    if not in_ladder_population:
        return BUCKET_NON_GEOMETRY
    for outcome in outcomes:
        if not outcome.passed:
            continue
        if is_floor_bound(outcome):
            return BUCKET_FLOOR_BOUND
        return fits_bucket(outcome.scale)
    if worst_kind in NON_TEXT_KINDS:
        return BUCKET_NON_TEXT
    return BUCKET_UNFIXABLE


def _tally(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def under_fill_histogram(fills: Iterable[float]) -> list[dict[str, object]]:
    """Ten 0.1-wide bins over ``content_bbox_fill`` (design 5.3)."""

    counts = [0] * _HISTOGRAM_BINS
    for value in fills:
        index = min(_HISTOGRAM_BINS - 1, max(0, int(float(value) * _HISTOGRAM_BINS)))
        counts[index] += 1
    return [
        {
            "bin": f"{index / _HISTOGRAM_BINS:.1f}-{(index + 1) / _HISTOGRAM_BINS:.1f}",
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def decision_inputs(buckets: Sequence[str]) -> dict[str, object]:
    """Compute *S* and *N* over one population's buckets (design section 7)."""

    ladder = [
        bucket
        for bucket in buckets
        if bucket not in {BUCKET_PASSES, BUCKET_NON_GEOMETRY}
    ]
    total = len(ladder)
    fits = sum(1 for bucket in ladder if bucket.startswith("fits_at_"))
    non_text = sum(1 for bucket in ladder if bucket == BUCKET_NON_TEXT)
    return {
        "ladder_population": total,
        "fits_by_scale": fits,
        "non_text": non_text,
        "floor_bound": sum(1 for bucket in ladder if bucket == BUCKET_FLOOR_BOUND),
        "unfixable_by_scale": sum(
            1 for bucket in ladder if bucket == BUCKET_UNFIXABLE
        ),
        "S": (fits / total) if total else None,
        "N": (non_text / total) if total else None,
    }


def build_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate every replayed slide row into the design 5.3 summary."""

    current = [row for row in rows if row.get("code_version") == "current"]
    legacy = [row for row in rows if row.get("code_version") == "legacy"]
    passing = [
        row
        for row in rows
        if row.get("bucket") in {BUCKET_PASSES, BUCKET_NON_GEOMETRY}
    ]
    fills = [
        float(((row.get("fit_summary") or {}) or {}).get("content_bbox_fill", 0.0))
        for row in passing
    ]
    low_fill = sum(1 for value in fills if value < 0.55)
    current_ladder = [
        row
        for row in current
        if row.get("bucket") not in {BUCKET_PASSES, BUCKET_NON_GEOMETRY}
    ]
    return {
        "slides": len(rows),
        "by_code_version": _tally(str(row.get("code_version")) for row in rows),
        "by_source": _tally(str(row.get("source")) for row in rows),
        "buckets_current": _tally(str(row.get("bucket")) for row in current),
        "buckets_legacy": _tally(str(row.get("bucket")) for row in legacy),
        "overflow_kinds_current": _tally(
            kind_label(row) for row in current_ladder
        ),
        "under_fill": {
            "population": len(passing),
            "histogram": under_fill_histogram(fills),
            "below_0.55": low_fill,
            "below_0.55_share": (low_fill / len(passing)) if passing else None,
            "grow_step_indicated": bool(passing) and (low_fill / len(passing)) >= 0.25,
        },
        "decision_inputs_current": decision_inputs(
            [str(row.get("bucket")) for row in current]
        ),
        "decision_inputs_legacy": decision_inputs(
            [str(row.get("bucket")) for row in legacy]
        ),
    }


__all__ = [
    "BUCKET_FLOOR_BOUND",
    "BUCKET_NON_GEOMETRY",
    "BUCKET_NON_TEXT",
    "BUCKET_PASSES",
    "BUCKET_UNFIXABLE",
    "FLOOR_BOUND_SHARE",
    "NON_TEXT_KINDS",
    "ScaleOutcome",
    "blocking_categories",
    "build_summary",
    "classify_bucket",
    "decision_inputs",
    "fits_bucket",
    "is_floor_bound",
    "kind_label",
    "scale_label",
    "under_fill_histogram",
    "worst_geometry_kind",
]
