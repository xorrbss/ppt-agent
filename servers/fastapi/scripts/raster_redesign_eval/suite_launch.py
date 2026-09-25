"""`launch`/`_scheduler` subcommands: spawn a detached scheduler process that
drives generate_raster_deck.py for each requested deck, up to --max-parallel
at a time, and survives the calling session ending.

Split out of the original run_suite.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from suite_paths import (
    DEFAULT_CONFIG,
    SUITE_DIR,
    add_decks_root_arg,
    deck_by_id,
    load_config,
    now_iso,
    resolve_deck_path,
    resolve_decks_root,
)

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def cmd_launch(args: argparse.Namespace) -> None:
    cfg = load_config(Path(args.config))
    run_dir = Path(args.out)
    run_dir.mkdir(parents=True, exist_ok=True)
    deck_ids = args.decks.split(",") if args.decks else [d["id"] for d in cfg["decks"]]
    for d in deck_ids:
        deck_by_id(cfg, d)  # validate early, in this (attended) process

    # Run-level provenance: this run went through the launch/_scheduler
    # multi-deck concurrent harness (as opposed to a one-off manual
    # generate_raster_deck.py invocation) -- recorded so `score`/`compare`
    # can show it next to every timing metric (2026-09-25 follow-up).
    (run_dir / "run_config.json").write_text(
        json.dumps({"concurrent_harness": args.concurrent_harness}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    decks_root = resolve_decks_root(args)
    python_exe = args.python or sys.executable
    scheduler_cmd = [
        python_exe, str(SUITE_DIR / "run_suite.py"), "_scheduler",
        "--out", str(run_dir), "--base", args.base,
        "--decks", ",".join(deck_ids), "--max-parallel", str(args.max_parallel),
        "--config", str(Path(args.config).resolve()),
        "--decks-root", str(decks_root),
    ]
    log_out = open(run_dir / "scheduler_stdout.log", "w", encoding="utf-8")
    log_err = open(run_dir / "scheduler_stderr.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        scheduler_cmd, stdout=log_out, stderr=log_err, stdin=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    (run_dir / "scheduler.pid").write_text(str(proc.pid), encoding="utf-8")
    print(f"[launch] scheduler pid={proc.pid} decks={deck_ids} max_parallel={args.max_parallel}")
    print(f"[launch] run dir: {run_dir}")
    print(f"[launch] this process has now returned; the scheduler survives on its own. "
          f"Use `status --out {run_dir}` to poll.")


def cmd_scheduler(args: argparse.Namespace) -> None:
    """Runs detached (spawned by cmd_launch). Blocks in the foreground of its
    own detached process until every requested deck's driver has exited."""
    cfg = load_config(Path(args.config))
    run_dir = Path(args.out)
    run_dir.mkdir(parents=True, exist_ok=True)
    decks_root = resolve_decks_root(args)
    deck_ids = args.decks.split(",")
    decks = [deck_by_id(cfg, d) for d in deck_ids]
    state_path = run_dir / "scheduler_state.json"
    state: dict[str, dict] = {d["id"]: {"status": "queued"} for d in decks}

    def write_state(done: bool = False) -> None:
        payload = {"updated_at": now_iso(), "done": done, "decks": state}
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    write_state()
    driver = SUITE_DIR / "generate_raster_deck.py"
    pending = list(decks)
    running: dict[str, dict] = {}

    while pending or running:
        while pending and len(running) < args.max_parallel:
            d = pending.pop(0)
            deck_out = run_dir / d["id"]
            deck_out.mkdir(parents=True, exist_ok=True)
            source = resolve_deck_path(decks_root, d["source_pptx"])
            cmd = [
                sys.executable, str(driver),
                "--source", str(source), "--out", str(deck_out),
                "--base", args.base,
                "--orientation", cfg["product_defaults"]["orientation"],
                "--analysis-mode", cfg["product_defaults"]["analysis_mode"],
            ]
            stdout_f = open(deck_out / "driver_stdout.log", "w", encoding="utf-8")
            stderr_f = open(deck_out / "driver_stderr.log", "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, stdout=stdout_f, stderr=stderr_f, stdin=subprocess.DEVNULL)
            running[d["id"]] = {"proc": proc, "files": (stdout_f, stderr_f)}
            state[d["id"]] = {"status": "running", "pid": proc.pid, "start_time": now_iso()}
            write_state()

        time.sleep(5)
        finished = [k for k, v in running.items() if v["proc"].poll() is not None]
        for k in finished:
            v = running.pop(k)
            rc = v["proc"].returncode
            for f in v["files"]:
                f.close()
            state[k]["status"] = "succeeded" if rc == 0 else "failed"
            state[k]["returncode"] = rc
            state[k]["end_time"] = now_iso()
            write_state()

    write_state(done=True)


def add_launch_subparser(sub) -> None:
    p_launch = sub.add_parser("launch")
    p_launch.add_argument("--base", default="http://localhost:5000")
    p_launch.add_argument("--out", required=True)
    p_launch.add_argument("--decks", default=None, help="comma-separated deck ids; default all")
    p_launch.add_argument("--max-parallel", type=int, default=2)
    p_launch.add_argument("--config", default=str(DEFAULT_CONFIG))
    p_launch.add_argument("--python", default=None, help="python.exe to run the scheduler+driver with")
    p_launch.add_argument("--concurrent-harness", action=argparse.BooleanOptionalAction, default=True,
                           help="record whether this run used the launch/_scheduler multi-deck concurrent "
                                "harness (default: true, since launch always does) -- written to "
                                "RUN_DIR/run_config.json for score/compare to show next to timing metrics")
    add_decks_root_arg(p_launch)
    p_launch.set_defaults(func=cmd_launch)

    p_sched = sub.add_parser("_scheduler")
    p_sched.add_argument("--out", required=True)
    p_sched.add_argument("--base", required=True)
    p_sched.add_argument("--decks", required=True)
    p_sched.add_argument("--max-parallel", type=int, required=True)
    p_sched.add_argument("--config", required=True)
    add_decks_root_arg(p_sched)
    p_sched.set_defaults(func=cmd_scheduler)
