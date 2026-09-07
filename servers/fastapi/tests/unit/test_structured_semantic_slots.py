"""Slot naming is what keeps two runs of one stage apart."""

from utils.structured_semantic_slots import (
    VQA_CRITIQUE_PREFIX,
    VQA_PATCH_PREFIX,
    deck_stage_slot_suffix,
    structured_semantic_slot,
)


def test_each_repair_round_gets_its_own_slot():
    assert structured_semantic_slot(validation_attempt=0, parse_attempt=0) == "structured.initial"
    assert structured_semantic_slot(validation_attempt=0, parse_attempt=1) == "structured.parse-repair"
    assert structured_semantic_slot(validation_attempt=1, parse_attempt=0) == "structured.schema-repair"
    assert (
        structured_semantic_slot(validation_attempt=1, parse_attempt=2)
        == "structured.schema-parse-repair"
    )


def test_suffix_separates_a_second_run_of_the_same_stage():
    first = structured_semantic_slot(validation_attempt=0, parse_attempt=0)
    retry = structured_semantic_slot(validation_attempt=0, parse_attempt=0, suffix=".brief-retry")

    assert retry == "structured.initial.brief-retry"
    assert retry != first


def test_the_suffix_reaches_every_repair_round():
    slots = {
        structured_semantic_slot(
            validation_attempt=validation, parse_attempt=parse, suffix=".brief-retry"
        )
        for validation in (0, 1)
        for parse in (0, 1)
    }

    assert len(slots) == 4
    assert all(slot.endswith(".brief-retry") for slot in slots)


def test_a_deck_level_stage_puts_the_slide_in_the_slot():
    """``repair`` and ``vqa`` share one scope across every slide.

    Their enclosing usage scope names no slide, so without this the second
    slide reserves the first slide's logical call and is refused as a replay.
    """

    first = structured_semantic_slot(
        validation_attempt=0,
        parse_attempt=0,
        prefix=VQA_CRITIQUE_PREFIX,
        suffix=deck_stage_slot_suffix(1),
    )
    last = structured_semantic_slot(
        validation_attempt=0,
        parse_attempt=0,
        prefix=VQA_CRITIQUE_PREFIX,
        suffix=deck_stage_slot_suffix(34),
    )

    assert first == "vqa-critique.initial.slide-1"
    assert last == "vqa-critique.initial.slide-34"
    assert first != last


def test_a_scope_that_names_the_slide_needs_no_suffix():
    assert deck_stage_slot_suffix(None) == ""


def test_the_two_vqa_call_sites_never_share_a_slot():
    critique = structured_semantic_slot(
        validation_attempt=0,
        parse_attempt=0,
        prefix=VQA_CRITIQUE_PREFIX,
        suffix=deck_stage_slot_suffix(7),
    )
    patch = structured_semantic_slot(
        validation_attempt=0,
        parse_attempt=0,
        prefix=VQA_PATCH_PREFIX,
        suffix=deck_stage_slot_suffix(7),
    )

    assert critique != patch


def test_a_second_run_of_one_call_site_on_one_slide_is_separated():
    """The patch validation retry and the final critique round.

    Both restart the structured loop, so they land on ``initial`` again and
    would otherwise reopen the row their first run left terminal.
    """

    patch = deck_stage_slot_suffix(7)
    patch_retry = deck_stage_slot_suffix(7, run="patch-retry")
    final_critique = deck_stage_slot_suffix(7, run="final")

    assert patch == ".slide-7"
    assert patch_retry == ".slide-7.patch-retry"
    assert len({patch, patch_retry, final_critique}) == 3


def test_every_slot_stays_inside_the_admission_charset():
    """``_normalize_semantic_slot`` rejects anything outside this set."""

    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    for prefix in (VQA_CRITIQUE_PREFIX, VQA_PATCH_PREFIX):
        for run in ("", "patch-retry", "final"):
            slot = structured_semantic_slot(
                validation_attempt=0,
                parse_attempt=0,
                prefix=prefix,
                suffix=deck_stage_slot_suffix(34, run=run),
            )
            assert set(slot) <= allowed
            assert len(slot) <= 128
