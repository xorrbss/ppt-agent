"""Deterministic, offline design-signal analysis for PPTX and PDF artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
MAX_PAGES = 500
SUPPORTED_SUFFIXES = {".pdf", ".pptx"}
ANALYSIS_SCHEMA_VERSION = 1


class ArtifactAnalysisError(ValueError):
    """Raised when an artifact cannot be analyzed safely or meaningfully."""


def _signal(values: list[dict[str, Any]], confidence: str) -> dict[str, Any]:
    """Shared shape for one analyzed design signal (colors, fonts, layouts, …)."""
    return {
        "confidence": confidence if values else "none",
        "status": "observed" if values else "unavailable",
        "values": values,
    }


def _read_artifact(source: Path) -> bytes:
    if not source.exists():
        raise ArtifactAnalysisError(f"{source}: input does not exist")
    if not source.is_file():
        raise ArtifactAnalysisError(f"{source}: input must be a regular file")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ArtifactAnalysisError(f"{source}: cannot inspect input: {exc}") from exc
    if size == 0:
        raise ArtifactAnalysisError(f"{source}: input is empty")
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactAnalysisError(
            f"{source}: input exceeds the {MAX_ARTIFACT_BYTES}-byte safety limit"
        )
    try:
        return source.read_bytes()
    except OSError as exc:
        raise ArtifactAnalysisError(f"{source}: cannot read input: {exc}") from exc


def analyze_artifact(input_path: Path | str) -> dict[str, Any]:
    """Analyze one PPTX or PDF without executing active or linked content."""
    source = Path(input_path).expanduser().resolve()
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ArtifactAnalysisError(f"{source}: expected one of: {supported}")
    raw = _read_artifact(source)

    if suffix == ".pptx":
        from utils.artifact_style_pptx import analyze_pptx

        details = analyze_pptx(source, raw)
        artifact_format = "pptx"
    else:
        from utils.artifact_style_pdf import analyze_pdf

        details = analyze_pdf(source, raw)
        artifact_format = "pdf"

    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "source": {
            "filename": source.name,
            "format": artifact_format,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        },
        **details,
    }


def analysis_json_bytes(analysis: Mapping[str, Any]) -> bytes:
    """Serialize an analysis report byte-for-byte deterministically."""
    text = json.dumps(
        analysis,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    )
    return f"{text}\n".encode("utf-8")
