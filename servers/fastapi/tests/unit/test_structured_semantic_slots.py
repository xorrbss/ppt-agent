"""Slot naming is what keeps two runs of one stage apart."""

from utils.structured_semantic_slots import structured_semantic_slot


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
