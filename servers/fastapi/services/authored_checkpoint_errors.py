from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any
import re
import uuid

from sqlalchemy import update

from models.sql.presentation_generation_job import PresentationGenerationJob
from utils.datetime_utils import get_current_utc_datetime


_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+"), r"\1 [redacted]"),
    (re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}"), "[redacted]"),
    (
        re.compile(
            r"(?i)\b([a-z0-9_-]*(?:api[_-]?key|token|secret|password)[a-z0-9_-]*)\s*[:=]\s*[^\s,;]+"
        ),
        r"\1=[redacted]",
    ),
    (re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+"), r"\1?[redacted]"),
    (
        re.compile(
            r'(?i)(?:[a-z]:\\|/)(?:[^\s/:*?"<>|]+[/\\])+[^\s/:*?"<>|]+'
        ),
        "[redacted]",
    ),
)
_PUBLISH_FENCE_SECONDS = 300


class AuthoredStageError(RuntimeError):
    def __init__(
        self,
        *,
        slide_index: int,
        stage: str,
        code: str,
        cause: BaseException | None = None,
        retryable: bool = True,
        message: str | None = None,
    ) -> None:
        self.slide_index = slide_index
        self.stage = stage
        self.code = code
        self.cause = cause
        self.retryable = retryable
        self.safe_message = message or _default_message(code, stage)
        super().__init__(self.safe_message)

    def as_safe_dict(
        self, *, request_id: str | None = None, attempt_number: int | None = None
    ) -> dict[str, Any]:
        cause = self.cause or self
        exception_type = type(cause).__name__
        safe_cause = sanitize_exception_message(cause)
        fingerprint = hashlib.sha256(
            f"{exception_type}:{safe_cause}".encode("utf-8")
        ).hexdigest()[:16]
        safe_message = sanitize_exception_message(RuntimeError(self.safe_message))
        value: dict[str, Any] = {
            "status_code": 500,
            "code": self.code,
            "detail": safe_message,
            "message": safe_message,
            "stage": self.stage,
            "slide_index": self.slide_index,
            "slide_number": self.slide_index + 1,
            "exception_type": exception_type,
            "cause": safe_cause,
            "fingerprint": fingerprint,
            "retryable": self.retryable,
        }
        if request_id:
            value["request_id"] = request_id
        if attempt_number is not None:
            value["attempt_number"] = attempt_number
        return value


def _default_message(code: str, stage: str) -> str:
    messages = {
        "authored_slide_author_failed": "Slide authoring failed after retries",
        "authored_slide_illustrate_failed": "Slide illustration failed after retries",
        "authored_slide_render_failed": "Slide rendering failed after retries",
        "authored_slide_persist_failed": "Slide checkpoint persistence failed",
        "authored_injected_failure": "Development failure injection triggered",
        "authored_attempt_lost": "Generation attempt lease was lost",
    }
    return messages.get(code, f"Authored slide {stage} failed")


def sanitize_exception_message(error: BaseException) -> str:
    """Return a short diagnostic while excluding payloads, credentials and paths."""

    value = " ".join(str(error).split())[:1024]
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    # Provider bodies and authored HTML are not useful or safe in task metadata.
    if "<!doctype" in value.lower() or "<html" in value.lower():
        value = "[authored content redacted]"
    return value[:512] or type(error).__name__


async def begin_publish(
    session,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
) -> None:
    """Lock final publication to the live attempt inside the caller transaction.

    The conditional UPDATE is deliberately left uncommitted. On databases with
    row locking it prevents a lease takeover or cancellation from crossing the
    filesystem/Presentation-row publication boundary until that transaction
    commits or rolls back.
    """

    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.attempt_token == attempt_token,
            PresentationGenerationJob.state.in_(("running", "staging")),
        )
        .values(
            state="staging",
            current_stage="publish",
            lease_expires_at=now + timedelta(seconds=_PUBLISH_FENCE_SECONDS),
            heartbeat_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )


async def complete_publish(
    session,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
) -> None:
    """Make the fenced job terminal in the same transaction as deck rows."""

    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.attempt_token == attempt_token,
            PresentationGenerationJob.state == "staging",
        )
        .values(
            state="published",
            attempt_token=None,
            lease_expires_at=None,
            heartbeat_at=now,
            current_stage="completed",
            last_error=None,
            resume_allowed=False,
            published_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )


def is_retryable_exception(error: BaseException) -> bool:
    """Classify provider/client failures without persisting their response body."""

    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        if status_code == 429 or status_code >= 500:
            return True
        if 400 <= status_code < 500:
            return False
    return True


