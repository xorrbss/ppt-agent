"""External-worker probe used by the production lifecycle Gate E test.

This process deliberately uses the production worker bootstrap, database,
claim, heartbeat, recovery, and fencing paths.  Only the operation handler is
replaced so the test can deterministically stop a lease owner mid-attempt.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys


# Executing this support file by path makes Python use ``tests/support`` as
# sys.path[0].  Pin the production server root explicitly so the probe behaves
# the same whether pytest was launched from the repository root or this server.
SERVER_ROOT = Path(__file__).resolve().parents[2]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from services import workspace_operation_worker
from services.workspace_operation_handler_registry import (
    WorkspaceOperationResult,
    register_workspace_operation_handler,
)


CRASH_PROBE_OPERATION_KIND = "test.crash_probe"


def _write_claim_marker(context) -> None:
    marker_path = Path(os.environ["WORKSPACE_CRASH_PROBE_MARKER"])
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "operation_id": str(context.claim.operation_id),
        "attempt_number": context.claim.attempt_number,
        "attempt_token": context.claim.attempt_token,
        "fencing_token": context.claim.fencing_token,
    }
    temporary_path = marker_path.with_name(f".{marker_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary_path, marker_path)


async def _execute_probe(context) -> WorkspaceOperationResult:
    _write_claim_marker(context)
    mode = os.environ.get("WORKSPACE_CRASH_PROBE_MODE", "succeed")
    if mode == "block":
        await asyncio.Event().wait()
    if mode != "succeed":
        raise RuntimeError("workspace_crash_probe_mode_invalid")
    return WorkspaceOperationResult(result_revision=context.claim.expected_revision)


def _register_probe_handler() -> None:
    register_workspace_operation_handler(
        CRASH_PROBE_OPERATION_KIND,
        _execute_probe,
        replace=True,
        recovery_policy="automatic",
    )


if __name__ == "__main__":
    workspace_operation_worker.register_workspace_operation_handlers = (
        _register_probe_handler
    )
    workspace_operation_worker.main()
