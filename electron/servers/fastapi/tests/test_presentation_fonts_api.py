import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.v1.ppt.endpoints.presentation import get_presentation
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)


class FakeAsyncSession:
    def __init__(self, presentation=None, slides=None):
        self._presentation = presentation
        self._slides = slides or []

    async def get(self, _model, _id):
        return self._presentation

    async def scalars(self, _query):
        return FakeScalarResult(self._slides)


def test_get_presentation_includes_fonts_for_custom_template():
    presentation_id = uuid.uuid4()
    presentation = PresentationModel(
        id=presentation_id,
        content="hello",
        n_slides=1,
        language="English",
        layout={"name": f"custom-{uuid.uuid4()}"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    slides = [
        SlideModel(
            id=uuid.uuid4(),
            presentation=presentation_id,
            layout_group=f"custom-{uuid.uuid4()}",
            layout="custom-layout-1",
            index=0,
            content={"title": "Intro"},
            properties=None,
        )
    ]
    sql_session = FakeAsyncSession(presentation=presentation, slides=slides)

    expected_fonts = {
        "Inter": "https://fonts.googleapis.com/css2?family=Inter&display=swap"
    }

    with patch(
        "api.v1.ppt.endpoints.presentation._resolve_presentation_fonts",
        new=AsyncMock(return_value=expected_fonts),
    ) as mock_resolve_fonts:
        response = asyncio.run(get_presentation(presentation_id, sql_session=sql_session))

    assert response.fonts == expected_fonts
    assert response.slides == slides
    mock_resolve_fonts.assert_awaited_once_with(presentation, slides, sql_session)


def test_get_presentation_not_found():
    presentation_id = uuid.uuid4()
    sql_session = FakeAsyncSession(presentation=None, slides=[])

    try:
        asyncio.run(get_presentation(presentation_id, sql_session=sql_session))
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Presentation not found"
