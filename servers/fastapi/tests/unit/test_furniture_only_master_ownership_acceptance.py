from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

from models.authored_vnext.deck import GenerateDeckRequest, MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.authored_vnext.master.slide import ExplicitTitle, SlideDraft
from services.authored_vnext.body_grammar import (
    BODY_GRAMMAR_VALUE_KEY,
    build_body_grammar_snapshot,
)
from services.authored_vnext.generation.core_bridge import DeterministicCoreBridge
from services.authored_vnext.generation.effective_style import (
    resolve_authored_composition,
)
from services.authored_vnext.generation.legacy_bridge import (
    LegacyMasterGeneration,
    _verified_resolved_snapshot_evidence,
)
from services.authored_vnext.generation.pipeline import AuthoredVNextGenerationPipeline
from services.authored_vnext.generation.repository import InMemoryGenerationRepository
from services.authored_vnext.integration.export_payload import (
    RESOLVED_SNAPSHOT_EVIDENCE_VALUE_KEY,
    worker_master_contract,
    worker_slides,
)
from services.authored_vnext.master.digests import canonical_digest, seal_master_contract
from services.authored_vnext.master.preview import build_preview
from services.authored_vnext.master.service import _seal_slide
from tests.support.authored_vnext_master_fixture import master_contract_payload
from utils.authored_styles import resolve_authored_style


def _master_payload(*, legacy_variant: bool = False) -> dict[str, object]:
    payload = deepcopy(master_contract_payload())
    payload["aiEditZones"] = [
        {
            "id": "ai-zone:authored-envelope",
            "scope": {"kind": "common"},
            "bounds": {"x": 76.0, "y": 90.0, "width": 1128.0, "height": 560.0},
        }
    ]
    if not legacy_variant:
        return payload

    for layout_index, layout in enumerate(payload["layouts"].values()):
        for slot_index, slot in enumerate(layout["slots"]):
            slot["bounds"] = {
                "x": float(4 + layout_index * 11 + slot_index),
                "y": float(5 + layout_index * 13 + slot_index),
                "width": float(2 + slot_index),
                "height": float(3 + layout_index),
            }
            slot["ownership"] = {"locked": True, "modelWritable": False}

    payload["tokens"]["fonts"] = {
        "latin": "Legacy Latin Face",
        "eastAsian": "Legacy East Asian Face",
        "complexScript": "Legacy Complex Face",
        "fallbacks": ["Legacy Token Fallback"],
    }
    payload["tokens"]["colors"] = {
        "background": "#110011",
        "surface": "#220022",
        "textPrimary": "#33FF33",
        "textSecondary": "#44EE44",
        "accent": "#FF00FF",
        "divider": "#00FFFF",
        "warning": "#FFFF00",
    }
    payload["fontManifest"].update(
        {
            "primary": "Legacy Manifest Primary",
            "fallbacks": ["Legacy Manifest Fallback"],
            "windowsFallback": "Legacy Windows Fallback",
            "themeScripts": {
                "latin": "Legacy Latin Theme",
                "hang": "Legacy Hang Theme",
                "jpan": "Legacy Jpan Theme",
                "hans": "Legacy Hans Theme",
                "hant": "Legacy Hant Theme",
            },
            "files": [],
        }
    )
    payload["semanticStyles"]["title"].update(
        {
            "fontPt": 96.0,
            "minFontPt": 8.0,
            "lineHeight": 2.0,
            "fontWeight": 100,
            "maxLines": 12,
            "margins": {
                "leftPt": 72.0,
                "rightPt": 0.0,
                "topPt": 72.0,
                "bottomPt": 0.0,
            },
        }
    )
    payload["semanticStyles"]["body"].update(
        {
            "fontPt": 8.0,
            "minFontPt": 8.0,
            "lineHeight": 2.0,
            "fontWeight": 900,
            "maxLines": 1,
            "margins": {
                "leftPt": 0.0,
                "rightPt": 72.0,
                "topPt": 0.0,
                "bottomPt": 72.0,
            },
        }
    )
    payload["fitPolicy"].update(
        {
            "preferredFontPt": 16.0,
            "minimumFontPt": 72.0,
            "standardMaxLines": 1,
            "longTitleMaxLines": 4,
            "promotionLayoutId": payload["layouts"]["cover"]["id"],
        }
    )
    payload["titleStressCorpus"] = [
        {
            **payload["titleStressCorpus"][0],
            "expectedLines": ["legacy compatibility only"],
        }
    ]
    return payload


def _sealed_master(*, legacy_variant: bool = False) -> DeckMasterContract:
    return seal_master_contract(
        DeckMasterContract.model_validate(
            _master_payload(legacy_variant=legacy_variant)
        )
    )


