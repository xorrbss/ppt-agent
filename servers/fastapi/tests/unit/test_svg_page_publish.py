"""The generation publisher uses the digest setter before constructing rows."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.generation_core.drivers.svg import publish
from services.svg_contract import source_digest
from services.svg_export_page_evidence import svg_authoring, svg_geometry_font


@pytest.mark.parametrize("measurement", ["measured", "unmeasured", "mismatch"])
def test_publisher_constructor_enforces_measurement(monkeypatch, measurement):
    owner, presentation_id = uuid.uuid4(), uuid.uuid4()
    presentation = PresentationModel(id=presentation_id, owner_id=owner, content="deck", n_slides=1,
                                     mode="svg", revision=0)
    rows = []
    session = SimpleNamespace(rollback=AsyncMock(), scalar=AsyncMock(return_value=presentation),
        flush=AsyncMock(), execute=AsyncMock(), commit=AsyncMock(), add=rows.append)
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
    monkeypatch.setattr("services.account_deletion_service.require_active_account_generation", AsyncMock())
    monkeypatch.setattr("services.presentation_publication_service.publish_presentation_revision", AsyncMock())
    monkeypatch.setattr(publish, "_save_slide_pngs", lambda *_args: ["image.png"])
    request = SimpleNamespace(content="deck", authored_style="default", instructions=None,
        reference_snapshot_ids=[], codex_generation_preset=None, aspect_ratio="16:9")
    outline = SimpleNamespace(slides=[SimpleNamespace(content="source")], model_dump=lambda **_kwargs: {})
    svg = "<svg>canonical</svg>"
    quality = ({"measurement_status": "unmeasured", "passed": False} if measurement == "unmeasured"
               else {"source_digest": source_digest("wrong" if measurement == "mismatch" else svg), "passed": True})
    payload = {"svg_content": svg, "quality": deepcopy(quality)}
    run = publish._publish(request, presentation_id, outline, "en", session, None, {}, ["content"], [(payload, b"png")])
    if measurement == "mismatch":
        with pytest.raises(ValueError, match="svg_page_measurement_digest_mismatch"):
            asyncio.run(run)
        assert not any(isinstance(row, SlideModel) for row in rows)
        session.commit.assert_not_awaited()
        return
    asyncio.run(run)
    slide = next(row for row in rows if isinstance(row, SlideModel))
    assert slide.svg_content == svg
    assert slide.content["svgReceipt"]["quality"] == quality
    assert payload == {"svg_content": svg, "quality": quality}
    if measurement == "unmeasured":
        for checker in (svg_authoring, svg_geometry_font):
            assert checker([slide])["reasonCode"].endswith("evidence_missing")
