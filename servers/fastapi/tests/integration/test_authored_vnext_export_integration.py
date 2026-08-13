"""Durable authored-vNext export integration and failure-safety tests.

The fake transport implements the authenticated Next worker's JSON boundary;
it never invokes a provider, browser, or PowerPoint process.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from copy import deepcopy
from hashlib import sha256
import multiprocessing
from pathlib import Path
from typing import Any
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.authored_vnext.integration.artifact_store import (
    ArtifactStoreError,
    BufferedArtifactStore,
)
from services.authored_vnext.integration.worker import WorkerError
from services.authored_vnext.integration.export_payload import (
    build_worker_request,
    canonical_json,
    raw_sha256,
)
from services.authored_vnext.integration.export_service import (
    AuthoredVNextExportService,
    CreateJobCommand,
    ExportServiceError,
)
from services.authored_vnext.master.repository import (
    InMemoryAuthoredVNextMasterRepository,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
    ExportArtifactInput,
    OperationFenceError,
    SourceDigestMismatchError,
)
from models.authored_vnext.master.contract import DeckMasterContract
from models.sql.user import User
from models.authored_vnext.deck import (
    DeckGenerationSnapshot,
    MasterDigestPin,
    SlideValidationCheckpoint,
    ValidatedSlideSpecV2,
)
from models.authored_vnext.master.operations import (
    CreateDeckRequest,
    CreateDraftRequest,
    MasterCasRequest,
)
from tests.support.authored_vnext_master_fixture import (
    long_title_slide_payload,
    master_contract_payload,
    master_contract_with_logo_payload,
)
from api.v1.ppt.endpoints.authored_vnext.export.router import (
    AUTHORED_VNEXT_EXPORT_ROUTER,
    get_authored_vnext_export_service,
)
from services.account_deletion_service import (
    cancel_authored_vnext_exports_for_account,
    mark_account_deleting,
)


PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
OWNER_ID = uuid.uuid5(uuid.NAMESPACE_URL, "authored-vnext-export-owner-a")


def _create_owner(engine) -> None:
    User.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        if session.get(User, OWNER_ID) is None:
            session.add(
                User(
                    id=OWNER_ID,
                    username="authored-vnext-export-owner-a",
                    hashed_password="test-only",
                )
            )
            session.commit()


def _claim_job_in_process(database_path: str, job_id: str, worker_id: str, queue) -> None:
    """Fresh-process claim probe; no engine/session object crosses the boundary."""

    engine = create_engine(
        f"sqlite:///{database_path}", connect_args={"check_same_thread": False}
    )
    repository = AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(OWNER_ID),
        tenant_id="tenant-a",
        owner_id=OWNER_ID,
    )
    try:
        claimed = repository.claim_export_job(
            uuid.UUID(job_id), worker_id=worker_id
        )
        queue.put(("claimed", worker_id, claimed.fence_token))
    except OperationFenceError:
        queue.put(("fenced", worker_id, None))
    finally:
        engine.dispose()


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def test_worker_canonical_json_normalizes_integral_numbers_and_unicode_nfc() -> None:
    assert canonical_json({"text": "e\u0301", "value": 76.0}) == '{"text":"é","value":76}'


def _worker_success(
    payload: dict[str, Any],
    artifact: bytes,
    *,
    artifact_digest: str | None = None,
    package_digest: str | None = None,
    fencing_token: str | None = None,
) -> dict[str, Any]:
    """Return the exact unwrapped JSON envelope emitted by the Next worker."""

    digest = artifact_digest or _digest(artifact)
    master = payload.get("master", {})
    furniture = master.get("furniture", [])
    font_files = master.get("fontManifest", {}).get("files", [])
    master_digest = (
        f"sha256:{payload.get('digests', {}).get('masterSha256', '0' * 64)}"
    )
    return {
        "schemaVersion": "authored-vnext-ooxml-worker/v1",
        "operation": "EXPORT_PPTX",
        "operationId": payload["operationId"],
        "fencingToken": fencing_token or payload["fencingToken"],
        "status": "SUCCEEDED",
        "artifact": {
            "mediaType": PPTX_MEDIA_TYPE,
            "filenameExtension": ".pptx",
            "byteLength": len(artifact),
            "sha256": digest.removeprefix("sha256:"),
            "contentBase64": base64.b64encode(artifact).decode("ascii"),
        },
        "receipt": {
            "adapterRevision": 1,
            "validation": "candidate-reopened",
            "candidateDigestVerified": True,
            "package": {
                "packageDigest": (package_digest or digest).removeprefix("sha256:"),
                "relationshipDigest": "1" * 64,
                "closureDigest": "2" * 64,
                "slideCount": len(payload["slides"]),
                "masterCount": 1,
                "layoutCount": 1,
                "macroDetected": False,
                "quarantineReasons": [],
            },
            "preservation": {
                "silentLossCount": 0,
                "missingParts": [],
                "unexpectedChangedParts": [],
            },
            "master": {
                "contractDigest": master_digest,
                "semanticDigest": master_digest,
                "furnitureCount": len(furniture),
                "logoCount": sum(item.get("kind") == "logo" for item in furniture),
                "pageNumberCount": sum(
                    item.get("kind") == "pageNumber" for item in furniture
                ),
                "fontFileCount": len(font_files),
                "duplicateFurnitureCount": 0,
                "fontFilesMode": "REFERENCE_METADATA_VERIFIED",
            },
            "slides": [
                {
                    "partName": f"ppt/slides/slide{index}.xml",
                    "slideId": slide["slideId"],
                    "layoutId": slide["layoutId"],
                    "masterDigest": master_digest,
                }
                for index, slide in enumerate(payload["slides"], start=1)
            ],
        },
    }


class DeterministicWorkerTransport:
    """Scriptable fake for retry, timeout, corruption, and fencing tests."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[dict[str, Any], int]] = []

    async def execute(
        self, payload: dict[str, Any], timeout_ms: int
    ) -> dict[str, Any]:
        self.calls.append((deepcopy(payload), timeout_ms))
        if not self.outcomes:
            raise AssertionError("fake worker received an unexpected call")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            outcome = outcome(payload)
        assert isinstance(outcome, dict)
        return deepcopy(outcome)


