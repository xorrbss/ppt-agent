"""Every slide reviewed in the one deck-level ``vqa`` scope is its own call.

Regression cover for a run that authored all 35 slides and then published none.
``vqa`` opens a single usage scope for the whole deck
(``services/authored_presentation_generation.py``), so the enclosing scope
contributes ``slide_index = -1`` to every call inside it. The critique and patch
call sites took the default structured slot, which made all 35 slides reserve
the *same* logical call: the first slide won, and slides 2..35 were refused as
replays (``public_provider_inflight_replay_unsupported`` while the first was
open, ``public_provider_terminal_replay_unsupported`` after it closed) for a
provider with no idempotency support. Vision QA repairs the verbatim-text
readback violations of a source restyle, so 34 unrepaired slides failed the deck.
"""

import asyncio

import pytest

from services.generation_core.authoring import author_vision_qa as author_vision_qa_mod
from services.generation_core.authoring.author_vision_qa import SlideCritique
from utils.structured_semantic_slots import (
    VQA_CRITIQUE_PREFIX,
    VQA_PATCH_PREFIX,
    deck_stage_slot_suffix,
    structured_semantic_slot,
)


def _run(coro):
    return asyncio.run(coro)


def _reset_cache() -> None:
    author_vision_qa_mod._vqa_cache.clear()
    author_vision_qa_mod._vqa_inflight.clear()
    author_vision_qa_mod._vqa_sem = None


@pytest.fixture(autouse=True)
def _no_shared_critique_cache():
    _reset_cache()
    yield
    _reset_cache()


def test_each_reviewed_slide_carries_its_own_slot_identity(monkeypatch):
    seen = []

    async def fake_review(image, slide_context=None, **kwargs):
        seen.append((kwargs.get("slide_index"), kwargs.get("slot_run", "")))
        return SlideCritique(needs_fix=False)

    monkeypatch.setattr(author_vision_qa_mod, "critique_slide_image", fake_review)

    _run(
        author_vision_qa_mod.critique_authored(
            [b"png-a", b"png-b", b"png-c"],
            contexts=["a", "b", "c"],
            slide_indices=[4, 9, 30],
        )
    )

    assert seen == [(4, ""), (9, ""), (30, "")]
    assert len(set(seen)) == 3


def test_a_second_review_round_does_not_reopen_the_first(monkeypatch):
    """``authored_quality_review_exact`` reviews candidates again in one scope."""

    seen = []

    async def fake_review(image, slide_context=None, **kwargs):
        seen.append((kwargs.get("slide_index"), kwargs.get("slot_run", "")))
        return SlideCritique(needs_fix=False)

    monkeypatch.setattr(author_vision_qa_mod, "critique_slide_image", fake_review)

    _run(
        author_vision_qa_mod.critique_authored(
            [b"png-a"], contexts=["a"], slide_indices=[4]
        )
    )
    _reset_cache()
    _run(
        author_vision_qa_mod.critique_authored(
            [b"png-a"], contexts=["a"], slide_indices=[4], critique_round="final"
        )
    )

    assert seen == [(4, ""), (4, "final")]
    assert len(set(seen)) == 2


def test_a_deck_of_slides_produces_no_duplicate_reservation_slots():
    """The identity the admission ledger reserves against, for a whole deck.

    Four calls per slide -- critique, its final round, the patch, and the
    patch's validation retry -- must all be distinct across 35 slides.
    """

    slots = {
        structured_semantic_slot(
            validation_attempt=0,
            parse_attempt=0,
            prefix=prefix,
            suffix=deck_stage_slot_suffix(slide, run=run),
        )
        for slide in range(35)
        for prefix, run in (
            (VQA_CRITIQUE_PREFIX, ""),
            (VQA_CRITIQUE_PREFIX, "final"),
            (VQA_PATCH_PREFIX, ""),
            (VQA_PATCH_PREFIX, "patch-retry"),
        )
    }

    assert len(slots) == 35 * 4


def test_a_caller_without_slide_indices_keeps_the_previous_shape(monkeypatch):
    """Benchmarks and single-slide callers pass none and must stay unchanged."""

    seen = []

    async def fake_review(image, slide_context=None, **kwargs):
        seen.append(kwargs)
        return SlideCritique(needs_fix=False)

    monkeypatch.setattr(author_vision_qa_mod, "critique_slide_image", fake_review)

    _run(author_vision_qa_mod.critique_authored([b"png"], contexts=["only"]))

    assert seen == [{"slide_index": None, "slot_run": ""}]
