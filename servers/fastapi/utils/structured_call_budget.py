"""Physical provider-call budget for one structured-output call site.

``utils/llm_utils.py`` hands every structured call one of these budgets and
consumes one unit per physical provider request, across the parse, transient
and schema-validation retry loops together.  The budget object lives in its
own leaf module so a caller that runs the same logical call twice -- the Stage
A content brief and its one reduced-input retry
(``services/content_brief_policy.py``) -- can create a single
budget and share it between both runs without importing the LLM transport.
Without a shared budget each run started from a fresh limit, so the brief could
make four physical calls while the public admission plan reserved two.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

#: One initial call plus one public-policy retry, for one logical call.
PUBLIC_STRUCTURED_MAX_PHYSICAL_CALLS = 2


@dataclass
class StructuredProviderCallBudget:
    """One initial call plus one public-policy retry across all helper loops."""

    limit: int = PUBLIC_STRUCTURED_MAX_PHYSICAL_CALLS
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume(self) -> None:
        if self.remaining <= 0:
            raise HTTPException(
                status_code=503,
                detail="public_provider_retry_limit_exceeded",
                headers={"Retry-After": "10"},
            )
        self.used += 1


__all__ = ["PUBLIC_STRUCTURED_MAX_PHYSICAL_CALLS", "StructuredProviderCallBudget"]