def test_buffered_artifact_store_publishes_content_addressed_read_back_receipt(
    tmp_path: Path,
) -> None:
    store = BufferedArtifactStore(tmp_path / "artifacts")
    artifact = b"PK\x03\x04deterministic-authored-vnext-pptx"
    digest = _digest(artifact)

    storage_key, byte_length = store.publish(artifact, digest)
    reopened = store.open_verified(storage_key, digest, byte_length)
    duplicate_key, duplicate_size = store.publish(artifact, digest)

    assert reopened.read_bytes() == artifact
    assert (duplicate_key, duplicate_size) == (storage_key, byte_length)
    assert not list((tmp_path / "artifacts").rglob("*.candidate"))


@pytest.mark.parametrize(
    ("artifact", "claimed_digest", "expected_code"),
    [
        (
            b"PK\x03\x04actual",
            _digest(b"PK\x03\x04different"),
            "authored_vnext_artifact_digest_mismatch",
        ),
        (b"", _digest(b""), "authored_vnext_artifact_must_be_buffered"),
    ],
)
def test_buffered_artifact_store_fails_closed_without_publishing_partial_file(
    tmp_path: Path,
    artifact: bytes,
    claimed_digest: str,
    expected_code: str,
) -> None:
    root = tmp_path / "artifacts"
    store = BufferedArtifactStore(root)

    with pytest.raises(ArtifactStoreError, match=expected_code):
        store.publish(artifact, claimed_digest)

    assert not list(root.rglob("*.pptx"))
    assert not list(root.rglob("*.candidate"))


def test_deterministic_worker_transport_preserves_timeout_and_retry_order() -> None:
    artifact = b"PK\x03\x04retry-success"
    payload = {
        "operationId": "job:test",
        "fencingToken": "1",
        "slides": [{"slideId": "slide:1", "layoutId": "layout:1", "values": {}}],
    }
    transport = DeterministicWorkerTransport(
        WorkerError("authored_vnext_worker_timeout", retryable=True),
        lambda request: _worker_success(request, artifact),
    )

    with pytest.raises(WorkerError) as failure:
        asyncio.run(transport.execute(payload, 1_000))
    receipt = asyncio.run(transport.execute(payload, 2_000))

    assert failure.value.retryable is True
    assert receipt["artifact"]["sha256"] == _digest(artifact).removeprefix("sha256:")
    assert [timeout for _payload, timeout in transport.calls] == [1_000, 2_000]


