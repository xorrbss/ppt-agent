"""Gated exact-reconstruction evaluation for authored slides.

This is a manual evaluation harness, not a CI test. It can score pre-rendered
source/candidate pairs without network access, generate deterministic synthetic
fixtures for a self-test, or exercise the real authored model pipeline when the
operator explicitly acknowledges cost.

Run from ``servers/fastapi``::

    uv run python scripts/evaluate_exact_reconstruction.py --self-test
    uv run python scripts/evaluate_exact_reconstruction.py --manifest path/to/manifest.json
    PRESENTON_EXACT_EVAL_LIVE=1 uv run python scripts/evaluate_exact_reconstruction.py \
      --live --acknowledge-model-cost --synthetic-slides 1

The report contains no API keys and the harness never prints prompt/image bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter


FASTAPI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = FASTAPI_ROOT.parents[1]
DEFAULT_OUTPUT = REPO_ROOT / ".test-runtime" / "exact-reconstruction-eval" / "report.json"
LIVE_GATE = "PRESENTON_EXACT_EVAL_LIVE"
CANVAS = (1280, 720)
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "footer", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "li", "main", "p", "section", "table",
    "td", "th", "tr",
}


@dataclass
class CallCounts:
    author: int = 0
    initial_comparison: int = 0
    revision: int = 0
    final_comparison: int = 0


@dataclass(frozen=True)
class ProcessResult:
    status: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float
    failure_reason: str | None = None


@dataclass(frozen=True)
class OcrRuntime:
    status: str
    command: tuple[str, ...]
    version: str | None
    requested_languages: tuple[str, ...]
    available_languages: tuple[str, ...]
    tessdata_dir: str | None
    inspection_seconds: float
    failure_reason: str | None = None


@dataclass(frozen=True)
class TextCapture:
    status: str
    method: str
    raw_text: str | None
    elapsed_seconds: float
    failure_reason: str | None = None
    returncode: int | None = None


class VisibleTextParser(HTMLParser):
    """Small dependency-free visible-text proxy when OCR is unavailable."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"style", "script", "svg"}:
            self.hidden_depth += 1
        elif tag == "br" and not self.hidden_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"style", "script", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag in BLOCK_TAGS and not self.hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts).replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def _load_font(size: int, *, korean: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [Path("C:/Windows/Fonts/malgun.ttf")] if korean else [Path("C:/Windows/Fonts/arial.ttf")]
    ) + [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, char_left in enumerate(left, start=1):
        current = [row]
        for column, char_right in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (char_left != char_right),
                )
            )
        previous = current
    return previous[-1]


