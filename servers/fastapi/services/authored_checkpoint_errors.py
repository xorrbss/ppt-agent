from __future__ import annotations

import hashlib
from datetime import timedelta
from collections.abc import Mapping
from typing import Any
import re
import uuid

from sqlalchemy import update

from models.sql.presentation_generation_job import PresentationGenerationJob
from utils.datetime_utils import get_current_utc_datetime
from utils.renderer_worker_policy import RendererPolicyError


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


_PRIOR_FAILURE_KEYS = ("code", "exception_type", "cause", "message", "detail", "fingerprint")


def _bounded_prior_failure(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Keep only the scalar diagnostic fields of an earlier attempt's failure.

    The stored value is already sanitized task metadata, but a later attempt
    must not widen it: copy a fixed set of short strings and nothing else.
    """

    if not isinstance(value, Mapping):
        return None
    bounded = {
        key: sanitize_exception_message(RuntimeError(value[key]))[
            :512 if key in {"message", "detail"} else 200
        ]
        for key in _PRIOR_FAILURE_KEYS
        if isinstance(value.get(key), str) and value.get(key)
    }
    raw_block_ids = value.get("block_ids")
    if isinstance(raw_block_ids, (list, tuple)):
        block_ids = [
            " ".join(item.split())[:160]
            for item in raw_block_ids[:16]
            if isinstance(item, str) and item.strip()
        ]
        if block_ids:
            bounded["block_ids"] = block_ids
    return bounded or None


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
        provider_attempt_count: int | None = None,
        prior_failure: Mapping[str, Any] | None = None,
    ) -> None:
        self.slide_index = slide_index
        self.stage = stage
        self.code = code
        self.cause = cause
        self.retryable = retryable
        # A later attempt that only knows "this slide already spent its calls"
        # would otherwise overwrite the one record that says *why* the slide
        # failed. Carry the first attempt's bounded diagnostic forward.
        self.prior_failure = _bounded_prior_failure(prior_failure)
        self.safe_message = message or _default_message(code, stage)
        self.provider_attempt_count = (
            provider_attempt_count
            if isinstance(provider_attempt_count, int)
            and not isinstance(provider_attempt_count, bool)
            # Three, not two: the author-stage rescue
            # (``services/authored_author_stage_rescue.py``) spends a third
            # call, and a resumed attempt that reports the spent budget must
            # not have that number silently dropped as out of range.
            and 0 <= provider_attempt_count <= 3
            else None
        )
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
        raw_block_ids = getattr(cause, "block_ids", ())
        if isinstance(raw_block_ids, (list, tuple)):
            block_ids = [
                " ".join(item.split())[:160]
                for item in raw_block_ids[:16]
                if isinstance(item, str) and item.strip()
            ]
            if block_ids:
                value["block_ids"] = block_ids
        if request_id:
            value["request_id"] = request_id
        if attempt_number is not None:
            value["attempt_number"] = attempt_number
        if self.provider_attempt_count is not None:
            value["provider_attempt_count"] = self.provider_attempt_count
        if self.prior_failure:
            value["prior_failure"] = dict(self.prior_failure)
        from services.authored_author_capacity_recovery import safe_owner_capacity_diagnostic

        diagnostic = safe_owner_capacity_diagnostic(
            getattr(cause, "owner_capacity_diagnostic", None)
        )
        if diagnostic is not None:
            value["owner_capacity_diagnostic"] = diagnostic
        from services.authored_title_recovery_contract import safe_title_token_diagnostic

        token_diagnostic = safe_title_token_diagnostic(
            getattr(cause, "title_token_diagnostic", None)
        )
        if token_diagnostic is not None:
            value["title_token_diagnostic"] = token_diagnostic
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

    if isinstance(error, RendererPolicyError):
        return False
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


