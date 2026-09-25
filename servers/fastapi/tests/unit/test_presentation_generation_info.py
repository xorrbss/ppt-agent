import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints.presentation_crud import (
    get_presentation_generation_info,
)
from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from models.sql.workspace import WorkspaceMessage
from templates.native_ui.constants import LEGACY_PRESENTATION_VERSION


def _run(coro):
    return asyncio.run(coro)


async def _make_engine(db_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def test_generation_info_returns_basename_only_and_saved_settings(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "generation-info.db")
        presentation_id = uuid.uuid4()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(
                PresentationModel(
                    id=presentation_id,
                    content="분기 실적을 요약해줘",
                    n_slides=8,
                    language="ko",
                    file_paths=[
                        r"C:\private\uploads\실적자료.pdf",
                        "/srv/uploads/시장 분석.xlsx",
                    ],
                    tone="professional",
                    verbosity="concise",
                    instructions="임원 보고용으로 작성",
                    include_table_of_contents=True,
                    include_title_slide=True,
                    web_search=False,
                    mode="authored",
                    version=LEGACY_PRESENTATION_VERSION,
                )
            )
            await session.commit()

            info = await get_presentation_generation_info(
                id=presentation_id,
                sql_session=session,
            )

            assert info.prompt == "분기 실적을 요약해줘"
            assert info.attachment_file_names == [
                "실적자료.pdf",
                "시장 분석.xlsx",
            ]
            assert info.settings.n_slides == 8
            assert info.settings.language == "ko"
            assert info.settings.tone == "professional"
            assert info.settings.verbosity == "concise"
            assert info.settings.instructions == "임원 보고용으로 작성"
            assert info.settings.include_table_of_contents is True
            assert info.settings.include_title_slide is True
            assert info.settings.web_search is False
            assert info.settings.mode == "authored"
            assert info.settings.version == LEGACY_PRESENTATION_VERSION

        await engine.dispose()

    _run(runner())


def test_generation_info_returns_empty_attachments_and_legacy_default(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "generation-info-defaults.db")
        presentation_id = uuid.uuid4()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(
                PresentationModel(
                    id=presentation_id,
                    content="Create a short summary",
                    n_slides=3,
                    language="en",
                    # Every deck this fork creates is authored; a row with no
                    # identity described a retired React-template format.
                    mode="authored",
                )
            )
            await session.commit()

            info = await get_presentation_generation_info(
                id=presentation_id,
                sql_session=session,
            )

            assert info.attachment_file_names == []
            assert info.settings.version == LEGACY_PRESENTATION_VERSION
            assert info.settings.include_table_of_contents is False
            assert info.settings.include_title_slide is True
            assert info.settings.web_search is False

        await engine.dispose()

    _run(runner())


def test_generation_info_prefers_the_original_workspace_generation_prompt(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "generation-info-workspace.db")
        presentation_id = uuid.uuid4()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            presentation = PresentationModel(
                id=presentation_id,
                content="Generated outline summary that is not the user's prompt",
                n_slides=90,
                language="ko",
                # Every deck this fork creates is authored; a row with no
                # identity described a retired React-template format.
                mode="authored",
            )
            conversation = WorkspaceConversation(presentation_id=presentation_id)
            session.add_all([presentation, conversation])
            await session.flush()
            session.add(
                WorkspaceOperation(
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=2,
                    kind="presentation.generate",
                    state="succeeded",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    request_payload={
                        "prompt": "첨부한 제안요청서를 바탕으로 구축 제안서를 만들어 줘."
                    },
                )
            )
            await session.commit()

            info = await get_presentation_generation_info(
                id=presentation_id,
                sql_session=session,
            )

            assert info.prompt == "첨부한 제안요청서를 바탕으로 구축 제안서를 만들어 줘."

        await engine.dispose()

    _run(runner())


def test_generation_info_does_not_report_the_attachment_fallback_as_user_prompt(
    tmp_path,
):
    async def runner():
        engine = await _make_engine(tmp_path / "generation-info-fallback.db")
        presentation_id = uuid.uuid4()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            presentation = PresentationModel(
                id=presentation_id,
                content="Generated Korean outline that replaced the original input",
                n_slides=90,
                language="ko",
                # Every deck this fork creates is authored; a row with no
                # identity described a retired React-template format.
                mode="authored",
            )
            conversation = WorkspaceConversation(presentation_id=presentation_id)
            session.add_all([presentation, conversation])
            await session.flush()
            session.add(
                WorkspaceOperation(
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=2,
                    kind="presentation.generate",
                    state="succeeded",
                    idempotency_key_hash="c" * 64,
                    request_hash="d" * 64,
                    request_payload={
                        "prompt": "Generate a presentation from the attached files."
                    },
                )
            )
            await session.commit()

            info = await get_presentation_generation_info(
                id=presentation_id,
                sql_session=session,
            )

            assert info.prompt == ""

        await engine.dispose()

    _run(runner())


def test_generation_info_returns_404_for_unknown_presentation(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "missing-generation-info.db")

        async with AsyncSession(engine, expire_on_commit=False) as session:
            with pytest.raises(HTTPException) as exc:
                await get_presentation_generation_info(
                    id=uuid.uuid4(),
                    sql_session=session,
                )
            assert exc.value.status_code == 404
            assert exc.value.detail == "Presentation not found"

        await engine.dispose()

    _run(runner())


async def _seed_generation_attempt(session, *, state="failed", owner_id=None, deleted=False):
    if owner_id is not None:
        from models.sql.user import User
        session.add(User(id=owner_id, username=f"attempt-owner-{owner_id}", hashed_password="test-only"))
        await session.flush()
    presentation = PresentationModel(
        id=uuid.uuid4(), content="original request", n_slides=3, language="ko",
        mode="authored", owner_id=owner_id,
        lifecycle_status="deleted" if deleted else "staging",
    )
    conversation = WorkspaceConversation(
        presentation_id=presentation.id, owner_id=owner_id,
        owner_scope=str(owner_id) if owner_id else "anonymous",
    )
    message = WorkspaceMessage(
        conversation_id=conversation.id, owner_id=owner_id,
        position=1, role="user", content="original request",
    )
    operation = WorkspaceOperation(
        presentation_id=presentation.id, conversation_id=conversation.id,
        owner_id=owner_id, owner_scope=conversation.owner_scope,
        anchor_position=2, kind="presentation.generate", state=state,
        idempotency_key_hash=uuid.uuid4().hex * 2,
        request_hash=uuid.uuid4().hex * 2,
        request_payload={"prompt": "original request", "user_message_id": str(message.id)},
    )
    session.add_all([presentation, conversation, message, operation])
    await session.commit()
    return presentation, conversation, message, operation


def test_generation_info_correlates_only_the_owned_operation_message(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "message-correlation.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            presentation, conversation, message, operation = await _seed_generation_attempt(session)
            info = await get_presentation_generation_info(presentation.id, session)
            assert (info.conversation_id, info.operation_id, info.first_user_message_id) == (conversation.id, operation.id, message.id)
            assert info.can_start_new_attempt is True
            _, _, other_message, _ = await _seed_generation_attempt(session)
            operation.request_payload = {**operation.request_payload, "user_message_id": str(other_message.id)}
            session.add(operation)
            await session.commit()
            info = await get_presentation_generation_info(presentation.id, session)
            assert info.first_user_message_id is None
            assert info.operation_id is None
            assert info.conversation_id is None
        await engine.dispose()
    _run(runner())


def test_new_attempt_link_is_durable_idempotent_and_owner_filtered(tmp_path, monkeypatch):
    from api.v1.ppt.endpoints import presentation_crud

    monkeypatch.setattr(presentation_crud, "_chat_first_create_enabled", lambda: True)

    async def runner():
        engine = await _make_engine(tmp_path / "attempt-links.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            previous, _, _, _ = await _seed_generation_attempt(session)
            next_id = uuid.uuid4()
            request = dict(content="changed request", presentation_id=next_id, previous_presentation_id=previous.id,
                           n_slides=3, language="ko", idempotency_key=f"create:{next_id}", sql_session=session)
            created = await presentation_crud.create_presentation(**request)
            assert created.id == next_id
            repeated = await presentation_crud.create_presentation(**request)
            assert repeated.id == next_id
            info = await get_presentation_generation_info(next_id, session)
            assert info.previous_presentation_id == previous.id
            assert info.first_user_message_id is not None
            parent_info = await get_presentation_generation_info(previous.id, session)
            assert parent_info.next_presentation_ids == [next_id]
            _, _, _, unrelated_operation = await _seed_generation_attempt(session)
            unrelated_operation.request_payload = {
                "prompt": f"unrelated \u0000 {previous.id}",
                "previous_presentation_id": "invalid \u0000 parent",
            }
            session.add(unrelated_operation)
            await session.commit()
            assert (await get_presentation_generation_info(previous.id, session)).next_presentation_ids == [next_id]
            _, _, _, foreign_operation = await _seed_generation_attempt(session, owner_id=uuid.uuid4())
            foreign_operation.request_payload = {**foreign_operation.request_payload, "previous_presentation_id": str(previous.id)}
            session.add(foreign_operation)
            await session.commit()
            assert (await get_presentation_generation_info(previous.id, session)).next_presentation_ids == [next_id]
            other, _, _, _ = await _seed_generation_attempt(session, state="cancelled")
            with pytest.raises(HTTPException) as conflict:
                await presentation_crud.create_presentation(**{**request, "previous_presentation_id": other.id})
            assert conflict.value.status_code == 409
            assert conflict.value.detail == "idempotency_key_payload_conflict"
            previous.lifecycle_status = "deleted"
            session.add(previous)
            await session.commit()
            assert (await get_presentation_generation_info(next_id, session)).previous_presentation_id is None
        await engine.dispose()
    _run(runner())


@pytest.mark.parametrize("case", ["running", "foreign", "deleted"])
def test_new_attempt_rejects_unavailable_or_active_predecessor(tmp_path, case):
    from api.v1.ppt.endpoints.presentation_generation_context import validate_previous_attempt

    async def runner():
        engine = await _make_engine(tmp_path / f"attempt-{case}.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            previous, _, _, _ = await _seed_generation_attempt(
                session, state="running" if case == "running" else "failed",
                owner_id=uuid.uuid4() if case == "foreign" else None,
                deleted=case == "deleted",
            )
            with pytest.raises(HTTPException) as rejected:
                await validate_previous_attempt(session, previous_presentation_id=previous.id, presentation_id=uuid.uuid4())
            assert rejected.value.status_code == (404 if case == "foreign" else 409)
            await session.rollback()
        await engine.dispose()
    _run(runner())
