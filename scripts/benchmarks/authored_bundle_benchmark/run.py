"""Provider-free authored render/checkpoint benchmark.

This harness invokes the product's atomic render-with-metrics API and durable
checkpoint bundle store directly.  It never calls a model/provider, a running
Presenton API, or an existing generation job.  Durability sweeps use an isolated
temporary SQLite database and checkpoint root owned by this benchmark process.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterable
import uuid


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
FASTAPI_ROOT = REPO_ROOT / "servers" / "fastapi"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "authored-performance-20260828" / "evidence.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON evidence path (default: artifacts/authored-performance-20260828/evidence.json)",
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--render-concurrency", type=int, default=4)
    parser.add_argument("--sample-interval-ms", type=int, default=50)
    return parser.parse_args()


def _configure_process(args: argparse.Namespace) -> None:
    if args.timeout_seconds < 1 or args.render_concurrency < 1:
        raise ValueError("timeout and render concurrency must be positive")
    if not 10 <= args.sample_interval_ms <= 1000:
        raise ValueError("sample interval must be between 10 and 1000 ms")
    sys.path.insert(0, str(FASTAPI_ROOT))
    # Pin the local, provider-free development renderer.  These mutations are
    # confined to this benchmark subprocess and cannot affect a running service.
    os.environ["AUTHORED_RENDERER_MODE"] = "isolated"
    os.environ["AUTHORED_RENDERER_PRODUCTION"] = "0"
    os.environ["AUTHORED_RENDERER_PRODUCTION_STRICT"] = "0"
    os.environ["AUTHORED_RENDERER_REQUIRE_EXTERNAL"] = "0"
    os.environ["AUTHORED_RENDERER_LEGACY_ENABLED"] = "0"
    os.environ["AUTHORED_CHROME_REUSE"] = "1"
    os.environ["AUTHORED_RENDER_CONCURRENCY"] = str(args.render_concurrency)
    os.environ["AUTHORED_RENDER_CACHE_SIZE"] = "32"


def _git_head() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() or None


def _chrome_like(name: str) -> bool:
    lowered = name.casefold()
    return any(token in lowered for token in ("chrome", "chromium", "headless_shell", "msedge"))


def _windows_process_snapshot() -> dict[int, tuple[int, str, int]]:
    """Return pid -> (parent pid, executable, RSS) using Win32 APIs only."""

    if os.name != "nt":
        return {}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    ulong_ptr = wintypes.WPARAM

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ulong_ptr),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCountersEx),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    snapshot_handle = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot_handle == invalid_handle:
        return {}
    result: dict[int, tuple[int, str, int]] = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        has_entry = bool(kernel32.Process32FirstW(snapshot_handle, ctypes.byref(entry)))
        while has_entry:
            pid = int(entry.th32ProcessID)
            rss = 0
            process = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
            if process:
                try:
                    counters = ProcessMemoryCountersEx()
                    counters.cb = ctypes.sizeof(counters)
                    if psapi.GetProcessMemoryInfo(
                        process, ctypes.byref(counters), counters.cb
                    ):
                        rss = int(counters.WorkingSetSize)
                finally:
                    kernel32.CloseHandle(process)
            result[pid] = (int(entry.th32ParentProcessID), entry.szExeFile, rss)
            has_entry = bool(kernel32.Process32NextW(snapshot_handle, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot_handle)
    return result


def _procfs_process_snapshot() -> dict[int, tuple[int, str, int]]:
    result: dict[int, tuple[int, str, int]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return result
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text("utf-8", errors="replace").splitlines()
            values = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in status if ":" in line}
            parent = int(values.get("PPid", "0"))
            name = values.get("Name", "")
            rss_kib = int(values.get("VmRSS", "0 kB").split()[0])
            result[int(entry.name)] = (parent, name, rss_kib * 1024)
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            continue
    return result


def _process_tree_sample(root_pid: int) -> tuple[int, int, list[str]]:
    snapshot = _windows_process_snapshot() if os.name == "nt" else _procfs_process_snapshot()
    members = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _, _) in snapshot.items():
            if parent in members and pid not in members:
                members.add(pid)
                changed = True
    rows = [snapshot[pid] for pid in members if pid in snapshot]
    chrome_names = sorted({name for _, name, _ in rows if _chrome_like(name)})
    chrome_count = sum(1 for _, name, _ in rows if _chrome_like(name))
    return sum(rss for _, _, rss in rows), chrome_count, chrome_names


class ProcessTreeSampler:
    def __init__(self, interval_seconds: float):
        self.interval_seconds = interval_seconds
        self.root_pid = os.getpid()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.sample_count = 0
        self.baseline_rss_bytes = 0
        self.peak_rss_bytes = 0
        self.peak_chromium_process_count = 0
        self.chromium_executables: set[str] = set()

    def start(self) -> None:
        rss, chrome_count, names = _process_tree_sample(self.root_pid)
        self.baseline_rss_bytes = rss
        self._record(rss, chrome_count, names)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=5)
        rss, chrome_count, names = _process_tree_sample(self.root_pid)
        self._record(rss, chrome_count, names)
        return {
            "source": "actual_os_process_tree_sampling",
            "rootPid": self.root_pid,
            "sampleIntervalMs": round(self.interval_seconds * 1000, 3),
            "sampleCount": self.sample_count,
            "baselineProcessTreeRssBytes": self.baseline_rss_bytes,
            "peakProcessTreeRssBytes": self.peak_rss_bytes,
            "peakProcessTreeRssDeltaBytes": max(0, self.peak_rss_bytes - self.baseline_rss_bytes),
            "peakChromiumProcessCount": self.peak_chromium_process_count,
            "chromiumExecutables": sorted(self.chromium_executables),
        }

    def _record(self, rss: int, chrome_count: int, names: Iterable[str]) -> None:
        self.sample_count += 1
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        self.peak_chromium_process_count = max(self.peak_chromium_process_count, chrome_count)
        self.chromium_executables.update(names)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._record(*_process_tree_sample(self.root_pid))


def _counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: after.get(key, 0) - before.get(key, 0) for key in sorted(set(before) | set(after))}


def _directory_usage(directory: Path) -> tuple[int, int]:
    files = [path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()]
    return sum(path.stat().st_size for path in files), len(files)


def _fixture_html(deck_name: str, slide_index: int) -> str:
    accent = (slide_index * 37) % 255
    return f"""<!doctype html><html><head><style>
