"""Deck-scoped header updates against a real session: what persists, what does not."""

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from models.authored_header_contract import (
    LEGACY_FIXED_BAND_SCHEMA_VERSION,
    HeaderEyebrowSpec,
    HeaderSublineSpec,
    HeaderTickSpec,
    HeaderTitleSpec,
    default_header_contract,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel  # noqa: F401
from models.sql.slide import SlideModel
from services import authored_header_update_service as service
from services.authored_header_update_service import (
    HeaderUpdateError,
    current_header_contract,
    update_deck_header,
)
from utils.authored_dom_quality import ensure_authored_qids


def _fixed_band_payload(contract) -> dict:
    """One deck as it was persisted before the band became derived."""
    payload = contract.model_dump(mode="json")
    for key in ("band_min_px", "band_max_px", "title_gap_px", "content_gap_px"):
        payload.pop(key, None)
    payload["schema_version"] = LEGACY_FIXED_BAND_SCHEMA_VERSION
    payload["band_height_px"] = 176
    payload["content_top_px"] = 196
    return payload


def _run(coro):
    return asyncio.run(coro)


def _authored_html(title: str) -> str:
    # One eyebrow row and one title line derive a 135px band: 32 + (18 + 12 + 45)
    # + 28. The slide declares it as well as writing it, which is what a restyle
    # has to leave intact.
    return ensure_authored_qids(
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        '<div data-header-part="band" data-header-band="135" style="height:135px">'
        '<span data-header-part="tick" style="width:24px;height:3px;'
        'background-color:#2563EB"></span>'
        '<span data-header-part="eyebrow" style="color:#2563EB">섹션</span>'
        f'<h1 data-header-part="title" style="color:#0F172A;font-size:34px">{title}</h1>'
        '<div data-header-part="divider" style="height:1px;'
        'background-color:#E2E8F0"></div>'
        "</div>"
        '<div data-header-part="body" style="margin-top:20px">본문</div>'
        "</body></html>"
    )


async def _seed(engine, *, slide_count: int = 3, contract=None) -> uuid.UUID:
    presentation_id = uuid.uuid4()
    theme = {"mode": "authored", "primary": "#2563EB"}
    if contract is not None:
        theme["headerContract"] = contract.model_dump(mode="json")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            PresentationModel(
                id=presentation_id,
                content="deck",
                n_slides=slide_count,
                language="Korean",
                title="deck",
                theme=theme,
            )
        )
        for index in range(slide_count):
            session.add(
                SlideModel(
                    presentation=presentation_id,
                    layout_group="authored",
                    layout="authored:PILLARS",
                    index=index,
                    content={"role": "PILLARS"},
                    html_content=_authored_html(f"제목 {index}"),
                )
            )
        await session.commit()
    return presentation_id


async def _engine(db_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


def _revision(monkeypatch, revised):
    async def fake_revise(current, instruction):
        fake_revise.seen = instruction
        return revised(current)

    monkeypatch.setattr(service, "revise_header_contract", fake_revise)
    return fake_revise


def test_a_style_change_is_applied_to_every_slide_and_persisted(monkeypatch, tmp_path):
    """A colour is the whole of a style change: it moves no row and no band.

    A type size would be a different request entirely — it changes how tall the
    title block renders, which the band root's own declaration would then
    contradict, so it is reported for re-authoring instead of patched.
    """

    async def runner():
        engine = await _engine(tmp_path / "header.db")
        contract = default_header_contract(primary="#2563EB")
        presentation_id = await _seed(engine, contract=contract)
        _revision(
            monkeypatch,
            lambda current: current.model_copy(
                update={"title": HeaderTitleSpec(color="#111827")}
            ),
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            outcome = await update_deck_header(
                session, presentation_id, "제목 색을 더 진하게"
            )

        assert outcome.applied is True
        assert outcome.updated_slides == (0, 1, 2)
        assert outcome.structural == ()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            slides = (
                await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                    .order_by(SlideModel.index)
                )
            ).scalars().all()
            presentation = await session.get(PresentationModel, presentation_id)
            versions = (
                await session.execute(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id == presentation_id
                    )
                )
            ).scalars().all()

        assert all("color:#111827" in (s.html_content or "").replace(" ", "") for s in slides)
        # The band the slides derived is untouched, so each still agrees with the
        # height written beside it.
        assert all('data-header-band="135"' in (s.html_content or "") for s in slides)
        # Slide body content survives the header rewrite.
        assert all(f"제목 {s.index}" in (s.html_content or "") for s in slides)
        assert presentation.theme["headerContract"]["title"]["color"] == "#111827"
        assert len(versions) == 1
        await engine.dispose()

    _run(runner())


