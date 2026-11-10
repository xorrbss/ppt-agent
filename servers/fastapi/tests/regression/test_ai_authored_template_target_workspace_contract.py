from __future__ import annotations

from fastapi import FastAPI

from api.v1.ppt.endpoints import workspace as workspace_endpoint


def test_openapi_requires_idempotency_header_and_excludes_it_from_body():
    app = FastAPI()
    app.include_router(workspace_endpoint.WORKSPACE_ROUTER)

    operation = app.openapi()["paths"][
        "/workspace/presentations/{source_presentation_id}/template-applications"
    ]["post"]
    header = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )

    assert operation["responses"].get("202") is not None
    assert header["in"] == "header"
    assert header["required"] is True
    request_schema = app.openapi()["components"]["schemas"][
        "WorkspaceTemplateApplicationRequest"
    ]
    assert set(request_schema["required"]) == {
        "source_revision",
        "prompt",
        "authoredStyle",
    }
    assert "idempotency_key" not in request_schema["properties"]
    assert "idempotencyKey" not in request_schema["properties"]
    response_schema = app.openapi()["components"]["schemas"][
        "WorkspaceTemplateApplicationResponse"
    ]
    assert {
        "source_presentation_id",
        "source_revision",
        "target_presentation_id",
        "conversation_id",
        "first_user_message_id",
        "operation_id",
        "status",
        "operation_state",
        "timeline",
    }.issubset(response_schema["required"])
