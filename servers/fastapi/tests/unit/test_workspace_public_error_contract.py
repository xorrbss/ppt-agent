from __future__ import annotations

import ast
from pathlib import Path

from api.main import app
from services.workspace_error_registry import (
    WORKSPACE_ERROR_REGISTRY,
    resolve_workspace_error,
)


_FASTAPI_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_ERROR_CONSTRUCTORS = frozenset(
    {
        "WorkspaceServiceError",
        "WorkspaceAPIException",
        "RetryableWorkspaceOperationError",
        "NonRetryableWorkspaceOperationError",
    }
)


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _production_public_error_literals() -> dict[str, list[str]]:
    """Collect literal codes that can cross the workspace service boundary."""

    locations: dict[str, list[str]] = {}
    for relative_root in ("api", "services"):
        for path in (_FASTAPI_ROOT / relative_root).rglob("*.py"):
            tree = ast.parse(
                path.read_text(encoding="utf-8-sig"),
                filename=str(path),
            )
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_name(node) not in _PUBLIC_ERROR_CONSTRUCTORS:
                    continue
                if not node.args:
                    continue
                code_node = node.args[0]
                if not (
                    isinstance(code_node, ast.Constant)
                    and isinstance(code_node.value, str)
                ):
                    continue
                relative_path = path.relative_to(_FASTAPI_ROOT).as_posix()
                locations.setdefault(code_node.value, []).append(
                    f"{relative_path}:{node.lineno}"
                )
    return locations


def test_every_literal_workspace_error_code_is_registered() -> None:
    locations = _production_public_error_literals()
    missing = {
        code: code_locations
        for code, code_locations in locations.items()
        if code not in WORKSPACE_ERROR_REGISTRY
    }
    assert missing == {}


def test_registry_is_closed_and_unknown_internal_codes_are_not_reflected() -> None:
    assert all(
        code == definition.code
        and definition.safe_message.strip()
        and 400 <= definition.status_code <= 599
        for code, definition in WORKSPACE_ERROR_REGISTRY.items()
    )
    fallback = resolve_workspace_error("provider_secret_timeout_987")
    assert fallback == WORKSPACE_ERROR_REGISTRY["workspace_request_failed"]


def test_actual_openapi_action_and_error_envelopes_are_exact() -> None:
    document = app.openapi()
    schemas = document["components"]["schemas"]

    action = schemas["WorkspaceActionResponse"]
    assert action["additionalProperties"] is False
    assert set(action["required"]) == {
        "receipt_id",
        "operation_id",
        "status",
        "timeline",
    }
    assert set(action["properties"]) == {
        "schema_version",
        "receipt_id",
        "operation_id",
        "status",
        "duplicate",
        "timeline",
        "conversation_id",
        "items",
        "next_before_position",
        "event_high_watermark",
        "has_more",
        "revision",
    }
    assert action["properties"]["schema_version"]["const"] == 1
    assert action["properties"]["timeline"] == {
        "$ref": "#/components/schemas/WorkspaceTimelineResponse"
    }

    envelope = schemas["WorkspaceErrorEnvelopeResponse"]
    assert envelope["additionalProperties"] is False
    assert set(envelope["required"]) == {"detail", "error", "request_id"}
    assert set(envelope["properties"]) == {"detail", "error", "request_id"}
    assert envelope["properties"]["error"] == {
        "$ref": "#/components/schemas/WorkspaceErrorDetailResponse"
    }

    detail = schemas["WorkspaceErrorDetailResponse"]
    assert detail["additionalProperties"] is False
    assert set(detail["required"]) == {
        "status_code",
        "detail",
        "code",
        "message",
        "request_id",
        "safe_message",
        "retryable",
    }
    assert set(detail["properties"]) == set(detail["required"])

    endpoint = document["paths"][
        "/api/v1/ppt/workspace/presentations/{presentation_id}/commands"
    ]["post"]
    assert endpoint["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/WorkspaceActionResponse"}

    documented_codes: set[str] = set()
    for definition in WORKSPACE_ERROR_REGISTRY.values():
        response = endpoint["responses"][str(definition.status_code)]
        example = response["content"]["application/json"]["examples"][
            definition.code
        ]["value"]
        documented_codes.add(definition.code)
        assert example == {
            "detail": definition.code,
            "error": {
                "status_code": definition.status_code,
                "detail": definition.code,
                "code": definition.code,
                "message": definition.safe_message,
                "request_id": "request-id",
                "safe_message": definition.safe_message,
                "retryable": definition.retryable,
            },
            "request_id": "request-id",
        }
    assert documented_codes == set(WORKSPACE_ERROR_REGISTRY)
