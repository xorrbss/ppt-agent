"""Stable content-free provider call identities."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.provider_usage_instrumentation import PublicOperationFence, ProviderUsageScope


def _normalize_semantic_slot(value: str) -> str:
    normalized = str(value).strip().lower()
    if not normalized or len(normalized) > 128:
        raise ValueError("provider_semantic_slot_invalid")
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in normalized):
        raise ValueError("provider_semantic_slot_invalid")
    return normalized


def _provider_idempotency_key(
    fence: PublicOperationFence,
    scope: ProviderUsageScope,
    semantic_slot: str,
) -> str:
    slide = "deck" if scope.slide_index is None else str(scope.slide_index)
    preimage = (
        f"public-provider-call/v2:{fence.operation_id}:{scope.stage}:"
        f"{slide}:{_normalize_semantic_slot(semantic_slot)}"
    )
    return f"ppt-{sha256(preimage.encode('utf-8')).hexdigest()}"
