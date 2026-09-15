from __future__ import annotations

import hashlib
import html
import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "paid-trial-runs"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def median(values: list[float]) -> float:
    return round(statistics.median(values), 3)


def main() -> None:
    before = load(RUNS / "execution-hashes-before.json")
    after = load(RUNS / "execution-hashes-after.json")
    ledger = load(ROOT / "paid-trial-call-ledger.json")
    joined = load(ROOT / "evaluation/paid-trial-independent-join.json")
    rows: list[dict[str, Any]] = []
    for arm in ("baseline", "dense-layout-supplement-v1"):
        for case_dir in sorted((RUNS / arm).iterdir()):
            if not case_dir.is_dir():
                continue
            receipt = load(case_dir / "off/model-receipt.json")
            timing = load(case_dir / "off/timing.json")
            trial = load(case_dir / "trial-timing.json")
            pipeline = load(case_dir / "on/pipeline-timing.json")
            fit = load(case_dir / "on/product-fit-timing.json")
            changed = bool(pipeline["html"]["captureChangedHtml"])
            rows.append({
                "arm": arm,
                "sampleId": case_dir.name,
                "requestedModel": receipt.get("requestedModel"),
                "requestedEffort": receipt.get("requestedEffort"),
                "returnedModel": receipt.get("returnedModel"),
                "usagePresent": bool(receipt.get("usage")),
                "transportReceiptPresent": bool(receipt.get("transportReceipt")),
                "callOrdinal": receipt.get("callOrdinal"),
                "authorProviderMs": timing["providerPhaseMs"],
                "offCaptureMs": timing["renderMs"],
                "offInspectionMs": timing["inspectionMs"],
                "onAdditionalMs": pipeline["timing"]["elapsedMs"],
                "onFitCaptureMs": fit["fitRenderMs"],
                "onVerifyCaptureMs": fit["verifyRenderMs"] if changed else 0.0,
                "fitStatus": (fit.get("fitReport") or {}).get("status"),
                "actualTrialTotalMs": trial["actualTotalMs"],
                "sharedAuthorThroughOffInspectionMs": trial.get("sharedAuthorThroughOffInspectionMs"),
                "fitChangedHtml": changed,
                "offHtmlPngBound": load(case_dir / "off/render-evidence.json")["htmlDigest"] == "sha256:" + sha(case_dir / "off/slide.html"),
                "onHtmlPngBound": load(case_dir / "on/render-evidence.json")["htmlDigest"] == "sha256:" + sha(case_dir / "on/final.html"),
            })
    changed_count = sum(row["fitChangedHtml"] for row in rows)
    actual_capture_count = len(rows) + len(rows) + changed_count
    aggregate = {
        "schemaVersion": "sol-common-repair-paid-runtime/v1",
        "status": "runtime_accounting_complete_quality_gate_separate",
        "providerCallsAddedByAggregation": 0,
        "hashes": {"before": before, "after": after, "allEqual": before == after},
        "ledger": {
            "count": len(ledger.get("calls", [])),
            "succeeded": sum(call.get("status") == "succeeded" for call in ledger.get("calls", [])),
            "active": sum(call.get("status") == "reserved" for call in ledger.get("calls", [])),
            "cap": 12,
        },
        "receipts": {
            "count": len(rows),
            "requestedModelCounts": {"gpt-5.6-sol": sum(row["requestedModel"] == "gpt-5.6-sol" for row in rows)},
            "requestedEffortCounts": {"medium": sum(row["requestedEffort"] == "medium" for row in rows)},
            "returnedModelCounts": {"unreported": sum(row["returnedModel"] is None for row in rows)},
            "usagePresent": sum(row["usagePresent"] for row in rows),
            "transportReceiptPresent": sum(row["transportReceiptPresent"] for row in rows),
        },
        "outputs": {
            "paid": 24,
            "providerZeroDryRunExcluded": 2,
            "neutralReviewed": joined["reviewed"],
        },
        "captures": {
            "offFitFalse": len(rows),
            "onFitTrue": len(rows),
            "onFitChangedHtml": changed_count,
            "onFitFalseVerificationRecaptures": changed_count,
            "fitStatusCounts": {
                status: sum(row["fitStatus"] == status for row in rows)
                for status in sorted({str(row["fitStatus"]) for row in rows})
            },
            "actualTotal": actual_capture_count,
            "htmlPngBindingsPassed": sum(row["offHtmlPngBound"] and row["onHtmlPngBound"] for row in rows),
        },
        "timingMs": {
            key: {"median": median([float(row[key]) for row in rows]), "values": [row[key] for row in rows]}
            for key in ("authorProviderMs", "offCaptureMs", "offInspectionMs", "onAdditionalMs", "onFitCaptureMs", "onVerifyCaptureMs", "actualTrialTotalMs")
        },
        "timingInterpretation": "Author, off capture/inspection, and on additional times remain separate. actualTrialTotalMs is the uninterrupted wall time of this sequential off-then-on experiment, not standalone product latency.",
        "quality": "See independent join and the separate root/lead quality gate; this runtime report does not promote quality.",
        "rows": rows,
    }
    changed_verify_values = [float(row["onVerifyCaptureMs"]) for row in rows if row["fitChangedHtml"]]
    aggregate["timingMs"]["onVerifyCaptureChangedOnlyMs"] = {
        "median": median(changed_verify_values) if changed_verify_values else None,
        "values": changed_verify_values,
    }
    arm_fields = (
        "authorProviderMs",
        "offCaptureMs",
        "offInspectionMs",
        "sharedAuthorThroughOffInspectionMs",
        "onAdditionalMs",
    )
    aggregate["timingByArmMs"] = {}
    for arm in ("baseline", "dense-layout-supplement-v1"):
        arm_rows = [row for row in rows if row["arm"] == arm]
        aggregate["timingByArmMs"][arm] = {
            field: {
                "median": median([float(row[field]) for row in arm_rows if row.get(field) is not None])
                if any(row.get(field) is not None for row in arm_rows)
                else None,
                "values": [row.get(field) for row in arm_rows],
            }
            for field in arm_fields
        }
    paired: list[dict[str, Any]] = []
    for sample_id in sorted({row["sampleId"] for row in rows}):
        baseline = next((row for row in rows if row["sampleId"] == sample_id and row["arm"] == "baseline"), None)
        improved = next((row for row in rows if row["sampleId"] == sample_id and row["arm"] == "dense-layout-supplement-v1"), None)
        differences = {}
        for field in arm_fields:
            left = baseline.get(field) if baseline else None
            right = improved.get(field) if improved else None
            differences[field] = round(float(right) - float(left), 3) if left is not None and right is not None else None
        paired.append({"sampleId": sample_id, "differenceDirection": "improved-minus-baseline", "differenceMs": differences})
    aggregate["sameSamplePromptPairs"] = paired
    (ROOT / "paid-trial-runtime-summary.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    by_key = {(row["sampleId"], row["arm"], row["branch"]): row for row in joined["rows"]}
    samples = sorted({row["sampleId"] for row in joined["rows"]})
    cells = []
    for sample_id in samples:
        blocks = []
        for arm, branch, label in (
            ("baseline", "off", "Baseline / Off"),
            ("baseline", "on", "Baseline / On"),
            ("dense-layout-supplement-v1", "off", "Improved / Off"),
            ("dense-layout-supplement-v1", "on", "Improved / On"),
        ):
            item = by_key[(sample_id, arm, branch)]
            item_id = item["itemId"]
            base = f"neutral-packets/{item_id}"
            blocks.append(f'<article><h3>{label}</h3><a href="{base}/render.png"><img src="{base}/render.png"></a><p><a href="{base}/content-audit.json">content review</a> · <a href="{base}/visual-review.json">visual review</a> · <a href="{base}/slide.html">HTML</a></p></article>')
        cells.append(f'<section><h2>{html.escape(sample_id)}</h2><div class="grid">{"".join(blocks)}</div></section>')
    gallery = '<!doctype html><meta charset="utf-8"><title>Paid trial 4-cell gallery</title><style>body{font-family:Arial,sans-serif;margin:24px;background:#f4f5f7}section{margin:0 0 36px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}article{background:white;padding:12px;border-radius:8px}img{width:100%;height:auto;border:1px solid #ccd}h3{margin:0 0 8px}a{color:#2457c5}</style>' + ''.join(cells)
    (ROOT / "paid-trial-gallery.html").write_text(gallery, encoding="utf-8")


if __name__ == "__main__":
    main()
