from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from visual_parity_gate import GateFailure, canonical_digest, verify_gate


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def descriptor(root: Path, relative: str, value: bytes) -> dict[str, str]:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value)
    return {"path": relative, "sha256": digest(value)}


def png_bytes(slide_number: int) -> bytes:
    color = bytes((slide_number * 10 % 255, 100, 180))
    scanlines = b"".join(b"\x00" + color * 4 for _ in range(4))
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


def make_manifest(root: Path) -> Path:
    presentation_id = "fixture-presentation-visual-parity"
    operation_id = "fixture-operation-visual-parity"
    generation_attempt_id = "fixture-generation-attempt-visual-parity"
    registry = [
        {"slideId": f"slide-{index:02d}", "language": language, "archetypes": archetypes}
        for index, (language, archetypes) in enumerate(
            [
                ("ko", ["card", "metric"]),
                ("ko", ["process", "architecture"]),
                ("zh", ["timeline", "roadmap"]),
                ("zh", ["comparison"]),
                ("ja", ["infographic"]),
                ("ko", ["table"]),
                ("ko", ["chart"]),
                ("zh", ["image"]),
                ("ja", ["card"]),
                ("ko", ["process"]),
                ("zh", ["metric"]),
                ("ja", ["comparison"]),
            ],
                start=1,
        )
    ]
    manifest: dict = {
        "schemaVersion": "authored-vnext-visual-parity-gate/v1",
        "gateId": "gate-visual-parity-001",
        "presentationId": presentation_id,
        "presentationRevision": 12,
        "operationId": operation_id,
        "generationAttemptId": generation_attempt_id,
        "slideRegistry": registry,
        "registryDigest": canonical_digest(registry),
        "freshness": {
            "runId": "run-visual-parity-001",
            "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "reusedFrom": None,
            "previousEvidenceDigest": None,
        },
        "modes": [],
        "diffPolicy": {"maxChangedPixelRatio": 0.02, "minSsim": 0.98},
        "perSlideDiffs": [],
        "manualReviewLedger": {
            "entries": [
                {
                    "reviewer": "qa-reviewer-01",
                    "reviewedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "decision": "accept",
                    "scope": ["all-slides", "titleLineBreakChanges", "clipping", "overlap", "ghosting", "nativeBackplateCollision"],
                }
            ]
        },
    }
    proof_slides = []
    language_by_id = {slide["slideId"]: slide["language"] for slide in registry}
    for slide in registry:
        archetypes = slide["archetypes"]
        marker = " ".join(archetypes)
        metric_suffix = " %" if "metric" in archetypes else ""
        html = f"<html lang='{language_by_id[slide['slideId']]}'><body><main class='{'card' if 'card' in archetypes else 'panel'}'>{marker}{metric_suffix}</main>"
        elements = [{"kind": "text", "semanticRole": "body", "text": marker}]
        if "process" in archetypes:
            elements.append({"kind": "shape", "semanticRole": "diagramNode", "text": "process"})
        if "table" in archetypes:
            html += "<table><tr><td>table</td></tr></table>"
            elements.append({"kind": "table", "semanticRole": "nativeTable", "text": "table"})
        if "chart" in archetypes:
            html += "<div class='chart'>chart</div>"
            elements.append({"kind": "chart", "semanticRole": "nativeChart", "text": "chart"})
        if "image" in archetypes:
            html += "<img style='object-fit:cover' alt='image'/>"
            elements.append({"kind": "image", "semanticRole": "croppedImage", "text": "image"})
        html += "</body></html>"
        proof_slides.append({"slideId": slide["slideId"], "slideIndex": len(proof_slides) + 1, "language": slide["language"], "html": html, "semanticSource": {"schemaVersion": "authored-semantic-source/v1", "elements": elements}})
    proof_descriptor = descriptor(root, "semantic/semantic-proof.json", json.dumps({"schemaVersion": "authored-semantic-proof/v1", "slides": proof_slides}, ensure_ascii=False, sort_keys=True).encode())
    manifest["semanticProof"] = proof_descriptor
    export_attempt_ids = set()
    for mode_index, mode_name in enumerate(("authored", "fidelity", "hybrid"), start=1):
        export_attempt_id = f"export-attempt-{mode_name}-001"
        export_attempt_ids.add(export_attempt_id)
        pptx = descriptor(root, f"{mode_name}/{mode_name}.pptx", b"PK\x03\x04" + mode_name.encode())
        report_value = {
            "schemaVersion": "authored-vnext-visual-parity-report/v1",
            "presentationId": presentation_id,
            "presentationRevision": 12,
            "operationId": operation_id,
            "generationAttemptId": generation_attempt_id,
            "exportAttemptId": export_attempt_id,
            "pptxSha256": pptx["sha256"],
            "defects": {field: 0 for field in ("titleLineBreakChanges", "clipping", "overlap", "ghosting", "nativeBackplateCollision")},
        }
        report_bytes = json.dumps(report_value, ensure_ascii=False, sort_keys=True).encode()
        report = descriptor(root, f"{mode_name}/{mode_name}-report.json", report_bytes)
        first_hashes = []
        reopen_hashes = []
        for slide_number in range(1, len(registry) + 1):
            render_bytes = png_bytes(slide_number)
            first_hashes.append({"slideNumber": slide_number, **descriptor(root, f"{mode_name}/first-open/slide-{slide_number}.png", render_bytes)})
            reopen_hashes.append({"slideNumber": slide_number, **descriptor(root, f"{mode_name}/reopen/slide-{slide_number}.png", render_bytes)})
        manifest["modes"].append(
            {
                "mode": mode_name,
                "attemptId": f"attempt-{mode_name}-001",
                "exportAttemptId": export_attempt_id,
                "presentationId": presentation_id,
                "presentationRevision": 12,
                "operationId": operation_id,
                "generationAttemptId": generation_attempt_id,
                "pptx": pptx,
                "report": report,
                "powerPoint": {"firstOpen": True, "reopened": True, "pptxSha256": pptx["sha256"], "firstOpenRenderHashes": first_hashes, "reopenRenderHashes": reopen_hashes},
                "defects": report_value["defects"],
            }
        )
    for slide_number in range(1, len(registry) + 1):
        manifest["perSlideDiffs"].append(
            {
                "slideNumber": slide_number,
                "pairs": {
                    pair: {"status": "expected"}
                    for pair in ("authored-fidelity", "authored-hybrid", "fidelity-hybrid")
                },
            }
        )
    receipt_value = {
        "schemaVersion": "authored-db-attempt-receipt/v1",
        "presentationId": presentation_id,
        "presentationRevision": 12,
        "operationId": operation_id,
        "generationAttemptId": generation_attempt_id,
        "attemptId": "db-attempt-001",
        "status": "succeeded",
        "exportAttemptIds": sorted(export_attempt_ids),
    }
    receipt = descriptor(root, "db/attempt-receipt.json", json.dumps(receipt_value, sort_keys=True).encode())
    manifest["dbAttemptResolution"] = {
        "schemaVersion": "authored-db-attempt-resolution/v1",
        "authority": "database",
        "status": "resolved",
        "presentationId": presentation_id,
        "operationId": operation_id,
        "generationAttemptId": generation_attempt_id,
        "attemptId": "db-attempt-001",
        "receipt": receipt,
        "receiptSha256": receipt["sha256"],
    }
    authority_value = {"schemaVersion": "authored-release-authority-receipt/v1", "authorityId": "review-board-001", "status": "authorized", "scope": ["all-slides", "titleLineBreakChanges", "clipping", "overlap", "ghosting", "nativeBackplateCollision"]}
    authority_receipt = descriptor(root, "db/release-authority.json", json.dumps(authority_value, sort_keys=True).encode())
    manifest["releaseAuthority"] = {"schemaVersion": "authored-release-authority/v1", "authorityId": "review-board-001", "receipt": authority_receipt, "receiptSha256": authority_receipt["sha256"]}
    manifest["atomicBinding"] = {
        "presentationId": presentation_id,
        "presentationRevision": 12,
        "operationId": operation_id,
        "generationAttemptId": generation_attempt_id,
        "registryDigest": manifest["registryDigest"],
        "exportAttemptIds": sorted(export_attempt_ids),
    }
    manifest["atomicBindingDigest"] = canonical_digest(manifest["atomicBinding"])
    path = root / "gate.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class VisualParityGateTests(unittest.TestCase):
    def test_complete_fixture_passes_without_authorizing_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = verify_gate(make_manifest(Path(directory)))
            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["promotionReady"])
            self.assertEqual(result["languages"], ["ja", "ko", "zh"])

    def test_report_is_sealed_when_trusted_signing_key_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = "visual-gate-test-key-with-at-least-32-bytes"
            with patch.dict(os.environ, {"AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY": key}):
                result = verify_gate(make_manifest(Path(directory)))
            signature = result.pop("authoritySignature")
            expected = "hmac-sha256:" + hmac.new(
                key.encode(),
                json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
                hashlib.sha256,
            ).hexdigest()
            self.assertEqual(signature, expected)

    def test_missing_mode_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_manifest(Path(directory))
            value = json.loads(path.read_text())
            value["modes"].pop()
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(GateFailure, "authored_fidelity_hybrid_modes_required"):
                verify_gate(path)

    def test_diff_defect_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_manifest(Path(directory))
            value = json.loads(path.read_text())
            value["perSlideDiffs"][0]["pairs"]["authored-fidelity"]["changedPixelRatio"] = 0.9
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(GateFailure, "per_slide_diff_1_authored-fidelity_not_passed"):
                verify_gate(path)

    def test_db_attempt_resolution_cannot_be_replaced_by_fixture_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_manifest(Path(directory))
            value = json.loads(path.read_text())
            value["dbAttemptResolution"]["authority"] = "fixture"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(GateFailure, "db_attempt_resolution_required"):
                verify_gate(path)

    def test_expired_freshness_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_manifest(Path(directory))
            value = json.loads(path.read_text())
            value["freshness"]["generatedAt"] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(GateFailure, "freshness_generatedAt_expired"):
                verify_gate(path)

    def test_future_skewed_freshness_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_manifest(Path(directory))
            value = json.loads(path.read_text())
            value["freshness"]["generatedAt"] = (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(GateFailure, "freshness_generatedAt_future"):
                verify_gate(path)


if __name__ == "__main__":
    unittest.main()