def test_an_unchanged_contract_touches_nothing(monkeypatch, tmp_path):
    async def runner():
        engine = await _engine(tmp_path / "noop.db")
        contract = default_header_contract(primary="#2563EB")
        presentation_id = await _seed(engine, contract=contract)
        _revision(monkeypatch, lambda current: current)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            outcome = await update_deck_header(session, presentation_id, "안녕")
            versions = (
                await session.execute(
                    select(PresentationVersionModel).where(
                        PresentationVersionModel.presentation_id == presentation_id
                    )
                )
            ).scalars().all()

        assert outcome.applied is False
        assert outcome.updated_slides == ()
        assert versions == []
        await engine.dispose()

    _run(runner())


def test_a_structural_change_is_reported_for_reauthoring(monkeypatch, tmp_path):
    async def runner():
        engine = await _engine(tmp_path / "structural.db")
        contract = default_header_contract(primary="#2563EB")
        presentation_id = await _seed(engine, contract=contract)
        _revision(
            monkeypatch,
            lambda current: current.model_copy(
                update={
                    "subline": HeaderSublineSpec(enabled=True),
                    "eyebrow": HeaderEyebrowSpec(
                        color="#B91C1C", tick=HeaderTickSpec(color="#B91C1C")
                    ),
                }
            ),
        )

        async with AsyncSession(engine, expire_on_commit=False) as session:
            outcome = await update_deck_header(
                session, presentation_id, "부제 추가하고 강조색 빨강으로"
            )

        assert outcome.applied is True
        assert "subline.enabled" in outcome.structural
        assert outcome.needs_reauthor is True
        # The patchable half still landed.
        assert outcome.updated_slides == (0, 1, 2)
        await engine.dispose()

    _run(runner())


def test_the_chat_message_reaches_the_revision_verbatim(monkeypatch, tmp_path):
    async def runner():
        engine = await _engine(tmp_path / "requirements.db")
        contract = default_header_contract(primary="#2563EB")
        presentation_id = await _seed(engine, contract=contract)
        recorder = _revision(monkeypatch, lambda current: current)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            await update_deck_header(session, presentation_id, "RFP 번호 넣어줘")

        assert recorder.seen == "RFP 번호 넣어줘"
        await engine.dispose()

    _run(runner())


def test_a_deck_without_authored_slides_is_refused(monkeypatch, tmp_path):
    async def runner():
        engine = await _engine(tmp_path / "empty.db")
        presentation_id = await _seed(engine, slide_count=0)
        _revision(monkeypatch, lambda current: current)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            with pytest.raises(HeaderUpdateError, match="no_authored_slides"):
                await update_deck_header(session, presentation_id, "헤더 바꿔줘")
        await engine.dispose()

    _run(runner())


def test_a_deck_stored_under_the_fixed_band_keeps_its_own_contract():
    """An unreadable payload reads as "no contract", which is the dangerous outcome.

    Every deck published before the band became derived stores the old keys, so
    rejecting them would quietly restyle each one against the plain default
    instead of the header it was actually authored with.
    """
    stored = _fixed_band_payload(default_header_contract(primary="#0B4EA2"))
    presentation = PresentationModel(
        content="deck",
        n_slides=1,
        language="Korean",
        theme={
            "mode": "authored",
            "primary": "#2563EB",
            "headerContract": stored,
        },
    )

    contract = current_header_contract(presentation)

    # The deck's own accent, not the one the fallback would take from the theme.
    assert contract.eyebrow.tick.color == "#0B4EA2"
    # Its slides were authored against one fixed 176px band, so the deck keeps
    # that band: a page remade or repaired later derives the same number as its
    # siblings instead of a shorter one of its own.
    assert (contract.band_min_px, contract.band_max_px) == (176, 176)
    assert (contract.title_gap_px, contract.content_gap_px) == (28, 20)


def test_a_deck_with_no_stored_contract_falls_back_without_requirement_badges():
    presentation = PresentationModel(
        content="deck",
        n_slides=1,
        language="Korean",
        theme={"mode": "authored", "primary": "#0B4EA2"},
    )
    contract = current_header_contract(presentation)

    assert contract.requirements.enabled is False
    assert contract.eyebrow.tick.color == "#0B4EA2"
