"""Bounded resilience primitives for SlideSpec repair model calls.

The repair model is an optional candidate source, never a page generator.  This
module intentionally keeps its cache and circuit state process-local and keyed
by the complete provider contract so a failure from one model/schema version
cannot suppress a different configuration.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Literal, TypeAlias


NoCandidateReason: TypeAlias = Literal[
    "deterministic_no_candidate",
    "llm_provider_error",
    "llm_empty",
    "llm_schema_rejected",
    "proposal_preflight_unprojectable",
    "negative_cache_hit",
]

NO_CANDIDATE_REASONS: frozenset[str] = frozenset(
    {
        "deterministic_no_candidate",
        "llm_provider_error",
        "llm_empty",
        "llm_schema_rejected",
        "proposal_preflight_unprojectable",
        "negative_cache_hit",
    }
)

CANONICAL_ISSUE_CODES: frozenset[str] = frozenset(
    {
        "dom.clipping",
        "dom.overflow",
        "dom.off_canvas",
        "dom.unintended_overlap",
        "constraint.containment.violated",
        "constraint.non_overlap.violated",
        "constraint.gap.violated",
        "constraint.alignment.violated",
        "constraint.order.violated",
        "constraint.readability.violated",
        "constraint.evidence.missing",
    }
)

_ISSUE_ALIASES: Mapping[str, str] = {
    "clipping": "dom.clipping",
    "dom.clip": "dom.clipping",
    "overflow": "dom.overflow",
    "off_canvas": "dom.off_canvas",
    "offcanvas": "dom.off_canvas",
    "dom.offcanvas": "dom.off_canvas",
    "overlap": "dom.unintended_overlap",
    "unintended_overlap": "dom.unintended_overlap",
    "dom.overlap": "dom.unintended_overlap",
    "containment": "constraint.containment.violated",
    "constraint.containment": "constraint.containment.violated",
    "non_overlap": "constraint.non_overlap.violated",
    "constraint.non_overlap": "constraint.non_overlap.violated",
    "gap": "constraint.gap.violated",
    "constraint.gap": "constraint.gap.violated",
    "alignment": "constraint.alignment.violated",
    "constraint.alignment": "constraint.alignment.violated",
    "order": "constraint.order.violated",
    "constraint.order": "constraint.order.violated",
    "readability": "constraint.readability.violated",
    "constraint.readability": "constraint.readability.violated",
    "constraint_readability": "constraint.readability.violated",
    "evidence_missing": "constraint.evidence.missing",
}


def canonicalize_issue_code(value: object) -> str | None:
    """Return a supported diagnostic enum value, never an open-ended label."""

    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in CANONICAL_ISSUE_CODES:
        return token
    return _ISSUE_ALIASES.get(token)


@dataclass(frozen=True, slots=True)
class RepairLlmIdentity:
    provider: str
    model: str
    schema_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        if not all(
            str(value).strip()
            for value in (
                self.provider,
                self.model,
                self.schema_version,
                self.prompt_version,
            )
        ):
            raise ValueError("repair LLM identity fields must be non-empty")


@dataclass(frozen=True, slots=True)
class RepairLlmCacheKey:
    identity: RepairLlmIdentity
    input_digest: str

    @property
    def digest(self) -> str:
        payload = (
            "slidespec-repair-negative/2",
            self.identity.provider,
            self.identity.model,
            self.identity.schema_version,
            self.identity.prompt_version,
            self.input_digest,
        )
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def repair_llm_input_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _NegativeEntry:
    reason: NoCandidateReason
    expires_at: float


class RepairLlmNegativeCache:
    """Thread-safe bounded TTL cache for conclusive no-plan responses only."""

    def __init__(
        self,
        max_entries: int = 256,
        *,
        ttl_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(0, int(max_entries))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock
        self._entries: OrderedDict[str, _NegativeEntry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: RepairLlmCacheKey) -> NoCandidateReason | None:
        digest = key.digest
        now = self._clock()
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(digest, None)
                return None
            self._entries.move_to_end(digest)
            return entry.reason

    def put(self, key: RepairLlmCacheKey, reason: NoCandidateReason) -> bool:
        # Transient provider failures belong to the circuit breaker, never the
        # negative cache.  Preflight failures occur before a provider key exists.
        if reason not in {"llm_empty", "llm_schema_rejected"}:
            return False
        if self.max_entries == 0 or self.ttl_seconds <= 0:
            return False
        with self._lock:
            self._entries[key.digest] = _NegativeEntry(
                reason=reason,
                expires_at=self._clock() + self.ttl_seconds,
            )
            self._entries.move_to_end(key.digest)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


@dataclass(slots=True)
class _CircuitState:
    consecutive_failures: int = 0
    open_until: float = 0.0


class RepairLlmCircuitBreaker:
    """Provider-contract keyed consecutive-failure circuit breaker."""

    def __init__(
        self,
        failure_threshold: int = 3,
        *,
        cooldown_seconds: float = 30.0,
        max_identities: int = 64,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_identities = max(1, int(max_identities))
        self._clock = clock
        self._states: OrderedDict[RepairLlmIdentity, _CircuitState] = OrderedDict()
        self._lock = threading.Lock()

    def allow_call(self, identity: RepairLlmIdentity) -> bool:
        now = self._clock()
        with self._lock:
            state = self._states.get(identity)
            if state is None:
                return True
            self._states.move_to_end(identity)
            return state.open_until <= now

    def record_failure(self, identity: RepairLlmIdentity) -> None:
        now = self._clock()
        with self._lock:
            state = self._states.setdefault(identity, _CircuitState())
            # A failure after cooldown starts a new consecutive sequence.
            if state.open_until and state.open_until <= now:
                state.consecutive_failures = 0
                state.open_until = 0.0
            state.consecutive_failures += 1
            if state.consecutive_failures >= self.failure_threshold:
                state.open_until = now + self.cooldown_seconds
            self._states.move_to_end(identity)
            while len(self._states) > self.max_identities:
                self._states.popitem(last=False)

    def record_success(self, identity: RepairLlmIdentity) -> None:
        with self._lock:
            self._states.pop(identity, None)

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


DEFAULT_REPAIR_LLM_NEGATIVE_CACHE = RepairLlmNegativeCache()
DEFAULT_REPAIR_LLM_CIRCUIT_BREAKER = RepairLlmCircuitBreaker(
    # One repair review can run for several minutes.  A 30-second cooldown
    # repeatedly re-admitted the same unhealthy provider between slide waves;
    # retain a bounded five-minute quarantine for this exact contract identity.
    cooldown_seconds=300.0,
)


__all__ = [
    "CANONICAL_ISSUE_CODES",
    "DEFAULT_REPAIR_LLM_CIRCUIT_BREAKER",
    "DEFAULT_REPAIR_LLM_NEGATIVE_CACHE",
    "NO_CANDIDATE_REASONS",
    "NoCandidateReason",
    "RepairLlmCacheKey",
    "RepairLlmCircuitBreaker",
    "RepairLlmIdentity",
    "RepairLlmNegativeCache",
    "canonicalize_issue_code",
    "repair_llm_input_digest",
]
