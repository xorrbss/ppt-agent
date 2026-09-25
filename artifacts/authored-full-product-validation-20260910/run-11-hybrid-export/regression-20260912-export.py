"""Re-run the product-chain Hybrid export for one deck and capture its evidence.

Same command route, idempotency-key shape and inspection as the W22 pair
(``w22-product-export.py`` + ``w22-inspect.py``); the only differences are a
fresh key, the deck passed on the command line, and the 50+ file numbers this
2026-09-12 regression writes.  Nothing here authors a slide: ``export.pptx``
runs entirely inside the export lane and calls no generation provider.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


BASE = "http://localhost:5000/api/v1/ppt/workspace"
TERMINAL = {"succeeded", "failed", "cancelled", "dead_letter", "deadline_exceeded"}
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def request_json(url: str, *, payload: dict[str, object] | None = None) -> tuple[int, dict]:
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


def submit(root: pathlib.Path, presentation_id: str, label: str, revision: int | None) -> tuple[str, float]:
    command_path = root / "50-command-response.json"
    started = time.monotonic()
    if command_path.exists():
        record = json.loads(command_path.read_text(encoding="utf8"))
        print(json.dumps({"operationId": record["response"]["operation_id"], "status": "resuming"}), flush=True)
        return str(record["response"]["operation_id"]), started
    key = f"{label}-hybrid-export-regression-" + dt.datetime.now().strftime("%Y%m%d%H%M%S")
    route = f"{BASE}/presentations/{presentation_id}/commands"
    command: dict[str, object] = {
        "command": "export.pptx",
        "command_type": "export.pptx",
        "idempotency_key": key,
        "asset_ids": [],
        "context": {"format": "pptx", "pptx_mode": "hybrid", "font_embedding": True},
    }
    if revision is not None:
        command["expected_revision"] = revision
    status, response = request_json(route, payload=command)
    command_path.write_text(
        json.dumps(
            {
                "httpStatus": status,
                "idempotencyKey": key,
                "request": {
                    "route": "POST " + route,
                    "presentationId": presentation_id,
                    "commandType": "export.pptx",
                    "expectedRevision": revision,
                    "pptxMode": "hybrid",
                    "fontEmbedding": True,
                },
                "response": response,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf8",
    )
    print(json.dumps({"operationId": response["operation_id"], "status": response.get("status"), "key": key}), flush=True)
    return str(response["operation_id"]), started


def poll(root: pathlib.Path, presentation_id: str, operation_id: str, started: float) -> tuple[dict, dict]:
    timeline_url = f"{BASE}/presentations/{presentation_id}/timeline?limit=100"
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
        print(
            json.dumps(
                {
                    "elapsedSeconds": round(time.monotonic() - started, 1),
                    "status": operation.get("status"),
                    "progress": operation.get("progress"),
                }
            ),
            flush=True,
        )
        if operation.get("status") in TERMINAL:
            (root / "51-timeline.json").write_text(
                json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf8"
            )
            return operation, timeline
        time.sleep(5)


def inspect(pptx: pathlib.Path) -> tuple[list[dict], list[str]]:
    slides: list[dict] = []
    all_runs: list[str] = []
    with zipfile.ZipFile(pptx) as archive:
        names = sorted(
            (
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ),
            key=lambda name: int(name.removeprefix("ppt/slides/slide").removesuffix(".xml")),
        )
        for number, name in enumerate(names, 1):
            root = ET.fromstring(archive.read(name))
            runs = [node.text or "" for node in root.findall(".//a:t", NS)]
            all_runs.extend(runs)
            slides.append(
                {
                    "slideNumber": number,
                    "pSp": len(root.findall(".//p:sp", NS)),
                    "pPic": len(root.findall(".//p:pic", NS)),
                    "aTRuns": len(runs),
                }
            )
    return slides, all_runs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--presentation", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--source-status", required=True)
    parser.add_argument("--expected-revision", type=int, default=None)
    parser.add_argument("--baseline", default=None)
    arguments = parser.parse_args()

    root = pathlib.Path(arguments.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    operation_id, started = submit(root, arguments.presentation, arguments.label, arguments.expected_revision)
    operation, _ = poll(root, arguments.presentation, operation_id, started)
    wall = round(time.monotonic() - started, 1)
    if operation.get("status") != "succeeded":
        raise RuntimeError(f"export_terminal_status:{operation.get('status')}:{operation.get('error')}")

    result = operation.get("result") or {}
    artifact_id = result.get("artifact_id")
    if not artifact_id:
        raise RuntimeError("export_artifact_id_missing")
    with urllib.request.urlopen(f"{BASE}/artifacts/{artifact_id}/download", timeout=180) as handle:
        payload = handle.read()
    pptx = root / "53-hybrid-export.pptx"
    pptx.write_bytes(payload)

    slides, all_runs = inspect(pptx)
    source = json.loads(pathlib.Path(arguments.source_status).read_text(encoding="utf8"))
    source_evidence = source["data"]["quality_gate_receipt"]["sourceReadback"]["evidence"]
    source_strings = [entry["renderedText"] for entry in source_evidence]
    matched = sum(1 for value in source_strings if value in all_runs)

    worker = result.get("public_export_worker_evidence") or {}
    execution = result.get("public_export_execution_receipt") or {}
    dispatches = result.get("_worker_dispatch_evidence") or []
    quality = worker.get("quality") or {}
    command_record = json.loads((root / "50-command-response.json").read_text(encoding="utf8"))
    digest = hashlib.sha256(payload).hexdigest()
    native = sum(1 for slide in slides if slide["pSp"] > 0)

    evidence = {
        "schema": "authored-hybrid-export-regression-20260912/v1",
        "request": {
            "presentationId": arguments.presentation,
            "operationId": operation_id,
            "idempotencyKey": command_record["idempotencyKey"],
            "fontEmbedding": True,
            "pptxMode": "hybrid",
        },
        "operation": {
            "status": operation["status"],
            "requestedExportMode": result.get("requested_export_mode"),
            "deliveredExportMode": worker.get("deliveredExportMode"),
            "hybridDegradedReason": result.get("hybrid_degraded_reason"),
            "operationWallMillis": execution.get("operationWallMillis"),
            "exportWorkerMillis": execution.get("exportWorkerMillis"),
            "clientObservedWallSeconds": wall,
            "workerDispatchMillisUsed": dispatches[0].get("durationMillis") if dispatches else None,
            "workerExecutionCount": result.get("worker_execution_count"),
            "qualityStatus": quality.get("status"),
            "slideFallbacks": quality.get("slideFallbacks"),
        },
        "artifact": {
            "artifactId": artifact_id,
            "bytes": len(payload),
            "sha256": digest,
            "receiptSha256": result.get("artifact_sha256"),
            "digestMatches": digest == result.get("artifact_sha256"),
            "path": str(pptx),
        },
        "inspection": {
            "slideCount": len(slides),
            "nativeLayerSlides": native,
            "totalPSp": sum(slide["pSp"] for slide in slides),
            "totalPPic": sum(slide["pPic"] for slide in slides),
            "totalATRuns": sum(slide["aTRuns"] for slide in slides),
            "sourceTextStringCount": len(source_strings),
            "sourceTextStringsMatchedVerbatimInATRuns": matched,
            "matchMethod": (
                "Each sourceReadback renderedText value counted when it exactly equaled at least "
                "one complete a:t node; duplicate source occurrences counted independently."
            ),
            "slides": slides,
        },
        "acceptance": {
            "deliveredEditableHybrid": worker.get("deliveredExportMode") == "editable-hybrid",
            "nativeLayerOnEverySlide": native == len(slides),
            "slideFallbacksEmpty": quality.get("slideFallbacks") == [],
            "digestMatches": digest == result.get("artifact_sha256"),
        },
    }
    if arguments.baseline:
        baseline = json.loads(pathlib.Path(arguments.baseline).read_text(encoding="utf8"))
        base_operation = baseline["operation"]
        evidence["baselineComparison"] = {
            "baselinePath": arguments.baseline,
            "baselineOperationWallMillis": base_operation.get("operationWallMillis"),
            "baselineExportWorkerMillis": base_operation.get("exportWorkerMillis"),
            "operationWallDeltaMillis": (
                (execution.get("operationWallMillis") or 0) - (base_operation.get("operationWallMillis") or 0)
            ),
            "exportWorkerDeltaMillis": (
                (execution.get("exportWorkerMillis") or 0) - (base_operation.get("exportWorkerMillis") or 0)
            ),
            "baselineArtifactSha256": baseline["artifact"]["sha256"],
            "artifactSha256Identical": baseline["artifact"]["sha256"] == digest,
        }
    (root / "52-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps(evidence["operation"], ensure_ascii=False))
    print(json.dumps(evidence["acceptance"], ensure_ascii=False))


if __name__ == "__main__":
    main()
