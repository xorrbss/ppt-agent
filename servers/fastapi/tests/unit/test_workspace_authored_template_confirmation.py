import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.generate_presentation_request import GeneratePresentationRequest
from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import WorkspaceTemplateApplicationRequest
from api.v1.ppt.endpoints import workspace as workspace_endpoint
from api.v1.ppt.endpoints.presentation_crud import get_presentation
from services.workspace_authored_template_confirmation_service import (
    confirm_authored_template_application,
)
from services.workspace_service import WorkspaceServiceError
from services.authored_vnext.generation.effective_style import (
    resolve_authored_composition,
)
from services.authored_vnext.master.digests import canonical_digest, seal_master_contract
from tests.support.authored_vnext_master_fixture import master_contract_payload
from utils.authored_styles import resolve_authored_style
from utils.owner_context import reset_current_owner_id, set_current_owner_id


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceMessageAttachment.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _source() -> PresentationModel:
    return PresentationModel(
        content="source manuscript",
        n_slides=2,
        language="Korean",
        outlines={"slides": [{"content": "one"}, {"content": "two"}]},
        mode="authored",
        layout=None,
        lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
        revision=7,
    )


def _request(*, style: str = "signal", revision: int = 7):
    return WorkspaceTemplateApplicationRequest(
        source_revision=revision,
        prompt="새 템플릿으로 다시 만들어 주세요",
        authored_style=style,
        vision_qa=True,
    )


async def _builder(source_id, request, _session):
    return GeneratePresentationRequest(
        content="source manuscript",
        slides_markdown=["one", "two"],
        language="Korean",
        template="authored",
        authored_style=request.authored_style,
        master_pin=request.master_pin,
        vision_qa=request.vision_qa,
    )


def _master_contract() -> DeckMasterContract:
    return seal_master_contract(
        DeckMasterContract.model_validate(deepcopy(master_contract_payload()))
    )


def test_confirm_is_atomic_idempotent_and_target_owned(tmp_path: Path):
    asyncio.run(_confirm_is_atomic_idempotent_and_target_owned(tmp_path))


async def _confirm_is_atomic_idempotent_and_target_owned(tmp_path: Path):
    engine, maker = await _database(tmp_path / "confirm.db")
    try:
        async with maker() as session:
            source = _source()
            session.add(source)
            await session.commit()
            source_id = source.id

        async with maker() as session:
            first = await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=_request(),
                idempotency_key="confirm-1",
                generation_request_builder=_builder,
            )
        async with maker() as session:
            replay = await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=_request(),
                idempotency_key="confirm-1",
                generation_request_builder=_builder,
            )

        assert first.duplicate is False
        assert replay.duplicate is True
        assert replay.target_presentation_id == first.target_presentation_id
        assert replay.operation.id == first.operation.id
        assert replay.message.id == first.message.id

        async with maker() as session:
            presentations = (
                await session.execute(select(PresentationModel))
            ).scalars().all()
            operations = (
                await session.execute(select(WorkspaceOperation))
            ).scalars().all()
            conversations = (
                await session.execute(select(WorkspaceConversation))
            ).scalars().all()
            messages = (
                await session.execute(select(WorkspaceMessage))
            ).scalars().all()
            jobs = (
                await session.execute(select(PresentationGenerationJob))
            ).scalars().all()
            tasks = (
                await session.execute(select(AsyncPresentationGenerationTaskModel))
            ).scalars().all()

        assert len(presentations) == 2
        target = next(item for item in presentations if item.id != source_id)
        assert target.id == first.target_presentation_id
        assert target.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
        assert target.revision == 0
        assert len(conversations) == len(messages) == len(operations) == len(jobs) == len(tasks) == 1
        assert conversations[0].presentation_id == target.id
        assert messages[0].content == _request().prompt
        assert operations[0].presentation_id == target.id
        assert operations[0].kind == "design.apply"
        assert operations[0].expected_revision == 0
        assert operations[0].request_payload["source_presentation_id"] == str(source_id)
        assert operations[0].request_payload["source_revision"] == 7
        settings = operations[0].request_payload["generation_settings"]
        assert settings["authored_style"] == "signal"
        assert settings["authored_style_intent"] == {
            "intent": "set",
            "styleId": "signal",
        }
        assert settings["master_intent"] == {"intent": "keep", "pin": None}
        assert settings["resolved_master_pin"] is None
        assert settings["resolved_composition_pin"] is None
        assert settings["resolved_style_digest"].startswith("sha256:")
        assert settings["design_signature"].startswith("sha256:")
        assert jobs[0].request_payload["_internal_authored_design_replay"] is True
        assert jobs[0].presentation_id == target.id
        assert jobs[0].workspace_operation_id == operations[0].id
        assert jobs[0].request_payload["presentation_id"] == str(target.id)

        async with maker() as session:
            staging_view = await get_presentation(target.id, session)
        assert staging_view.id == target.id
    finally:
        await engine.dispose()


