"""Durable authored-vNext export integration and failure-safety tests.

The fake transport implements the authenticated Next worker's JSON boundary;
it never invokes a provider, browser, or PowerPoint process.
"""

from __future__ import annotations

import asyncio
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

from services.authored_vnext.integration.artifact_store import (
    ArtifactStoreError,
    BufferedArtifactStore,
)
from services.authored_vnext.integration.export_payload import (
    build_worker_request,
    canonical_json,
    raw_sha256,
)
from services.authored_vnext.integration.export_service import (
    AuthoredVNextExportService,
    CreateJobCommand,
)
from tests.support.authored_vnext_master_memory_repository import (
    InMemoryAuthoredVNextMasterRepository,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
    ExportArtifactInput,
    OperationFenceError,
    SourceDigestMismatchError,
)
from models.authored_vnext.master.contract import DeckMasterContract
from models.sql.user import User
from models.sql.account_deletion_request import AccountDeletionRequest
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
)
from services.account_deletion_service import (
    cancel_authored_vnext_exports_for_account,
    mark_account_deleting,
)


PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
OWNER_ID = uuid.uuid5(uuid.NAMESPACE_URL, "authored-vnext-export-owner-a")


class _SceneSnapshot(DeckGenerationSnapshot):
    """Keep the fixture's master aliases while persisting the canonical snapshot."""

    @property
    def master_id(self):
        return self.master.master_id

    @property
    def master_revision(self):
        return self.master.master_revision

    @property
    def master_digest(self):
        return self.master.master_digest


def _scene_snapshot(deck, published, *, revision=None):
    from models.authored_vnext.deck.scene import (
        SceneEditability,
        SceneTextElement,
        SceneTextStyle,
        seal_deck_scene,
        seal_slide_scene,
    )
    from models.authored_vnext.master.contract import Rect
    from services.authored_vnext.generation.scene_compiler import default_scene_capabilities

    revision = revision or deck.revision
    source_digest = f"sha256:{raw_sha256([s.model_dump(mode='json', by_alias=True) for s in deck.slides])}"
    layout_digest = "sha256:" + "8" * 64
    scenes = [
        seal_slide_scene(
            slide_id=slide.slide_id,
            revision=slide.revision,
            elements=[
                SceneTextElement(
                    kind="text",
                    element_id=f"{slide.slide_id}:title",
                    z_order=1,
                    bounds=Rect(x=60, y=80, width=800, height=140),
                    semantic_role="title",
                    editability=SceneEditability(text=True),
                    text=slide.title.text,
                    style=SceneTextStyle(font_family=slide.title_fit.font_family, font_size=24),
                ),
                SceneTextElement(
                    kind="text",
                    element_id=f"{slide.slide_id}:body",
                    z_order=2,
                    bounds=Rect(x=60, y=240, width=800, height=220),
                    semantic_role="body",
                    editability=SceneEditability(text=True),
                    text=slide.body,
                    style=SceneTextStyle(font_family=slide.title_fit.font_family, font_size=16),
                ),
            ],
        )
        for slide in deck.slides
    ]
    scene = seal_deck_scene(
        deck_id=deck.deck_id,
        revision=revision,
        composition_digest=published.digest,
        source_digest=source_digest,
        slides=scenes,
        capabilities=default_scene_capabilities(),
    )
    snapshot = _SceneSnapshot(
        deck_id=deck.deck_id,
        revision=revision,
        digest=deck.digest,
        master=MasterDigestPin(
            master_id=deck.master_id,
            master_revision=deck.master_revision,
            master_digest=published.digest,
            layout_registry_digest=layout_digest,
            font_manifest_digest=deck.slides[0].title_fit.font_manifest_digest,
        ),
        source_digest=source_digest,
        scene=scene,
        slides=[
            ValidatedSlideSpecV2(
                slide=slide,
                scene=slide_scene,
                checkpoint=SlideValidationCheckpoint(
                    master_digest=published.digest,
                    layout_registry_digest=layout_digest,
                    font_manifest_digest=slide.title_fit.font_manifest_digest,
                    source_digest=source_digest,
                    visual_plan_digest="sha256:" + "9" * 64,
                    scene_digest=slide_scene.slide_digest,
                    quality_policy_version="quality/v1",
                    layout_id=slide.layout_id,
                    body_slot_id="slot:long-body",
                    validated_at="2026-08-13T12:00:00Z",
                ),
            )
            for slide, slide_scene in zip(deck.slides, scenes, strict=True)
        ],
        created_at="2026-08-13T12:00:00Z",
    )
    return snapshot.model_copy(
        update={"digest": f"sha256:{raw_sha256(snapshot.model_dump(mode='json', by_alias=True, exclude={'digest'}))}"}
    )


def _create_owner(engine) -> None:
    User.__table__.create(engine, checkfirst=True)
    AccountDeletionRequest.__table__.create(engine, checkfirst=True)
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

    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    repository = AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(OWNER_ID),
        tenant_id="tenant-a",
        owner_id=OWNER_ID,
    )
    try:
        claimed = repository.claim_export_job(uuid.UUID(job_id), worker_id=worker_id)
        queue.put(("claimed", worker_id, claimed.fence_token))
    except OperationFenceError:
        queue.put(("fenced", worker_id, None))
    finally:
        engine.dispose()


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def test_worker_canonical_json_normalizes_integral_numbers_and_unicode_nfc() -> None:
    assert canonical_json({"text": "e\u0301", "value": 76.0}) == '{"text":"é","value":76}'


