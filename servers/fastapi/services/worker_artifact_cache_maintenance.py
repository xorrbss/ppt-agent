"""Eviction pass for the worker artifact transport cache, and put batching.

``WorkerArtifactTransportCache`` (``services/artifact_store.py``) evicts by
TTL and by byte budget.  Every admitted object used to run a full eviction
pass, and each pass walks the whole cache directory twice (``rglob`` + ``stat``
before and after evicting), so staging one export bundle cost
O(objects x cache size): ~160 puts per SVG export at 155 ms per walk with 238
entries.

Inside ``maintenance_batch()`` a put only records what it admitted (path,
size, resulting mtime, clock, and the entry's mtime before a cache hit).  At
the end of the batch one directory walk is taken and the per-put passes are
*replayed in memory* in admission order -- each with its own clock and its
own protected entry, exactly as they would have run -- and the union of what
they evict is removed once.  The evicted set is therefore identical to the
per-put passes for the same cache contents, sizes, mtimes, clocks and put
sequence, including a batch long enough for entries to expire mid-batch.  An
entry a replayed pass evicts but a later put re-admits is kept, because the
per-put scheme would have rewritten it.

What batching does change: eviction happens at the end of the batch rather
than between puts, so the cache may exceed its budget by at most the batch
for the batch's duration; and an entry whose unlink fails (e.g. held open on
Windows) stays until the next pass instead of letting a replayed pass evict
the next-oldest entry in its place.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, Sequence

CacheEntry = tuple[Path, float, int]


class CacheMetrics(Protocol):
    def set_cache_usage(self, *, entries: int, size_bytes: int) -> None: ...

    def observe_cache(self, event: str, *, count: int = 1, size_bytes: int = 0) -> None: ...


def scan_cache_entries(root: Path) -> list[CacheEntry]:
    entries: list[CacheEntry] = []
    if not root.exists():
        return entries
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file() or path.name.startswith("."):
                continue
            stat = path.stat()
        except OSError:
            continue
        entries.append((path, stat.st_mtime, stat.st_size))
    return entries


@dataclass(frozen=True, slots=True)
class CacheEvictionPolicy:
    ttl_seconds: int
    max_cache_bytes: int


@dataclass(frozen=True, slots=True)
class StagedPut:
    """One admission inside a batch, as the deferred per-put pass saw it."""

    path: Path
    size: int
    mtime: float
    clock: float
    prior_mtime: float | None


def entry_mtime(path: Path) -> float | None:
    """The mtime of a live cache entry, or ``None`` when there is none."""

    try:
        return path.stat().st_mtime if path.is_file() else None
    except OSError:
        return None


def touch_admitted(path: Path, size: int, prior_mtime: float | None) -> StagedPut:
    """Refresh an admitted entry's mtime (the TTL/LRU clock) and record it."""

    os.utime(path, None)
    return StagedPut(path=path, size=size, mtime=path.stat().st_mtime, clock=time.time(),
                     prior_mtime=prior_mtime)


def _planned_evictions(
    entries: dict[Path, tuple[float, int]],
    *,
    policy: CacheEvictionPolicy,
    now: float,
    protected: Path | None,
) -> list[Path]:
    """The per-put pass's decisions, assuming every unlink succeeds."""

    evicted = [
        path
        for path, (modified, _) in entries.items()
        if path != protected and now - modified > policy.ttl_seconds
    ]
    expired = set(evicted)
    survivors = [(path, modified, size) for path, (modified, size) in entries.items() if path not in expired]
    total = sum(size for _, _, size in survivors)
    for path, _, size in sorted(survivors, key=lambda item: item[1]):
        if total <= policy.max_cache_bytes:
            break
        if path == protected:
            continue
        total -= size
        evicted.append(path)
    return evicted


