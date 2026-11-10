"""Compatibility and rollout boundaries for private exact reconstruction.

These cases deliberately live apart from the feature tests: they protect saved
authored decks and the public style surface while exact reconstruction remains a
private, preset-backed capability.
"""

import asyncio
import uuid

import pytest
from api.v1.ppt.endpoints import authored as authored_endpoint
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel  # noqa: F401 - SQL metadata
from models.sql.slide import SlideModel
from services import presentation_version_service as version_service
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from utils.authored_styles import AuthoredStyle
from services.generation_core.authoring.author_slide import EXACT_RECONSTRUCTION, Brand
from utils.llm_calls.critique_slide import _image_mime_type


def _run(coro):
    return asyncio.run(coro)


def _outline(slides: int = 2) -> PresentationOutlineModel:
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"slide {index}") for index in range(slides)]
    )


def _brand() -> Brand:
    return Brand(topic="Compatibility", primary="#2563EB", fonts="Inter")


def _style(**overrides) -> AuthoredStyle:
    values = {
        "id": "private-exact",
        "name": "Private exact",
        "description": "not a public API mode",
        "preview_bg": "#FFFFFF",
        "preview_accent": "#000000",
        "brief": "private style brief",
    }
    values.update(overrides)
    return AuthoredStyle(**values)


def test_public_style_listing_keeps_exact_mapping_and_mode_private(
    monkeypatch, tmp_path
):
    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    exact = _style(
        reference_mode=EXACT_RECONSTRUCTION,
        slide_reference_images=(source,),
        reference_images={"cover": (source,)},
    )
    monkeypatch.setattr(authored_endpoint, "load_authored_styles", lambda: [exact])

    response = authored_endpoint.get_authored_styles()

    assert response == [{
        **exact.public_dict(),
        "supportedAspectRatios": ["16:9"],
        "supportedCanvases": [
            {"id": "wide-16x9", "enabled": True, "reason": None},
            {"id": "tall-9x16", "enabled": False, "reason": "canvas_stage_disabled"},
            {"id": "tall-9x13", "enabled": False, "reason": "canvas_stage_disabled"},
            {"id": "square-1x1", "enabled": False, "reason": "canvas_stage_disabled"},
            {"id": "ultrawide-21x9", "enabled": False, "reason": "canvas_stage_disabled"},
        ],
    }]
    assert "reference_mode" not in response[0]
    assert "slide_reference_images" not in response[0]
    assert "reference_images" not in response[0]


@pytest.mark.parametrize(
    ("source_bytes", "expected_mime"),
    [
        (b"\x89PNG\r\n\x1a\nsource", "image/png"),
        (b"\xff\xd8\xff\xe0source", "image/jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBPsource", "image/webp"),
    ],
)
def test_exact_source_mime_uses_bytes_not_windows_or_linux_filename(
    source_bytes, expected_mime
):
    # Exact QA reads source bytes from a local Path.  The model payload must not
    # derive MIME from an OS-specific separator or a misleading file extension.
    assert _image_mime_type(source_bytes) == expected_mime


def test_restoring_authored_review_snapshot_preserves_html_and_image_metadata(tmp_path):
    async def runner():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'compat.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        presentation_id = uuid.uuid4()
        old_slide = SlideModel(
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:CONTENT",
            index=0,
            content={
                "__authored__": True,
                "image": "authored/old.png",
                "role": "CONTENT",
            },
            html_content="<html><body>faithful source</body></html>",
            properties={"image": "authored/old.png"},
        )
        current_slide = SlideModel(
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:CONTENT",
            index=0,
            content={
                "__authored__": True,
                "image": "authored/new.png",
                "role": "CONTENT",
            },
            html_content="<html><body>candidate</body></html>",
            properties={"image": "authored/new.png"},
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            version = await version_service.snapshot_slides(
                session,
                presentation_id,
                [old_slide.model_dump(mode="json")],
                label="quality-review-before",
                force=True,
            )
            session.add(current_slide)
            await session.commit()

            restored = await version_service.restore_version(
                session, presentation_id, version.id
            )
            await session.commit()
            live = list(
                await session.exec(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                    .order_by(SlideModel.index)
                )
            )

            assert restored is not None
            assert live[0].html_content == old_slide.html_content
            assert live[0].content["image"] == "authored/old.png"
            assert live[0].properties["image"] == "authored/old.png"
        await engine.dispose()

    _run(runner())
