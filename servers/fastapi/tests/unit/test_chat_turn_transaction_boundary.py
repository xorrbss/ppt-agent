"""Turn-replay harness for the chat operation transaction boundary.

Two properties are pinned here:

* **Result parity** - a fixed tool-call sequence must produce byte-identical
  durable state regardless of whether the turn's mutations are flushed as they
  happen or buffered until publication.
* **Boundary** - no write transaction may span the provider call.  The runner
  heartbeats from an independent session every 30s, and SQLite admits one
  writer per database, so a lock held across a multi-minute model call kills
  the operation.  The probe below is that heartbeat.
"""

from __future__ import annotations

import asyncio
import copy
import os
import uuid
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.chat import ChatTurnResult
from services.chat import memory_layer
from services.chat.conversation_store import ChatConversationStore
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.slide_ui_query import slide_ui_revision
from services.workspace_chat_operation_handler import (
    PresentationChatOperationHandler,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationCancelled,
)
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.models.layouts import SlideLayout


TABLES = [
    User.__table__,
    ImageAsset.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    PresentationVersionModel.__table__,
    TemplateV2.__table__,
    TemplateV2LocalState.__table__,
    ChatHistoryMessageModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceOperation.__table__,
    WorkspaceActionReceipt.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    WorkspaceArtifact.__table__,
]

TEMPLATE_ID = "template-v2-turn-replay"

# Deliberately far below the production 30s.  A held writer lock must fail the
# probe fast instead of stalling the suite for the real busy_timeout.
PROBE_BUSY_TIMEOUT_MS = 750


async def _database(path: Path):
    app_data = path.parent / f"{path.stem}-app-data"
    app_data.mkdir(parents=True, exist_ok=True)
    os.environ["APP_DATA_DIRECTORY"] = str(app_data)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            # Mirror services/database.py so the probe reproduces production
            # writer/writer contention rather than an artificial default.
            cursor.execute(f"PRAGMA busy_timeout={PROBE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.fetchone()
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _layout() -> dict:
    return SlideLayout.model_validate(
        {
            "id": "title-image",
            "description": "Editable title and image",
            "components": [
                {
                    "id": "hero",
                    "description": "Hero content",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "text",
                            "runs": [{"text": "Original title"}],
                            "decorative": False,
                            "name": "title",
                            "min_length": 1,
                            "max_length": 80,
                        },
                        {
                            "type": "image",
                            "data": "https://example.invalid/placeholder.png",
                            "decorative": False,
                            "name": "hero_image",
                            "is_icon": False,
                        },
                    ],
                }
            ],
        }
    ).model_dump(mode="json")


def _content(title: str) -> dict:
    return {
        "hero": {
            "title": title,
            "hero_image": {
                "url": "https://cdn.example.invalid/hero.png",
                "prompt": "A retained source prompt",
                "source": "chat",
            },
        }
    }


def _patch_external_side_effects(monkeypatch) -> None:
    """Silence asset/image/memory side effects; they are not under test."""

    async def no_assets(**_kwargs):
        return []

    async def no_memory_index(**_kwargs):
        return None

    monkeypatch.setattr(memory_layer, "process_slide_and_fetch_assets", no_assets)
    monkeypatch.setattr(
        memory_layer, "process_old_and_new_slides_and_fetch_assets", no_assets
    )
    monkeypatch.setattr(
        memory_layer.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_slide_edit",
        no_memory_index,
    )
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", TEMPLATE_ID)


