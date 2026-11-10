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


from utils.structured_semantic_slots import (
    VQA_CRITIQUE_PREFIX,
    VQA_PATCH_PREFIX,
    deck_stage_slot_suffix,
    structured_semantic_slot,
)


def _run(coro):
    return asyncio.run(coro)


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