def test_confirm_openapi_contract_uses_required_idempotency_header():
    app = FastAPI()
    app.include_router(workspace_endpoint.WORKSPACE_ROUTER)

    operation = app.openapi()["paths"][
        "/workspace/presentations/{source_presentation_id}/template-applications"
    ]["post"]

    header = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )
    assert header["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkspaceTemplateApplicationRequest"
    }
    assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkspaceTemplateApplicationResponse"
    }


def test_design_intents_distinguish_keep_set_and_clear():
    legacy = _request()
    assert legacy.authored_style.intent == "set"
    assert legacy.authored_style.style_id == "signal"
    assert legacy.master.intent == "keep"

    explicit = WorkspaceTemplateApplicationRequest.model_validate(
        {
            "source_revision": 7,
            "prompt": "디자인 변경",
            "authoredStyle": {"intent": "keep"},
            "master": {"intent": "clear"},
            "visionQa": False,
        }
    )
    assert explicit.authored_style.intent == "keep"
    assert explicit.master.intent == "clear"


def test_design_admission_resolves_keep_and_clear_before_enqueue(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_design_admission_resolves_keep_and_clear(tmp_path, monkeypatch))


async def _design_admission_resolves_keep_and_clear(tmp_path: Path, monkeypatch):
    from api.v1.ppt.endpoints import presentation_generate

    owner_id = uuid.uuid4()
    owner_token = set_current_owner_id(owner_id)
    contract = _master_contract()
    assert contract.digest is not None
    assert contract.font_manifest.digest is not None
    master_pin = MasterDigestPin(
        master_id="master:design-admission",
        master_revision=3,
        master_digest=contract.digest,
        layout_registry_digest=canonical_digest(
            contract.layouts.model_dump(mode="json", by_alias=True)
        ),
        font_manifest_digest=contract.font_manifest.digest,
    )
    source_composition, _ = resolve_authored_composition(
        contract,
        style=resolve_authored_style("signal"),
    )

    def resolve_kept_master(*, owner_id, requested, require_current_head=True):
        assert owner_id is not None
        assert requested.revision == master_pin.master_revision
        assert require_current_head is False
        return SimpleNamespace(
            owner_id=owner_id,
            pin=master_pin,
            contract=contract,
        )

    monkeypatch.setattr(
        "services.workspace_authored_template_confirmation_service.resolve_legacy_master_pin",
        resolve_kept_master,
    )

    async def admit(request, _session):
        if request.master_pin is not None:
            assert request._authored_master_pin == master_pin
            assert request._authored_master_resolution.contract == contract
            assert request._authored_composition_pin.style_id == "osb-smart-banking"
        return (uuid.uuid4(),)

    monkeypatch.setattr(
        presentation_generate,
        "check_if_api_request_is_valid",
        admit,
    )

    source = _source()
    source.theme = {
        "mode": "authored",
        "style": "signal",
        "authored_vnext": {
            "schemaVersion": "legacy-authored-vnext-projection/v1",
            "masterPin": master_pin.model_dump(mode="json", by_alias=True),
            "compositionPin": source_composition.model_dump(
                mode="json", by_alias=True
            ),
        },
    }
    engine, maker = await _database(tmp_path / "design-intents.db")
    try:
        async with maker() as session:
            session.add(
                User(
                    id=owner_id,
                    username=f"workspace-keep-{owner_id.hex[:8]}",
                    hashed_password="test-only",
                )
            )
            source.owner_id = owner_id
            session.add(source)
            await session.commit()
            source_id = source.id

        set_style_keep_master = WorkspaceTemplateApplicationRequest.model_validate(
            {
                "source_revision": 7,
                "prompt": "본문 스타일 변경",
                "authoredStyle": {
                    "intent": "set",
                    "styleId": "osb-smart-banking",
                },
                "master": {"intent": "keep"},
            }
        )
        keep_style_clear_master = WorkspaceTemplateApplicationRequest.model_validate(
            {
                "source_revision": 7,
                "prompt": "마스터 제거",
                "authoredStyle": {"intent": "keep"},
                "master": {"intent": "clear"},
            }
        )
        async with maker() as session:
            kept = await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=set_style_keep_master,
                idempotency_key="keep-master",
                generation_request_builder=_builder,
            )
        async with maker() as session:
            cleared = await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=keep_style_clear_master,
                idempotency_key="clear-master",
                generation_request_builder=_builder,
            )
        async with maker() as session:
            kept_operation = await session.get(WorkspaceOperation, kept.operation.id)
            cleared_operation = await session.get(
                WorkspaceOperation, cleared.operation.id
            )
            kept_job = (
                await session.execute(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.workspace_operation_id
                        == kept.operation.id
                    )
                )
            ).scalar_one()

        kept_settings = kept_operation.request_payload["generation_settings"]
        assert kept_settings["authored_style"] == "osb-smart-banking"
        assert kept_settings["master_intent"] == {"intent": "keep", "pin": None}
        assert kept_settings["resolved_master_pin"] == master_pin.model_dump(
            mode="json", by_alias=True
        )
        assert kept_settings["resolved_composition_pin"]["styleId"] == (
            "osb-smart-banking"
        )
        assert kept_job.request_payload["master_pin"] == {
            "master_id": master_pin.master_id,
            "revision": master_pin.master_revision,
            "digest": master_pin.master_digest,
        }
        assert kept_job.request_payload[
            "_internal_authored_composition_pin"
        ] == kept_settings["resolved_composition_pin"]

        cleared_settings = cleared_operation.request_payload["generation_settings"]
        assert cleared_settings["authored_style"] == "signal"
        assert cleared_settings["master_intent"] == {
            "intent": "clear",
            "pin": None,
        }
        assert cleared_settings["resolved_master_pin"] is None
        assert cleared_settings["resolved_composition_pin"] is None
    finally:
        reset_current_owner_id(owner_token)
        await engine.dispose()


