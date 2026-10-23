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


def _harness(monkeypatch, *, rowcount=1, wrong_digest=False, assets=False, readability=False):
    owner, presentation_id = uuid.uuid4(), uuid.uuid4()
    target = SlideModel(presentation=presentation_id, owner_id=owner, index=0,
        layout_group="svg", layout="svg:content", source_format="svg", svg_content="<svg>old</svg>",
        content={"source": "keep", "svgReceipt": {"quality": {"source_digest": source_digest("<svg>old</svg>")},
            "png_sha256": "old", "png_path": "old.png", "raw_response_sha256": "old-model",
            "model_attempt_count": 9, "initial_quality": {"passed": False}}},
        properties={"image": "old.png"})
    presentation = SimpleNamespace(id=presentation_id, owner_id=owner, revision=3,
        theme={"svg": {"quality": [{}]}})
    from services.authored_canvas import presentation_canvas_metadata
    from services.document_canvas_generation import svg_readability_policy
    policy = svg_readability_policy(presentation_canvas_metadata(presentation.theme)) if readability else None
    if policy is not None:
        target.content["svgReceipt"]["readability_policy"] = policy
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
    if policy is not None:
        quality["physical_readability"] = {"policy": policy, "policy_digest": policy["digest"],
            "measurement_complete": True, "passed": True, "minimum_font_pt": policy["quality_target_font_pt"]}
    monkeypatch.setattr("services.svg_presentation_checkpoint._write_png", lambda payload: {
        "png_sha256": sha256_hex(payload), "png_path": f"svg/{sha256_hex(payload)}.png"})
    capture = AsyncMock(return_value=SimpleNamespace(png=png, quality=quality))
    monkeypatch.setattr(remake, "render_svg_with_metrics", capture)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=rowcount)),
        refresh=AsyncMock(), add_all=lambda _rows: None,
        get_bind=lambda: SimpleNamespace(dialect=sqlite.dialect()))
    return session, presentation, target, task, capture


@pytest.mark.parametrize("mode", ["remeasure_source", "local_svg"])
def test_remeasure_source_preview_never_authors_or_changes_source(monkeypatch, mode):
    from models.presentation_from_template import AuthoredPageRemakePreviewRequest
    session,presentation,target,task,capture=_harness(monkeypatch)
    presentation.instructions=None
    presentation.theme["svg"]["styleSnapshot"]={}
    candidate = None
    if mode == "local_svg":
        canvas = remake.presentation_canvas_contract(presentation.theme)
        target.svg_content = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas.width_px}" '
            f'height="{canvas.height_px}" viewBox="0 0 {canvas.width_px} {canvas.height_px}">'
            '<text x="90" y="90" font-family="Noto Sans KR" font-size="20" fill="#000000">keep</text></svg>')
        candidate = target.svg_content.replace('x="90"', 'x="100"')
        capture.side_effect = lambda svg, **kwargs: SimpleNamespace(png=capture.return_value.png,
            quality={**capture.return_value.quality, "source_digest": source_digest(svg)})
    capture.return_value.quality["source_digest"]=source_digest(target.svg_content)
    author=AsyncMock()
    monkeypatch.setattr(remake,"author_slide_svg",author)
    monkeypatch.setattr(remake,"_save_slide_pngs",lambda *args:[task.data["candidate_image"]])
    saved=AsyncMock()
    monkeypatch.setattr("services.authored_page_remake_service._set_task",saved)
    request=AuthoredPageRemakePreviewRequest(mode=mode,source_digest=source_digest(target.svg_content),svg_content=candidate)
    baseline=deepcopy((target.svg_content,target.content,target.properties))
    asyncio.run(remake.preview_svg(presentation,[target],0,request,task,session))
    author.assert_not_awaited()
    final=saved.await_args.kwargs["data"]
    assert final["mode"]==mode
    assert (final["candidate_svg"]==target.svg_content) is (mode == "remeasure_source")
    assert "provider_receipt" not in final and "raw_response" not in final
    assert (target.svg_content,target.content,target.properties)==baseline
    with pytest.raises(ValueError,match="source_changed"):
        asyncio.run(remake.preview_svg(presentation,[target],0,
            request.model_copy(update={"source_digest":"sha256:"+"0"*64}),task,session))
    assert capture.await_count==1
    task.owner_id = uuid.uuid4()
    with pytest.raises(ValueError, match="source_changed"):
        asyncio.run(remake.preview_svg(presentation,[target],0,request,task,session))
    task.owner_id = presentation.owner_id
    if mode == "local_svg":
        for bad_svg in (candidate.replace("keep", "lost"), candidate.replace("<text ", '<text onclick="bad" ')):
            with pytest.raises(AuthoredPageRemakeError):
                asyncio.run(remake.preview_svg(presentation,[target],0,request.model_copy(update={"svg_content":bad_svg}),task,session))
        assert capture.await_count == 1
        task.data = final
        asyncio.run(remake.publish_svg_candidate(session,presentation,[target],0,task,snapshot=False))
        params = session.execute.await_args.args[0].compile(dialect=sqlite.dialect()).params
        assert params["svg_content"] == final["candidate_svg"]
        receipt = params["content"]["svgReceipt"]
        assert receipt["quality"]["verified"] is True and receipt["quality"]["classification"] == "passed"
        assert receipt["png_sha256"] == sha256_hex(capture.return_value.png)
        assert receipt["remake"]["mode"] == "local_svg" and receipt["remake"]["provider_calls"] == 0
        assert "provider_receipt" not in receipt["remake"]
        author.assert_not_awaited()


