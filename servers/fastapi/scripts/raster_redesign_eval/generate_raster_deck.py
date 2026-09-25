"""Drive the full screenshot-blue raster-reconstruction product path:

  upload -> reference snapshot -> presentation shell -> raster analysis
  -> (review+)approve -> async generate -> export.pptx (chained) + export.pdf
  (follow-up command) -> download artifacts.

Talks directly to the FastAPI backend (default http://localhost:5000, the
local single-origin proxy). No auth is sent; the local stack observed here
answers unauthenticated (DISABLE_AUTH-equivalent), matching Electron/local
dev per RUNNING-LOCAL.md.

Zero third-party deps: stdlib urllib only, so it runs with plain `python`
(no uv sync needed), though `uv run python` from servers/fastapi also works.

Usage:
  python generate_raster_deck.py --source SOURCE.pptx --out OUT_DIR \
      --analysis-mode auto_preserve|reconstruct|auto_redesign \
      [--base http://localhost:5000] [--orientation landscape] \
      [--skip-generate]   # stop after analysis is approved; don't call /generate
      [--skip-pdf]        # don't chase a follow-up export.pdf command

analysis_mode:
  auto_preserve -> every page becomes a full-page screenshot copy, no
                    editable text (this is what produced the KNOWN-BAD
                    negative control). Useful as a fast smoke test of the
                    plumbing, NOT the target result.
  reconstruct   -> per-region text/table/screenshot extraction.
  auto_redesign -> screenshot-blue A4 layout reconstruction; this is the
                    default analysis mode this eval suite targets. Review of
                    any review_required regions is auto-accepted as-is
                    (text/role left unchanged) purely to reach approval; it
                    does NOT edit or correct the detected content.

This module is a thin facade: the actual work lives in raster_deck_http.py
(stdlib urllib JSON client), raster_deck_upload.py (asset upload),
raster_deck_analysis.py (snapshot/shell/raster-analysis/review),
raster_deck_generate.py (async generate + export + download),
raster_deck_evidence.py (product-side raster-render-advisory evidence), and
raster_deck_run.py (DriverRun, the per-stage timing/failure report). Split
out of a single 680-line generate_raster_deck.py (2026-09-25 eval suite) to
keep each module under this repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from raster_deck_analysis import create_presentation_shell, create_reference_snapshot, ensure_approved
from raster_deck_analysis import start_raster_analysis, wait_for_analysis
from raster_deck_evidence import fetch_slide_evidence
from raster_deck_generate import download_artifact, follow_export_to_artifact, generate_async, request_export
from raster_deck_run import DriverRun
from raster_deck_upload import upload_document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base", default="http://localhost:5000")
    parser.add_argument("--orientation", default="landscape", choices=["landscape", "portrait"])
    parser.add_argument("--analysis-mode", default="auto_redesign",
                         choices=["auto_preserve", "reconstruct", "auto_redesign"])
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--include-export-as", action="store_true",
                         help="send export_as in the generate request and verify the legacy "
                              "status-endpoint reaches completed (observational; export is still "
                              "also requested manually afterward)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = Path(args.source)
    run = DriverRun(out_dir)

    with run.stage("upload"):
        asset = upload_document(args.base, source_path)
    with run.stage("reference_snapshot"):
        snapshot = create_reference_snapshot(args.base, asset["asset_id"])
    with run.stage("presentation_create"):
        presentation = create_presentation_shell(args.base, snapshot["snapshot_id"], orientation=args.orientation)

    with run.stage("raster_analysis_start"):
        started = start_raster_analysis(
            args.base, snapshot_id=snapshot["snapshot_id"], presentation_id=presentation["id"],
            orientation=args.orientation, analysis_mode=args.analysis_mode,
        )
    with run.stage("raster_analysis_wait"):
        ready = wait_for_analysis(args.base, started["operation_id"])
    with run.stage("raster_review_approve"):
        approved = ensure_approved(args.base, ready)

    manifest = {
        "presentation_id": presentation["id"],
        "snapshot_id": snapshot["snapshot_id"],
        "analysis_operation_id": started["operation_id"],
        "approval_pin": approved["approval_pin"],
        "manual_review_used": approved.get("_manual_review_used"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[manifest] manual_review_used={manifest['manual_review_used']}")

    if args.skip_generate:
        print("[done] --skip-generate: stopped after approval; see manifest.json")
        return

    with run.stage("generate_async"):
        result = generate_async(
            args.base, presentation_id=presentation["id"], snapshot_id=snapshot["snapshot_id"],
            approval_pin=approved["approval_pin"], orientation=args.orientation,
            include_export_as=args.include_export_as,
        )
    print(f"[generate] done presentation_id={result['presentation_id']}")
    if "status_endpoint_check" in result:
        (out_dir / "status_endpoint_check.json").write_text(
            json.dumps(result["status_endpoint_check"], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[status-endpoint-check] {result['status_endpoint_check']}")

    with run.stage("slide_evidence_fetch"):
        fetch_slide_evidence(args.base, result["presentation_id"], out_dir)

    # Export is always ALSO requested explicitly here (regardless of
    # --include-export-as) so every deck reliably gets its artifacts even if
    # the chain's own export attempt failed or is still catching up.
    with run.stage("export_pptx_request"):
        pptx_op_id = request_export(args.base, result["presentation_id"], "pptx")
    if pptx_op_id:
        with run.stage("export_pptx_follow"):
            export_result = follow_export_to_artifact(
                args.base, presentation_id=result["presentation_id"], operation_id=pptx_op_id,
            )
        with run.stage("export_pptx_download"):
            download_artifact(args.base, export_result["artifact_id"], out_dir / f"{result['presentation_id']}.pptx")

    if not args.skip_pdf:
        with run.stage("export_pdf_request"):
            pdf_op_id = request_export(args.base, result["presentation_id"], "pdf")
        if pdf_op_id:
            with run.stage("export_pdf_follow"):
                pdf_result = follow_export_to_artifact(
                    args.base, presentation_id=result["presentation_id"], operation_id=pdf_op_id,
                )
            with run.stage("export_pdf_download"):
                download_artifact(args.base, pdf_result["artifact_id"], out_dir / f"{result['presentation_id']}.pdf")

    print("[done]")
    print(json.dumps({"timings": run.timings}, indent=2))


if __name__ == "__main__":
    main()
