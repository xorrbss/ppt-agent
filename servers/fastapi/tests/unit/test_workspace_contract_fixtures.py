"""Wire-contract parity checks for the shared Workspace v1 fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api.main import app
from models.workspace import (
    WorkspaceActionResponse,
    WorkspaceAssetResponse,
    WorkspaceBootstrapRequest,
    WorkspaceBootstrapResponse,
    WorkspaceCapabilitiesResponse,
    WorkspaceCommandRequest,
    WorkspaceEventPageResponse,
    WorkspaceOperationActionRequest,
    WorkspaceTimelineResponse,
)
from pydantic import BaseModel
from services.workspace_error_registry import WORKSPACE_ERROR_REGISTRY


FIXTURE_ROOT = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "chat-first-workspace"
    / "v1"
)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _assert_exact_model(model: type[BaseModel], value: dict[str, Any]) -> None:
    parsed = model.model_validate(value)
    assert parsed.model_dump(mode="json") == value


def _response_ref(document: dict[str, Any], path: str, method: str) -> str:
    return document["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]


def _request_ref(document: dict[str, Any], path: str, method: str) -> str:
    return document["paths"][path][method]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]


def test_workspace_contract_fixtures_are_versioned_and_complete():
    names = {
        "bootstrap-and-assets.json",
        "timeline-projection.json",
        "commands-and-receipts.json",
        "errors-and-runtime.json",
    }
    assert {path.name for path in FIXTURE_ROOT.glob("*.json")} == names
    assert all(_fixture(name)["schema_version"] == 1 for name in names)


def test_fixture_examples_round_trip_through_public_pydantic_models():
    bootstrap = _fixture("bootstrap-and-assets.json")
    commands = _fixture("commands-and-receipts.json")
    timeline = _fixture("timeline-projection.json")
    runtime = _fixture("errors-and-runtime.json")

    examples: tuple[tuple[type[BaseModel], dict[str, Any]], ...] = (
        (WorkspaceBootstrapRequest, bootstrap["bootstrap_request"]),
        (WorkspaceAssetResponse, bootstrap["asset_response"]),
        (WorkspaceBootstrapResponse, bootstrap["bootstrap_response"]),
        (WorkspaceTimelineResponse, timeline["timeline"]),
        (WorkspaceEventPageResponse, timeline["event_page"]),
        (WorkspaceCommandRequest, commands["command_request"]),
        (WorkspaceOperationActionRequest, commands["action_request"]),
        (WorkspaceActionResponse, commands["action_response"]),
        (WorkspaceActionResponse, commands["duplicate_action_response"]),
    )
    for model, example in examples:
        _assert_exact_model(model, example)
    for example in runtime["capability_responses"].values():
        _assert_exact_model(WorkspaceCapabilitiesResponse, example)


def test_bootstrap_fixture_preserves_snapshot_and_receipt_before_asset_wait():
    fixture = _fixture("bootstrap-and-assets.json")
    request = fixture["bootstrap_request"]
    response = fixture["bootstrap_response"]
    timeline = response["timeline"]
    first, operation = timeline["items"]
    attachment = first["attachments"][0]
    policy = fixture["asset_policy"]

    assert first["role"] == "user"
    assert first["content"] == request["prompt"]
    assert first["position"] == 1 < operation["position"]
    assert response["first_user_message_id"] == first["id"]
    assert response["operation_id"] == operation["operation_id"]
    assert response["receipt_id"] == response["operation_id"]
    assert response["operation_state"] == operation["status"] == "waiting_for_assets"
    assert attachment["id"] == request["asset_ids"][0]
    assert attachment["status"] in policy["wait_states"]
    assert attachment["current_state"] == fixture["asset_response"]["status"]
    assert attachment["name"] == fixture["asset_response"]["file_name"]
    assert attachment["sha256"] == fixture["asset_response"]["sha256"]
    assert attachment["download_action"] == "download"
    assert attachment["preview_action"] == "preview"
    assert set(policy["wait_states"]) <= set(policy["bootstrap_allowed"])
    assert set(policy["block_states"]).isdisjoint(policy["bootstrap_allowed"])


def test_timeline_fixture_has_monotonic_cursors_and_complete_operation_card():
    fixture = _fixture("timeline-projection.json")
    timeline = fixture["timeline"]
    positions = [item["position"] for item in timeline["items"]]
    operation = next(
        item for item in timeline["items"] if item["anchor_type"] == "operation"
    )
    message = next(
        item for item in timeline["items"] if item["anchor_type"] == "message"
    )
    attachment = message["attachments"][0]
    events = operation["events"]

    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert [int(event["cursor"]) for event in events] == [1, 2, 3]
    assert int(timeline["event_high_watermark"]) >= int(events[-1]["cursor"])
    assert fixture["event_page"]["event_high_watermark"] == events[-1]["cursor"]
    assert operation["version"] == operation["operation_version"]
    assert operation["actor"] == {"type": "user", "label": "You"}
    assert operation["created_at"] and operation["updated_at"]
    assert operation["target"]["presentation_id"]
    assert operation["activity_summary"]
    assert {action["id"] for action in operation["allowed_actions"]} == {
        "approve",
        "cancel",
    }
    assert attachment["status"] == "ready"
    assert attachment["current_state"] == "deleted"
    assert attachment["error"] == attachment["safe_failure"]["failure_code"]
    assert attachment["download_url"] is None
    assert attachment["preview_url"] is None
    assert fixture["projection_invariants"] == {
        "top_level_order": "position_only",
        "pagination_cursor": "next_before_position",
        "event_replay_cursor": "event_high_watermark",
        "event_projection": "update_anchor_card_only",
        "message_pagination_and_event_replay_are_independent": True,
        "canonical_decimal_event_cursor": True,
    }


def test_action_receipt_exposes_durable_duplicate_and_legacy_flattening():
    fixture = _fixture("commands-and-receipts.json")
    command = fixture["command_request"]
    action = fixture["action_request"]
    first = fixture["action_response"]
    duplicate = fixture["duplicate_action_response"]
    publication = fixture["publication"]

    assert command["command_type"] == "slide.edit"
    assert action["action"] == "approve"
    assert action["expected_revision"] == command["expected_revision"]
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    for key in ("receipt_id", "operation_id", "status", "timeline"):
        assert duplicate[key] == first[key]
    for receipt in (first, duplicate):
        timeline = receipt["timeline"]
        for field in (
            "conversation_id",
            "items",
            "next_before_position",
            "event_high_watermark",
            "has_more",
            "revision",
        ):
            assert receipt[field] == timeline[field]
    assert publication["checkpoint_increments_revision"] is False
    assert publication["final_publish_increments_revision_once"] is True
    assert len(publication["immutable_artifact_sha256"]) == 64


def test_runtime_capabilities_error_registry_and_privacy_are_exact():
    fixture = _fixture("errors-and-runtime.json")
    capabilities = fixture["capability_responses"]
    expected_errors = [
        {
            "code": definition.code,
            "status_code": definition.status_code,
            "safe_message": definition.safe_message,
            "retryable": definition.retryable,
        }
        for definition in WORKSPACE_ERROR_REGISTRY.values()
    ]

    enabled = capabilities["sqlite_enabled"]
    read_only = capabilities["postgresql_read_only"]
    blocked = capabilities["mysql_blocked"]
    assert enabled["reads_supported"] and enabled["writes_supported"]
    assert enabled["timeline"] and enabled["commands"] and enabled["events"]
    assert read_only["reads_supported"] and not read_only["writes_supported"]
    assert read_only["timeline"] and read_only["events"]
    assert not read_only["commands"] and not read_only["commands_enabled"]
    assert read_only["kill_switch_active"] is True
    assert blocked["reason"] == "chat_first_runtime_unsupported"
    assert not any(
        blocked[field]
        for field in (
            "supported",
            "enabled",
            "timeline",
            "commands",
            "events",
            "reads_supported",
            "writes_supported",
            "commands_enabled",
        )
    )
    assert fixture["errors"] == expected_errors
    forbidden = set(fixture["privacy"]["telemetry_forbidden_fields"])
    assert {"prompt", "message_content", "trace", "stack_trace"} <= forbidden
    assert "prompt" in fixture["privacy"]["public_share_forbidden_fields"]


def test_generated_openapi_matches_fixture_shapes_and_endpoint_refs():
    bootstrap = _fixture("bootstrap-and-assets.json")
    commands = _fixture("commands-and-receipts.json")
    timeline = _fixture("timeline-projection.json")
    runtime = _fixture("errors-and-runtime.json")
    document = app.openapi()
    schemas = document["components"]["schemas"]

    timeline_items = timeline["timeline"]["items"]
    message = next(item for item in timeline_items if item["anchor_type"] == "message")
    operation = next(
        item for item in timeline_items if item["anchor_type"] == "operation"
    )
    shape_examples = {
        "WorkspaceBootstrapRequest": bootstrap["bootstrap_request"],
        "WorkspaceAssetResponse": bootstrap["asset_response"],
        "WorkspaceBootstrapResponse": bootstrap["bootstrap_response"],
        "WorkspaceTimelineResponse": timeline["timeline"],
        "WorkspaceCommandRequest": commands["command_request"],
        "WorkspaceOperationActionRequest": commands["action_request"],
        "WorkspaceActionResponse": commands["action_response"],
        "WorkspaceCapabilitiesResponse": runtime["capability_responses"][
            "sqlite_enabled"
        ],
        "WorkspaceMessageTimelineItem": message,
        "WorkspaceTimelineAttachmentResponse": message["attachments"][0],
        "WorkspaceTimelineAttachmentFailureResponse": message["attachments"][0][
            "safe_failure"
        ],
        "WorkspaceOperationTimelineItem": operation,
        "WorkspaceOperationEventResponse": operation["events"][0],
        "WorkspaceOperationActorResponse": operation["actor"],
        "WorkspaceOperationTargetResponse": operation["target"],
        "WorkspaceAllowedActionResponse": operation["allowed_actions"][0],
    }
    for schema_name, example in shape_examples.items():
        schema = schemas[schema_name]
        assert schema.get("additionalProperties") is False
        assert set(schema["properties"]) == set(example)
        assert set(schema.get("required", ())) <= set(example)
        if "schema_version" in example:
            assert schema["properties"]["schema_version"]["const"] == 1

    capabilities_path = "/api/v1/ppt/workspace/capabilities"
    asset_path = "/api/v1/ppt/workspace/assets/{asset_id}"
    bootstrap_path = "/api/v1/ppt/workspace/presentations/{presentation_id}/bootstrap"
    timeline_path = "/api/v1/ppt/workspace/presentations/{presentation_id}/timeline"
    command_path = "/api/v1/ppt/workspace/presentations/{presentation_id}/commands"
    action_path = "/api/v1/ppt/workspace/operations/{operation_id}/actions"
    assert _response_ref(document, capabilities_path, "get").endswith(
        "/WorkspaceCapabilitiesResponse"
    )
    assert _response_ref(document, asset_path, "get").endswith(
        "/WorkspaceAssetResponse"
    )
    assert _request_ref(document, bootstrap_path, "post").endswith(
        "/WorkspaceBootstrapRequest"
    )
    assert _response_ref(document, bootstrap_path, "post").endswith(
        "/WorkspaceBootstrapResponse"
    )
    assert _response_ref(document, timeline_path, "get").endswith(
        "/WorkspaceTimelineResponse"
    )
    assert _request_ref(document, command_path, "post").endswith(
        "/WorkspaceCommandRequest"
    )
    assert _response_ref(document, command_path, "post").endswith(
        "/WorkspaceActionResponse"
    )
    assert _request_ref(document, action_path, "post").endswith(
        "/WorkspaceOperationActionRequest"
    )
    assert _response_ref(document, action_path, "post").endswith(
        "/WorkspaceActionResponse"
    )
