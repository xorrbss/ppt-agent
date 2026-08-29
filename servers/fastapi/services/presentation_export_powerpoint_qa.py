"""Trusted local artifact and PowerPoint Desktop QA for Hybrid export attempts."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
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
import xml.etree.ElementTree as ET
import zipfile

from services.presentation_export_capabilities import PresentationExportContractError
from services.presentation_export_evidence_sidecars import (
    publish_finalized_hybrid_sidecars,
    require_matching_hybrid_sidecars,
)
from utils.get_env import get_app_data_directory_env


_POWERPOINT_QA_OBSERVATION_STATES = (
    "first-open",
    "save-copy",
    "reopen",
    "readback",
    "observation-sealed",
)


@dataclass(slots=True)
class _PowerPointDialogWatch:
    stop: threading.Event
    thread: threading.Thread
    observed: set[tuple[int, str]]
    failures: list[str]


def _advance_powerpoint_qa_observation(
    states: list[str], state: str
) -> None:
    """Append one auditable state without allowing gaps or reordering."""

    next_index = len(states)
    if (
        next_index >= len(_POWERPOINT_QA_OBSERVATION_STATES)
        or _POWERPOINT_QA_OBSERVATION_STATES[next_index] != state
    ):
        raise PresentationExportContractError(
            "presentation_export_powerpoint_observation_state_invalid"
        )
    states.append(state)


def _powerpoint_release_gate_failures(
    *,
    release_required: bool,
    observation_states: list[str],
    dialog_watcher_status: str,
    dialog_watcher_armed_before_first_open: bool,
    repair_dialogs: int | None,
    readback_match: bool | None,
    semantic_payload_binding: str,
) -> list[str]:
    failures: list[str] = []
    if not release_required:
        return failures
    if (
        dialog_watcher_status in {"not-requested", "unavailable"}
        or not dialog_watcher_armed_before_first_open
    ):
        failures.append("watcher-unavailable")
    elif dialog_watcher_status == "interrupted":
        failures.append("watcher-interrupted")
    elif dialog_watcher_status == "stop-failed":
        failures.append("watcher-stop-failed")
    if repair_dialogs is None:
        failures.append("repair-observation-required")
    elif repair_dialogs != 0:
        failures.append("repair-dialog-observed")
    if readback_match is not True:
        failures.append("readback-mismatch")
    if semantic_payload_binding != "passed":
        failures.append("semantic-binding-mismatch")
    if tuple(observation_states) != _POWERPOINT_QA_OBSERVATION_STATES:
        failures.append("observation-state-incomplete")
    return list(dict.fromkeys(failures))


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


def _font_name(font: object, *, east_asian: bool = False) -> str:
    try:
        value = font.NameFarEast if east_asian else font.Name
        return str(value or "")
    except Exception:
        return ""


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
            has_axis_title = bool(axis.HasTitle)
            title_range = (
                axis.AxisTitle.Format.TextFrame2.TextRange
                if has_axis_title
                else None
            )
            axes[axis_name] = {
                "present": True,
                "hasTitle": has_axis_title,
                "title": str(axis.AxisTitle.Text or "") if has_axis_title else "",
                "titleEastAsianTypeface": (
                    _font_name(title_range.Font, east_asian=True)
                    if title_range is not None
                    else ""
                ),
                "tickLabelTypeface": _font_name(axis.TickLabels.Font),
                "tickLabelEastAsianTypeface": _font_name(
                    axis.TickLabels.Font, east_asian=True
                ),
                "tickLabelSizePt": round(float(axis.TickLabels.Font.Size), 3),
            }
        except Exception:
            axes[axis_name] = {
                "present": False,
                "hasTitle": False,
                "title": "",
                "titleEastAsianTypeface": "",
                "tickLabelTypeface": "",
                "tickLabelEastAsianTypeface": "",
                "tickLabelSizePt": None,
            }
    has_legend = bool(chart.HasLegend)
    legend = {
        "present": has_legend,
        "position": None,
        "typeface": "",
        "eastAsianTypeface": "",
        "sizePt": None,
    }
    if has_legend:
        legend_range = chart.Legend.Format.TextFrame2.TextRange
        legend["position"] = int(chart.Legend.Position)
        legend["typeface"] = _font_name(legend_range.Font)
        legend["eastAsianTypeface"] = _font_name(
            legend_range.Font, east_asian=True
        )
        legend["sizePt"] = round(float(legend_range.Font.Size), 3)
    has_title = bool(chart.HasTitle)
    title = {
        "present": has_title,
        "text": "",
        "typeface": "",
        "eastAsianTypeface": "",
        "sizePt": None,
    }
    if has_title:
        title_range = chart.ChartTitle.Format.TextFrame2.TextRange
        title["text"] = str(chart.ChartTitle.Text or "")
        title["typeface"] = _font_name(title_range.Font)
        title["eastAsianTypeface"] = _font_name(
            title_range.Font, east_asian=True
        )
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


def _start_powerpoint_dialog_watch(main_hwnd: int) -> _PowerPointDialogWatch:
    """Observe, but never dismiss, visible PowerPoint dialog windows."""

    import win32gui  # type: ignore[import-not-found]
    import win32process  # type: ignore[import-not-found]

    _, process_id = win32process.GetWindowThreadProcessId(main_hwnd)
    stop = threading.Event()
    ready = threading.Event()
    observed: set[tuple[int, str]] = set()
    failures: list[str] = []

    def scan() -> None:
        ready.set()
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

            try:
                win32gui.EnumWindows(visit, None)
            except Exception:
                failures.append("dialog-enumeration-failed")
                return

    thread = threading.Thread(
        target=scan, name="presenton-powerpoint-dialog-watch", daemon=True
    )
    thread.start()
    if not ready.wait(timeout=1) or not thread.is_alive():
        stop.set()
        thread.join(timeout=1)
        raise RuntimeError("powerpoint_dialog_watcher_unavailable")
    return _PowerPointDialogWatch(
        stop=stop,
        thread=thread,
        observed=observed,
        failures=failures,
    )


def _seal_powerpoint_dialog_watch(
    dialog_watch: _PowerPointDialogWatch | None,
    *,
    requested: bool,
) -> tuple[str, int | None]:
    if dialog_watch is None:
        return ("unavailable" if requested else "not-requested"), None
    interrupted = bool(dialog_watch.failures) or not dialog_watch.thread.is_alive()
    # Allow delayed Office notifications to become visible before sealing the
    # observation window. This is an observation only; dialogs are never dismissed.
    time.sleep(0.15)
    interrupted = (
        interrupted
        or bool(dialog_watch.failures)
        or not dialog_watch.thread.is_alive()
    )
    dialog_watch.stop.set()
    dialog_watch.thread.join(timeout=1)
    if dialog_watch.thread.is_alive():
        status = "stop-failed"
    elif interrupted:
        status = "interrupted"
    else:
        status = "sealed"
    return status, len(dialog_watch.observed)


def _powerpoint_main_hwnd(application, window_api=None) -> int:
    """Read and validate the exact HWND exposed by the new COM application."""

    if window_api is None:
        import win32gui as window_api  # type: ignore[import-not-found]

    try:
        hwnd = int(application.HWND)
    except Exception as error:
        raise ValueError("powerpoint_main_hwnd_unavailable") from error
    if hwnd <= 0 or window_api.GetClassName(hwnd) != "PPTFrameClass":
        raise ValueError("powerpoint_main_hwnd_unavailable")
    return hwnd


def _powerpoint_window_pids(window_api=None, process_api=None) -> frozenset[int]:
    if window_api is None:
        import win32gui as window_api  # type: ignore[import-not-found]
    if process_api is None:
        import win32process as process_api  # type: ignore[import-not-found]
    pids: set[int] = set()

    def visit(hwnd: int, _context: object) -> bool:
        try:
            if window_api.GetClassName(hwnd) == "PPTFrameClass":
                _, pid = process_api.GetWindowThreadProcessId(hwnd)
                if int(pid) > 0:
                    pids.add(int(pid))
        except Exception:
            pass
        return True

    window_api.EnumWindows(visit, None)
    return frozenset(pids)


def _qid_owner_census(
    snapshot: list[list[dict[str, Any]]],
    expected: frozenset[tuple[int, str, str]],
) -> dict[str, int]:
    missing = ghosting = mismatches = 0
    for expected_slide, qid, disposition in expected:
        matches: list[tuple[int, str]] = []
        raster_name = f"Presenton raster visual fragment {qid} semantic-owner"
        for slide_number, shapes in enumerate(snapshot, start=1):
            for shape in shapes:
                name = str(shape.get("name") or "")
                actual_disposition = (
                    "raster" if name == raster_name
                    else "native" if _contains_presenton_qid(name, qid)
                    else None
                )
                if actual_disposition is not None:
                    matches.append((slide_number, actual_disposition))
        correct = [item for item in matches if item == (expected_slide, disposition)]
        if not correct:
            missing += 1
        ghosting += max(0, len(matches) - 1)
        mismatches += sum(item != (expected_slide, disposition) for item in matches)
    return {
        "qid_owner_missing": missing,
        "qid_owner_ghosting": ghosting,
        "qid_owner_mismatches": mismatches,
    }


def _geometry_contract_mismatches(
    snapshot: list[list[dict[str, Any]]],
    expected: dict[tuple[int, str], tuple[float, float, float, float]],
    *,
    tolerance_px: float = 1.0,
) -> int:
    mismatches = 0
    for (slide_number, qid), geometry in expected.items():
        shapes = snapshot[slide_number - 1] if 0 < slide_number <= len(snapshot) else []
        matches = [shape for shape in shapes if _contains_presenton_qid(str(shape.get("name") or ""), qid)]
        if len(matches) != 1:
            mismatches += 1
            continue
        shape = matches[0]
        actual = tuple(float(shape[field]) * 4 / 3 for field in ("left", "top", "width", "height"))
        if any(abs(left - right) > tolerance_px for left, right in zip(actual, geometry)):
            mismatches += 1
    return mismatches


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


def _contains_cjk(value: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u9fff"
        or "\uac00" <= char <= "\ud7af"
        or "\uf900" <= char <= "\ufaff"
        for char in value
    )


def _ooxml_font_preflight(path: Path) -> dict[str, Any]:
    """Read the sealed package directly; never infer CJK coverage from HTML."""

    drawing_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    cjk_runs = 0
    east_asian_typefaces: set[str] = set()
    digest = sha256()
    with zipfile.ZipFile(path, "r") as package:
        names = sorted(
            name
            for name in package.namelist()
            if name.startswith(("ppt/slides/", "ppt/theme/", "ppt/presentation"))
            and name.endswith(".xml")
        )
        for name in names:
            payload = package.read(name)
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(payload)
            root = ET.fromstring(payload)
            cjk_runs += sum(
                1
                for node in root.iter(f"{{{drawing_ns}}}t")
                if _contains_cjk(str(node.text or ""))
            )
            east_asian_typefaces.update(
                str(node.attrib.get("typeface") or "").strip()
                for node in root.iter(f"{{{drawing_ns}}}ea")
                if str(node.attrib.get("typeface") or "").strip()
            )
        embedded_font_files = sum(
            name.startswith("ppt/fonts/") and not name.endswith("/")
            for name in package.namelist()
        )
    return {
        "cjk_text_runs": cjk_runs,
        "declared_east_asian_typefaces": east_asian_typefaces,
        "embedded_font_files": embedded_font_files,
        "ooxml_readback_sha256": digest.hexdigest(),
    }


def _font_preflight_report(
    package_evidence: dict[str, Any],
    first_snapshot: list[list[dict[str, Any]]],
    reopened_snapshot: list[list[dict[str, Any]]],
    *,
    embedding_required: bool,
) -> dict[str, Any]:
    def cjk_readback_runs(snapshot: list[list[dict[str, Any]]]) -> list[tuple[str, str]]:
        runs: list[tuple[str, str]] = []
        for slide in snapshot:
            for shape in slide:
                text = str(shape.get("text") or "")
                if _contains_cjk(text):
                    runs.append((text, str(shape.get("eastAsianTypeface") or "").strip()))
                table = shape.get("table")
                if isinstance(table, dict):
                    for row in table.get("cells", ()):
                        for cell in row:
                            cell_text = str(cell.get("text") or "")
                            font = cell.get("font") if isinstance(cell.get("font"), dict) else {}
                            if _contains_cjk(cell_text):
                                runs.append(
                                    (cell_text, str(font.get("eastAsianTypeface") or "").strip())
                                )
                chart = shape.get("chart")
                if isinstance(chart, dict):
                    title = chart.get("title") if isinstance(chart.get("title"), dict) else {}
                    title_text = str(title.get("text") or "")
                    if _contains_cjk(title_text):
                        runs.append(
                            (
                                title_text,
                                str(title.get("eastAsianTypeface") or "").strip(),
                            )
                        )
                    axes = chart.get("axes") if isinstance(chart.get("axes"), dict) else {}
                    for axis in axes.values():
                        if not isinstance(axis, dict):
                            continue
                        axis_title = str(axis.get("title") or "")
                        if _contains_cjk(axis_title):
                            runs.append(
                                (
                                    axis_title,
                                    str(
                                        axis.get("titleEastAsianTypeface") or ""
                                    ).strip(),
                                )
                            )
                    legend = (
                        chart.get("legend")
                        if isinstance(chart.get("legend"), dict)
                        else {}
                    )
                    legend_face = str(
                        legend.get("eastAsianTypeface") or ""
                    ).strip()
                    category_axis = (
                        axes.get("category")
                        if isinstance(axes.get("category"), dict)
                        else {}
                    )
                    category_face = str(
                        category_axis.get("tickLabelEastAsianTypeface") or ""
                    ).strip()
                    for series in chart.get("series", ()):
                        if not isinstance(series, dict):
                            continue
                        series_name = str(series.get("name") or "")
                        if _contains_cjk(series_name):
                            runs.append((series_name, legend_face))
                        for category in series.get("categories", ()):
                            category_text = str(category or "")
                            if _contains_cjk(category_text):
                                runs.append((category_text, category_face))
        return runs

    first_cjk_runs = cjk_readback_runs(first_snapshot)
    reopened_cjk_runs = cjk_readback_runs(reopened_snapshot)
    declared = set(package_evidence["declared_east_asian_typefaces"])
    declared.update(
        typeface for _, typeface in reopened_cjk_runs if typeface
    )
    missing_typeface_runs = sum(
        not typeface for _, typeface in reopened_cjk_runs
    )
    replacement_glyphs = sum(
        text.count("\ufffd") + text.count("\u25a1")
        for text, _ in reopened_cjk_runs
    )
    embedded_font_files = int(package_evidence["embedded_font_files"])
    stable_text = first_cjk_runs == reopened_cjk_runs
    cjk_runs = int(package_evidence["cjk_text_runs"])
    passed = (
        cjk_runs == 0
        or (
            bool(reopened_cjk_runs)
            and missing_typeface_runs == 0
            and replacement_glyphs == 0
            and stable_text
            and (not embedding_required or embedded_font_files > 0)
        )
    )
    return {
        "status": "not-required" if cjk_runs == 0 else ("passed" if passed else "blocked"),
        "cjk_text_runs": cjk_runs,
        "declared_east_asian_typefaces": sorted(declared),
        "missing_east_asian_typeface_runs": missing_typeface_runs,
        "replacement_glyphs": replacement_glyphs,
        "embedded_font_files": embedded_font_files,
        "embedding_required": embedding_required,
        "embedding_verified": embedded_font_files > 0,
        "ooxml_readback_sha256": package_evidence["ooxml_readback_sha256"],
    }


def _run_powerpoint_com_qa(
    path: Path,
    expected_slides: int,
    evidence_directory: Path | None = None,
    expected_native_qids: frozenset[str] = frozenset(),
    expected_qid_owners: frozenset[tuple[int, str, str]] = frozenset(),
    expected_geometry: dict[tuple[int, str], tuple[float, float, float, float]] | None = None,
    geometry_contract_digest: str | None = None,
    interactive_observation: bool = False,
    require_font_embedding: bool = True,
    release_required: bool = False,
) -> dict[str, Any]:
    import pythoncom  # type: ignore[import-not-found]
    import win32com.client  # type: ignore[import-not-found]

    pythoncom.CoInitialize()
    application = first = reopened = None
    owned_application = False
    dialog_watch: _PowerPointDialogWatch | None = None
    observation_states: list[str] = []
    dialog_watcher_status = "not-requested"
    dialog_watcher_armed_before_first_open = False
    owned_process_id: int | None = None
    try:
        package_font_evidence = _ooxml_font_preflight(path)
        preexisting_pids = _powerpoint_window_pids()
        application = win32com.client.DispatchEx("PowerPoint.Application")
        main_hwnd = _powerpoint_main_hwnd(application)
        import win32process  # type: ignore[import-not-found]
        _, process_id = win32process.GetWindowThreadProcessId(main_hwnd)
        if int(process_id) <= 0 or int(process_id) in preexisting_pids:
            raise PresentationExportContractError("presentation_export_powerpoint_ownership_unproven")
        owned_process_id = int(process_id)
        owned_application = True
        application.DisplayAlerts = 1
        watcher_requested = interactive_observation or release_required
        if watcher_requested:
            try:
                application.Visible = True
                dialog_watch = _start_powerpoint_dialog_watch(
                    main_hwnd
                )
                interactive_observation = True
                dialog_watcher_status = "armed"
                dialog_watcher_armed_before_first_open = True
            except Exception:
                dialog_watcher_status = "unavailable"
                if release_required:
                    raise PresentationExportContractError(
                        "presentation_export_powerpoint_dialog_watcher_unavailable"
                    )
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
            _advance_powerpoint_qa_observation(observation_states, "first-open")
            slide_count = int(first.Slides.Count)
            shape_counts = [
                int(first.Slides.Item(i).Shapes.Count)
                for i in range(1, slide_count + 1)
            ]
            first_snapshot, first_snapshot_failures = _powerpoint_deck_snapshot(first)
            first.Export(str(first_render), "PNG", 1280, 720)
            first.SaveCopyAs(str(copy_path), 24)
            _advance_powerpoint_qa_observation(observation_states, "save-copy")
            first.Close()
            first = None
            reopened = application.Presentations.Open(
                str(copy_path), True, False, bool(interactive_observation)
            )
            _advance_powerpoint_qa_observation(observation_states, "reopen")
            reopened_count = int(reopened.Slides.Count)
            reopened_shapes = [
                int(reopened.Slides.Item(i).Shapes.Count)
                for i in range(1, reopened_count + 1)
            ]
            reopened_snapshot, reopened_snapshot_failures = _powerpoint_deck_snapshot(
                reopened
            )
            font_preflight = _font_preflight_report(
                package_font_evidence,
                first_snapshot,
                reopened_snapshot,
                embedding_required=require_font_embedding,
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
                        is_raster_owner = (
                            shape_name.startswith("presenton raster visual fragment ")
                            and shape_name.endswith(" semantic-owner")
                        )
                        shape_type = int(shape.Type)
                        if not is_backplate and shape_type in {11, 13}:
                            native_image += 1
                            crop = _crop_snapshot(shape)
                            if crop is not None and any(abs(value) > 0.01 for value in crop.values()):
                                native_image_crop += 1
                        elif (
                            not is_backplate
                            and not is_raster_owner
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
            if expected_qid_owners:
                owner_census = _qid_owner_census(reopened_snapshot, expected_qid_owners)
            else:
                owner_census = {
                    "qid_owner_missing": sum(
                        not any(_contains_presenton_qid(name, qid) for name in observed_shape_names)
                        for qid in expected_native_qids
                    ),
                    "qid_owner_ghosting": _native_qid_ghosting(
                        reopened_snapshot, expected_native_qids
                    ),
                    "qid_owner_mismatches": 0,
                }
            missing_qids = owner_census["qid_owner_missing"]
            geometry_mismatches = _geometry_contract_mismatches(
                reopened_snapshot,
                expected_geometry or {},
            )
            has_semantic_expectation = bool(
                expected_qid_owners or expected_native_qids
            )
            semantic_payload_binding = (
                "passed"
                if has_semantic_expectation
                and not any(owner_census.values())
                and geometry_mismatches == 0
                else "failed" if has_semantic_expectation else "not-verified"
            )
            _advance_powerpoint_qa_observation(observation_states, "readback")
            native_qid_ghosting = owner_census["qid_owner_ghosting"]
            backplate_collisions = _backplate_collisions(reopened_snapshot)
            dialog_watcher_status, repair_dialogs = _seal_powerpoint_dialog_watch(
                dialog_watch,
                requested=watcher_requested,
            )
            _advance_powerpoint_qa_observation(
                observation_states, "observation-sealed"
            )
            repair_observation = (
                "interactive-observation"
                if dialog_watcher_status == "sealed"
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
            base_passed = not (
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
                or missing_qids > 0
                or owner_census["qid_owner_mismatches"] > 0
                or native_qid_ghosting > 0
                or geometry_mismatches > 0
                or font_preflight["status"] == "blocked"
                or (repair_dialogs is not None and repair_dialogs != 0)
            )
            release_gate_failures = _powerpoint_release_gate_failures(
                release_required=release_required,
                observation_states=observation_states,
                dialog_watcher_status=dialog_watcher_status,
                dialog_watcher_armed_before_first_open=(
                    dialog_watcher_armed_before_first_open
                ),
                repair_dialogs=repair_dialogs,
                readback_match=comparison["readback_match"],
                semantic_payload_binding=semantic_payload_binding,
            )
            passed = base_passed and not release_gate_failures
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
                "observation_states": observation_states.copy(),
                "dialog_watcher_status": dialog_watcher_status,
                "dialog_watcher_armed_before_first_open": (
                    dialog_watcher_armed_before_first_open
                ),
                "owned_process_id": owned_process_id,
                "release_gate_failures": release_gate_failures,
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
                "inspection_failures": inspection_failures,
                "native_text": native_text,
                "native_image": native_image,
                "native_shape": native_shape,
                "native_table": native_table,
                "native_chart": native_chart,
                "native_image_crop": native_image_crop,
                "native_qid_missing": missing_qids,
                "native_qid_ghosting": native_qid_ghosting,
                "qid_owner_mismatches": owner_census["qid_owner_mismatches"],
                "geometry_contract_digest": geometry_contract_digest,
                "geometry_contract_mismatches": geometry_mismatches,
                "backplate_collisions": backplate_collisions,
                "font_preflight": font_preflight,
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
            dialog_watch.stop.set()
            dialog_watch.thread.join(timeout=1)
        for deck in (reopened, first) if owned_application else ():
            if deck is not None:
                try:
                    deck.Close()
                except Exception:
                    pass
        if application is not None and owned_application:
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
    expected_qid_owners: frozenset[tuple[int, str, str]] = frozenset(),
    expected_geometry: dict[tuple[int, str], tuple[float, float, float, float]] | None = None,
    geometry_contract_digest: str | None = None,
    require_font_embedding: bool = True,
    release_required: bool = False,
) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "status": "failed" if release_required else "preflight",
            "platform": "unsupported",
            "release_gate_failures": (
                ["watcher-unavailable"] if release_required else []
            ),
        }
    if not _powerpoint_executable_available():
        return {
            "status": "failed" if release_required else "not-verified",
            "platform": "windows",
            "release_gate_failures": (
                ["watcher-unavailable"] if release_required else []
            ),
        }
    try:
        return await asyncio.to_thread(
            _run_powerpoint_com_qa,
            path,
            expected_slides,
            evidence_directory,
            expected_native_qids,
            expected_qid_owners,
            expected_geometry,
            geometry_contract_digest,
            os.getenv(
                "AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION", "false"
            ).lower()
            == "true",
            require_font_embedding,
            release_required,
        )
    except Exception as error:
        reason = (
            "watcher-unavailable"
            if "dialog_watcher_unavailable" in str(error)
            else "observation-state-incomplete"
        )
        return {
            "status": "failed",
            "platform": "windows",
            "supported": True,
            "automation": "PowerPoint.COM",
            "release_gate_failures": [reason] if release_required else [],
        }


__all__ = [
    "publish_finalized_hybrid_sidecars",
    "require_matching_hybrid_sidecars",
    "resolve_hybrid_artifact",
    "trusted_powerpoint_qa",
]
