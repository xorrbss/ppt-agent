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


def _run(coro):
    return asyncio.run(coro)


async def _make_engine(db_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


def test_generation_info_returns_prompt_file_names_and_settings(tmp_path):
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

        await engine.dispose()

    _run(runner())
