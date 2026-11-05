"""Run a preset's decks in alternating arm order under per-deck and cumulative cost caps.

  python3 scripts/svg_live_measurement/paired_run.py --preset p8 --out /var/tmp/svg-live/p8
  python3 scripts/svg_live_measurement/paired_run.py --preset s3 --out /var/tmp/svg-live/s3 --plan
  python3 scripts/svg_live_measurement/paired_run.py --preset baseline --out /var/tmp/svg-live/dry --fake \
      --repeats 1 --prompts p1 --body-override '{"common_header_mode":"free"}'

Presets are in presets.json (arms = backend env + request-body overrides). Each deck lands in
<out>/runs/NN-<prompt>-<arm>/ (see run_deck.py); a deck with a summary.json is skipped, so a rerun
resumes. The loop stops when a deck passes --per-deck-cap, the total passes --total-cap, or a deck
cannot start or is aborted mid-run by the cost watchdog. It ends with blind.py and summarize.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STOP_STATES = {"admission_refused", "backend_failed", "cost_cap_abort"}


def build_order(preset: dict, prompts: list[str], repeats: int) -> list[tuple[str, str]]:
    """(prompt, arm) pairs: repeat-major, prompts alternating, arm order flipping every cell."""
    arms = list(preset["arms"])
    order = []
    for repeat in range(repeats):
        for prompt_index, prompt in enumerate(prompts):
            shift = (repeat + prompt_index) % len(arms)
            order.extend((prompt, arm) for arm in arms[shift:] + arms[:shift])
    return order


def existing_cost(runs: Path) -> float:
    total = 0.0
    for path in runs.glob("*/summary.json"):
        total += json.loads(path.read_text()).get("cost_usd") or 0.0
    return total


def main() -> int:
    presets = json.loads((HERE / "presets.json").read_text())
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", required=True, choices=sorted(presets))
    parser.add_argument("--out", required=True)
    parser.add_argument("--fake", action="store_true", help="fake LLM clients (dry run, no key, no cost)")
    parser.add_argument("--port", type=int, default=8732)
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--prompts", help="comma list of body names in bodies/ (default: the preset's)")
    parser.add_argument("--per-deck-cap", type=float)
    parser.add_argument("--total-cap", type=float)
    parser.add_argument("--body-override", default="{}", help="JSON merged over every body after the arm's own")
    parser.add_argument("--first", type=int, default=1, help="skip runs numbered below this")
    parser.add_argument("--plan", action="store_true", help="print the run order and worst-case budget, run nothing")
    args = parser.parse_args()

    preset = presets[args.preset]
    prompts = args.prompts.split(",") if args.prompts else preset["prompts"]
    repeats = args.repeats or preset["repeats"]
    per_deck = args.per_deck_cap or preset["caps"]["per_deck_usd"]
    total_cap = args.total_cap or preset["caps"]["total_usd"]
    order = build_order(preset, prompts, repeats)
    out = Path(args.out).resolve()
    runs = out / "runs"

    print(f"preset {args.preset}: {preset['description']}\nrequires: {preset['requires']}")
    print(f"{len(order)} decks, per-deck cap ${per_deck}, total cap ${total_cap}, rule {preset['rule']}")
    for number, (prompt, arm) in enumerate(order, start=1):
        print(f"  {number:02d} {prompt} arm {arm}")
    if args.plan:
        return 0
    runs.mkdir(parents=True, exist_ok=True)
    (out / "preset.json").write_text(json.dumps({"name": args.preset, **preset, "order": order}, indent=1))

    total = existing_cost(runs)
    for number, (prompt, arm) in enumerate(order, start=1):
        if number < args.first:
            continue
        run = runs / f"{number:02d}-{prompt}-{arm}"
        if (run / "summary.json").exists():
            print(f"skip {run.name} (done)")
            continue
        arm_def = preset["arms"][arm]
        override = {**arm_def["body"], **json.loads(args.body_override)}
        command = [sys.executable, str(HERE / "run_deck.py"), "--name", run.name, "--arm", arm, "--prompt", prompt,
                   "--body", str(HERE / "bodies" / f"{prompt}.json"), "--body-override", json.dumps(override),
                   "--out", str(run), "--port", str(args.port), "--max-cost", str(min(per_deck, total_cap - total))]
        for key, value in arm_def["env"].items():
            command += ["--env", f"{key}={value}"]
        for key in arm_def["unset"]:
            command += ["--unset", key]
        if args.fake:
            command.append("--fake")
        print(f"== run {number} {prompt} arm {arm}", flush=True)
        subprocess.run(command, check=False)
        summary_path = run / "summary.json"
        if not summary_path.exists():
            print(f"STOP: {run.name} produced no summary")
            break
        summary = json.loads(summary_path.read_text())
        cost = summary.get("cost_usd") or 0.0
        total += cost
        print(f"   deck ${cost:.4f}, total ${total:.4f}", flush=True)
        if summary.get("client_state") in STOP_STATES:
            print(f"STOP: {run.name} ended {summary.get('client_state')}")
            break
        if cost > per_deck or total >= total_cap:
            print(f"STOP: cost cap (deck ${cost:.4f} of ${per_deck}, total ${total:.4f} of ${total_cap})")
            break
    subprocess.run([sys.executable, str(HERE / "blind.py"), str(out)], check=False)
    subprocess.run([sys.executable, str(HERE / "summarize.py"), str(out)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
