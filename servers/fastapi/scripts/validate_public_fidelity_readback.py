"""Fail-closed Office readback validator for a public Fidelity PPTX.

This is an RC evidence producer, not an exporter and not a release switch.  It
opens one already-published Fidelity artifact, checks its OOXML package, then
uses the Office applications that are actually installed on the host.  Every
result is appended as one JSONL receipt; a missing capability is evidence of a
blocked gate, never a substitute PASS.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping
import zipfile


SCHEMA_VERSION = "public-fidelity-office-readback/v1"
POWERPOINT_SAVE_COPY_FORMAT = 24  # ppSaveAsOpenXMLPresentation
POWERPOINT_PDF_FORMAT = 32  # ppSaveAsPDF


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def inspect_fidelity_pptx(path: Path) -> dict[str, Any]:
    """Validate the minimum OPC shape without treating any ZIP as a PPTX."""

    result: dict[str, Any] = {
        "status": "failed",
        "digest": _sha256_file(path),
        "bytes": path.stat().st_size,
        "slideCount": 0,
        "packageDigest": None,
        "blockers": [],
    }
    try:
        with zipfile.ZipFile(path) as archive:
            names = sorted(archive.namelist())
            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "ppt/presentation.xml",
                "ppt/_rels/presentation.xml.rels",
            }
            missing = sorted(required.difference(names))
            slide_names = [
                name
                for name in names
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ]
            if missing:
                result["blockers"].append("ooxml.required_parts_missing")
            if not slide_names:
                result["blockers"].append("ooxml.slide_parts_missing")
            package = {
                "names": names,
                "partDigests": {
                    name: "sha256:" + sha256(archive.read(name)).hexdigest()
                    for name in names
                },
            }
            result["slideCount"] = len(slide_names)
            result["packageDigest"] = _canonical_digest(package)
            if not result["blockers"]:
                result["status"] = "passed"
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        result["blockers"].append("ooxml.package_unreadable")
    return result


def _find_soffice() -> str | None:
    candidates = [
        shutil.which("soffice"),
        shutil.which("soffice.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "LibreOffice" / "program" / "soffice.exe"),
        str(Path(os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")) / "LibreOffice" / "program" / "soffice.exe"),
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


def libreoffice_readback(input_path: Path, work_dir: Path) -> dict[str, Any]:
    executable = _find_soffice()
    if executable is None:
        return {"capability": "missing", "status": "unavailable", "blockers": ["libreoffice.unavailable"]}
    output_dir = work_dir / "libreoffice"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "--headless",
        "--convert-to",
        "pptx",
        "--outdir",
        str(output_dir),
        str(input_path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"capability": "available", "status": "failed", "blockers": ["libreoffice.open_save_failed"]}
    output_path = output_dir / input_path.name
    if completed.returncode != 0 or not output_path.is_file():
        return {"capability": "available", "status": "failed", "blockers": ["libreoffice.open_save_failed"]}
    output = inspect_fidelity_pptx(output_path)
    blockers = list(output["blockers"])
    if output["status"] != "passed":
        blockers.append("libreoffice.readback_package_invalid")
    return {
        "capability": "available",
        "status": "passed" if not blockers else "failed",
        "outputDigest": output["digest"],
        "outputPackageDigest": output["packageDigest"],
        "slideCount": output["slideCount"],
        "blockers": blockers,
    }


def powerpoint_readback(input_path: Path, work_dir: Path) -> dict[str, Any]:
    """Open/save-copy/reopen/render through a short-lived, owned COM launch.

    The PowerShell child records the pre/post POWERPNT PID sets.  It always
    calls Close/Quit and never kills a remaining PID: a non-empty post-launch
    delta is reported as a cleanup blocker rather than risking another user's
    desktop instance.
    """

    if os.name != "nt" or shutil.which("powershell") is None:
        return {"capability": "missing", "status": "unavailable", "blockers": ["powerpoint.com_unavailable"]}
    output_dir = work_dir / "powerpoint"
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_path = output_dir / "reopened-copy.pptx"
    pdf_path = output_dir / "render.pdf"
    command = r'''
$ErrorActionPreference = 'Stop'
$before = @(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$app = $null
$presentation = $null
try {
  $app = New-Object -ComObject PowerPoint.Application
  $app.Visible = $false
  $presentation = $app.Presentations.Open($env:PUBLIC_FIDELITY_INPUT, $false, $true, $false)
  $presentation.SaveCopyAs($env:PUBLIC_FIDELITY_COPY, 24)
  $presentation.Close()
  $presentation = $app.Presentations.Open($env:PUBLIC_FIDELITY_COPY, $false, $true, $false)
  $presentation.SaveAs($env:PUBLIC_FIDELITY_PDF, 32)
  $presentation.Close()
  $presentation = $null
  $app.Quit()
  $app = $null
  $after = @(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  [pscustomobject]@{ status = 'passed'; before = $before; after = $after } | ConvertTo-Json -Compress
} catch {
  if ($presentation -ne $null) { try { $presentation.Close() } catch {} }
  if ($app -ne $null) { try { $app.Quit() } catch {} }
  $after = @(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  [pscustomobject]@{ status = 'failed'; before = $before; after = $after; error = 'powerpoint_com_operation_failed' } | ConvertTo-Json -Compress
}
'''
    environment = os.environ | {
        "PUBLIC_FIDELITY_INPUT": str(input_path),
        "PUBLIC_FIDELITY_COPY": str(copy_path),
        "PUBLIC_FIDELITY_PDF": str(pdf_path),
    }
    try:
        completed = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env=environment,
        )
        report = json.loads(completed.stdout.strip() or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"capability": "available", "status": "failed", "blockers": ["powerpoint.com_operation_failed"]}
    before = {int(value) for value in report.get("before", []) if isinstance(value, int)}
    after = {int(value) for value in report.get("after", []) if isinstance(value, int)}
    owned_delta = sorted(after.difference(before))
    blockers: list[str] = []
    if report.get("status") != "passed" or not copy_path.is_file() or not pdf_path.is_file():
        blockers.append("powerpoint.open_save_copy_reopen_render_failed")
    if owned_delta:
        blockers.append("powerpoint.owned_process_not_cleanly_exited")
    copy = inspect_fidelity_pptx(copy_path) if copy_path.is_file() else None
    if copy is not None and copy["status"] != "passed":
        blockers.append("powerpoint.readback_package_invalid")
    if pdf_path.is_file() and not pdf_path.read_bytes().startswith(b"%PDF-"):
        blockers.append("powerpoint.render_pdf_invalid")
    return {
        "capability": "available",
        "status": "passed" if not blockers else "failed",
        "copyDigest": copy["digest"] if copy else None,
        "copyPackageDigest": copy["packageDigest"] if copy else None,
        "slideCount": copy["slideCount"] if copy else None,
        "renderDigest": _sha256_file(pdf_path) if pdf_path.is_file() else None,
        # Preserve the complete pre/post evidence, including pre-existing user
        # instances.  The validator intentionally never terminates either set.
        "processesBefore": sorted(before),
        "processesAfter": sorted(after),
        "ownedProcessDelta": owned_delta,
        "blockers": blockers,
    }


def append_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    """Append one fsync'd JSONL record; never rewrite historical evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def build_receipt(
    input_path: Path,
    *,
    work_dir: Path,
    product_operation_id: str | None,
    product_artifact_digest: str | None,
) -> dict[str, Any]:
    ooxml = inspect_fidelity_pptx(input_path)
    product_binding_complete = bool(product_operation_id) and product_artifact_digest == ooxml["digest"]
    if ooxml["status"] != "passed":
        office_not_run_blocker = "ooxml.blocked"
    elif not product_binding_complete:
        # A valid-looking arbitrary PPTX is not release evidence.  Do not open
        # it in host Office applications before it is bound to the product
        # operation and the downloaded artifact digest.
        office_not_run_blocker = "product_path_artifact_binding_missing"
    else:
        office_not_run_blocker = None
    office_not_run = (
        {"capability": "not_run", "status": "not_run", "blockers": [office_not_run_blocker]}
        if office_not_run_blocker
        else None
    )
    libreoffice = libreoffice_readback(input_path, work_dir) if office_not_run is None else office_not_run
    powerpoint = powerpoint_readback(input_path, work_dir) if office_not_run is None else office_not_run
    blockers = list(ooxml["blockers"])
    for office in (libreoffice, powerpoint):
        blockers.extend(office.get("blockers", ()))
    if not product_binding_complete:
        blockers.append("product_path_artifact_binding_missing")
    receipt: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "validatedAt": datetime.now(UTC).isoformat(),
        "input": {"path": str(input_path.resolve()), **ooxml},
        "productBinding": {
            "operationId": product_operation_id,
            "artifactDigest": product_artifact_digest,
            "status": "bound" if product_binding_complete else "unverified",
        },
        "libreOffice": libreoffice,
        "powerPoint": powerpoint,
        "officeReadbackStatus": (
            "passed"
            if product_binding_complete
            and libreoffice["status"] == "passed"
            and powerpoint["status"] == "passed"
            else "blocked"
        ),
        # Other QualityReceipt/artifact publication gates are intentionally
        # outside this script.  It cannot authorize promotion by itself.
        "releaseReadiness": "blocked",
        "promotionReady": False,
        "blockers": sorted(set(blockers + ["quality_receipt_publication_gate_required"])),
    }
    receipt["receiptDigest"] = _canonical_digest(receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", type=Path, help="published Fidelity PPTX to read back")
    parser.add_argument("--receipt", type=Path, required=True, help="append-only JSONL receipt path")
    parser.add_argument("--work-dir", type=Path, help="owned temporary output directory")
    parser.add_argument("--product-operation-id", help="public operation identity for product-path binding")
    parser.add_argument("--product-artifact-digest", help="expected sha256:<hex> of the downloaded artifact")
    args = parser.parse_args(argv)
    if not args.pptx.is_file() or args.pptx.suffix.lower() != ".pptx":
        parser.error("pptx must be an existing .pptx file")
    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="public-fidelity-readback-"))
    work_dir.mkdir(parents=True, exist_ok=True)
    receipt = build_receipt(
        args.pptx,
        work_dir=work_dir,
        product_operation_id=args.product_operation_id,
        product_artifact_digest=args.product_artifact_digest,
    )
    append_receipt(args.receipt, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    office_evidence_complete = receipt["officeReadbackStatus"] == "passed"
    product_binding_complete = receipt["productBinding"]["status"] == "bound"
    return 0 if office_evidence_complete and product_binding_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
