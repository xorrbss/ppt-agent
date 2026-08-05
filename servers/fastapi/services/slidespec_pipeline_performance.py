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
    """Bounded process-local cache containing only conclusive empty proposals."""

    def __init__(self, max_entries: int = 256) -> None:
        self.max_entries = max(0, max_entries)
        self._entries: OrderedDict[str, None] = OrderedDict()

    def contains(self, key: ProposalCacheKey) -> bool:
        digest = key.digest
        if digest not in self._entries:
            return False
        self._entries.move_to_end(digest)
        return True

    def put(self, key: ProposalCacheKey) -> None:
        if self.max_entries == 0:
            return
        digest = key.digest
        self._entries[digest] = None
        self._entries.move_to_end(digest)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
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


def adjacent_influence_digests(signatures: Sequence[object]) -> tuple[str, ...]:
    """Return a digest that invalidates a changed slide and its direct neighbors."""

    local = tuple(digest_parts("slide-source/1", value) for value in signatures)
    return tuple(
        digest_parts(
            "deck-adjacency/1",
            local[index - 1] if index > 0 else None,
            local[index],
            local[index + 1] if index + 1 < len(local) else None,
        )
        for index in range(len(local))
    )


def inspection_cache_digest(
    *,
    spec_digest: str,
    content_digest: str,
    source_digest: str,
    font_digest: str,
    browser_digest: str,
    adjacent_digest: str,
) -> str:
    return digest_parts(
        INSPECTION_CACHE_VERSION,
        spec_digest,
        content_digest,
        source_digest,
        font_digest,
        browser_digest,
        adjacent_digest,
    )


def _plan_identity(plan: RepairPlan) -> str:
    return _canonical_digest(plan.model_dump(mode="json", by_alias=True, exclude_none=True))


def deterministic_candidate_order(plans: Sequence[RepairPlan]) -> tuple[RepairPlan, ...]:
    """Deduplicate and totally order candidates independently of provider order."""

    unique: dict[str, RepairPlan] = {}
    for plan in plans:
        identity = _plan_identity(plan)
        unique.setdefault(identity, plan)
    return tuple(
        sorted(
            unique.values(),
            key=lambda plan: (repair_cost(plan).sort_key, _plan_identity(plan)),
        )
    )


def _coverage(plan: RepairPlan) -> frozenset[str]:
    if plan.diagnostic_refs:
        return frozenset(plan.diagnostic_refs)
    return frozenset(str(target) for target in plan.scope)


def _sign(value: object, *, origin: float = 0.0) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0
    delta = float(value) - origin
    return 1 if delta > 1e-9 else -1 if delta < -1e-9 else 0


def _operation_effect_direction(operation: Mapping[str, object]) -> tuple[object, ...]:
    """Classify mutation direction without treating magnitude as equivalence.

    Pre-render dominance is only sound when both candidates mutate the same
    owners in the same way.  In particular, an inexpensive leaf translation
    cannot prove that an ancestor/composition translation is redundant.
    """

    kind = str(operation.get("op", ""))
    value = operation.get("value")
    value_map = value if isinstance(value, Mapping) else {}
    precondition = operation.get("precondition")
    precondition_map = precondition if isinstance(precondition, Mapping) else {}
    current = precondition_map.get("expectedBounds", precondition_map.get("current"))
    current_map = current if isinstance(current, Mapping) else {}

    def number(name: str) -> object:
        return operation.get(name, value_map.get(name))

    if kind in {"translateWithinParent", "translateBounds", "translate"}:
        return (
            _sign(number("x") if number("dx") is None else number("dx")),
            _sign(number("y") if number("dy") is None else number("dy")),
        )
    if kind == "setFontScale":
        return (_sign(number("scale"), origin=1.0),)
    if kind in {"setBounds", "resizeWithinBounds", "resizeBounds"}:
        proposed = operation.get("bounds")
        proposed_map = (
            proposed
            if isinstance(proposed, Mapping)
            else operation
            if kind == "resizeWithinBounds"
            else value_map
        )
        return tuple(
            _sign(proposed_map.get(axis), origin=float(current_map.get(axis, 0.0)))
            if isinstance(current_map.get(axis), (int, float))
            else 0
            for axis in ("x", "y", "width", "height")
        )
    if kind == "setSpacing":
        current_gap = precondition_map.get(
            "expectedValue", current_map.get("gap", precondition_map.get("gap"))
        )
        return (
            _sign(number("gap"), origin=float(current_gap))
            if isinstance(current_gap, (int, float))
            else 0,
        )
    # Categorical/structured effects (flow, crop, routing, subtree replacement,
    # etc.) have no safe scalar direction.  Requiring the exact payload keeps
    # alternatives incomparable unless they are genuinely identical.
    effect = {
        key: item
        for key, item in operation.items()
        if key not in {"op", "target", "precondition"}
    }
    return (_canonical_digest(effect),)


def _mutation_equivalence_class(plan: RepairPlan) -> tuple[object, ...]:
    operations = tuple(
        operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        for operation in plan.operations
    )
    targets = tuple(sorted({str(operation.get("target", "")) for operation in operations}))
    effects = tuple(
        sorted(
            (
                str(operation.get("target", "")),
                str(operation.get("op", "")),
                _operation_effect_direction(operation),
            )
            for operation in operations
        )
    )
    return targets, effects


def pareto_prune_plans(
    plans: Sequence[RepairPlan],
) -> tuple[tuple[RepairPlan, ...], tuple[RepairPlan, ...]]:
    """Remove candidates dominated on coverage and every pre-render cost axis."""

    ordered = deterministic_candidate_order(plans)

    def dominates(left: RepairPlan, right: RepairPlan) -> bool:
        # Coverage equality alone is not semantic equivalence: candidates that
        # repair the same issue via a leaf, ancestor, or coordinated composition
        # can have materially different browser outcomes.  Only prune within an
        # exact owner/family/direction class; every other pair is incomparable.
        if _mutation_equivalence_class(left) != _mutation_equivalence_class(right):
            return False
        left_coverage = _coverage(left)
        right_coverage = _coverage(right)
        if not right_coverage or not left_coverage.issuperset(right_coverage):
            return False
        left_cost = repair_cost(left).sort_key[:-1]
        right_cost = repair_cost(right).sort_key[:-1]
        return all(a <= b for a, b in zip(left_cost, right_cost)) and any(
            a < b for a, b in zip(left_cost, right_cost)
        )

    survivors: list[RepairPlan] = []
    pruned: list[RepairPlan] = []
    for candidate in ordered:
        if any(other is not candidate and dominates(other, candidate) for other in ordered):
            pruned.append(candidate)
        else:
            survivors.append(candidate)
    return tuple(survivors), tuple(pruned)


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
