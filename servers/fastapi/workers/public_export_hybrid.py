"""Authored-hybrid assembly executed inside the isolated export worker.

The public export lane may deliver an editable deck, and the isolated export
worker is the only process allowed to run Chrome or an OOXML assembler for it
(decision ``public-export.hybrid-in-isolated-lane``).  This module owns the
child-process boundary to the Node assembler: it stages digest-verified inputs
into the worker's own scratch, launches the configured command inside the same
supervised job object, and reports a bounded evidence summary.

Assembly failure preserves its reason; the controller must not label the
fidelity raster deck as a successful editable export.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from services.public_export_hybrid_evidence import (
    bounded_hybrid_quality_summary, bounded_hybrid_timing, bounded_hybrid_editability,
)
from services.authored_canvas import AuthoredCanvasMetadata


HYBRID_JOB_SCHEMA = "public-hybrid-export-job/v1"
HYBRID_RESULT_SCHEMA = "public-hybrid-export-result/v1"
HYBRID_COMMAND_ENV = "PUBLIC_EXPORT_HYBRID_COMMAND_JSON"
HYBRID_FONT_ALLOWLIST_ENV = "PUBLIC_EXPORT_FONT_ALLOWLIST_DIR"
MAX_HYBRID_RESULT_BYTES = 256 * 1024
MAX_HYBRID_JOB_BYTES = 8 * 1024 * 1024
#: Reserve bounds for assembly, readback and storage. A fixed four-minute
#: reserve starved later slides in a ten-minute export even when packaging fit
#: comfortably inside the remaining time. Keep a proportional reserve without
#: extending the operation's absolute deadline.
ASSEMBLY_RESERVE_SECONDS = 240
MIN_ASSEMBLY_RESERVE_SECONDS = 90
_CHILD_ENV_ALLOWLIST = (
    "COMSPEC",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
    "AUTHORED_HYBRID_CHROME_PATH",
    "CHROME_PATH",
    "CHROMIUM_PATH",
    "PUPPETEER_EXECUTABLE_PATH",
)


@dataclass(frozen=True, slots=True)
class HybridAssemblyOutcome:
    """Either an assembled hybrid package, or the reason there is none."""

    output_path: pathlib.Path | None
    evidence: dict[str, object]
    fallback_reason: str | None


def hybrid_assembler_command(
    environment: Mapping[str, str] | None = None,
) -> list[str] | None:
    """Return the configured Node assembler argv, or ``None`` when unset."""

    incoming = os.environ if environment is None else environment
    configured = incoming.get(HYBRID_COMMAND_ENV, "").strip()
    if not configured:
        return None
    try:
        command = json.loads(configured)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item or "\x00" in item for item in command)
        or not pathlib.PurePath(command[0]).is_absolute()
    ):
        return None
    return [str(item) for item in command]


def _child_environment(environment: Mapping[str, str]) -> dict[str, str]:
    child = {
        name: environment[name]
        for name in _CHILD_ENV_ALLOWLIST
        if environment.get(name, "").strip()
    }
    # The isolated worker is itself the attested renderer boundary, exactly as
    # the render role is granted AUTHORED_RENDERER_WORKER. The assembler is
    # never allowed to self-authorize this from its own file.
    child["AUTHORED_VNEXT_RENDERER_ENABLED"] = "true"
    child["AUTHORED_RENDERER_KILL_SWITCH"] = "false"
    child["DISABLE_GOOGLE_FONTS_COLLECTION"] = "true"
    child["NODE_ENV"] = "development"
    if (
        environment.get("PUBLIC_WORKER_OS_ISOLATION")
        == "windows-job-object-local-development"
        and environment.get("PUBLIC_WORKER_ROLE") == "export"
    ):
        child["AUTHORED_HYBRID_SUPERVISED_WINDOWS"] = "1"
    return child


def _bounded_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) > MAX_HYBRID_RESULT_BYTES:
        return {"truncated": True}
    return dict(value)


async def assemble_hybrid_pptx(
    *,
    base_pptx: pathlib.Path,
    html_paths: Sequence[pathlib.Path],
    scratch: pathlib.Path,
    font_embedding: bool,
    budget_seconds: float,
    concurrency: int = 2,
    semantic_slides: Sequence[Mapping[str, object]] = (),
    export_attempt_id: str | None = None,
    renderer_source_digest: str | None = None,
    canvas: AuthoredCanvasMetadata | None = None,
    environment: Mapping[str, str] | None = None,
) -> HybridAssemblyOutcome:
    """Run the configured assembler; return the reason when it cannot deliver."""

    incoming = dict(os.environ if environment is None else environment)
    command = hybrid_assembler_command(incoming)
    if command is None:
        return HybridAssemblyOutcome(None, {}, "hybrid_assembler_not_configured")
    assembly_reserve = min(
        ASSEMBLY_RESERVE_SECONDS,
        max(MIN_ASSEMBLY_RESERVE_SECONDS, budget_seconds * 0.20),
    )
    if budget_seconds <= assembly_reserve + 30:
        return HybridAssemblyOutcome(None, {}, "hybrid_assembler_budget_exhausted")
    output_path = scratch / "hybrid.pptx"
    job_scratch = scratch / "hybrid-scratch"
    job_scratch.mkdir(parents=True, exist_ok=True)
    font_directory = incoming.get(HYBRID_FONT_ALLOWLIST_ENV, "").strip()
    job = {
        "schemaVersion": HYBRID_JOB_SCHEMA,
        "basePptxPath": str(base_pptx),
        "outputPath": str(output_path),
        "scratchDirectory": str(job_scratch),
        "fontEmbedding": bool(font_embedding),
        "concurrency": concurrency,
        # The per-slide pass budget stops well before the worker deadline. The
        # remainder pays for OOXML assembly, readback and the CAS write, so a
        # slow deck degrades its last slides to raster instead of expiring the
        # whole job and losing the export.
        "budgetMillis": int(
            max(30_000, (budget_seconds - assembly_reserve) * 1000)
        ),
        "slides": [
            {"slideIndex": index, "htmlPath": str(path)}
            for index, path in enumerate(html_paths)
        ],
        **({"fontAllowlistDirectory": font_directory} if font_directory else {}),
    }
    if semantic_slides:
        # The declared graph names which containers may become native groups.
        # It travels with the identity it was sealed against, or not at all.
        if (
            len(semantic_slides) != len(html_paths)
            or not export_attempt_id
            or not renderer_source_digest
        ):
            return HybridAssemblyOutcome(
                None, {}, "hybrid_assembler_semantic_binding_invalid"
            )
        job["semanticSlides"] = [dict(slide) for slide in semantic_slides]
        job["exportAttemptId"] = export_attempt_id
        job["rendererSourceDigest"] = renderer_source_digest
    if canvas is not None:
        job["canvas"] = canvas.model_dump(mode="json", by_alias=True)
    payload = json.dumps(job, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_HYBRID_JOB_BYTES:
        return HybridAssemblyOutcome(None, {}, "hybrid_job_wire_limit")
    assembler_started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(scratch),
            env=_child_environment(incoming),
        )
    except OSError:
        return HybridAssemblyOutcome(None, {}, "hybrid_assembler_launch_failed")
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(payload), timeout=budget_seconds
        )
    except (asyncio.TimeoutError, TimeoutError):
        process.kill()
        await process.wait()
        return HybridAssemblyOutcome(None, {}, "hybrid_assembler_timeout")
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if stderr:
        # Assembler diagnostics belong in the worker's captured stderr tail, not
        # in the durable receipt.
        os.write(2, stderr[-4096:])
    try:
        result = json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError):
        return HybridAssemblyOutcome(None, {}, "hybrid_assembler_result_invalid")
    if (
        not isinstance(result, Mapping)
        or result.get("schemaVersion") != HYBRID_RESULT_SCHEMA
    ):
        return HybridAssemblyOutcome(None, {}, "hybrid_assembler_result_invalid")
    if result.get("ok") is not True or process.returncode != 0:
        reason = result.get("errorCode")
        return HybridAssemblyOutcome(
            None,
            # A failed assembly still owes an account of itself; the caller
            # records it beside the fallback reason.
            {"quality": bounded_hybrid_quality_summary(result.get("quality")),
             "timing": bounded_hybrid_timing(result.get("timing")),
             "editability": bounded_hybrid_editability(result.get("editability")),
             "assemblerProcessMillis": round((time.monotonic() - assembler_started) * 1000)},
            str(reason)[:160] if isinstance(reason, str) and reason else "hybrid_assembler_failed",
        )
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        return HybridAssemblyOutcome(None, {}, "hybrid_assembler_output_missing")
    evidence = {
        "timing": bounded_hybrid_timing(result.get("timing")),
        "editability": bounded_hybrid_editability(result.get("editability")),
        "assemblerProcessMillis": round((time.monotonic() - assembler_started) * 1000),
        "quality": bounded_hybrid_quality_summary(result.get("quality")),
        "structural": _bounded_evidence(result.get("structural")),
        "font_embedding": _bounded_evidence(result.get("fontEmbedding")),
    }
    return HybridAssemblyOutcome(output_path, evidence, None)


__all__ = [
    "HYBRID_COMMAND_ENV",
    "HYBRID_FONT_ALLOWLIST_ENV",
    "HybridAssemblyOutcome",
    "assemble_hybrid_pptx",
    "hybrid_assembler_command",
]
