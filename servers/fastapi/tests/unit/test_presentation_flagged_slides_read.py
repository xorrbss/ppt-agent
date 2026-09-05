"""``GET /presentation/{id}`` is the editor's only source of D7 slide flags.

The terminal-slide fallback and the author-stage rescue publish a
remade-or-placeholder slide flagged instead of failing the deck, and the
publication step persists that receipt on ``presentation.theme``
(``authoredQualityGate.flaggedSlides``).  The editor reads the flags off the
ordinary presentation read, so this pins two things: the read serves the
receipt rows byte-for-byte, and it stays owner-scoped like every other read.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation_crud
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PresentationModel,
)
from models.sql.slide import SlideModel
from utils.owner_context import reset_current_owner_id, set_current_owner_id

TABLES = [PresentationModel.__table__, SlideModel.__table__]

FLAGGED_SLIDES = [
    {
        "index": 0,
        "reason": "author:attempts_exhausted",
        "remakeAttempted": True,
        "remakeOutcome": "placeholder",
    },
    {
        "index": 2,
        "reason": "composition_safety:overflow,text_collision",
        "remakeAttempted": True,
        "remakeOutcome": "remake_budget_exhausted",
        "remakeCount": 2,
    },
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(maker, owner_id: uuid.UUID) -> uuid.UUID:
    presentation_id = uuid.uuid4()
    async with maker() as session, session.begin():
        session.add(
            PresentationModel(
                id=presentation_id,
                owner_id=owner_id,
                content="flagged deck",
                n_slides=3,
                language="ko",
                title="Flagged deck",
                mode="authored",
                lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                theme={
                    "mode": "authored",
                    "style": "default",
                    "authoredQualityGate": {
                        "status": "passed",
                        "flaggedSlides": FLAGGED_SLIDES,
                    },
                },
            )
        )
        session.add_all(
            SlideModel(
                presentation=presentation_id,
                index=index,
                owner_id=owner_id,
                layout_group="authored",
                layout="authored:body",
                html_content=f"<section>slide {index}</section>",
            )
            for index in range(3)
        )
    return presentation_id


def test_presentation_read_serves_flagged_slides_from_the_receipt(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "flagged-read.db")
        owner_id = uuid.uuid4()
        token = set_current_owner_id(owner_id)
        try:
            presentation_id = await _seed(maker, owner_id)
            async with maker() as session:
                deck = await presentation_crud.get_presentation(presentation_id, session)
        finally:
            reset_current_owner_id(token)
            await engine.dispose()

        payload = jsonable_encoder(deck)
        gate = payload["theme"]["authoredQualityGate"]
        assert gate["flaggedSlides"] == FLAGGED_SLIDES
        # The receipt's indices address the slides the same read serves.
        served_indices = [slide["index"] for slide in payload["slides"]]
        assert served_indices == [0, 1, 2]
        assert all(entry["index"] in served_indices for entry in gate["flaggedSlides"])

    asyncio.run(scenario())


def test_presentation_read_keeps_flagged_receipt_owner_scoped(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "flagged-owner.db")
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        token = set_current_owner_id(owner_id)
        try:
            presentation_id = await _seed(maker, owner_id)
        finally:
            reset_current_owner_id(token)

        token = set_current_owner_id(other_owner_id)
        try:
            async with maker() as session:
                with pytest.raises(HTTPException) as raised:
                    await presentation_crud.get_presentation(presentation_id, session)
        finally:
            reset_current_owner_id(token)
            await engine.dispose()
        assert raised.value.status_code == 404

    asyncio.run(scenario())
