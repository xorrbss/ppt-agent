from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Literal

import pypdfium2
from pptx import Presentation

from services.source_locked.contracts import AnalysisStage, PptxSourceManifestV1
from services.source_locked.digests import sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.pdf import analyze_pdf
from services.source_locked.pptx import analyze_pptx


DEFAULT_SOFFICE_PATH = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
_OLE_COMPOUND_FILE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_DEFAULT_TIMEOUT_SECONDS = 180
_MAX_CONVERSION_INPUT_BYTES = 250 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceLockedPptConversionResultV1:
    original_ppt_sha256: str
    converted_pptx_sha256: str
    converted_pptx_bytes: bytes
    converted_manifest: PptxSourceManifestV1
    source_page_count: int
    converted_page_count: int
    source_visual_page_digests: tuple[str, ...]
    converted_visual_page_digests: tuple[str, ...]
    missing_content_count: Literal[0]
    visual_readback_exact: Literal[True]
    isolated_libreoffice_profile: Literal[True]
    power_point_com_used: Literal[False] = False
    byte_exact_source_package: Literal[False] = False
    fallback_allowed: Literal[False] = False
    contract_version: Literal["source-locked-ppt-conversion-result/v1"] = (
        "source-locked-ppt-conversion-result/v1"
    )


def ppt_conversion_readback_projection(
    result: SourceLockedPptConversionResultV1,
) -> dict[str, object]:
    """Stable proof compared across isolated conversions; ZIP metadata is excluded."""

    converted_manifest = result.converted_manifest.model_dump(mode="json")
    converted_manifest.pop("package_sha256", None)
    return {
        "contract_version": "source-locked-ppt-conversion-readback/v1",
        "original_ppt_sha256": result.original_ppt_sha256,
        "converted_manifest": converted_manifest,
        "source_page_count": result.source_page_count,
        "converted_page_count": result.converted_page_count,
        "source_visual_page_digests": result.source_visual_page_digests,
        "converted_visual_page_digests": result.converted_visual_page_digests,
        "missing_content_count": result.missing_content_count,
        "visual_readback_exact": result.visual_readback_exact,
        "isolated_libreoffice_profile": result.isolated_libreoffice_profile,
        "power_point_com_used": result.power_point_com_used,
        "byte_exact_source_package": result.byte_exact_source_package,
        "fallback_allowed": result.fallback_allowed,
    }


def _conversion_required(message: str, *, cause: Exception | None = None) -> None:
    error = SourceLockedError(
        "SOURCE_CONVERSION_REQUIRED",
        AnalysisStage.SOURCE_ANALYSIS,
        message,
    )
    if cause is None:
        raise error
    raise error from cause


def _run_libreoffice_conversion(
    *,
    soffice_path: Path,
    input_path: Path,
    output_directory: Path,
    output_filter: str,
    profile_directory: Path,
    timeout_seconds: int,
) -> Path:
    profile_directory.mkdir(parents=True, exist_ok=False)
    output_directory.mkdir(parents=True, exist_ok=False)
    command = [
        str(soffice_path),
        "--headless",
        "--invisible",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--norestore",
        "--nolockcheck",
        f"-env:UserInstallation={profile_directory.resolve().as_uri()}",
        "--convert-to",
        output_filter,
        "--outdir",
        str(output_directory),
        str(input_path),
    ]
    environment = os.environ.copy()
    environment["SAL_DISABLE_OPENCL"] = "1"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            cwd=input_path.parent,
            env=environment,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _conversion_required("Legacy .ppt conversion could not be verified", cause=exc)
    if completed.returncode != 0:
        _conversion_required("Legacy .ppt conversion could not be verified")

    extension = output_filter.split(":", 1)[0]
    expected = output_directory / f"{input_path.stem}.{extension}"
    if not expected.is_file() or expected.stat().st_size <= 0:
        _conversion_required("Legacy .ppt conversion did not produce a complete output")
    return expected


def _rendered_pdf_page_digests(pdf_bytes: bytes, *, scale: int = 2) -> tuple[str, ...]:
    digests: list[str] = []
    try:
        document = pypdfium2.PdfDocument(pdf_bytes)
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().convert("RGBA")
                        width, height = image.size
                        payload = width.to_bytes(4, "big") + height.to_bytes(4, "big")
                        payload += image.tobytes()
                        digests.append(sha256_digest(payload))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            document.close()
    except Exception as exc:
        _conversion_required("Legacy .ppt visual readback could not be verified", cause=exc)
    return tuple(digests)


