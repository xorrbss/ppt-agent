"""CLI producer for the Hybrid-only LibreOffice same-file readback receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.authored_vnext.semantic.editable_hybrid_libreoffice_readback import (
    HybridReadbackError,
    run_editable_hybrid_readback,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rc-pptx", required=True, type=Path)
    parser.add_argument("--known-good-control-pptx", required=True, type=Path)
    parser.add_argument("--rc-seal", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output_json.exists():
        raise SystemExit("invocation_failed:output_receipt_must_be_new")
    try:
        seal = json.loads(args.rc_seal.read_text(encoding="utf-8"))
        receipt = run_editable_hybrid_readback(args.rc_pptx, args.known_good_control_pptx, seal, args.work_dir)
    except (OSError, json.JSONDecodeError, HybridReadbackError) as error:
        receipt = {"schemaVersion": "authored-editable-hybrid-libreoffice-readback/v1", "mode": "editable_hybrid", "capabilityId": "editable.hybrid.libreoffice-readback.v1", "status": "failed", "classification": "document_invalid" if str(error).startswith("document_invalid:") else "invocation_failed", "blockers": [str(error)]}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": receipt["status"], "classification": receipt.get("classification"), "receipt": str(args.output_json.resolve())}, ensure_ascii=False))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
