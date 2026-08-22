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
