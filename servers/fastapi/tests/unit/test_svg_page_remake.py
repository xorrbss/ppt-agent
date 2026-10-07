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
        content={"source": "keep", "svgReceipt": {"quality": {"source_digest": source_digest("<svg>old</svg>")},
            "png_sha256": "old", "png_path": "old.png", "raw_response_sha256": "old-model",
            "model_attempt_count": 9, "initial_quality": {"passed": False}}},
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
        "raw_response": "new provider raw response", "provider_receipt": {"attempt": 1},
    })
    monkeypatch.setattr("services.svg_document_mutations.read_svg_slide_assets",
                        AsyncMock(return_value=("assets",) if assets else None))
    monkeypatch.setattr("services.svg_document_mutations.rebind_svg_assets",
                        AsyncMock(return_value=({"rebound": True}, "<svg>materialized</svg>")))
    quality = {"source_digest": source_digest("wrong" if wrong_digest else svg), "passed": True,
               "verified": False, "measurement_complete": True, "semantic_review": "required"}
    monkeypatch.setattr("services.svg_presentation_checkpoint._write_png", lambda payload: {
        "png_sha256": sha256_hex(payload), "png_path": f"svg/{sha256_hex(payload)}.png"})
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
    from services.generation_core.quality.advisory_record import advisory_record
    from services.svg_export_visual_review import svg_visual_review
    prior_advisory = advisory_record({"slides": [], "model": "fixture", "promptVersion": "fixture"},
                                    [sha256_hex(target.svg_content.encode())])
    presentation.theme["svg"]["advisory"] = prior_advisory
    result = asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    assert result["status"] == "published"
    statement = session.execute.await_args.args[0]
    params = statement.compile(dialect=sqlite.dialect()).params
    assert params["svg_content"] == task.data["candidate_svg"]
    assert params["content"]["svgReceipt"]["quality"]["source_digest"] == source_digest(params["svg_content"])
    receipt = params["content"]["svgReceipt"]
    assert receipt["png_sha256"] == sha256_hex(capture.return_value.png)
    assert receipt["png_path"].endswith(f"{receipt['png_sha256']}.png")
    assert receipt["raw_response_sha256"] == sha256_hex(task.data["raw_response"].encode())
    assert receipt["remake"]["provider_receipt"] == task.data["provider_receipt"]
    assert "model_attempt_count" not in receipt and "initial_quality" not in receipt
    assert receipt["quality"]["verified"] is True and receipt["quality"]["classification"] == "passed"
    assert receipt["quality"]["semantic_review"] == "required"
    assert result["quality_status"] == "passed"
    assert presentation.theme["svg"]["qualityStatus"] == "passed"
    assert presentation.theme["svg"]["advisory"] is prior_advisory
    visual = svg_visual_review(slides=[SimpleNamespace(svg_content=params["svg_content"], content=params["content"])],
                               advisory=presentation.theme["svg"]["advisory"])
    assert visual["status"] == "failed" and visual["reasonCode"] == "svg_visual_advisory_stale"
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


@pytest.mark.parametrize("stale_binding", [False, True])
def test_remake_projects_current_native_table_before_cas(monkeypatch, stale_binding):
    from tests.unit.test_svg_native_publish import _page, _table_group, ROWS
    from services.generation_core.native_objects import SLIDE_CONTENT_KEY

    session, presentation, target, task, capture = _harness(monkeypatch)
    payload, svg = _page(_table_group())
    native = remake._native_candidate(svg, svg, payload["nativeDeclarations"])
    if stale_binding:
        native["nativeBindingDigest"] = "stale"
    task.data.update(candidate_svg=svg, candidate_native=native,
                     candidate_digest=remake._candidate_digest(svg, task.data["candidate_image"], native))
    capture.return_value.quality["source_digest"] = source_digest(svg)
    if stale_binding:
        with pytest.raises(ValueError, match="svg_page_remake_native_binding_invalid"):
            asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
        session.execute.assert_not_awaited()
        return
    asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    content = session.execute.await_args.args[0].compile(dialect=sqlite.dialect()).params["content"]
    table = content[SLIDE_CONTENT_KEY]["objects"][0]
    assert table["kind"] == "table"
    assert [[cell["text"] for cell in row] for row in table["payload"]["rows"]] == ROWS
    assert content["svgReceipt"]["nativeObjects"] == {"declared": 1, "accepted": 1, "dropped": []}
    assert "nativeDeclarations" not in content["svgReceipt"]


def test_remake_preserves_measured_failure_and_other_page_review(monkeypatch):
    session, presentation, target, task, capture = _harness(monkeypatch)
    capture.return_value.quality.update(passed=False, failures=[{"kind": "small_body_text"}])
    other_quality = {"passed": True, "verified": True, "source_digest": "other-page"}
    presentation.theme["svg"]["quality"].append(other_quality)
    result = asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    content = session.execute.await_args.args[0].compile(dialect=sqlite.dialect()).params["content"]
    assert content["svgReceipt"]["quality"]["verified"] is False
    assert content["svgReceipt"]["quality"]["classification"] == "review_required"
    assert content["svgReceipt"]["quality"]["failures"] == [{"kind": "small_body_text"}]
    assert result["quality_status"] == "review_required"
    assert presentation.theme["svg"]["reviewPages"] == [0]
    assert presentation.theme["svg"]["quality"][1] is other_quality


@pytest.mark.parametrize("change", [None, "svg", "image", "owner", "index", "native", "revision", "claim"])
def test_published_current_candidate_metadata_refresh_requires_exact_proofs(monkeypatch, change):
    session, presentation, target, task, capture = _harness(monkeypatch)
    native = remake._native_candidate(task.data["candidate_svg"], task.data["candidate_svg"], None)
    task.data.update(candidate_native=native, phase="published", publish={"status": "published"})
    task.data["candidate_digest"] = remake._candidate_digest(task.data["candidate_svg"], task.data["candidate_image"], native)
    target.svg_content = task.data["candidate_svg"]
    target.properties["image"] = task.data["candidate_image"]
    old_source, old_properties = target.svg_content, deepcopy(target.properties)
    args = {"operation_id": uuid.uuid4(), "attempt_token": "claim", "expected_revision": presentation.revision}
    if change == "svg": target.svg_content = "<svg>changed</svg>"
    elif change == "image": target.properties["image"] = "changed.png"
    elif change == "owner": task.owner_id = uuid.uuid4()
    elif change == "index": task.data["slide_index"] = 1
    elif change == "native":
        native["svgDigest"] = "sha256:" + "0" * 64
        task.data["candidate_digest"] = remake._candidate_digest(task.data["candidate_svg"], task.data["candidate_image"], native)
    elif change == "revision": args["expected_revision"] -= 1
    elif change == "claim": args["operation_id"] = None
    if change is not None:
        with pytest.raises((AuthoredPageRemakeError, ValueError)):
            asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False, **args))
        session.execute.assert_not_awaited()
        return
    result = asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False, **args))
    statement = session.execute.await_args.args[0]
    params = statement.compile(dialect=sqlite.dialect()).params
    assert params["svg_content"] == old_source and params["properties"] == old_properties
    assert params["content"]["svgReceipt"]["png_sha256"] == sha256_hex(capture.return_value.png)
    assert params["content"]["svgReceipt"]["quality"]["semantic_review"] == "required"
    assert params["content"]["source"] == target.content["source"]
    assert params["revision_1"] == presentation.revision
    assert result["quality_status"] == "passed"
    assert "presentation" in str(statement) and "revision" in str(statement)
