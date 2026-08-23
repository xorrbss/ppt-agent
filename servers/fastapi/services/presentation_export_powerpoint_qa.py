"""Trusted local artifact and PowerPoint Desktop QA for Hybrid export attempts."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
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


def _rgb_hex(value: object) -> str:
    return f"#{int(value) & 0xFFFFFF:06X}"


def _font_snapshot(text_range) -> dict[str, Any]:
    font = text_range.Font
    return {
        "latinTypeface": str(font.Name or ""),
        "eastAsianTypeface": str(font.NameFarEast or ""),
        "sizePt": round(float(font.Size), 3),
        "bold": bool(font.Bold),
        "fill": _rgb_hex(font.Fill.ForeColor.RGB),
    }


def _table_snapshot(shape) -> dict[str, Any]:
    table = shape.Table
    rows = int(table.Rows.Count)
    columns = int(table.Columns.Count)
    cells: list[list[dict[str, Any]]] = []
    for row_index in range(1, rows + 1):
        row: list[dict[str, Any]] = []
        for column_index in range(1, columns + 1):
            cell_shape = table.Cell(row_index, column_index).Shape
            text_range = cell_shape.TextFrame2.TextRange
            row.append(
                {
                    "text": str(text_range.Text or "").replace("\r\n", "\n").replace("\r", "\n"),
                    "font": _font_snapshot(text_range),
                    "fill": _rgb_hex(cell_shape.Fill.ForeColor.RGB),
                }
            )
        cells.append(row)
    return {"rows": rows, "columns": columns, "cells": cells}


def _variant_values(value: object) -> list[Any]:
    values = value if isinstance(value, (tuple, list)) else (value,)
    return [
        round(float(item), 6) if isinstance(item, float) else item
        for item in values
    ]


def _chart_snapshot(shape) -> dict[str, Any]:
    chart = shape.Chart
    series_collection = chart.SeriesCollection()
    series: list[dict[str, Any]] = []
    for index in range(1, int(series_collection.Count) + 1):
        item = series_collection.Item(index)
        series.append(
            {
                "name": str(item.Name or ""),
                "categories": _variant_values(item.XValues),
                "values": _variant_values(item.Values),
            }
        )
    axes: dict[str, Any] = {}
    for axis_name, axis_type in (("category", 1), ("value", 2)):
        try:
            axis = chart.Axes(axis_type)
            axes[axis_name] = {
                "present": True,
                "hasTitle": bool(axis.HasTitle),
                "title": str(axis.AxisTitle.Text or "") if bool(axis.HasTitle) else "",
                "tickLabelTypeface": str(axis.TickLabels.Font.Name or ""),
                "tickLabelSizePt": round(float(axis.TickLabels.Font.Size), 3),
            }
        except Exception:
            axes[axis_name] = {
                "present": False,
                "hasTitle": False,
                "title": "",
                "tickLabelTypeface": "",
                "tickLabelSizePt": None,
            }
    has_legend = bool(chart.HasLegend)
    legend = {"present": has_legend, "position": None, "typeface": "", "sizePt": None}
    if has_legend:
        legend_range = chart.Legend.Format.TextFrame2.TextRange
        legend["position"] = int(chart.Legend.Position)
        legend["typeface"] = str(legend_range.Font.Name or "")
        legend["sizePt"] = round(float(legend_range.Font.Size), 3)
    has_title = bool(chart.HasTitle)
    title = {"present": has_title, "text": "", "typeface": "", "sizePt": None}
    if has_title:
        title_range = chart.ChartTitle.Format.TextFrame2.TextRange
        title["text"] = str(chart.ChartTitle.Text or "")
        title["typeface"] = str(title_range.Font.Name or "")
        title["sizePt"] = round(float(title_range.Font.Size), 3)
    return {
        "chartType": int(chart.ChartType),
        "title": title,
        "series": series,
        "axes": axes,
        "legend": legend,
    }


def _chart_readback(snapshot: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    prefix = "Presenton hybrid chart "
    for slide_number, records in enumerate(snapshot, start=1):
        for record in records:
            name = record.get("name")
            chart = record.get("chart")
            if not isinstance(name, str) or not name.startswith(prefix) or not isinstance(chart, dict):
                continue
            element_id = name.removeprefix(prefix).strip()
            if element_id:
                result.append({"slideNumber": slide_number, "elementId": element_id, "chart": chart})
    return result


def _crop_snapshot(shape) -> dict[str, float] | None:
    if int(shape.Type) not in {11, 13}:
        return None
    picture = shape.PictureFormat
    return {
        "leftPt": round(float(picture.CropLeft), 3),
        "topPt": round(float(picture.CropTop), 3),
        "rightPt": round(float(picture.CropRight), 3),
        "bottomPt": round(float(picture.CropBottom), 3),
    }


def _powerpoint_deck_snapshot(deck) -> tuple[list[list[dict[str, Any]]], int]:
    slides: list[list[dict[str, Any]]] = []
    failures = 0
    for slide_index in range(1, int(deck.Slides.Count) + 1):
        records: list[dict[str, Any]] = []
        shapes = deck.Slides.Item(slide_index).Shapes
        for shape_index in range(1, int(shapes.Count) + 1):
            try:
                shape = shapes.Item(shape_index)
                has_text = bool(shape.HasTextFrame) and bool(shape.TextFrame2.HasText)
                text = latin_face = east_asian_face = ""
                if has_text:
                    text_range = shape.TextFrame2.TextRange
                    text = str(text_range.Text or "").replace("\r\n", "\n").replace("\r", "\n")
                    try:
                        latin_face = str(text_range.Font.Name or "")
                    except Exception:
                        latin_face = ""
                    try:
                        east_asian_face = str(text_range.Font.NameFarEast or "")
                    except Exception:
                        east_asian_face = ""
                has_table = bool(shape.HasTable)
                has_chart = bool(shape.HasChart)
                records.append(
                    {
                        "shapeIndex": shape_index,
                        "name": str(shape.Name or ""),
                        "type": int(shape.Type),
                        "left": round(float(shape.Left), 3),
                        "top": round(float(shape.Top), 3),
                        "width": round(float(shape.Width), 3),
                        "height": round(float(shape.Height), 3),
                        "hasText": has_text,
                        "hasTable": has_table,
                        "hasChart": has_chart,
                        "text": text,
                        "lineBreakCount": text.count("\n"),
                        "latinTypeface": latin_face,
                        "eastAsianTypeface": east_asian_face,
                        "table": _table_snapshot(shape) if has_table else None,
                        "chart": _chart_snapshot(shape) if has_chart else None,
                        "crop": _crop_snapshot(shape),
                    }
                )
            except Exception:
                failures += 1
        slides.append(records)
    return slides, failures


def _contains_presenton_qid(name: str, qid: str) -> bool:
    marker = f" {qid}"
    return name.startswith("Presenton hybrid ") and (
        name.endswith(marker)
        or name.endswith(f"{marker}-container")
        or f"{marker} " in name
    )


def _native_qid_ghosting(snapshot: list[list[dict[str, Any]]], qids: frozenset[str]) -> int:
    names = [str(shape.get("name") or "") for slide in snapshot for shape in slide]
    return sum(
        max(
            0,
            sum(
                name.startswith("Presenton hybrid ")
                and (name.endswith(f" {qid}") or name.endswith(f" {qid}-container"))
                for name in names
            )
            - 1,
        )
        for qid in qids
    )


def _backplate_collisions(snapshot: list[list[dict[str, Any]]]) -> int:
    return sum(
        max(
            0,
            sum(str(shape.get("name") or "") == "Presenton hybrid backplate" for shape in slide) - 1,
        )
        for slide in snapshot
    )


def _start_powerpoint_dialog_watch(main_hwnd: int):
    """Observe, but never dismiss, visible PowerPoint dialog windows."""

    import win32gui  # type: ignore[import-not-found]
    import win32process  # type: ignore[import-not-found]

    _, process_id = win32process.GetWindowThreadProcessId(main_hwnd)
    stop = threading.Event()
    observed: set[tuple[int, str]] = set()

    def scan() -> None:
        while not stop.wait(0.05):
            def visit(hwnd: int, _context: object) -> bool:
                try:
                    _, candidate_pid = win32process.GetWindowThreadProcessId(hwnd)
                    if (
                        candidate_pid == process_id
                        and hwnd != main_hwnd
                        and win32gui.IsWindowVisible(hwnd)
                        and win32gui.GetClassName(hwnd) == "#32770"
                    ):
                        observed.add((hwnd, str(win32gui.GetWindowText(hwnd) or "")))
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(visit, None)

    thread = threading.Thread(
        target=scan, name="presenton-powerpoint-dialog-watch", daemon=True
    )
    thread.start()
    return stop, thread, observed


def _powerpoint_main_hwnd(application, window_api=None) -> int:
    """Resolve the exact DispatchEx window without trusting pywin32's HWND shim."""

    if window_api is None:
        import win32gui as window_api  # type: ignore[import-not-found]

    token = f"Presenton QA {uuid.uuid4().hex}"
    application.Caption = token
    matches: list[int] = []

    def visit(hwnd: int, _context: object) -> bool:
        try:
            if (
                window_api.GetClassName(hwnd) == "PPTFrameClass"
                and window_api.GetWindowText(hwnd) == token
            ):
                matches.append(hwnd)
        except Exception:
            pass
        return True

    deadline = time.monotonic() + 2.0
    while not matches and time.monotonic() < deadline:
        window_api.EnumWindows(visit, None)
        if not matches:
            time.sleep(0.05)
    if len(matches) != 1:
        raise ValueError("powerpoint_main_hwnd_unavailable")
    return matches[0]


