"""Compare safe, aggregate maximum-repair observations without mutating a deck.

The input is intentionally a compact JSON observation rather than slides or HTML.
It can be produced from an 80-slide replay, CI fixture, or an approved manual
review export.  This keeps benchmark comparison usable where rendering/LLM
credentials are unavailable and prevents source content from entering reports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


_REQUIRED_COUNTS = (
    "slide_count",
    "detailed_issue_count",
    "resolved_issue_count",
    "remaining_issue_count",
    "fixed_count",
    "full_render_count",
)


def _count(observation: Mapping[str, Any], name: str) -> int:
    value = observation.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"observation.{name} must be a non-negative integer")
    return value


def summarize_observation(observation: Mapping[str, Any]) -> dict[str, int | float]:
    """Validate a no-content observation and derive its verified resolution rate."""

    summary = {name: _count(observation, name) for name in _REQUIRED_COUNTS}
    for name in ("hard_violation_count", "content_loss_count"):
        summary[name] = _count(observation, name)
    detailed = int(summary["detailed_issue_count"])
    resolved = int(summary["resolved_issue_count"])
    remaining = int(summary["remaining_issue_count"])
    if resolved + remaining > detailed:
        raise ValueError("resolved plus remaining issues cannot exceed detailed issues")
    summary["resolution_rate"] = round(resolved / detailed, 6) if detailed else 0.0
    return summary


def compare_observations(
    baseline: Mapping[str, Any], maximum_repair: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a compact before/after report with explicit safety acceptance."""

    before = summarize_observation(baseline)
    after = summarize_observation(maximum_repair)
    if before["slide_count"] != after["slide_count"]:
        raise ValueError("baseline and maximum repair observations must cover the same deck")
    acceptance = {
        "hard_gate_preserved": after["hard_violation_count"] == 0
        and after["content_loss_count"] == 0,
        "resolved_issues_not_regressed": after["resolved_issue_count"]
        >= before["resolved_issue_count"],
        "fixed_slides_not_regressed": after["fixed_count"] >= before["fixed_count"],
    }
    return {
        "schema": "maximum-quality-repair-benchmark/1",
        "slide_count": before["slide_count"],
        "baseline": before,
        "maximum_repair": after,
        "delta": {
            "full_render_count": after["full_render_count"] - before["full_render_count"],
            "resolved_issue_count": after["resolved_issue_count"]
            - before["resolved_issue_count"],
            "resolution_rate": round(
                float(after["resolution_rate"]) - float(before["resolution_rate"]), 6
            ),
        },
        "acceptance": {**acceptance, "passed": all(acceptance.values())},
    }


def load_observation(path: Path, role: str) -> Mapping[str, Any]:
    """Load either a standalone observation or the paired fixture format."""

    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, Mapping):
        raise ValueError("observations must be JSON objects")
    nested = decoded.get(role)
    if isinstance(nested, Mapping):
        return nested
    return decoded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--maximum-repair", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = compare_observations(
            load_observation(args.baseline, "baseline"),
            load_observation(args.maximum_repair, "maximum_repair"),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
