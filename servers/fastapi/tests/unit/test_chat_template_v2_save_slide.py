from __future__ import annotations

import copy
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from utils.owner_context import reset_current_owner_id, set_current_owner_id
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from services.chat import memory_layer
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.slide_ui_query import slide_ui_revision
from services.chat.template_v2_hydration import TemplateV2HydrationError
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.models.layouts import SlideLayout


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    TemplateV2.__table__,
                    TemplateV2LocalState.__table__,
                ],
            )
        )
    return engine, maker


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


def _content(title: str, image: str = "https://cdn.example.invalid/hero.png") -> dict:
    return {
        "hero": {
            "title": title,
            "hero_image": {
                "url": image,
                "prompt": "A deliberately retained source prompt",
                "source": "chat",
            },
        }
    }


async def _seed(maker, *, owner_id: uuid.UUID, existing: bool = False):
    presentation_id = uuid.uuid4()
    template_id = "template-v2-chat-save"
    layout = _layout()
    old_ui = None
    slide_id = None

    async with maker() as session:
        presentation = PresentationModel(
            id=presentation_id,
            owner_id=owner_id,
            content="V2 save tool fixture",
            n_slides=1 if existing else 0,
            language="en",
            title="V2 save tool fixture",
            layout={"name": "native-v2", "slides": []},
            version=TEMPLATE_V2_VERSION,
        )
        template = TemplateV2(
            id=template_id,
            owner_id=owner_id,
            presentation_id=presentation_id,
            name="Chat save fixture",
            layouts={"layouts": [layout]},
        )
        session.add(presentation)
        session.add(template)
        session.add(
            TemplateV2LocalState(
                template_id=template_id,
                presentation_id=presentation_id,
            )
        )
        if existing:
            old_ui = copy.deepcopy(layout)
            old_ui["components"][0]["elements"][0]["runs"][0]["text"] = "Old title"
            old_ui["components"][0]["elements"][1]["data"] = (
                "https://cdn.example.invalid/old.png"
            )
            slide = SlideModel(
                owner_id=owner_id,
                presentation=presentation_id,
                layout_group="native-v2",
                layout="title-image",
                index=0,
                content=_content("Old title", "https://cdn.example.invalid/old.png"),
                html_content=None,
                ui=old_ui,
                properties={},
            )
            slide_id = slide.id
            old_ui = copy.deepcopy(slide.ui)
            session.add(slide)
        await session.commit()

    return presentation_id, template_id, slide_id, old_ui


def _patch_external_side_effects(monkeypatch, snapshots: list[dict] | None = None):
    async def no_new_assets(**_kwargs):
        return []

    async def no_replacement_assets(**_kwargs):
        return []

    async def no_memory_index(**_kwargs):
        return None

    monkeypatch.setattr(memory_layer, "process_slide_and_fetch_assets", no_new_assets)
    monkeypatch.setattr(memory_layer, "get_images_directory", lambda: "images")
    monkeypatch.setattr(
        memory_layer,
        "process_old_and_new_slides_and_fetch_assets",
        no_replacement_assets,
    )
    monkeypatch.setattr(
        memory_layer.MEM0_PRESENTATION_MEMORY_SERVICE,
        "store_slide_edit",
        no_memory_index,
    )
    if snapshots is not None:
        async def record_snapshot(_session, _presentation_id, slides, **kwargs):
            snapshots.append({"slides": copy.deepcopy(slides), **kwargs})

        monkeypatch.setattr(memory_layer, "snapshot_slides", record_snapshot)


def _enable_v2_write(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "template-v2-chat-save")


@pytest.mark.anyio
async def test_save_slide_creates_canonical_template_v2_ui_and_updates_count(
    tmp_path: Path, monkeypatch
):
    _enable_v2_write(monkeypatch)
    _patch_external_side_effects(monkeypatch)
    engine, maker = await _database(tmp_path / "template-v2-create.sqlite")
    owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        presentation_id, _, _, _ = await _seed(maker, owner_id=owner_id)
        async with maker() as session:
            result = await PresentationChatMemoryLayer(session, presentation_id).save_slide(
                content=_content("Created from Chat"),
                layout_id="title-image",
                index=0,
                replace_old_slide_at_index=False,
            )
            assert result["saved"] is True
            assert result["action"] == "created"

            slide = await session.scalar(select(SlideModel))
            presentation = await session.get(PresentationModel, presentation_id)
            assert slide is not None
            assert presentation is not None
            assert presentation.n_slides == 1
            assert slide.content == _content("Created from Chat")
            assert slide.html_content is None
            assert slide.ui is not None
            assert slide.ui["components"][0]["elements"][0]["runs"][0]["text"] == (
                "Created from Chat"
            )
            image = slide.ui["components"][0]["elements"][1]
            assert image["data"] == "https://cdn.example.invalid/hero.png"
            assert image["fit"] == "cover"
            assert image["prompt"] == "A deliberately retained source prompt"
            assert result["revision"] == slide_ui_revision(slide.ui)
    finally:
        reset_current_owner_id(token)
        await engine.dispose()