class DeterministicWorkerTransport:
    """Scriptable fake for retry, timeout, corruption, and fencing tests."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[dict[str, Any], int]] = []

    async def execute(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
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
    scoped_key, scoped_size = store.publish(
        artifact,
        digest,
        storage_namespace="a" * 32,
    )
    scoped_reopened = store.open_verified(scoped_key, digest, scoped_size)

    assert reopened.read_bytes() == artifact
    assert (duplicate_key, duplicate_size) == (storage_key, byte_length)
    assert scoped_key != storage_key
    assert scoped_reopened.read_bytes() == artifact
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


def test_published_master_and_deck_build_exact_versioned_worker_digest_envelope() -> None:
    repository = InMemoryAuthoredVNextMasterRepository()
    master_service = AuthoredVNextMasterService(repository)
    draft = master_service.create_draft(
        CreateDraftRequest(contract=DeckMasterContract.model_validate(master_contract_payload()))
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

    legacy_deck = deck
    deck = _scene_snapshot(legacy_deck, published)
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
    assert all(slide["values"] == {} for slide in payload["slides"])
    assert [slide["elements"][0]["text"] for slide in payload["scene"]["slides"]] == [
        slide.title.text for slide in legacy_deck.slides
    ]
    assert [item["kind"] for item in payload["master"]["furniture"]] == ["logo", "text", "text", "line", "pageNumber"]
    assert [(item["id"], item["kind"]) for item in payload["master"]["layouts"]] == [
        ("layout:cover", "cover"),
        ("layout:section", "section"),
        ("layout:standard-title", "standard"),
        ("layout:long-title", "long-title"),
    ]
    assert payload["master"]["layouts"][2]["placeholders"][0]["textStyle"]["maxLines"] == 2
    assert payload["master"]["layouts"][3]["placeholders"][0]["textStyle"]["maxLines"] >= 3
    assert payload["master"]["fontManifest"]["files"][0]["path"].endswith("malgun.ttf")
    assert payload["digests"] == {
        "masterSha256": raw_sha256(payload["master"]),
        "specSha256": deck.scene.scene_digest.removeprefix("sha256:"),
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

    generated = _scene_snapshot(legacy_deck.model_copy(update={"slides": legacy_deck.slides[:1]}), published)
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
        CreateDraftRequest(contract=DeckMasterContract.model_validate(master_payload or master_contract_payload()))
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
    generated = _scene_snapshot(deck, published, revision=deck.revision + 1)
    repository.append_deck(generated, expected_revision=deck.revision, expected_digest=deck.digest)
    deck = generated
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
        _master, deck, digests = _published_sql_deck(repository, "deck:durable-export")
        job = _create_sql_job(repository, deck=deck, digests=digests, key="first-publication")
        duplicate = _create_sql_job(repository, deck=deck, digests=digests, key="first-publication")
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
        failed_claim = repository.claim_export_job(failed_job.id, worker_id="worker-b")
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

        unchanged_pointer, unchanged_active = repository.get_active_artifact(deck.deck_id)
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
        _master, deck, digests = _published_sql_deck(repository, "deck:retry-resume")
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
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    _create_owner(engine)
    AuthoredVNextSqlRepository.create_schema(engine)
    repository = AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(OWNER_ID),
        tenant_id="tenant-a",
        owner_id=OWNER_ID,
    )
    _master, deck, digests = _published_sql_deck(repository, "deck:multiprocess-cas")
    job = _create_sql_job(repository, deck=deck, digests=digests, key="multiprocess-cas")
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
        # Windows spawn imports the full application model graph; keep the
        # concurrency assertion bounded without assuming warm imports.
        process.join(timeout=60)
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


def test_account_deletion_cancels_active_export_and_fences_late_publication(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    database_path = tmp_path / "authored-vnext.sqlite3"
    try:
        _master, deck, digests = _published_sql_deck(repository, "deck:account-deletion")
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
                    assert await cancel_authored_vnext_exports_for_account(session, owner_id=OWNER_ID) == 1
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


def test_unexpected_worker_failure_is_durably_failed_and_retryable(
    tmp_path: Path,
) -> None:
    engine, repository = _sql_repository(tmp_path)
    try:
        _master, deck, digests = _published_sql_deck(repository, "deck:unexpected-worker-failure")
        service = AuthoredVNextExportService(
            repository,
            DeterministicWorkerTransport(RuntimeError("unexpected adapter failure")),
            BufferedArtifactStore(tmp_path / "service-artifacts"),
        )
        created = _create_service_job(
            service,
            deck=deck,
            digests=digests,
            key="unexpected-worker-failure",
        )

        failed = asyncio.run(service.run_job(created.id))
        assert failed.state == "failed"
        assert failed.failure_code == "authored_vnext_export_failed"
        assert failed.request_payload

        queued = repository.retry_export_job(
            failed.id,
            expected_operation_fence=failed.operation_fence,
        )
        assert queued.state == "queued"
    finally:
        engine.dispose()


