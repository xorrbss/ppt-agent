from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import uuid

import pytest
from fastapi import HTTPException
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


def test_generation_compacts_legacy_multibyte_uploaded_snapshot_to_byte_budget() -> None:
    documents = [
        {
            "assetId": str(uuid.uuid4()),
            "name": f"{marker}.txt",
            "contentSha256": marker.lower() * 64,
            "text": marker + ("한" * 149_998) + marker,
        }
        for marker in ("A", "B")
    ]
    view = ReferenceSnapshotView(
        snapshot_id=uuid.uuid4(),
        provider_id="uploaded_document",
        source_revision="revision-1",
        source_fingerprint="a" * 64,
        normalized_payload_json=json.dumps(
            {
                "purpose": "generation-reference",
                "assetCount": 2,
                "documents": documents,
            },
            ensure_ascii=False,
        ),
        asset_bindings=(),
        content_sha256="b" * 64,
        conversation_id=None,
        created_at=datetime.now(timezone.utc),
        expires_at=None,
    )

    context = presentation_generate._immutable_reference_context((view,))
    serialized = context.split("\n", 1)[1]

    assert len(serialized.encode("utf-8")) <= 512 * 1024
    assert '"text":"A' in serialized
    assert serialized.count("A.txt") == 1
    assert serialized.count("B.txt") == 1


def test_durable_payload_never_contains_legacy_file_locator() -> None:
    request = GeneratePresentationRequest(
        content="legacy upload",
        files=["C:/private/server/upload.docx"],
    )

    payload = presentation_generate._durable_request_payload(request)

    assert payload["files"] is None
    assert "C:/private/server/upload.docx" not in str(payload)


def test_source_svg_admission_retry_preserves_failure_without_synthesized_pins() -> None:
    request = GeneratePresentationRequest(
        content="",
        strategy="svg",
        authored_style="osb-smart-banking",
        sourceMode="source_content_restyle",
        sourceContentManifestPin={
            "manifestId": "missing-owned-manifest",
            "digest": "sha256:" + ("a" * 64),
        },
        contentPolicy={
            "text": "verbatim",
            "preserveAllTableCells": True,
            "preserveAllChartValues": False,
            "preserveAllImageOccurrences": True,
            "preserveSourceOrder": True,
            "preserveSourceSlideCount": True,
            "allowSlideExpansion": False,
            "allowSummarization": False,
            "overflowPolicy": "same_page_recompose_or_fail",
        },
    )

    payload = presentation_generate._durable_request_payload(
        request, admission_retry=True
    )
    assert payload["_internal_admission_retry_required"] is True
    assert "_internal_svg_style_snapshot" not in payload
    assert "_internal_svg_source_binding" not in payload
    assert "_internal_authored_canvas" not in payload
    restored = presentation_generate._request_from_durable_payload(payload)
    assert restored.authored_style == "osb-smart-banking"
    assert restored._svg_style_snapshot is None

    with pytest.raises(HTTPException, match="svg_source_canvas_binding_required"):
        presentation_generate._durable_request_payload(request)


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_source_svg_admission_reflows_widescreen_to_selected_a4(monkeypatch, orientation) -> None:
    from test_svg_source_layout import manifest_for
    from services.document_canvas import canvas_digest, document_canvas_for_orientation
    from services.document_canvas_generation import source_restyle_font_floor
    from services.source_content.contracts import SourceSlideSize
    from services.source_content.digests import digest_json
    from services.source_content.errors import SourceContentError
    from services.source_content.pipeline import admit_source_content_request

    owner_id = str(uuid.uuid4())
    snapshot_id = uuid.uuid4()
    manifest = manifest_for([("text", {"text": "원문 125% 보존"})]).model_copy(update={
        "manifest_id": "manifest-widescreen-a4",
        "owner_id": owner_id,
        "reference_snapshot_id": str(snapshot_id),
        "slide_size": SourceSlideSize(cx=14630400, cy=8229600),
    })
    manifest = manifest.model_copy(update={"manifest_digest": digest_json(
        manifest.model_dump(mode="json", by_alias=True, exclude={"manifest_digest"})
    )})
    original = manifest.model_dump(mode="json", by_alias=True)

    async def admit_manifest(_session, **pin):
        assert pin == {"owner_id": owner_id, "manifest_id": manifest.manifest_id,
                       "digest": manifest.manifest_digest}
        return manifest

    monkeypatch.setattr(
        "services.source_content.manifest_store.admit_source_content_manifest_pin",
        admit_manifest,
    )
    request = GeneratePresentationRequest(
        content="", strategy="svg", authored_style="osb-smart-banking",
        page_size="A4", orientation=orientation,
        reference_snapshot_ids=[snapshot_id], sourceMode="source_content_restyle",
        sourceContentManifestPin={"manifestId": manifest.manifest_id, "digest": manifest.manifest_digest},
        contentPolicy={"text": "verbatim", "preserveAllTableCells": True,
                       "preserveAllChartValues": False, "preserveAllImageOccurrences": True,
                       "preserveSourceOrder": True, "preserveSourceSlideCount": True,
                       "allowSlideExpansion": False, "allowSummarization": False,
                       "overflowPolicy": "same_page_recompose_or_fail"},
    )
    asyncio.run(admit_source_content_request(request, sql_session=object(), owner_id=owner_id))
    target = document_canvas_for_orientation(orientation)
    assert source_restyle_font_floor(None, target) == 7
    assert source_restyle_font_floor("body 5px", target) == 7
    assert source_restyle_font_floor("body 10px", target) == 10
    binding = request._svg_source_binding
    assert request._document_canvas == target
    assert request._authored_canvas == target.canvas_contract()
    assert binding["version"] == "svg-source-input/v2"
    assert binding["sourceSlideSize"] == {"cx": 14630400, "cy": 8229600}
    assert binding["documentCanvas"] == target.model_dump(mode="json", by_alias=True)
    assert binding["canvasDigest"] == canvas_digest(target)
    assert request.n_slides == len(manifest.units) == 1
    assert manifest.model_dump(mode="json", by_alias=True) == original

    # Replayed requests retain separate source geometry and target canvas.
    payload = presentation_generate._durable_request_payload(request)
    restored = presentation_generate._request_from_durable_payload(payload)
    asyncio.run(admit_source_content_request(restored, sql_session=object(), owner_id=owner_id))
    assert restored._svg_source_binding == binding
    assert restored._document_canvas == target
    restored.orientation = "portrait" if orientation == "landscape" else "landscape"
    with pytest.raises(SourceContentError, match="svg_source_input_binding_mismatch"):
        asyncio.run(admit_source_content_request(restored, sql_session=object(), owner_id=owner_id))


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
        strategy="authored",
        template="authored",
    )

    asyncio.run(presentation_generate.check_if_api_request_is_valid(request, object()))

    assert request.files is None
    assert request.reference_snapshot_ids == [snapshot_id]
    assert request._reference_snapshots == (view,)
