from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from api.v1.ppt.endpoints.authored_operations import (
    build_authored_retemplate_generation_request,
)
from api.v1.ppt.endpoints.presentation_generation_staging import (
    _durable_request_payload,
)
from models.authored_vnext.deck import (
    DeckMetadataSnapshot,
    GenerateDeckRequest,
    MasterDigestPin,
)
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.presentation_from_template import RetemplatePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_vnext.generation.composition import compose_authored_style
from services.authored_vnext.generation.core_bridge import DeterministicCoreBridge
from services.authored_vnext.generation.effective_style import (
    materialize_authored_composition,
    materialize_body_visual_contract,
    resolve_authored_composition,
)
from services.authored_vnext.generation.legacy_bridge import (
    LegacyMasterGeneration,
    _verified_resolved_snapshot_evidence,
    build_legacy_generation_export_payload,
)
from services.authored_vnext.generation.pipeline import AuthoredVNextGenerationPipeline
from services.authored_vnext.generation.repository import InMemoryGenerationRepository
from services.authored_vnext.integration.export_payload import worker_slides
from services.authored_vnext.master.digests import canonical_digest, seal_master_contract
from services.authored_vnext.master.preview import build_preview
from tests.support.authored_vnext_master_fixture import master_contract_payload
from utils.authored_styles import resolve_authored_style


def _overlaps(left, right) -> bool:
    return not (
        left.right <= right.x
        or right.right <= left.x
        or left.bottom <= right.y
        or right.bottom <= left.y
    )


