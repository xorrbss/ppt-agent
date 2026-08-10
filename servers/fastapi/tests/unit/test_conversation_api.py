from fastapi import FastAPI

from api.v1.ppt.endpoints.conversations import CONVERSATION_ROUTER


def test_canonical_conversation_openapi_has_one_structured_transport():
    app = FastAPI()
    app.include_router(CONVERSATION_ROUTER)
    openapi = app.openapi()

    request_schema = openapi["components"]["schemas"][
        "ConversationMessageCreateRequest"
    ]
    assert "generation_mode" not in request_schema["properties"]
    assert "presentation_type" not in request_schema["properties"]
    assert set(request_schema["properties"]) == {
        "content",
        "idempotency_key",
        "expected_revision",
        "command_type",
        "operation_context",
        "context",
        "asset_ids",
    }
    assert request_schema["properties"]["command_type"]["default"] == (
        "presentation.edit"
    )
    assert request_schema["properties"]["asset_ids"]["maxItems"] == 100
    assert request_schema["properties"]["asset_ids"]["uniqueItems"] is True

    paths = openapi["paths"]
    assert "/conversations" in paths
    assert "/conversations/{conversation_id}/timeline" in paths
    assert "/conversations/{conversation_id}/messages" in paths
    assert "/conversations/{conversation_id}/events" in paths
    assert "/conversations/{conversation_id}/runs/{run_id}/actions" in paths
    assert "/conversations/{conversation_id}/assets" in paths