def _snapshot_comparison(
    first: list[list[dict[str, Any]]], reopened: list[list[dict[str, Any]]]
) -> dict[str, int | bool]:
    shape_count_changes = geometry_gt_5pt = geometry_gt_10pt = 0
    line_break_changes = typeface_changes = text_changes = 0
    object_identity_changes = table_content_changes = chart_content_changes = 0
    crop_changes = 0
    if len(first) != len(reopened):
        shape_count_changes += abs(len(first) - len(reopened))
    for first_slide, reopened_slide in zip(first, reopened, strict=False):
        if len(first_slide) != len(reopened_slide):
            shape_count_changes += abs(len(first_slide) - len(reopened_slide))
        for first_shape, reopened_shape in zip(
            first_slide, reopened_slide, strict=False
        ):
            if any(
                first_shape[key] != reopened_shape[key]
                for key in ("name", "type", "hasText", "hasTable", "hasChart")
            ):
                object_identity_changes += 1
            drift = max(
                abs(float(first_shape[key]) - float(reopened_shape[key]))
                for key in ("left", "top", "width", "height")
            )
            if drift > 5:
                geometry_gt_5pt += 1
            if drift > 10:
                geometry_gt_10pt += 1
            if first_shape["text"] != reopened_shape["text"]:
                text_changes += 1
            if first_shape["lineBreakCount"] != reopened_shape["lineBreakCount"]:
                line_break_changes += 1
            if (
                first_shape["latinTypeface"] != reopened_shape["latinTypeface"]
                or first_shape["eastAsianTypeface"]
                != reopened_shape["eastAsianTypeface"]
            ):
                typeface_changes += 1
            if first_shape.get("table") != reopened_shape.get("table"):
                table_content_changes += 1
            if first_shape.get("chart") != reopened_shape.get("chart"):
                chart_content_changes += 1
            if first_shape.get("crop") != reopened_shape.get("crop"):
                crop_changes += 1
    return {
        "shape_count_changes": shape_count_changes,
        "object_identity_changes": object_identity_changes,
        "geometry_changes_gt_5pt": geometry_gt_5pt,
        "geometry_changes_gt_10pt": geometry_gt_10pt,
        "text_changes": text_changes,
        "line_break_changes": line_break_changes,
        "typeface_changes": typeface_changes,
        "table_content_changes": table_content_changes,
        "chart_content_changes": chart_content_changes,
        "crop_changes": crop_changes,
        "readback_match": not any(
            (
                shape_count_changes,
                object_identity_changes,
                geometry_gt_5pt,
                geometry_gt_10pt,
                text_changes,
                line_break_changes,
                typeface_changes,
                table_content_changes,
                chart_content_changes,
                crop_changes,
            )
        ),
    }