def test_published_master_and_deck_build_exact_versioned_worker_digest_envelope() -> None:
    repository = InMemoryAuthoredVNextMasterRepository()
    master_service = AuthoredVNextMasterService(repository)
    draft = master_service.create_draft(
        CreateDraftRequest(
            contract=DeckMasterContract.model_validate(master_contract_payload())
        )
    )
    published = master_service.publish(
        draft.master_id,
        MasterCasRequest(
            expected_revision=draft.revision,
            expected_digest=draft.digest,
        ),
    )
    slides = []
    for slide_id, layout_id in (
        ("long-title", "layout:long-title"),
        ("cover", "layout:cover"),
        ("section", "layout:section"),
        ("standard", "layout:standard-title"),
    ):
        slide = long_title_slide_payload()
        slide["slideId"] = f"slide:{slide_id}"
        slide["layoutId"] = layout_id
        slide["title"]["text"] = f"{slide_id} export title"
        slide["title"]["lines"] = [f"{slide_id} export title"]
        slides.append(slide)
    deck = master_service.create_deck(
        CreateDeckRequest(
            deck_id="deck:export-contract",
            master_id=published.master_id,
            master_revision=published.revision,
            master_digest=published.digest,
            slides=slides,
        )
    )

    payload, digests = build_worker_request(
        operation_id="job:export-contract",
        fence_token=7,
        master=published.contract,
        deck=deck,
        expected_package_digest=None,
    )

    assert payload["schemaVersion"] == "authored-vnext-ooxml-worker/v1"
    assert payload["operation"] == "EXPORT_PPTX"
    assert payload["operationId"] == "job:export-contract"
    assert payload["fencingToken"] == 7
    assert payload["slides"][0]["slideId"] == "slide:long-title"
    assert [slide["layoutId"] for slide in payload["slides"]] == [
        "layout:long-title",
        "layout:cover",
        "layout:section",
        "layout:standard-title",
    ]
    assert [set(slide["values"]) for slide in payload["slides"]] == [
        {"long-title", "long-body"},
        {"cover-title", "cover-body"},
        {"section-title", "section-body"},
        {"standard-title", "standard-body"},
    ]
    assert [item["kind"] for item in payload["master"]["furniture"]] == [
        "logo", "text", "text", "line", "pageNumber"
    ]
    assert [
        (item["id"], item["kind"])
        for item in payload["master"]["layouts"]
    ] == [
        ("layout:cover", "cover"),
        ("layout:section", "section"),
        ("layout:standard-title", "standard"),
        ("layout:long-title", "long-title"),
    ]
    assert payload["master"]["layouts"][2]["placeholders"][0]["textStyle"]["maxLines"] == 2
    assert payload["master"]["layouts"][3]["placeholders"][0]["textStyle"]["maxLines"] >= 3
    assert payload["master"]["fontManifest"]["files"][0]["path"].endswith(
        "malgun.ttf"
    )
    assert payload["digests"] == {
        "masterSha256": raw_sha256(payload["master"]),
        "specSha256": raw_sha256(payload["slides"]),
        "assetManifestSha256": raw_sha256([]),
        "fontManifestSha256": raw_sha256(payload["master"]["fontManifest"]),
        "packageSha256": None,
    }
    assert digests == {
        "master": f"sha256:{payload['digests']['masterSha256']}",
        "spec": f"sha256:{payload['digests']['specSha256']}",
        "assetManifest": f"sha256:{payload['digests']['assetManifestSha256']}",
        "fontManifest": f"sha256:{payload['digests']['fontManifestSha256']}",
    }

    slide = deck.slides[0]
    source_digest = "sha256:" + "7" * 64
    layout_registry_digest = "sha256:" + "8" * 64
    checkpoint = SlideValidationCheckpoint(
        master_digest=published.digest,
        layout_registry_digest=layout_registry_digest,
        font_manifest_digest=slide.title_fit.font_manifest_digest,
        source_digest=source_digest,
        visual_plan_digest="sha256:" + "9" * 64,
        quality_policy_version="quality/v1",
        layout_id=slide.layout_id,
        body_slot_id="slot:long-body",
        validated_at="2026-08-13T12:00:00Z",
    )
    generated = DeckGenerationSnapshot(
        deck_id=deck.deck_id,
        revision=deck.revision,
        digest=deck.digest,
        master=MasterDigestPin(
            master_id=deck.master_id,
            master_revision=deck.master_revision,
            master_digest=published.digest,
            layout_registry_digest=layout_registry_digest,
            font_manifest_digest=slide.title_fit.font_manifest_digest,
        ),
        source_digest=source_digest,
        slides=[ValidatedSlideSpecV2(slide=slide, checkpoint=checkpoint)],
        created_at="2026-08-13T12:00:00Z",
    )
    generated_payload, generated_digests = build_worker_request(
        operation_id="job:generation-snapshot",
        fence_token=8,
        master=published.contract,
        deck=generated,
        expected_package_digest=None,
    )
    assert generated_payload["slides"] == payload["slides"][:1]
    assert generated_digests == {
        **digests,
        "spec": f"sha256:{generated_payload['digests']['specSha256']}",
    }


def _sql_repository(tmp_path: Path, *, tenant_id: str = "tenant-a"):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'authored-vnext.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    _create_owner(engine)
    AuthoredVNextSqlRepository.create_schema(engine)
    return engine, AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(OWNER_ID),
        tenant_id=tenant_id,
        owner_id=OWNER_ID,
    )


def _published_sql_deck(
    repository: AuthoredVNextSqlRepository,
    deck_id: str,
    *,
    master_payload: dict[str, object] | None = None,
    assets: list[dict[str, Any]] | None = None,
):
    master_service = AuthoredVNextMasterService(repository)
    draft = master_service.create_draft(
        CreateDraftRequest(
            contract=DeckMasterContract.model_validate(
                master_payload or master_contract_payload()
            )
        )
    )
    published = master_service.publish(
        draft.master_id,
        MasterCasRequest(
            expected_revision=draft.revision,
            expected_digest=draft.digest,
        ),
    )
    deck = master_service.create_deck(
        CreateDeckRequest(
            deck_id=deck_id,
            master_id=published.master_id,
            master_revision=published.revision,
            master_digest=published.digest,
            slides=[long_title_slide_payload()],
        )
    )
    _payload, digests = build_worker_request(
        operation_id="job:sql",
        fence_token=1,
        master=published.contract,
        deck=deck,
        expected_package_digest=None,
        assets=assets,
    )
    return published, deck, digests


