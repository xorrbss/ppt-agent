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
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET
import zipfile


SCHEMA_VERSION = "public-fidelity-office-readback/v3"
POWERPOINT_SAVE_COPY_FORMAT = 24  # ppSaveAsOpenXMLPresentation
POWERPOINT_PDF_FORMAT = 32  # ppSaveAsPDF
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


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


def _slide_part_order(name: str) -> int:
    match = re.fullmatch(r"ppt/slides/slide([1-9][0-9]*)\.xml", name)
    return int(match.group(1)) if match else sys.maxsize


def _normalize_expected_digest(value: str) -> str:
    normalized = value.removeprefix("sha256:").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("slide image digest is invalid")
    return "sha256:" + normalized


def _resolve_relationship_target(slide_name: str, target: str) -> str | None:
    if not target or "\\" in target:
        return None
    if target.startswith("/"):
        resolved = posixpath.normpath(target.lstrip("/"))
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(slide_name), target)
        )
    if resolved.startswith("../") or resolved == ".." or resolved.startswith("/"):
        return None
    return resolved


def inspect_fidelity_pptx(
    path: Path,
    *,
    expected_slide_image_digests: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate exact raster bindings without treating any ZIP as Fidelity."""

    result: dict[str, Any] = {
        "status": "failed",
        "digest": _sha256_file(path),
        "bytes": path.stat().st_size,
        "slideCount": 0,
        "packageDigest": None,
        # Fidelity is a raster-retention export mode.  These observations make
        # that scope explicit without overstating a package-level image count
        # as visual-parity or editable-native proof.
        "rasterEvidence": {
            "imagePartCount": 0,
            "slidesWithImageRelationship": 0,
            "slidesWithFullSlideImage": 0,
            "slidesWithExpectedDigestMatch": 0,
            "expectedDigestCount": (
                len(expected_slide_image_digests)
                if expected_slide_image_digests is not None
                else 0
            ),
            "slides": [],
            "visualParity": "not_verified",
            "nativeEditability": "not_assessed",
        },
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
            slide_names = sorted(
                [
                    name
                    for name in names
                    if re.fullmatch(r"ppt/slides/slide[1-9][0-9]*\.xml", name)
                ],
                key=_slide_part_order,
            )
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
            presentation_root = ET.fromstring(archive.read("ppt/presentation.xml"))
            slide_size = presentation_root.find(f"{{{_P_NS}}}sldSz")
            if slide_size is None:
                result["blockers"].append("ooxml.slide_size_missing")
                slide_width = slide_height = None
            else:
                slide_width = int(slide_size.attrib.get("cx", "0"))
                slide_height = int(slide_size.attrib.get("cy", "0"))
                if slide_width <= 0 or slide_height <= 0:
                    result["blockers"].append("ooxml.slide_size_invalid")
                    slide_width = slide_height = None
            image_part_names = [
                name for name in names if name.startswith("ppt/media/")
            ]
            image_relationship_slides = 0
            full_slide_image_slides = 0
            expected_digest_matches = 0
            slide_evidence: list[dict[str, Any]] = []
            normalized_expected: list[str] | None = None
            if expected_slide_image_digests is not None:
                try:
                    normalized_expected = [
                        _normalize_expected_digest(value)
                        for value in expected_slide_image_digests
                    ]
                except ValueError:
                    result["blockers"].append("ooxml.slide_image_expected_digest_invalid")
                    normalized_expected = []
                if len(normalized_expected) != len(slide_names):
                    result["blockers"].append("ooxml.slide_image_expected_digest_count_mismatch")
            for slide_index, slide_name in enumerate(slide_names):
                relationship_name = (
                    "ppt/slides/_rels/" + Path(slide_name).name + ".rels"
                )
                relationships: dict[str, str] = {}
                unresolved_target = False
                if relationship_name in names:
                    relationship_root = ET.fromstring(archive.read(relationship_name))
                    for relationship in relationship_root.findall(
                        f"{{{_PKG_REL_NS}}}Relationship"
                    ):
                        if not relationship.attrib.get("Type", "").endswith("/image"):
                            continue
                        if relationship.attrib.get("TargetMode") == "External":
                            unresolved_target = True
                            continue
                        relationship_id = relationship.attrib.get("Id", "")
                        target = _resolve_relationship_target(
                            slide_name,
                            relationship.attrib.get("Target", ""),
                        )
                        if not relationship_id or target is None or target not in names:
                            unresolved_target = True
                            continue
                        relationships[relationship_id] = target
                if relationships:
                    image_relationship_slides += 1
                else:
                    result["blockers"].append("ooxml.slide_image_relationship_missing")
                if unresolved_target:
                    result["blockers"].append("ooxml.slide_image_target_missing")

                slide_root = ET.fromstring(archive.read(slide_name))
                full_candidates: list[tuple[str, str]] = []
                for picture in slide_root.findall(f".//{{{_P_NS}}}pic"):
                    blip = picture.find(f".//{{{_A_NS}}}blip")
                    transform = picture.find(f".//{{{_A_NS}}}xfrm")
                    offset = (
                        transform.find(f"{{{_A_NS}}}off")
                        if transform is not None
                        else None
                    )
                    extent = (
                        transform.find(f"{{{_A_NS}}}ext")
                        if transform is not None
                        else None
                    )
                    relationship_id = (
                        blip.attrib.get(f"{{{_R_NS}}}embed", "")
                        if blip is not None
                        else ""
                    )
                    if (
                        relationship_id in relationships
                        and offset is not None
                        and extent is not None
                        and slide_width is not None
                        and slide_height is not None
                        and int(offset.attrib.get("x", "-1")) == 0
                        and int(offset.attrib.get("y", "-1")) == 0
                        and int(extent.attrib.get("cx", "-1")) == slide_width
                        and int(extent.attrib.get("cy", "-1")) == slide_height
                    ):
                        target = relationships[relationship_id]
                        digest = "sha256:" + sha256(archive.read(target)).hexdigest()
                        full_candidates.append((target, digest))
                if len(full_candidates) != 1:
                    result["blockers"].append("ooxml.slide_image_geometry_invalid")
                    selected_target = None
                    observed_digest = None
                else:
                    full_slide_image_slides += 1
                    selected_target, observed_digest = full_candidates[0]

                expected_digest = (
                    normalized_expected[slide_index]
                    if normalized_expected is not None
                    and slide_index < len(normalized_expected)
                    else None
                )
                digest_status = "not_provided"
                if expected_digest is not None:
                    digest_status = (
                        "matched" if expected_digest == observed_digest else "mismatched"
                    )
                    if digest_status == "matched":
                        expected_digest_matches += 1
                    else:
                        result["blockers"].append("ooxml.slide_image_digest_mismatch")
                slide_evidence.append(
                    {
                        "slideIndex": slide_index,
                        "slidePart": slide_name,
                        "imageRelationshipCount": len(relationships),
                        "fullSlideImageTarget": selected_target,
                        "observedImageDigest": observed_digest,
                        "expectedImageDigest": expected_digest,
                        "expectedImageDigestStatus": digest_status,
                    }
                )
            result["rasterEvidence"] = {
                "imagePartCount": len(image_part_names),
                "slidesWithImageRelationship": image_relationship_slides,
                "slidesWithFullSlideImage": full_slide_image_slides,
                "slidesWithExpectedDigestMatch": expected_digest_matches,
                "expectedDigestCount": (
                    len(normalized_expected)
                    if normalized_expected is not None
                    else 0
                ),
                "slides": slide_evidence,
                "visualParity": "not_verified",
                "nativeEditability": "not_assessed",
            }
            if not result["blockers"]:
                result["status"] = "passed"
    except (
        OSError,
        KeyError,
        ValueError,
        ET.ParseError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ):
        result["blockers"].append("ooxml.package_unreadable")
    return result


def inspect_pdf(path: Path) -> dict[str, Any]:
    """Return conservative PDF structural evidence, including page count."""

    result: dict[str, Any] = {
        "status": "failed",
        "digest": _sha256_file(path),
        "bytes": path.stat().st_size,
        "pageCount": None,
        "blockers": [],
    }
    try:
        payload = path.read_bytes()
    except OSError:
        result["blockers"].append("pdf.unreadable")
        return result
    if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload:
        result["blockers"].append("pdf.structure_invalid")
        return result
    # PowerPoint emits uncompressed Page dictionaries.  Refuse to infer a
    # count from a /Pages tree or a generic /Count value: those can describe
    # nested trees rather than rendered pages.
    page_count = len(re.findall(rb"/Type\s*/Page\b", payload))
    if page_count <= 0:
        result["blockers"].append("pdf.page_count_not_observed")
        return result
    result["pageCount"] = page_count
    result["status"] = "passed"
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
        return {"capability": "missing", "status": "not_verified", "blockers": ["libreoffice.unavailable"]}
    input_evidence = inspect_fidelity_pptx(input_path)
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
    slide_count_matches_input = output["slideCount"] == input_evidence["slideCount"]
    if not slide_count_matches_input:
        blockers.append("libreoffice.slide_count_mismatch")
    return {
        "capability": "available",
        "status": "passed" if not blockers else "failed",
        "inputDigest": input_evidence["digest"],
        "inputPackageDigest": input_evidence["packageDigest"],
        "inputSlideCount": input_evidence["slideCount"],
        "outputDigest": output["digest"],
        "outputPackageDigest": output["packageDigest"],
        "slideCount": output["slideCount"],
        "slideCountMatchesInput": slide_count_matches_input,
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
        return {"capability": "missing", "status": "not_verified", "blockers": ["powerpoint.com_unavailable"]}
    input_evidence = inspect_fidelity_pptx(input_path)
    output_dir = work_dir / "powerpoint"
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_path = output_dir / "reopened-copy.pptx"
    pdf_path = output_dir / "render.pdf"
    command = r'''
$ErrorActionPreference = 'Stop'
$before = @(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$powerPointType = [type]::GetTypeFromProgID('PowerPoint.Application')
if ($null -eq $powerPointType) {
  [pscustomobject]@{ capability = 'missing'; status = 'not_verified'; before = $before; after = $before; error = 'powerpoint_com_unavailable' } | ConvertTo-Json -Compress
  exit 0
}
$app = $null
$presentation = $null
try {
  $app = New-Object -ComObject PowerPoint.Application
  $app.Visible = $false
  $presentation = $app.Presentations.Open($env:PUBLIC_FIDELITY_INPUT, $false, $true, $false)
  $sourceSlideCount = $presentation.Slides.Count
  $presentation.SaveCopyAs($env:PUBLIC_FIDELITY_COPY, 24)
  $presentation.Close()
  $presentation = $app.Presentations.Open($env:PUBLIC_FIDELITY_COPY, $false, $true, $false)
  $reopenedSlideCount = $presentation.Slides.Count
  $presentation.SaveAs($env:PUBLIC_FIDELITY_PDF, 32)
  $presentation.Close()
  $presentation = $null
  $app.Quit()
  $app = $null
  $after = @(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  [pscustomobject]@{ capability = 'available'; status = 'passed'; before = $before; after = $after; sourceSlideCount = $sourceSlideCount; reopenedSlideCount = $reopenedSlideCount } | ConvertTo-Json -Compress
} catch {
  if ($presentation -ne $null) { try { $presentation.Close() } catch {} }
  if ($app -ne $null) { try { $app.Quit() } catch {} }
  $after = @(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  [pscustomobject]@{ capability = 'available'; status = 'failed'; before = $before; after = $after; error = 'powerpoint_com_operation_failed' } | ConvertTo-Json -Compress
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
    if report.get("capability") == "missing":
        return {
            "capability": "missing",
            "status": "not_verified",
            "blockers": ["powerpoint.com_unavailable"],
        }
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
    pdf = inspect_pdf(pdf_path) if pdf_path.is_file() else None
    if pdf is None or pdf["status"] != "passed":
        blockers.append("powerpoint.render_pdf_invalid")
    source_slide_count = report.get("sourceSlideCount")
    reopened_slide_count = report.get("reopenedSlideCount")
    expected_slide_count = input_evidence["slideCount"]
    source_slide_count_matches_input = source_slide_count == expected_slide_count
    reopened_slide_count_matches_input = reopened_slide_count == expected_slide_count
    copy_slide_count_matches_input = copy is not None and copy["slideCount"] == expected_slide_count
    pdf_page_count_matches_input = pdf is not None and pdf["pageCount"] == expected_slide_count
    if not source_slide_count_matches_input:
        blockers.append("powerpoint.source_slide_count_mismatch")
    if not reopened_slide_count_matches_input:
        blockers.append("powerpoint.reopened_slide_count_mismatch")
    if not copy_slide_count_matches_input:
        blockers.append("powerpoint.copy_slide_count_mismatch")
    if not pdf_page_count_matches_input:
        blockers.append("powerpoint.pdf_page_count_mismatch")
    return {
        "capability": "available",
        "status": "passed" if not blockers else "failed",
        "inputDigest": input_evidence["digest"],
        "inputPackageDigest": input_evidence["packageDigest"],
        "inputSlideCount": expected_slide_count,
        "copyDigest": copy["digest"] if copy else None,
        "copyPackageDigest": copy["packageDigest"] if copy else None,
        "slideCount": copy["slideCount"] if copy else None,
        "sourceSlideCount": source_slide_count,
        "reopenedSlideCount": reopened_slide_count,
        "sourceSlideCountMatchesInput": source_slide_count_matches_input,
        "reopenedSlideCountMatchesInput": reopened_slide_count_matches_input,
        "copySlideCountMatchesInput": copy_slide_count_matches_input,
        "renderDigest": pdf["digest"] if pdf else None,
        "renderPageCount": pdf["pageCount"] if pdf else None,
        "renderPageCountMatchesInput": pdf_page_count_matches_input,
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
    product_package_digest: str | None = None,
    product_slide_count: int | None = None,
    product_slide_image_digests: Sequence[str] | None = None,
) -> dict[str, Any]:
    ooxml = inspect_fidelity_pptx(
        input_path,
        expected_slide_image_digests=product_slide_image_digests,
    )
    artifact_digest_matches = bool(product_artifact_digest) and product_artifact_digest == ooxml["digest"]
    package_digest_matches = (
        product_package_digest is None
        or product_package_digest == ooxml["packageDigest"]
    )
    slide_count_matches = (
        product_slide_count is None or product_slide_count == ooxml["slideCount"]
    )
    raster_evidence = ooxml["rasterEvidence"]
    slide_image_digests_complete = (
        product_slide_image_digests is not None
        and len(product_slide_image_digests) == ooxml["slideCount"]
        and raster_evidence["slidesWithExpectedDigestMatch"] == ooxml["slideCount"]
    )
    product_binding_blockers: list[str] = []
    if not product_operation_id:
        product_binding_blockers.append("product_path_operation_id_missing")
    if not product_artifact_digest:
        product_binding_blockers.append("product_path_artifact_digest_missing")
    elif not artifact_digest_matches:
        product_binding_blockers.append("product_path_artifact_digest_mismatch")
    if not package_digest_matches:
        product_binding_blockers.append("product_path_package_digest_mismatch")
    if not slide_count_matches:
        product_binding_blockers.append("product_path_slide_count_mismatch")
    if product_slide_image_digests is None:
        product_binding_blockers.append("product_path_slide_image_digests_missing")
    elif not slide_image_digests_complete:
        product_binding_blockers.append("product_path_slide_image_digests_mismatch")
    product_binding_complete = (
        bool(product_operation_id)
        and artifact_digest_matches
        and package_digest_matches
        and slide_count_matches
        and slide_image_digests_complete
    )
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
        {"capability": "not_run", "status": "not_verified", "blockers": [office_not_run_blocker, *product_binding_blockers]}
        if office_not_run_blocker
        else None
    )
    libreoffice = libreoffice_readback(input_path, work_dir) if office_not_run is None else office_not_run
    powerpoint = powerpoint_readback(input_path, work_dir) if office_not_run is None else office_not_run
    blockers = list(ooxml["blockers"])
    for office in (libreoffice, powerpoint):
        blockers.extend(office.get("blockers", ()))
    blockers.extend(product_binding_blockers)
    if not product_binding_complete:
        blockers.append("product_path_artifact_binding_missing")
    office_statuses = {libreoffice["status"], powerpoint["status"]}
    office_readback_status = (
        "passed"
        if product_binding_complete and office_statuses == {"passed"}
        else "failed"
        if "failed" in office_statuses
        else "not_verified"
    )
    receipt: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "validatedAt": datetime.now(UTC).isoformat(),
        "input": {"path": str(input_path.resolve()), **ooxml},
        "fidelity": {
            "mode": "raster_fidelity",
            "visualParity": "not_verified",
            "nativeEditability": "not_assessed",
            "note": "Structural/readback evidence retains raster Fidelity; it does not prove visual parity or Hybrid/native editability.",
        },
        "productBinding": {
            "operationId": product_operation_id,
            "artifactDigest": product_artifact_digest,
            "observedArtifactDigest": ooxml["digest"],
            "artifactDigestStatus": "matched" if artifact_digest_matches else "not_verified",
            "packageDigest": product_package_digest,
            "observedPackageDigest": ooxml["packageDigest"],
            "packageDigestStatus": (
                "not_provided" if product_package_digest is None
                else "matched" if package_digest_matches else "mismatched"
            ),
            "slideCount": product_slide_count,
            "observedSlideCount": ooxml["slideCount"],
            "slideCountStatus": (
                "not_provided" if product_slide_count is None
                else "matched" if slide_count_matches else "mismatched"
            ),
            "slideImageDigests": list(product_slide_image_digests or ()),
            "observedSlideImageDigests": [
                slide["observedImageDigest"]
                for slide in raster_evidence["slides"]
            ],
            "slideImageDigestStatus": (
                "matched" if slide_image_digests_complete
                else "not_provided" if product_slide_image_digests is None
                else "mismatched"
            ),
            "status": "bound" if product_binding_complete else "unverified",
        },
        "libreOffice": libreoffice,
        "powerPoint": powerpoint,
        "officeReadbackStatus": office_readback_status,
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
    parser.add_argument("--product-package-digest", help="optional expected canonical package digest from publication evidence")
    parser.add_argument("--product-slide-count", type=int, help="optional expected published PPTX slide count")
    parser.add_argument(
        "--product-slide-image-digest",
        action="append",
        dest="product_slide_image_digests",
        help="expected sha256 digest for one full-slide raster, repeated in slide order",
    )
    args = parser.parse_args(argv)
    if not args.pptx.is_file() or args.pptx.suffix.lower() != ".pptx":
        parser.error("pptx must be an existing .pptx file")
    if args.product_slide_count is not None and args.product_slide_count <= 0:
        parser.error("product-slide-count must be positive")
    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="public-fidelity-readback-"))
    work_dir.mkdir(parents=True, exist_ok=True)
    receipt = build_receipt(
        args.pptx,
        work_dir=work_dir,
        product_operation_id=args.product_operation_id,
        product_artifact_digest=args.product_artifact_digest,
        product_package_digest=args.product_package_digest,
        product_slide_count=args.product_slide_count,
        product_slide_image_digests=args.product_slide_image_digests,
    )
    append_receipt(args.receipt, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    office_evidence_complete = receipt["officeReadbackStatus"] == "passed"
    product_binding_complete = receipt["productBinding"]["status"] == "bound"
    return 0 if office_evidence_complete and product_binding_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