def test_target_id_is_stable_per_idempotency_key_and_never_the_source():
    source_id = uuid.uuid4()

    first = workspace_endpoint._template_application_target_id(
        source_presentation_id=source_id,
        idempotency_key="stable-target-key",
    )
    second = workspace_endpoint._template_application_target_id(
        source_presentation_id=source_id,
        idempotency_key="stable-target-key",
    )
    other = workspace_endpoint._template_application_target_id(
        source_presentation_id=source_id,
        idempotency_key="a-different-key",
    )

    # A retry must land on the same target deck, never create a second one.
    assert first == second
    assert first != source_id
    assert other != first


def test_confirm_rejects_stale_source_revision_and_key_conflict(tmp_path: Path):
    asyncio.run(_confirm_rejects_stale_source_revision_and_key_conflict(tmp_path))


async def _confirm_rejects_stale_source_revision_and_key_conflict(tmp_path: Path):
    engine, maker = await _database(tmp_path / "conflicts.db")
    try:
        async with maker() as session:
            source = _source()
            session.add(source)
            await session.commit()
            source_id = source.id

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await confirm_authored_template_application(
                    session,
                    source_presentation_id=source_id,
                    request=_request(revision=6),
                    idempotency_key="stale",
                    generation_request_builder=_builder,
                )
        assert stale.value.code == "revision_conflict"

        async with maker() as session:
            await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=_request(),
                idempotency_key="same-key",
                generation_request_builder=_builder,
            )
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as conflict:
                await confirm_authored_template_application(
                    session,
                    source_presentation_id=source_id,
                    request=_request(style="poster"),
                    idempotency_key="same-key",
                    generation_request_builder=_builder,
                )
        assert conflict.value.code == "idempotency_key_payload_conflict"
    finally:
        await engine.dispose()