def _create_sql_job(
    repository: AuthoredVNextSqlRepository,
    *,
    deck,
    digests: dict[str, str],
    key: str,
    expected_publication_revision: int | None = None,
    expected_publication_digest: str | None = None,
    max_attempts: int = 3,
):
    return repository.create_export_job(
        deck_id=deck.deck_id,
        deck_revision=deck.revision,
        deck_digest=deck.digest,
        master_revision=deck.master_revision,
        master_digest=deck.master_digest,
        spec_digest=digests["spec"],
        asset_manifest_digest=digests["assetManifest"],
        font_manifest_digest=digests["fontManifest"],
        expected_package_digest=None,
        idempotency_key=key,
        request_payload={"deckId": deck.deck_id, "idempotencyKey": key},
        expected_publication_revision=expected_publication_revision,
        expected_publication_digest=expected_publication_digest,
        timeout_ms=1_000,
        max_attempts=max_attempts,
    )


def _artifact_input(
    *,
    artifact_path: Path,
    artifact: bytes,
    deck,
    digests: dict[str, str],
) -> ExportArtifactInput:
    digest = _digest(artifact)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(artifact)
    return ExportArtifactInput(
        storage_key=str(artifact_path),
        artifact_digest=digest,
        readback_digest=digest,
        package_digest=_digest(b"package-graph:" + artifact),
        size_bytes=len(artifact),
        master_digest=deck.master_digest,
        spec_digest=digests["spec"],
        asset_manifest_digest=digests["assetManifest"],
        font_manifest_digest=digests["fontManifest"],
    )


def test_sql_export_publication_is_idempotent_scoped_and_failure_atomic(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:durable-export"
        )
        job = _create_sql_job(
            repository, deck=deck, digests=digests, key="first-publication"
        )
        duplicate = _create_sql_job(
            repository, deck=deck, digests=digests, key="first-publication"
        )
        assert duplicate.id == job.id

        claimed = repository.claim_export_job(job.id, worker_id="worker-a")
        artifact = b"PK\x03\x04last-known-good-pptx"
        receipt = repository.complete_export_job(
            job.id,
            fence_token=claimed.fence_token,
            artifact=_artifact_input(
                artifact_path=tmp_path / "artifacts" / "good.pptx",
                artifact=artifact,
                deck=deck,
                digests=digests,
            ),
        )
        pointer, active = repository.get_active_artifact(deck.deck_id)
        assert active.id == receipt.id
        assert pointer.revision == 1
        assert pointer.digest == _digest(artifact)
        last_known_good = (pointer.revision, pointer.generation, pointer.digest, active.id)

        with pytest.raises(SourceDigestMismatchError):
            repository.create_export_job(
                deck_id=deck.deck_id,
                deck_revision=deck.revision,
                deck_digest=_digest(b"stale-deck"),
                master_revision=deck.master_revision,
                master_digest=deck.master_digest,
                spec_digest=digests["spec"],
                asset_manifest_digest=digests["assetManifest"],
                font_manifest_digest=digests["fontManifest"],
                expected_package_digest=None,
                idempotency_key="stale-source",
                request_payload={"stale": True},
            )

        failed_job = _create_sql_job(
            repository,
            deck=deck,
            digests=digests,
            key="worker-failure",
            expected_publication_revision=pointer.revision,
            expected_publication_digest=pointer.digest,
        )
        failed_claim = repository.claim_export_job(
            failed_job.id, worker_id="worker-b"
        )
        repository.fail_export_job(
            failed_job.id,
            fence_token=failed_claim.fence_token,
            failure_code="authored_vnext_worker_unavailable",
        )
        with pytest.raises(OperationFenceError):
            repository.complete_export_job(
                failed_job.id,
                fence_token=failed_claim.fence_token,
                artifact=_artifact_input(
                    artifact_path=tmp_path / "artifacts" / "late.pptx",
                    artifact=b"PK\x03\x04late-fenced-result",
                    deck=deck,
                    digests=digests,
                ),
            )

        unchanged_pointer, unchanged_active = repository.get_active_artifact(
            deck.deck_id
        )
        assert (
            unchanged_pointer.revision,
            unchanged_pointer.generation,
            unchanged_pointer.digest,
            unchanged_active.id,
        ) == last_known_good

        isolated = AuthoredVNextSqlRepository(
            engine,
            owner_scope=str(OWNER_ID),
            tenant_id="tenant-b",
            owner_id=OWNER_ID,
        )
        assert isolated.get_active_artifact(deck.deck_id) is None
        with pytest.raises(KeyError):
            isolated.get_export_job(job.id)
    finally:
        engine.dispose()