def test_remeasure_source_publish_preserves_native_sidecars_and_old_author_receipt(monkeypatch):
    from services.generation_core.native_objects import SLIDE_CONTENT_KEY
    session,presentation,target,task,capture=_harness(monkeypatch)
    original_native={"schemaVersion":"existing","objects":[{"id":"original-table"}]}
    target.content[SLIDE_CONTENT_KEY]=deepcopy(original_native)
    target.content["svgReceipt"]["nativeObjects"]={"tableCount":1}
    task.data.update(mode="remeasure_source",candidate_svg=target.svg_content,
        remeasure_source_digest=source_digest(target.svg_content),
        source_digest=authored_page_source_digest(target))
    task.data["candidate_digest"]=remake._candidate_digest(target.svg_content,task.data["candidate_image"],
        mode="remeasure_source",source_binding=source_digest(target.svg_content))
    capture.return_value.quality["source_digest"]=source_digest(target.svg_content)
    asyncio.run(remake.publish_svg_candidate(session,presentation,[target],0,task,snapshot=False))
    params=session.execute.await_args.args[0].compile(dialect=sqlite.dialect()).params
    assert params["svg_content"]==target.svg_content
    assert params["content"][SLIDE_CONTENT_KEY]==original_native
    receipt=params["content"]["svgReceipt"]
    assert receipt["nativeObjects"]=={"tableCount":1}
    assert receipt["raw_response_sha256"]=="old-model" and receipt["model_attempt_count"]==9
    assert receipt["remake"]["mode"]=="remeasure_source"
    assert "provider_receipt" not in receipt["remake"]


