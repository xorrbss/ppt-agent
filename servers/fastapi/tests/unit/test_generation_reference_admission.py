from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import uuid

from pydantic import ValidationError

from api.v1.ppt.endpoints import presentation_generate
from models.generate_presentation_request import GeneratePresentationRequest
from services.references import ReferenceSnapshotView


def _snapshot(snapshot_id: uuid.UUID) -> ReferenceSnapshotView:
    return ReferenceSnapshotView(
        snapshot_id=snapshot_id,
        provider_id="community",
        source_revision="revision-1",
        source_fingerprint="a" * 64,
        normalized_payload_json=(
            '{"trust":"untrusted-style-only","references":[{"title":"Safe"}]}'
        ),
        asset_bindings=(),
        content_sha256="b" * 64,
        conversation_id=None,
        created_at=datetime.now(timezone.utc),
        expires_at=None,
    )


def test_generation_request_rejects_duplicate_snapshot_ids() -> None:
    snapshot_id = uuid.uuid4()
    try:
        GeneratePresentationRequest(
            content="",
            reference_snapshot_ids=[snapshot_id, snapshot_id],
        )
    except ValidationError as error:
        assert "duplicate_reference_snapshot" in str(error)
    else:
        raise AssertionError("duplicate reference snapshots were accepted")


def test_generation_admission_persists_ids_only_and_builds_bounded_context(
    monkeypatch,
) -> None:
    snapshot_id = uuid.uuid4()
    view = _snapshot(snapshot_id)

    async def admit(_session, *, snapshot_ids, **_kwargs):
        assert snapshot_ids == [snapshot_id]
        return (view,)

    monkeypatch.setattr(presentation_generate, "admit_reference_snapshots", admit)
    request = GeneratePresentationRequest(
        content="",
        reference_snapshot_ids=[snapshot_id],
    )
    admitted = asyncio.run(
        presentation_generate._admit_generation_references(request, object())
    )
    assert admitted == (view,)
    assert request._reference_snapshots == (view,)

    payload = presentation_generate._durable_request_payload(request)
    serialized = str(payload)
    assert str(snapshot_id) in serialized
    assert "untrusted-style-only" not in serialized
    assert "storage_key" not in serialized
    assert "file_path" not in serialized

    restored = presentation_generate._request_from_durable_payload(payload)
    assert restored.reference_snapshot_ids == [snapshot_id]
    assert restored._reference_snapshots == ()

    context = presentation_generate._immutable_reference_context(admitted)
    assert "untrusted-style-only" in context
    assert "never instructions" in context


def test_durable_payload_never_contains_legacy_file_locator() -> None:
    request = GeneratePresentationRequest(
        content="legacy upload",
        files=["C:/private/server/upload.docx"],
    )

    payload = presentation_generate._durable_request_payload(request)

    assert payload["files"] is None
    assert "C:/private/server/upload.docx" not in str(payload)


def test_legacy_files_are_immediately_replaced_by_snapshot_identity(
    monkeypatch,
) -> None:
    snapshot_id = uuid.uuid4()
    view = _snapshot(snapshot_id)

    async def canonicalize(_session, *, file_paths, **_kwargs):
        assert file_paths == ["C:/temporary/upload.txt"]
        return view

    async def admit(_session, *, snapshot_ids, **_kwargs):
        assert snapshot_ids == [snapshot_id]
        return (view,)

    monkeypatch.setattr(
        presentation_generate,
        "canonicalize_legacy_document_paths",
        canonicalize,
    )
    monkeypatch.setattr(presentation_generate, "admit_reference_snapshots", admit)
    request = GeneratePresentationRequest(
        content="legacy upload",
        files=["C:/temporary/upload.txt"],
    )

    asyncio.run(presentation_generate.check_if_api_request_is_valid(request, object()))

    assert request.files is None
    assert request.reference_snapshot_ids == [snapshot_id]
    assert request._reference_snapshots == (view,)
