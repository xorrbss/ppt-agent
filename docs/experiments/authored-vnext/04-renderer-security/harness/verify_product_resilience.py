#!/usr/bin/env python3
"""Observe product kill-switch/LKG behavior and merge it with runtime cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    args = parser.parse_args()
    try:
        observation: dict[str, Any] = json.loads(
            args.observation.read_text(encoding="utf-8")
        )
        containment = observation.get("containment") or {}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lkg = root / "last-known-good.png"
            candidate = root / "candidate.png"
            lkg.write_bytes(b"known-good-artifact")
            before = hashlib.sha256(lkg.read_bytes()).digest()
            environment = {
                "AUTHORED_RENDERER_MODE": "isolated",
                "AUTHORED_RENDERER_KILL_SWITCH": "true",
                "AUTHORED_RENDERER_LEGACY_ENABLED": "false",
            }
            completed = subprocess.run(
                [sys.executable, str(args.launcher)],
                input=(
                    b'{"schema_version":"authored-render-request/v1",'
                    b'"html":"<main>kill switch probe</main>","timeout":1}\n'
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
                timeout=10,
            )
            response = json.loads(completed.stdout)
            kill_switch = (
                completed.returncode == 1
                and completed.stderr == b""
                and response.get("error") == "renderer-kill-switch-active"
            )
            after = hashlib.sha256(lkg.read_bytes()).digest()
            observation["resilience"] = {
                "crash_cleanup": containment.get("crash_cleanup") is True,
                "timeout_cleanup": containment.get("timeout_cleanup") is True,
                "lkg_preserved": before == after,
                "candidate_unpublished": not candidate.exists(),
                "kill_switch_rejects_new_work": kill_switch,
            }
        temporary_path = args.observation.with_name(
            f".{args.observation.name}.tmp-{os.getpid()}"
        )
        temporary_path.write_text(
            json.dumps(observation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary_path, args.observation)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"product resilience verification failed: {type(exc).__name__}")
        return 3
    failed = [key for key, passed in observation["resilience"].items() if not passed]
    print("resilience=" + ("PASS" if not failed else "FAIL:" + ",".join(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
