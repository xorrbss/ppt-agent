"""Run a prompts x arms x runs matrix of REAL svg deck generations and collect chart metrics.

One deck = one throwaway backend (scripts/server_chart_eval/run_backend_real.sh, env of the arm)
with a fresh app-data directory, one /generate/async request with chained PPTX export, then
metrics.py over the deck's directory. Nothing in the product is touched. Stdlib only.

Run from anywhere with any python3 (the backend uses servers/fastapi/.venv, `uv sync --frozen`):

  PRESENTON_ANTHROPIC_API_KEY=... python3 scripts/server_chart_eval/run_matrix.py \
      --out /var/tmp/server-chart-eval/pilot --arms server,author --runs 1 --max-cost-usd 5

The key reaches the backend process only. State per cell is the directory <out>/<prompt>__<arm>__rN/
(body.json, arm_env.json, backend.log, app/, deck.pptx, metrics.json). A cell with metrics.json is
finished and skipped by --resume (a failed deck is finished too; --retry-failed redoes those).
Cost control: before each deck the runner stops if spent + estimate would pass --max-cost-usd, where
estimate = the largest measured deck cost so far (or --first-deck-estimate-usd before any); a deck is
also aborted in flight above min(--per-deck-cap-usd, remaining budget).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FASTAPI = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(FASTAPI / "scripts" / "svg_live_measurement"))

import collect_run  # noqa: E402  (generic cost / DB helpers of the existing live-measurement kit)
import metrics  # noqa: E402
import run_deck  # noqa: E402  (Http client, readiness, generation polling, process-group stop)

FOLLOWUP_KINDS = ("export.pptx", "svg.chart.verify")
TERMINAL = collect_run.TERMINAL_OPERATION


def cell_id(prompt: str, arm: str, run: int) -> str:
    return f"{prompt}__{arm}__r{run}"


def plan_cells(prompts: list[dict], arms: list[str], runs: int) -> list[dict]:
    """Run-major order; the arm order rotates per prompt so no arm always goes first."""
    cells = []
    for run in range(1, runs + 1):
        for p_index, prompt in enumerate(prompts):
            order = arms[(p_index + run - 1) % len(arms):] + arms[:(p_index + run - 1) % len(arms)]
            for arm in order:
                cells.append({"id": cell_id(prompt["id"], arm, run), "prompt": prompt["id"], "arm": arm,
                              "run": run, "body": prompt["body"]})
    return cells


def wait_followups(app: Path, timeout: float, quiet_s: float = 25.0) -> float:
    """Wait until every export.pptx / svg.chart.verify operation is terminal and none was added for
    ``quiet_s`` (a V5 export enqueues verification rounds, which chain a refreshed export)."""
    start = time.time()
    last_change, last_seen = start, None
    while time.time() - start < timeout:
        try:
            with collect_run._db(app) as db:
                rows = [(r["kind"], r["state"]) for r in db.execute(
                    "SELECT kind, state FROM workspace_operations WHERE kind IN (?, ?)", FOLLOWUP_KINDS)]
        except sqlite3.Error:
            rows = []
        if rows != last_seen:
            last_seen, last_change = rows, time.time()
        if rows and all(state in TERMINAL for _, state in rows) and time.time() - last_change >= quiet_s:
            break
        if not rows and time.time() - start > 90:
            break  # no export was ever enqueued
        time.sleep(5)
    return round(time.time() - start, 1)


def start_backend(args, arm_env: dict, app: Path, out: Path):
    env = dict(os.environ)
    env["PORT"] = str(args.port)
    for knob in ("SVG_SERVER_CHARTS", "SVG_NATIVE_CHART_INTEGRITY_V5"):
        env.pop(knob, None)  # the arm decides, not the calling shell
    env.update(arm_env)
    if args.fake:
        env["FAKE_LLM_CLIENTS"] = "1"
        script = FASTAPI / "scripts" / "fake_llm_e2e" / "run_backend.sh"
        env.pop("PRESENTON_ANTHROPIC_API_KEY", None)
    else:
        script = HERE / "run_backend_real.sh"
    log = open(out / "backend.log", "wb")  # noqa: SIM115 - owned by the child for its lifetime
    import subprocess
    process = subprocess.Popen(["bash", str(script), str(app)], cwd=FASTAPI, env=env, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    (out / "backend.pid").write_text(str(process.pid))
    return process


def run_cell(args, cell: dict, arm_env: dict, out_root: Path, budget_left: float) -> dict:
    out = out_root / cell["id"]
    if out.exists():
        shutil.rmtree(out)
    app = out / "app"
    app.mkdir(parents=True)
    (out / "body.json").write_text(json.dumps(cell["body"], ensure_ascii=False, indent=1))
    (out / "arm_env.json").write_text(json.dumps(arm_env, indent=1))
    http = run_deck.Http(f"http://127.0.0.1:{args.port}")
    process, state, wall, followup_wait = None, "backend_failed", 0.0, 0.0
    started = time.time()
    try:
        process = start_backend(args, arm_env, app, out)
        if run_deck.wait_ready(http, process, 300):
            cap = min(args.per_deck_cap_usd, budget_left)
            state, wall = run_deck.generate(http, cell["body"], app, out, args.timeout, cap)
            if state == "completed" and cell["body"].get("export_as"):
                followup_wait = wait_followups(app, args.export_timeout)
    finally:
        run_deck.stop_backend(process)
    meta = {"cell": cell["id"], "prompt": cell["prompt"], "arm": cell["arm"], "run": cell["run"],
            "mode": "fake" if args.fake else "real", "client_state": state, "wall_s_client": round(wall, 1),
            "followup_wait_s": followup_wait, "wall_s_cell": round(time.time() - started, 1),
            "arm_env": arm_env}
    return metrics.collect_deck(out, meta)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="matrix directory (one subdirectory per deck)")
    parser.add_argument("--prompts", default=str(HERE / "prompts.json"))
    parser.add_argument("--arms-file", default=str(HERE / "arms.json"))
    parser.add_argument("--arms", default="server,author", help="comma list of arm names from the arms file")
    parser.add_argument("--only-prompts", default="", help="comma list of prompt ids (default all)")
    parser.add_argument("--runs", type=int, default=1, help="runs per (prompt, arm) cell")
    parser.add_argument("--n-slides", type=int, default=None, help="override n_slides in every prompt")
    parser.add_argument("--max-cost-usd", type=float, required=True, help="total ceiling over all decks (resumed ones count)")
    parser.add_argument("--per-deck-cap-usd", type=float, default=3.0, help="abort one deck in flight above this")
    parser.add_argument("--first-deck-estimate-usd", type=float, default=1.5, help="estimate before any deck is measured")
    parser.add_argument("--resume", action="store_true", help="skip cells that already have metrics.json")
    parser.add_argument("--retry-failed", action="store_true", help="with --resume, redo cells whose deck did not complete")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--timeout", type=int, default=1800, help="generation seconds per deck")
    parser.add_argument("--export-timeout", type=int, default=1800, help="seconds to wait for export (+ V5 chart verification)")
    parser.add_argument("--fake", action="store_true", help="fake LLM clients: no key, no cost (plumbing check)")
    parser.add_argument("--plan", action="store_true", help="print the cell order and exit")
    args = parser.parse_args()

    arms_all = json.loads(Path(args.arms_file).read_text())
    arm_names = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arm_names if a not in arms_all]
    if unknown:
        raise SystemExit(f"unknown arm(s) {unknown}; known: {sorted(arms_all)}")
    prompts = json.loads(Path(args.prompts).read_text())
    if args.only_prompts:
        keep = set(args.only_prompts.split(","))
        prompts = [p for p in prompts if p["id"] in keep]
    if args.n_slides:
        for prompt in prompts:
            prompt["body"] = {**prompt["body"], "n_slides": args.n_slides}
    cells = plan_cells(prompts, arm_names, args.runs)
    out_root = Path(args.out).resolve()
    if args.plan:
        for cell in cells:
            print(cell["id"], arms_all[cell["arm"]]["env"])
        return 0
    if not args.fake and not os.environ.get("PRESENTON_ANTHROPIC_API_KEY"):
        raise SystemExit("PRESENTON_ANTHROPIC_API_KEY is not set (use --fake for a no-cost dry run)")
    if run_deck._port_busy(args.port):
        raise SystemExit(f"port {args.port} is busy")
    out_root.mkdir(parents=True, exist_ok=True)

    spent, measured, results = 0.0, [], []
    for cell in cells:
        done = out_root / cell["id"] / "metrics.json"
        if args.resume and done.exists():
            prior = json.loads(done.read_text())
            if prior.get("client_state") == "completed" or not args.retry_failed:
                spent += prior.get("cost_usd") or 0.0
                if prior.get("client_state") == "completed":
                    measured.append(prior.get("cost_usd") or 0.0)
                results.append(prior)
                print(f"skip {cell['id']} (done, ${prior.get('cost_usd')})", flush=True)
                continue
    # second pass keeps resumed spending known before any new deck starts
    spent_start = spent
    for cell in cells:
        done = out_root / cell["id"] / "metrics.json"
        if args.resume and done.exists():
            prior = json.loads(done.read_text())
            if prior.get("client_state") == "completed" or not args.retry_failed:
                continue
        estimate = max(measured) if measured else args.first_deck_estimate_usd
        if spent + estimate > args.max_cost_usd:
            print(f"STOP before {cell['id']}: spent ${spent:.2f} + estimate ${estimate:.2f} > ceiling "
                  f"${args.max_cost_usd:.2f}", flush=True)
            break
        print(f"run {cell['id']} (spent ${spent:.2f}, estimate ${estimate:.2f})", flush=True)
        result = run_cell(args, cell, arms_all[cell["arm"]]["env"], out_root, args.max_cost_usd - spent)
        spent += result.get("cost_usd") or 0.0
        if result.get("client_state") == "completed":
            measured.append(result.get("cost_usd") or 0.0)
        results.append(result)
        print(json.dumps({k: result.get(k) for k in ("cell", "client_state", "cost_usd", "wall_s_generate", "slides",
                                                      "charts")}, ensure_ascii=False), flush=True)
        if result.get("client_state") == "backend_failed":
            print("STOP: the backend did not start; fix that before continuing", flush=True)
            break
    print(f"spent ${spent:.2f} (this invocation ${spent - spent_start:.2f}) of ${args.max_cost_usd:.2f}")
    summary = metrics.summarize(out_root)
    (out_root / "summary.txt").write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
