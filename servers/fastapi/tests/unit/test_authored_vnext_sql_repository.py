from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import sqlite3
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from models.authored_vnext.master.contract import DeckMasterContract
from models.sql.user import User
from models.authored_vnext.master.operations import (
    CreateDraftRequest,
    MasterCasRequest,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from services.authored_vnext.master.errors import (
    MasterNotFoundError,
    MasterRevisionConflictError,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
import services.authored_vnext.generation.sql_repository as generation_sql_repository
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from tests.support.authored_vnext_master_fixture import (
    master_contract_payload,
)


DIGEST_A = "sha256:" + "a" * 64


def _repository(
    path: Path,
    *,
    owner: str = "owner-a",
    timeout: float = 30,
) -> AuthoredVNextSqlRepository:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": timeout},
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


def test_candidate_write_recovers_sqlite_lock_within_first_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "candidate-lock.db"
    base = _repository(path, timeout=0)
    repository = SqlGenerationRepository(base)
    lock_holder = sqlite3.connect(path, timeout=0, check_same_thread=False)
    lock_holder.execute("BEGIN IMMEDIATE")
    delays: list[float] = []

    def release_lock(delay: float) -> None:
        delays.append(delay)
        lock_holder.commit()

    monkeypatch.setattr(generation_sql_repository.time, "sleep", release_lock)
    digest = "sha256:" + "1" * 64
    payload = {"schema_version": "authored-vnext-ai-text-zones/v1", "values": []}
    try:
        repository.save_candidate(digest, payload)
        repository.save_candidate(digest, payload)
    finally:
        lock_holder.close()

    assert delays == [generation_sql_repository.SQLITE_LOCK_RETRY_DELAYS[0]]
    assert repository.get_candidate(digest) == payload
    with Session(base.engine) as session:
        assert session.scalar(
            text(
                "SELECT count(*) FROM authored_vnext_candidates "
                "WHERE candidate_digest = :digest"
            ),
            {"digest": digest},
        ) == 1


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
