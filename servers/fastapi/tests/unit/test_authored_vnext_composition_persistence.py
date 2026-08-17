"""Focused immutable composition and SQL persistence vertical slice."""

from __future__ import annotations

import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models.authored_vnext.deck import GenerateDeckRequest, MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import CreateDraftRequest, MasterCasRequest
from models.sql.authored_vnext_generation_operation import (
    AuthoredVNextGenerationOperation,
)
from models.sql.user import User
from services.authored_vnext.generation.core_bridge import DeterministicCoreBridge
from services.authored_vnext.generation.effective_style import (
    resolve_authored_composition,
)
from services.authored_vnext.generation.pipeline import AuthoredVNextGenerationPipeline
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.integration.sql_repository import AuthoredVNextSqlRepository
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.master.service import AuthoredVNextMasterService
from tests.support.authored_vnext_master_fixture import master_contract_payload
from utils.authored_styles import AuthoredStyle


def test_composition_pin_is_persisted_through_request_slides_result_and_export(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'composition.db'}",
        connect_args={"check_same_thread": False},
    )
    User.__table__.create(engine)
    owner_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(User(id=owner_id, username="composition", hashed_password="test"))
        session.commit()
    base = AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(owner_id),
        tenant_id="default",
        owner_id=owner_id,
    )
    base.create_schema(engine)
    AuthoredVNextGenerationOperation.__table__.create(engine)
    master_service = AuthoredVNextMasterService(base)
    draft = master_service.create_draft(
        CreateDraftRequest(
            contract=DeckMasterContract.model_validate(master_contract_payload())
        )
    )
    master = master_service.publish(
        draft.master_id,
        MasterCasRequest(
            expected_revision=draft.revision,
            expected_digest=draft.digest,
        ),
    )
    composition, effective_master = resolve_authored_composition(
        master.contract,
        style=AuthoredStyle(
            id="composition-test",
            name="Composition test",
            description="Persisted authored visual",
            preview_bg="#F8FAFC",
            preview_accent="#2563EB",
            primary_color="#2563EB",
            background_color="#F8FAFC",
            heading_font=master.contract.font_manifest.primary,
            body_font=master.contract.font_manifest.primary,
            brief="balanced density with panels, tables, charts and process",
        ),
    )
    assert effective_master.digest == composition.effective_contract_digest
    pin = MasterDigestPin(
        master_id=master.master_id,
        master_revision=master.revision,
        master_digest=master.digest,
        layout_registry_digest=canonical_digest(
            master.contract.layouts.model_dump(mode="json", by_alias=True)
        ),
        font_manifest_digest=master.contract.font_manifest.digest,
    )
    request = GenerateDeckRequest(
        operation_id="operation:composition-test",
        deck_id="deck:composition-test",
        master=pin,
        composition=composition,
        outline={
            "sourceDigest": "sha256:" + "9" * 64,
            "deckTitle": "Composition persistence",
            "items": [
                {
                    "itemId": "outline:0",
                    "index": 0,
                    "title": "고정된 합성 계약",
                    "summary": "마스터 슬롯 안에서 authored style을 적용합니다.",
                }
            ],
        },
        quality_policy_version="authored-vnext-quality/v1",
        publication_fence={
            "expectedDeckRevision": 0,
            "expectedActiveRevisionDigest": None,
        },
    )
    repository = SqlGenerationRepository(base)
    repository.begin_operation(
        operation_id=request.operation_id,
        deck_id=request.deck_id,
        kind="generate",
        idempotency_key="composition-persistence-test",
        request=request,
        expected_revision=None,
        expected_digest=None,
    )
    repository.claim_operation(request.operation_id, worker_id="test-worker")
    bridge = DeterministicCoreBridge()

    result = AuthoredVNextGenerationPipeline(repository, bridge).generate(request)

    expected = composition.model_dump(mode="json", by_alias=True)
    row = repository.get_operation_row(request.operation_id)
    assert row.request_payload["composition"] == expected
    assert row.checkpoint_payload["composition"] == expected
    assert row.result_payload["composition"] == expected
    assert repository.get_operation(request.operation_id).composition == composition
    persisted_deck = repository.get_deck(request.deck_id)
    assert persisted_deck.composition == composition
    assert all(
        slide.checkpoint.composition == composition for slide in persisted_deck.slides
    )
    assert result.checkpoint.composition == composition
    assert bridge.calls[0]["checkpoint"]["composition"] == expected
