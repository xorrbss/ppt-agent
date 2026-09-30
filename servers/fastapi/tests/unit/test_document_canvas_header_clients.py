"""HTTP and MCP coverage for the document canvas reader negotiation."""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mcp_server
from api.v1.ppt.endpoints import presentation_crud
from models.document_canvas import (
    DOCUMENT_CANVAS_CONTRACT_VERSION,
    DOCUMENT_CANVAS_CONTRACTS_HEADER,
)
from models.presentation_with_slides import PresentationWithSlides
from services.authored_canvas import merge_presentation_canvas
from services.document_canvas import document_canvas_for_orientation


def test_mcp_hook_advertises_canvas_contract_on_every_request(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_http_headers", lambda include=None: {})
    for path in ("/api/v1/ppt/presentation/fixture", "/api/v1/ppt/presentation/all"):
        request = httpx.Request("GET", f"http://127.0.0.1:8000{path}")
        asyncio.run(mcp_server.attach_request_auth_headers(request))
        assert request.headers[DOCUMENT_CANVAS_CONTRACTS_HEADER] == DOCUMENT_CANVAS_CONTRACT_VERSION


def test_python_presentation_readers_use_shared_contract_constants():
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    for file in scripts.rglob("*.py"):
        source = file.read_text(encoding="utf-8")
        if "X-Document-Canvas-Contracts" in source:
            assert "DOCUMENT_CANVAS_CONTRACTS_HEADER" in source, file
            assert "DOCUMENT_CANVAS_CONTRACT_VERSION" in source, file


def test_document_canvas_deck_requires_capable_http_reader(monkeypatch):
    canvas = document_canvas_for_orientation("portrait")
    theme = merge_presentation_canvas(
        {"mode": "svg"}, canvas.model_dump(mode="json", by_alias=True)
    )
    deck_id = uuid.uuid4()
    deck = PresentationWithSlides.model_validate(
        {
            "id": deck_id,
            "content": "fixture",
            "n_slides": 0,
            "language": "en",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "slides": [],
            "theme": theme,
            "mode": "svg",
            "profile_driver_id": "svg-v1",
            "profile_schema_version": 1,
        }
    )

    async def load_deck(_id, _session):
        assert _id == deck_id
        return deck

    monkeypatch.setattr(presentation_crud, "_get_presentation_with_slides", load_deck)
    app = FastAPI()
    app.include_router(presentation_crud.PRESENTATION_CRUD_ROUTER, prefix="/api/v1/ppt")
    from services.database import get_async_session

    async def session():
        yield object()

    app.dependency_overrides[get_async_session] = session
    with TestClient(app) as client:
        path = f"/api/v1/ppt/presentation/{deck_id}"
        rejected = client.get(path)
        accepted = client.get(path, headers={DOCUMENT_CANVAS_CONTRACTS_HEADER: DOCUMENT_CANVAS_CONTRACT_VERSION})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "document_canvas_client_upgrade_required"
    assert accepted.status_code == 200
    assert accepted.json()["theme"]["documentCanvas"]["contractVersion"] == DOCUMENT_CANVAS_CONTRACT_VERSION