async def _seed(
    maker,
    *,
    slide_titles: tuple[str, ...],
    presentation_revision: int,
    expected_revision: int,
    stale_version_count: int = 0,
):
    operation_id = uuid.uuid4()
    attempt_token = "attempt-token"
    layout = _layout()
    revisions: dict[str, str] = {}
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="before",
            n_slides=len(slide_titles),
            language="en",
            title="Turn replay fixture",
            layout={"name": "native-v2", "slides": []},
            version=TEMPLATE_V2_VERSION,
            revision=presentation_revision,
        )
        session.add(presentation)
        await session.flush()
        session.add(
            TemplateV2(
                id=TEMPLATE_ID,
                presentation_id=presentation.id,
                name="Turn replay fixture",
                layouts={"layouts": [layout]},
            )
        )
        session.add(
            TemplateV2LocalState(
                template_id=TEMPLATE_ID, presentation_id=presentation.id
            )
        )
        for index, title in enumerate(slide_titles):
            slide_ui = copy.deepcopy(layout)
            slide_ui["components"][0]["elements"][0]["runs"][0]["text"] = title
            revisions[title] = slide_ui_revision(slide_ui)
            session.add(
                SlideModel(
                    presentation=presentation.id,
                    layout_group="native-v2",
                    layout="title-image",
                    index=index,
                    content=_content(title),
                    html_content=None,
                    ui=slide_ui,
                    properties={},
                )
            )
        for _ in range(stale_version_count):
            session.add(
                PresentationVersionModel(
                    presentation_id=presentation.id, slides=[], label="seeded"
                )
            )
        conversation = WorkspaceConversation(
            presentation_id=presentation.id, next_position=2
        )
        session.add(conversation)
        await session.flush()
        session.add(
            WorkspaceOperation(
                id=operation_id,
                conversation_id=conversation.id,
                presentation_id=presentation.id,
                anchor_position=1,
                kind="presentation.edit",
                state="running",
                idempotency_key_hash="1" * 64,
                request_hash="2" * 64,
                request_payload={"command": "restructure the deck"},
                expected_revision=expected_revision,
                attempt_number=1,
                attempt_token=attempt_token,
                fencing_token=1,
            )
        )
    claim = WorkspaceOperationClaim(
        operation_id=operation_id,
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=conversation.id,
        presentation_id=presentation.id,
        kind="presentation.edit",
        request_payload={"command": "restructure the deck"},
        expected_revision=expected_revision,
        attempt_number=1,
        attempt_token=attempt_token,
        fencing_token=1,
    )
    return presentation.id, operation_id, claim, revisions


class _Context:
    def __init__(self, claim: WorkspaceOperationClaim):
        self.claim = claim
        self.stages: list[str] = []

    async def progress(self, *, stage, current=None, total=None, percent=None):
        self.stages.append(stage)
        return True

    async def cancellation_requested(self):
        return False

    async def raise_if_cancelled(self):
        return None


async def _heartbeat_probe(maker, operation_id: uuid.UUID) -> None:
    """Reproduce the runner's independent lease write during the model call."""

    async with maker() as session:
        await session.execute(
            update(WorkspaceOperation)
            .where(WorkspaceOperation.id == operation_id)
            .values(heartbeat_at=None)
        )
        await session.commit()


async def _advance_revision_probe(
    maker, presentation_id: uuid.UUID, revision: int
) -> None:
    """Commit the competing winner while this attempt is still provisional."""

    async with maker() as session:
        await session.execute(
            update(PresentationModel)
            .where(PresentationModel.id == presentation_id)
            .values(revision=revision)
        )
        await session.commit()


def _replay_service_factory(
    *,
    steps: Callable[[PresentationChatMemoryLayer], Any],
    probe: Callable[[], Any] | None = None,
):
    """Drive the real tool surface as a fixed, deterministic turn."""

    class ReplayChatService:
        def __init__(self, *, sql_session, presentation_id, conversation_id):
            self._session = sql_session
            self._presentation_id = presentation_id
            self._conversation_id = conversation_id

        async def generate_reply(self, _command):
            tools = PresentationChatMemoryLayer(
                self._session, self._presentation_id
            )
            tool_calls = await steps(tools)
            # The provider is still "thinking" here.  Any writer lock the tool
            # calls left open would block this probe.
            if probe is not None:
                await probe()
            await ChatConversationStore(self._session).append_turn(
                presentation_id=self._presentation_id,
                conversation_id=self._conversation_id,
                user_message="restructure the deck",
                assistant_message="Deck restructured.",
                tool_calls=tool_calls,
            )
            return ChatTurnResult(
                conversation_id=self._conversation_id,
                response_text="Deck restructured.",
                tool_calls=tool_calls,
            )

    return ReplayChatService