def _furniture_variant() -> DeckMasterContract:
    payload = _master_payload()
    payload["furniture"]["logo"]["bounds"] = {
        "x": 1120.0,
        "y": 24.0,
        "width": 48.0,
        "height": 48.0,
    }
    payload["furniture"]["pageNumber"].update(
        {"fill": "#00AA44", "text": "99"}
    )
    payload["furniture"]["divider"]["stroke"] = "#FF3355"
    return seal_master_contract(DeckMasterContract.model_validate(payload))


def _grammar(contract: DeckMasterContract) -> tuple[object, dict[str, object]]:
    draft = SlideDraft(
        slide_id="slide:ownership-comparison",
        layout_id=contract.layouts.standard.id,
        title=ExplicitTitle(
            text="템플릿 소유권 검증",
            lines=["템플릿 소유권 검증"],
        ),
        body=(
            "고객 여정의 핵심 문제를 확인합니다.\n"
            "근거 데이터를 연결합니다.\n"
            "실행 단계와 책임을 명확히 합니다."
        ),
    )
    slide = _seal_slide(contract, draft, revision=1)
    grammar = build_body_grammar_snapshot(contract, slide)
    assert grammar is not None
    return slide, grammar


def test_furniture_only_ownership_is_legacy_invariant_through_generation(tmp_path) -> None:
    base_master = _sealed_master()
    legacy_master = _sealed_master(legacy_variant=True)
    furniture_master = _furniture_variant()
    style = resolve_authored_style("osb-smart-banking")

    base_pin, base_effective = resolve_authored_composition(base_master, style=style)
    legacy_pin, legacy_effective = resolve_authored_composition(
        legacy_master,
        style=style,
    )
    furniture_pin, furniture_effective = resolve_authored_composition(
        furniture_master,
        style=style,
    )

    assert base_pin.authored_template == legacy_pin.authored_template
    assert base_pin.authored_template is not None
    assert base_pin.authored_template == furniture_pin.authored_template
    resolved = base_pin.authored_template.resolved_reference_layout
    assert resolved is not None
    assert base_pin.authored_template.resolved_reference_layout_digest == resolved.digest
    assert legacy_pin.authored_template.resolved_reference_layout_digest == resolved.digest
    assert furniture_pin.authored_template.resolved_reference_layout_digest == resolved.digest

    assert base_effective.layouts == legacy_effective.layouts
    assert base_effective.semantic_styles.title == legacy_effective.semantic_styles.title
    assert base_effective.semantic_styles.body == legacy_effective.semantic_styles.body
    assert base_effective.font_manifest == legacy_effective.font_manifest
    assert base_effective.tokens == legacy_effective.tokens
    assert base_effective.fit_policy == legacy_effective.fit_policy
    assert base_effective.title_stress_corpus == legacy_effective.title_stress_corpus == []

    base_slide, base_grammar = _grammar(base_effective)
    _, legacy_grammar = _grammar(legacy_effective)
    _, furniture_grammar = _grammar(furniture_effective)
    assert base_grammar == legacy_grammar == furniture_grammar
    assert base_grammar["referenceLayoutDigest"] == resolved.digest
    selected_reference_layout = next(
        item
        for item in resolved.layouts
        if item["id"] == base_grammar["referenceLayoutId"]
    )
    assert base_grammar["title"]["bounds"] == selected_reference_layout["title"]["bounds"]
    assert base_grammar["contentRect"] == selected_reference_layout["contentRect"]
    assert all(block["bounds"] for block in base_grammar["blocks"])
    assert all(block["textStyle"]["fontFamily"] for block in base_grammar["blocks"])

    base_worker_master = worker_master_contract(
        base_effective,
        {},
        base_pin.authored_template,
    )
    legacy_worker_master = worker_master_contract(
        legacy_effective,
        {},
        legacy_pin.authored_template,
    )
    furniture_worker_master = worker_master_contract(
        furniture_effective,
        {},
        furniture_pin.authored_template,
    )
    for key in ("theme", "tokens", "fontManifest", "layouts"):
        assert base_worker_master[key] == legacy_worker_master[key]
        assert base_worker_master[key] == furniture_worker_master[key]
    assert base_worker_master["furniture"] == legacy_worker_master["furniture"]
    assert base_worker_master["furniture"] != furniture_worker_master["furniture"]

    master_revision = MasterRevisionResponse(
        master_id=base_master.master_id,
        revision=7,
        digest=base_master.digest,
        status="published",
        contract=base_master,
    )
    master_pin = MasterDigestPin(
        master_id=base_master.master_id,
        master_revision=master_revision.revision,
        master_digest=base_master.digest,
        layout_registry_digest=canonical_digest(
            base_master.layouts.model_dump(mode="json", by_alias=True)
        ),
        font_manifest_digest=base_master.font_manifest.digest,
    )
    manuscript = [
        ("스마트뱅킹 전환", "고객 중심 금융 경험을 재설계합니다.", None),
        ("핵심 KPI", "활성 고객과 전환율 지표를 연결합니다.", None),
        ("실행 로드맵", "진단, 구축, 전환, 안정화를 단계별로 관리합니다.", None),
        ("목표 아키텍처", "채널, 서비스, 데이터 플랫폼 계층을 연결합니다.", None),
        ("감사합니다", "후속 협의와 문의를 진행하겠습니다.", "section"),
    ]
    source_digest = canonical_digest(
        [{"title": title, "summary": summary} for title, summary, _ in manuscript]
    )
    request = GenerateDeckRequest(
        operation_id="operation:furniture-only-ownership-acceptance",
        deck_id="deck:furniture-only-ownership-acceptance",
        master=master_pin,
        composition=base_pin,
        outline={
            "sourceDigest": source_digest,
            "deckTitle": manuscript[0][0],
            "items": [
                {
                    "itemId": f"outline:{index}",
                    "index": index,
                    "title": title,
                    "summary": summary,
                    **({"roleHint": role} if role is not None else {}),
                }
                for index, (title, summary, role) in enumerate(manuscript)
            ],
        },
        quality_policy_version="authored-vnext-quality/v1",
        publication_fence={"expectedDeckRevision": 0},
    )
    repository = InMemoryGenerationRepository()
    repository.save_master(master_revision)
    bridge = DeterministicCoreBridge()
    result = AuthoredVNextGenerationPipeline(repository, bridge).generate(request)
    persisted = repository.get_deck(request.deck_id)

    assert len(bridge.calls) == 1
    assert result.export_artifact_digest.startswith("sha256:")
    assert persisted.composition is not None
    persisted_template = persisted.composition.authored_template
    assert persisted_template is not None
    assert persisted_template.retained_reference_id == (
        "artifact-template-ai/osb-proposal-1-29"
    )
    assert persisted_template.resolved_reference_layout_digest == resolved.digest
    assert persisted_template.resolved_reference_layout is not None
    assert persisted_template.resolved_reference_layout.source_digest == resolved.source_digest

    exported = worker_slides(persisted, base_effective)
    previews = [
        build_preview(
            base_effective,
            master_revision.revision,
            item.slide,
            page_number=page_number,
        ).html
        for page_number, item in enumerate(persisted.slides, start=1)
    ]
    assert len(previews) == len(exported) == len(manuscript)
    for preview, exported_slide in zip(previews, exported, strict=True):
        grammar = json.loads(exported_slide["values"][BODY_GRAMMAR_VALUE_KEY])
        snapshot = json.loads(
            exported_slide["values"][RESOLVED_SNAPSHOT_EVIDENCE_VALUE_KEY]
        )
        assert grammar["digest"] in preview
        assert grammar["referenceLayoutDigest"] == resolved.digest
        assert grammar["referenceSourceDigest"] == resolved.source_digest
        assert grammar["title"]["textStyle"]["fontFamily"] in preview
        assert all(
            block["textStyle"]["fontFamily"] in preview
            for block in grammar["blocks"]
        )
        assert snapshot["resolvedTemplateId"] == persisted_template.retained_reference_id
        assert snapshot["resolvedReferenceLayoutDigest"] == resolved.digest
        assert snapshot["bodyGrammarDigest"] == grammar["digest"]

    generation = LegacyMasterGeneration(
        result=result,
        master=master_revision,
        composition_pin=base_pin,
        ai_text_zones=SimpleNamespace(model_dump=lambda **_: {"values": []}),
        pipeline=SimpleNamespace(),
    )
    evidence = _verified_resolved_snapshot_evidence(
        generation,
        readback_verified=True,
    )
    assert evidence["template_snapshot"] == {
        "resolved_template_id": persisted_template.retained_reference_id,
        "resolved_template_digest": persisted_template.digest,
        "reference_layout_digest": resolved.digest,
        "retained_reference_digest": resolved.source_digest,
    }
    assert evidence["preview_snapshot"] == evidence["native_pptx_snapshot"]
    assert evidence["preview_snapshot"]["reference_layout_digest"] == resolved.digest

    assert evidence["readback_verified"] is True
    assert evidence["visual_fidelity"]["basis"] == (
        "resolved-geometry-and-native-readback"
    )
    assert evidence["resolved_composition_pin"]["digest"] == base_pin.digest
    assert base_slide.spec_digest.startswith("sha256:")
