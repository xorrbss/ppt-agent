"""Bounded, privacy-safe Tesseract OCR quality checks for authored slides.

The public functions intentionally return text only to in-process callers.  Their
``to_dict`` methods contain a digest, never OCR text, so they are safe for API
responses and telemetry.  Successful OCR is LRU-cached by image fingerprint;
failures and timeouts are deliberately never cached.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TIMEOUT_SECONDS = 60.0
MAX_CONCURRENT_OCR = 2
# A thread semaphore, rather than an asyncio semaphore, is deliberately used so
# callers/tests may safely use independent event loops in one process.
_WORKER_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_OCR)
_CLASSES = ("korean", "english", "digits", "symbols")


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


@dataclass
class AuthoredOcrCache:
    """Small process-local cache; only successful captures are retained."""

    max_entries: int = 64
    entries: OrderedDict[str, tuple[str, float]] = field(default_factory=OrderedDict)
    requests: int = 0
    calls: int = 0  # actual image-OCR CLI executions (never cache hits)
    cache_hits: int = 0
    runtimes: dict[str, OcrRuntime] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def get(self, key: str) -> tuple[str, float] | None:
        with self.lock:
            value = self.entries.get(key)
            if value is not None:
                self.entries.move_to_end(key)
                self.cache_hits += 1
            return value

    def put(self, key: str, value: tuple[str, float]) -> None:
        with self.lock:
            self.entries[key] = value
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)


DEFAULT_CACHE = AuthoredOcrCache()


@dataclass(frozen=True)
class OcrQualityResult:
    available: bool
    status: str
    failure_reason: str | None
    text: str | None
    text_digest: str | None
    metrics: dict[str, Any]
    calls: int
    cache_hits: int
    elapsed_ms: float
    returncode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "text_digest": self.text_digest,
            "metrics": self.metrics,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "elapsed_ms": self.elapsed_ms,
            "returncode": self.returncode,
        }


@dataclass(frozen=True)
class OcrComparison:
    accepted: bool
    reason: str
    strict_improvement: bool
    no_regression: bool
    resolved: dict[str, float]
    remaining: dict[str, float]
    regressions: dict[str, float]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "strict_improvement": self.strict_improvement,
            "no_regression": self.no_regression,
            "resolved": self.resolved,
            "remaining": self.remaining,
            "regressions": self.regressions,
            "metrics": self.metrics,
        }


def normalize_ocr_text(value: str) -> str:
    value = (
        unicodedata.normalize("NFKC", value)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
    )
    return "\n".join(
        re.sub(r"[\t \u2000-\u200a]+", " ", line).strip() for line in value.split("\n")
    ).strip("\n")


def _category(char: str) -> str | None:
    code = ord(char)
    if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return "korean"
    if char.isascii() and char.isalpha():
        return "english"
    if char.isdigit():
        return "digits"
    return "symbols" if not char.isspace() else None


def _alignment(source: str, candidate: str) -> dict[str, Any]:
    """Levenshtein traceback. Newline matches are aligned as tokens, not indexes."""
    # Slide OCR is normally short. Refuse pathological inputs rather than making a
    # request consume unbounded RAM/CPU in the traceback matrix.
    if max(len(source), len(candidate)) > 3000:
        return {"available": False, "failure_reason": "comparison_text_too_large"}
    rows, cols = len(source) + 1, len(candidate) + 1
    matrix = [list(range(cols))] + [[row] + [0] * (cols - 1) for row in range(1, rows)]
    for row in range(1, rows):
        for col in range(1, cols):
            matrix[row][col] = min(
                matrix[row - 1][col] + 1,
                matrix[row][col - 1] + 1,
                matrix[row - 1][col - 1] + (source[row - 1] != candidate[col - 1]),
            )
    deletion = insertion = substitution = matches = 0
    missing_breaks = extra_breaks = matched_breaks = 0
    totals = {kind: 0 for kind in _CLASSES}
    matched = dict(totals)
    missing = dict(totals)
    extra = dict(totals)
    for char in source:
        kind = _category(char)
        if kind:
            totals[kind] += 1
    row, col = len(source), len(candidate)
    while row or col:
        if (
            row
            and col
            and source[row - 1] == candidate[col - 1]
            and matrix[row][col] == matrix[row - 1][col - 1]
        ):
            char = source[row - 1]
            kind = _category(char)
            if kind:
                matched[kind] += 1
            if char == "\n":
                matched_breaks += 1
            matches += 1
            row -= 1
            col -= 1
        elif row and col and matrix[row][col] == matrix[row - 1][col - 1] + 1:
            left, right = source[row - 1], candidate[col - 1]
            kind = _category(left)
            if kind:
                missing[kind] += 1
            kind = _category(right)
            if kind:
                extra[kind] += 1
            missing_breaks += left == "\n"
            extra_breaks += right == "\n"
            substitution += 1
            row -= 1
            col -= 1
        elif row and matrix[row][col] == matrix[row - 1][col] + 1:
            char = source[row - 1]
            kind = _category(char)
            if kind:
                missing[kind] += 1
            missing_breaks += char == "\n"
            deletion += 1
            row -= 1
        else:
            char = candidate[col - 1]
            kind = _category(char)
            if kind:
                extra[kind] += 1
            extra_breaks += char == "\n"
            insertion += 1
            col -= 1
    classes = {
        kind: {
            "source_count": totals[kind],
            "candidate_count": sum(1 for c in candidate if _category(c) == kind),
            "exact_matches": matched[kind],
            "missing": missing[kind],
            "additional": extra[kind],
            "missing_or_substituted": totals[kind] - matched[kind],
            "preservation_rate": matched[kind] / totals[kind] if totals[kind] else None,
        }
        for kind in _CLASSES
    }
    return {
        "available": True,
        "edit_distance": matrix[-1][-1],
        "matches": matches,
        "substitutions": substitution,
        "deletions": deletion,
        "insertions": insertion,
        "character_classes": classes,
        "matched_line_breaks": matched_breaks,
        "missing_line_breaks": missing_breaks,
        "extra_line_breaks": extra_breaks,
    }


def comparison_metrics(source: str, candidate: str) -> dict[str, Any]:
    alignment = _alignment(source, candidate)
    if not alignment["available"]:
        return {
            "source_characters": len(source),
            "candidate_characters": len(candidate),
            **alignment,
        }
    source_breaks, candidate_breaks = source.count("\n"), candidate.count("\n")
    precision = (
        alignment["matched_line_breaks"] / candidate_breaks if candidate_breaks else 1.0
    )
    recall = alignment["matched_line_breaks"] / source_breaks if source_breaks else 1.0
    return {
        "source_characters": len(source),
        "candidate_characters": len(candidate),
        **alignment,
        "cer": alignment["edit_distance"] / max(1, len(source)),
        "missing_rate": alignment["deletions"] / max(1, len(source)),
        "source_character_error_rate": (
            alignment["deletions"] + alignment["substitutions"]
        )
        / max(1, len(source)),
        "line_break_exact": not alignment["missing_line_breaks"]
        and not alignment["extra_line_breaks"],
        "line_break_f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
        "source_line_breaks": source_breaks,
        "candidate_line_breaks": candidate_breaks,
    }


def expected_text_from_browser_metrics(metrics: dict[str, Any]) -> str:
    """Build visible expected text from browser metrics, preferring leaf QIDs.

    This is intentionally based only on rendered browser text; outline/source
    content must not be used as an OCR expectation.  The function accepts the
    compact metrics formats used by capture clients (``elements`` list or QID
    mapping) and skips hidden/aria-hidden/non-visible elements.
    """
    raw = metrics.get("elements", metrics.get("qid_elements", metrics))
    elements = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
    visible: list[dict[str, Any]] = []
    qids: set[str] = set()
    for item in elements:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or item.get("data_qid") or "")
        style = str(item.get("style") or "").lower()
        if (
            item.get("hidden")
            or item.get("aria_hidden") in (True, "true")
            or item.get("visible") is False
            or "display:none" in style
            or "visibility:hidden" in style
        ):
            continue
        text = item.get(
            "rendered_text", item.get("inner_text", item.get("text_content", ""))
        )
        if qid and isinstance(text, str) and text.strip():
            qids.add(qid)
            visible.append({"qid": qid, "text": text, "item": item})
    output: list[str] = []
    observed_parents = {
        str(entry["item"].get("parent_qid"))
        for entry in visible
        if entry["item"].get("parent_qid")
    }
    for entry in visible:
        item, qid = entry["item"], entry["qid"]
        children = item.get("child_qids") or item.get("children") or []
        child_qids = {
            str(child.get("qid")) if isinstance(child, dict) else str(child)
            for child in children
        }
        # A parent with observed child QIDs would duplicate their visible text.
        if child_qids & qids or qid in observed_parents:
            continue
        if entry["text"].strip() not in output:
            output.append(entry["text"].strip())
    return "\n".join(output)


def find_tesseract(explicit_command: str | Path | None = None) -> tuple[str, ...]:
    candidates: list[str] = []
    # An explicit command is a deterministic operator choice, not a hint: never
    # silently fall back to PATH when it is missing.
    if explicit_command:
        candidate = str(explicit_command)
        return (
            (candidate,) if Path(candidate).is_file() or shutil.which(candidate) else ()
        )
    if os.environ.get("TESSERACT_CMD"):
        candidates.append(os.environ["TESSERACT_CMD"])
    discovered = shutil.which("tesseract")
    if discovered:
        candidates.append(discovered)
    if os.name == "nt":
        candidates += [
            r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
            r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
        ]
    for candidate in candidates:
        if Path(candidate).is_file() or shutil.which(candidate):
            return (candidate,)
    return ()


def discover_tessdata(explicit: str | Path | None = None) -> Path | None:
    roots = [
        explicit,
        os.environ.get("AUTHORED_OCR_TESSDATA_PATH"),
        os.environ.get("TESSDATA_PREFIX"),
        os.environ.get("PRESENTON_APP_ROOT")
        and str(Path(os.environ["PRESENTON_APP_ROOT"]) / "tessdata"),
        str(Path(__file__).resolve().parents[3] / "tessdata"),
        str(Path.cwd() / "tessdata"),
        "/app/tessdata",
    ]
    for root in roots:
        if root and Path(root).is_dir():
            return Path(root).resolve()
    return None


def run_process(command: tuple[str, ...], *, timeout_seconds: float) -> ProcessResult:
    timeout = min(MAX_TIMEOUT_SECONDS, max(0.1, float(timeout_seconds)))
    started = time.monotonic()
    kwargs: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        return ProcessResult(
            "start_failed",
            None,
            b"",
            b"",
            time.monotonic() - started,
            f"process_start_failed:{type(exc).__name__}",
        )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        elapsed = time.monotonic() - started
        if process.returncode:
            return ProcessResult(
                "failed",
                process.returncode,
                stdout,
                stderr,
                elapsed,
                f"exit_code_{process.returncode}",
            )
        return ProcessResult("success", process.returncode, stdout, stderr, elapsed)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()  # always reap before returning
        return ProcessResult(
            "timeout",
            process.returncode,
            stdout,
            stderr,
            time.monotonic() - started,
            f"timeout_after_{timeout:g}_seconds",
        )
    # Convert any ordinary Popen/pipe failure into a structured result, but
    # deliberately let cancellation and other BaseException instances escape.
    except Exception as exc:  # noqa: BLE001
        if process.poll() is None:
            process.kill()
        stdout, stderr = process.communicate()
        return ProcessResult(
            "failed",
            process.returncode,
            stdout,
            stderr,
            time.monotonic() - started,
            f"process_error:{type(exc).__name__}",
        )


def inspect_tesseract(
    *,
    explicit_command: str | Path | None,
    tessdata_dir: str | Path | None,
    language: str,
    timeout_seconds: float,
    process_runner=run_process,
) -> OcrRuntime:
    started = time.monotonic()
    command = find_tesseract(explicit_command)
    requested = tuple(x for x in language.split("+") if x)
    data = discover_tessdata(tessdata_dir)
    if not command:
        return OcrRuntime(
            "unavailable",
            (),
            None,
            requested,
            (),
            str(data) if data else None,
            time.monotonic() - started,
            "tesseract_executable_not_found",
        )
    version = process_runner((*command, "--version"), timeout_seconds=timeout_seconds)
    if version.status != "success":
        return OcrRuntime(
            "unavailable",
            command,
            None,
            requested,
            (),
            str(data) if data else None,
            time.monotonic() - started,
            version.failure_reason,
        )
    listing = process_runner(
        (*command, *(("--tessdata-dir", str(data)) if data else ()), "--list-langs"),
        timeout_seconds=timeout_seconds,
    )
    available = (
        tuple(
            line.strip()
            for line in listing.stdout.decode("utf-8", "replace").splitlines()
            if line.strip() and not line.lower().startswith("list of ")
        )
        if listing.status == "success"
        else ()
    )
    missing = tuple(lang for lang in requested if lang not in available)
    return OcrRuntime(
        "language_missing" if missing else "ready",
        command,
        version.stdout.decode("utf-8", "replace").splitlines()[0]
        if version.stdout
        else None,
        requested,
        available,
        str(data) if data else None,
        time.monotonic() - started,
        f"missing_languages:{','.join(missing)}" if missing else None,
    )


def _ocr_bytes(
    image: bytes,
    *,
    language: str,
    cache: AuthoredOcrCache,
    timeout_seconds: float,
    command: str | Path | None,
    tessdata_dir: str | Path | None,
    psm: int,
) -> OcrQualityResult:
    with _WORKER_SEMAPHORE:
        started = time.monotonic()
        with cache.lock:
            cache.requests += 1
        fingerprint = sha256(image).hexdigest()
        data = discover_tessdata(tessdata_dir)
        selected_command = str(
            command
            or os.environ.get("TESSERACT_CMD")
            or shutil.which("tesseract")
            or ""
        )
        # The absolute data path and command make a replacement/binary switch miss
        # cache; the traineddata mtime catches in-place language-data upgrades.
        data_stamp = (
            max(
                (entry.stat().st_mtime_ns for entry in data.glob("*.traineddata")),
                default=0,
            )
            if data
            else 0
        )
        key = f"{fingerprint}:{language}:{psm}:{selected_command}:{data}:{data_stamp}"
        hit = cache.get(key)
        if hit:
            text, _previous_ms = hit
            return OcrQualityResult(
                True,
                "success",
                None,
                text,
                sha256(text.encode()).hexdigest(),
                {},
                cache.calls,
                cache.cache_hits,
                0.0,
            )
        runtime_key = f"{selected_command}:{language}:{data}:{data_stamp}"
        with cache.lock:
            runtime = cache.runtimes.get(runtime_key)
        if runtime is None:
            runtime = inspect_tesseract(
                explicit_command=command,
                tessdata_dir=tessdata_dir,
                language=language,
                timeout_seconds=timeout_seconds,
            )
            # Availability can change while this process is alive (for example a
            # language pack is mounted after startup), so cache only confirmed
            # ready runtimes. Failures are intentionally retried on next request.
            if runtime.status == "ready":
                with cache.lock:
                    cache.runtimes[runtime_key] = runtime
        if runtime.status != "ready":
            return OcrQualityResult(
                False,
                runtime.status,
                runtime.failure_reason,
                None,
                None,
                {},
                cache.calls,
                cache.cache_hits,
                (time.monotonic() - started) * 1000,
            )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(image)
            image_path = handle.name
        try:
            args = (
                *runtime.command,
                image_path,
                "stdout",
                "-l",
                language,
                "--psm",
                str(psm),
                *(
                    ("--tessdata-dir", runtime.tessdata_dir)
                    if runtime.tessdata_dir
                    else ()
                ),
            )
            with cache.lock:
                cache.calls += 1
            process = run_process(args, timeout_seconds=timeout_seconds)
        finally:
            Path(image_path).unlink(missing_ok=True)
        if process.status != "success":
            return OcrQualityResult(
                False,
                process.status,
                process.failure_reason,
                None,
                None,
                {},
                cache.calls,
                cache.cache_hits,
                (time.monotonic() - started) * 1000,
                process.returncode,
            )
        text = process.stdout.decode("utf-8", "replace").strip()
        cache.put(key, (text, (time.monotonic() - started) * 1000))
        return OcrQualityResult(
            True,
            "success",
            None,
            text,
            sha256(text.encode()).hexdigest(),
            {},
            cache.calls,
            cache.cache_hits,
            (time.monotonic() - started) * 1000,
            process.returncode,
        )


async def inspect_authored_ocr(
    image: bytes,
    *,
    expected_text: str | None = None,
    language: str = "kor+eng",
    cache: AuthoredOcrCache | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    command: str | Path | None = None,
    tessdata_dir: str | Path | None = None,
    psm: int = 6,
) -> OcrQualityResult:
    """OCR one image under a global two-job bound. Cancellation waits for reaping."""
    worker = asyncio.create_task(
        asyncio.to_thread(
            _ocr_bytes,
            image,
            language=language,
            cache=cache or DEFAULT_CACHE,
            timeout_seconds=timeout_seconds,
            command=command,
            tessdata_dir=tessdata_dir,
            psm=psm,
        )
    )
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Do not orphan the CLI process: worker completion entails communicate()
        # after kill/timeout.  Preserve cancellation only after that cleanup.
        await asyncio.shield(worker)
        raise
    if expected_text is not None and result.text is not None:
        metrics = comparison_metrics(
            normalize_ocr_text(expected_text), normalize_ocr_text(result.text)
        )
        return OcrQualityResult(**{**result.__dict__, "metrics": metrics})
    return result


def compare_authored_ocr(
    baseline: OcrQualityResult | str,
    candidate: OcrQualityResult | str,
    *,
    mode: str = "baseline",
    confidence_tolerance: float = 0.02,
    metric_tolerance: float = 0.01,
) -> OcrComparison:
    left = baseline.text if isinstance(baseline, OcrQualityResult) else baseline
    right = candidate.text if isinstance(candidate, OcrQualityResult) else candidate
    if left is None or right is None:
        return OcrComparison(False, "ocr_unavailable", False, False, {}, {}, {}, {})
    metrics = comparison_metrics(normalize_ocr_text(left), normalize_ocr_text(right))
    if not metrics.get("available", True):
        return OcrComparison(
            False, metrics["failure_reason"], False, False, {}, {}, {}, metrics
        )
    # Both legacy modes use the same conservative two-way OCR threshold.  The
    # distinction is provenance for callers; expected-relative improvement is
    # evaluated by ``compare_expected_candidate`` below.
    _ = mode
    cer = float(metrics["cer"])
    threshold = max(0.0, metric_tolerance + confidence_tolerance)
    accepted = cer <= threshold
    classes = metrics["character_classes"]
    remaining = {
        "text": int(metrics["deletions"] + metrics["substitutions"]),
        "numbers": int(classes["digits"]["missing_or_substituted"]),
        "symbols": int(classes["symbols"]["missing_or_substituted"]),
        "korean": int(classes["korean"]["missing_or_substituted"]),
        "english": int(classes["english"]["missing_or_substituted"]),
    }
    preserved = cer == 0.0
    return OcrComparison(
        accepted,
        "content_preserved"
        if preserved
        else ("within_tolerance" if accepted else "text_regression"),
        False,
        accepted,
        {},
        remaining,
        {} if accepted else remaining,
        metrics,
    )


def ocr_confidence(
    metrics: dict[str, Any],
    *,
    min_characters: int = 12,
    max_cer: float = 0.08,
    max_missing_rate: float = 0.03,
) -> dict[str, Any]:
    """Conservative decision helper for callers that must avoid OCR false alarms."""
    chars = int(metrics.get("source_characters", 0))
    sufficient = metrics.get("available", True) and chars >= min_characters
    pass_thresholds = (
        float(metrics.get("cer", 1.0)) <= max_cer
        and float(metrics.get("missing_rate", 1.0)) <= max_missing_rate
    )
    return {
        "confidence": "high" if sufficient else "insufficient",
        "needs_attention": bool(sufficient and not pass_thresholds),
        "thresholds": {
            "min_characters": min_characters,
            "max_cer": max_cer,
            "max_missing_rate": max_missing_rate,
        },
    }


def compare_expected_candidate(
    expected: OcrQualityResult | str,
    baseline: OcrQualityResult | str,
    candidate: OcrQualityResult | str,
    *,
    confidence_tolerance: float = 0.02,
    metric_tolerance: float = 0.01,
) -> OcrComparison:
    """Compare candidate against expected text and prove no OCR-content regression.

    This three-way form is for exact reconstruction: a candidate is an OCR
    improvement only if its expected-relative CER strictly drops. Identical text
    is correctly reported as ``content_preserved`` (not an improvement), leaving
    visual/VQA scoring to decide whether the whole revision improved.
    """
    expected_text = (
        expected.text if isinstance(expected, OcrQualityResult) else expected
    )
    baseline_text = (
        baseline.text if isinstance(baseline, OcrQualityResult) else baseline
    )
    candidate_text = (
        candidate.text if isinstance(candidate, OcrQualityResult) else candidate
    )
    if expected_text is None or baseline_text is None or candidate_text is None:
        return OcrComparison(False, "ocr_unavailable", False, False, {}, {}, {}, {})
    base = comparison_metrics(
        normalize_ocr_text(expected_text), normalize_ocr_text(baseline_text)
    )
    trial = comparison_metrics(
        normalize_ocr_text(expected_text), normalize_ocr_text(candidate_text)
    )
    if not base.get("available", True) or not trial.get("available", True):
        return OcrComparison(
            False,
            "comparison_unavailable",
            False,
            False,
            {},
            {},
            {},
            {"baseline": base, "candidate": trial},
        )
    base_cer, candidate_cer = float(base["cer"]), float(trial["cer"])
    no_regression = candidate_cer <= base_cer + metric_tolerance
    strict = candidate_cer < base_cer - confidence_tolerance

    def omissions(metric: dict[str, Any]) -> dict[str, int]:
        classes = metric["character_classes"]
        return {
            "text": int(metric["deletions"] + metric["substitutions"]),
            "numbers": int(classes["digits"]["missing_or_substituted"]),
            "symbols": int(classes["symbols"]["missing_or_substituted"]),
            "korean": int(classes["korean"]["missing_or_substituted"]),
            "english": int(classes["english"]["missing_or_substituted"]),
        }

    before, after = omissions(base), omissions(trial)
    resolved = {key: max(0, before[key] - after[key]) for key in before}
    remaining = after
    regressions = {key: max(0, after[key] - before[key]) for key in before}
    reason = (
        "ocr_improved"
        if strict
        else ("content_preserved" if no_regression else "text_regression")
    )
    return OcrComparison(
        no_regression,
        reason,
        strict,
        no_regression,
        resolved,
        remaining,
        regressions,
        {"baseline": base, "candidate": trial},
    )


def compare_expected_candidate_order_insensitive(
    expected: str,
    baseline: OcrQualityResult | str,
    candidate: OcrQualityResult | str,
    *,
    source_text_preserved: bool,
    metric_tolerance: float = 0.025,
    precision_tolerance: float = 0.04,
    min_character_recall: float = 0.75,
    min_token_recall: float = 0.65,
) -> OcrComparison:
    """Prove OCR non-regression when geometry changes visual reading order.

    Sequence CER is intentionally the primary gate.  This fallback is only
    available when the caller has independently proved that the semantic source
    text is byte-for-byte unchanged.  It then compares character and word-token
    multisets, so translations may change reading order but cannot hide lost
    content.  Recall and precision must both remain within a small tolerance and
    absolute coverage floors keep weak OCR from becoming conclusive.
    """

    baseline_text = baseline.text if isinstance(baseline, OcrQualityResult) else baseline
    candidate_text = candidate.text if isinstance(candidate, OcrQualityResult) else candidate
    if not source_text_preserved:
        return OcrComparison(
            False,
            "source_text_not_preserved",
            False,
            False,
            {},
            {},
            {"source_text": 1.0},
            {"source_text_preserved": False},
        )
    if baseline_text is None or candidate_text is None:
        return OcrComparison(False, "ocr_unavailable", False, False, {}, {}, {}, {})

    def character_bag(text: str) -> Counter[str]:
        return Counter(
            character
            for character in normalize_ocr_text(text).lower()
            if not character.isspace()
        )

    def token_bag(text: str) -> Counter[str]:
        return Counter(
            re.findall(r"[0-9a-z\uac00-\ud7a3]{2,}", normalize_ocr_text(text).lower())
        )

    def coverage(reference: Counter[str], observed: Counter[str]) -> dict[str, float]:
        matched = sum((reference & observed).values())
        reference_count = sum(reference.values())
        observed_count = sum(observed.values())
        return {
            "recall": matched / max(1, reference_count),
            "precision": matched / max(1, observed_count),
            "matched": float(matched),
            "expected": float(reference_count),
            "observed": float(observed_count),
        }

    expected_characters = character_bag(expected)
    expected_tokens = token_bag(expected)
    baseline_character = coverage(expected_characters, character_bag(baseline_text))
    candidate_character = coverage(expected_characters, character_bag(candidate_text))
    baseline_token = coverage(expected_tokens, token_bag(baseline_text))
    candidate_token = coverage(expected_tokens, token_bag(candidate_text))
    metrics = {
        "source_text_preserved": True,
        "tolerance": metric_tolerance,
        "precision_tolerance": precision_tolerance,
        "minimums": {
            "character_recall": min_character_recall,
            "token_recall": min_token_recall,
        },
        "baseline": {"character": baseline_character, "token": baseline_token},
        "candidate": {"character": candidate_character, "token": candidate_token},
    }
    dimensions = (
        (
            "character_recall",
            baseline_character["recall"],
            candidate_character["recall"],
            metric_tolerance,
        ),
        (
            "character_precision",
            baseline_character["precision"],
            candidate_character["precision"],
            precision_tolerance,
        ),
        (
            "token_recall",
            baseline_token["recall"],
            candidate_token["recall"],
            metric_tolerance,
        ),
        (
            "token_precision",
            baseline_token["precision"],
            candidate_token["precision"],
            precision_tolerance,
        ),
    )
    regressions = {
        name: before - after
        for name, before, after, tolerance in dimensions
        if after + tolerance < before
    }
    floors_met = (
        candidate_character["recall"] >= min_character_recall
        and candidate_token["recall"] >= min_token_recall
    )
    accepted = floors_met and not regressions
    return OcrComparison(
        accepted,
        "order_insensitive_content_preserved"
        if accepted
        else ("insufficient_ocr_coverage" if not floors_met else "text_regression"),
        False,
        accepted,
        {},
        {
            "character_recall": candidate_character["recall"],
            "token_recall": candidate_token["recall"],
        },
        regressions,
        metrics,
    )
