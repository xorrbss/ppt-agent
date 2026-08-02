"""Stable, implementation-independent invariants for Workspace v1 fixtures."""

from __future__ import annotations

import json
from pathlib import Path


FIXTURE_ROOT = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "chat-first-workspace"
    / "v1"
)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_workspace_contract_fixtures_are_versioned_and_complete():
    names = {
        "bootstrap-and-assets.json",
        "timeline-projection.json",
        "commands-and-receipts.json",
        "errors-and-runtime.json",
    }
    assert {path.name for path in FIXTURE_ROOT.glob("*.json")} == names
    assert all(_fixture(name)["schema_version"] == 1 for name in names)


def test_bootstrap_fixture_preserves_first_user_message_before_waiting():
    fixture = _fixture("bootstrap-and-assets.json")
    bootstrap = fixture["bootstrap"]
    first = bootstrap["first_user_message"]
    anchor = bootstrap["operation_anchor"]
    policy = fixture["asset_policy"]

    assert first["role"] == "user"
    assert first["content"]
    assert first["position"] == 1
    assert anchor["position"] > first["position"]
    assert bootstrap["operation_state"] == "waiting_for_assets"
    assert first["attachments"][0]["status"] in policy["wait_states"]
    assert set(policy["wait_states"]) <= set(policy["bootstrap_allowed"])
    assert set(policy["block_states"]).isdisjoint(policy["bootstrap_allowed"])
    assert bootstrap["duplicate_bootstrap_result"] == {
        "conversation_id": bootstrap["conversation_id"],
        "first_user_message_id": first["id"],
        "operation_id": bootstrap["operation_id"],
    }


def test_timeline_fixture_keeps_message_pagination_and_event_replay_separate():
    fixture = _fixture("timeline-projection.json")
    positions = [item["position"] for item in fixture["top_level_items"]]
    operation = fixture["operation_card"]
    sequences = [event["sequence"] for event in operation["events"]]

    assert positions == sorted(positions)
    assert positions[0] == 1
    assert len(set(positions)) == len(positions)
    assert sequences == list(range(1, len(sequences) + 1))
    assert fixture["pagination"]["next_message_cursor"] != fixture["event_replay"]["high_watermark_cursor"]
    assert fixture["projection_invariants"] == {
        "top_level_order": "position_only",
        "event_projection": "update_anchor_card_only",
        "message_pagination_and_event_replay_are_independent": True,
    }
    assert operation["anchor_message_id"] == fixture["top_level_items"][1]["message_id"]


def test_command_receipt_and_publication_invariants_are_explicit():
    fixture = _fixture("commands-and-receipts.json")
    command = fixture["command"]
    action = fixture["action"]
    receipt = fixture["receipt"]
    publication = fixture["publication"]

    assert command["command_type"] == "slide.edit"
    assert command["operation_id"] == action["operation_id"] == receipt["operation_id"]
    assert action["action_id"] == receipt["action_id"]
    assert action["idempotency_key"] == receipt["idempotency_key"]
    assert receipt["duplicate_result"] == "same_receipt"
    assert publication["checkpoint_increments_revision"] is False
    assert publication["final_publish_increments_revision_once"] is True
    assert len(publication["immutable_artifact_sha256"]) == 64


def test_runtime_is_fail_closed_and_privacy_fields_are_not_telemetry():
    fixture = _fixture("errors-and-runtime.json")
    capabilities = fixture["runtime_capabilities"]
    errors = {item["code"]: item for item in fixture["errors"]}

    assert capabilities["sqlite"]["chat_first_workspace"] is True
    assert capabilities["postgresql"]["chat_first_workspace"] is True
    assert capabilities["mysql"] == {
        "chat_first_workspace": False,
        "mode": "blocked",
        "error_code": "chat_first_runtime_unsupported",
    }
    assert {"revision_conflict", "event_cursor_expired", "operation_dead_lettered"} <= set(errors)
    forbidden = set(fixture["privacy"]["telemetry_forbidden_fields"])
    assert {"prompt", "message_content", "trace", "stack_trace"} <= forbidden
    assert "prompt" in fixture["privacy"]["public_share_forbidden_fields"]
