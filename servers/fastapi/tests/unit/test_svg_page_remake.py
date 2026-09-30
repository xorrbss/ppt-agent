"""SVG publication validates canonical measurement without weakening the CAS."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy.dialects import sqlite

from models.sql.slide import SlideModel
from services import svg_page_remake as remake
from services.authored_page_remake_service import (
    AuthoredPageRemakeError, PREVIEW_KIND, _canonical_digest, authored_page_source_digest,
)
from services.svg_contract import source_digest
from utils.hashing import sha256_hex


def _harness(monkeypatch, *, rowcount=1, wrong_digest=False, assets=False):
    owner, presentation_id = uuid.uuid4(), uuid.uuid4()
    target = SlideModel(presentation=presentation_id, owner_id=owner, index=0,
        layout_group="svg", layout="svg:content", source_format="svg", svg_content="<svg>old</svg>",
        content={"source": "keep", "svgReceipt": {"quality": {"source_digest": source_digest("<svg>old</svg>")}}},
        properties={"image": "old.png"})
    presentation = SimpleNamespace(id=presentation_id, owner_id=owner, revision=3,
        theme={"svg": {"quality": [{}]}})
    svg, png = "<svg>new</svg>", b"new capture"
    image = f"capture-{sha256_hex(png)}.png"
    task = SimpleNamespace(id=uuid.uuid4(), owner_id=owner, status="completed", data={
        "kind": PREVIEW_KIND, "phase": "preview_ready", "source_format": "svg",
        "presentation_id": str(presentation_id), "slide_id": str(target.id), "slide_index": 0,
        "source_revision": 3, "source_digest": authored_page_source_digest(target),
        "candidate_svg": svg, "candidate_image": image,
        "candidate_digest": _canonical_digest({"svg": svg, "image": image}),
    })
    monkeypatch.setattr("services.svg_document_mutations.read_svg_slide_assets",
                        AsyncMock(return_value=("assets",) if assets else None))
    monkeypatch.setattr("services.svg_document_mutations.rebind_svg_assets",
                        AsyncMock(return_value=({"rebound": True}, "<svg>materialized</svg>")))
    quality = {"source_digest": source_digest("wrong" if wrong_digest else svg), "passed": True}
    capture = AsyncMock(return_value=SimpleNamespace(png=png, quality=quality))
    monkeypatch.setattr(remake, "render_svg_with_metrics", capture)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=rowcount)),
        refresh=AsyncMock(), add_all=lambda _rows: None,
        get_bind=lambda: SimpleNamespace(dialect=sqlite.dialect()))
    return session, presentation, target, task, capture


@pytest.mark.parametrize("assets", [False, True])
def test_publication_keeps_all_cas_predicates_and_canonical_measurement(monkeypatch, assets):
    session, presentation, target, task, capture = _harness(monkeypatch, assets=assets)
    baseline = deepcopy((target.svg_content, target.content, target.properties))
    result = asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    assert result["status"] == "published"
    statement = session.execute.await_args.args[0]
    params = statement.compile(dialect=sqlite.dialect()).params
    assert params["svg_content"] == task.data["candidate_svg"]
    assert params["content"]["svgReceipt"]["quality"]["source_digest"] == source_digest(params["svg_content"])
    assert params["svg_content_1"] == baseline[0]
    assert params["content_1"] == baseline[1]
    assert params["properties_1"] == baseline[2]
    assert params["id_1"] == target.id and params["presentation_1"] == presentation.id
    assert params["owner_id_1"] == presentation.owner_id
    assert statement.get_execution_options()["synchronize_session"] is False
    assert capture.await_args.args[0] == params["svg_content"]
    assert capture.await_args.kwargs["materialized_svg"] == ("<svg>materialized</svg>" if assets else None)
    session.refresh.assert_awaited_once_with(target)


def test_failed_cas_leaves_in_memory_page_and_task_unchanged(monkeypatch):
    session, presentation, target, task, _ = _harness(monkeypatch, rowcount=0)
    baseline = deepcopy((target.svg_content, target.content, target.properties, task.data))
    with pytest.raises(AuthoredPageRemakeError, match="svg_page_remake_source_changed"):
        asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    assert (target.svg_content, target.content, target.properties, task.data) == baseline
    session.refresh.assert_not_awaited()


def test_digest_mismatch_never_reaches_cas(monkeypatch):
    session, presentation, target, task, _ = _harness(monkeypatch, wrong_digest=True)
    with pytest.raises(ValueError, match="svg_page_measurement_digest_mismatch"):
        asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    session.execute.assert_not_awaited()