def test_company_standard_authored_template_vertical_slice(
    fake_async_session,
) -> None:
    source_id = uuid.uuid4()
    reference_id = uuid.uuid4()
    manuscript = [
        "# 스마트뱅킹 전환\n고객 중심 금융 경험을 재설계합니다.",
        "# 핵심 KPI\n활성 고객 125만 명, 전환율 18%, 월 거래 420만 건",
        (
            "# 실행 로드맵\n1분기 진단, 2분기 구축, 3분기 전환, 4분기 안정화를 "
            "단계별 승인 기준과 품질 검증, 운영 이관 점검에 연결하고 각 분기의 "
            "완료 조건과 후속 조치를 명확히 관리합니다."
        ),
        "# 목표 아키텍처\n채널 계층, 서비스 계층, 데이터 플랫폼 계층을 연결합니다.",
        "# 감사합니다\nRFP 문의와 후속 협의를 진행하겠습니다.",
    ]
    source = PresentationModel(
        id=source_id,
        content="OSB 스마트뱅킹 제안",
        n_slides=len(manuscript),
        language="Korean",
        outlines=PresentationOutlineModel(
            slides=[SlideOutlineModel(content=value) for value in manuscript]
        ).model_dump(mode="json"),
        reference_snapshot_ids=[str(reference_id)],
        mode="authored",
        theme={
            "mode": "authored",
            "authored_vnext": {
                "deckMetadata": {
                    "documentId": "RFP-OSB-2026-01",
                    "kind": "rfp",
                }
            },
        },
    )
    source_slides = [
        SlideModel(
            presentation=source_id,
            layout_group="authored",
            layout=f"authored:{index}",
            index=index,
            content={
                "__authored__": True,
                "values": {
                    "title": manuscript[index].splitlines()[0],
                    "table": [["항목", "값"], ["순서", index]],
                    "chart": {"series": [index, index + 1]},
                },
                "imageRefs": [f"asset://source/image-{index}.png"],
            },
            properties={"sourceRef": f"semantic://slide/{index}"},
            html_content=f"<section>{index}</section>",
        )
        for index in range(len(manuscript))
    ]

    class _SlideResult:
        def scalars(self):
            return self

        def all(self):
            return source_slides

    fake_async_session._get_results[source_id] = source
    fake_async_session.execute = AsyncMock(return_value=_SlideResult())
    source_before = source.model_dump(mode="json")
    slides_before = [slide.model_dump(mode="json") for slide in source_slides]

    generation_request = asyncio.run(
        build_authored_retemplate_generation_request(
            source_id,
            RetemplatePresentationRequest(authored_style="osb-smart-banking"),
            fake_async_session,
        )
    )

    assert source.model_dump(mode="json") == source_before
    assert [slide.model_dump(mode="json") for slide in source_slides] == slides_before
    assert generation_request.slides_markdown == manuscript
    assert generation_request.n_slides == len(manuscript)
    assert generation_request.reference_snapshot_ids == [reference_id]
    semantic = generation_request._authored_source_semantic_snapshot
    assert semantic["slideCount"] == len(manuscript)
    assert [item["index"] for item in semantic["slides"]] == list(range(len(manuscript)))
    assert [item["semanticContent"] for item in semantic["slides"]] == manuscript
    assert semantic["slides"][1]["values"]["values"]["chart"]["series"] == [1, 2]
    assert semantic["slides"][2]["values"]["imageRefs"] == [
        "asset://source/image-2.png"
    ]
    assert generation_request._authored_deck_metadata == DeckMetadataSnapshot(
        document_id="RFP-OSB-2026-01",
        kind="rfp",
    )
    durable = _durable_request_payload(generation_request)
    assert durable["_internal_authored_source_semantic_snapshot"] == semantic
    assert durable["_internal_authored_deck_metadata"]["documentId"] == (
        "RFP-OSB-2026-01"
    )

    contract_payload = deepcopy(master_contract_payload())
    contract_payload["semanticElements"] = [
        {
            "id": "element:legacy-title-placeholder",
            "type": "placeholder",
            "scope": {"kind": "layout", "layoutId": "layout:standard-title"},
            "bounds": {"x": 90, "y": 90, "width": 1000, "height": 90},
            "rotation": 0,
            "zIndex": 2,
            "visibility": "layoutOnly",
            "editableByUser": True,
            "editableByAI": False,
            "locked": False,
            "style": {},
            "content": {"placeholderRole": "title", "role": "legacy-title"},
        },
        {
            "id": "element:legacy-body-placeholder",
            "type": "placeholder",
            "scope": {"kind": "layout", "layoutId": "layout:standard-title"},
            "bounds": {"x": 40, "y": 160, "width": 1200, "height": 500},
            "rotation": 0,
            "zIndex": 2,
            "visibility": "layoutOnly",
            "editableByUser": True,
            "editableByAI": True,
            "locked": False,
            "style": {},
            "content": {"placeholderRole": "body", "role": "legacy-body"},
        },
        {
            "id": "element:uncertain-locked-shape",
            "type": "shape",
            "scope": {"kind": "common"},
            "bounds": {"x": 10, "y": 250, "width": 40, "height": 120},
            "rotation": 0,
            "zIndex": 0,
            "visibility": "always",
            "editableByUser": False,
            "editableByAI": False,
            "locked": True,
            "style": {"shape": "rect", "fill": "#CCCCCC"},
            "content": {"role": "unclassified-import"},
        },
    ]
    master_contract = seal_master_contract(
        DeckMasterContract.model_validate(contract_payload)
    )
    master = MasterRevisionResponse(
        master_id=master_contract.master_id,
        revision=2,
        digest=master_contract.digest,
        status="published",
        contract=master_contract,
    )
    style = resolve_authored_style("osb-smart-banking")

    with patch(
        "utils.llm_calls.author_slide.author_slide_html",
        new=AsyncMock(),
    ) as provider_call:
        composition, effective = resolve_authored_composition(
            master_contract,
            style=style,
            metadata=generation_request._authored_deck_metadata,
        )
        assert composition.merge_policy_version == (
            "company-furniture-consistency-policy-authored-template/v1"
        )
        assert composition.consistency_policy is not None
        assert composition.authored_template is not None
        assert composition.consistency_policy.metadata.document_id == "RFP-OSB-2026-01"
        assert composition.authored_template.retained_reference_id == (
            "artifact-template-ai/osb-proposal-1-29"
        )
        assert effective.furniture == master_contract.furniture
        assert not any(item.type == "placeholder" for item in effective.semantic_elements)
        assert "element:uncertain-locked-shape" in (
            composition.consistency_policy.review_required_element_ids
        )
        assert not any(
            item.id == "element:uncertain-locked-shape"
            for item in effective.semantic_elements
        )
        for role_policy in composition.consistency_policy.role_policies:
            assert not any(
                _overlaps(role_policy.content_rect, zone.bounds)
                for zone in composition.consistency_policy.reserved_zones
                if zone.classification != "reviewRequired"
            )
        assert next(
            item
            for item in composition.consistency_policy.role_policies
            if item.role == "closing"
        ).show_document_id is True

        legacy_effective = materialize_body_visual_contract(
            master_contract,
            composition.body_visual,
            style_selected=True,
            style_id=style.id,
            merge_policy_version="master-slots-authored-body/v3",
        )
        legacy_pin = compose_authored_style(
            master_digest=master_contract.digest,
            style_digest=composition.style_digest,
            style_id=style.id,
            body_visual=composition.body_visual,
            effective_contract_digest=legacy_effective.digest,
            merge_policy_version="master-slots-authored-body/v3",
        )
        assert materialize_authored_composition(master_contract, legacy_pin) == (
            legacy_effective
        )

        master_pin = MasterDigestPin(
            master_id=master.master_id,
            master_revision=master.revision,
            master_digest=master.digest,
            layout_registry_digest=canonical_digest(
                master.contract.layouts.model_dump(mode="json", by_alias=True)
            ),
            font_manifest_digest=master.contract.font_manifest.digest,
        )
        request = GenerateDeckRequest(
            operation_id="operation:company-standard-osb",
            deck_id="deck:company-standard-osb-new",
            master=master_pin,
            composition=composition,
            outline={
                "sourceDigest": semantic["digest"],
                "deckTitle": "스마트뱅킹 전환",
                "items": [
                    {
                        "itemId": f"outline:{index}",
                        "index": index,
                        "title": value.splitlines()[0].removeprefix("# "),
                        "summary": "\n".join(value.splitlines()[1:]),
                    }
                    for index, value in enumerate(manuscript)
                ],
            },
            quality_policy_version="authored-vnext-quality/v1",
            publication_fence={"expectedDeckRevision": 0},
        )
        repository = InMemoryGenerationRepository()
        repository.save_master(master)
        bridge = DeterministicCoreBridge()
        result = AuthoredVNextGenerationPipeline(repository, bridge).generate(request)

        persisted = repository.get_deck(request.deck_id)
        assert request.deck_id != f"deck:legacy-{source_id.hex}"
        assert len(repository._operations) == 1
        assert len(bridge.calls) == 1
        assert len(persisted.slides) == len(manuscript)
        assert [item.slide.body for item in persisted.slides] == [
            "\n".join(value.splitlines()[1:]) for value in manuscript
        ]
        assert persisted.source_digest == semantic["digest"]
        assert persisted.composition == composition

        previews = [
            build_preview(
                effective,
                master.revision,
                item.slide,
                page_number=page_number,
            ).html
            for page_number, item in enumerate(persisted.slides, start=1)
        ]
        exported = worker_slides(persisted, effective)
        assert len(previews) == len(exported) == len(manuscript)
        assert all(
            f'id="furniture:page-number"' in preview
            and f'>{page_number}</div>' in preview
            for page_number, preview in enumerate(previews, start=1)
        )
        scene = persisted.scene
        assert scene is not None
        assert scene.scene_digest == result.checkpoint.scene_digest
        assert scene.composition_digest == composition.digest
        assert [slide.slide_id for slide in scene.slides] == [
            item.slide.slide_id for item in persisted.slides
        ]
        assert all(item["values"] == {} for item in exported)
        assert all(
            {element.kind for element in slide.elements} >= {"text", "shape"}
            for slide in scene.slides
        )
        assert all(
            len({element.element_id for element in slide.elements})
            == len(slide.elements)
            for slide in scene.slides
        )
        assert all(
            next(
                element.text
                for element in slide.elements
                if element.kind == "text" and element.semantic_role == "body"
            )
            == persisted.slides[index].slide.body
            for index, slide in enumerate(scene.slides)
        )
        assert 'semantic-role-document-id' in previews[0]
        assert 'semantic-role-document-id' not in previews[1]
        assert 'semantic-role-document-id' in previews[-1]

        generation = LegacyMasterGeneration(
            result=result,
            master=master,
            composition_pin=composition,
            ai_text_zones=SimpleNamespace(model_dump=lambda **_: {"values": []}),
            pipeline=SimpleNamespace(),
            source_semantic_snapshot=semantic,
        )
        export_payload = build_legacy_generation_export_payload(generation)
        assert export_payload["compositionPin"] == composition.model_dump(
            mode="json", by_alias=True
        )
        assert export_payload["sourceSemanticSnapshot"] == semantic
        assert export_payload["sourceSemanticSnapshot"]["slideCount"] == len(exported)
        assert export_payload["sceneDigest"] == scene.scene_digest
        assert export_payload["scene"] == scene.model_dump(
            mode="json", by_alias=True
        )
        visual_evidence = _verified_resolved_snapshot_evidence(
            generation,
            readback_verified=True,
        )
        assert visual_evidence["resolved_template_id"] == (
            "artifact-template-ai/osb-proposal-1-29"
        )
        assert visual_evidence["resolved_template_digest"] == (
            composition.authored_template.digest
        )
        assert visual_evidence["page_numbers"] == list(
            range(1, len(exported) + 1)
        )
        assert visual_evidence["preview_snapshot"] == visual_evidence[
            "native_pptx_snapshot"
        ]
        assert visual_evidence["visual_fidelity"]["status"] == "passed"
        assert visual_evidence["readback_verified"] is True
        composed_generation = LegacyMasterGeneration(
            result=result,
            master=master.model_copy(update={"contract": effective}),
            composition_pin=composition,
            ai_text_zones=generation.ai_text_zones,
            pipeline=generation.pipeline,
            source_semantic_snapshot=semantic,
        )
        assert _verified_resolved_snapshot_evidence(
            composed_generation,
            readback_verified=True,
        ) == visual_evidence
        provider_call.assert_not_awaited()


