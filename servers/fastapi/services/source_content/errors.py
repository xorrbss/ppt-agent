"""Stable source-content error boundary."""

from __future__ import annotations

from typing import Any


class SourceContentError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int = 422,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.detail = detail or {}


def source_error(code: str, *, status_code: int = 422) -> SourceContentError:
    return SourceContentError(code, status_code=status_code)


SECURITY_REJECTED = "source_content_security_rejected"
MANIFEST_INVALID = "source_content_manifest_invalid"
UNSUPPORTED_OBJECT = "source_content_unsupported_object"