def _replayed_evictions(
    entries: list[CacheEntry],
    puts: Sequence[StagedPut],
    *,
    policy: CacheEvictionPolicy,
) -> list[Path]:
    state = {path: (modified, size) for path, modified, size in entries}
    # Rewind the batch: entries it created did not exist, hits had older mtimes.
    for put in reversed(puts):
        if put.prior_mtime is None:
            state.pop(put.path, None)
        else:
            state[put.path] = (put.prior_mtime, put.size)
    evicted: set[Path] = set()
    for put in puts:
        state[put.path] = (put.mtime, put.size)
        evicted.discard(put.path)
        for path in _planned_evictions(state, policy=policy, now=put.clock, protected=put.path):
            del state[path]
            evicted.add(path)
    return [path for path, _, _ in entries if path in evicted]


def run_cache_eviction_pass(
    root: Path,
    *,
    policy: CacheEvictionPolicy,
    known_entries: dict[Path, int],
    metrics: CacheMetrics,
    protected: Path | None = None,
    clock: Callable[[], float] | None = None,
    staged: Sequence[StagedPut] = (),
) -> dict[Path, int]:
    """Evict expired, then oldest-over-budget entries; return the new index.

    With ``staged`` puts the decisions are the replay of their deferred
    per-put passes (module docstring) instead of one pass at ``clock``.
    """

    now = clock() if clock is not None else time.time()
    entries = scan_cache_entries(root)
    observed = {path: size for path, _, size in entries}
    missing = {
        path: size for path, size in known_entries.items() if path not in observed
    }
    evicted_count = len(missing)
    evicted_bytes = sum(missing.values())
    if staged:
        for path in _replayed_evictions(entries, staged, policy=policy):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            evicted_count += 1
            evicted_bytes += observed[path]
    else:
        survivors: list[CacheEntry] = []
        for path, modified, size in entries:
            if path != protected and now - modified > policy.ttl_seconds:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    survivors.append((path, modified, size))
                else:
                    evicted_count += 1
                    evicted_bytes += size
            else:
                survivors.append((path, modified, size))
        total = sum(size for _, _, size in survivors)
        for path, _, size in sorted(survivors, key=lambda item: item[1]):
            if total <= policy.max_cache_bytes:
                break
            if path == protected:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= size
            evicted_count += 1
            evicted_bytes += size
    current = scan_cache_entries(root)
    metrics.set_cache_usage(
        entries=len(current),
        size_bytes=sum(size for _, _, size in current),
    )
    if evicted_count:
        metrics.observe_cache("eviction", count=evicted_count, size_bytes=evicted_bytes)
    return {path: size for path, _, size in current}


class DeferredCacheMaintenance:
    """Collapse the per-put eviction passes of one staging batch into one walk."""

    def __init__(self) -> None:
        self._depth = 0
        self._staged: list[StagedPut] = []

    def defer(self, put: StagedPut) -> bool:
        """Record a put inside a batch; ``False`` means run the pass now."""

        if self._depth == 0:
            return False
        self._staged.append(put)
        return True

    @asynccontextmanager
    async def batch(
        self, run_pass: Callable[[Sequence[StagedPut]], Awaitable[None]]
    ) -> AsyncIterator[None]:
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0 and self._staged:
                staged, self._staged = self._staged, []
                # Also after a failed put: the admitted entries stay bounded.
                await run_pass(staged)



async def stage_worker_inputs(service: Any, cache: Any, references: Sequence[Any]) -> None:
    """Copy one export's worker inputs into the cache under one maintenance walk.

    ``service`` is the durable artifact service (``stream_ref_to_object_store``)
    and ``cache`` the ``WorkerArtifactTransportCache``; the copies run
    concurrently, as the export paths always staged them.
    """

    async with cache.maintenance_batch():
        await asyncio.gather(
            *(service.stream_ref_to_object_store(reference, cache) for reference in references)
        )

__all__ = [
    "CacheEntry",
    "CacheEvictionPolicy",
    "DeferredCacheMaintenance",
    "StagedPut",
    "entry_mtime",
    "run_cache_eviction_pass",
    "scan_cache_entries",
    "stage_worker_inputs",
    "touch_admitted",
]
