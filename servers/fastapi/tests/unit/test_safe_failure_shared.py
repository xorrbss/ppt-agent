"""``_safe_failure`` lives in one place and both generation paths use it."""

from __future__ import annotations

from services.presentation_generation_attempt_timing import _safe_failure
from services.public_generation_worker_lifecycle import (
    _safe_failure as lifecycle_safe_failure,
)


def test_single_implementation() -> None:
    assert lifecycle_safe_failure is _safe_failure


def test_code_resolution_and_truncation() -> None:
    assert _safe_failure({}) == ("presentation_generation_failed", True)
    assert _safe_failure({"code": "x"}) == ("x", True)
    assert _safe_failure({"detail": {"code": "nested"}}) == ("nested", True)
    assert _safe_failure({"detail": "plain"}) == ("plain", True)
    code, _ = _safe_failure({"code": "c" * 200})
    assert len(code) == 128


def test_retryable_resolution() -> None:
    assert _safe_failure({"code": "x", "retryable": False}) == ("x", False)
    assert _safe_failure({"detail": {"code": "n", "retryable": True}, "status_code": 400}) == (
        "n",
        True,
    )
    assert _safe_failure({"code": "x", "status_code": 400}) == ("x", False)
    assert _safe_failure({"code": "x", "status_code": 429}) == ("x", True)
    assert _safe_failure({"code": "x", "status_code": "503"}) == ("x", True)
    assert _safe_failure({"code": "x", "status_code": "bad"}) == ("x", True)
