"""Generation + export phase of the product-path driver
(generate_raster_deck.py): async generate -> poll the workspace timeline ->
export.pptx/pdf commands -> follow to artifact -> download.

Split out of the original generate_raster_deck.py (2026-09-25 eval suite) to
keep each module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import time
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from raster_deck_http import ApiError, request as _request

TIMELINE_POLL_SECONDS = 5
STATUS_POLL_SECONDS = 5

_TERMINAL_OP_STATES = ("succeeded", "failed", "cancelled", "dead_letter")


def _find_timeline_operation(base: str, presentation_id: str, operation_id: str) -> Optional[dict]:
    timeline = _request(base, "GET", f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline")
    return next(
        (i for i in timeline.get("items", [])
         if i.get("anchor_type") == "operation" and i.get("operation_id") == operation_id),
        None,
    )


def generate_async(base: str, *, presentation_id: str, snapshot_id: str,
                    approval_pin: dict, orientation: str, max_wait_seconds: int = 1800,
                    include_export_as: bool = False, export_as_value: str = "pptx",
                    verify_status_endpoint_seconds: int = 180) -> dict:
    """Start generation and wait for the WORKSPACE OPERATION itself to reach
    a terminal state, polled directly off the timeline (authoritative, not
    the legacy compat endpoint -- see below).

    include_export_as=False (default): omits `export_as` from the request so
    the (previously broken) export chain never fires; export is requested
    separately via request_export() once this returns.

    include_export_as=True: sends `export_as` as before AND, once the
    workspace operation succeeds, spends up to verify_status_endpoint_seconds
    polling the legacy async-status endpoint (GET /presentation/status/{id})
    to check the fix to the export-chain rollback bug: it must reach
    "completed" with either an export_operation_id or an explicit export
    error in its data -- never stay stuck at "running" or "error" forever
    (that was the pre-fix defect). This check is purely observational: its
    outcome is recorded in the returned dict's "status_endpoint_check" and
    never raises -- generation success/failure is still decided solely by
    the workspace timeline.
    """
    body = {
        "presentation_id": presentation_id,
        "content": "",
        "reference_snapshot_ids": [snapshot_id],
        "source_mode": "raster_reconstruction",
        "raster_approval_pin": approval_pin,
        "strategy": "svg",
        "page_size": "A4",
        "orientation": orientation,
        "template": "authored",
        "authored_style": "screenshot-blue",
        "include_title_slide": False,
        "include_table_of_contents": False,
    }
    if include_export_as:
        body["export_as"] = export_as_value
    started = _request(base, "POST", "/api/v1/ppt/presentation/generate/async", json_body=body)
    task_id = started.get("id")
    operation_id = started.get("operation_id")
    print(f"[generate] task_id={task_id} operation_id={operation_id} include_export_as={include_export_as}")
    if operation_id is None:
        raise RuntimeError(f"generate/async response carried no operation_id: {started}")

    deadline = time.time() + max_wait_seconds
    last_status = None
    while True:
        if time.time() > deadline:
            raise RuntimeError(f"generation did not reach a terminal state within {max_wait_seconds}s "
                                f"(last workspace operation status={last_status})")
        op = _find_timeline_operation(base, presentation_id, operation_id)
        op_status = op.get("status") if op else None
        if op_status != last_status:
            last_status = op_status
            print(f"[generate] workspace operation status={op_status} progress={op.get('progress') if op else None}")
        if op_status == "succeeded":
            result = {"presentation_id": presentation_id, "operation_id": operation_id, "result": op.get("result")}
            if include_export_as and task_id:
                result["status_endpoint_check"] = _verify_status_endpoint(base, task_id, verify_status_endpoint_seconds)
            return result
        if op_status in ("failed", "cancelled", "dead_letter"):
            raise RuntimeError(
                f"generation failed (workspace operation terminal): status={op_status} "
                f"retryCount={op.get('retryCount')} error={op.get('error')} "
                f"error_message={op.get('error_message')}"
            )
        time.sleep(STATUS_POLL_SECONDS)


def _verify_status_endpoint(base: str, task_id: str, budget_seconds: int) -> dict:
    """Observational check of the legacy compat endpoint's fixed behavior.
    Never raises; returns what was actually observed."""
    deadline = time.time() + budget_seconds
    last = None
    while time.time() < deadline:
        try:
            status = _request(base, "GET", f"/api/v1/ppt/presentation/status/{task_id}")
        except ApiError as exc:
            last = {"error": f"status endpoint request failed: {exc}"}
            break
        st = status.get("status")
        if st != last:
            print(f"[status-endpoint-check] status={st}")
        last = status
        if st == "completed":
            data = status.get("data") or {}
            return {
                "reached_completed": True,
                "export_operation_id": data.get("export_operation_id"),
                "export_error": data.get("export_error"),
                "stuck": False,
            }
        if st == "error":
            return {"reached_completed": False, "error": status.get("error"), "stuck": False}
        time.sleep(STATUS_POLL_SECONDS)
    return {"reached_completed": False, "stuck": True, "last_seen": last,
            "note": f"did not reach a terminal state within {budget_seconds}s"}


def follow_export_to_artifact(base: str, *, presentation_id: str, operation_id: str) -> dict:
    while True:
        timeline = _request(base, "GET", f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline")
        item = next(
            (i for i in timeline.get("items", [])
             if i.get("anchor_type") == "operation" and i.get("operation_id") == operation_id),
            None,
        )
        if item and item["status"] in ("succeeded", "failed", "cancelled", "dead_letter"):
            if item["status"] != "succeeded":
                raise RuntimeError(f"export operation {item['status']}: {item.get('result')}")
            return item["result"]
        print(f"[export] waiting... status={item['status'] if item else 'not_found'}")
        time.sleep(TIMELINE_POLL_SECONDS)


def download_artifact(base: str, artifact_id: str, dest: Path) -> Path:
    """Stream the artifact straight to disk in fixed-size chunks -- never
    buffer the whole PPTX/PDF (tens of MB) in memory at once."""
    url = f"{base}/api/v1/ppt/workspace/artifacts/{artifact_id}/download"
    req = urllib.request.Request(url, method="GET")
    chunk_size = 1024 * 1024
    total = 0
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    print(f"[download] {dest} ({total} bytes)")
    return dest


def request_export(base: str, presentation_id: str, fmt: str) -> Optional[str]:
    """Issue an export.pptx / export.pdf workspace command with a fresh,
    unique idempotency key (so a retry after a fix creates a new operation
    instead of replaying a dead_letter one)."""
    command_type = f"export.{fmt}"
    idempotency_key = f"{command_type}:{presentation_id}:{uuid.uuid4().hex[:12]}"
    try:
        result = _request(
            base, "POST", f"/api/v1/ppt/workspace/presentations/{presentation_id}/commands",
            json_body={
                "command": "{}",
                "command_type": command_type,
                "idempotency_key": idempotency_key,
            },
        )
        op_id = result.get("operation_id")
        print(f"[{command_type}] operation_id={op_id} idempotency_key={idempotency_key}")
        return op_id
    except RuntimeError as exc:
        print(f"[{command_type}] command failed: {exc}")
        return None


def request_pdf_export(base: str, presentation_id: str) -> Optional[str]:
    return request_export(base, presentation_id, "pdf")
