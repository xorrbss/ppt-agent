from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator

from api.error_handling import install_api_error_handling
from models.api_error_model import APIErrorModel


class _RevisionPayload(BaseModel):
    revision: int


class _SealedPayload(BaseModel):
    digest: str

    @model_validator(mode="after")
    def reject_stale_digest(self):
        if self.digest == "stale":
            raise ValueError("stale checkpoint digest")
        return self


def _test_app() -> FastAPI:
    app = FastAPI()
    install_api_error_handling(app)

    @app.get("/revision-conflict")
    async def revision_conflict():
        raise HTTPException(
            status_code=409,
            detail="template_v2_revision_conflict",
        )

    @app.post("/schema")
    async def validate_schema(payload: _RevisionPayload):
        return payload

    @app.post("/sealed-schema")
    async def validate_sealed_schema(payload: _SealedPayload):
        return payload

    @app.get("/unexpected")
    async def unexpected():
        raise RuntimeError("provider secret response")

    @app.get("/uploaded-reference-failure")
    async def uploaded_reference_failure():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "uploaded_reference_extraction_failed",
                "message": "The uploaded reference file could not be read.",
                "filename": "proposal.pdf",
                "retryable": True,
            },
        )

    return app


def test_http_error_preserves_detail_and_adds_structured_request_id():
    client = TestClient(_test_app())

    response = client.get(
        "/revision-conflict",
        headers={"X-Correlation-ID": "client-correlation-123"},
    )

    assert response.status_code == 409
    assert response.headers["X-Request-ID"] == "client-correlation-123"
    assert response.json() == {
        "detail": "template_v2_revision_conflict",
        "error": {
            "status_code": 409,
            "detail": "template_v2_revision_conflict",
            "code": "template_v2_revision_conflict",
            "message": "template_v2_revision_conflict",
            "request_id": "client-correlation-123",
        },
        "request_id": "client-correlation-123",
    }


def test_schema_error_uses_same_envelope_and_generated_request_id():
    client = TestClient(_test_app())

    response = client.post("/schema", json={"revision": "not-an-integer"})

    assert response.status_code == 422
    request_id = response.headers["X-Request-ID"]
    body = response.json()
    assert request_id
    assert body["request_id"] == request_id
    assert body["detail"] == body["error"]["detail"]
    assert body["error"]["status_code"] == 422
    assert body["error"]["code"] == "request_validation_error"
    assert body["error"]["message"] == "Request validation failed"


def test_model_validator_error_is_a_serializable_clear_422():
    client = TestClient(_test_app(), raise_server_exceptions=False)

    response = client.post("/sealed-schema", json={"digest": "stale"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "request_validation_error"
    assert body["detail"] == [
        {
            "type": "value_error",
            "loc": ["body"],
            "msg": "Value error, stale checkpoint digest",
        }
    ]
    assert "stale" not in str(body["detail"][0].get("input", ""))


def test_unhandled_error_does_not_expose_internal_exception_text():
    client = TestClient(_test_app(), raise_server_exceptions=False)

    response = client.get("/unexpected")

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert response.json()["error"]["code"] == "internal_server_error"
    assert "provider secret response" not in response.text
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_structured_http_detail_is_promoted_to_the_error_envelope():
    client = TestClient(_test_app())

    response = client.get("/uploaded-reference-failure")

    assert response.status_code == 422
    assert response.json()["error"] == {
        "status_code": 422,
        "detail": {
            "code": "uploaded_reference_extraction_failed",
            "message": "The uploaded reference file could not be read.",
            "filename": "proposal.pdf",
            "retryable": True,
        },
        "code": "uploaded_reference_extraction_failed",
        "message": "The uploaded reference file could not be read.",
        "filename": "proposal.pdf",
        "request_id": response.json()["request_id"],
        "retryable": True,
    }


def test_async_error_payload_can_retain_original_generation_request_id():
    error = APIErrorModel.from_exception(
        HTTPException(
            status_code=422,
            detail="template_v2_generation_invalid",
        ),
        request_id="generation-request-42",
    )

    assert error.model_dump(mode="json") == {
        "status_code": 422,
        "detail": "template_v2_generation_invalid",
        "code": "template_v2_generation_invalid",
        "message": "template_v2_generation_invalid",
        "request_id": "generation-request-42",
    }
