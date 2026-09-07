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

from services import authored_repair_receipts as receipts_mod
from services import authored_terminal_slide_fallback as terminal_mod
from services.authored_repair_receipts import (
    PATCH_ROUND,
    REMAKE_FIRST_ROUND,
    TERMINAL_ROUND,
    repair_round_receipts,
)
from services.provider_usage_instrumentation import (
    ProviderUsageScope,
    active_usage_scope_slide_index,
    provider_call_admission,
    provider_usage_scope,
    record_provider_call,
)
from utils.llm_calls import author_slide_patch as patch_mod
from utils.llm_calls.author_slide import authored_provider_semantic_slot

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


def _install_remake(monkeypatch, *, candidate: str = "<html><body>REMADE</body></html>"):
    """A reviser that pays for one author call, and an admission that accepts it."""

    calls: list[int] = []

    async def fake_revise(htmls, pngs, contents, roles, brand, design_system, **kwargs):
        index = int(kwargs["slide_indices"][0])
        calls.append(index)
        await _author_boundary(index)
        return [[candidate], [b"remade-png"], [0], [{"path": "full_reauthor"}]]

    async def fake_admit(*, candidate_html, capacity, profile, reference_mode):
        return candidate_html, b"remade-png", {"telemetry": "ok"}

    monkeypatch.setattr(terminal_mod, "revise_authored_deck", fake_revise)
    monkeypatch.setattr(terminal_mod, "_admit_remade_slide", fake_admit)
    return calls


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


def test_a_remake_call_records_one_receipt_of_the_remake_stage(monkeypatch):
    receipts = _capture_receipts(monkeypatch)
    calls = _install_remake(monkeypatch)
    job = _job()

    async def scenario():
        await _first_pass_call(job, 3)
        async with repair_round_receipts(job, repair_round=REMAKE_FIRST_ROUND):
            admitted, outcome = await terminal_mod.remake_slide_once(**_remake_kwargs(3))
        return admitted, outcome

    admitted, outcome = _run(scenario())

    assert outcome == "remade" and admitted is not None
    assert calls == [3]
    # The first-pass author receipt is exactly what it was before this seam.
    first_pass = receipts[0]
    assert (first_pass.stage, first_pass.retry_class) == ("author", "initial")
    assert first_pass.receipt_key_hash == _first_pass_receipt_hash(job, 3)
    (call,) = _calls(receipts)
    assert call.job_id == job.id
    assert call.operation == "authored_generation"
    assert call.stage == "repair_remake"
    assert call.provider == "codex"
    assert call.model == _AUTHOR_MODEL
    assert call.retry_class == "recovery"
    assert call.outcome == "succeeded"
    assert call.usage_state == "reported"
    assert (call.input_tokens, call.output_tokens) == (1200, 300)
    assert call.latency_ms == 1500
    assert len(call.receipt_key_hash) == 64  # a digest, never the preimage
    # The round's wall clock rides in the same stage as a local span receipt.
    (span,) = _spans(receipts)
    assert span.stage == "repair_remake"
    assert span.usage_state == "unavailable"
    assert span.receipt_key_hash != call.receipt_key_hash


def test_a_failed_remake_call_is_still_receipted_as_failed(monkeypatch):
    """Run 48's one failed remake spent 57 s of provider time; it must show."""

    receipts = _capture_receipts(monkeypatch)

    async def failing_revise(htmls, pngs, contents, roles, brand, design_system, **kwargs):
        index = int(kwargs["slide_indices"][0])
        slot = authored_provider_semantic_slot(
            receipt_retry_class="recovery", receipt_attempt_ordinal=2, slide_index=index
        )
        try:
            async with provider_call_admission(
                is_retry=True, semantic_slot=slot, provider="codex", model=_AUTHOR_MODEL
            ):
                raise RuntimeError("public_provider_bridge_exception")
        finally:
            await record_provider_call(
                provider="codex", model=_AUTHOR_MODEL, response=None,
                latency_ms=57_000, retry_class="recovery", outcome="failed",
            )

    monkeypatch.setattr(terminal_mod, "revise_authored_deck", failing_revise)
    job = _job()

    async def scenario():
        await _first_pass_call(job, 19)
        async with repair_round_receipts(job, repair_round=REMAKE_FIRST_ROUND):
            return await terminal_mod.remake_slide_once(**_remake_kwargs(19))

    admitted, outcome = _run(scenario())

    assert (admitted, outcome) == (None, "remake_provider_failed")
    (call,) = _calls(receipts)
    assert call.stage == "repair_remake"
    assert call.outcome == "failed"
    assert call.usage_state == "unavailable"
    assert call.latency_ms == 57_000


def test_the_same_slide_remade_in_both_rounds_gets_two_receipts(monkeypatch):
    """Run 48 remade slide 19 in the remake-first round and again in the
    terminal round. One scope per round with a fresh call sequence would give
    both calls the same key and the store would refuse the second as a
    conflicting replay."""

    receipts = _capture_receipts(monkeypatch)
    _install_remake(monkeypatch)
    job = _job()

    async def scenario():
        await _first_pass_call(job, 19)
        async with repair_round_receipts(job, repair_round=REMAKE_FIRST_ROUND):
            await terminal_mod.remake_slide_once(**_remake_kwargs(19))
        async with repair_round_receipts(job, repair_round=TERMINAL_ROUND):
            await terminal_mod.remake_slide_once(**_remake_kwargs(19))

    _run(scenario())

    first, second = _calls(receipts)
    assert first.stage == second.stage == "repair_remake"
    assert first.receipt_key_hash != second.receipt_key_hash
    first_span, second_span = _spans(receipts)
    assert first_span.receipt_key_hash != second_span.receipt_key_hash
    assert len({r.receipt_key_hash for r in receipts}) == 5


