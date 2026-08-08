"""Deterministic retry policy for durable workspace operations.

The policy deliberately contains no database access.  Keeping the decision
pure makes recovery and normal handler failures use exactly the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import uuid


DEFAULT_BASE_BACKOFF_SECONDS = 2
DEFAULT_MAX_BACKOFF_SECONDS = 300


@dataclass(frozen=True, slots=True)
class WorkspaceOperationRetryDecision:
    state: str
    retry_count: int
    backoff_seconds: int
    next_attempt_at: datetime | None

    @property
    def will_retry(self) -> bool:
        return self.state == "queued"


def _stable_jitter(operation_id: uuid.UUID, retry_count: int, ceiling: int) -> int:
    if ceiling <= 0:
        return 0
    digest = sha256(f"{operation_id}:{retry_count}".encode("ascii")).digest()
    return int.from_bytes(digest[:2], "big") % (ceiling + 1)


def decide_workspace_operation_retry(
    *,
    operation_id: uuid.UUID,
    retry_count: int,
    max_attempts: int,
    now: datetime,
    retryable: bool,
    base_backoff_seconds: int = DEFAULT_BASE_BACKOFF_SECONDS,
    max_backoff_seconds: int = DEFAULT_MAX_BACKOFF_SECONDS,
) -> WorkspaceOperationRetryDecision:
    """Return the next durable state after an attempt fails.

    ``retry_count`` counts failed attempts.  A row with ``max_attempts=3`` can
    therefore be claimed at most three times before it is dead-lettered.
    Jitter is stable for an operation/attempt pair so a recovery replay cannot
    accidentally keep postponing the same attempt.
    """

    next_retry_count = max(0, retry_count) + 1
    normalized_max_attempts = max(1, max_attempts)
    if not retryable:
        return WorkspaceOperationRetryDecision(
            state="failed",
            retry_count=next_retry_count,
            backoff_seconds=0,
            next_attempt_at=None,
        )
    if next_retry_count >= normalized_max_attempts:
        return WorkspaceOperationRetryDecision(
            state="dead_letter",
            retry_count=next_retry_count,
            backoff_seconds=0,
            next_attempt_at=None,
        )

    base = max(1, base_backoff_seconds)
    cap = max(base, max_backoff_seconds)
    exponential = min(cap, base * (2 ** max(0, next_retry_count - 1)))
    jitter_ceiling = max(1, exponential // 4)
    delay = min(
        cap,
        exponential + _stable_jitter(operation_id, next_retry_count, jitter_ceiling),
    )
    return WorkspaceOperationRetryDecision(
        state="queued",
        retry_count=next_retry_count,
        backoff_seconds=delay,
        next_attempt_at=now + timedelta(seconds=delay),
    )
