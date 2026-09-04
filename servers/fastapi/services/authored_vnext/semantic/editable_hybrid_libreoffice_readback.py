"""Fail-closed LibreOffice same-file readback for an Editable Hybrid RC.

This intentionally has no Fidelity fallback and never uses PowerPoint.  It
opens a byte-exact staged copy of one already exported RC PPTX with an
invocation-owned LibreOffice profile, exports only a PDF readback, and leaves
the source PPTX untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping
import xml.etree.ElementTree as ET
import zipfile


SCHEMA_VERSION = "authored-editable-hybrid-libreoffice-readback/v1"
SEAL_SCHEMA_VERSION = "authored-editable-hybrid-libreoffice-readback-seal/v1"
_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
_QID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_SLIDE = re.compile(r"ppt/slides/slide([1-9][0-9]*)\.xml")


class HybridReadbackError(ValueError):
    """A machine-readable contract or document-invalid refusal."""


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise HybridReadbackError(f"document_invalid:{field}")
    return value


def validate_rc_seal(value: Mapping[str, Any], artifact_path: Path) -> dict[str, Any]:
    """Validate the Hybrid-only RC binding before any host application starts."""

    if not isinstance(value, Mapping) or value.get("schemaVersion") != SEAL_SCHEMA_VERSION:
        raise HybridReadbackError("document_invalid:seal_schema")
    rc = value.get("rc")
    artifact = value.get("artifact")
    hybrid = value.get("hybrid")
    if not isinstance(rc, Mapping) or not isinstance(artifact, Mapping) or not isinstance(hybrid, Mapping):
        raise HybridReadbackError("document_invalid:seal_shape")
    required_rc = ("id", "sealedAt", "sourceDigest", "profileDigest", "policyDigest", "workerDigest", "providerDigest", "fontDigest", "exporterDigest")
    if set(rc) != set(required_rc) or not isinstance(rc["id"], str) or not rc["id"]:
        raise HybridReadbackError("document_invalid:rc_binding")
    try:
        datetime.fromisoformat(str(rc["sealedAt"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise HybridReadbackError("document_invalid:rc_sealed_at") from error
    for key in required_rc[2:]:
        _require_digest(rc[key], f"rc_{key}")
    if set(artifact) != {"sha256", "slideCount"} or not isinstance(artifact["slideCount"], int) or artifact["slideCount"] < 1:
        raise HybridReadbackError("document_invalid:artifact_binding")
    actual_digest = sha256_file(artifact_path)
    if _require_digest(artifact["sha256"], "artifact_digest") != actual_digest:
        raise HybridReadbackError("document_invalid:artifact_digest_mismatch")
    required_hybrid = {"profile", "capabilityId", "nativeQids", "rasterIslandQids", "readingOrderQids", "fallback", "fonts", "repair"}
    if set(hybrid) != required_hybrid or hybrid["profile"] != "editable_hybrid" or hybrid["capabilityId"] != "editable.hybrid.libreoffice-readback.v1":
        raise HybridReadbackError("document_invalid:hybrid_profile_or_capability")
    native = hybrid["nativeQids"]
    raster = hybrid["rasterIslandQids"]
    reading = hybrid["readingOrderQids"]
    if not all(isinstance(items, list) for items in (native, raster, reading)) or not native:
        raise HybridReadbackError("document_invalid:hybrid_qid_lists")
    expected = [*native, *raster]
    if (any(not isinstance(qid, str) or _QID.fullmatch(qid) is None for qid in expected + reading)
            or len(expected) != len(set(expected)) or len(reading) != len(set(reading))
            or set(reading) != set(expected)):
        raise HybridReadbackError("document_invalid:hybrid_qid_coverage")
    fallback, fonts, repair = hybrid["fallback"], hybrid["fonts"], hybrid["repair"]
    if (not isinstance(fallback, Mapping) or not isinstance(fonts, Mapping) or not isinstance(repair, Mapping)
            or set(fallback) != {"policyDigest", "status"} or set(fonts) != {"fontDigest", "status"}
            or set(repair) != {"policyDigest", "status"} or fallback.get("status") != "required"
            or fonts.get("status") != "verified" or repair.get("status") != "clean"
            or fallback.get("policyDigest") != rc["policyDigest"] or repair.get("policyDigest") != rc["policyDigest"]
            or fonts.get("fontDigest") != rc["fontDigest"]):
        raise HybridReadbackError("document_invalid:hybrid_policy_font_repair_binding")
    return {
        "rcId": rc["id"],
        "sameRcDigest": canonical_digest({key: rc[key] for key in required_rc}),
        "sourceDigest": rc["sourceDigest"], "profileDigest": rc["profileDigest"], "policyDigest": rc["policyDigest"],
        "workerDigest": rc["workerDigest"], "providerDigest": rc["providerDigest"], "fontDigest": rc["fontDigest"], "exporterDigest": rc["exporterDigest"],
        "artifactDigest": actual_digest, "expectedSlideCount": artifact["slideCount"],
        "nativeQids": native, "rasterIslandQids": raster, "readingOrderQids": reading,
    }


def inspect_hybrid_pptx(path: Path, *, expected_qids: list[str] | None = None) -> dict[str, Any]:
    """Read OOXML in document order; QIDs must be direct shape attributes."""

    result: dict[str, Any] = {"digest": sha256_file(path), "slideCount": 0, "status": "failed", "blockers": [], "qids": []}
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"}
            if not required.issubset(names):
                result["blockers"].append("ooxml.required_parts_missing")
            slides = sorted((name for name in names if _SLIDE.fullmatch(name)), key=lambda item: int(_SLIDE.fullmatch(item).group(1)))
            result["slideCount"] = len(slides)
            if not slides:
                result["blockers"].append("ooxml.slide_parts_missing")
            expected = set(expected_qids or [])
            qids: list[str] = []
            for slide in slides:
                root = ET.fromstring(archive.read(slide))
                for element in root.iter():
                    for attribute in element.attrib.values():
                        if attribute in expected:
                            qids.append(attribute)
            result["qids"] = qids
            if expected_qids is not None:
                if qids != expected_qids:
                    result["blockers"].append("hybrid.qid_reading_order_or_coverage_mismatch")
                if len(qids) != len(set(qids)):
                    result["blockers"].append("hybrid.qid_duplicate")
    except (OSError, zipfile.BadZipFile, ValueError, ET.ParseError):
        result["blockers"].append("ooxml.document_invalid")
    result["status"] = "passed" if not result["blockers"] else "failed"
    return result


def _find_soffice() -> Path | None:
    candidates = [
        os.environ.get("SOFFICE_PATH"), shutil.which("soffice"), shutil.which("soffice.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "LibreOffice" / "program" / "soffice.exe"),
        str(Path(os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")) / "LibreOffice" / "program" / "soffice.exe"),
    ]
    return next((Path(item).resolve() for item in candidates if item and Path(item).is_file()), None)


def _binary_architecture(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ": return "non_pe"
            handle.seek(0x3C)
            handle.seek(int.from_bytes(handle.read(4), "little"))
            if handle.read(4) != b"PE\0\0": return "unknown"
            return {0x8664: "x64", 0x014C: "x86", 0xAA64: "arm64"}.get(int.from_bytes(handle.read(2), "little"), "unknown")
    except OSError:
        return "unknown"


def _font_inventory(executable: Path) -> dict[str, Any]:
    roots = [Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts", executable.parent.parent / "share" / "fonts"]
    entries: list[str] = []
    for root in roots:
        if root.is_dir():
            for font in sorted((item for item in root.rglob("*") if item.suffix.lower() in {".ttf", ".otf", ".ttc"}), key=lambda item: str(item).lower()):
                try: entries.append(f"{font.name}|{font.stat().st_size}|{font.stat().st_mtime_ns}")
                except OSError: continue
    return {"roots": [str(root) for root in roots if root.is_dir()], "fontFileCount": len(entries), "inventoryDigest": canonical_digest({"entries": entries})}


def runtime_inventory(executable: Path) -> dict[str, Any]:
    try:
        version = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HybridReadbackError("environment_unavailable:soffice_version_unavailable") from error
    reported_version = version.stdout.strip()
    # Current Windows builds can return 0 with an empty `--version` stream.
    # ProductVersion is the signed executable's local identity in that case;
    # do not incorrectly turn an installed runtime into an unavailable gate.
    if not reported_version and os.name == "nt" and shutil.which("powershell"):
        try:
            product_version = subprocess.run(
                ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "(Get-Item -LiteralPath $env:HYBRID_SOFFICE_PATH).VersionInfo.ProductVersion"],
                capture_output=True, text=True, timeout=20, check=False,
                env=os.environ | {"HYBRID_SOFFICE_PATH": str(executable)},
            )
            reported_version = product_version.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            reported_version = ""
    if version.returncode != 0 or not reported_version:
        raise HybridReadbackError("environment_unavailable:soffice_version_unavailable")
    return {"path": str(executable), "binaryDigest": sha256_file(executable), "version": reported_version, "architecture": _binary_architecture(executable), "pythonArchitecture": "x64" if sys.maxsize > 2**32 else "x86", "fonts": _font_inventory(executable)}


def _soffice_processes() -> list[int]:
    if os.name != "nt": return []
    command = "@(Get-Process soffice,soffice.bin -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }) | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=15, check=False)
        payload = json.loads(result.stdout or "[]")
        return sorted({int(item) for item in (payload if isinstance(payload, list) else [payload])})
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return []


def _pdf_page_count(path: Path) -> int | None:
    try: payload = path.read_bytes()
    except OSError: return None
    count = len(re.findall(rb"/Type\s*/Page\b", payload))
    return count or None


def _run_readback(input_path: Path, work_dir: Path, *, label: str, expected_slide_count: int, expected_qids: list[str] | None) -> dict[str, Any]:
    before = _soffice_processes()
    if before:
        return {"status": "failed", "classification": "invocation_failed", "blockers": ["libreoffice.preexisting_processes"], "processesBefore": before, "processesAfter": before}
    stage_dir, output_dir, profile = work_dir / label / "stage", work_dir / label / "output", work_dir / label / "profile"
    stage_dir.mkdir(parents=True); output_dir.mkdir(); profile.mkdir()
    staged = stage_dir / "opened-rc.pptx"
    shutil.copyfile(input_path, staged)
    source_before, staged_digest = sha256_file(input_path), sha256_file(staged)
    if source_before != staged_digest:
        return {"status": "failed", "classification": "document_invalid", "blockers": ["libreoffice.staged_bytes_not_exact"]}
    command = ["--headless", "--nologo", "--nodefault", "--nofirststartwizard", f"-env:UserInstallation={profile.resolve().as_uri()}", "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(output_dir), str(staged)]
    executable = _find_soffice()
    assert executable is not None
    try:
        process = subprocess.Popen([str(executable), *command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        if "process" in locals(): process.kill(); process.communicate()
        return {"status": "failed", "classification": "invocation_failed", "blockers": ["libreoffice.invocation_failed"], "errorType": type(error).__name__}
    output = output_dir / "opened-rc.pdf"
    after = _soffice_processes()
    blockers: list[str] = []
    if process.returncode != 0 or not output.is_file(): blockers.append("libreoffice.output_missing_or_failed")
    if set(after).difference(before): blockers.append("libreoffice.invocation_owned_process_survived")
    pdf_pages = _pdf_page_count(output) if output.is_file() else None
    if pdf_pages != expected_slide_count: blockers.append("libreoffice.output_slide_count_mismatch")
    source_after = sha256_file(input_path)
    if source_before != source_after: blockers.append("libreoffice.source_digest_changed")
    package = inspect_hybrid_pptx(staged, expected_qids=expected_qids)
    blockers.extend(package["blockers"])
    if re.search(r"\brepair\b", stdout + stderr, flags=re.I): blockers.append("libreoffice.repair_reported")
    return {"status": "passed" if not blockers else "failed", "classification": None if not blockers else ("document_invalid" if package["status"] != "passed" else "invocation_failed"), "sourceDigestBefore": source_before, "sourceDigestAfter": source_after, "sourceDigestUnchanged": source_before == source_after, "stagedDigest": staged_digest, "outputPdfDigest": sha256_file(output) if output.is_file() else None, "outputPdfPageCount": pdf_pages, "expectedSlideCount": expected_slide_count, "readback": package, "invocation": {"executable": str(executable), "arguments": command, "launcherProcessId": process.pid, "profile": str(profile.resolve()), "profileUri": profile.resolve().as_uri(), "processesBefore": before, "processesAfter": after, "stdoutDigest": "sha256:" + sha256(stdout.encode()).hexdigest(), "stderrDigest": "sha256:" + sha256(stderr.encode()).hexdigest()}, "blockers": blockers}


def run_editable_hybrid_readback(rc_pptx: Path, control_pptx: Path, seal: Mapping[str, Any], work_dir: Path) -> dict[str, Any]:
    """Run a control first, then a byte-exact Hybrid RC readback; never a Fidelity flow."""
    if work_dir.exists(): raise HybridReadbackError("invocation_failed:work_dir_must_be_fresh")
    work_dir.mkdir(parents=True)
    binding = validate_rc_seal(seal, rc_pptx)
    if sha256_file(control_pptx) == binding["artifactDigest"]:
        raise HybridReadbackError("document_invalid:known_good_control_must_be_distinct")
    control_package = inspect_hybrid_pptx(control_pptx)
    if control_package["status"] != "passed":
        raise HybridReadbackError("document_invalid:known_good_control_package")
    rc_package = inspect_hybrid_pptx(rc_pptx, expected_qids=binding["readingOrderQids"])
    if rc_package["status"] != "passed" or rc_package["slideCount"] != binding["expectedSlideCount"]:
        raise HybridReadbackError("document_invalid:rc_package_or_slide_count")
    executable = _find_soffice()
    if executable is None:
        return {"schemaVersion": SCHEMA_VERSION, "mode": "editable_hybrid", "capabilityId": "editable.hybrid.libreoffice-readback.v1", "status": "unavailable", "classification": "environment_unavailable", "sameRcBinding": binding, "blockers": ["libreoffice.environment_unavailable"]}
    try: inventory = runtime_inventory(executable)
    except HybridReadbackError as error:
        return {"schemaVersion": SCHEMA_VERSION, "mode": "editable_hybrid", "capabilityId": "editable.hybrid.libreoffice-readback.v1", "status": "unavailable", "classification": "environment_unavailable", "sameRcBinding": binding, "blockers": [str(error)]}
    control = _run_readback(control_pptx, work_dir, label="known-good-control", expected_slide_count=control_package["slideCount"], expected_qids=None)
    rc = _run_readback(rc_pptx, work_dir, label="exact-rc", expected_slide_count=binding["expectedSlideCount"], expected_qids=binding["readingOrderQids"])
    blockers = [*control["blockers"], *rc["blockers"]]
    return {"schemaVersion": SCHEMA_VERSION, "mode": "editable_hybrid", "capabilityId": "editable.hybrid.libreoffice-readback.v1", "status": "passed" if control["status"] == rc["status"] == "passed" else "failed", "classification": rc.get("classification") or control.get("classification"), "validatedAt": datetime.now(UTC).isoformat(), "runtime": inventory, "sameRcBinding": binding, "knownGoodControl": control, "exactRcReadback": rc, "blockers": blockers}
