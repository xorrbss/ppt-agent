"""Deterministic caches and schedulers for the SlideSpec review pipeline.

This module owns orchestration-only performance policy.  It deliberately knows
nothing about HTML mutation or Chrome internals, so cache invalidation remains
explicit at the adapter boundary and repair safety continues to live in the
projection and acceptance layers.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Generic, Literal, TypeVar

from models.slidespec_v1 import RepairPlan
from services.slidespec_repair import repair_cost


PROPOSAL_CACHE_VERSION = "slidespec-proposal-negative/1"
INSPECTION_CACHE_VERSION = "slidespec-incremental-inspection/1"
PIPELINE_CAPABILITY_VERSION = "slidespec-projection-capability/1"
EVIDENCE_ATOM_CACHE_VERSION = "slidespec-evidence-atom/1"

EvidenceAtomState = Literal[
    "proven_clean",
    "proven_issue",
    "unknown",
    "not_applicable",
]
_EVIDENCE_ATOM_STATES = frozenset(
    {"proven_clean", "proven_issue", "unknown", "not_applicable"}
)
T = TypeVar("T")


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def digest_parts(*parts: object) -> str:
    """Hash versioned, structured inputs without ambiguous string joining."""

    return _canonical_digest(parts)


def dom_evidence_digest(evidence: object) -> str:
    defects = getattr(evidence, "defects", ())
    canonical = sorted(
        (
            str(getattr(item, "code", "")),
            str(getattr(item, "severity", "")),
            str(getattr(item, "semantic_id", "")),
            tuple(sorted(str(value) for value in getattr(item, "related_semantic_ids", ()))),
            str(getattr(item, "qid", "") or ""),
        )
        for item in defects
    )
    atoms = getattr(evidence, "atoms", ())
    canonical_atoms = sorted(
        (
            str(getattr(atom, "sensor", "")),
            str(getattr(atom, "metric", "")),
            str(getattr(atom, "state", "unknown")),
            str(getattr(atom, "stage", "")),
            str(getattr(atom, "qid", "") or ""),
            str(getattr(atom, "semantic_id", "") or ""),
            str(getattr(atom, "constraint_id", "") or ""),
            tuple(sorted(str(value) for value in getattr(atom, "dependency_refs", ()))),
            str(getattr(atom, "reason_code", "") or ""),
            bool(getattr(atom, "global_scope", False)),
            _canonical_digest(getattr(atom, "diagnostics", {})),
        )
        for atom in atoms
    )
    return digest_parts(
        "dom-evidence/2",
        canonical,
        canonical_atoms,
    )


@dataclass(frozen=True, slots=True)
class ProposalCacheKey:
    spec_digest: str
    dom_digest: str
    capability_digest: str
    model_digest: str

    @property
    def digest(self) -> str:
        return digest_parts(
            PROPOSAL_CACHE_VERSION,
            self.spec_digest,
            self.dom_digest,
            self.capability_digest,
            self.model_digest,
        )


class NegativeProposalCache:
    """Bounded TTL cache containing only conclusive empty proposals."""

    def __init__(
        self,
        max_entries: int = 256,
        *,
        ttl_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(0, max_entries)
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def contains(self, key: ProposalCacheKey) -> bool:
        digest = key.digest
        with self._lock:
            expires_at = self._entries.get(digest)
            if expires_at is None:
                return False
            if expires_at <= self._clock():
                self._entries.pop(digest, None)
                return False
            self._entries.move_to_end(digest)
            return True

    def put(self, key: ProposalCacheKey) -> None:
        if self.max_entries == 0 or self.ttl_seconds <= 0:
            return
        digest = key.digest
        with self._lock:
            self._entries[digest] = self._clock() + self.ttl_seconds
            self._entries.move_to_end(digest)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


DEFAULT_NEGATIVE_PROPOSAL_CACHE = NegativeProposalCache()


@dataclass(frozen=True, slots=True)
class EvidenceAtomCacheKey:
    """Identity for one sensor measurement, never for a whole-slide verdict.

    Both source and render digests are intentional.  Source changes invalidate
    imported semantic ownership while render changes invalidate geometry even
    when source bytes are unchanged.  QID and metric keep an unrelated footer
    miss from poisoning a confirmed overlap on another element.
    """

    qid: str
    metric: str
    sensor_version: str
    source_digest: str
    render_digest: str

    def __post_init__(self) -> None:
        for name in (
            "qid",
            "metric",
            "sensor_version",
            "source_digest",
            "render_digest",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"evidence atom cache key requires {name}")

    @property
    def digest(self) -> str:
        return digest_parts(
            EVIDENCE_ATOM_CACHE_VERSION,
            self.qid,
            self.metric,
            self.sensor_version,
            self.source_digest,
            self.render_digest,
        )


@dataclass(frozen=True, slots=True)
class CachedEvidenceAtom(Generic[T]):
    state: EvidenceAtomState
    value: T
    expires_at: float | None = None


class EvidenceAtomCache(Generic[T]):
    """Bounded atom cache with fail-safe unknown handling.

    Unknown measurements are excluded by default.  Deployments may opt into a
    short TTL to absorb repeated transient sensor failures, but an unknown (or a
    lower-confidence state) never overwrites a cached proven issue for the same
    exact evidence identity.
    """

    _STATE_RANK: Mapping[str, int] = {
        "not_applicable": 0,
        "unknown": 1,
        "proven_clean": 2,
        "proven_issue": 3,
    }

    def __init__(
        self,
        max_entries: int = 2048,
        *,
        unknown_ttl_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(0, max_entries)
        self.unknown_ttl_seconds = max(0.0, float(unknown_ttl_seconds))
        self._clock = clock
        self._entries: OrderedDict[str, CachedEvidenceAtom[T]] = OrderedDict()
        self._lock = threading.Lock()
        self._counters = {
            "hits": 0,
            "misses": 0,
            "writes": 0,
            "unknown_skipped": 0,
            "expired": 0,
            "monotonic_rejections": 0,
        }

    def get(self, key: EvidenceAtomCacheKey) -> CachedEvidenceAtom[T] | None:
        digest = key.digest
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                self._counters["misses"] += 1
                return None
            if entry.expires_at is not None and entry.expires_at <= self._clock():
                self._entries.pop(digest, None)
                self._counters["expired"] += 1
                self._counters["misses"] += 1
                return None
            self._entries.move_to_end(digest)
            self._counters["hits"] += 1
            return entry

    def put(
        self,
        key: EvidenceAtomCacheKey,
        *,
        state: EvidenceAtomState,
        value: T,
    ) -> bool:
        if state not in _EVIDENCE_ATOM_STATES:
            raise ValueError(f"unsupported evidence atom state: {state}")
        if self.max_entries == 0:
            return False
        now = self._clock()
        expires_at = None
        if state == "unknown":
            if self.unknown_ttl_seconds <= 0:
                with self._lock:
                    self._counters["unknown_skipped"] += 1
                return False
            expires_at = now + self.unknown_ttl_seconds

        digest = key.digest
        with self._lock:
            previous = self._entries.get(digest)
            if (
                previous is not None
                and (previous.expires_at is None or previous.expires_at > now)
                and self._STATE_RANK[previous.state] > self._STATE_RANK[state]
            ):
                self._entries.move_to_end(digest)
                self._counters["monotonic_rejections"] += 1
                return False
            self._entries[digest] = CachedEvidenceAtom(
                state=state,
                value=value,
                expires_at=expires_at,
            )
            self._entries.move_to_end(digest)
            self._counters["writes"] += 1
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def snapshot_counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


DEFAULT_EVIDENCE_ATOM_CACHE: EvidenceAtomCache[object] = EvidenceAtomCache()


class PipelinePerformanceWindow:
    """Bounded process-local operational samples for dashboard percentiles."""

    def __init__(self, max_entries: int = 128) -> None:
        self.max_entries = max(1, max_entries)
        self._samples: OrderedDict[int, tuple[str, dict[str, float]]] = OrderedDict()
        self._next_id = 0
        self._lock = threading.Lock()

    def record(self, mode: str, timings_ms: Mapping[str, float]) -> None:
        sample = {
            str(key): float(value)
            for key, value in timings_ms.items()
            if isinstance(value, (int, float))
        }
        with self._lock:
            self._samples[self._next_id] = (mode, sample)
            self._next_id += 1
            while len(self._samples) > self.max_entries:
                self._samples.popitem(last=False)

    @staticmethod
    def _percentile(values: Sequence[float], fraction: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(len(ordered) - 1, lower + 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    def snapshot(self) -> dict[str, dict[str, dict[str, float]]]:
        with self._lock:
            samples = tuple(self._samples.values())
        result: dict[str, dict[str, dict[str, float]]] = {}
        for mode in ("cold", "warm"):
            stages = sorted(
                {
                    stage
                    for sample_mode, timings in samples
                    if sample_mode == mode
                    for stage in timings
                }
            )
            if not stages:
                continue
            result[mode] = {}
            for stage in stages:
                values = [
                    timings[stage]
                    for sample_mode, timings in samples
                    if sample_mode == mode and stage in timings
                ]
                result[mode][stage] = {
                    "p50_ms": round(self._percentile(values, 0.50), 3),
                    "p95_ms": round(self._percentile(values, 0.95), 3),
                    "p99_ms": round(self._percentile(values, 0.99), 3),
                }
        return result


PIPELINE_PERFORMANCE_WINDOW = PipelinePerformanceWindow()


class IncrementalInspectionCache(Generic[T]):
    """LRU for immutable baseline inspection results keyed by full environment digest."""

    def __init__(self, max_entries: int = 256) -> None:
        self.max_entries = max(0, max_entries)
        self._entries: OrderedDict[str, T] = OrderedDict()

    def get(self, digest: str) -> T | None:
        value = self._entries.get(digest)
        if value is not None:
            self._entries.move_to_end(digest)
        return value

    def put(self, digest: str, value: T) -> None:
        if self.max_entries == 0:
            return
        self._entries[digest] = value
        self._entries.move_to_end(digest)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()


from services.slidespec_pipeline_planning import (
    adjacent_influence_digests,
    deterministic_candidate_order,
    inspection_cache_digest,
    pareto_prune_plans,
)



R = TypeVar("R")


class BoundedSlideScheduler:
    """Run independent slide preparation concurrently while preserving deck order."""

    def __init__(self, max_concurrency: int = 3) -> None:
        self.max_concurrency = max(1, max_concurrency)

    async def map(
        self,
        items: Sequence[T],
        worker: Callable[[T], Awaitable[R]],
    ) -> tuple[R, ...]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def bounded(item: T) -> R:
            async with semaphore:
                return await worker(item)

        return tuple(await asyncio.gather(*(bounded(item) for item in items)))


__all__ = [
    "BoundedSlideScheduler",
    "CachedEvidenceAtom",
    "DEFAULT_EVIDENCE_ATOM_CACHE",
    "DEFAULT_NEGATIVE_PROPOSAL_CACHE",
    "EVIDENCE_ATOM_CACHE_VERSION",
    "EvidenceAtomCache",
    "EvidenceAtomCacheKey",
    "EvidenceAtomState",
    "INSPECTION_CACHE_VERSION",
    "IncrementalInspectionCache",
    "NegativeProposalCache",
    "PIPELINE_CAPABILITY_VERSION",
    "PIPELINE_PERFORMANCE_WINDOW",
    "PROPOSAL_CACHE_VERSION",
    "ProposalCacheKey",
    "adjacent_influence_digests",
    "deterministic_candidate_order",
    "digest_parts",
    "dom_evidence_digest",
    "inspection_cache_digest",
    "pareto_prune_plans",
]
# ruff: noqa: E402, F401