def test_sql_timeout_cancel_retry_resume_never_moves_active_pointer_early(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:retry-resume"
        )
        job = _create_sql_job(
            repository,
            deck=deck,
            digests=digests,
            key="timeout-then-resume",
            max_attempts=2,
        )
        first_claim = repository.claim_export_job(job.id, worker_id="worker-a")
        repository.fail_export_job(
            job.id,
            fence_token=first_claim.fence_token,
            failure_code="authored_vnext_worker_timeout",
        )
        assert repository.get_active_artifact(deck.deck_id) is None

        queued_again = repository.retry_export_job(job.id)
        assert queued_again.state == "queued"
        resumed = repository.claim_export_job(job.id, worker_id="worker-b")
        assert resumed.attempt_count == 2
        assert resumed.fence_token > first_claim.fence_token
        repository.complete_export_job(
            job.id,
            fence_token=resumed.fence_token,
            artifact=_artifact_input(
                artifact_path=tmp_path / "artifacts" / "resumed.pptx",
                artifact=b"PK\x03\x04resumed-success",
                deck=deck,
                digests=digests,
            ),
        )
        pointer_before_cancel, _receipt = repository.get_active_artifact(deck.deck_id)

        cancel_job = _create_sql_job(
            repository,
            deck=deck,
            digests=digests,
            key="cancel-before-run",
            expected_publication_revision=pointer_before_cancel.revision,
            expected_publication_digest=pointer_before_cancel.digest,
        )
        cancelled = repository.request_cancel(cancel_job.id)
        assert cancelled.state == "cancelled"
        with pytest.raises(OperationFenceError):
            repository.claim_export_job(cancel_job.id, worker_id="worker-c")
        pointer_after_cancel, _receipt = repository.get_active_artifact(deck.deck_id)
        assert (
            pointer_after_cancel.revision,
            pointer_after_cancel.generation,
            pointer_after_cancel.digest,
        ) == (
            pointer_before_cancel.revision,
            pointer_before_cancel.generation,
            pointer_before_cancel.digest,
        )
    finally:
        engine.dispose()


def test_sql_claim_cas_fences_one_of_two_independent_processes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "multiprocess.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path}", connect_args={"check_same_thread": False}
    )
    _create_owner(engine)
    AuthoredVNextSqlRepository.create_schema(engine)
    repository = AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(OWNER_ID),
        tenant_id="tenant-a",
        owner_id=OWNER_ID,
    )
    _master, deck, digests = _published_sql_deck(
        repository, "deck:multiprocess-cas"
    )
    job = _create_sql_job(
        repository, deck=deck, digests=digests, key="multiprocess-cas"
    )
    engine.dispose()

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_claim_job_in_process,
            args=(str(database_path), str(job.id), f"worker-{index}", queue),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    outcomes = sorted(queue.get(timeout=2) for _process in processes)

    assert [outcome[0] for outcome in outcomes] == ["claimed", "fenced"]
    claimed = next(outcome for outcome in outcomes if outcome[0] == "claimed")
    assert claimed[2] == 1


def _create_service_job(
    service: AuthoredVNextExportService,
    *,
    deck,
    digests: dict[str, str],
    key: str,
    active_revision: int = 0,
    active_digest: str | None = None,
    package_digest: str | None = None,
    asset_digests: list[str] | None = None,
):
    asset_digests = asset_digests or []
    expected_font_digest = service.repository.get_master(
        deck.master_id, deck.master_revision
    ).contract.font_manifest.digest
    assert expected_font_digest is not None
    request_payload = {
        "deckId": deck.deck_id,
        "expectedDeckRevision": deck.revision,
        "expectedDeckDigest": deck.digest,
        "expectedMasterRevision": deck.master_revision,
        "expectedMasterDigest": deck.master_digest,
        "expectedActiveArtifactRevision": active_revision,
        "expectedActiveArtifactDigest": active_digest,
        "expectedSourcePackageDigest": package_digest,
        "expectedAssetDigests": asset_digests,
        "expectedFontDigest": expected_font_digest,
        "idempotencyKey": key,
        "timeoutMs": 1_000,
        "maxAttempts": 3,
    }
    return service.create_job(
        CreateJobCommand(
            deck_id=deck.deck_id,
            expected_deck_revision=deck.revision,
            expected_deck_digest=deck.digest,
            expected_master_revision=deck.master_revision,
            expected_master_digest=deck.master_digest,
            expected_active_artifact_revision=active_revision,
            expected_active_artifact_digest=active_digest,
            expected_source_package_digest=package_digest,
            expected_asset_digests=asset_digests,
            expected_font_digest=expected_font_digest,
            idempotency_key=key,
            timeout_ms=1_000,
            max_attempts=3,
            request_payload=request_payload,
        )
    )