@pytest.mark.parametrize("assets", [False, True])
def test_publication_keeps_all_cas_predicates_and_canonical_measurement(monkeypatch, assets):
    session, presentation, target, task, capture = _harness(monkeypatch, assets=assets, readability=True)
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
    assert capture.await_args.kwargs["readability_policy"] == baseline[1]["svgReceipt"]["readability_policy"]
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

    # The existing revision route also rejects a V7 class-label omission on
    # both preview and remeasurement, before publishing any page mutation.
    from utils.hashing import canonical_json_digest
    snapshot = {"authoring_profile": {"id": "art-direction-v1", "version": 7}}
    snapshot["digest"] = canonical_json_digest(snapshot)
    session, presentation, target, task, capture = _harness(monkeypatch, readability=True)
    presentation.theme["svg"]["styleSnapshot"] = snapshot
    presentation.instructions = None
    target.content["source"] = "A → B: 연결\n외부 연계·저장소: A, B"
    task.data["source_digest"] = authored_page_source_digest(target)
    baseline = deepcopy((target.svg_content, target.content, target.properties, task.data))
    with pytest.raises(AuthoredPageRemakeError, match="svg_source_classification_missing"):
        asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    assert capture.await_args.kwargs["timeline_ink_probe"] is True
    assert (target.svg_content, target.content, target.properties, task.data) == baseline
    session.execute.assert_not_awaited()

    author = AsyncMock(return_value=SimpleNamespace(svg_content=task.data["candidate_svg"],
        receipt={}, native_declarations=None, raw_response="provider response"))
    monkeypatch.setattr(remake, "author_slide_svg", author)
    monkeypatch.setattr(remake, "finalize_svg", lambda svg, **kwargs: svg)
    monkeypatch.setattr(remake,"_save_slide_pngs",lambda *args:[task.data["candidate_image"]])
    monkeypatch.setattr("services.authored_page_remake_service._set_task", AsyncMock())
    with pytest.raises(AuthoredPageRemakeError, match="svg_source_classification_missing") as failure:
        asyncio.run(remake.preview_svg(presentation, [target], 0,
            SimpleNamespace(instruction=None), task, session))
    assert failure.value.retryable is False
    assert failure.value.preview_evidence["candidate_svg"]==task.data["candidate_svg"]
    assert failure.value.preview_evidence["phase"]!="preview_ready"
    author.assert_awaited_once()
    assert "readability_policy" not in author.await_args.kwargs
    assert capture.await_args.kwargs["readability_policy"] == target.content["svgReceipt"]["readability_policy"]
    assert capture.await_args.args[0] == task.data["candidate_svg"]
    assert capture.await_args.kwargs["timeline_ink_probe"] is True
    assert (target.svg_content, target.content, target.properties, task.data) == baseline
    session.execute.assert_not_awaited()

    # A dispatched provider failure keeps its typed reason and cannot trigger
    # a second purchase or invent returned candidate bytes.
    from utils.provider_overload import ProviderOutcomeError
    author.reset_mock()
    author.side_effect=ProviderOutcomeError("public_provider_call_timeout",recovery_allowed=True)
    with pytest.raises(AuthoredPageRemakeError,match="public_provider_call_timeout") as provider_failure:
        asyncio.run(remake.preview_svg(presentation,[target],0,
            SimpleNamespace(instruction=None),task,session))
    assert provider_failure.value.stage=="generation" and provider_failure.value.retryable is False
    assert provider_failure.value.preview_evidence["provider_result_returned"] is False
    assert "candidate_svg" not in provider_failure.value.preview_evidence
    author.assert_awaited_once()
    author.reset_mock()
    author.side_effect=None
    author.return_value.receipt={"validation_error":"svg_source_classification_missing"}
    with pytest.raises(AuthoredPageRemakeError,match="svg_source_classification_missing") as invalid_response:
        asyncio.run(remake.preview_svg(presentation,[target],0,
            SimpleNamespace(instruction=None),task,session))
    assert invalid_response.value.stage=="author_validation" and invalid_response.value.retryable is False
    assert invalid_response.value.preview_evidence["raw_response"]=="provider response"
    assert invalid_response.value.preview_evidence["candidate_svg"]==task.data["candidate_svg"]
    author.assert_awaited_once()

    # The real frozen v2 binding follows preview through canonicalization,
    # furniture attestation, measured header quality and publication.
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.authored_canvas import presentation_canvas_metadata
    from services.svg_common_header_v2 import prepare_common_header_v2
    from services.svg_contract import finalize_svg as canonical_finalize
    from services.generation_core.drivers.svg import render as render_driver
    session, presentation, target, task, capture = _harness(monkeypatch)
    presentation.instructions = None
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(
        page_id='stable-preview-page', content='keep', title='Frozen title')])
    candidate = dict(candidate_id='frozen-preview-header', revision=1,
        svg='<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><text id="heading" fill="#111111"/></svg>',
        body_box=dict(x=64, y=150, width=1152, height=520),
        fields=[dict(node_id='heading', role='title', value_ref='title',
            box=dict(x=64, y=25, width=1152, height=70), font_family='Noto Sans KR',
            font_weight=700, font_size_px=32, line_height_px=45, baseline_offset_px=38,
            max_lines=2, alignment='left', optional=False)])
    contract, bindings = prepare_common_header_v2(candidate, outline,
        presentation_canvas_metadata(presentation.theme), snapshot={})
    outline.slides[0].common_header = bindings[0]
    presentation.outlines = outline.model_dump(mode='json')
    presentation.theme['svg']['styleSnapshot'] = {'common_header': contract}
    body = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="80" y="200" font-family="Noto Sans KR" font-size="20" fill="#111111">keep</text></svg>')
    target.svg_content = canonical_finalize(body, index=0, n=1,
        style_snapshot=presentation.theme['svg']['styleSnapshot'], header_page=bindings[0])
    frozen_inputs = deepcopy((presentation.theme['svg']['styleSnapshot'], presentation.outlines))
    author = AsyncMock(return_value=SimpleNamespace(svg_content=body, receipt={},
        native_declarations=None, raw_response='body-only provider response'))
    monkeypatch.setattr(remake, 'author_slide_svg', author)
    monkeypatch.setattr(remake, 'finalize_svg', canonical_finalize)
    png = capture.return_value.png
    capture.side_effect = lambda svg, **kwargs: SimpleNamespace(png=png, quality=dict(
        passed=True, verified=False, measurement_complete=True, source_digest=source_digest(svg), failures=[]))
    header_checks, header_failure = [], [False]
    def apply_header(quality, svg, frozen_contract, binding, furniture):
        assert frozen_contract == contract and binding == bindings[0]
        assert furniture['generated_common_header']['page'] == bindings[0]
        assert furniture['svg_digest'] == source_digest(svg)
        header_checks.append(svg)
        quality['common_header'] = dict(passed=not header_failure[0], header_digest=contract['digest'],
            page_digest=binding['digest'], title_digest=binding['title_digest'])
        if header_failure[0]:
            quality['passed'] = False
    monkeypatch.setattr(render_driver, '_apply_common_header_quality', apply_header)
    saved = AsyncMock()
    monkeypatch.setattr('services.authored_page_remake_service._set_task', saved)
    image = task.data['candidate_image']
    monkeypatch.setattr(remake, '_save_slide_pngs', lambda *args: [image])
    asyncio.run(remake.preview_svg(presentation, [target], 0, SimpleNamespace(instruction=None), task, session))
    assert author.await_args.kwargs['header_page'] == bindings[0]
    assert author.await_args.args[0] == presentation.outlines['slides'][0]['content']
    assert author.await_args.args[3:5] == (0, 1)
    assert capture.await_args.kwargs['system_furniture']['generated_common_header']['page'] == bindings[0]
    task.data = deepcopy(saved.await_args.kwargs['data'])
    ready = deepcopy(task.data)
    asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    params = session.execute.await_args.args[0].compile(dialect=sqlite.dialect()).params
    receipt = params['content']['svgReceipt']
    assert receipt['quality']['common_header']['passed'] is True
    assert receipt['quality']['common_header']['page_digest'] == bindings[0]['digest']
    assert receipt['system_furniture']['generated_common_header']['page'] == bindings[0]
    assert len(header_checks) == 2
    assert (presentation.theme['svg']['styleSnapshot'], presentation.outlines) == frozen_inputs
    task.data = ready
    session.execute.reset_mock()
    header_failure[0] = True
    with pytest.raises(AuthoredPageRemakeError, match='svg_common_header_quality_failed'):
        asyncio.run(remake.publish_svg_candidate(session, presentation, [target], 0, task, snapshot=False))
    session.execute.assert_not_awaited()
    author.reset_mock()
    target.content['source'] = 'changed source'
    with pytest.raises(ValueError, match='svg_common_header_source_mismatch'):
        asyncio.run(remake.preview_svg(presentation, [target], 0, SimpleNamespace(instruction=None), task, session))
    target.content['source'] = 'keep'
    presentation.outlines['slides'][0]['common_header']['header_digest'] = 'altered'
    with pytest.raises(ValueError, match='svg_common_header_digest_mismatch'):
        asyncio.run(remake.preview_svg(presentation, [target], 0, SimpleNamespace(instruction=None), task, session))
    author.assert_not_awaited()



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
    # The deck's status fields are rebuilt from every published page's receipt.
    other = SimpleNamespace(id=uuid.uuid4(), svg_content="<svg>other</svg>",
                            content={"svgReceipt": {"quality": other_quality}})
    result = asyncio.run(remake.publish_svg_candidate(session, presentation, [target, other], 0, task,
                                                      snapshot=False))
    content = session.execute.await_args.args[0].compile(dialect=sqlite.dialect()).params["content"]
    assert content["svgReceipt"]["quality"]["verified"] is False
    assert content["svgReceipt"]["quality"]["classification"] == "review_required"
    assert content["svgReceipt"]["quality"]["failures"] == [{"kind": "small_body_text"}]
    assert result["quality_status"] == "review_required"
    assert presentation.theme["svg"]["reviewPages"] == [0]
    assert presentation.theme["svg"]["quality"][1] == other_quality


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