def test_visual_evidence_accepts_valid_repeated_semantic_roles() -> None:
    digest = "sha256:" + "a" * 64
    resolved = SimpleNamespace(digest=digest, source_digest=digest, layouts=[{}])
    template = SimpleNamespace(
        retained_reference_id="artifact-template-ai/osb-proposal-1-29",
        resolved_reference_layout=resolved,
        resolved_reference_layout_digest=digest,
        digest=digest,
    )
    policy = SimpleNamespace(digest=digest)
    composition = SimpleNamespace(
        master_digest=digest,
        authored_template=template,
        consistency_policy=policy,
        digest=digest,
        model_dump=lambda **_: {"digest": digest},
    )
    deck = SimpleNamespace(slides=[object(), object()], composition=composition)
    generation = SimpleNamespace(
        result=SimpleNamespace(deck=deck),
        master=SimpleNamespace(contract=SimpleNamespace(digest="effective:" + digest)),
    )
    exported = [
        {
            "values": {
                "__resolvedSnapshotEvidence": json.dumps(
                    {
                        "pageNumber": page_number,
                        "semanticRolesUsed": ["process-step"],
                        "layoutRolesUsed": ["osb-process", "process", "content"],
                    }
                )
            }
        }
        for page_number in (1, 2)
    ]

    with patch(
        "services.authored_vnext.generation.legacy_bridge.worker_slides",
        return_value=exported,
    ):
        evidence = _verified_resolved_snapshot_evidence(
            generation,
            readback_verified=True,
        )

    assert evidence["semantic_roles_used"] == ["process-step"]
    assert evidence["page_numbers"] == [1, 2]
