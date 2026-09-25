"""Submit and capture the W22 full product-lane Hybrid export evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parent
BASE = "http://localhost:5000/api/v1/ppt/workspace"
PRESENTATION_ID = "bc843f70-a28e-45f6-a87f-8a4b6c017928"
KEY = "run11-hybrid-export-w22-" + dt.datetime.now().strftime("%Y%m%d%H%M%S")
TERMINAL = {"succeeded", "failed", "cancelled", "dead_letter", "deadline_exceeded"}


def request_json(url: str, *, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf8", "replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def main() -> None:
    route = f"{BASE}/presentations/{PRESENTATION_ID}/commands"
    command = {
        "command": "export.pptx",
        "command_type": "export.pptx",
        "idempotency_key": KEY,
        "expected_revision": 1,
        "asset_ids": [],
        "context": {"format": "pptx", "pptx_mode": "hybrid", "font_embedding": True},
    }
    started = time.monotonic()
    command_path = ROOT / "45-command-response.json"
    if command_path.exists():
        command_record = json.loads(command_path.read_text(encoding="utf8"))
        response = command_record["response"]
        operation_id = str(response["operation_id"])
        print(json.dumps({"operationId": operation_id, "status": "resuming", "key": command_record["idempotencyKey"]}), flush=True)
    else:
        status, response = request_json(route, payload=command)
        operation_id = str(response["operation_id"])
        command_record = {
            "httpStatus": status,
            "idempotencyKey": KEY,
            "request": {
                "route": "POST " + route,
                "presentationId": PRESENTATION_ID,
                "commandType": "export.pptx",
                "expectedRevision": 1,
                "pptxMode": "hybrid",
                "fontEmbedding": True,
            },
            "response": response,
        }
        command_path.write_text(
            json.dumps(command_record, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(json.dumps({"operationId": operation_id, "status": response.get("status"), "key": KEY}), flush=True)

    timeline_url = f"{BASE}/presentations/{PRESENTATION_ID}/timeline?limit=100"
    operation = None
    timeline = None
    while True:
        try:
            _, timeline = request_json(timeline_url)
        except RuntimeError as error:
            print(json.dumps({"status": "poll-retry", "error": str(error)[:160]}), flush=True)
            time.sleep(5)
            continue
        operation = next(
            (item for item in timeline.get("items", []) if item.get("operation_id") == operation_id),
            None,
        )
        if operation is None:
            raise RuntimeError("operation_missing_from_timeline")
        elapsed = round(time.monotonic() - started, 1)
        print(json.dumps({"elapsedSeconds": elapsed, "status": operation.get("status"), "progress": operation.get("progress")}), flush=True)
        if operation.get("status") in TERMINAL:
            break
        time.sleep(5)

    (ROOT / "46-timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf8"
    )
    if operation.get("status") != "succeeded":
        raise RuntimeError(f"export_terminal_status:{operation.get('status')}:{operation.get('error')}")
    result = operation.get("result") or {}
    artifact_id = result.get("artifact_id")
    if not artifact_id:
        raise RuntimeError("export_artifact_id_missing")
    download_url = f"{BASE}/artifacts/{artifact_id}/download"
    with urllib.request.urlopen(download_url, timeout=120) as response_handle:
        payload = response_handle.read()
    (ROOT / "48-hybrid-export.pptx").write_bytes(payload)
    print(json.dumps({
        "operationId": operation_id,
        "wallSeconds": round(time.monotonic() - started, 1),
        "artifactId": artifact_id,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "receiptSha256": result.get("artifact_sha256"),
    }), flush=True)


if __name__ == "__main__":
    main()
