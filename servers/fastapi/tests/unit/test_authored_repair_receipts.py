"""Provider receipts for the repair ladder's remake and patch calls (design M0).

Bench run 48 had 25 ``author`` receipts and none for the 7 remake calls the
repair stage made, because the remake rounds ran outside any usage scope.
These tests pin the seam that closes that gap: a round scope gives each
remake and patch call a receipt of its own stage, keeps the two remake rounds
of one slide apart, deduplicates a replayed round, and leaves the first-pass
author keys byte for byte as they were.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from types import SimpleNamespace

from services.provider_usage_instrumentation import (
    ProviderUsageScope,
    active_usage_scope_slide_index,
    provider_call_admission,
    provider_usage_scope,
    record_provider_call,
)
from services.generation_core.authoring.author_slide import authored_provider_semantic_slot

_AUTHOR_MODEL = "gpt-5.6-sol"


def _run(coro):
    return asyncio.run(coro)


def _job(attempt_number: int = 1):
    return SimpleNamespace(id=uuid.uuid4(), attempt_number=attempt_number)


def _capture_receipts(monkeypatch) -> list:
    """Route every receipt into a list; nothing here touches the database."""

    receipts: list = []

    async def receipt(value):
        receipts.append(value)

    monkeypatch.setattr(
        "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
        receipt,
    )
    monkeypatch.setattr(
        "services.provider_usage_instrumentation.durable_provider_runtime_required",
        lambda: False,
    )
    # The span receipt must reach the same sink as the calls, not the queue.
    monkeypatch.setenv("AUTHORED_LOCAL_STAGE_ASYNC_ENABLED", "false")
    monkeypatch.delenv("PROVIDER_INPUT_MICROUNITS_PER_1K", raising=False)
    monkeypatch.delenv("PROVIDER_OUTPUT_MICROUNITS_PER_1K", raising=False)
    return receipts


async def _author_boundary(index: int, *, input_tokens: int = 1200, output_tokens: int = 300):
    """The call boundary ``author_slide_html`` runs for a full re-author.

    Same slot shape and receipt fields as the real function: the slide goes
    into the slot when the scope names no slide, and the receipt is filed as a
    recovery call of the author model.
    """

    slot = authored_provider_semantic_slot(
        receipt_retry_class="recovery",
        receipt_attempt_ordinal=2,
        slide_index=(None if active_usage_scope_slide_index() is not None else index),
    )
    async with provider_call_admission(
        is_retry=True, semantic_slot=slot, provider="codex", model=_AUTHOR_MODEL
    ):
        pass
    await record_provider_call(
        provider="codex",
        model=_AUTHOR_MODEL,
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        ),
        latency_ms=1500,
        retry_class="recovery",
    )


async def _first_pass_call(job, index: int) -> None:
    """The first-pass author call every remake follows in a real deck.

    The runtime control admits a retry only against an initial request of the
    same durable job (``retry_requires_an_initial_request``), so the remake
    tests seed the one call the author stage would already have filed.
    """

    with provider_usage_scope(
        job_id=job.id, operation="authored_generation", stage="author",
        attempt_number=job.attempt_number, slide_index=index,
    ):
        async with provider_call_admission(
            semantic_slot="author.initial", provider="codex", model=_AUTHOR_MODEL
        ):
            pass
        await record_provider_call(
            provider="codex", model=_AUTHOR_MODEL, latency_ms=66_000,
            response=SimpleNamespace(usage=SimpleNamespace(input_tokens=12_700, output_tokens=4_400)),
        )


def _remake_kwargs(index: int = 3):
    return {
        "index": index,
        "html": "<html><body>TERMINAL</body></html>",
        "png": b"png",
        "content": "slide content",
        "role": "body",
        "defects": (SimpleNamespace(category="overlap", detail="two boxes overlap"),),
        "capacity": {},
        "profile": SimpleNamespace(profile_id="ordinary"),
        "reference_mode": "style_reference",
        "brand": SimpleNamespace(primary="#123456"),
        "design_system": "DESIGN SYSTEM",
        "style": SimpleNamespace(id="editorial", reference_mode="style_reference"),
        "total_slides": 24,
        "reference_kwargs": {},
    }


def _calls(receipts):
    """Provider calls of the repair ladder: not the spans, not the seeded first pass."""

    return [r for r in receipts if r.provider != "local" and r.stage != "author"]


def _first_pass_receipt_hash(job, index: int) -> str:
    """The v2 key the author stage files for its initial call, derived by hand."""

    scope = ProviderUsageScope(job.id, "authored_generation", "author", job.attempt_number, index)
    ordinal = scope.public_logical_call_ordinal("author.initial")
    request_id = f"call:authored_generation:author:{job.attempt_number}:slide-{index}:{ordinal}:1"
    return hashlib.sha256(f"receipt-v2:{job.id}:{request_id}:provider".encode("utf-8")).hexdigest()


def _spans(receipts):
    return [r for r in receipts if r.provider == "local" and r.model == "none"]


# ---------------------------------------------------------------------------
# Remake calls
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Patch calls
# ---------------------------------------------------------------------------


_PATCH_DOM = [
    {
        "qid": "title",
        "tag": "h1",
        "text": "Quarterly result",
        "bbox": {"x": 10, "y": 20, "width": 400, "height": 80},
        "computedStyle": {"fontSize": "40px"},
    }
]


class _PatchClient:
    """A non-streaming transport answering one valid patch plan with usage."""

    def __init__(self):
        self.calls = 0

    def generate(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            content={
                "strategy": "patch",
                "actions": [
                    {"action": "setStyle", "qid": "title", "styles": {"font-size": "36px"}}
                ],
                "rationale": "remove title clipping",
            },
            usage=SimpleNamespace(input_tokens=800, output_tokens=120),
        )


# ---------------------------------------------------------------------------
# Keys: rounds apart, first pass unchanged, no job means no receipt
# ---------------------------------------------------------------------------


def test_the_round_is_part_of_every_key_the_scope_derives():
    job_id = uuid.uuid4()
    first = ProviderUsageScope(job_id, "authored_generation", "repair_remake", 1, None, 1)
    second = ProviderUsageScope(job_id, "authored_generation", "repair_remake", 1, None, 2)
    resumed = ProviderUsageScope(job_id, "authored_generation", "repair_remake", 2, None, 2)
    slot = "author.recovery.slide-19"

    assert first.public_logical_call_key(slot) == "author.recovery.slide-19.round-1"
    assert second.public_logical_call_key(slot) == "author.recovery.slide-19.round-2"
    assert resumed.public_logical_call_key(slot) == "author.recovery.slide-19.round-2.resume-2"
    assert first.next_runtime_request_id(slot) != second.next_runtime_request_id(slot)
    assert first.next_receipt_key_hash("provider") != second.next_receipt_key_hash("provider")
    # The public ordinal names the slot only; the round rides in the key.
    assert first.public_logical_call_ordinal(slot) == second.public_logical_call_ordinal(slot)


def test_first_pass_keys_are_unchanged_without_a_round():
    job_id = uuid.uuid4()
    scope = ProviderUsageScope(job_id, "authored_generation", "author", 1, 3)

    assert scope.round_ordinal is None
    assert scope.public_logical_call_key("author.initial") == "author.initial"
    assert scope.next_runtime_request_id("author.initial") == (
        f"call:authored_generation:author:1:slide-3:"
        f"{scope.public_logical_call_ordinal('author.initial')}:1"
    )
    expected = hashlib.sha256(
        f"receipt-v1:{job_id}:authored_generation:author:1:3:1:provider".encode("utf-8")
    ).hexdigest()
    assert scope.next_receipt_key_hash("provider") == expected


async def _noop_receipt(value):
    return value
