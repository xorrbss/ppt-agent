#!/usr/bin/env python3
"""Convert the host-safe FastAPI/Next baseline summaries to gate observations.

The adapter is intentionally conservative.  A baseline field that does not
prove a gate boundary is left unobserved; it is never inferred as zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable


BASELINE_SCHEMA = "authored-renderer-baseline-v1"
OBSERVATION_SCHEMA = "authored-security-observation/v1"


class AdapterError(ValueError):
    pass


def _evidence(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError(f"baseline could not be read ({type(exc).__name__})") from exc
    if not isinstance(value, dict) or value.get("schema_version") != BASELINE_SCHEMA:
        raise AdapterError("baseline schema is not authored-renderer-baseline-v1")
    return value


def _counter(document: dict[str, Any], observation: str, field: str) -> int:
    observations = document.get("observations")
    if not isinstance(observations, dict):
        raise AdapterError("baseline observations must be an object")
    item = observations.get(observation)
    if not isinstance(item, dict):
        raise AdapterError(f"baseline observation {observation} is missing")
    value = item.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AdapterError(f"baseline counter {observation}.{field} is invalid")
    return value


def adapt(fastapi: dict[str, Any], nextjs: dict[str, Any]) -> dict[str, Any]:
    for document in (fastapi, nextjs):
        if document.get("schema_version") != BASELINE_SCHEMA:
            raise AdapterError("baseline schema is not authored-renderer-baseline-v1")

    host_attempts = sum(_counter(doc, "host_file_read", "attempts") for doc in (fastapi, nextjs))
    host_violations = sum(
        _counter(doc, "host_file_read", "successful_reads") for doc in (fastapi, nextjs)
    )
    loopback_attempts = sum(_counter(doc, "loopback_egress", "attempts") for doc in (fastapi, nextjs))
    loopback_violations = sum(
        _counter(doc, "loopback_egress", "successful_responses") for doc in (fastapi, nextjs)
    )
    reported_errors = fastapi.get("errors", [])
    if not isinstance(reported_errors, list):
        raise AdapterError("baseline errors must be an array")

    return {
        "schema_version": OBSERVATION_SCHEMA,
        "run_id": "current-fastapi-next-baseline",
        "execution": {
            "completed": True,
            "timed_out": False,
            "crashed": False,
            # monitor_healthy/isolation_intact and launch-option inspection were
            # not established by the host-safe probes and remain missing.
            "host_stability_risk_cases_executed": [],
            "observer_errors": ["baseline-reported-error"] if reported_errors else [],
            "reported_kill_switch_events": [],
        },
        # The baseline ran on the host, not in the hardened harness.  An empty
        # object makes every required containment control INCONCLUSIVE.
        "containment": {},
        "boundaries": {
            "host_read": {
                "observed": True,
                "attempts": host_attempts,
                "blocked": max(host_attempts - host_violations, 0),
                "violations": host_violations,
                "evidence": [_evidence("baseline:fastapi:host-read"), _evidence("baseline:next:host-read")],
            },
            "loopback_egress": {
                "observed": True,
                "attempts": loopback_attempts,
                "blocked": max(loopback_attempts - loopback_violations, 0),
                "violations": loopback_violations,
                "evidence": [_evidence("baseline:fastapi:loopback"), _evidence("baseline:next:loopback")],
            },
            "external_egress": {"observed": False, "violations": None, "evidence": []},
            "metadata_egress": {"observed": False, "violations": None, "evidence": []},
            "child_process": {"observed": False, "violations": None, "evidence": []},
            "ipc": {"observed": False, "violations": None, "evidence": []},
            "secret_log": {"observed": False, "violations": None, "evidence": []},
            "resource_escape": {"observed": False, "violations": None, "evidence": []},
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fastapi", type=Path, required=True)
    parser.add_argument("--next", dest="nextjs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        observation = adapt(_load(args.fastapi), _load(args.nextjs))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
        temporary.write_text(
            json.dumps(observation, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, args.output)
    except (AdapterError, OSError) as exc:
        if isinstance(exc, AdapterError):
            print(str(exc), file=sys.stderr)
        else:
            print(f"adapter output failed ({type(exc).__name__})", file=sys.stderr)
        return 3
    print(f"wrote={args.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
