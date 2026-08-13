from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from models.authored_vnext.master.contract import DeckMasterContract
from models.sql.user import User
from models.authored_vnext.master.operations import (
    CreateDeckRequest,
    CreateDraftRequest,
    MasterCasRequest,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
    ExportArtifactInput,
    OperationFenceError,
    PublicationConflictError,
    SourceDigestMismatchError,
)
from services.authored_vnext.master.errors import (
    MasterNotFoundError,
    MasterRevisionConflictError,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from tests.support.authored_vnext_master_fixture import (
    long_title_slide_payload,
    master_contract_payload,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def _repository(path: Path, *, owner: str = "owner-a") -> AuthoredVNextSqlRepository:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    User.__table__.create(engine, checkfirst=True)
    AuthoredVNextSqlRepository.create_schema(engine)
    owner_id = uuid.uuid5(uuid.NAMESPACE_URL, f"authored-vnext-test:{owner}")
    with Session(engine) as session:
        if session.get(User, owner_id) is None:
            session.add(
                User(
                    id=owner_id,
                    username=f"{owner}-{owner_id.hex[:8]}",
                    hashed_password="test-only",
                )
            )
            session.commit()
    return AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(owner_id),
        tenant_id="tenant-a",
        owner_id=owner_id,
    )


def _publish_process(
    path: str,
    master_id: str,
    revision: int,
    digest: str,
    results,
) -> None:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    service = AuthoredVNextMasterService(
        AuthoredVNextSqlRepository(
            engine,
            owner_scope=str(
                uuid.uuid5(uuid.NAMESPACE_URL, "authored-vnext-test:owner-a")
            ),
            tenant_id="tenant-a",
            owner_id=uuid.uuid5(
                uuid.NAMESPACE_URL, "authored-vnext-test:owner-a"
            ),
        )
    )
    try:
        service.publish(
            master_id,
            MasterCasRequest(expected_revision=revision, expected_digest=digest),
        )
        results.put("published")
    except MasterRevisionConflictError:
        results.put("conflict")
    finally:
        engine.dispose()


def _published_deck(repository: AuthoredVNextSqlRepository):
    service = AuthoredVNextMasterService(repository)
    draft = service.create_draft(
        CreateDraftRequest(contract=DeckMasterContract.model_validate(master_contract_payload()))
    )
    master = service.publish(
        draft.master_id,
        MasterCasRequest(
            expected_revision=draft.revision,
            expected_digest=draft.digest,
        ),
    )
    deck = service.create_deck(
        CreateDeckRequest(
            master_id=master.master_id,
            master_revision=master.revision,
            master_digest=master.digest,
            slides=[long_title_slide_payload()],
            deck_id="deck:sql-test",
        )
    )
    return master, deck


def _job(repository, master, deck, key="export-one"):
    return repository.create_export_job(
        deck_id=deck.deck_id,
        deck_revision=deck.revision,
        deck_digest=deck.digest,
        master_revision=master.revision,
        master_digest=master.digest,
        spec_digest=deck.slides[0].spec_digest,
        asset_manifest_digest=DIGEST_A,
        font_manifest_digest=master.contract.font_manifest.digest,
        expected_package_digest=DIGEST_B,
        idempotency_key=key,
        request_payload={"deckDigest": deck.digest, "format": "pptx"},
    )


def _artifact(master, deck, *, digest=DIGEST_C):
    return ExportArtifactInput(
        storage_key=f"sha256/{digest.removeprefix('sha256:')}.pptx",
        artifact_digest=digest,
        readback_digest=digest,
        package_digest=DIGEST_B,
        size_bytes=42,
        master_digest=master.digest,
        spec_digest=deck.slides[0].spec_digest,
        asset_manifest_digest=DIGEST_A,
        font_manifest_digest=master.contract.font_manifest.digest,
    )


def test_master_cas_is_process_safe_and_owner_scoped(tmp_path: Path) -> None:
    path = tmp_path / "cas.db"
    repository = _repository(path)
    service = AuthoredVNextMasterService(repository)
    draft = service.create_draft(
        CreateDraftRequest(contract=DeckMasterContract.model_validate(master_contract_payload()))
    )

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [
        context.Process(
            target=_publish_process,
            args=(
                str(path),
                draft.master_id,
                draft.revision,
                draft.digest,
                results,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=5) for _ in processes]
    assert sorted(outcomes) == ["conflict", "published"]
    assert repository.get_master(draft.master_id).revision == 2
    with pytest.raises(MasterNotFoundError):
        _repository(path, owner="owner-b").get_master(draft.master_id)


def test_export_fence_fail_closed_and_last_known_good(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "export.db")
    master, deck = _published_deck(repository)
    job = _job(repository, master, deck)
    assert _job(repository, master, deck).id == job.id
    # A concurrent request captures the same empty publication pointer.
    stale = _job(repository, master, deck, key="export-two")
    claimed = repository.claim_export_job(job.id, worker_id="worker-a")
    receipt = repository.complete_export_job(
        job.id,
        fence_token=claimed.fence_token,
        artifact=_artifact(master, deck),
    )
    pointer, active = repository.get_active_artifact(deck.deck_id) or (None, None)
    assert active.id == receipt.id
    assert pointer.digest == DIGEST_C

    stale_claim = repository.claim_export_job(stale.id, worker_id="worker-old")
    repository.fail_export_job(
        stale.id,
        fence_token=stale_claim.fence_token,
        failure_code="worker_timeout",
    )
    repository.retry_export_job(stale.id)
    new_claim = repository.claim_export_job(stale.id, worker_id="worker-new")
    with pytest.raises(OperationFenceError):
        repository.complete_export_job(
            stale.id,
            fence_token=stale_claim.fence_token,
            artifact=_artifact(master, deck, digest=DIGEST_D),
        )
    with pytest.raises(PublicationConflictError):
        repository.complete_export_job(
            stale.id,
            fence_token=new_claim.fence_token,
            artifact=_artifact(master, deck, digest=DIGEST_D),
        )
    assert repository.get_active_artifact(deck.deck_id)[1].id == receipt.id

    mismatch = _job(repository, master, deck, key="export-three")
    mismatch_claim = repository.claim_export_job(mismatch.id, worker_id="worker-mismatch")
    bad = _artifact(master, deck, digest=DIGEST_D)
    bad = ExportArtifactInput(**{**bad.__dict__, "readback_digest": DIGEST_A})
    with pytest.raises(SourceDigestMismatchError):
        repository.complete_export_job(mismatch.id, fence_token=mismatch_claim.fence_token, artifact=bad)
    assert repository.get_active_artifact(deck.deck_id)[1].id == receipt.id

    replacement = _job(repository, master, deck, key="export-four")
    replacement_claim = repository.claim_export_job(replacement.id, worker_id="worker-replacement")
    repository.complete_export_job(
        replacement.id,
        fence_token=replacement_claim.fence_token,
        artifact=_artifact(master, deck, digest=DIGEST_D),
    )
    current_pointer, _ = repository.get_active_artifact(deck.deck_id)
    rolled_back = repository.rollback_publication(
        deck_id=deck.deck_id,
        target_artifact_id=receipt.id,
        expected_generation=current_pointer.generation,
        expected_digest=current_pointer.digest,
    )
    assert rolled_back.digest == receipt.artifact_digest
    with pytest.raises(PublicationConflictError):
        repository.rollback_publication(
            deck_id=deck.deck_id,
            target_artifact_id=receipt.id,
            expected_generation=current_pointer.generation,
            expected_digest=current_pointer.digest,
        )


def test_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "d3e7a9c2b4f6")
    engine = create_engine(url)
    assert "authored_vnext_export_jobs" not in inspect(engine).get_table_names()
    command.upgrade(config, "e4f8a2c6d0b3")
    assert {
        "authored_vnext_master_revisions",
        "authored_vnext_slide_revisions",
        "authored_vnext_deck_revisions",
        "authored_vnext_export_jobs",
        "authored_vnext_export_artifact_receipts",
        "authored_vnext_render_receipts",
        "authored_vnext_publication_pointers",
    } <= set(inspect(engine).get_table_names())
    owner_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            User(
                id=owner_id,
                username=f"migration-owner-{owner_id.hex[:8]}",
                hashed_password="test-only",
            )
        )
        session.execute(
            text(
                "INSERT INTO authored_vnext_master_revisions "
                "(id, owner_scope, tenant_id, master_id, revision, digest, "
                "status, payload) VALUES (:id, :scope, 'tenant-a', "
                "'master:migration', 1, :digest, 'draft', :payload)"
            ),
            {
                "id": uuid.uuid4().hex,
                "scope": str(owner_id),
                "digest": DIGEST_A,
                "payload": json.dumps({"schemaVersion": "test"}),
            },
        )
        session.commit()
    command.upgrade(config, "head")
    hardened = inspect(engine)
    owner_column = next(
        column
        for column in hardened.get_columns("authored_vnext_master_revisions")
        if column["name"] == "owner_id"
    )
    assert owner_column["nullable"] is False
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT owner_id FROM authored_vnext_master_revisions "
                "WHERE master_id = 'master:migration'"
            )
        ) == owner_id.hex
    command.downgrade(config, "d3e7a9c2b4f6")
    assert "authored_vnext_export_jobs" not in inspect(engine).get_table_names()
    engine.dispose()