def _normalize_ocr_text(value: str) -> str:
    """Normalize presentation text without erasing line-break differences."""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [re.sub(r"[\t \u2000-\u200a]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(lines).strip("\n")


def _character_class(char: str) -> str | None:
    code = ord(char)
    if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return "korean"
    if "A" <= char <= "Z" or "a" <= char <= "z":
        return "english"
    if char.isdigit():
        return "digits"
    if not char.isspace():
        return "symbols"
    return None


def _edit_alignment(source: str, candidate: str) -> dict[str, Any]:
    """Return Levenshtein operations and exact source-character preservation."""
    rows, columns = len(source) + 1, len(candidate) + 1
    matrix = np.empty((rows, columns), dtype=np.int32)
    matrix[:, 0] = np.arange(rows, dtype=np.int32)
    matrix[0, :] = np.arange(columns, dtype=np.int32)
    for row in range(1, rows):
        source_char = source[row - 1]
        for column in range(1, columns):
            matrix[row, column] = min(
                matrix[row - 1, column] + 1,
                matrix[row, column - 1] + 1,
                matrix[row - 1, column - 1] + (source_char != candidate[column - 1]),
            )

    deletions = insertions = substitutions = matches = 0
    class_totals = {key: 0 for key in ("korean", "english", "digits", "symbols")}
    class_matches = dict(class_totals)
    for char in source:
        category = _character_class(char)
        if category:
            class_totals[category] += 1
    row, column = len(source), len(candidate)
    while row or column:
        if row and column and source[row - 1] == candidate[column - 1] and matrix[row, column] == matrix[row - 1, column - 1]:
            category = _character_class(source[row - 1])
            if category:
                class_matches[category] += 1
            matches += 1
            row -= 1
            column -= 1
        elif row and column and matrix[row, column] == matrix[row - 1, column - 1] + 1:
            substitutions += 1
            row -= 1
            column -= 1
        elif row and matrix[row, column] == matrix[row - 1, column] + 1:
            deletions += 1
            row -= 1
        else:
            insertions += 1
            column -= 1

    preservation = {
        category: {
            "source_count": total,
            "exact_matches": class_matches[category],
            "missing_or_substituted": total - class_matches[category],
            "preservation_rate": class_matches[category] / total if total else None,
        }
        for category, total in class_totals.items()
    }
    return {
        "edit_distance": int(matrix[-1, -1]),
        "matches": matches,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "character_classes": preservation,
    }


def _comparison_metrics(source: str, candidate: str) -> dict[str, Any]:
    alignment = _edit_alignment(source, candidate)
    source_breaks = {i for i, char in enumerate(source) if char == "\n"}
    candidate_breaks = {i for i, char in enumerate(candidate) if char == "\n"}
    if not source_breaks and not candidate_breaks:
        break_f1 = 1.0
    else:
        overlap = len(source_breaks & candidate_breaks)
        precision = overlap / len(candidate_breaks) if candidate_breaks else 0.0
        recall = overlap / len(source_breaks) if source_breaks else 0.0
        break_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "source_characters": len(source),
        "candidate_characters": len(candidate),
        **alignment,
        "cer": alignment["edit_distance"] / max(1, len(source)),
        "missing_rate": alignment["deletions"] / max(1, len(source)),
        "source_character_error_rate": (
            alignment["deletions"] + alignment["substitutions"]
        ) / max(1, len(source)),
        "line_break_exact": source.split("\n") == candidate.split("\n"),
        "line_break_f1": break_f1,
        "source_line_breaks": len(source_breaks),
        "candidate_line_breaks": len(candidate_breaks),
        "missing_line_breaks": max(0, len(source_breaks) - len(candidate_breaks)),
        "extra_line_breaks": max(0, len(candidate_breaks) - len(source_breaks)),
    }


def _capture_report(capture: TextCapture) -> dict[str, Any]:
    return {
        "status": capture.status,
        "method": capture.method,
        "characters": len(capture.raw_text) if capture.raw_text is not None else None,
        "elapsed_seconds": capture.elapsed_seconds,
        "failure_reason": capture.failure_reason,
        "returncode": capture.returncode,
    }


def _text_metrics(source: TextCapture, candidate: TextCapture, method: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": source.status == "success" and candidate.status == "success",
        "method": method,
        "source_capture": _capture_report(source),
        "render_capture": _capture_report(candidate),
    }
    if not result["available"] or source.raw_text is None or candidate.raw_text is None:
        failures = [
            f"source:{source.status}" if source.status != "success" else None,
            f"render:{candidate.status}" if candidate.status != "success" else None,
        ]
        result["reason"] = ",".join(item for item in failures if item) or "text_unavailable"
        return result
    raw_source = source.raw_text.replace("\r\n", "\n").replace("\r", "\n")
    raw_candidate = candidate.raw_text.replace("\r\n", "\n").replace("\r", "\n")
    raw = _comparison_metrics(raw_source, raw_candidate)
    normalized = _comparison_metrics(
        _normalize_ocr_text(raw_source), _normalize_ocr_text(raw_candidate)
    )
    result.update(
        {
            "raw": raw,
            "normalized": normalized,
            # Backward-compatible aliases for prior evaluation reports.
            "cer": raw["cer"],
            "normalized_cer": normalized["cer"],
            "missing_rate": raw["missing_rate"],
            "line_break_exact": raw["line_break_exact"],
            "line_break_f1": raw["line_break_f1"],
        }
    )
    return result


def _image_metrics(source_path: Path, candidate_path: Path) -> dict[str, Any]:
    with Image.open(source_path) as source_raw, Image.open(candidate_path) as candidate_raw:
        source_size = source_raw.size
        candidate_size = candidate_raw.size
        source = source_raw.convert("RGB").resize(CANVAS)
        candidate = candidate_raw.convert("RGB").resize(CANVAS)
        source_array = np.asarray(source, dtype=np.float32)
        candidate_array = np.asarray(candidate, dtype=np.float32)
        delta = source_array - candidate_array
        mae = float(np.mean(np.abs(delta)))
        mse = float(np.mean(delta * delta))
        source_edges = np.asarray(source.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32)
        candidate_edges = np.asarray(candidate.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32)
        edge_mae = float(np.mean(np.abs(source_edges - candidate_edges)))
    return {
        "source_size": list(source_size),
        "candidate_size": list(candidate_size),
        "canvas_exact": source_size == candidate_size == CANVAS,
        "mae": mae,
        "rmse": math.sqrt(mse),
        "psnr_db": None if mse == 0 else 20 * math.log10(255 / math.sqrt(mse)),
        "pixel_similarity": max(0.0, 1.0 - mae / 255),
        "edge_similarity": max(0.0, 1.0 - edge_mae / 255),
    }


def _iou(left: dict[str, float], right: dict[str, float]) -> float:
    lx2, ly2 = left["x"] + left["width"], left["y"] + left["height"]
    rx2, ry2 = right["x"] + right["width"], right["y"] + right["height"]
    width = max(0.0, min(lx2, rx2) - max(left["x"], right["x"]))
    height = max(0.0, min(ly2, ry2) - max(left["y"], right["y"]))
    intersection = width * height
    union = left["width"] * left["height"] + right["width"] * right["height"] - intersection
    return intersection / union if union else 1.0


def _layout_metrics(source_objects: Sequence[dict[str, Any]], candidate_objects: Sequence[dict[str, Any]]) -> dict[str, Any]:
    source_by_id = {str(item["id"]): item for item in source_objects if "id" in item}
    candidate_by_id = {str(item["id"]): item for item in candidate_objects if "id" in item}
    common = sorted(source_by_id.keys() & candidate_by_id.keys())
    if not common:
        return {"available": False, "reason": "object_boxes_unavailable"}
    ious = [_iou(source_by_id[key], candidate_by_id[key]) for key in common]
    return {
        "available": True,
        "source_objects": len(source_by_id),
        "candidate_objects": len(candidate_by_id),
        "matched_objects": len(common),
        "mean_bbox_iou": mean(ious),
        "missing_ids": sorted(source_by_id.keys() - candidate_by_id.keys()),
        "extra_ids": sorted(candidate_by_id.keys() - source_by_id.keys()),
    }


def _run_process(command: Sequence[str], *, timeout_seconds: float) -> ProcessResult:
    """Run and always reap a child, including timeout and interruption paths."""
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError as error:
        return ProcessResult(
            status="start_failed",
            returncode=None,
            stdout=b"",
            stderr=b"",
            elapsed_seconds=time.perf_counter() - started,
            failure_reason=type(error).__name__,
        )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                process.kill()
            stdout, stderr = process.communicate()
        return ProcessResult(
            status="timeout",
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            elapsed_seconds=time.perf_counter() - started,
            failure_reason=f"timeout_after_{timeout_seconds:g}_seconds",
        )
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    return ProcessResult(
        status="success" if process.returncode == 0 else "failed",
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=time.perf_counter() - started,
        failure_reason=None if process.returncode == 0 else f"exit_code_{process.returncode}",
    )


def _decode_output(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _find_tesseract(explicit: Path | None) -> tuple[str, ...]:
    if explicit is not None:
        return (str(explicit.resolve()),) if explicit.is_file() else ()
    discovered = shutil.which("tesseract")
    if discovered:
        return (discovered,)
    if sys.platform == "win32":
        for candidate in (
            Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
            Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        ):
            if candidate.is_file():
                return (str(candidate),)
    return ()


def _inspect_tesseract(
    *,
    explicit_command: Path | None,
    tessdata_dir: Path | None,
    language: str,
    timeout_seconds: float,
) -> OcrRuntime:
    started = time.perf_counter()
    requested = tuple(part.strip() for part in language.split("+") if part.strip())
    if not requested or any(not re.fullmatch(r"[A-Za-z0-9_]+", part) for part in requested):
        raise ValueError("ocr language must be a '+'-separated list of safe language codes")
    command = _find_tesseract(explicit_command)
    resolved_tessdata = str(tessdata_dir.resolve()) if tessdata_dir else None
    if not command:
        return OcrRuntime(
            status="unavailable",
            command=(),
            version=None,
            requested_languages=requested,
            available_languages=(),
            tessdata_dir=resolved_tessdata,
            inspection_seconds=time.perf_counter() - started,
            failure_reason="tesseract_executable_not_found",
        )
    version_result = _run_process((*command, "--version"), timeout_seconds=timeout_seconds)
    if version_result.status != "success":
        return OcrRuntime(
            status="timeout" if version_result.status == "timeout" else "failed",
            command=command,
            version=None,
            requested_languages=requested,
            available_languages=(),
            tessdata_dir=resolved_tessdata,
            inspection_seconds=time.perf_counter() - started,
            failure_reason=f"version_check_{version_result.failure_reason or version_result.status}",
        )
    version_lines = _decode_output(version_result.stdout or version_result.stderr).splitlines()
    version = version_lines[0].strip() if version_lines else "unknown"
    list_command = [*command]
    if resolved_tessdata:
        list_command.extend(("--tessdata-dir", resolved_tessdata))
    list_command.append("--list-langs")
    language_result = _run_process(list_command, timeout_seconds=timeout_seconds)
    if language_result.status != "success":
        return OcrRuntime(
            status="timeout" if language_result.status == "timeout" else "failed",
            command=command,
            version=version,
            requested_languages=requested,
            available_languages=(),
            tessdata_dir=resolved_tessdata,
            inspection_seconds=time.perf_counter() - started,
            failure_reason=f"language_check_{language_result.failure_reason or language_result.status}",
        )
    listing = _decode_output(language_result.stdout + b"\n" + language_result.stderr)
    available = tuple(
        sorted(
            line.strip()
            for line in listing.splitlines()
            if re.fullmatch(r"[A-Za-z0-9_]+", line.strip())
        )
    )
    missing = tuple(item for item in requested if item not in available)
    return OcrRuntime(
        status="language_missing" if missing else "ready",
        command=command,
        version=version,
        requested_languages=requested,
        available_languages=available,
        tessdata_dir=resolved_tessdata,
        inspection_seconds=time.perf_counter() - started,
        failure_reason=f"missing_languages:{'+'.join(missing)}" if missing else None,
    )


def _runtime_report(runtime: OcrRuntime, *, text_source: str) -> dict[str, Any]:
    return {
        "text_source": text_source,
        "engine": "tesseract" if text_source == "tesseract" else "not_requested",
        "status": runtime.status,
        "version": runtime.version,
        "requested_languages": list(runtime.requested_languages),
        "available_languages": list(runtime.available_languages),
        "tessdata_dir": runtime.tessdata_dir,
        "inspection_seconds": runtime.inspection_seconds,
        "failure_reason": runtime.failure_reason,
    }


def _clean_tesseract_stdout(stdout: bytes) -> str:
    text = _decode_output(stdout).replace("\r\n", "\n").replace("\r", "\n").rstrip("\f")
    # Tesseract emits one transport newline after the page; it is not source content.
    return text[:-1] if text.endswith("\n") else text


def _ocr_image(
    image_path: Path,
    *,
    runtime: OcrRuntime,
    language: str,
    psm: int,
    timeout_seconds: float,
) -> TextCapture:
    if runtime.status != "ready":
        return TextCapture(
            status=runtime.status,
            method="tesseract",
            raw_text=None,
            elapsed_seconds=0.0,
            failure_reason=runtime.failure_reason,
        )
    command = [*runtime.command, str(image_path), "stdout"]
    if runtime.tessdata_dir:
        command.extend(("--tessdata-dir", runtime.tessdata_dir))
    command.extend(("-l", language, "--psm", str(psm)))
    result = _run_process(command, timeout_seconds=timeout_seconds)
    if result.status != "success":
        return TextCapture(
            status="timeout" if result.status == "timeout" else "failed",
            method="tesseract",
            raw_text=None,
            elapsed_seconds=result.elapsed_seconds,
            failure_reason=result.failure_reason or result.status,
            returncode=result.returncode,
        )
    return TextCapture(
        status="success",
        method="tesseract",
        raw_text=_clean_tesseract_stdout(result.stdout),
        elapsed_seconds=result.elapsed_seconds,
        returncode=result.returncode,
    )


def _html_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    return parser.text()


def _resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    slides = payload.get("slides") if isinstance(payload, dict) else None
    if not isinstance(slides, list) or not slides:
        raise ValueError("manifest must contain a non-empty 'slides' list")
    base = path.parent
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(slides):
        if not isinstance(raw, dict):
            raise ValueError(f"slides[{index}] must be an object")
        slide = dict(raw)
        slide_id = str(slide.get("id") or f"slide-{index + 1}")
        if slide_id in ids:
            raise ValueError(f"duplicate slide id: {slide_id}")
        ids.add(slide_id)
        if not slide.get("source_image"):
            raise ValueError(f"{slide_id}: source_image is required")
        slide["id"] = slide_id
        slide["source_image"] = str(_resolve_path(base, str(slide["source_image"])))
        if slide.get("render_image"):
            slide["render_image"] = str(_resolve_path(base, str(slide["render_image"])))
        result.append(slide)
    return result


def _synthetic_slides(directory: Path, count: int, *, candidates: bool) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    palette = ["#1D4ED8", "#0F766E", "#9333EA", "#C2410C", "#334155"]
    slides: list[dict[str, Any]] = []
    for index in range(count):
        slide_id = f"synthetic-{index + 1}"
        title = f"정확 복원 EXACT {index + 1:02d}"
        body = (
            f"매출 Revenue  {120 + index * 17}\n"
            f"Q{index + 1} 목표 target: {91 + index}%\n"
            "기호 Symbols: ₩ · + / # &"
        )
        source_path = directory / f"{slide_id}-source.png"
        image = Image.new("RGB", CANVAS, "#F8FAFC")
        draw = ImageDraw.Draw(image)
        color = palette[index % len(palette)]
        draw.rectangle((0, 0, 1280, 72), fill=color)
        draw.text((64, 118), title, fill="#0F172A", font=_load_font(42, korean=True))
        draw.multiline_text((64, 208), body, fill="#334155", font=_load_font(30, korean=True), spacing=16)
        draw.rounded_rectangle((820, 170, 1180, 555), radius=28, fill=color)
        draw.ellipse((930, 270, 1070, 410), fill="#FFFFFF")
        image.save(source_path)
        objects = [
            {"id": "header", "x": 0, "y": 0, "width": 1280, "height": 72},
            {"id": "title", "x": 64, "y": 118, "width": 650, "height": 52},
            {"id": "body", "x": 64, "y": 208, "width": 520, "height": 90},
            {"id": "card", "x": 820, "y": 170, "width": 360, "height": 385},
        ]
        slide: dict[str, Any] = {
            "id": slide_id,
            "source_image": str(source_path),
            "source_text": f"{title}\n{body}",
            "content": f"{title}\n{body}",
            "role": "content",
            "source_objects": objects,
        }
        if candidates:
            candidate_path = directory / f"{slide_id}-render.png"
            candidate = image.copy()
            candidate_text = slide["source_text"]
            candidate_objects = [dict(item) for item in objects]
            if index == 1:
                candidate_text = candidate_text.replace("매출 Revenue", "매츨 Revenve")
            elif index == 2:
                candidate_text = candidate_text.replace("\nQ", " Q")
            elif index == 3:
                altered = ImageDraw.Draw(candidate)
                altered.rectangle((805, 170, 1180, 555), fill="#FFFFFF")
                altered.rounded_rectangle((850, 170, 1210, 555), radius=28, fill=color)
                candidate_objects[-1]["x"] = 850
            elif index == 4:
                altered = ImageDraw.Draw(candidate)
                altered.rectangle((0, 0, 1280, 72), fill="#111827")
            if index in {1, 2}:
                altered = ImageDraw.Draw(candidate)
                altered.rectangle((50, 105, 760, 380), fill="#F8FAFC")
                candidate_title, candidate_body = candidate_text.split("\n", 1)
                altered.text(
                    (64, 118),
                    candidate_title,
                    fill="#0F172A",
                    font=_load_font(42, korean=True),
                )
                altered.multiline_text(
                    (64, 208),
                    candidate_body,
                    fill="#334155",
                    font=_load_font(30, korean=True),
                    spacing=16,
                )
            candidate.save(candidate_path)
            slide.update(
                {
                    "render_image": str(candidate_path),
                    "render_text": candidate_text,
                    "candidate_objects": candidate_objects,
                }
            )
        slides.append(slide)
    return slides


def _capture_slide_text(
    slide: dict[str, Any],
    *,
    side: str,
    text_source: str,
    runtime: OcrRuntime,
    ocr_language: str,
    ocr_psm: int,
    ocr_timeout_seconds: float,
) -> TextCapture:
    if text_source == "tesseract":
        image_field = "source_image" if side == "source" else "render_image"
        return _ocr_image(
            Path(slide[image_field]),
            runtime=runtime,
            language=ocr_language,
            psm=ocr_psm,
            timeout_seconds=ocr_timeout_seconds,
        )
    if text_source == "manifest":
        field = "source_text" if side == "source" else "render_text"
        value = slide.get(field)
        if isinstance(value, str):
            return TextCapture("success", "manifest_text", value, 0.0)
        return TextCapture("manifest_text_missing", "manifest_text", None, 0.0, field)
    if text_source == "html_proxy":
        if side == "source":
            value = slide.get("source_text")
            if isinstance(value, str):
                return TextCapture("success", "manifest_text", value, 0.0)
            return TextCapture(
                "manifest_text_missing", "manifest_text", None, 0.0, "source_text"
            )
        html = slide.get("render_html")
        if isinstance(html, str) and html:
            started = time.perf_counter()
            return TextCapture(
                "success", "html_visible_text_proxy", _html_text(html), time.perf_counter() - started
            )
        return TextCapture(
            "html_proxy_missing", "html_visible_text_proxy", None, 0.0, "render_html"
        )
    raise ValueError(f"unsupported text source: {text_source}")


def _score_slide(
    slide: dict[str, Any],
    *,
    text_source: str,
    runtime: OcrRuntime,
    ocr_language: str,
    ocr_psm: int,
    ocr_timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_path = Path(slide["source_image"])
    candidate_path = Path(slide["render_image"])
    if not source_path.is_file() or not candidate_path.is_file():
        raise FileNotFoundError(f"{slide['id']}: source/render image is missing")
    source_capture = _capture_slide_text(
        slide,
        side="source",
        text_source=text_source,
        runtime=runtime,
        ocr_language=ocr_language,
        ocr_psm=ocr_psm,
        ocr_timeout_seconds=ocr_timeout_seconds,
    )
    candidate_capture = _capture_slide_text(
        slide,
        side="render",
        text_source=text_source,
        runtime=runtime,
        ocr_language=ocr_language,
        ocr_psm=ocr_psm,
        ocr_timeout_seconds=ocr_timeout_seconds,
    )
    return {
        "id": slide["id"],
        "image": _image_metrics(source_path, candidate_path),
        "text": _text_metrics(
            source_capture,
            candidate_capture,
            "source_image_ocr_vs_render_image_ocr" if text_source == "tesseract" else text_source,
        ),
        "layout": _layout_metrics(slide.get("source_objects", []), slide.get("candidate_objects", [])),
        "elapsed_seconds": time.perf_counter() - started,
        "calls": slide.get("calls", asdict(CallCounts())),
        "revision": slide.get("revision", {"attempted": False, "accepted": False, "reason": "not_applicable"}),
    }


def _aggregate(results: Sequence[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    text = [item["text"] for item in results if item["text"].get("available")]
    layout = [item["layout"] for item in results if item["layout"].get("available")]
    class_preservation: dict[str, float | None] = {}
    for category in ("korean", "english", "digits", "symbols"):
        totals = [item["raw"]["character_classes"][category] for item in text]
        source_count = sum(entry["source_count"] for entry in totals)
        exact_matches = sum(entry["exact_matches"] for entry in totals)
        class_preservation[category] = exact_matches / source_count if source_count else None
    calls = CallCounts()
    for result in results:
        for key in asdict(calls):
            setattr(calls, key, getattr(calls, key) + int(result["calls"].get(key, 0)))
    attempted = sum(bool(item["revision"].get("attempted")) for item in results)
    accepted = sum(bool(item["revision"].get("accepted")) for item in results)
    return {
        "slides": len(results),
        "elapsed_seconds": elapsed,
        "seconds_per_slide": elapsed / max(1, len(results)),
        "mean_pixel_similarity": mean(item["image"]["pixel_similarity"] for item in results),
        "mean_edge_similarity": mean(item["image"]["edge_similarity"] for item in results),
        "mean_cer": mean(item["raw"]["cer"] for item in text) if text else None,
        "mean_raw_cer": mean(item["raw"]["cer"] for item in text) if text else None,
        "mean_normalized_cer": mean(item["normalized"]["cer"] for item in text) if text else None,
        "mean_missing_rate": mean(item["raw"]["missing_rate"] for item in text) if text else None,
        "line_break_exact_rate": mean(item["line_break_exact"] for item in text) if text else None,
        "raw_character_class_preservation": class_preservation,
        "mean_bbox_iou": mean(item["mean_bbox_iou"] for item in layout) if layout else None,
        "calls": asdict(calls),
        "revision_attempts": attempted,
        "revision_accepts": accepted,
        "revision_acceptance_rate": accepted / attempted if attempted else None,
    }


def _critique_score(critique: Any) -> tuple[int, int, int] | None:
    if critique is None or critique.needs_fix != bool(critique.issues):
        return None
    if any(issue.detail.lstrip().upper().startswith("INDETERMINATE:") for issue in critique.issues):
        return None
    severity = {"high": 3, "medium": 2, "low": 1}
    return (
        int(critique.needs_fix),
        sum(severity.get(issue.severity, 3) for issue in critique.issues),
        len(critique.issues),
    )


def _acceptance(before: Any, after: Any) -> tuple[bool, str]:
    before_score, after_score = _critique_score(before), _critique_score(after)
    if before_score is None:
        return False, "initial_unusable"
    if after_score is None:
        return False, "final_unusable"
    if after_score < before_score:
        return True, "improved"
    return (False, "unchanged" if after_score == before_score else "worsened")


def _load_live_configuration(
    user_config: Path | None, provider_override: str | None
) -> dict[str, Any]:
    if user_config is not None:
        if not user_config.is_file():
            raise FileNotFoundError(f"USER_CONFIG_PATH does not exist: {user_config}")
        os.environ["USER_CONFIG_PATH"] = str(user_config.resolve())
    from utils.user_config import update_env_with_user_config

    update_env_with_user_config()
    if provider_override:
        # The user config can retain its normal interactive provider while a manual
        # evaluator deliberately selects another already-configured provider.
        os.environ["LLM"] = provider_override
    from utils.llm_config import get_llm_config
    from utils.llm_provider import get_llm_provider, get_model

    get_llm_config()  # Validate configured credentials without serializing them.
    return {"provider": str(get_llm_provider().value), "model": str(get_model())}


async def _run_live(
    slides: list[dict[str, Any]],
    *,
    user_config: Path | None,
    provider_override: str | None,
    artifact_directory: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configuration = _load_live_configuration(user_config, provider_override)
    from utils.authored_styles import AuthoredStyle
    from utils.llm_calls.author_slide import Brand, author_slide_html, build_design_system
    from utils.llm_calls.author_vision_qa import critique_authored, revise_authored_deck
    from utils.slide_capture import render_html_to_png

    references = tuple(Path(slide["source_image"]) for slide in slides)
    if len(references) != len(set(path.resolve() for path in references)):
        raise ValueError("exact live evaluation requires one unique source image per slide")
    if any(not path.is_file() for path in references):
        raise FileNotFoundError("one or more exact source images are missing")
    style = AuthoredStyle(
        id="exact-evaluation",
        name="Exact evaluation",
        description="Private evaluation-only exact reconstruction style",
        preview_bg="#F8FAFC",
        preview_accent="#2563EB",
        brief="Reconstruct the attached source image exactly.",
        slide_reference_images=references,
        reference_mode="exact_reconstruction",
    )
    brand = Brand(topic="Exact reconstruction evaluation", language="English")
    design_system = build_design_system(brand, style)
    n = len(slides)
    htmls: list[str] = []
    pngs: list[bytes] = []
    source_bytes = [path.read_bytes() for path in references]
    started = time.perf_counter()
    author_started = time.perf_counter()
    async def author_and_render(index: int, slide: dict[str, Any]) -> tuple[str, bytes]:
        html = await author_slide_html(
            str(slide.get("content") or slide.get("source_text") or "Reconstruct exactly"),
            design_system,
            brand,
            str(slide.get("role") or "content"),
            index,
            n,
            reference_images=[references[index]],
            reference_mode="exact_reconstruction",
        )
        return html, await render_html_to_png(html)

    authored = await asyncio.gather(
        *(author_and_render(index, slide) for index, slide in enumerate(slides))
    )
    for slide, (html, png) in zip(slides, authored):
        htmls.append(html)
        pngs.append(png)
        slide["calls"] = asdict(CallCounts(author=1))

    configuration["author_render_seconds"] = time.perf_counter() - author_started
    contexts = [str(slide.get("source_text") or slide.get("content") or "")[:240] for slide in slides]
    initial_started = time.perf_counter()
    initial = await critique_authored(
        pngs,
        contexts=contexts,
        source_images=source_bytes,
        reference_mode="exact_reconstruction",
    )
    configuration["initial_comparison_seconds"] = time.perf_counter() - initial_started
    for slide in slides:
        slide["calls"]["initial_comparison"] = 1
    revision_started = time.perf_counter()
    candidate_htmls, candidate_pngs, candidate_indices = await revise_authored_deck(
        htmls,
        pngs,
        [str(slide.get("content") or slide.get("source_text") or "") for slide in slides],
        [str(slide.get("role") or "content") for slide in slides],
        brand,
        design_system,
        max_cycles=1,
        style=style,
        initial_critiques=initial,
        reference_images_by_slide=[(path,) for path in references],
    )
    configuration["revision_seconds"] = time.perf_counter() - revision_started
    for index in candidate_indices:
        slides[index]["calls"]["revision"] = 1
    if candidate_indices:
        final_started = time.perf_counter()
        final = await critique_authored(
            [candidate_pngs[index] for index in candidate_indices],
            contexts=[contexts[index] for index in candidate_indices],
            source_images=[source_bytes[index] for index in candidate_indices],
            reference_mode="exact_reconstruction",
        )
        configuration["final_comparison_seconds"] = time.perf_counter() - final_started
    else:
        final = []
        configuration["final_comparison_seconds"] = 0.0
    final_by_index = dict(zip(candidate_indices, final))
    artifact_directory.mkdir(parents=True, exist_ok=False)
    for index, slide in enumerate(slides):
        chosen_html, chosen_png = htmls[index], pngs[index]
        attempted = index in final_by_index
        accepted, reason = (False, "not_flagged")
        if attempted:
            slide["calls"]["final_comparison"] = 1
            accepted, reason = _acceptance(initial[index], final_by_index[index])
            if accepted:
                chosen_html, chosen_png = candidate_htmls[index], candidate_pngs[index]
        output_png = artifact_directory / f"{index + 1:02d}-{slide['id']}-render.png"
        output_html = artifact_directory / f"{index + 1:02d}-{slide['id']}.html"
        output_png.write_bytes(chosen_png)
        output_html.write_text(chosen_html, encoding="utf-8")
        slide["render_image"] = str(output_png)
        slide["render_html"] = chosen_html
        slide["revision"] = {"attempted": attempted, "accepted": accepted, "reason": reason}
    configuration["pipeline_elapsed_seconds"] = time.perf_counter() - started
    configuration["artifact_directory"] = str(artifact_directory)
    return slides, configuration


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@contextmanager
def _exclusive_output(path: Path) -> Iterable[None]:
    """Prevent overlapping model runs from charging twice and overwriting a report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError(
            f"evaluation output is already locked: {lock_path}; wait for the active run"
        ) from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as lock:
            lock.write(str(os.getpid()))
        yield
    finally:
        lock_path.unlink(missing_ok=True)


async def _main(args: argparse.Namespace) -> int:
    if args.ocr_timeout_seconds <= 0:
        raise ValueError("--ocr-timeout-seconds must be greater than zero")
    if args.live and (not args.acknowledge_model_cost or os.getenv(LIVE_GATE) != "1"):
        raise RuntimeError(
            f"live evaluation requires --acknowledge-model-cost and {LIVE_GATE}=1"
        )
    if args.live and not 1 <= args.synthetic_slides <= 5 and args.manifest is None:
        raise ValueError("live synthetic evaluation accepts 1..5 slides; smoke with 1..2 first")
    output = args.output.resolve()
    configuration: dict[str, Any] = {}
    if args.text_source == "tesseract":
        runtime = _inspect_tesseract(
            explicit_command=args.tesseract_command,
            tessdata_dir=args.tessdata_dir,
            language=args.ocr_language,
            timeout_seconds=min(args.ocr_timeout_seconds, 15.0),
        )
    else:
        runtime = OcrRuntime(
            status="not_requested",
            command=(),
            version=None,
            requested_languages=(),
            available_languages=(),
            tessdata_dir=None,
            inspection_seconds=0.0,
            failure_reason=None,
        )
    with _exclusive_output(output), tempfile.TemporaryDirectory(prefix="presenton-exact-eval-") as temp:
        temp_path = Path(temp)
        if args.self_test:
            slides = _synthetic_slides(temp_path, 5, candidates=True)
            mode = "self_test"
        elif args.manifest:
            slides = _read_manifest(args.manifest.resolve())
            mode = "live" if args.live else "local_manifest"
        elif args.live:
            source_directory = output.parent / "synthetic-sources"
            slides = _synthetic_slides(source_directory, args.synthetic_slides, candidates=False)
            mode = "live_synthetic"
        else:
            raise ValueError("choose --self-test, --manifest, or --live")

        if args.live:
            if len(slides) > 5:
                raise ValueError("live evaluation is capped at 5 slides")
            slides, configuration = await _run_live(
                slides,
                user_config=args.user_config,
                provider_override=args.provider,
                artifact_directory=output.with_suffix("").with_name(output.stem + "-artifacts"),
            )
        elif any(not slide.get("render_image") for slide in slides):
            raise ValueError("local manifests require render_image for every slide")

        started = time.perf_counter()
        results = [
            _score_slide(
                slide,
                text_source=args.text_source,
                runtime=runtime,
                ocr_language=args.ocr_language,
                ocr_psm=args.ocr_psm,
                ocr_timeout_seconds=args.ocr_timeout_seconds,
            )
            for slide in slides
        ]
        elapsed = time.perf_counter() - started
        report = {
            "schema_version": 1,
            "mode": mode,
            "generated_at_epoch": int(time.time()),
            "configuration": configuration,
            "ocr_available": runtime.status == "ready",
            "ocr": _runtime_report(runtime, text_source=args.text_source),
            "aggregate": _aggregate(results, elapsed + float(configuration.get("pipeline_elapsed_seconds", 0))),
            "slides": results,
        }
        _write_report(output, report)

    if args.self_test:
        assert len(report["slides"]) == 5
        if args.text_source == "manifest":
            assert report["slides"][0]["text"]["cer"] == 0
            assert report["slides"][1]["text"]["cer"] > 0
            assert report["slides"][2]["text"]["line_break_exact"] is False
        assert report["slides"][3]["layout"]["mean_bbox_iou"] < 1
        assert report["aggregate"]["calls"] == asdict(CallCounts())
    print(json.dumps({"mode": report["mode"], "slides": len(report["slides"]), "output": str(output), "aggregate": report["aggregate"]}, ensure_ascii=False))
    return 0 if all(item["text"].get("available") for item in report["slides"]) else 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="JSON manifest for source/render pairs")
    parser.add_argument("--self-test", action="store_true", help="score five deterministic local fixtures")
    parser.add_argument("--live", action="store_true", help="run the real authored model/render/review pipeline")
    parser.add_argument("--acknowledge-model-cost", action="store_true", help="explicitly acknowledge billable model calls")
    parser.add_argument("--synthetic-slides", type=int, default=1, help="live synthetic slide count (1..5)")
    parser.add_argument("--user-config", type=Path, help="existing userConfig.json; its contents are never reported")
    parser.add_argument(
        "--provider",
        choices=("openai", "google"),
        help="explicit live-evaluation provider override after loading user config",
    )
    parser.add_argument("--ocr-language", default="kor+eng", help="Tesseract language selection")
    parser.add_argument(
        "--text-source",
        choices=("tesseract", "manifest", "html_proxy"),
        default="tesseract",
        help="explicit text metric source; no automatic fallback is performed",
    )
    parser.add_argument("--tesseract-command", type=Path, help="explicit Tesseract executable")
    parser.add_argument("--tessdata-dir", type=Path, help="directory containing requested traineddata")
    parser.add_argument("--ocr-psm", type=int, default=6, choices=range(0, 14))
    parser.add_argument("--ocr-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON report path")
    return parser


def _safe_error_message(error: Exception) -> str:
    message = str(error)
    for name, value in os.environ.items():
        if value and ("KEY" in name.upper() or "TOKEN" in name.upper()):
            message = message.replace(value, "[REDACTED]")
    message = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", message)
    return message[:1000]


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main(_parser().parse_args())))
    except Exception as error:  # noqa: BLE001 - manual harness emits a stable, secret-free failure
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "message": _safe_error_message(error),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