def _snapshot_sha256(value: list[list[dict[str, Any]]]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _run_powerpoint_com_qa(
    path: Path,
    expected_slides: int,
    evidence_directory: Path | None = None,
    expected_native_qids: frozenset[str] = frozenset(),
    interactive_observation: bool = False,
) -> dict[str, Any]:
    import pythoncom  # type: ignore[import-not-found]
    import win32com.client  # type: ignore[import-not-found]

    pythoncom.CoInitialize()
    application = first = reopened = None
    dialog_watch = None
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        application.DisplayAlerts = 1
        if interactive_observation:
            try:
                application.Visible = True
                dialog_watch = _start_powerpoint_dialog_watch(
                    _powerpoint_main_hwnd(application)
                )
            except Exception:
                interactive_observation = False
        if evidence_directory is None:
            evidence_context = tempfile.TemporaryDirectory(
                prefix="presenton-powerpoint-qa-"
            )
        else:
            if evidence_directory.exists():
                raise PresentationExportContractError(
                    "presentation_export_powerpoint_evidence_exists"
                )
            evidence_directory.mkdir(parents=True)
            evidence_context = nullcontext(str(evidence_directory))
        with evidence_context as temp:
            temp_root = Path(temp)
            first_render = temp_root / "first-render"
            reopen_render = temp_root / "reopen-render"
            copy_path = temp_root / "reopened.pptx"
            first_render.mkdir()
            reopen_render.mkdir()
            first = application.Presentations.Open(
                str(path), True, False, bool(interactive_observation)
            )
            slide_count = int(first.Slides.Count)
            shape_counts = [
                int(first.Slides.Item(i).Shapes.Count)
                for i in range(1, slide_count + 1)
            ]
            first_snapshot, first_snapshot_failures = _powerpoint_deck_snapshot(first)
            first.Export(str(first_render), "PNG", 1280, 720)
            first.SaveCopyAs(str(copy_path), 24)
            first.Close()
            first = None
            reopened = application.Presentations.Open(
                str(copy_path), True, False, bool(interactive_observation)
            )
            reopened_count = int(reopened.Slides.Count)
            reopened_shapes = [
                int(reopened.Slides.Item(i).Shapes.Count)
                for i in range(1, reopened_count + 1)
            ]
            reopened_snapshot, reopened_snapshot_failures = _powerpoint_deck_snapshot(
                reopened
            )
            typography_gt_5pt = typography_gt_10pt = inspection_failures = 0
            native_text = native_image = native_shape = native_table = native_chart = 0
            native_image_crop = 0
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
                            crop = _crop_snapshot(shape)
                            if crop is not None and any(abs(value) > 0.01 for value in crop.values()):
                                native_image_crop += 1
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
            first_pngs = sorted(first_render.glob("*.PNG"))
            pngs = sorted(reopen_render.glob("*.PNG"))
            first_png_hashes = [_sha256_file(png) for png in first_pngs]
            reopen_png_hashes = [_sha256_file(png) for png in pngs]
            comparison = _snapshot_comparison(first_snapshot, reopened_snapshot)
            inspection_failures += first_snapshot_failures + reopened_snapshot_failures
            observed_shape_names = {
                str(shape.get("name") or "")
                for slide in reopened_snapshot
                for shape in slide
            }
            missing_qids = sorted(
                qid
                for qid in expected_native_qids
                if not any(
                    _contains_presenton_qid(name, qid)
                    for name in observed_shape_names
                )
            )
            semantic_payload_binding = (
                "passed"
                if expected_native_qids and not missing_qids
                else "not-verified"
            )
            native_qid_ghosting = _native_qid_ghosting(
                reopened_snapshot, expected_native_qids
            )
            backplate_collisions = _backplate_collisions(reopened_snapshot)
            if dialog_watch is not None:
                # Allow delayed Office notifications to become visible before
                # the observation window is sealed.
                time.sleep(0.15)
                dialog_watch[0].set()
                dialog_watch[1].join(timeout=1)
            repair_dialogs = len(dialog_watch[2]) if dialog_watch is not None else None
            repair_observation = (
                "interactive-observation"
                if dialog_watch is not None
                else "not-observable-via-noninteractive-com"
            )
            if evidence_directory is not None:
                (temp_root / "first-open-readback.json").write_text(
                    json.dumps(first_snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (temp_root / "reopen-readback.json").write_text(
                    json.dumps(reopened_snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            passed = not (
                slide_count != expected_slides
                or reopened_count != expected_slides
                or shape_counts != reopened_shapes
                or len(first_pngs) != expected_slides
                or len(pngs) != expected_slides
                or first_png_hashes != reopen_png_hashes
                or typography_gt_5pt > 0
                or typography_gt_10pt > 0
                or inspection_failures > 0
                or comparison["readback_match"] is not True
                or bool(missing_qids)
                or (dialog_watch is not None and repair_dialogs != 0)
            )
            report = {
                "status": "passed" if passed else "failed",
                "platform": "windows",
                "supported": True,
                "automation": "PowerPoint.COM",
                "reopened": True,
                "rendered": True,
                "readback": True,
                "verification_scope": "powerpoint-first-open-save-reopen-stability",
                "semantic_payload_binding": semantic_payload_binding,
                "repair_dialogs": repair_dialogs,
                "repair_observation": repair_observation,
                "rendered_png_sha256": reopen_png_hashes,
                "first_open_rendered_png_sha256": first_png_hashes,
                "reopen_rendered_png_sha256": reopen_png_hashes,
                "render_hashes_match": first_png_hashes == reopen_png_hashes,
                "first_open_readback_sha256": _snapshot_sha256(first_snapshot),
                "reopen_readback_sha256": _snapshot_sha256(reopened_snapshot),
                **(
                    {
                        "first_open_readback_artifact": "first-open-readback.json",
                        "reopen_readback_artifact": "reopen-readback.json",
                    }
                    if evidence_directory is not None
                    else {}
                ),
                **comparison,
                "powerpoint_version": str(application.Version),
                "slide_count": reopened_count,
                "typography_issues_gt_5pt": typography_gt_5pt,
                "typography_issues_gt_10pt": typography_gt_10pt,
                "native_text": native_text,
                "native_image": native_image,
                "native_shape": native_shape,
                "native_table": native_table,
                "native_chart": native_chart,
                "native_image_crop": native_image_crop,
                "native_qid_missing": len(missing_qids),
                "native_qid_ghosting": native_qid_ghosting,
                "backplate_collisions": backplate_collisions,
                "first_open_chart_readback": _chart_readback(first_snapshot),
                "reopen_chart_readback": _chart_readback(reopened_snapshot),
            }
            if evidence_directory is not None:
                (temp_root / "powerpoint-qa.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            return report
    finally:
        if dialog_watch is not None:
            dialog_watch[0].set()
            dialog_watch[1].join(timeout=1)
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


async def trusted_powerpoint_qa(
    path: Path,
    expected_slides: int,
    *,
    evidence_directory: Path | None = None,
    expected_native_qids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "preflight", "platform": "unsupported"}
    if not _powerpoint_executable_available():
        return {"status": "not-verified", "platform": "windows"}
    try:
        return await asyncio.to_thread(
            _run_powerpoint_com_qa,
            path,
            expected_slides,
            evidence_directory,
            expected_native_qids,
            os.getenv(
                "AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION", "false"
            ).lower()
            == "true",
        )
    except Exception:
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
