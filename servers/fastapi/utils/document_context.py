import json
import os
from pathlib import Path
from typing import Optional, Sequence

from fastapi import HTTPException


DEFAULT_MAX_DOCUMENT_CONTEXT_CHARS = 80_000
DEFAULT_MAX_DOCUMENT_CHARS = 50_000
HARD_MAX_DOCUMENT_CONTEXT_CHARS = 500_000

TOTAL_LIMIT_ENV = "PRESENTON_MAX_DOCUMENT_CONTEXT_CHARS"
PER_DOCUMENT_LIMIT_ENV = "PRESENTON_MAX_DOCUMENT_CHARS"


def _configured_limit(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError:
        return default

    if value <= 0:
        return default
    return min(value, HARD_MAX_DOCUMENT_CONTEXT_CHARS)


def get_document_context_limits() -> tuple[int, int]:
    """Return the configured (total, per-document) character limits."""
    total_limit = _configured_limit(
        TOTAL_LIMIT_ENV,
        DEFAULT_MAX_DOCUMENT_CONTEXT_CHARS,
    )
    per_document_limit = _configured_limit(
        PER_DOCUMENT_LIMIT_ENV,
        DEFAULT_MAX_DOCUMENT_CHARS,
    )
    return total_limit, min(per_document_limit, total_limit)


def _safe_filename(path: Optional[str], index: int) -> str:
    if path:
        name = Path(path).name
        if name:
            return name
    return f"document-{index + 1}"


class DocumentContextLimitError(HTTPException):
    """A safe, public 413 response for oversized extracted document text."""

    def __init__(
        self,
        *,
        actual_chars: int,
        limit_chars: int,
        scope: str,
        filename: Optional[str] = None,
    ) -> None:
        detail = {
            "code": "document_context_too_large",
            "message": "The uploaded document text exceeds the allowed size.",
            "scope": scope,
            "actual_chars": actual_chars,
            "limit_chars": limit_chars,
        }
        if filename:
            detail["filename"] = filename
        super().__init__(status_code=413, detail=detail)

    def to_sse_detail(self) -> str:
        return json.dumps(self.detail, ensure_ascii=False)


def ensure_document_within_limit(
    document: str,
    *,
    filename: Optional[str] = None,
    per_document_limit: Optional[int] = None,
) -> None:
    """Reject one decoded document without including its contents in the error."""
    _, configured_per_document_limit = get_document_context_limits()
    limit = (
        configured_per_document_limit
        if per_document_limit is None
        else min(per_document_limit, HARD_MAX_DOCUMENT_CONTEXT_CHARS)
    )
    actual_chars = len(document)
    if actual_chars > limit:
        raise DocumentContextLimitError(
            actual_chars=actual_chars,
            limit_chars=limit,
            scope="document",
            filename=filename,
        )


def build_document_context(
    documents: Sequence[str],
    source_paths: Optional[Sequence[str]] = None,
    *,
    total_limit: Optional[int] = None,
    per_document_limit: Optional[int] = None,
) -> str:
    """Validate and join extracted documents without silently truncating them."""
    configured_total_limit, configured_per_document_limit = (
        get_document_context_limits()
    )
    effective_total_limit = (
        configured_total_limit
        if total_limit is None
        else min(total_limit, HARD_MAX_DOCUMENT_CONTEXT_CHARS)
    )
    effective_per_document_limit = (
        configured_per_document_limit
        if per_document_limit is None
        else min(per_document_limit, HARD_MAX_DOCUMENT_CONTEXT_CHARS)
    )
    effective_per_document_limit = min(
        effective_per_document_limit,
        effective_total_limit,
    )

    validated_documents: list[str] = []
    combined_chars = 0

    for index, document in enumerate(documents):
        source_path = (
            source_paths[index]
            if source_paths is not None and index < len(source_paths)
            else None
        )
        filename = _safe_filename(source_path, index)
        ensure_document_within_limit(
            document,
            filename=filename,
            per_document_limit=effective_per_document_limit,
        )

        separator_chars = 2 if validated_documents else 0
        combined_chars += separator_chars + len(document)
        if combined_chars > effective_total_limit:
            raise DocumentContextLimitError(
                actual_chars=combined_chars,
                limit_chars=effective_total_limit,
                scope="total",
                filename=filename,
            )
        validated_documents.append(document)

    return "\n\n".join(validated_documents)
