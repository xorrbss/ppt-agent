"""``GET /presentation/{id}`` is the editor's only source of D7 slide flags.

The terminal-slide fallback and the author-stage rescue publish a
remade-or-placeholder slide flagged instead of failing the deck, and the
publication step persists that receipt on ``presentation.theme``
(``authoredQualityGate.flaggedSlides``).  The editor reads the flags off the
ordinary presentation read, so this pins two things: the read serves the
receipt rows byte-for-byte, and it stays owner-scoped like every other read.

The receipt fixture used to be hand-built here and asserted against itself,
which could not have caught either the missing ``slideId`` backfill (defect 1
of the flagged-slide badge review) or a dropped reason from a slide flagged by
both stages (defect 3): a fixture written by the test always agrees with the
test. This drives the real D7 terminal-gate fallback through the same harness
``tests/unit/test_authored_terminal_slide_fallback.py`` uses -- stubbed
provider and Chromium, real generation/publication code -- so the receipt
this module reads back is the one publication actually wrote.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.v1.ppt.endpoints import presentation_crud
from tests.unit.test_authored_terminal_slide_fallback import (
    _TERMINAL_HTML,
    _generate_on_sqlite,
    _install_deck_stubs,
    _install_remake,
    _outline,
    _request,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def _run(coro):
    return asyncio.run(coro)


async def _read(tmp_path: Path, presentation_id: uuid.UUID):
    """Read the deck back over a fresh connection to the same sqlite file.

    ``_generate_on_sqlite`` disposes its own engine once publication commits;
    the file-backed database it wrote to survives that, so a new engine
    against the same path sees exactly what was persisted.
    """

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'terminal-fallback.db'}"
    )
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await presentation_crud.get_presentation(presentation_id, session)
    finally:
        await engine.dispose()


def _seed_flagged_deck(monkeypatch, tmp_path):
    """Publish a one-slide deck whose only slide stays terminal and is flagged."""

    _install_deck_stubs(monkeypatch, tmp_path, [_TERMINAL_HTML])
    _install_remake(monkeypatch, [], candidate=_TERMINAL_HTML)
    return _run(_generate_on_sqlite(tmp_path, _request(), _outline(1)))


def test_presentation_read_serves_the_real_flagged_slide_receipt(
    monkeypatch, tmp_path
):
    owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        result, presentation = _seed_flagged_deck(monkeypatch, tmp_path)
        written_flags = presentation.theme["authoredQualityGate"]["flaggedSlides"]
        deck = _run(_read(tmp_path, presentation.id))
    finally:
        reset_current_owner_id(token)

    assert result.path == "deck.pptx"
    payload = jsonable_encoder(deck)
    gate = payload["theme"]["authoredQualityGate"]

    # Served byte-for-byte off what publication actually persisted -- not a
    # fixture reconstructed by the test.
    assert gate["flaggedSlides"] == written_flags
    assert len(gate["flaggedSlides"]) == 1
    entry = gate["flaggedSlides"][0]

    # The receipt's index still addresses a slide the same read serves.
    served_indices = [slide["index"] for slide in payload["slides"]]
    assert entry["index"] in served_indices

    # And its slideId names the actual persisted slide row it was written
    # for, not a positional guess a client would have to make on its own.
    flagged_served_slide = next(
        slide for slide in payload["slides"] if slide["index"] == entry["index"]
    )
    assert entry["slideId"] == flagged_served_slide["id"]


def test_presentation_read_keeps_flagged_receipt_owner_scoped(monkeypatch, tmp_path):
    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        _, presentation = _seed_flagged_deck(monkeypatch, tmp_path)
    finally:
        reset_current_owner_id(token)

    token = set_current_owner_id(other_owner_id)
    try:
        with pytest.raises(HTTPException) as raised:
            _run(_read(tmp_path, presentation.id))
    finally:
        reset_current_owner_id(token)
    assert raised.value.status_code == 404
