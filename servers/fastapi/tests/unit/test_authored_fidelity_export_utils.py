import asyncio
import uuid
from pathlib import Path

import pytest

from utils import export_utils


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "success": True,
            "path": "/api/export-presentation/file?name=fidelity.pptx",
        }


class _Client:
    def __init__(self) -> None:
        self.request_json: dict[str, object] | None = None

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, _url: str, *, json: dict[str, object]) -> _Response:
        self.request_json = json
        return _Response()


class _HybridResponse(_Response):
    def json(self) -> dict[str, object]:
        return {
            "success": True,
            "path": "/api/export-presentation/file?name=hybrid.pptx",
            "quality": {"status": "partially-editable", "editableSlides": 1},
        }


class _HybridClient(_Client):
    async def post(self, _url: str, *, json: dict[str, object]) -> _HybridResponse:
        self.request_json = json
        return _HybridResponse()


class _PdfResponse(_Response):
    def json(self) -> dict[str, object]:
        return {
            "success": True,
            "path": "/api/export-presentation/file?name=authored.pdf",
        }


def test_authored_fidelity_request_does_not_require_hybrid_attempt_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "fidelity.pptx"
    artifact.write_bytes(b"pptx")
    client = _Client()

    monkeypatch.setattr(export_utils, "_get_next_public_url", lambda: "http://next")
    monkeypatch.setattr(
        export_utils.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        export_utils,
        "_resolve_hybrid_export_path",
        lambda _path: artifact,
    )

    presentation_id = uuid.uuid4()
    result = asyncio.run(
        export_utils.export_authored_fidelity_presentation(
            presentation_id,
            "Fidelity",
            cookie_header="session=test",
            expected_revision=13,
        )
    )

    assert result.path == str(artifact)
    assert client.request_json == {
        "id": str(presentation_id),
        "format": "pptx",
        "title": "Fidelity",
        "pptxMode": "fidelity",
        "expectedPresentationRevision": 13,
    }


def test_authored_hybrid_request_forwards_attempt_manifest_and_operation_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "hybrid.pptx"
    artifact.write_bytes(b"pptx")
    client = _HybridClient()
    presentation_id = uuid.uuid4()
    export_attempt_id = uuid.uuid4()
    workspace_operation_id = uuid.uuid4()
    canonical_digest = "a" * 64

    monkeypatch.setattr(export_utils, "_get_next_public_url", lambda: "http://next")
    monkeypatch.setattr(export_utils.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(export_utils, "_resolve_hybrid_export_path", lambda _path: artifact)
    monkeypatch.setattr(export_utils, "_require_persisted_hybrid_coverage", lambda *_args: None)
    monkeypatch.setattr(
        export_utils,
        "_validated_hybrid_coverage",
        lambda *_args, **_kwargs: {"source": {"sourceDigest": "b" * 64}},
    )

    result = asyncio.run(
        export_utils.export_authored_hybrid_presentation(
            presentation_id,
            "Hybrid",
            cookie_header="session=test",
            expected_revision=1,
            export_attempt_id=export_attempt_id,
            canonical_source_digest=canonical_digest,
            workspace_operation_id=workspace_operation_id,
        )
    )

    assert result.path == str(artifact)
    assert client.request_json is not None
    assert client.request_json["exportAttemptId"] == str(export_attempt_id)
    assert client.request_json["canonicalSourceDigest"] == canonical_digest
    assert client.request_json["operationId"] == str(workspace_operation_id)
    assert client.request_json["operationId"] != client.request_json["exportAttemptId"]


def test_canonical_pdf_uses_next_route_instead_of_shared_dom_export_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "authored.pdf"
    artifact.write_bytes(b"%PDF")
    client = _Client()

    async def post(_url: str, *, json: dict[str, object]) -> _PdfResponse:
        client.request_json = json
        return _PdfResponse()

    client.post = post
    monkeypatch.setattr(export_utils, "_get_next_public_url", lambda: "http://next")
    monkeypatch.setattr(export_utils.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(export_utils, "_resolve_hybrid_export_path", lambda _path: artifact)

    presentation_id = uuid.uuid4()
    workspace_operation_id = uuid.uuid4()
    result = asyncio.run(
        export_utils.export_presentation(
            presentation_id,
            "Authored PDF",
            "pdf",
            cookie_header="session=test",
            expected_revision=7,
            workspace_operation_id=workspace_operation_id,
        )
    )

    assert result.path == str(artifact)
    assert client.request_json == {
        "id": str(presentation_id),
        "format": "pdf",
        "title": "Authored PDF",
        "expectedPresentationRevision": 7,
        "operationId": str(workspace_operation_id),
    }