def _replay_steps(replace_target_revision: str):
    """A sequence that exercises every buffer-visibility hazard.

    Insert (pending row invisible to SQL), replace (identity-map mutation),
    delete (pending removal still present in SQL), and index shifts that make
    an ``ORDER BY index`` / ``WHERE index = ?`` read stale.  Each step also
    reads back, so a broken overlay corrupts the result rather than silently
    passing.
    """

    async def steps(tools: PresentationChatMemoryLayer) -> list[str]:
        return await _run_replay_steps(tools, replace_target_revision)

    return steps


async def _run_replay_steps(
    tools: PresentationChatMemoryLayer, replace_target_revision: str
) -> list[str]:
    calls: list[str] = []

    created = await tools.save_slide(
        content=_content("Inserted"),
        layout_id="title-image",
        index=1,
        replace_old_slide_at_index=False,
    )
    assert created["saved"] is True, created
    assert created["action"] == "created"
    assert created["index"] == 1
    calls.append("saveSlide:create")

    # Read-after-insert: the pending row must be visible and ordered.
    after_insert = await tools.get("presentation_outline")
    assert after_insert["slide_count"] == 4, after_insert
    assert [item["index"] for item in after_insert["slides"]] == [0, 1, 2, 3]
    titles = [item["content"]["hero"]["title"] for item in after_insert["slides"]]
    assert titles == ["First", "Inserted", "Second", "Third"], titles

    # The same hazard through the index-addressed read used by the edit tools.
    at_index = await tools.get_slide_at_index(1, include_full_content=True)
    assert at_index is not None
    assert at_index["content"]["hero"]["title"] == "Inserted", at_index

    replaced = await tools.save_slide(
        content=_content("Replaced"),
        layout_id="title-image",
        index=2,
        replace_old_slide_at_index=True,
        expected_revision=replace_target_revision,
    )
    assert replaced["saved"] is True, replaced
    assert replaced["action"] == "replaced"
    calls.append("saveSlide:replace")

    deleted = await tools.delete_slide(index=0)
    assert deleted["deleted"] is True, deleted
    assert deleted["shifted_slide_count"] == 3
    calls.append("deleteSlide")

    # Read-after-delete: the removed row must be gone and the rest reindexed.
    final = await tools.get("presentation_outline")
    assert final["slide_count"] == 3, final
    assert [item["index"] for item in final["slides"]] == [0, 1, 2]
    final_titles = [item["content"]["hero"]["title"] for item in final["slides"]]
    assert final_titles == ["Inserted", "Replaced", "Third"], final_titles

    return calls


async def _durable_state(maker, presentation_id: uuid.UUID) -> dict[str, Any]:
    async with maker() as session:
        presentation = await session.get(PresentationModel, presentation_id)
        slides = list(
            (
                await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                    .order_by(SlideModel.index)
                )
            ).scalars()
        )
        history = list(
            (
                await session.execute(
                    select(WorkspaceMessage).order_by(
                        WorkspaceMessage.position
                    )
                )
            ).scalars()
        )
        version_count = len(
            list(
                (
                    await session.execute(
                        select(PresentationVersionModel).where(
                            PresentationVersionModel.presentation_id
                            == presentation_id
                        )
                    )
                ).scalars()
            )
        )
        return {
            "revision": presentation.revision,
            "n_slides": presentation.n_slides,
            "slides": [
                {
                    "index": slide.index,
                    "layout": slide.layout,
                    "title": slide.content["hero"]["title"],
                }
                for slide in slides
            ],
            "history": [(row.role, row.content) for row in history],
            "version_count": version_count,
        }


