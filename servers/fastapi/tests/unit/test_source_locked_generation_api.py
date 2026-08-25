from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException, UploadFile
from pptx import Presentation
import pytest

from api.v1.ppt.endpoints import source_locked as endpoint
from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.contracts import GenerationMode, PageCountPolicy
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.generation_store import DurableSourceLockedGenerationStore


def _pptx(label: str = "source locked") -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    textbox = slide.shapes.add_textbox(0, 0, presentation.slide_width, presentation.slide_height)
    textbox.text = label
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def _contract(filename: str = "source.pptx"):
    return admit_source_locked_v1(
        [filename],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )


def _upload(data: bytes, filename: str = "source.pptx") -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(data))


def _generate(data: bytes, key: str):
    contract = _contract()
    digest = idempotency_payload_digest(contract, sha256_digest(data))
    return asyncio.run(
        endpoint.generate_source_locked_v1(
            files=[_upload(data)],
            generation_mode=GenerationMode.SOURCE_LOCKED,
            page_count_policy=PageCountPolicy.SOURCE,
            preserve_all_image_occurrences=True,
            idempotency_key=key,
            expected_payload_digest=digest,
        )
    )


def test_generate_persists_terminal_artifact_and_durable_replay(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "app-data" / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    source = _pptx()

    first = _generate(source, "generation-api-1")

    assert [item.status.value for item in first.progress_stages] == [
        "completed",
        "completed",
        "completed",
    ]
    assert first.fallback_allowed is False
    assert first.idempotent_replay is False
    assert first.artifact.sha256 == sha256_digest(source)
    assert first.restoration_verification.byte_exact_source_package is True
    assert first.restoration_verification.missing_content_count == 0
    assert first.artifact.download_url.endswith(first.artifact.artifact_id)

    artifact_path, _ = store.artifact_path(first.artifact.artifact_id)
    assert artifact_path.read_bytes() == source
    assert artifact_path.is_relative_to(store.artifact_root)

    # A new store instance proves the record and response survive process-local state.
    reopened = DurableSourceLockedGenerationStore(store.root)
    monkeypatch.setattr(endpoint, "_generation_store", lambda: reopened)
    monkeypatch.setattr(
        endpoint,
        "_run_restoration",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("replay restored again")),
    )
    replay = _generate(source, "generation-api-1")
    assert replay.idempotent_replay is True
    assert replay.generation_record_id == first.generation_record_id
    assert replay.artifact == first.artifact


def test_generate_same_key_with_different_digest_fails_closed(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    _generate(_pptx("first"), "generation-api-conflict")

    with pytest.raises(HTTPException) as exc:
        _generate(_pptx("different"), "generation-api-conflict")

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert exc.value.detail["fallback_allowed"] is False


def test_generation_digest_mismatch_never_calls_restoration_or_store(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    monkeypatch.setattr(
        endpoint,
        "_run_restoration",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("restoration called")),
    )
    source = _pptx()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint.generate_source_locked_v1(
                files=[_upload(source)],
                generation_mode=GenerationMode.SOURCE_LOCKED,
                page_count_policy=PageCountPolicy.SOURCE,
                preserve_all_image_occurrences=True,
                idempotency_key="generation-api-mismatch",
                expected_payload_digest="sha256:" + "0" * 64,
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "IDEMPOTENCY_PAYLOAD_DIGEST_MISMATCH"
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_records_v1").fetchone()[0] == 0


def test_download_resolves_only_recorded_safe_artifact_ids(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    generated = _generate(_pptx(), "generation-api-download")

    response = asyncio.run(endpoint.download_source_locked_artifact_v1(generated.artifact.artifact_id))
    assert Path(response.path).resolve().is_relative_to(store.artifact_root)
    assert response.filename == generated.artifact.filename
    assert response.headers["etag"] == f'"{generated.artifact.sha256}"'

    with pytest.raises(HTTPException) as invalid:
        asyncio.run(endpoint.download_source_locked_artifact_v1("../../outside.pptx"))
    assert invalid.value.status_code == 404
    assert invalid.value.detail["code"] == "ARTIFACT_ID_INVALID"


def test_store_detects_artifact_tampering_on_replay(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    source = _pptx()
    generated = _generate(source, "generation-api-integrity")
    path, _ = store.artifact_path(generated.artifact.artifact_id)
    path.write_bytes(b"tampered")

    contract = _contract()
    digest = idempotency_payload_digest(contract, sha256_digest(source))
    with pytest.raises(SourceLockedError) as exc:
        store.lookup(
            idempotency_key="generation-api-integrity",
            idempotency_payload_digest=digest,
        )
    assert exc.value.detail.code == "ARTIFACT_INTEGRITY_FAILURE"


def test_missing_content_readback_never_persists_success(tmp_path, monkeypatch):
    store = DurableSourceLockedGenerationStore(tmp_path / "source_locked" / "v1")
    monkeypatch.setattr(endpoint, "_generation_store", lambda: store)
    source = _pptx()
    source_digest = sha256_digest(source)
    monkeypatch.setattr(
        endpoint,
        "_run_restoration",
        lambda **_kwargs: SimpleNamespace(
            artifact_bytes=source,
            artifact_media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            verification=SimpleNamespace(
                source_type="pptx",
                source_snapshot_sha256=source_digest,
                output_package_sha256=source_digest,
                page_count=1,
                slide_count=1,
                direct_occurrence_count=0,
                rendered_page_count=0,
                authoritative_image_digests=(),
                missing_content_count=1,
                byte_exact_source_package=True,
                fallback_allowed=False,
            ),
        ),
    )

    with pytest.raises(HTTPException) as exc:
        _generate(source, "generation-api-missing")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "RESTORATION_READBACK_INVALID"
    assert exc.value.detail["fallback_allowed"] is False
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_records_v1").fetchone()[0] == 0
    assert list(store.artifact_root.glob("*.pptx")) == []
