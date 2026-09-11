"""Create bounded W22 evidence from the terminal timeline and downloaded PPTX."""

from __future__ import annotations

import hashlib
import json
import pathlib
import xml.etree.ElementTree as ET
import zipfile


ROOT = pathlib.Path(__file__).resolve().parent
PPTX = ROOT / "48-hybrid-export.pptx"
TIMELINE = json.loads((ROOT / "46-timeline.json").read_text(encoding="utf8"))
COMMAND = json.loads((ROOT / "45-command-response.json").read_text(encoding="utf8"))
SOURCE = json.loads((ROOT.parent / "run-11" / "06-final-status.json").read_text(encoding="utf8"))
OPERATION_ID = COMMAND["response"]["operation_id"]
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


operation = next(item for item in TIMELINE["items"] if item.get("operation_id") == OPERATION_ID)
result = operation["result"]
slides = []
all_runs = []
target_text_break = None
with zipfile.ZipFile(PPTX) as archive:
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
        if number == 20:
            for shape in root.findall(".//p:sp", NS):
                non_visual = shape.find("./p:nvSpPr/p:cNvPr", NS)
                name_value = non_visual.get("name", "") if non_visual is not None else ""
                if "q-phone-step-1" in name_value:
                    target_text_break = {
                        "elementId": "q-phone-step-1",
                        "shapeName": name_value,
                        "aTValues": [node.text or "" for node in shape.findall(".//a:t", NS)],
                        "aBrCount": len(shape.findall(".//a:br", NS)),
                        "aPCount": len(shape.findall(".//a:p", NS)),
                    }
                    break
        slides.append({
            "slideNumber": number,
            "pSp": len(root.findall(".//p:sp", NS)),
            "pPic": len(root.findall(".//p:pic", NS)),
            "aTRuns": len(runs),
        })

source_evidence = SOURCE["data"]["quality_gate_receipt"]["sourceReadback"]["evidence"]
source_strings = [entry["renderedText"] for entry in source_evidence]
matched = sum(1 for value in source_strings if value in all_runs)
payload = PPTX.read_bytes()
worker = result.get("public_export_worker_evidence") or {}
execution = result.get("public_export_execution_receipt") or {}
dispatches = result.get("_worker_dispatch_evidence") or []
quality = worker.get("quality") or {}
evidence = {
    "schema": "run-11-authored-hybrid-export-w22-evidence/v1",
    "request": {
        "presentationId": "bc843f70-a28e-45f6-a87f-8a4b6c017928",
        "operationId": operation["operation_id"],
        "idempotencyKey": COMMAND["idempotencyKey"],
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
        "configuredDeckBudgetMillis": 1_496_707,
        "workerDispatchMillisUsed": dispatches[0].get("durationMillis") if dispatches else None,
        "firstDispatchMillis": dispatches[0].get("durationMillis") if dispatches else None,
        "workerExecutionCount": result.get("worker_execution_count"),
        "qualityStatus": quality.get("status"),
        "slideFallbacks": quality.get("slideFallbacks"),
    },
    "artifact": {
        "artifactId": result["artifact_id"],
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "receiptSha256": result.get("artifact_sha256"),
        "digestMatches": hashlib.sha256(payload).hexdigest() == result.get("artifact_sha256"),
        "path": str(PPTX),
    },
    "inspection": {
        "slideCount": len(slides),
        "nativeLayerSlides": sum(1 for slide in slides if slide["pSp"] > 0),
        "totalPSp": sum(slide["pSp"] for slide in slides),
        "totalPPic": sum(slide["pPic"] for slide in slides),
        "totalATRuns": sum(slide["aTRuns"] for slide in slides),
        "sourceTextStringCount": len(source_strings),
        "sourceTextStringsMatchedVerbatimInATRuns": matched,
        "matchMethod": "Each sourceReadback renderedText value counted when it exactly equaled at least one complete a:t node; duplicate source occurrences counted independently.",
        "targetTextBreak": target_text_break,
        "slides": slides,
    },
    "acceptance": {
        "insideGrant": (dispatches[0].get("durationMillis", 1_740_001) if dispatches else 1_740_001) < 1_740_000,
        "deliveredEditableHybrid": worker.get("deliveredExportMode") == "editable-hybrid",
        "native35Of35": sum(1 for slide in slides if slide["pSp"] > 0) == 35,
        "slideFallbacksEmpty": quality.get("slideFallbacks") == [],
    },
}
(ROOT / "47-evidence.json").write_text(
    json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8"
)
print(json.dumps(evidence["operation"], ensure_ascii=False))
print(json.dumps(evidence["inspection"], ensure_ascii=False)[:1000])
print(json.dumps(evidence["acceptance"], ensure_ascii=False))