def test_export_service_runs_worker_buffer_readback_and_atomic_publication(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:service-success"
        )
        artifact = b"PK\x03\x04service-worker-success"
        worker = DeterministicWorkerTransport(
            lambda payload: _worker_success(payload, artifact)
        )
        store = BufferedArtifactStore(tmp_path / "service-artifacts")
        service = AuthoredVNextExportService(repository, worker, store)
        created = _create_service_job(
            service, deck=deck, digests=digests, key="service-success"
        )

        completed = asyncio.run(service.run_job(created.id))
        pointer, receipt = repository.get_active_artifact(deck.deck_id)
        downloaded = store.open_verified(
            receipt.storage_key, receipt.artifact_digest, receipt.size_bytes
        ).read_bytes()

        assert completed.state == "succeeded"
        assert pointer.digest == _digest(artifact)
        assert receipt.readback_verified is True
        assert downloaded == artifact
        assert len(worker.calls) == 1
        worker_payload, timeout_ms = worker.calls[0]
        assert timeout_ms == 1_000
        assert worker_payload["operationId"] == str(created.id)
        assert worker_payload["fencingToken"] == completed.fence_token
    finally:
        engine.dispose()


def test_export_service_resolves_owner_logo_bytes_into_verified_worker_asset(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    try:
        logo_bytes = (
            Path(__file__).resolve().parents[3] / "nextjs" / "public" / "Logo.png"
        ).read_bytes()
        asset_store = OwnerMasterAssetStore(tmp_path / "master-assets" / "owner-a")
        receipt = asset_store.publish_png(logo_bytes)
        contract_payload = master_contract_with_logo_payload()
        contract_payload["brandAssets"]["logo"]["assetId"] = receipt["assetId"]
        contract_payload["brandAssets"]["logo"]["digest"] = receipt["digest"]
        contract_payload["furniture"]["logo"]["assetId"] = receipt["assetId"]
        worker_asset = {
            "assetId": receipt["assetId"],
            "mediaType": "image/png",
            "sha256": str(receipt["digest"]).removeprefix("sha256:"),
            "bytesBase64": base64.b64encode(logo_bytes).decode("ascii"),
        }
        _master, deck, digests = _published_sql_deck(
            repository,
            "deck:service-logo",
            master_payload=contract_payload,
            assets=[worker_asset],
        )
        artifact = b"PK\x03\x04service-worker-logo-success"
        worker = DeterministicWorkerTransport(
            lambda payload: _worker_success(payload, artifact)
        )
        service = AuthoredVNextExportService(
            repository,
            worker,
            BufferedArtifactStore(tmp_path / "service-artifacts"),
            asset_store,
        )
        created = _create_service_job(
            service,
            deck=deck,
            digests=digests,
            key="service-logo",
            asset_digests=[str(receipt["digest"])],
        )
        asyncio.run(service.run_job(created.id))
        sent = worker.calls[0][0]
        assert sent["assets"] == [worker_asset]
        assert sent["master"]["furniture"][0]["assetSha256"] == worker_asset["sha256"]
        assert repository.get_active_artifact(deck.deck_id) is not None
    finally:
        engine.dispose()


def test_account_deletion_cancels_active_export_and_fences_late_publication(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    database_path = tmp_path / "authored-vnext.sqlite3"
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:account-deletion"
        )
        job = _create_sql_job(
            repository,
            deck=deck,
            digests=digests,
            key="account-deletion-active-export",
        )
        claimed = repository.claim_export_job(job.id, worker_id="worker-before-delete")

        async def delete_account() -> None:
            async_engine = create_async_engine(
                f"sqlite+aiosqlite:///{database_path}",
                connect_args={"timeout": 30.0},
            )
            sessions = async_sessionmaker(async_engine, expire_on_commit=False)
            try:
                async with sessions() as session:
                    marker = await mark_account_deleting(session, user_id=OWNER_ID)
                    assert marker.generation == 1
                async with sessions() as session:
                    assert (
                        await cancel_authored_vnext_exports_for_account(
                            session, owner_id=OWNER_ID
                        )
                        == 1
                    )
                    await session.commit()
            finally:
                await async_engine.dispose()

        asyncio.run(delete_account())

        cancelled = repository.get_export_job(job.id)
        assert cancelled.state == "cancelled"
        assert cancelled.cancel_requested is True
        assert cancelled.failure_code == "account_deletion_in_progress"
        assert cancelled.fence_token > claimed.fence_token
        assert cancelled.operation_fence > claimed.operation_fence

        late_artifact = _artifact_input(
            artifact_path=tmp_path / "artifacts" / "late.pptx",
            artifact=b"PK\x03\x04late-after-account-deletion",
            deck=deck,
            digests=digests,
        )
        with pytest.raises(OperationFenceError):
            repository.complete_export_job(
                job.id,
                fence_token=claimed.fence_token,
                artifact=late_artifact,
            )
        assert repository.get_active_artifact(deck.deck_id) is None
        with pytest.raises(OperationFenceError):
            _create_sql_job(
                repository,
                deck=deck,
                digests=digests,
                key="account-deletion-new-export",
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "failure",
    [
        WorkerError("authored_vnext_worker_timeout", retryable=True),
        WorkerError("authored_vnext_worker_unavailable", retryable=True),
    ],
)
def test_worker_failure_preserves_downloadable_last_known_good(
    tmp_path: Path,
    failure: WorkerError,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:last-known-good"
        )
        store = BufferedArtifactStore(tmp_path / "service-artifacts")
        good_bytes = b"PK\x03\x04active-before-failure"
        first_service = AuthoredVNextExportService(
            repository,
            DeterministicWorkerTransport(
                lambda payload: _worker_success(payload, good_bytes)
            ),
            store,
        )
        first = _create_service_job(
            first_service, deck=deck, digests=digests, key="last-known-good"
        )
        assert asyncio.run(first_service.run_job(first.id)).state == "succeeded"
        pointer_before, receipt_before = repository.get_active_artifact(deck.deck_id)

        failing_service = AuthoredVNextExportService(
            repository, DeterministicWorkerTransport(failure), store
        )
        second = _create_service_job(
            failing_service,
            deck=deck,
            digests=digests,
            key=f"failure-{failure.code}",
            active_revision=pointer_before.revision,
            active_digest=pointer_before.digest,
        )
        failed = asyncio.run(failing_service.run_job(second.id))
        pointer_after, receipt_after = repository.get_active_artifact(deck.deck_id)

        assert failed.state == "failed"
        assert failed.failure_code == failure.code
        assert (
            pointer_after.revision,
            pointer_after.generation,
            pointer_after.digest,
            receipt_after.id,
        ) == (
            pointer_before.revision,
            pointer_before.generation,
            pointer_before.digest,
            receipt_before.id,
        )
        assert store.open_verified(
            receipt_after.storage_key,
            receipt_after.artifact_digest,
            receipt_after.size_bytes,
        ).read_bytes() == good_bytes
    finally:
        engine.dispose()


def test_font_asset_and_worker_artifact_digest_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:digest-fail-closed"
        )
        store = BufferedArtifactStore(tmp_path / "service-artifacts")
        service = AuthoredVNextExportService(
            repository,
            DeterministicWorkerTransport(
                lambda payload: _worker_success(
                    payload,
                    b"PK\x03\x04actual-bytes",
                    artifact_digest=_digest(b"PK\x03\x04claimed-other-bytes"),
                )
            ),
            store,
        )
        with pytest.raises(ExportServiceError, match="font_digest_mismatch"):
            service.create_job(
                CreateJobCommand(
                    deck_id=deck.deck_id,
                    expected_deck_revision=deck.revision,
                    expected_deck_digest=deck.digest,
                    expected_master_revision=deck.master_revision,
                    expected_master_digest=deck.master_digest,
                    expected_active_artifact_revision=0,
                    expected_active_artifact_digest=None,
                    expected_source_package_digest=None,
                    expected_asset_digests=[],
                    expected_font_digest=_digest(b"wrong-font"),
                    idempotency_key="wrong-font",
                    timeout_ms=1_000,
                    max_attempts=1,
                    request_payload={"case": "wrong-font"},
                )
            )
        with pytest.raises(ExportServiceError, match="asset_digest_mismatch"):
            service.create_job(
                CreateJobCommand(
                    deck_id=deck.deck_id,
                    expected_deck_revision=deck.revision,
                    expected_deck_digest=deck.digest,
                    expected_master_revision=deck.master_revision,
                    expected_master_digest=deck.master_digest,
                    expected_active_artifact_revision=0,
                    expected_active_artifact_digest=None,
                    expected_source_package_digest=None,
                    expected_asset_digests=[_digest(b"unsupported-asset")],
                    expected_font_digest=_master.contract.font_manifest.digest,
                    idempotency_key="unsupported-asset",
                    timeout_ms=1_000,
                    max_attempts=1,
                    request_payload={"case": "unsupported-asset"},
                )
            )

        created = _create_service_job(
            service, deck=deck, digests=digests, key="worker-digest-mismatch"
        )
        failed = asyncio.run(service.run_job(created.id))
        assert failed.state == "failed"
        assert failed.failure_code == "authored_vnext_worker_artifact_digest_mismatch"
        assert repository.get_active_artifact(deck.deck_id) is None
        assert not list((tmp_path / "service-artifacts").rglob("*.pptx"))
    finally:
        engine.dispose()