def convert_legacy_ppt_v1(
    source_bytes: bytes,
    *,
    soffice_path: Path = DEFAULT_SOFFICE_PATH,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> SourceLockedPptConversionResultV1:
    """Convert in isolation and return only after strict structural/visual readback."""

    if not source_bytes or len(source_bytes) > _MAX_CONVERSION_INPUT_BYTES:
        _conversion_required("Legacy .ppt conversion requires a bounded non-empty source")
    if not source_bytes.startswith(_OLE_COMPOUND_FILE_SIGNATURE):
        _conversion_required("The legacy .ppt source is not a valid compound document")
    executable = soffice_path.resolve()
    if not executable.is_file():
        _conversion_required("The reviewed LibreOffice conversion adapter is unavailable")
    if timeout_seconds <= 0:
        _conversion_required("Legacy .ppt conversion timeout must be positive")

    with tempfile.TemporaryDirectory(prefix="source-locked-ppt-") as temporary:
        root = Path(temporary).resolve()
        source_path = root / "source.ppt"
        source_path.write_bytes(source_bytes)
        converted_path = _run_libreoffice_conversion(
            soffice_path=executable,
            input_path=source_path,
            output_directory=root / "pptx-output",
            output_filter="pptx:Impress MS PowerPoint 2007 XML",
            profile_directory=root / "profile-pptx",
            timeout_seconds=timeout_seconds,
        )
        source_pdf_path = _run_libreoffice_conversion(
            soffice_path=executable,
            input_path=source_path,
            output_directory=root / "source-pdf-output",
            output_filter="pdf:impress_pdf_Export",
            profile_directory=root / "profile-source-pdf",
            timeout_seconds=timeout_seconds,
        )

        converted_bytes = converted_path.read_bytes()
        converted_copy = root / "converted.pptx"
        converted_copy.write_bytes(converted_bytes)
        converted_pdf_path = _run_libreoffice_conversion(
            soffice_path=executable,
            input_path=converted_copy,
            output_directory=root / "converted-pdf-output",
            output_filter="pdf:impress_pdf_Export",
            profile_directory=root / "profile-converted-pdf",
            timeout_seconds=timeout_seconds,
        )
        source_pdf = source_pdf_path.read_bytes()
        converted_pdf = converted_pdf_path.read_bytes()

    try:
        converted_manifest = analyze_pptx(converted_bytes)
        python_pptx_readback = Presentation(BytesIO(converted_bytes))
        source_pdf_manifest = analyze_pdf(source_pdf)
        converted_pdf_manifest = analyze_pdf(converted_pdf)
    except SourceLockedError as exc:
        _conversion_required("The converted PPTX did not pass structural readback", cause=exc)
    except Exception as exc:
        _conversion_required("The converted PPTX could not be read back", cause=exc)

    source_visual_digests = _rendered_pdf_page_digests(source_pdf)
    converted_visual_digests = _rendered_pdf_page_digests(converted_pdf)
    page_count = source_pdf_manifest.page_count
    if (
        page_count != converted_manifest.slide_count
        or page_count != converted_manifest.page_count
        or page_count != converted_pdf_manifest.page_count
        or page_count != len(python_pptx_readback.slides)
        or page_count != len(source_visual_digests)
        or source_visual_digests != converted_visual_digests
        or converted_manifest.missing_content_count != 0
    ):
        _conversion_required(
            "Legacy .ppt conversion did not preserve exact page and visual readback invariants"
        )

    return SourceLockedPptConversionResultV1(
        original_ppt_sha256=sha256_digest(source_bytes),
        converted_pptx_sha256=sha256_digest(converted_bytes),
        converted_pptx_bytes=converted_bytes,
        converted_manifest=converted_manifest,
        source_page_count=page_count,
        converted_page_count=converted_manifest.page_count,
        source_visual_page_digests=source_visual_digests,
        converted_visual_page_digests=converted_visual_digests,
        missing_content_count=0,
        visual_readback_exact=True,
        isolated_libreoffice_profile=True,
    )