EXPECTED_STATE: dict[str, Any] = {
    "revision": 4,
    "n_slides": 3,
    "slides": [
        {"index": 0, "layout": "title-image", "title": "Inserted"},
        {"index": 1, "layout": "title-image", "title": "Replaced"},
        {"index": 2, "layout": "title-image", "title": "Third"},
    ],
    "history": [("assistant", "Deck restructured.")],
    # save_slide(create), save_slide(replace) and delete_slide each force one.
    "version_count": 3,
}


def test_replayed_turn_produces_the_pinned_durable_state(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_replayed_turn_produces_the_pinned_durable_state(tmp_path, monkeypatch))


async def _replayed_turn_produces_the_pinned_durable_state(
    tmp_path: Path, monkeypatch
):
    _patch_external_side_effects(monkeypatch)
    engine, maker = await _database(tmp_path / "replay.db")
    try:
        presentation_id, _operation_id, claim, revisions = await _seed(
            maker,
            slide_titles=("First", "Second", "Third"),
            presentation_revision=3,
            expected_revision=3,
        )
        context = _Context(claim)
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_replay_service_factory(
                steps=_replay_steps(revisions["Second"])
            ),
        )

        result = await handler.execute(context)

        assert result.result_revision == 4
        assert result.payload["tool_calls"] == [
            "saveSlide:create",
            "saveSlide:replace",
            "deleteSlide",
        ]
        assert context.stages == [
            "reading_presentation",
            "applying_command",
            "publishing_revision",
        ]
        assert await _durable_state(maker, presentation_id) == EXPECTED_STATE
    finally:
        await engine.dispose()


def test_immediate_dml_inside_a_turn_is_rejected(tmp_path: Path, monkeypatch):
    asyncio.run(_immediate_dml_inside_a_turn_is_rejected(tmp_path, monkeypatch))


async def _immediate_dml_inside_a_turn_is_rejected(tmp_path: Path, monkeypatch):
    """The boundary is enforced, not merely audited.

    A bulk statement reaches the connection without a flush, so it would
    silently restore the long writer lock.  The buffer rejects it instead.
    """

    _patch_external_side_effects(monkeypatch)
    engine, maker = await _database(tmp_path / "dml-guard.db")
    try:
        presentation_id, _operation_id, claim, _revisions = await _seed(
            maker,
            slide_titles=("First",),
            presentation_revision=3,
            expected_revision=3,
        )

        async def bulk_update(tools: PresentationChatMemoryLayer) -> list[str]:
            await tools._sql_session.execute(
                update(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .values(index=SlideModel.index + 1)
            )
            return ["bulkUpdate"]

        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_replay_service_factory(steps=bulk_update),
        )

        with pytest.raises(Exception) as caught:
            await handler.execute(_Context(claim))
        assert "presentation_edit_failed" in str(caught.value)

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            assert presentation.revision == 3
    finally:
        await engine.dispose()


def test_no_writer_lock_is_held_across_the_provider_call(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_no_writer_lock_is_held_across_the_provider_call(tmp_path, monkeypatch))


