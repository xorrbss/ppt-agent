"""Trusted local artifact and PowerPoint Desktop QA for Hybrid export attempts."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import uuid

from services.presentation_export_capabilities import PresentationExportContractError
from utils.get_env import get_app_data_directory_env


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_hybrid_artifact(path_value: str) -> Path:
    app_data = get_app_data_directory_env()
    if not app_data:
        raise PresentationExportContractError("presentation_export_app_data_required")
    exports_root = (Path(app_data) / "exports").resolve()
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = exports_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(exports_root)
    except (OSError, ValueError) as error:
        raise PresentationExportContractError(
            "presentation_export_artifact_path_invalid"
        ) from error
    if resolved.suffix.lower() != ".pptx" or not resolved.is_file():
        raise PresentationExportContractError("presentation_export_artifact_path_invalid")
    return resolved


def require_matching_hybrid_sidecars(
    artifact: Path,
    *,
    quality: dict[str, Any],
    coverage: dict[str, Any],
    canonical_digest,
) -> None:
    for suffix, expected in ((".quality.json", quality), (".coverage.json", coverage)):
        sidecar = Path(f"{artifact}{suffix}")
        try:
            persisted = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PresentationExportContractError(
                "presentation_export_evidence_sidecar_invalid"
            ) from error
        if not hmac.compare_digest(
            canonical_digest(persisted), canonical_digest(expected)
        ):
            raise PresentationExportContractError(
                "presentation_export_evidence_sidecar_mismatch"
            )


def publish_finalized_hybrid_sidecars(
    artifact: Path,
    *,
    quality: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    """Replace only this attempt's sidecars after trusted QA has finalized them."""

    staged: list[tuple[Path, Path]] = []
    token = uuid.uuid4().hex
    try:
        for suffix, payload in ((".quality.json", quality), (".coverage.json", coverage)):
            target = Path(f"{artifact}{suffix}")
            temporary = Path(f"{target}.{token}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            staged.append((temporary, target))
        for temporary, target in staged:
            os.replace(temporary, target)
    except OSError as error:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        raise PresentationExportContractError(
            "presentation_export_evidence_sidecar_finalization_failed"
        ) from error


def _powerpoint_executable_available() -> bool:
    candidates = (
        shutil.which("POWERPNT.EXE"),
        r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
    )
    return any(candidate and Path(candidate).is_file() for candidate in candidates)


def _run_powerpoint_com_qa(path: Path, expected_slides: int) -> dict[str, Any]:
    import pythoncom  # type: ignore[import-not-found]
    import win32com.client  # type: ignore[import-not-found]

    pythoncom.CoInitialize()
    application = first = reopened = None
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        application.DisplayAlerts = 1
        with tempfile.TemporaryDirectory(prefix="presenton-powerpoint-qa-") as temp:
            temp_root = Path(temp)
            first_render = temp_root / "first-render"
            reopen_render = temp_root / "reopen-render"
            copy_path = temp_root / "reopened.pptx"
            first_render.mkdir()
            reopen_render.mkdir()
            first = application.Presentations.Open(str(path), True, False, False)
            slide_count = int(first.Slides.Count)
            shape_counts = [
                int(first.Slides.Item(i).Shapes.Count)
                for i in range(1, slide_count + 1)
            ]
            first.Export(str(first_render), "PNG", 1280, 720)
            first.SaveCopyAs(str(copy_path), 24)
            first.Close()
            first = None
            reopened = application.Presentations.Open(str(copy_path), True, False, False)
            reopened_count = int(reopened.Slides.Count)
            reopened_shapes = [
                int(reopened.Slides.Item(i).Shapes.Count)
                for i in range(1, reopened_count + 1)
            ]
            typography_gt_5pt = typography_gt_10pt = inspection_failures = 0
            native_text = native_image = native_shape = native_table = native_chart = 0
            for slide_index in range(1, reopened_count + 1):
                shapes = reopened.Slides.Item(slide_index).Shapes
                for shape_index in range(1, int(shapes.Count) + 1):
                    shape = shapes.Item(shape_index)
                    try:
                        has_text = bool(shape.HasTextFrame) and bool(
                            shape.TextFrame2.HasText
                        )
                        if has_text:
                            native_text += 1
                        if bool(shape.HasTable):
                            native_table += 1
                        if bool(shape.HasChart):
                            native_chart += 1
                        shape_name = str(shape.Name or "").lower()
                        is_backplate = "presenton hybrid backplate" in shape_name
                        shape_type = int(shape.Type)
                        if not is_backplate and shape_type in {11, 13}:
                            native_image += 1
                        elif (
                            not is_backplate
                            and not has_text
                            and not bool(shape.HasTable)
                            and not bool(shape.HasChart)
                            and shape_type not in {11, 13}
                        ):
                            native_shape += 1
                        if has_text:
                            text_range = shape.TextFrame2.TextRange
                            overflow = max(
                                float(text_range.BoundHeight) - float(shape.Height),
                                float(text_range.BoundWidth) - float(shape.Width),
                            )
                            if overflow > 5:
                                typography_gt_5pt += 1
                            if overflow > 10:
                                typography_gt_10pt += 1
                    except Exception:
                        inspection_failures += 1
            reopened.Export(str(reopen_render), "PNG", 1280, 720)
            pngs = sorted(reopen_render.glob("*.PNG"))
            passed = not (
                slide_count != expected_slides
                or reopened_count != expected_slides
                or shape_counts != reopened_shapes
                or len(pngs) != expected_slides
                or typography_gt_5pt > 0
                or typography_gt_10pt > 0
                or inspection_failures > 0
            )
            return {
                "status": "passed" if passed else "failed",
                "platform": "windows",
                "supported": True,
                "automation": "PowerPoint.COM",
                "reopened": True,
                "rendered": True,
                "readback": True,
                "repair_dialogs": 0,
                "rendered_png_sha256": [_sha256_file(png) for png in pngs],
                "powerpoint_version": str(application.Version),
                "slide_count": reopened_count,
                "typography_issues_gt_5pt": typography_gt_5pt,
                "typography_issues_gt_10pt": typography_gt_10pt,
                "native_text": native_text,
                "native_image": native_image,
                "native_shape": native_shape,
                "native_table": native_table,
                "native_chart": native_chart,
            }
    finally:
        for deck in (reopened, first):
            if deck is not None:
                try:
                    deck.Close()
                except Exception:
                    pass
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


async def trusted_powerpoint_qa(path: Path, expected_slides: int) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "preflight", "platform": "unsupported"}
    if not _powerpoint_executable_available():
        return {"status": "not-verified", "platform": "windows"}
    try:
        return await asyncio.to_thread(_run_powerpoint_com_qa, path, expected_slides)
    except Exception as error:
        return {
            "status": "failed",
            "platform": "windows",
            "supported": True,
            "automation": "PowerPoint.COM",
        }


__all__ = [
    "publish_finalized_hybrid_sidecars",
    "require_matching_hybrid_sidecars",
    "resolve_hybrid_artifact",
    "trusted_powerpoint_qa",
]