def test_http_master_to_export_job_to_downloadable_pptx_and_duplicate_is_stable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AUTHORED_VNEXT_EXPORT_ENABLED", "true")
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:http-download"
        )
        artifact = b"PK\x03\x04http-downloadable-pptx"
        worker = DeterministicWorkerTransport(
            lambda payload: _worker_success(payload, artifact)
        )
        service = AuthoredVNextExportService(
            repository,
            worker,
            BufferedArtifactStore(tmp_path / "http-artifacts"),
        )
        app = FastAPI()
        app.include_router(AUTHORED_VNEXT_EXPORT_ROUTER, prefix="/api/v1/ppt")
        app.dependency_overrides[get_authored_vnext_export_service] = lambda: service
        request = {
            "deckId": deck.deck_id,
            "expectedDeckRevision": deck.revision,
            "expectedDeckDigest": deck.digest,
            "expectedMasterRevision": deck.master_revision,
            "expectedMasterDigest": deck.master_digest,
            "expectedActiveArtifactRevision": 0,
            "expectedActiveArtifactDigest": None,
            "expectedSourcePackageDigest": None,
            "expectedAssetDigests": [],
            "expectedFontDigest": _master.contract.font_manifest.digest,
            "idempotencyKey": "http-download",
            "timeoutMs": 1_000,
            "maxAttempts": 3,
        }

        with TestClient(app) as client:
            created = client.post(
                "/api/v1/ppt/authored-vnext/export/jobs", json=request
            )
            assert created.status_code == 202, created.text
            job = created.json()
            assert job["state"] == "queued"

            duplicate = client.post(
                "/api/v1/ppt/authored-vnext/export/jobs", json=request
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["jobId"] == job["jobId"]

            completed = client.post(
                f"/api/v1/ppt/authored-vnext/export/jobs/{job['jobId']}/run"
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["state"] == "succeeded"
            assert completed.json()["artifact"]["digest"] == _digest(artifact)

            status_response = client.get(
                f"/api/v1/ppt/authored-vnext/export/jobs/{job['jobId']}"
            )
            assert status_response.status_code == 200
            assert status_response.json() == completed.json()

            download = client.get(
                f"/api/v1/ppt/authored-vnext/export/decks/{deck.deck_id}/artifact"
            )
            assert download.status_code == 200, download.text
            assert download.content == artifact
            assert download.headers["content-type"] == PPTX_MEDIA_TYPE
            assert download.headers["etag"] == f'"{_digest(artifact)}"'
            assert "attachment" in download.headers["content-disposition"]

        assert len(worker.calls) == 1
    finally:
        engine.dispose()


def test_http_rollback_rejects_corrupt_target_and_preserves_lkg(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AUTHORED_VNEXT_EXPORT_ENABLED", "true")
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(
            repository, "deck:rollback-readback"
        )
        first_bytes = b"PK\x03\x04rollback-target"
        second_bytes = b"PK\x03\x04last-known-good"
        worker = DeterministicWorkerTransport(
            lambda payload: _worker_success(payload, first_bytes),
            lambda payload: _worker_success(payload, second_bytes),
        )
        service = AuthoredVNextExportService(
            repository,
            worker,
            BufferedArtifactStore(tmp_path / "rollback-artifacts"),
        )
        app = FastAPI()
        app.include_router(AUTHORED_VNEXT_EXPORT_ROUTER, prefix="/api/v1/ppt")
        app.dependency_overrides[get_authored_vnext_export_service] = lambda: service

        base_request = {
            "deckId": deck.deck_id,
            "expectedDeckRevision": deck.revision,
            "expectedDeckDigest": deck.digest,
            "expectedMasterRevision": deck.master_revision,
            "expectedMasterDigest": deck.master_digest,
            "expectedSourcePackageDigest": None,
            "expectedAssetDigests": [],
            "expectedFontDigest": _master.contract.font_manifest.digest,
            "timeoutMs": 1_000,
            "maxAttempts": 3,
        }
        with TestClient(app) as client:
            first_request = {
                **base_request,
                "expectedActiveArtifactRevision": 0,
                "expectedActiveArtifactDigest": None,
                "idempotencyKey": "rollback-target",
            }
            first_job = client.post(
                "/api/v1/ppt/authored-vnext/export/jobs", json=first_request
            ).json()
            first = client.post(
                f"/api/v1/ppt/authored-vnext/export/jobs/{first_job['jobId']}/run"
            ).json()["artifact"]

            second_request = {
                **base_request,
                "expectedActiveArtifactRevision": first["publicationRevision"],
                "expectedActiveArtifactDigest": first["digest"],
                "idempotencyKey": "last-known-good",
            }
            second_job = client.post(
                "/api/v1/ppt/authored-vnext/export/jobs", json=second_request
            ).json()
            second = client.post(
                f"/api/v1/ppt/authored-vnext/export/jobs/{second_job['jobId']}/run"
            ).json()["artifact"]

            target = repository.get_artifact_receipt(
                deck_id=deck.deck_id,
                artifact_id=uuid.UUID(first["artifactId"]),
            )
            Path(target.storage_key).write_bytes(b"corrupt")
            rollback = client.post(
                "/api/v1/ppt/authored-vnext/export/decks/"
                f"{deck.deck_id}/artifacts/{first['artifactId']}/rollback",
                json={
                    "expectedPublicationRevision": second["publicationRevision"],
                    "expectedArtifactDigest": second["digest"],
                },
            )
            assert rollback.status_code == 422
            active = repository.get_active_artifact(deck.deck_id)
            assert active is not None
            assert str(active[1].id) == second["artifactId"]
            assert active[1].artifact_digest == second["digest"]
    finally:
        engine.dispose()