def test_a_replayed_round_reproduces_the_same_keys_and_is_not_double_counted(monkeypatch):
    """A resumed attempt that replays a round must land on the receipts the
    first run wrote: identical keys, which the store deduplicates."""

    receipts = _capture_receipts(monkeypatch)
    _install_remake(monkeypatch)
    job = _job()

    async def one_round():
        async with repair_round_receipts(job, repair_round=REMAKE_FIRST_ROUND):
            await terminal_mod.remake_slide_once(**_remake_kwargs(7))
            await terminal_mod.remake_slide_once(**_remake_kwargs(9))

    async def scenario():
        await _first_pass_call(job, 7)
        await _first_pass_call(job, 9)
        seeded = len(receipts)
        await one_round()
        first_run = [r.receipt_key_hash for r in receipts[seeded:]]
        await one_round()
        return first_run, [r.receipt_key_hash for r in receipts[seeded + len(first_run):]]

    first_run, replay = _run(scenario())

    assert len(first_run) == 3  # two calls and the round's span
    assert len(set(first_run)) == 3
    assert replay == first_run


def test_a_remake_under_a_slide_scope_keeps_its_slot_free_of_the_slide(monkeypatch):
    """The slot shape follows the scope, as ``author_slide_html`` decides it."""

    receipts = _capture_receipts(monkeypatch)
    _install_remake(monkeypatch)
    seen: list[str] = []
    original = terminal_mod.revise_authored_deck

    async def observing_revise(*args, **kwargs):
        seen.append(
            authored_provider_semantic_slot(
                receipt_retry_class="recovery", receipt_attempt_ordinal=2,
                slide_index=(None if active_usage_scope_slide_index() is not None else 3),
            )
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr(terminal_mod, "revise_authored_deck", observing_revise)
    job = _job()

    async def scenario():
        await _first_pass_call(job, 3)
        async with repair_round_receipts(job, repair_round=REMAKE_FIRST_ROUND):
            await terminal_mod.remake_slide_once(**_remake_kwargs(3))

    _run(scenario())

    # A deck-level round scope: the slide travels in the slot.
    assert seen == ["author.recovery.slide-3"]
    assert _calls(receipts)[0].stage == "repair_remake"


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


def test_a_patch_call_records_one_receipt_of_the_patch_stage(monkeypatch):
    receipts = _capture_receipts(monkeypatch)
    monkeypatch.setenv("LLM", "ollama")
    client = _PatchClient()
    monkeypatch.setattr(patch_mod, "get_llm_config", lambda: "config")
    monkeypatch.setattr(patch_mod, "get_client", lambda *, config: client)
    monkeypatch.setattr(patch_mod, "get_model", lambda: "patch-model")
    job = _job()

    async def scenario():
        async with repair_round_receipts(job, repair_round=PATCH_ROUND):
            return await patch_mod.propose_authored_slide_patch(
                b"\x89PNG\r\n\x1a\nrendered-slide",
                _PATCH_DOM,
                {"needs_fix": True, "issues": ["title clips"]},
            )

    result = _run(scenario())

    assert result.ok is True and client.calls == 1
    (call,) = _calls(receipts)
    assert call.job_id == job.id
    assert call.stage == "repair_patch"
    assert call.provider == "ollama"
    assert call.model == "patch-model"
    assert call.retry_class == "initial"
    assert call.usage_state == "reported"
    assert (call.input_tokens, call.output_tokens) == (800, 120)
    (span,) = _spans(receipts)
    assert span.stage == "repair_patch"


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


def test_a_patch_round_keeps_the_keys_its_calls_had_under_the_repair_scope():
    """The patch round calls each slot once, so it needs no round in its keys."""

    assert PATCH_ROUND.round_ordinal is None
    assert PATCH_ROUND.stage == "repair_patch"
    assert (REMAKE_FIRST_ROUND.stage, REMAKE_FIRST_ROUND.round_ordinal) == ("repair_remake", 1)
    assert (TERMINAL_ROUND.stage, TERMINAL_ROUND.round_ordinal) == ("repair_remake", 2)


def test_without_a_durable_job_nothing_is_recorded(monkeypatch):
    receipts = _capture_receipts(monkeypatch)
    _install_remake(monkeypatch)

    async def scenario():
        async with repair_round_receipts(None, repair_round=REMAKE_FIRST_ROUND):
            return await terminal_mod.remake_slide_once(**_remake_kwargs(3))

    admitted, outcome = _run(scenario())

    assert outcome == "remade" and admitted is not None
    assert receipts == []


def test_the_round_scope_is_the_plain_usage_scope(monkeypatch):
    """No second mechanism: the scope it opens is ``provider_usage_scope``."""

    opened: list[dict] = []
    original = provider_usage_scope

    def observing(**kwargs):
        opened.append(dict(kwargs))
        return original(**kwargs)

    monkeypatch.setattr(receipts_mod, "provider_usage_scope", observing)
    job = _job(attempt_number=2)

    async def scenario():
        async with repair_round_receipts(job, repair_round=TERMINAL_ROUND):
            pass

    monkeypatch.setattr(
        "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
        _noop_receipt,
    )
    monkeypatch.setenv("AUTHORED_LOCAL_STAGE_ASYNC_ENABLED", "false")
    _run(scenario())

    assert opened == [
        {
            "job_id": job.id,
            "operation": "authored_generation",
            "stage": "repair_remake",
            "attempt_number": 2,
            "round_ordinal": 2,
        }
    ]


async def _noop_receipt(value):
    return value