html,body{{margin:0;width:1280px;height:720px;overflow:hidden;background:#f7faff;color:#10233f;font-family:Arial,sans-serif}}
main{{box-sizing:border-box;width:1280px;height:720px;padding:56px 68px}}
h1{{margin:0 0 22px;font-size:42px;line-height:1.15}}
.band{{height:10px;width:100%;background:rgb(20,110,{accent});margin-bottom:30px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}}
.card{{box-sizing:border-box;height:190px;padding:24px;border:1px solid #bfd2ea;border-radius:14px;background:#fff}}
.label{{font-size:18px;font-weight:700}}.value{{margin-top:48px;font-size:34px;font-weight:700;color:#1769aa}}
</style></head><body><main><h1>{deck_name} · Slide {slide_index + 1}</h1><div class="band"></div><div class="grid">
<section class="card"><div class="label">Throughput</div><div class="value">{slide_index + 101}</div></section>
<section class="card"><div class="label">Quality</div><div class="value">99.{slide_index % 10}%</div></section>
<section class="card"><div class="label">Checkpoint</div><div class="value">r0</div></section>
</div></main></body></html>"""


async def _render_and_checkpoint(
    *,
    name: str,
    slide_count: int,
    scratch: Path,
    timeout_seconds: int,
    sample_interval_seconds: float,
) -> dict[str, Any]:
    from services.authored_slide_checkpoint_bundle import AtomicSlideCheckpointStore
    from utils import slide_capture

    htmls = [_fixture_html(name, index) for index in range(slide_count)]
    store = AtomicSlideCheckpointStore(scratch / name / "bundles")
    before = slide_capture.get_render_runtime_counters()
    sampler = ProcessTreeSampler(sample_interval_seconds)
    sampler.start()
    total_started = time.perf_counter()
    render_started = time.perf_counter()
    captures = await asyncio.gather(
        *(slide_capture.render_html_to_png_with_metrics(html, timeout=timeout_seconds) for html in htmls)
    )
    render_elapsed = time.perf_counter() - render_started

    def persist_and_recover() -> tuple[int, int, int]:
        persisted = 0
        recovered = 0
        for index, (html, capture) in enumerate(zip(htmls, captures, strict=True)):
            if capture.browser_metrics is None:
                raise RuntimeError(f"browser metrics missing for slide {index}")
            committed = store.commit(
                attempt_id=name,
                slide_index=index,
                revision=0,
                html=html,
                png=capture.png,
                metrics=capture.browser_metrics,
            )
            persisted += 1
            restored = store.recover(attempt_id=name, slide_index=index)
            if restored.digest != committed.digest or restored.png != capture.png:
                raise RuntimeError(f"checkpoint recovery mismatch for slide {index}")
            recovered += 1
        bytes_written, files_written = _directory_usage(store.root)
        return persisted, recovered, bytes_written, files_written

    checkpoint_started = time.perf_counter()
    persisted, recovered, bundle_bytes, bundle_files = await asyncio.to_thread(persist_and_recover)
    checkpoint_elapsed = time.perf_counter() - checkpoint_started
    total_elapsed = time.perf_counter() - total_started
    process_metrics = sampler.stop()
    after = slide_capture.get_render_runtime_counters()
    counters = _counter_delta(before, after)
    png_bytes = sum(len(capture.png) for capture in captures)
    return {
        "evidenceClass": "actual_measured",
        "name": name,
        "slideCount": slide_count,
        "elapsedSeconds": round(total_elapsed, 6),
        "slidesPerSecond": round(slide_count / total_elapsed, 6),
        "renderElapsedSeconds": round(render_elapsed, 6),
        "renderSlidesPerSecond": round(slide_count / render_elapsed, 6),
        "checkpointElapsedSeconds": round(checkpoint_elapsed, 6),
        "checkpointSlidesPerSecond": round(slide_count / checkpoint_elapsed, 6),
        "pngBytes": png_bytes,
        "checkpointBundleBytes": bundle_bytes,
        "checkpointBundleFiles": bundle_files,
        "browserMetricsCapturedCount": sum(capture.browser_metrics is not None for capture in captures),
        "checkpointPersistedCount": persisted,
        "checkpointRecoveredCount": recovered,
        "metricRecollectionCount": counters.get("metrics_recollection_requests", 0),
        "chromiumProcessLaunchCount": counters.get("chrome_process_launches", 0),
        "runtimeCounterDelta": counters,
        "processTree": process_metrics,
    }


async def _measure_deck(
    *,
    name: str,
    slide_count: int,
    scratch: Path,
    timeout_seconds: int,
    sample_interval_seconds: float,
) -> dict[str, Any]:
    from utils import slide_capture

    slide_capture.reset_render_runtime(clear_caches=True, retire_session=True)
    return await _render_and_checkpoint(
        name=name,
        slide_count=slide_count,
        scratch=scratch,
        timeout_seconds=timeout_seconds,
        sample_interval_seconds=sample_interval_seconds,
    )


async def _measure_concurrent_decks(
    *, scratch: Path, timeout_seconds: int, sample_interval_seconds: float
) -> dict[str, Any]:
    from services.authored_slide_checkpoint_bundle import AtomicSlideCheckpointStore
    from utils import slide_capture

    slide_capture.reset_render_runtime(clear_caches=True, retire_session=True)
    specs = (("concurrent-a", 8), ("concurrent-b", 8))
    before = slide_capture.get_render_runtime_counters()
    sampler = ProcessTreeSampler(sample_interval_seconds)
    sampler.start()
    started = time.perf_counter()

    async def one_deck(name: str, count: int) -> int:
        htmls = [_fixture_html(name, index) for index in range(count)]
        captures = await asyncio.gather(
            *(slide_capture.render_html_to_png_with_metrics(html, timeout=timeout_seconds) for html in htmls)
        )
        store = AtomicSlideCheckpointStore(scratch / name / "bundles")

        def persist() -> int:
            for index, (html, capture) in enumerate(zip(htmls, captures, strict=True)):
                if capture.browser_metrics is None:
                    raise RuntimeError(f"browser metrics missing for {name} slide {index}")
                store.commit(
                    attempt_id=name,
                    slide_index=index,
                    revision=0,
                    html=html,
                    png=capture.png,
                    metrics=capture.browser_metrics,
                )
                store.recover(attempt_id=name, slide_index=index)
            return count

        return await asyncio.to_thread(persist)

    completed = await asyncio.gather(*(one_deck(name, count) for name, count in specs))
    elapsed = time.perf_counter() - started
    process_metrics = sampler.stop()
    counters = _counter_delta(before, slide_capture.get_render_runtime_counters())
    total_slides = sum(completed)
    return {
        "evidenceClass": "actual_measured",
        "deckCount": len(specs),
        "slidesPerDeck": [count for _, count in specs],
        "totalSlides": total_slides,
        "elapsedSeconds": round(elapsed, 6),
        "slidesPerSecond": round(total_slides / elapsed, 6),
        "checkpointRecoveredCount": total_slides,
        "metricRecollectionCount": counters.get("metrics_recollection_requests", 0),
        "chromiumProcessLaunchCount": counters.get("chrome_process_launches", 0),
        "runtimeCounterDelta": counters,
        "processTree": process_metrics,
    }


def _age(directory: Path, seconds: int) -> None:
    timestamp = time.time() - seconds
    os.utime(directory, (timestamp, timestamp))


async def _measure_durability(scratch: Path) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel
    from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from services import authored_checkpoint_artifacts as artifacts

    durability_root = scratch / "durability"
    checkpoint_root = durability_root / "checkpoints"
    database = durability_root / "sweeper.db"
    durability_root.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
            ],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    old_maker = artifacts.async_session_maker
    old_root = os.environ.get("AUTHORED_CHECKPOINT_DIRECTORY")
    artifacts.async_session_maker = maker
    os.environ["AUTHORED_CHECKPOINT_DIRECTORY"] = str(checkpoint_root)
    try:
        unsealed_id = uuid.uuid4()
        unsealed_dir = checkpoint_root / str(unsealed_id)
        render_path = unsealed_dir / "slide-0-benchmark-r0-evidence.png"
        png = b"benchmark-render-bytes"
        artifacts.atomic_write(render_path, png)
        artifacts.write_valid_evidence(
            render_path,
            attempt_id="benchmark-attempt-000000000000000",
            slide_index=0,
            revision=0,
            html="<html><body>checkpoint evidence</body></html>",
            png=png,
            metrics={"measured": True},
        )
        _age(unsealed_dir, 3600)
        preseal_sweep = await artifacts.sweep_expired_checkpoint_artifacts(
            ttl_seconds=60, batch_size=10, max_bytes=1024 * 1024, max_files=100
        )
        preserved_before_seal = unsealed_dir.is_dir()

        artifacts.mark_checkpoint_artifacts_sealed(unsealed_id)
        _age(unsealed_dir, 3600)
        postseal_sweep = await artifacts.sweep_expired_checkpoint_artifacts(
            ttl_seconds=60, batch_size=10, max_bytes=1024 * 1024, max_files=100
        )
        reclaimed_after_seal = not unsealed_dir.exists()

        budget_ids = [uuid.uuid4(), uuid.uuid4()]
        budget_dirs: list[Path] = []
        for job_id in budget_ids:
            directory = checkpoint_root / str(job_id)
            directory.mkdir(parents=True)
            artifacts.atomic_write(directory / "payload.bin", b"x" * 4096)
            artifacts.mark_checkpoint_artifacts_sealed(job_id)
            _age(directory, 3600)
            budget_dirs.append(directory)
        one_directory_bytes, one_directory_files = _directory_usage(budget_dirs[0])
        budget_sweep = await artifacts.sweep_expired_checkpoint_artifacts(
            ttl_seconds=60,
            batch_size=10,
            max_bytes=one_directory_bytes,
            max_files=one_directory_files,
        )
        retained_by_budget = sum(directory.exists() for directory in budget_dirs)

        long_ttl_id = uuid.uuid4()
        long_ttl_dir = checkpoint_root / str(long_ttl_id)
        long_ttl_dir.mkdir(parents=True)
        artifacts.atomic_write(long_ttl_dir / "payload.bin", b"long-ttl")
        artifacts.mark_checkpoint_artifacts_sealed(long_ttl_id)
        _age(long_ttl_dir, 3600)
        long_ttl_sweep = await artifacts.sweep_expired_checkpoint_artifacts(
            ttl_seconds=7 * 24 * 60 * 60,
            batch_size=10,
            max_bytes=1024 * 1024,
            max_files=100,
        )
        long_ttl_retained = long_ttl_dir.is_dir()
    finally:
        artifacts.async_session_maker = old_maker
        if old_root is None:
            os.environ.pop("AUTHORED_CHECKPOINT_DIRECTORY", None)
        else:
            os.environ["AUTHORED_CHECKPOINT_DIRECTORY"] = old_root
        await engine.dispose()

    return {
        "preSealPreservation": {
            "evidenceClass": "actual_measured_with_synthetic_age",
            "syntheticAgeSeconds": 3600,
            "ttlSeconds": 60,
            "preserved": preserved_before_seal,
            "sweep": preseal_sweep,
        },
        "postSealTtlReclamation": {
            "evidenceClass": "actual_measured_with_synthetic_age",
            "syntheticAgeSeconds": 3600,
            "ttlSeconds": 60,
            "reclaimed": reclaimed_after_seal,
            "sweep": postseal_sweep,
        },
        "byteBudgetReclamation": {
            "evidenceClass": "actual_measured_with_synthetic_age",
            "candidateDirectories": len(budget_dirs),
            "perDirectoryBytes": one_directory_bytes,
            "byteBudget": one_directory_bytes,
            "deletedDirectories": budget_sweep["deleted_orphan"],
            "retainedDirectories": retained_by_budget,
            "sweep": budget_sweep,
        },
        "longTtlRetention": {
            "evidenceClass": "instrumentation_only_time_shifted",
            "syntheticAgeSeconds": 3600,
            "ttlSeconds": 7 * 24 * 60 * 60,
            "retained": long_ttl_retained,
            "actualSevenDayWaitObserved": False,
            "sweep": long_ttl_sweep,
        },
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from utils import slide_capture

    chrome = slide_capture.find_chrome()
    if not chrome:
        raise RuntimeError("Chrome/Chromium was not found")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".benchmark-scratch-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        deck_33 = await _measure_deck(
            name="deck-33",
            slide_count=33,
            scratch=scratch,
            timeout_seconds=args.timeout_seconds,
            sample_interval_seconds=args.sample_interval_ms / 1000,
        )
        deck_80 = await _measure_deck(
            name="deck-80",
            slide_count=80,
            scratch=scratch,
            timeout_seconds=args.timeout_seconds,
            sample_interval_seconds=args.sample_interval_ms / 1000,
        )
        concurrent = await _measure_concurrent_decks(
            scratch=scratch,
            timeout_seconds=args.timeout_seconds,
            sample_interval_seconds=args.sample_interval_ms / 1000,
        )
        durability = await _measure_durability(scratch)
        slide_capture.reset_render_runtime(clear_caches=True, retire_session=True)

    result = {
        "schema": "authored-render-checkpoint-benchmark/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "repository": {
            "root": str(REPO_ROOT),
            "gitHead": _git_head(),
        },
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "chromePath": chrome,
            "chromeSha256": hashlib.sha256(Path(chrome).read_bytes()).hexdigest(),
            "renderMode": "isolated-development-in-process-reusable-chromium",
            "renderConcurrency": args.render_concurrency,
            "renderCacheSize": 32,
            "providerCalls": 0,
            "providerCallEvidenceClass": "instrumentation_only_no_provider_layer_imported",
        },
        "claims": {
            "actualMeasured": [
                "33-slide atomic render + checkpoint persist/recover",
                "80-slide atomic render + checkpoint persist/recover",
                "two concurrent 8-slide decks",
                "OS-sampled process-tree RSS and Chromium-family process count",
                "pre-seal preservation and post-seal/budget sweeper behavior using isolated storage",
            ],
            "instrumentationOnly": [
                "provider calls are zero by harness construction; network egress was not traced",
                "seven-day TTL retention uses a synthetic one-hour mtime and does not represent a seven-day wall-clock soak",
            ],
            "notVerified": [
                "running FastAPI public API to production worker path",
                "OS-level non-root/read-only/tmpfs/network-none external worker isolation",
                "historical operation/job recovery",
                "product PPTX export I/O",
                "product PDF export I/O",
                "seven-day wall-clock TTL soak",
            ],
        },
        "decks": {"33": deck_33, "80": deck_80},
        "concurrentLoad": concurrent,
        "durability": durability,
        "beforeAfter": {
            "before": {
                "status": "not_comparable",
                "reason": "No prior benchmark used this exact 33/80 atomic render-plus-checkpoint harness on this checkout and host.",
            },
            "after": {
                "deck33SlidesPerSecond": deck_33["slidesPerSecond"],
                "deck80SlidesPerSecond": deck_80["slidesPerSecond"],
                "deck33PeakProcessTreeRssBytes": deck_33["processTree"]["peakProcessTreeRssBytes"],
                "deck80PeakProcessTreeRssBytes": deck_80["processTree"]["peakProcessTreeRssBytes"],
                "deck33MetricRecollectionCount": deck_33["metricRecollectionCount"],
                "deck80MetricRecollectionCount": deck_80["metricRecollectionCount"],
            },
            "durabilityStateTransition": {
                "beforeSealPreserved": durability["preSealPreservation"]["preserved"],
                "afterSealTtlReclaimed": durability["postSealTtlReclamation"]["reclaimed"],
                "budgetDeletedDirectories": durability["byteBudgetReclamation"]["deletedDirectories"],
                "budgetRetainedDirectories": durability["byteBudgetReclamation"]["retainedDirectories"],
            },
        },
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    args = _parse_args()
    _configure_process(args)
    result = asyncio.run(_run(args))
    print(json.dumps({
        "output": str(args.output.resolve()),
        "deck33": {
            "elapsedSeconds": result["decks"]["33"]["elapsedSeconds"],
            "slidesPerSecond": result["decks"]["33"]["slidesPerSecond"],
            "peakProcessTreeRssBytes": result["decks"]["33"]["processTree"]["peakProcessTreeRssBytes"],
            "peakChromiumProcessCount": result["decks"]["33"]["processTree"]["peakChromiumProcessCount"],
            "metricRecollectionCount": result["decks"]["33"]["metricRecollectionCount"],
        },
        "deck80": {
            "elapsedSeconds": result["decks"]["80"]["elapsedSeconds"],
            "slidesPerSecond": result["decks"]["80"]["slidesPerSecond"],
            "peakProcessTreeRssBytes": result["decks"]["80"]["processTree"]["peakProcessTreeRssBytes"],
            "peakChromiumProcessCount": result["decks"]["80"]["processTree"]["peakChromiumProcessCount"],
            "metricRecollectionCount": result["decks"]["80"]["metricRecollectionCount"],
        },
        "durabilityStateTransition": result["beforeAfter"]["durabilityStateTransition"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
