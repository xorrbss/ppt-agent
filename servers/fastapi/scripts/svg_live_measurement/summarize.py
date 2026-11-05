"""Per-deck table, per-arm statistics and the plan's decision rule.

  python3 summarize.py <out-dir>

Reads <out>/runs/*/summary.json, <out>/preset.json (arms and rule, written by paired_run.py) and,
when filled in, <out>/ratings.csv with <out>/blind_key.json. Rules (docs/svg-mode-improvement-plan-
20261105.md section 5):
  performance  adopt when median wall time or median cost improves, and neither the gate pass rate
               nor the fallback page count gets worse; with ratings, the mean must not drop
  quality      adopt only when the blind mean slide rating rises and neither first-attempt failures
               nor fallback pages rise (a gate pass rate alone never decides a quality change)
  none         statistics only
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

AXES = ("layout", "hierarchy", "readability", "richness", "consistency", "korean")


def _wall(row):
    return row.get("wall_s_operation") or row.get("wall_s_receipt") or row.get("wall_s_client")


def _gate(row):
    gate = row.get("gate") or {}
    return gate.get("passed") or 0, gate.get("pages") or row.get("slides") or 0


def _ratings(root: Path) -> dict[str, list[float]]:
    key_path, csv_path = root / "blind_key.json", root / "ratings.csv"
    ratings: dict[str, list[float]] = defaultdict(list)
    if not (key_path.exists() and csv_path.exists()):
        return ratings
    key = json.loads(key_path.read_text())
    with open(csv_path, newline="") as handle:
        for row in csv.DictReader(handle):
            values = [float(row[axis]) for axis in AXES if row.get(axis)]
            if len(values) == len(AXES) and row["png"] in key:
                ratings[key[row["png"]]["run"]].append(statistics.mean(values))
    return ratings


def _med(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def _rate(values):
    return round(statistics.mean(values), 2) if values else None


def decide(rule: str, control: dict, challenger: dict) -> str:
    if rule == "none" or not control or not challenger:
        return "no decision rule for this preset"
    c_rating, h_rating = control["mean_rating"], challenger["mean_rating"]
    rated = c_rating is not None and h_rating is not None
    fallback_ok = challenger["fallback_pages"] <= control["fallback_pages"]
    if rule == "performance":
        faster = ((challenger["median_wall_s"] or 1e9) < (control["median_wall_s"] or 0)
                  or (challenger["median_cost_usd"] or 1e9) < (control["median_cost_usd"] or 0))
        gate_ok = (challenger["pass_rate"] or 0) >= (control["pass_rate"] or 0)
        detail = f"faster_or_cheaper={faster} gate_not_worse={gate_ok} fallback_not_worse={fallback_ok}"
        if not (faster and gate_ok and fallback_ok):
            return f"KEEP the control ({detail})"
        if not rated:
            return f"PENDING: rate the blind PNGs first ({detail})"
        return (f"ADOPT the challenger ({detail}, rating {h_rating} vs {c_rating})" if h_rating >= c_rating
                else f"KEEP the control: rating dropped {h_rating} vs {c_rating} ({detail})")
    if not rated:
        return "PENDING: a quality change needs the blind ratings"
    failures_ok = challenger["first_attempt_failures"] <= control["first_attempt_failures"]
    better = h_rating > c_rating
    detail = (f"rating {h_rating} vs {c_rating} better={better} first_attempt_failures_not_worse={failures_ok} "
              f"fallback_not_worse={fallback_ok}")
    return f"ADOPT the challenger ({detail})" if better and failures_ok and fallback_ok else f"KEEP the control ({detail})"


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    root = Path(argv[0])
    rows = [json.loads(p.read_text()) for p in sorted((root / "runs").glob("*/summary.json"))]
    if not rows:
        print("no finished runs")
        return 0
    preset = json.loads((root / "preset.json").read_text()) if (root / "preset.json").exists() else {}
    ratings = _ratings(root)

    print("| run | arm | state | wall s | header stages s | cost $ | logical/physical | cached in | cache write | "
          "grammar 400 | repairs | 1st-try fail | gate | fallback | vision calls | chart adv | export s | "
          "charts/tables/text | rating | load before |")
    print("|---|---|---|---:|---|---:|---|---:|---:|---:|---:|---:|---|---|---|---|---:|---|---:|---|")
    arms: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        arms[row.get("arm") or "?"].append(row)
        passed, pages = _gate(row)
        calls = row.get("receipt_calls") or {}
        vision = (row.get("vision_review") or {}).get("calls") or {}
        advisories = sum(len(v) for v in (row.get("chart_advisories") or {}).values())
        native = row.get("pptx") or {}
        tokens = row.get("tokens") or {}
        rating = _rate(ratings.get(row["run"], []))
        load = (row.get("uptime_before") or "").split("load average:")[-1].strip()
        header = ",".join(f"{k}={v}" for k, v in (row.get("header_stage_s") or {}).items()) or "-"
        print(f"| {row['run']} | {row.get('arm')} | {row.get('client_state')} | {_wall(row)} | {header} | "
              f"{row.get('cost_usd')} | {calls.get('logical')}/{calls.get('physical')} | "
              f"{tokens.get('cached_input_tokens')} | {tokens.get('cache_write_input_tokens')} | "
              f"{row.get('grammar_400')} | {row.get('repairs')} | {row.get('first_attempt_failures')} | "
              f"{passed}/{pages} {(row.get('gate') or {}).get('status')} | {row.get('fallback_pages')} | "
              f"{vision.get('logical')}/{vision.get('physical')} | {advisories} | {row.get('export_s')} | "
              f"{native.get('charts')}/{native.get('tables')}/{native.get('text_boxes')} | "
              f"{'' if rating is None else rating} | {load} |")

    stats: dict[str, dict] = {}
    for arm, items in sorted(arms.items()):
        passed = sum(_gate(r)[0] for r in items)
        pages = sum(_gate(r)[1] for r in items)
        scores = [s for r in items for s in ratings.get(r["run"], [])]
        stats[arm] = {
            "decks": len(items), "median_wall_s": _med(_wall(r) for r in items),
            "median_cost_usd": _med(r.get("cost_usd") for r in items),
            "median_export_s": _med(r.get("export_s") for r in items),
            "pass_rate": round(passed / pages, 3) if pages else None,
            "fallback_pages": sum(len(r.get("fallback_pages") or []) for r in items),
            "first_attempt_failures": sum(r.get("first_attempt_failures") or 0 for r in items),
            "repairs": sum(r.get("repairs") or 0 for r in items), "mean_rating": _rate(scores)}
    print()
    print(json.dumps(stats, indent=1))
    by_prompt: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_prompt[row.get("prompt") or "?"][row.get("arm") or "?"].append(row)
    for prompt, grouped in sorted(by_prompt.items()):
        cells = ", ".join(f"{arm}: wall {_med(_wall(r) for r in items)} cost {_med(r.get('cost_usd') for r in items)}"
                          for arm, items in sorted(grouped.items()))
        print(f"per prompt {prompt}: {cells}")
    print("DECISION:", decide(preset.get("rule", "none"), stats.get(str(preset.get("control"))),
                              stats.get(str(preset.get("challenger")))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