async def _no_writer_lock_is_held_across_the_provider_call(
    tmp_path: Path, monkeypatch
):
    _patch_external_side_effects(monkeypatch)
    engine, maker = await _database(tmp_path / "boundary.db")
    try:
        presentation_id, operation_id, claim, revisions = await _seed(
            maker,
            slide_titles=("First", "Second", "Third"),
            presentation_revision=3,
            expected_revision=3,
            # Push past MAX_VERSIONS_PER_PRESENTATION so snapshot_slides also
            # reaches its prune DELETE during the turn.
            stale_version_count=21,
        )
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_replay_service_factory(
                steps=_replay_steps(revisions["Second"]),
                probe=lambda: _heartbeat_probe(maker, operation_id),
            ),
        )

        try:
            result = await handler.execute(_Context(claim))
        except OperationalError as error:  # pragma: no cover - failure path
            pytest.fail(
                "A writer lock was held across the provider call; the runner "
                f"heartbeat could not commit: {error}"
            )

        assert result.result_revision == 4
        # Parity with the unprobed replay: the boundary change must not alter
        # what the turn durably writes.
        state = await _durable_state(maker, presentation_id)
        assert {key: state[key] for key in EXPECTED_STATE if key != "version_count"} == {
            key: value
            for key, value in EXPECTED_STATE.items()
            if key != "version_count"
        }
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", ("provider", "cancel", "crash"))
def test_failed_cancelled_or_crashed_turn_never_publishes_partial_state(
    tmp_path: Path, monkeypatch, failure: str
):
    asyncio.run(
        _failed_cancelled_or_crashed_turn_never_publishes_partial_state(
            tmp_path, monkeypatch, failure
        )
    )


async def _failed_cancelled_or_crashed_turn_never_publishes_partial_state(
    tmp_path: Path, monkeypatch, failure: str
):
    _patch_external_side_effects(monkeypatch)
    engine, maker = await _database(tmp_path / f"{failure}.db")
    try:
        presentation_id, _operation_id, claim, _revisions = await _seed(
            maker,
            slide_titles=("First",),
            presentation_revision=3,
            expected_revision=3,
        )

        async def mutate_then_fail(tools: PresentationChatMemoryLayer) -> list[str]:
            created = await tools.save_slide(
                content=_content("Provisional"),
                layout_id="title-image",
                index=1,
                replace_old_slide_at_index=False,
            )
            assert created["saved"] is True
            if failure == "cancel":
                raise WorkspaceOperationCancelled("cancelled")
            if failure == "crash":
                raise SystemExit("crashed")
            raise RuntimeError("provider failed")

        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_replay_service_factory(steps=mutate_then_fail),
        )
        expected_error = (
            WorkspaceOperationCancelled
            if failure == "cancel"
            else SystemExit
            if failure == "crash"
            else Exception
        )
        with pytest.raises(expected_error):
            await handler.execute(_Context(claim))

        state = await _durable_state(maker, presentation_id)
        assert state == {
            "revision": 3,
            "n_slides": 1,
            "slides": [
                {"index": 0, "layout": "title-image", "title": "First"}
            ],
            "history": [],
            "version_count": 0,
        }
    finally:
        await engine.dispose()


def test_losing_revision_cas_rolls_back_only_the_provisional_turn(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _losing_revision_cas_rolls_back_only_the_provisional_turn(
            tmp_path, monkeypatch
        )
    )


async def _losing_revision_cas_rolls_back_only_the_provisional_turn(
    tmp_path: Path, monkeypatch
):
    _patch_external_side_effects(monkeypatch)
    engine, maker = await _database(tmp_path / "cas-conflict.db")
    try:
        presentation_id, _operation_id, claim, _revisions = await _seed(
            maker,
            slide_titles=("First",),
            presentation_revision=3,
            expected_revision=3,
        )

        async def provisional_edit(tools: PresentationChatMemoryLayer) -> list[str]:
            await tools.save_slide(
                content=_content("Losing edit"),
                layout_id="title-image",
                index=1,
                replace_old_slide_at_index=False,
            )
            return ["saveSlide:create"]

        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_replay_service_factory(
                steps=provisional_edit,
                probe=lambda: _advance_revision_probe(maker, presentation_id, 4),
            ),
        )
        with pytest.raises(
            NonRetryableWorkspaceOperationError,
            match="stale_presentation_revision",
        ):
            await handler.execute(_Context(claim))

        state = await _durable_state(maker, presentation_id)
        assert state["revision"] == 4
        assert state["n_slides"] == 1
        assert state["slides"] == [
            {"index": 0, "layout": "title-image", "title": "First"}
        ]
        assert state["history"] == []
        assert state["version_count"] == 0
    finally:
        await engine.dispose()
