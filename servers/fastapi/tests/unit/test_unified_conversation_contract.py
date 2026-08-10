"""Focused parity tests for the versioned Unified Conversation event contract."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError

from services.conversation_event_contract import (
    DURABLE_EVENT_TYPES,
    TRANSIENT_EVENT_TYPES,
    ConversationEventType,
    UnifiedConversationEvent,
    build_conversation_event,
    canonical_event_cursor,
    recover_assistant_checkpoints,
)


FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "unified-conversation"
    / "v1"
    / "event-envelope.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _reconnect_fixture() -> dict:
    return json.loads(FIXTURE.with_name("reconnect-sequence.json").read_text(encoding="utf-8"))


def test_versioned_fixture_examples_round_trip_through_public_event_model():
    contract = _fixture()
    assert contract["schema_version"] == 1
    for example in contract["examples"]:
        parsed = UnifiedConversationEvent.model_validate(example)
        assert parsed.model_dump(mode="json") == example


def test_event_type_partition_matches_versioned_fixture_and_requires_replay_rules():
    contract = _fixture()
    invariants = contract["x-invariants"]
    assert {item.value for item in DURABLE_EVENT_TYPES} == set(invariants["durable_types"])
    assert {item.value for item in TRANSIENT_EVENT_TYPES} == set(invariants["transient_types"])
    assert {item.value for item in ConversationEventType} == set(contract["properties"]["type"]["enum"])
    assert set(contract["x-data-requirements"]) == {item.value for item in ConversationEventType}
    assert "event_high_watermark" in contract["x-replay"]["pagination"]
    assert "not silently accepted" in contract["x-replay"]["retention"]


@pytest.mark.parametrize("invalid", ["", "01", "+1", "cursor:1", "１"])
def test_workspace_compatible_cursors_are_canonical_decimal_strings(invalid: str):
    with pytest.raises(ValueError, match="canonical"):
        canonical_event_cursor(invalid)
    assert canonical_event_cursor("0") == "0"
    assert canonical_event_cursor(42) == "42"


def test_durable_checkpoint_reconnects_without_replaying_transient_tokens():
    event = build_conversation_event(
        event_id="checkpoint-44",
        event_type="assistant.checkpoint",
        conversation_id="11111111-1111-4111-8111-111111111111",
        run_id="44444444-4444-4444-8444-444444444444",
        message_id="55555555-5555-4555-8555-555555555555",
        cursor="44",
        checkpoint_cursor="44",
        data={"content": "Recoverable answer."},
    )
    assert event.durability == "durable"
    assert event.cursor == event.checkpoint_cursor == "44"

    delta = build_conversation_event(
        event_id="delta-live",
        event_type="assistant.delta",
        conversation_id=event.conversation_id,
        run_id=event.run_id,
        message_id=event.message_id,
        checkpoint_cursor=event.cursor,
        data={"delta": " live token"},
    )
    assert delta.durability == "transient"
    assert delta.cursor is None
    assert delta.checkpoint_cursor == "44"


def test_reconnect_fixture_recovers_assistant_checkpoint_and_run_state_from_cursor():
    fixture = _reconnect_fixture()
    events = [UnifiedConversationEvent.model_validate(value) for value in fixture["events"]]
    replay = [
        event
        for event in events
        if event.cursor is not None
        and int(event.cursor) > int(fixture["reconnect_after_cursor"])
    ]
    next_cursor, content = recover_assistant_checkpoints(
        replay, after_cursor=fixture["reconnect_after_cursor"]
    )
    assert next_cursor == fixture["expected_recovery"]["next_cursor"]
    assert content == {
        uuid.UUID(fixture["expected_recovery"]["assistant_message_id"]): (
            fixture["expected_recovery"]["assistant_content"]
        )
    }
    assert replay[-1].data["status"] == fixture["expected_recovery"]["run_status"]


def test_contract_fails_closed_for_wrong_durability_cursors_and_private_data():
    example = _fixture()["examples"][0]
    with pytest.raises(ValidationError, match="durable events require"):
        UnifiedConversationEvent.model_validate({**example, "cursor": None})
    with pytest.raises(ValidationError, match="must be transient"):
        UnifiedConversationEvent.model_validate(
            {**_fixture()["examples"][1], "durability": "durable"}
        )
    with pytest.raises(ValidationError, match="server-private"):
        UnifiedConversationEvent.model_validate(
            {**example, "data": {"tool": {"raw_provider_response": "secret"}}}
        )
    checkpoint = _fixture()["examples"][1]
    with pytest.raises(ValidationError, match="public text limit"):
        UnifiedConversationEvent.model_validate(
            {
                **checkpoint,
                "type": "assistant.checkpoint",
                "durability": "durable",
                "cursor": "44",
                "data": {"content": "x" * 100_001},
            }
        )


def test_presentation_publication_requires_the_exact_published_revision():
    example = _fixture()["examples"][2]
    assert UnifiedConversationEvent.model_validate(example).data["revision"] == 8
    with pytest.raises(ValidationError, match="requires presentation_id"):
        UnifiedConversationEvent.model_validate(
            {**example, "presentation_id": None}
        )