@pytest.mark.anyio
async def test_template_v2_replace_requires_matching_revision_without_mutation(
    tmp_path: Path, monkeypatch
):
    _enable_v2_write(monkeypatch)
    snapshots: list[dict] = []
    _patch_external_side_effects(monkeypatch, snapshots)
    engine, maker = await _database(tmp_path / "template-v2-conflict.sqlite")
    owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        presentation_id, _, slide_id, old_ui = await _seed(
            maker, owner_id=owner_id, existing=True
        )
        async with maker() as session:
            layer = PresentationChatMemoryLayer(session, presentation_id)
            required = await layer.save_slide(
                content=_content("Must not save"),
                layout_id="title-image",
                index=0,
                replace_old_slide_at_index=True,
            )
            conflict = await layer.save_slide(
                content=_content("Must not save"),
                layout_id="title-image",
                index=0,
                replace_old_slide_at_index=True,
                expected_revision="stale-revision",
            )
            slide = await session.get(SlideModel, slide_id)
            assert required == {
                "saved": False,
                "message": "expectedRevision is required for Template V2 replacement.",
                "validation_errors": ["expected_revision_required"],
            }
            assert conflict["saved"] is False
            assert conflict["validation_errors"] == ["revision_conflict"]
            assert conflict["revision"] == slide_ui_revision(old_ui)
            assert slide is not None
            assert slide.content == _content("Old title", "https://cdn.example.invalid/old.png")
            assert slide.ui == old_ui
            assert snapshots == []
    finally:
        reset_current_owner_id(token)
        await engine.dispose()


@pytest.mark.anyio
async def test_template_v2_replace_snapshots_prechange_state_and_keeps_slide_id(
    tmp_path: Path, monkeypatch
):
    _enable_v2_write(monkeypatch)
    snapshots: list[dict] = []
    _patch_external_side_effects(monkeypatch, snapshots)
    engine, maker = await _database(tmp_path / "template-v2-replace.sqlite")
    owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        presentation_id, _, slide_id, old_ui = await _seed(
            maker, owner_id=owner_id, existing=True
        )
        old_content = _content("Old title", "https://cdn.example.invalid/old.png")
        async with maker() as session:
            result = await PresentationChatMemoryLayer(session, presentation_id).save_slide(
                content=_content("Replacement title"),
                layout_id="title-image",
                index=0,
                replace_old_slide_at_index=True,
                expected_revision=slide_ui_revision(old_ui),
            )
            slide = await session.get(SlideModel, slide_id)
            assert result["saved"] is True
            assert result["action"] == "replaced"
            assert result["slide_id"] == str(slide_id)
            assert slide is not None
            assert slide.id == slide_id
            assert slide.content == _content("Replacement title")
            assert slide.ui is not None
            assert slide.html_content is None
            assert len(snapshots) == 1
            assert snapshots[0]["label"] == "chat:saveSlide:replace"
            assert snapshots[0]["slides"][0]["content"] == old_content
            assert snapshots[0]["slides"][0]["ui"] == old_ui
    finally:
        reset_current_owner_id(token)
        await engine.dispose()


@pytest.mark.anyio
async def test_template_v2_malformed_hydration_leaves_existing_slide_unchanged(
    tmp_path: Path, monkeypatch
):
    _enable_v2_write(monkeypatch)
    _patch_external_side_effects(monkeypatch)
    engine, maker = await _database(tmp_path / "template-v2-malformed.sqlite")
    owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        presentation_id, _, slide_id, old_ui = await _seed(
            maker, owner_id=owner_id, existing=True
        )
        async with maker() as session:
            template = await session.scalar(select(TemplateV2))
            assert template is not None
            template.layouts = {"layouts": [{"id": "title-image", "components": "bad"}]}
            session.add(template)
            await session.commit()

            with pytest.raises(TemplateV2HydrationError):
                await PresentationChatMemoryLayer(session, presentation_id).save_slide(
                    content=_content("Must not save"),
                    layout_id="title-image",
                    index=0,
                    replace_old_slide_at_index=True,
                    expected_revision=slide_ui_revision(old_ui),
                )

            slide = await session.get(SlideModel, slide_id)
            assert slide is not None
            assert slide.content == _content("Old title", "https://cdn.example.invalid/old.png")
            assert slide.ui == old_ui
    finally:
        reset_current_owner_id(token)
        await engine.dispose()
