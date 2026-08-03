import json
import logging
import os
from pathlib import Path
from typing import Optional, Sequence

from fastapi import HTTPException


DEFAULT_MAX_DOCUMENT_CONTEXT_CHARS = 500_000
# By default, one document may use the full context budget. Keeping a lower
# implicit per-document cap rejects otherwise valid single-file uploads.
DEFAULT_MAX_DOCUMENT_CHARS = DEFAULT_MAX_DOCUMENT_CONTEXT_CHARS
HARD_MAX_DOCUMENT_CONTEXT_CHARS = 500_000

TOTAL_LIMIT_ENV = "PRESENTON_MAX_DOCUMENT_CONTEXT_CHARS"
PER_DOCUMENT_LIMIT_ENV = "PRESENTON_MAX_DOCUMENT_CHARS"
TRUNCATION_MARKER = "\n\n[Source text shortened to fit the generation context.]\n\n"

LOGGER = logging.getLogger(__name__)


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
    overflow_strategy: str = "error",
) -> str:
    """Validate and join extracted documents within the prompt context budget.

    ``overflow_strategy="truncate"`` is intended for generation paths. It
    deterministically keeps the beginning and end of every source while
    retaining the existing hard input ceiling. The default remains strict for
    callers that rely on a 413 response.
    """
    if overflow_strategy not in {"error", "truncate"}:
        raise ValueError("overflow_strategy must be 'error' or 'truncate'")

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
        input_limit = (
            HARD_MAX_DOCUMENT_CONTEXT_CHARS
            if overflow_strategy == "truncate"
            else effective_per_document_limit
        )
        ensure_document_within_limit(
            document,
            filename=filename,
            per_document_limit=input_limit,
        )

        separator_chars = 2 if validated_documents else 0
        combined_chars += separator_chars + len(document)
        if (
            overflow_strategy == "truncate"
            and combined_chars > HARD_MAX_DOCUMENT_CONTEXT_CHARS
        ):
            raise DocumentContextLimitError(
                actual_chars=combined_chars,
                limit_chars=HARD_MAX_DOCUMENT_CONTEXT_CHARS,
                scope="total",
                filename=filename,
            )
        if overflow_strategy == "error" and combined_chars > effective_total_limit:
            raise DocumentContextLimitError(
                actual_chars=combined_chars,
                limit_chars=effective_total_limit,
                scope="total",
                filename=filename,
            )
        validated_documents.append(document)

    if combined_chars <= effective_total_limit:
        return "\n\n".join(validated_documents)

    LOGGER.warning(
        "Compacting extracted document context from %d to %d characters across %d sources",
        combined_chars,
        effective_total_limit,
        len(validated_documents),
    )
    return _compact_documents(validated_documents, effective_total_limit)


def _compact_documents(documents: Sequence[str], total_limit: int) -> str:
    """Fit all sources into ``total_limit`` while preserving each source."""
    if not documents or total_limit <= 0:
        return ""

    separator_chars = 2 * (len(documents) - 1)
    if separator_chars >= total_limit:
        return "\n\n".join(documents)[:total_limit]

    content_budget = total_limit - separator_chars
    lengths = [len(document) for document in documents]
    nonempty = [index for index, length in enumerate(lengths) if length > 0]
    allocations = [0] * len(documents)

    # Reserve one character for every non-empty source so a small source is not
    # dropped merely because a neighboring document is much larger.
    if content_budget >= len(nonempty):
        for index in nonempty:
            allocations[index] = 1
        remaining_budget = content_budget - len(nonempty)
        capacities = [max(length - 1, 0) for length in lengths]
    else:
        remaining_budget = content_budget
        capacities = lengths

    total_capacity = sum(capacities)
    if remaining_budget > 0 and total_capacity > 0:
        exact_shares = [
            remaining_budget * capacity / total_capacity
            for capacity in capacities
        ]
        floor_shares = [int(share) for share in exact_shares]
        for index, share in enumerate(floor_shares):
            allocations[index] += share

        undistributed = remaining_budget - sum(floor_shares)
        remainder_order = sorted(
            range(len(documents)),
            key=lambda index: exact_shares[index] - floor_shares[index],
            reverse=True,
        )
        for index in remainder_order[:undistributed]:
            allocations[index] += 1

    compacted = [
        _compact_document(document, allocation)
        for document, allocation in zip(documents, allocations)
    ]
    return "\n\n".join(compacted)


def _compact_document(document: str, target_chars: int) -> str:
    if len(document) <= target_chars:
        return document
    if target_chars <= 0:
        return ""
    if target_chars <= len(TRUNCATION_MARKER) + 2:
        return document[:target_chars]

    preserved_chars = target_chars - len(TRUNCATION_MARKER)
    head_chars = (preserved_chars * 2) // 3
    tail_chars = preserved_chars - head_chars
    return (
        document[:head_chars]
        + TRUNCATION_MARKER
        + document[len(document) - tail_chars :]
    )
