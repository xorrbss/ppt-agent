"""author_slide_svg with AssetOffer slots: prompt kinds and in-place icon flattening."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from PIL import Image

from services.generation_core.drivers.svg.embed_assets import (
    apply_vector_icon_quality, flatten_icon_svg,
)
from services.svg_contract import validate_svg
from services.generation_core.authoring import author_slide_svg as module

ICON = (Path(module.__file__).resolve().parents[5] / "assets" / "infographics" / "monochrome-v1"
        / "originals" / "ai-01-neural-network.svg")


def _data_url(color=(10, 20, 30, 255)) -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (40, 40), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _stub_provider(monkeypatch, response_svg: str, seen: dict):
    monkeypatch.setattr(module, "get_client", lambda **_: object())
    monkeypatch.setattr(module, "get_llm_config", lambda: None)
    monkeypatch.setattr(module, "get_model", lambda: "m")
    monkeypatch.setattr(module, "authoring_model", lambda model: model)
    monkeypatch.setattr(module, "get_llm_provider", lambda: SimpleNamespace(value="p"))
    monkeypatch.setattr(module, "_authoring_output_profile", lambda **_: (1000, None))
    monkeypatch.setattr(module, "_authoring_extra_body", lambda *_a, **_k: None)

    def kwargs(**values):
        seen["messages"] = values["messages"]
        return {}

    monkeypatch.setattr(module, "get_generate_kwargs", kwargs)

    async def no_wait():
        return None

    async def record(**_kwargs):
        return None

    monkeypatch.setattr(module, "_wait_for_author_rate_slot", no_wait)
    monkeypatch.setattr(module, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(module, "supports_provider_idempotency", lambda *_a, **_k: False)
    monkeypatch.setattr(module, "provider_call_admission", lambda **_k: contextlib.nullcontext())
    monkeypatch.setattr(module, "provider_generate_call",
                        lambda *_a, **_k: (lambda: SimpleNamespace(content=response_svg)))
    monkeypatch.setattr(module, "record_provider_call", record)
    monkeypatch.setattr(module, "extract_text", lambda content: content)
    monkeypatch.setattr("services.document_canvas_generation.generation_font_floor", lambda *_a: 18)


def _style():
    return {"contract_version": module.SVG_CONTRACT_VERSION, "digest": "d", "references": {}}


def _svg(images: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<text x="40" y="80" font-family="Inter" font-size="32" fill="#111111">제목</text>'
            f"{images}</svg>")


def _run(monkeypatch, response, manifest):
    seen: dict = {}
    _stub_provider(monkeypatch, response, seen)
    result = asyncio.run(module.author_slide_svg("# 제목", _style(), "content", 1, 3, source_images=manifest,
                                                 canonical_source_images=True))
    return result, seen


def test_vector_icon_slot_is_flattened_in_place_and_reported(monkeypatch):
    icon = flatten_icon_svg(ICON.read_text(encoding="utf-8"), color="#1E5EFF")
    manifest = {
        "source-slot-illus": {"data_url": _data_url(), "description": "현장", "required": False,
                              "allowOmit": True, "kind": "illustration"},
        "source-slot-icon1": {"data_url": _data_url((0, 0, 0, 0)), "description": "icon: 분석",
                              "required": False, "allowOmit": True, "kind": "icon", "vector": icon},
    }
    response = _svg('<image href="source-slot-illus" x="700" y="120" width="400" height="400" '
                    'preserveAspectRatio="xMidYMid meet"/>'
                    '<image href="source-slot-icon1" x="40" y="200" width="48" height="48" '
                    'preserveAspectRatio="xMidYMid meet"/>')
    result, seen = _run(monkeypatch, response, manifest)
    assert result.receipt["validation_error"] is None
    assert result.receipt["vector_icons_used"] == ["source-slot-icon1"]
    assert 'href="source-slot-icon1"' not in result.svg_content and "<polyline" in result.svg_content
    assert 'href="source-slot-illus"' in result.svg_content  # canonical raster slot stays a handle
    validate_svg(result.svg_content, source_manifest={"source-slot-illus": {"required": False}})
    prompt = json.dumps([str(message) for message in seen["messages"]], ensure_ascii=False)
    assert '\\"kind\\": \\"icon\\"' in prompt or '"kind": "icon"' in prompt


def test_source_only_decks_keep_their_receipt_shape(monkeypatch):
    manifest = {"source-slot-a": {"data_url": _data_url(), "description": "사진", "required": False,
                                  "allowOmit": True}}
    response = _svg('<image href="source-slot-a" x="40" y="200" width="200" height="200" '
                    'preserveAspectRatio="xMidYMid meet"/>')
    result, seen = _run(monkeypatch, response, manifest)
    assert "vector_icons_used" not in result.receipt
    prompt = json.dumps([str(message) for message in seen["messages"]], ensure_ascii=False)
    assert "kind" not in prompt.split("source_images", 1)[1][:200]
    # Failed/unknown dispatches must not fabricate a successful physical outcome.
    from utils.provider_overload import ProviderOutcomeError
    _stub_provider(monkeypatch,response,{})
    records=[]
    async def record(**kwargs):
        records.append(kwargs)
    def fail():
        raise ProviderOutcomeError('public_provider_call_timeout',recovery_allowed=True)
    monkeypatch.setattr(module,'record_provider_call',record)
    monkeypatch.setattr(module,'provider_generate_call',lambda *_a,**_k:fail)
    with pytest.raises(ProviderOutcomeError):
        asyncio.run(module.author_slide_svg('# title',_style(),'content',1,3,source_images=manifest,
                                             canonical_source_images=True))
    assert len(records)==1 and records[0]['outcome']=='failed' and records[0]['response'] is None


def test_omitted_vector_icon_flags_the_page():
    quality = {"passed": True}
    apply_vector_icon_quality(quality, {"assetOffer": {"vectorIcons": {"offered": ["a", "b"], "used": ["a"]}}})
    assert quality["passed"] is False
    assert quality["assetOffer"]["omitted"] == ["b"]
    assert quality["failures"] == [{"kind": "asset_icon_omitted", "slotIds": ["b"]}]


def test_author_prompt_uses_content_grounded_neighbor_intent_without_fixed_layout(monkeypatch):
    from services.generation_core.drivers.svg.visual_intent import build_deck_visual_intents

    contents = ["# 매출 추세\n2024년 12억원, 2025년 18억원", "# 도입 전후 비교\n전: 5일\n후: 2일"]
    intents = build_deck_visual_intents(contents, ["content", "content"])
    seen = {}
    _stub_provider(monkeypatch, _svg(""), seen)
    result = asyncio.run(module.author_slide_svg(contents[1], _style(), "content", 1, 2,
                                                visual_intent=intents[1]))
    prompt = "\n".join(str(message) for message in seen["messages"])
    assert "visual_intent" in prompt and "매출 추세" in prompt and "도입 전후 비교" in prompt
    assert intents[1]["reading_flow"]["intent"] == "comparison"
    assert result.receipt["visual_intent_version"] == "svg-visual-intent/v1"
    assert "layout_id" not in intents[1] and "slots" not in intents[1]

    from copy import deepcopy
    from llmai.shared import ImageContentPart
    from utils.hashing import canonical_json_digest, sha256_prefixed
    from services.generation_core.drivers.svg.visual_intent import visual_intent_receipt

    source = json.dumps({"source_authoring_contract": {"version": "svg-source-whole-page/v1"},
        "page_id": 9012, "bounds": {"x": 800, "y": 600, "width": 1280}, "source_facts": [
            {"kind": "text", "data": {"text": "# 시스템 구조"}},
            {"kind": "text", "data": {"text": "핵심 메시지: 공용 API가 주문의 연결을 유지한다."}},
            {"kind": "text", "data": {"text": "주문 화면 → 주문 API: 접수\n주문 API → 주문 DB: 저장"}},
        ]}, ensure_ascii=False, indent=2)
    policy = {"id": "svg-quality-first", "version": 2}
    source_intents = build_deck_visual_intents([contents[0], source], ["section", "content"], quality_policy=policy)
    original_intent = deepcopy(source_intents[1])
    style = _reference_style(8)
    style["quality_recovery_policy"] = policy
    style["digest"] = canonical_json_digest({key: value for key, value in style.items() if key != "digest"})
    source_images = {f"source-slot-{index}": {"data_url": _data_url((100 + index, 80, 60, 255)),
                     "description": f"원본 그룹의 상세 화면 {index}: 연결 레이블을 유지", "required": False}
                     for index in range(3)}
    grounded = asyncio.run(module.author_slide_svg(source, style, "content", 1, 2,
                             visual_intent=source_intents[1], source_images=source_images))
    text = "\n".join(message.content if isinstance(message.content, str) else "\n".join(
        part.text for part in message.content if hasattr(part, "text")) for message in seen["messages"])
    images = [part for message in seen["messages"] if not isinstance(message.content, str)
              for part in message.content if isinstance(part, ImageContentPart)]
    image_evidence = json.JSONDecoder().raw_decode(text.split("SOURCE IMAGE EVIDENCE (actual offered content):\n", 1)[1])[0]
    assert source_intents[1] == original_intent
    assert source_intents[1]["emphasis"]["focal_text"] == "핵심 메시지: 공용 API가 주문의 연결을 유지한다."
    assert source_intents[1]["neighboring_intents"][0]["page_role"] == "section"
    assert source_intents[1]["source_evidence"]["blocks"][1]["source_path"] == "source_facts[1].data.text"
    assert json.dumps(source, ensure_ascii=False) in text
    assert "원본 그룹의 상세 화면 2" in text and "its supplied source-grounded reason" in text
    assert grounded.receipt["reference_selection"]["reference_roles"] == ["architecture", "content"]
    assert len(images) == 5 and len(image_evidence["source_image_inputs"]) == 3
    for offered in image_evidence["source_image_inputs"]:
        actual = images[offered["image_index"]]
        assert sha256_prefixed(actual.data) == sha256_prefixed(base64.b64decode(source_images[offered["slot_id"]]["data_url"].split(",", 1)[1]))
    assert grounded.receipt["visual_intent_digest"] == visual_intent_receipt(source_intents[1])["visual_intent_digest"]
    assert all(example["use_for"] for example in grounded.receipt["design_example_inputs"])

    legacy_style = deepcopy(style)
    legacy_style["quality_recovery_policy"]["version"] = 1
    legacy_style["digest"] = canonical_json_digest({key: value for key, value in legacy_style.items() if key != "digest"})
    legacy_intent = build_deck_visual_intents([source])[0]
    legacy = asyncio.run(module.author_slide_svg(source, legacy_style, "content", 0, 1, visual_intent=legacy_intent))
    legacy_text = "\n".join(str(message) for message in seen["messages"])
    assert "source_evidence" not in legacy_text and "SOURCE IMAGE EVIDENCE" not in legacy_text
    assert legacy.receipt["visual_intent_digest"] == visual_intent_receipt(legacy_intent)["visual_intent_digest"]


def test_new_intent_seed_replays_exactly_and_historical_checkpoint_keeps_context(monkeypatch):
    from services.svg_visual_intent_checkpoint import freeze_visual_intents
    from unittest.mock import AsyncMock

    async def scenario():
        job = object()
        saved = {}

        async def checkpoint(_job, _index, *, payload=None):
            if payload is not None:
                saved.update(payload)
            return dict(saved)

        first = await freeze_visual_intents(contents=["# 메시지\n42%"], roles=["content"], job=job,
                                          historical_payload=None, checkpoint=checkpoint)
        assert await freeze_visual_intents(contents=["# 메시지\n42%"], roles=["content"], job=job,
                                          historical_payload=dict(saved), checkpoint=checkpoint) == first
        import pytest
        with pytest.raises(ValueError, match="svg_visual_intent_checkpoint_conflict"):
            await freeze_visual_intents(contents=["changed"], roles=["content"], job=job,
                                        historical_payload=dict(saved), checkpoint=checkpoint)
        legacy = AsyncMock(return_value={"schema_version": "svg-slide-checkpoint/v1"})
        assert await freeze_visual_intents(contents=["# 메시지"], roles=["content"], job=job,
                                          historical_payload={"schema_version": "svg-slide-checkpoint/v1"},
                                          checkpoint=legacy) is None
        assert legacy.await_count == 1 and "payload" not in legacy.await_args.kwargs

    asyncio.run(scenario())


def _art_direction_style(style_id="hyundai-culture-center", **extra):
    from services.generation_core.drivers.svg.svg_style_adapter import adapt_svg_style
    from utils.authored_styles import resolve_authored_style
    from utils.hashing import canonical_json_digest

    snapshot = adapt_svg_style(resolve_authored_style(style_id), width=840, height=1188)
    snapshot.update(extra)
    snapshot["authoring_profile"] = {"id": "art-direction-v1", "version": 1}
    snapshot["digest"] = canonical_json_digest({key: value for key, value in snapshot.items() if key != "digest"})
    return snapshot


def test_art_direction_preserves_brand_source_contract_and_overrides_without_metadata():
    from copy import deepcopy
    from utils.hashing import canonical_json_digest

    for style_id in ("hyundai-culture-center", "default"):
        style = _art_direction_style(style_id, source_template={"content_contract": "verbatim-source-content"},
                                     overrides={"common_guidance": ["Keep exactly 42 supplied labels."]})
        original = deepcopy(style)
        design = module.svg_prompt_design(style)
        assert style == original
        assert design["tokens"] == style["tokens"]
        assert design["source_template"] == style["source_template"]
        assert design["overrides"] == style["overrides"]
        assert design["common_guidance"]["DATA VISUALIZATION"] == style["common_guidance"]["DATA VISUALIZATION"]
        assert design["common_guidance"]["AVOID"] == style["common_guidance"]["AVOID"]
        assert "art_direction" in design and "mapped_guidance" not in design
        assert all(key not in design for key in ("authoring_profile", "digest", "provenance", "reference_examples"))
        assert len(module.svg_design_text(style)) < len(json.dumps(original, ensure_ascii=False))
        if style_id == "hyundai-culture-center":
            assert "clause headings" in design["art_direction"]["typography"]
            assert "hairline" in design["art_direction"]["treatments"]
            assert "teal headers" in design["art_direction"]["treatments"]
            assert "choose its position" in design["art_direction"]["treatments"]
        for version in (2, 3, 4, 5, 6):
            candidate = deepcopy(style)
            candidate["authoring_profile"]["version"] = version
            candidate["digest"] = canonical_json_digest(
                {key: value for key, value in candidate.items() if key != "digest"})
            projected = module.svg_prompt_design(candidate)
            if version == 2:
                assert projected == design
            else:
                assert projected["tokens"] == style["tokens"]
                assert projected["source_template"] == style["source_template"]
                assert projected["overrides"] == style["overrides"]
                assert "title's position" in projected["art_direction"]["emphasis"]
                assert "without changing or omitting its wording" in projected["art_direction"]["emphasis"]


def test_art_direction_retains_master_furniture_and_uploaded_identity():
    from copy import deepcopy
    from utils.hashing import canonical_json_digest

    master = {"tokens": {"accent": "#123456"}, "system_furniture": {
        "logo": {"x": 12, "y": 14, "width": 56, "height": 18, "data_url": "private-bytes"}},
        "fonts": {"heading": {"family": "Pretendard", "size": 42, "digest": "font-digest"}}}
    pack = {"identity": {"colors": ["#123456"]}, "fonts": {"body": {"family": "Pretendard"}},
            "provenance": {"source_digest": "source-digest"}}
    for extra, key in (({"master_template": master}, "master_template"),
                       ({"design_pack_template": pack}, "design_pack_template")):
        style = _art_direction_style(**extra)
        design = module.svg_prompt_design(style)
        assert "art_direction" not in design
        if key == "master_template":
            assert design[key]["system_furniture"]["logo"] == {"x": 12, "y": 14, "width": 56, "height": 18}
            assert design[key]["fonts"]["heading"] == {"family": "Pretendard", "size": 42}
        else:
            assert design[key]["identity"] == pack["identity"]
            assert design[key]["fonts"] == pack["fonts"]
        v3 = deepcopy(style)
        v3["authoring_profile"]["version"] = 3
        v3["digest"] = canonical_json_digest({key: value for key, value in v3.items() if key != "digest"})
        assert module.svg_prompt_design(v3) == design
        assert "every exact source label" in module.svg_author_rules(v3, 18)

    other = _art_direction_style()
    other["style_id"] = "other-svg-style"
    other["digest"] = canonical_json_digest({key: value for key, value in other.items() if key != "digest"})
    before = module.svg_prompt_design(other)
    other["authoring_profile"]["version"] = 3
    other["digest"] = canonical_json_digest({key: value for key, value in other.items() if key != "digest"})
    assert module.svg_prompt_design(other) == before


def test_art_direction_actual_prompt_preserves_user_data_and_prefix_and_hashes_request(monkeypatch):
    from llmai.shared import ImageContentPart
    from utils.llm_calls.author_prompt_split import author_messages

    content = "# 정확한 표\n| 항목 | 값 |\n| 매출 | 42 |\n| 비용 | 12 |"
    instructions = "사진 없이 원문 유지, 페이지 수 3, 제목 42px."
    style = _art_direction_style()
    seen = {}
    _stub_provider(monkeypatch, _svg(""), seen)
    result = asyncio.run(module.author_slide_svg(content, style, "content", 1, 3, instructions=instructions))
    assert result.receipt["authoring_profile"] == style["authoring_profile"]
    assert result.receipt["author_prompt_digest"].startswith("sha256:")
    messages = seen["messages"]
    user_content = messages[1].content
    user_text = user_content if isinstance(user_content, str) else user_content[0].text
    assembled_text = messages[0].content + "\n\n" + user_text
    assert json.dumps(instructions, ensure_ascii=False) in user_text
    assert json.dumps(content, ensure_ascii=False) in user_text
    assert "data-native-table" in assembled_text and "data-native-chart" in assembled_text
    assert "Body text minimum: 18 CSS px" in assembled_text
    native_text = user_text.split("EXPLICIT NATIVE DATA (content only; choose composition freely):\n", 1)[1]
    native_data = json.loads(native_text.split("\n", 1)[0])
    assert native_data["tables"][0]["rows"] == [["항목", "값"], ["매출", "42"], ["비용", "12"]]
    design = module.svg_design_text(style)
    assert assembled_text.count(design) == 1
    assert "author_prompt_digest" not in assembled_text and "authoring_profile" not in assembled_text

    rules = module.svg_author_rules(style, 18)
    first = author_messages(rules, design + "\n\npage one", design, enabled=True)
    second = author_messages(rules, design + "\n\npage two", design, enabled=True)
    assert first[0].content == second[0].content and first[1].content != second[1].content
    images = [ImageContentPart(data=b"first", mime_type="image/png"),
              ImageContentPart(data=b"second", mime_type="image/png")]
    def digest(text="page one", offered=images, budget=1000, kwargs=None):
        assembled = author_messages(rules, design + "\n\n" + text, design, enabled=True, image_parts=offered)
        receipt = module.svg_author_input_receipt(style, assembled, provider="p", model="m", budget=budget,
                                                  kwargs=kwargs or {})
        assert "first" not in json.dumps(receipt) and "second" not in json.dumps(receipt)
        return receipt["author_prompt_digest"]
    baseline = digest()
    assert digest() == baseline
    assert digest(text="page two") != baseline
    assert digest(offered=list(reversed(images))) != baseline
    assert digest(budget=1001) != baseline
    assert digest(kwargs={"extra_body": {"reasoning_effort": "high"}}) != baseline


def test_legacy_author_design_text_rules_and_receipt_remain_exact(monkeypatch):
    from services.generation_core.drivers.svg.svg_style_adapter import adapt_svg_style
    from utils.authored_styles import resolve_authored_style

    for style_id in ("hyundai-culture-center", "default"):
        style = adapt_svg_style(resolve_authored_style(style_id), width=840, height=1188)
        assert module.svg_design_text(style) == json.dumps(
            {key: value for key, value in style.items() if key not in {"references", "diagnostics"}}, ensure_ascii=False)
        assert module.svg_author_rules(style, 18) == module.svg_author_rules(_art_direction_style(style_id), 18)
    seen = {}
    _stub_provider(monkeypatch, _svg(""), seen)
    result = asyncio.run(module.author_slide_svg("# 제목", _style(), "content", 1, 3))
    assert "authoring_profile" not in result.receipt and "author_prompt_digest" not in result.receipt


def test_v8_generic_direction_preserves_all_guidance_and_contracts_for_every_identity():
    from copy import deepcopy
    from utils.hashing import canonical_json_digest
    from services.generation_core.drivers.svg.art_direction import GENERIC_ART_DIRECTION_RULES
    from services.svg_authoring_profile import validate_authoring_profile

    assert validate_authoring_profile("art-direction-v1@8") == {"id": "art-direction-v1", "version": 8}
    for style_id in ("hyundai-culture-center", "default", "botanical-journal"):
        for extra in ({}, {"master_template": {"brand_assets": {"wordmark": "Brand 42"},
                                               "system_furniture": {"digest": "keep-digest", "reservations": [42]}}},
                      {"design_pack_template": {"identity": {"brand": "Brand 42"},
                                                "composition_guidance": ["Exactly 3 columns at x=42."]}}):
            style = _art_direction_style(style_id, **extra)
            style["authoring_profile"]["version"] = 8
            style["digest"] = canonical_json_digest({k: v for k, v in style.items() if k != "digest"})
            original = deepcopy(style)
            design = module.svg_prompt_design(style)
            direction = design["art_direction"]
            identity = direction["identity_guidance"]
            reference = direction["reference_guidance"]
            assert {**identity, **reference["common_guidance"]} == style["common_guidance"]
            for key in ("mapped_guidance", "overrides", "composition_guidance"):
                assert reference[key] == style[key]
            assert design["tokens"] == style["tokens"]
            for key, value in extra.items():
                assert design[key] == value
            renamed = deepcopy(style)
            renamed["style_id"] = "another-brand-with-no-production-branch"
            renamed["digest"] = canonical_json_digest({k: v for k, v in renamed.items() if k != "digest"})
            assert module.svg_prompt_design(renamed)["art_direction"] == direction
            assert GENERIC_ART_DIRECTION_RULES in module.svg_author_rules(style, 18)
            assert GENERIC_ART_DIRECTION_RULES not in module.svg_design_text(style)
            assert style == original


def test_v8_correction_carries_previous_svg_and_preserves_v7_floor_and_coverage(monkeypatch):
    from utils.hashing import canonical_json_digest
    from services.svg_contract import source_digest

    style = _art_direction_style()
    style["authoring_profile"]["version"] = 8
    style["digest"] = canonical_json_digest({k: v for k, v in style.items() if k != "digest"})
    seen = {}
    previous = _svg("")
    _stub_provider(monkeypatch, previous, seen)
    result = asyncio.run(module.author_slide_svg("# 제목", style, "content", 1, 3,
                        instructions="Keep all supplied wording.", correction_hint="measured overlap",
                        previous_svg=previous))
    parts = [m.content for m in seen["messages"]]
    assembled = "\n".join(p if isinstance(p, str) else "\n".join(
        item.text for item in p if hasattr(item, "text")) for p in parts)
    assert json.dumps({"previous_svg": previous}, ensure_ascii=False) in assembled
    assert "For this correction, use at least 18 CSS px for body text" in assembled
    assert "preserve its composition" in assembled and "untrusted source data" in assembled
    assert result.receipt["repair_input_svg_digest"] == source_digest(previous)
    assert result.receipt["source_classification_coverage"]["version"] == "svg-source-classification-coverage/v4"
    assert result.receipt["reference_selection"]["version"] == "svg-reference-selection/v8"
    assert result.receipt["body_font_floor_px"] == 18
    historical = dict(style)
    historical["authoring_profile"] = {"id": "art-direction-v1", "version": 7}
    historical["digest"] = canonical_json_digest({k: v for k, v in historical.items() if k != "digest"})
    old = asyncio.run(module.author_slide_svg("# 제목", historical, "content", 1, 3,
                    correction_hint="measured overlap", previous_svg=previous))
    assert "repair_input_svg_digest" not in old.receipt
    assert "previous_svg" not in str(seen["messages"])


def _reference_style(version=2):
    from utils.hashing import canonical_json_digest

    style = _style()
    style["references"] = {role: [{"data_url": _data_url((index * 30, 10, 20, 255))}]
                           for index, role in enumerate(("cover", "content", "data", "timeline", "architecture"))}
    style["authoring_profile"] = {"id": "art-direction-v1", "version": version}
    style["digest"] = canonical_json_digest({key: value for key, value in style.items() if key != "digest"})
    return style


def test_v2_reference_role_uses_literal_native_schedule_and_relationship_evidence():
    from copy import deepcopy
    from services.generation_core.drivers.svg.reference_selection import select_reference_role
    from services.generation_core.drivers.svg.visual_intent import build_deck_visual_intents
    from utils.hashing import canonical_json_digest

    style = _reference_style()
    original = deepcopy(style)
    cases = [
        ("# 계획\n1–2주: 인터뷰\n3–4주: 구축", "timeline"),
        ("# 추진 일정\nA: 2026-11-02~2026-11-15\nB: 2026-11-16~2026-12-06\n의존관계: A → B", "timeline"),
        ("# 시스템 구성\n상점 화면 → 주문 API: 등록\n주문 API → 주문 DB: 저장", "architecture"),
        ("# 비교\n| 항목 | 값 |\n| 매출 | 42 |", "data"),
        ("# 결과\nChart: 1월 240, 2월 360", "data"),
        ("# 차트 사용 검토\nchart를 검토한다. 수치는 제공하지 않았다.", "content"),
        ("# 도입 절차\n1. 접수\n2. 배차", "content"),
        ("# 프로세스\n접수 → 배차\n배차 → 배송", "content"),
        ("# 성과\n240건 → 740건", "content"),
        ("# 일정 비교표\n1–2주: 인터뷰\n3–4주: 구축\n| 항목 | 값 |\n| 비용 | 42 |", "data"),
    ]
    for content, expected in cases:
        intent = build_deck_visual_intents([content])[0]
        selected = select_reference_role(style, "content", content, intent)
        assert selected["selected_role"] == expected
        assert selected["page_role"] == "content"
    assert select_reference_role(style, "cover", cases[2][0])["selected_role"] == "cover"
    assert style == original
    del style["references"]["timeline"]
    style["digest"] = canonical_json_digest({key: value for key, value in style.items() if key != "digest"})
    missing = select_reference_role(style, "content", cases[0][0])
    assert missing["requested_role"] == "timeline" and missing["selected_role"] == "content"
    assert missing["fallback_to_page_role"] is True
    assert select_reference_role(_reference_style(1), "content", cases[2][0]) is None
    assert select_reference_role(_style(), "content", cases[2][0]) is None

    metadata_source = json.dumps({"source_authoring_contract": {"version": "svg-source-whole-page/v1"},
        "page_id": 8123, "bounds": {"x": 343, "y": 720}, "source_facts": [
            {"kind": "text", "data": {"text": "# 고객 지원"}},
            {"kind": "text", "data": {"text": "핵심 메시지: 담당 창구가 요청의 맥락을 유지한다."}},
            {"kind": "text", "data": {"text": "담당자는 요청 내용을 확인하고 고객에게 결과를 설명한다."}},
        ]}, ensure_ascii=False, indent=2)
    legacy_intent = build_deck_visual_intents([metadata_source])[0]
    legacy_style = _reference_style(8)
    legacy_style["quality_recovery_policy"] = {"id": "svg-quality-first", "version": 1}
    legacy_style["digest"] = canonical_json_digest({key: value for key, value in legacy_style.items() if key != "digest"})
    legacy_selection = select_reference_role(legacy_style, "content", metadata_source, legacy_intent)
    assert legacy_selection["basis"] == "source_numeric_emphasis"
    current = deepcopy(legacy_style)
    current["quality_recovery_policy"]["version"] = 2
    current["digest"] = canonical_json_digest({key: value for key, value in current.items() if key != "digest"})
    selected = select_reference_role(current, "content", metadata_source, legacy_intent)
    assert "data" not in selected["reference_roles"]
    assert all(advice["basis"] != "source_numeric_emphasis" for advice in selected["expression_advice"])
    assert selected["source_evidence_basis"] == "original_source_facts"
    assert build_deck_visual_intents([metadata_source], quality_policy=legacy_style["quality_recovery_policy"]) == [legacy_intent]
    quantity_source = metadata_source.replace("담당 창구가 요청의 맥락을 유지한다.", "처리율이 42%를 기록했다.")
    assert any(advice["basis"] == "source_numeric_emphasis" for advice in
               select_reference_role(current, "content", quantity_source, legacy_intent)["expression_advice"])
    unknown_json = json.dumps({"metadata": {"page_id": 42, "description": "42원, 2일"}})
    unknown = select_reference_role(current, "content", unknown_json)
    assert unknown["source_evidence_basis"] == "unclassified_json" and "data" not in unknown["reference_roles"]


def test_v2_author_changes_only_reference_input_and_records_actual_reference_digest(monkeypatch):
    from llmai.shared import ImageContentPart
    from utils.hashing import sha256_prefixed

    content = "# 비교\n| 항목 | 값 |\n| 매출 | 42 |"
    seen = {}
    _stub_provider(monkeypatch, _svg(""), seen)
    budget_roles = []
    monkeypatch.setattr(module, "_authoring_output_profile",
                        lambda **kwargs: (budget_roles.append(kwargs["role"]) or 1000, None))
    calls = []
    sampler = module.svg_reference_samples
    def track_samples(style, role, index, count):
        calls.append(role)
        return sampler(style, role, index, count)
    monkeypatch.setattr(module, "svg_reference_samples", track_samples)
    source = {"source-slot-a": {"data_url": _data_url((220, 30, 10, 255)), "required": False}}
    first = asyncio.run(module.author_slide_svg(content, _reference_style(1), "content", 1, 3,
                                                 source_images=source))
    messages_v1 = seen["messages"]
    assert calls == ["content"]
    assert "reference_selection" not in first.receipt and "reference_image_digests" not in first.receipt
    calls.clear()
    second = asyncio.run(module.author_slide_svg(content, _reference_style(2), "content", 1, 3,
                                                  source_images=source))
    messages_v2 = seen["messages"]
    assert calls == ["data", "content"] and budget_roles == ["content", "content"]
    def parts(messages):
        return [part for message in messages if not isinstance(message.content, str)
                for part in message.content if isinstance(part, ImageContentPart)]
    images_v1, images_v2 = parts(messages_v1), parts(messages_v2)
    assert len(images_v1) == len(images_v2) == 2
    assert images_v1[0].data != images_v2[0].data and images_v1[1].data == images_v2[1].data
    def text(messages):
        return [message.content if isinstance(message.content, str) else
                [part.text for part in message.content if not isinstance(part, ImageContentPart)]
                for message in messages]
    assert text(messages_v1) == text(messages_v2)
    assert second.receipt["reference_selection"]["selected_role"] == "data"
    assert second.receipt["reference_image_digests"] == [sha256_prefixed(images_v2[0].data)]
    assert second.receipt["author_prompt_digest"] != first.receipt["author_prompt_digest"]
    assert "reference_selection" not in str(text(messages_v2))
    replay = asyncio.run(module.author_slide_svg(content, _reference_style(2), "content", 1, 3,
                                                  source_images=source))
    assert replay.receipt["author_prompt_digest"] == second.receipt["author_prompt_digest"]
    assert replay.receipt["reference_image_digests"] == second.receipt["reference_image_digests"]

    calls.clear()
    instructions = "설명 문장 그대로 유지, 제목 42px."
    correction = '{"failures":[{"kind":"text_overlap","pairs":[["svg-85","svg-89"]]}]}'
    third = asyncio.run(module.author_slide_svg(content, _reference_style(3), "content", 1, 3,
        source_images=source, instructions=instructions, correction_hint=correction))
    messages_v3 = seen["messages"]
    assert calls == ["data", "content"] and budget_roles[-1] == "content"
    assert [image.data for image in parts(messages_v3)] == [image.data for image in images_v2]
    assert third.receipt["reference_selection"] == {
        **second.receipt["reference_selection"], "version": "svg-reference-selection/v3"}
    assert third.receipt["reference_image_digests"] == second.receipt["reference_image_digests"]
    assert third.receipt["authoring_profile"] == {"id": "art-direction-v1", "version": 3}
    assert third.receipt["author_prompt_digest"] != second.receipt["author_prompt_digest"]
    assembled = "\n".join(item if isinstance(item, str) else "\n".join(item)
                          for item in text(messages_v3))
    for value in (content, instructions, correction):
        assert json.dumps(value, ensure_ascii=False) in assembled
    assert "every exact source label" in assembled and "nodes and connector routes" in assembled
    assert "every exact source label" not in str(text(messages_v2))
    no_repair = module.svg_author_rules(_reference_style(3), 18)
    assert module.SVG_SPATIAL_REPAIR_RULES_V3 not in no_repair

    diagram = ("# 시스템 구성\n채널: 상점 화면, 기사 앱\n서비스: 주문 API, 배송 API\n"
               "외부 연계·저장소: 주문 DB, 운영 DB\n상점 화면 → 주문 API: 등록\n"
               "주문 API → 주문 DB: 주문 저장\n기사 앱 → 배송 API: 상태 전송\n"
               "배송 API → 운영 DB: 상태 저장")
    nodes_only = '<text x="40" y="120" font-family="Inter" font-size="18" fill="#111111">'
    nodes_only += "상점 화면, 기사 앱, 주문 API, 배송 API, 주문 DB, 운영 DB</text>"
    _stub_provider(monkeypatch, _svg(nodes_only), seen)
    historical = asyncio.run(module.author_slide_svg(diagram, _reference_style(3), "content", 1, 3))
    assert historical.receipt["validation_error"] is None
    assert "source_classification_coverage" not in historical.receipt
    failed = asyncio.run(module.author_slide_svg(diagram, _reference_style(4), "content", 1, 3))
    coverage = failed.receipt["source_classification_coverage"]
    assert coverage["checked_count"] == coverage["missing_count"] == 3
    assert failed.receipt["validation_error"].startswith("svg_source_classification_missing:")
    assert failed.receipt["reference_selection"]["selected_role"] == "architecture"
    assert failed.receipt["reference_selection"]["version"] == "svg-reference-selection/v4"
    from services.generation_core.drivers.svg.render import attempt_render, RETRY
    from services.authored_checkpoint_errors import AuthoredStageError
    import pytest
    checkpoints = []
    async def checkpoint(_job, _index, *, payload):
        checkpoints.append(payload.get("repair_hint"))
    render_context = SimpleNamespace(source_reflow=False, raster_reconstruction=False, canonical_assets=False,
                                     snapshot=_reference_style(4))
    payload = {"attempts": [{"receipt": failed.receipt}]}
    async def render_failure(ordinal):
        return await attempt_render(ctx=render_context, request=None, presentation_id="p", index=1,
            content=diagram, payload=payload, svg=failed.svg_content, ordinal=ordinal, job=None,
            checkpoint=checkpoint, async_session_maker=None, write_png=None)
    assert asyncio.run(render_failure(0)) is RETRY
    assert payload["svg_content"] is None
    assert payload["initial_quality"]["passed"] is False
    assert checkpoints[0] == failed.receipt["validation_error"]
    with pytest.raises(AuthoredStageError):
        asyncio.run(render_failure(1))
    assembled_v4 = "\n".join(item if isinstance(item, str) else "\n".join(item)
                             for item in text(seen["messages"]))
    assert json.dumps(diagram, ensure_ascii=False) in assembled_v4
    assert "coherent view with shared nodes" in assembled_v4
    assert "Repeated node names or colors alone" in assembled_v4

    lists = ''.join('<text x="40" y="120" font-family="Inter" font-size="18" fill="#111111">'
                    + line.replace(": ", ':<tspan x="40" y="140">') + '</tspan></text>'
                    for line in diagram.splitlines()[1:4])
    _stub_provider(monkeypatch, _svg(nodes_only + lists), seen)
    recovered = asyncio.run(module.author_slide_svg(diagram, _reference_style(4), "content", 1, 3,
        correction_hint=payload["repair_hint"], receipt_attempt_ordinal=2))
    assert recovered.receipt["validation_error"] is None
    assert recovered.receipt["source_classification_coverage"]["missing_count"] == 0
    from xml.etree import ElementTree as ET
    hidden = ET.fromstring(_svg(nodes_only + '<g opacity="0.0">' + lists + '</g>'))
    assert module.source_classification_coverage(diagram, hidden)["missing_count"] == 3
    hidden_span = ET.fromstring(_svg(lists.replace('<tspan ', '<tspan opacity="0" ')))
    assert module.source_classification_coverage(diagram, hidden_span)["missing_count"] == 3
    unrelated = ET.fromstring(_svg('<text x="40" y="80">채널:</text>' + nodes_only))
    assert module.source_classification_coverage(diagram, unrelated)["missing_count"] == 3
    assert module.source_classification_coverage(content, ET.fromstring(_svg("")))["checked_count"] == 0
    assert module.SVG_SOURCE_PRESERVATION_RULES_V4 not in module.svg_author_rules(_reference_style(3), 18)

    aligned = ''
    for x, line in zip((61, 417, 773), diagram.splitlines()[1:4]):
        category, members = line.split(': ', 1)
        first_member, second_member = members.split(', ')
        aligned += (f'<text x="{x}" y="171" font-family="Inter" font-size="18" fill="#FFFFFF">'
                    f'{category}</text><text x="{x}" y="209" text-anchor="start" '
                    'font-family="Inter" font-size="17" fill="#111111">'
                    f'<tspan x="{x}" y="209">{first_member},</tspan>'
                    f'<tspan x="{x}" y="235">{second_member}</tspan></text>')
    def associated(markup):
        return module.source_classification_coverage(diagram, ET.fromstring(_svg(markup)),
                                                    allow_associated_blocks=True)
    assert module.source_classification_coverage(diagram, ET.fromstring(_svg(aligned)))["missing_count"] == 3
    assert associated(aligned)["missing_count"] == 0
    assert associated(aligned)["association_counts"] == {"single_text": 0, "aligned_text_blocks": 3}
    assert associated('<g transform="translate(20,30)">' + aligned + '</g>')["missing_count"] == 0
    assert associated(aligned.replace('y="209"', 'y="409"').replace('y="235"', 'y="435"'))["missing_count"] == 3
    assert associated(aligned.replace('text-anchor="start"', 'text-anchor="middle"'))["missing_count"] == 3
    assert associated(aligned.replace('text-anchor="start"', 'transform="translate(0,1)"'))["missing_count"] == 3
    duplicate = '<text x="61" y="171" font-family="Inter" font-size="18" fill="#111111">채널</text>'
    assert associated(aligned + duplicate)["missing_count"] == 1
    crossing = '<text x="61" y="190" font-family="Inter" font-size="18" fill="#111111">서비스</text>'
    assert associated(aligned + crossing)["missing_count"] == 1
    assert associated(aligned.replace('기사 앱', '없는 구성원'))["missing_count"] == 1
    assert associated('<g opacity="0.0">' + aligned + '</g>')["missing_count"] == 3
    assert associated(nodes_only)["missing_count"] == 3
    _stub_provider(monkeypatch, _svg(aligned), seen)
    fifth = asyncio.run(module.author_slide_svg(diagram, _reference_style(5), "content", 1, 3))
    assert fifth.receipt["validation_error"] is None
    assert fifth.receipt["source_classification_coverage"]["version"] == "svg-source-classification-coverage/v2"
    assert fifth.receipt["source_classification_coverage"]["missing_count"] == 0
    assert fifth.receipt["reference_selection"]["version"] == "svg-reference-selection/v5"
    rules_v5 = module.svg_author_rules(_reference_style(5), 18)
    assert "reserve enough width and height" in rules_v5
    assert "inside their own explanation area" in rules_v5
    assert "Keep each complete list in one text element" not in rules_v5
    assert module.SVG_SOURCE_PRESERVATION_RULES_V5 not in module.svg_author_rules(_reference_style(4), 18)

    split_aligned = ''
    for x, line in zip((64, 436, 808), diagram.splitlines()[1:4]):
        category, members = line.split(': ', 1)
        first_member, second_member = members.split(', ')
        split_aligned += (f'<text x="{x}" y="168" font-family="Inter" font-size="17" fill="#111111">'
            f'{category}</text><text x="{x}" y="207" font-family="Inter" font-size="17" fill="#111111">'
            f'{first_member},</text><text x="{x}" y="235" font-family="Inter" font-size="17" fill="#111111">'
            f'{second_member}</text>')
    def split_coverage(markup):
        return module.source_classification_coverage(diagram, ET.fromstring(_svg(markup)),
            allow_associated_blocks=True, allow_split_list_blocks=True)
    assert associated(split_aligned)["missing_count"] == 3
    assert split_coverage(split_aligned)["missing_count"] == 0
    assert split_coverage(split_aligned)["version"] == "svg-source-classification-coverage/v3"
    assert split_coverage(split_aligned)["association_counts"]["aligned_text_blocks"] == 3
    assert split_coverage(split_aligned.replace('y="235"', 'y="435"'))["missing_count"] == 3
    assert split_coverage(split_aligned.replace('기사 앱', '누락된 구성원'))["missing_count"] == 1
    repeated = '<text x="64" y="235" font-family="Inter" font-size="17" fill="#111111">기사 앱</text>'
    assert split_coverage(split_aligned + repeated)["missing_count"] == 1
    boundary = '<text x="64" y="220" font-family="Inter" font-size="17" fill="#111111">서비스</text>'
    assert split_coverage(split_aligned + boundary)["missing_count"] == 1
    unrelated = '<text x="64" y="226" font-family="Inter" font-size="17" fill="#111111">다른 설명</text>'
    assert split_coverage(split_aligned + unrelated)["missing_count"] == 1
    assert split_coverage(nodes_only)["missing_count"] == 3
    assert split_coverage('<g opacity="0">' + split_aligned + '</g>')["missing_count"] == 3

    branching = diagram + "\n주문 API → 배송 API: 요청\n배송 API → 상점 화면: 확인"
    evidence = module.source_relationship_evidence(branching)
    assert evidence["edge_count"] == evidence["unique_endpoint_count"] == 6
    degrees = {item["name"]: item for item in evidence["shared_endpoints"]}
    assert degrees["주문 API"] == {"name": "주문 API", "incoming_count": 1, "outgoing_count": 2}
    assert degrees["배송 API"] == {"name": "배송 API", "incoming_count": 2, "outgoing_count": 2}
    assert not {"layout_id", "x", "y", "width", "height", "position"}.intersection(evidence)
    _stub_provider(monkeypatch, _svg(split_aligned), seen)
    sixth = asyncio.run(module.author_slide_svg(branching, _reference_style(6), "content", 1, 3))
    assert sixth.receipt["validation_error"] is None
    assert sixth.receipt["source_classification_coverage"]["missing_count"] == 0
    assert sixth.receipt["reference_selection"]["version"] == "svg-reference-selection/v6"
    assembled_v6 = "\n".join(item if isinstance(item, str) else "\n".join(item)
                             for item in text(seen["messages"]))
    assert json.dumps(branching, ensure_ascii=False) in assembled_v6
    assert json.dumps(evidence, ensure_ascii=False) in assembled_v6
    assert "SOURCE RELATIONSHIP EVIDENCE" not in rules_v5
    assert module.SVG_CLASSIFICATION_LINE_BREAKS_V6 not in rules_v5

    from copy import deepcopy
    from utils.hashing import canonical_json_digest
    from services.generation_core.drivers.svg.source_classification_association import (
        apply_source_classification_association, source_classifications,
    )
    frozen_seven = _reference_style(7)
    preserved_seven = deepcopy(frozen_seven)
    legacy = {key: value for key, value in frozen_seven.items() if key != "authoring_profile"}
    legacy["digest"] = canonical_json_digest({key: value for key, value in legacy.items() if key != "digest"})
    assert module.svg_prompt_design(frozen_seven) == module.svg_prompt_design(legacy)
    assert module.svg_author_rules(frozen_seven, 18) == module.svg_author_rules(legacy, 18)
    assert frozen_seven["digest"] != legacy["digest"]
    assert frozen_seven == preserved_seven
    import services.document_canvas_generation as canvas_generation
    _stub_provider(monkeypatch, _svg(nodes_only), seen)
    monkeypatch.setattr(canvas_generation, "generation_font_floor", lambda *args: 16.256)
    asyncio.run(module.author_slide_svg(diagram, legacy, "content", 1, 3))
    legacy_messages = seen["messages"]
    calls.clear()
    seventh = asyncio.run(module.author_slide_svg(diagram, frozen_seven, "content", 1, 3))
    assert calls == ["content"]
    assert text(seen["messages"]) == text(legacy_messages)
    assert [image.data for image in parts(seen["messages"])] == [image.data for image in parts(legacy_messages)]
    assert seventh.receipt["authoring_profile"] == {"id": "art-direction-v1", "version": 7}
    assert seventh.receipt["source_classification_coverage"]["status"] == "pending_render_association"
    assert seventh.receipt["validation_error"] is None
    assert "reference_selection" not in seventh.receipt
    assert "SOURCE RELATIONSHIP EVIDENCE" not in str(text(seen["messages"]))
    assert "For this correction, use at least" not in str(text(seen["messages"]))
    assert frozen_seven == preserved_seven
    repaired_seven = asyncio.run(module.author_slide_svg(diagram, frozen_seven, "content", 1, 3,
        correction_hint="svg_contract_unsupported_attribute:font-family"))
    assert "For this correction, use at least 17 CSS px for body text" in str(text(seen["messages"]))
    assert repaired_seven.receipt["body_font_floor_px"] == 16.256
    assert frozen_seven == preserved_seven
    asyncio.run(module.author_slide_svg(diagram, _reference_style(6), "content", 1, 3,
        correction_hint="svg_contract_unsupported_attribute:font-family"))
    assert "For this correction, use at least" not in str(text(seen["messages"]))

    groups = source_classifications(diagram)
    colors = ("#0088AA", "#CC5500", "#7755AA")
    legend_svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
    for index, (group, color) in enumerate(zip(groups, colors)):
        x = 50 + index * 350
        legend_svg += f'<text x="{x}" y="30" fill="{color}">{group["label"]}</text>'
        for row, member in enumerate(reversed(group["members"])):
            y = 100 + row * 60
            legend_svg += (f'<g><rect x="{x}" y="{y}" width="200" height="40" fill="{color}"/>'
                f'<text x="{x + 10}" y="{y + 26}" fill="#FFFFFF">{member}</text></g>')
    legend_svg += '</svg>'

    def measured(markup):
        root = ET.fromstring(markup)
        keys = {node: f"svg-{index}" for index, node in enumerate(root.iter())}
        parents = {child: parent for parent in root.iter() for child in parent}
        elements = []
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            x, y = float(node.get("x", "0")), float(node.get("y", "0"))
            box = {"x": x, "y": y - 18 if tag == "text" else y,
                "width": float(node.get("width", "170")), "height": float(node.get("height", "20"))}
            elements.append({"qid": keys[node], "tag": tag, "parent_qid": keys.get(parents.get(node)),
                "css_visible": True, "effective_opacity": 1, "bbox": box,
                "quad": [{"x": px, "y": py} for px, py in ((x, box["y"]),
                    (x + box["width"], box["y"]), (x + box["width"], box["y"] + box["height"]),
                    (x, box["y"] + box["height"]))],
                "has_owned_text": tag == "text", "has_owned_paint": tag == "rect",
                "owned_text": node.text or "", "rendered_text": node.text or "",
                "text_ink_fragments": [box] if tag == "text" else []})
        from utils.hashing import sha256_hex
        return {"passed": True, "verified": True, "measurement_complete": True,
                "source_digest": sha256_hex(markup.encode()),
                "browser_metrics": {"elements": elements}, "failures": []}

    def association(markup, quality=None):
        quality = quality if quality is not None else measured(markup)
        apply_source_classification_association(quality, markup, diagram)
        return quality

    accepted = association(legend_svg)
    assert accepted["source_classification_association"]["status"] == "proven"
    assert {w["basis"] for w in accepted["source_classification_association"]["witnesses"]} == {"explicit_color_legend"}
    keyed_root = ET.fromstring(legend_svg)
    for index, header in enumerate(list(keyed_root.findall("{http://www.w3.org/2000/svg}text"))):
        header.set("fill", colors[1])
        key = ET.Element("{http://www.w3.org/2000/svg}rect", {
            "x": str(float(header.get("x")) - 10), "y": "8", "width": "200", "height": "28",
            "fill": colors[index],
        })
        keyed_root.insert(list(keyed_root).index(header), key)
    contrasting_legend = ET.tostring(keyed_root, encoding="unicode")
    assert association(contrasting_legend)["source_classification_association"]["status"] == "proven"
    keyed_root.insert(0, ET.Element("{http://www.w3.org/2000/svg}rect", {
        "x": "25", "y": "12", "width": "12", "height": "12", "fill": colors[1],
    }))
    assert association(ET.tostring(keyed_root, encoding="unicode"))["passed"] is False
    svg_paint_flags = measured(legend_svg)
    for item in svg_paint_flags["browser_metrics"]["elements"]:
        item["has_owned_paint"] = False
    assert association(legend_svg, svg_paint_flags)["source_classification_association"]["status"] == "proven"
    uncertain_paint = measured(legend_svg)
    next(item for item in uncertain_paint["browser_metrics"]["elements"]
         if item["tag"] == "rect")["occlusion_unknown_fraction"] = 1
    assert association(legend_svg, uncertain_paint)["passed"] is False
    wrong_bytes = measured(legend_svg)
    wrong_bytes["source_digest"] = "0" * 64
    assert association(legend_svg, wrong_bytes)["passed"] is False
    hidden = measured(legend_svg)
    next(item for item in hidden["browser_metrics"]["elements"] if item["owned_text"] == groups[0]["members"][0])["css_visible"] = False
    assert association(legend_svg, hidden)["passed"] is False
    duplicate_paint = legend_svg.replace('</g>', '<rect x="50" y="100" width="200" height="40" fill="#0088AA"/></g>', 1)
    assert association(duplicate_paint)["passed"] is False
    assert association(legend_svg.replace('fill="#0088AA"', 'fill="#CC5500"', 1))["passed"] is False
    inherited = legend_svg.replace('fill="#0088AA"', '')
    assert association(inherited)["passed"] is False
    assert association(legend_svg.replace('</svg>', '<style>rect{fill:#123456}</style></svg>'))["passed"] is False
    unmeasured = measured(legend_svg)
    unmeasured["measurement_complete"] = False
    assert association(legend_svg, unmeasured)["passed"] is False
    assert association(_svg(nodes_only))["source_classification_association"]["missing_count"] == 3

    panels = legend_svg
    for index, group in enumerate(groups):
        x = 50 + index * 350
        panels = panels.replace(f'<text x="{x}" y="30"',
            f'<rect x="{x - 10}" y="0" width="230" height="220" fill="#EEEEEE"/><text x="{x}" y="30"')
    panel_quality = association(panels)
    assert panel_quality["source_classification_association"]["status"] == "proven"
    assert {w["basis"] for w in panel_quality["source_classification_association"]["witnesses"]} == {"visible_class_enclosure"}
    assert association(_svg(split_aligned))["source_classification_association"]["status"] == "proven"

    from services.generation_core.drivers.svg.source_classification_association import (
        _axis_aligned, _paint_contains,
    )
    shape_box = {"x": 0, "y": 0, "width": 100, "height": 100}
    corner_box = {"x": 1, "y": 1, "width": 10, "height": 10}
    center_box = {"x": 40, "y": 40, "width": 20, "height": 20}
    for tag, attributes in (("circle", 'cx="50" cy="50" r="50"'),
                            ("ellipse", 'cx="50" cy="50" rx="50" ry="50"'),
                            ("rect", 'width="100" height="100" rx="30"')):
        shape = {"box": shape_box, "node": ET.fromstring(f'<{tag} {attributes}/>'), "item": {"tag": tag}}
        assert not _paint_contains(shape, corner_box)
        assert _paint_contains(shape, center_box)
    sharp = {"box": shape_box, "node": ET.fromstring('<rect width="100" height="100"/>'), "item": {"tag": "rect"}}
    assert _paint_contains(sharp, corner_box)
    transformed = ET.fromstring('<g transform="translate(20,30)"><rect transform="scale(2,1)"/></g>')
    source_parents = {child: parent for parent in transformed.iter() for child in parent}
    assert _axis_aligned(transformed[0], source_parents)
    for transform in ('rotate(30)', 'skewX(12)', 'matrix(1,0,0,1,0,0)', 'translate(10%,0)', 'scale(0)', 'translate(1px,0)'):
        transformed.set("transform", transform)
        assert not _axis_aligned(transformed[0], source_parents)
    skewed_quality = measured(legend_svg)
    node_paint = next(item for item in skewed_quality["browser_metrics"]["elements"] if item["tag"] == "rect")
    node_paint["quad"][1]["y"] += 2
    assert association(legend_svg, skewed_quality)["passed"] is False

    import services.svg_contract as contract_module
    import services.svg_renderer as renderer_module
    import services.svg_master_furniture as furniture_module
    monkeypatch.setattr(contract_module, "finalize_svg", lambda svg, **kwargs: svg)
    monkeypatch.setattr(furniture_module, "master_furniture_receipt", lambda *args, **kwargs: None)
    async def rendered_fixture(svg, **kwargs):
        quality = measured(svg)
        quality.update(passed=False, body_font_floor_px=16.256,
            failures=[{"kind": "small_body_text", "qid": "svg-2", "minimum_px": 16.256}])
        return SimpleNamespace(quality=quality, png=b"png")
    monkeypatch.setattr(renderer_module, "render_svg_with_metrics", rendered_fixture)
    render_ctx = SimpleNamespace(snapshot=frozen_seven, source_reflow=False, raster_reconstruction=False,
        canonical_assets=False, count=3, canvas=SimpleNamespace(width_px=1280, height_px=720),
        render_semaphore=asyncio.Semaphore(1), physical_kwargs={})
    render_payload = {"attempts": [{"receipt": seventh.receipt}]}
    async def save_render(*args, **kwargs):
        pass
    async def measured_failure(ordinal):
        return await attempt_render(ctx=render_ctx, request=SimpleNamespace(instructions=None, aspect_ratio="16:9"),
            presentation_id="presentation", index=1, content=diagram, payload=render_payload,
            svg=_svg(nodes_only), ordinal=ordinal, job=None, checkpoint=save_render,
            async_session_maker=None, write_png=lambda _: {})
    assert asyncio.run(measured_failure(0)) is RETRY
    assert json.loads(render_payload["repair_hint"])["kind"] == "svg_source_classification_missing"
    geometry_hint = json.loads(render_payload["repair_hint"])["geometry"]
    assert geometry_hint == {"failure_counts": {"small_body_text": 1}, "minimum_body_px": 16.256,
                             "safe_body_px": 17, "sample_qids": ["svg-2"]}
    assert len(render_payload["repair_hint"]) <= 1500
    assert render_payload["attempts"][-1]["quality"]["source_classification_association"]["status"] == "unproven"
    render_payload["attempts"].append({"receipt": seventh.receipt})
    with pytest.raises(AuthoredStageError) as rejection:
        asyncio.run(measured_failure(1))
    assert rejection.value.code == "svg_source_classification_missing"
    render_payload["attempts"].append({"receipt": seventh.receipt})
    async def measured_geometry_failure():
        return await attempt_render(ctx=render_ctx, request=SimpleNamespace(instructions=None, aspect_ratio="16:9"),
            presentation_id="presentation", index=1, content=diagram, payload=render_payload,
            svg=legend_svg, ordinal=1, job=None, checkpoint=save_render,
            async_session_maker=None, write_png=lambda _: {})
    with pytest.raises(AuthoredStageError) as geometry_rejection:
        asyncio.run(measured_geometry_failure())
    assert geometry_rejection.value.code == "svg_source_quality_failed"
    assert render_payload["quality"]["source_classification_association"]["status"] == "proven"
    assert render_payload["quality"]["passed"] is False
    render_ctx.snapshot = _reference_style(6)
    assert asyncio.run(measured_geometry_failure())[0] is render_payload

    from copy import deepcopy
    from services.generation_core.drivers.svg import collect as collect_module
    from services.svg_classification_remeasurement import (
        REMEASUREMENT_SCHEMA, SVG_CLASSIFICATION_REMEASUREMENT,
        author_witness_digest, remeasurement_digest,
    )
    from utils.hashing import sha256_hex

    async def passing_render(svg, **kwargs):
        quality = measured(svg)
        quality.update(geometry_passed=True, png_digest=sha256_hex(b"png"))
        return SimpleNamespace(quality=quality, png=b"png")

    monkeypatch.setattr(renderer_module, "render_svg_with_metrics", passing_render)
    monkeypatch.setattr(collect_module, "_attempt_loop", lambda *args, **kwargs: pytest.fail("cache-only entered author loop"))

    def cache_case(count):
        reason = "classification_legend_paint" if count == 4 else "native_single_series_title_unit"
        snapshot = deepcopy(frozen_seven)
        context = SimpleNamespace(snapshot=snapshot, source_reflow=False, raster_reconstruction=False,
            canonical_assets=False, count=count, manifests=[{} for _ in range(count)],
            semaphore=asyncio.Semaphore(1), render_semaphore=asyncio.Semaphore(1),
            canvas=SimpleNamespace(width_px=1280, height_px=720), physical_kwargs={})
        request = SimpleNamespace(instructions=None, aspect_ratio="16:9", codex_generation_preset=None)
        cached, witnesses, writes = {}, [], []
        for index in range(count):
            digest = remeasurement_digest({"content": diagram, "style": snapshot, "index": index,
                "count": count, "instructions": None, "language": "ko", "preset": None})
            digest = remeasurement_digest({"page": digest, "assets": {}})
            quality = measured(legend_svg)
            quality.update(geometry_passed=True, png_digest=sha256_hex(b"png"))
            if index == 1:
                quality.update(passed=False, verified=False, failures=[{"kind": "source_classification_unproven"
                    if count == 4 else "native_requested_chart_missing"}])
            saved = {"svg_content": legend_svg, "input_digest": digest, "png_sha256": sha256_hex(b"png"),
                "quality": quality, "attempts": [{"kind": "model", "raw_response": legend_svg, "receipt": {}} for _ in range(2)]}
            cached[index] = saved
            witnesses.append({"slide_index": index, "source_sha256": sha256_hex(legend_svg.encode()),
                "png_sha256": saved["png_sha256"], "input_digest": digest,
                "author_receipt_digest": author_witness_digest(saved),
                "browser_metrics_digest": remeasurement_digest(quality["browser_metrics"])})
        manifest = {"operation_id": "old-operation", "style_digest": remeasurement_digest(snapshot),
            "reference_digest": remeasurement_digest(snapshot.get("references")), "target_indices": [1],
            "max_additional_author_calls": 0, "source_and_png_must_remain_identical": True,
            "expected_page_count": count, "recovery_reason": reason, "pages": witnesses}
        target = cached[1]
        target["classification_remeasurement_archive"] = {"checkpoint_row": {"svg_checkpoint": deepcopy(target)},
            "approval_manifest": manifest, "approval_manifest_hash": remeasurement_digest(manifest)}
        target["cache_only_remeasurement"] = {"schema": REMEASUREMENT_SCHEMA, "revision": SVG_CLASSIFICATION_REMEASUREMENT,
            "operation_id": "retry-operation", "source_operation_id": "old-operation", **witnesses[1],
            "style_digest": manifest["style_digest"], "reference_digest": manifest["reference_digest"],
            "approval_manifest_hash": remeasurement_digest(manifest), "status": "pending",
            "expected_page_count": count, "recovery_reason": reason}
        async def checkpoint(job, index, *, payload=None):
            if payload is not None:
                writes.append(index)
                cached[index] = deepcopy(payload)
            return deepcopy(cached[index])
        hooks = collect_module._PageHooks(checkpoint, None,
            lambda saved: b"png" if saved.get("png_sha256") == sha256_hex(b"png") else None,
            lambda png: {"png_sha256": sha256_hex(png)})
        job = SimpleNamespace(workspace_operation_id="retry-operation")
        state = collect_module._RunState(cache_only_approval=collect_module._cache_remeasurement_approval(context, job, target))
        outline = SimpleNamespace(slides=[SimpleNamespace(content=diagram) for _ in range(count)])
        return context, request, outline, job, state, hooks, cached, writes

    for count in (2, 4):
        context, request, outline, job, state, hooks, cached, writes = cache_case(count)
        original = deepcopy(cached[1])
        result = asyncio.run(collect_module._page(context, request, "presentation", outline, "ko", job, 1, state, hooks))
        assert result[0]["cache_only_remeasurement"]["status"] == "completed"
        assert result[0]["quality"]["source_classification_association"]["status"] == "proven"
        assert result[0]["classification_remeasurement_archive"] == original["classification_remeasurement_archive"]
        assert result[0]["svg_content"] == original["svg_content"]
        assert author_witness_digest(result[0]) == author_witness_digest(original)
        assert len(result[0]["attempts"]) == 2
        assert writes == [1]
        writes.clear()
        assert asyncio.run(collect_module._page(context, request, "presentation", outline, "ko", job, 1, state, hooks))[1] == b"png"
        assert writes == []
        cached[0]["png_sha256"] = "0" * 64
        with pytest.raises(AuthoredStageError):
            asyncio.run(collect_module._page(context, request, "presentation", outline, "ko", job, 0, state, hooks))
        assert writes == []

    context, request, outline, job, state, hooks, cached, writes = cache_case(4)
    monkeypatch.setattr(contract_module, "finalize_svg", lambda svg, **kwargs: svg + " ")
    with pytest.raises(AuthoredStageError) as immutable_rejection:
        asyncio.run(collect_module._page(context, request, "presentation", outline, "ko", job, 1, state, hooks))
    assert immutable_rejection.value.code == "svg_cache_remeasurement_changed"
    assert cached[1]["svg_content"] == legend_svg
    assert cached[1]["cache_only_remeasurement"]["status"] == "failed"
    with pytest.raises(AuthoredStageError):
        asyncio.run(collect_module._page(context, request, "presentation", outline, "ko", job, 1, state, hooks))

    context, request, outline, job, state, hooks, cached, writes = cache_case(4)
    async def forbidden_retry(**kwargs):
        return collect_module.render.RETRY
    monkeypatch.setattr(collect_module.render, "attempt_render", forbidden_retry)
    with pytest.raises(AuthoredStageError) as retry_rejection:
        asyncio.run(collect_module._page(context, request, "presentation", outline, "ko", job, 1, state, hooks))
    assert retry_rejection.value.code == "svg_cache_remeasurement_retry_forbidden"
    assert cached[1]["cache_only_remeasurement"]["status"] == "failed"
    assert cached[1]["quality"]["passed"] is False

    from services.generation_core.drivers.svg.source_paint_occlusion import apply_source_paint_occlusion
    from services.svg_contract import source_digest as measured_source_digest

    def painted_quality(markup, ink_box, reliable=True):
        quality = measured(markup)
        quality.update(source_digest=measured_source_digest(markup), wrapper_digest=measured_source_digest(markup), geometry_passed=True)
        quality["browser_metrics"]["source_digest"] = measured_source_digest(markup)
        for item in quality["browser_metrics"]["elements"]:
            if item["tag"] == "text":
                item["bbox"] = dict(ink_box)
                item["text_ink_fragments"] = [{**ink_box, "cellClusters": 0,
                    "glyphInkClusters": [{**ink_box, "index": 0, "length": 1,
                        "basis": "isolated-matching-advance"}] if reliable else []}]
        return quality

    def painted_svg(markup):
        return '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">' + markup + '</svg>'

    label = '<text x="55" y="68" fill="#000000">A</text>'
    later_rectangle = '<rect x="50" y="40" width="40" height="40" rx="4" fill="#002060"/>'
    label_ink = {"x": 55, "y": 48, "width": 12, "height": 20}
    paint_svg = painted_svg(label + later_rectangle)
    hidden = painted_quality(paint_svg, label_ink)
    apply_source_paint_occlusion(hidden, paint_svg)
    assert hidden["source_paint_occlusion"]["status"] == "occluded"
    assert hidden["source_paint_occlusion"]["collisions"][0]["text_qid"] == "svg-1"
    assert hidden["geometry_passed"] is False
    background_svg = painted_svg(later_rectangle + label)
    clear = painted_quality(background_svg, label_ink)
    apply_source_paint_occlusion(clear, background_svg)
    assert clear["passed"] is True
    assert clear["source_paint_occlusion"]["candidate_pairs"] == 0
    corner_svg = painted_svg('<text x="1" y="6" fill="#000000">A</text>'
        '<rect width="100" height="100" rx="30" fill="#002060"/>')
    outside_corner = painted_quality(corner_svg, {"x": 1, "y": 1, "width": 5, "height": 5})
    apply_source_paint_occlusion(outside_corner, corner_svg)
    assert outside_corner["passed"] is True
    assert not outside_corner["source_paint_occlusion"]["collisions"]
    unknown = painted_quality(paint_svg, label_ink, reliable=False)
    apply_source_paint_occlusion(unknown, paint_svg)
    assert unknown["passed"] is False
    assert unknown["source_paint_occlusion"]["status"] == "unproven"
    assert not unknown["source_paint_occlusion"]["collisions"]
    assert unknown["geometry_passed"] is True
    absent_ink = painted_quality(paint_svg, label_ink)
    next(item for item in absent_ink["browser_metrics"]["elements"]
         if item["tag"] == "text")["text_ink_fragments"] = []
    apply_source_paint_occlusion(absent_ink, paint_svg)
    assert absent_ink["source_paint_occlusion"]["status"] == "unproven"
    assert absent_ink["passed"] is False
    assert absent_ink["geometry_passed"] is True
    assert not absent_ink["source_paint_occlusion"]["collisions"]
    missing_visibility = painted_quality(paint_svg, label_ink)
    for item in missing_visibility["browser_metrics"]["elements"]:
        item.pop("css_visible", None)
        item.pop("effective_opacity", None)
    apply_source_paint_occlusion(missing_visibility, paint_svg)
    assert missing_visibility["source_paint_occlusion"]["status"] == "unproven"
    assert missing_visibility["passed"] is False
    assert not missing_visibility["source_paint_occlusion"]["collisions"]
    partial = painted_quality(paint_svg, {"x": 45, "y": 48, "width": 12, "height": 20})
    apply_source_paint_occlusion(partial, paint_svg)
    assert partial["source_paint_occlusion"]["status"] == "unproven"
    assert not partial["source_paint_occlusion"]["collisions"]
    assert partial["geometry_passed"] is True
    ambiguous_svg = paint_svg.replace('rx="4"', 'rx="4" transform="rotate(1)"')
    ambiguous = painted_quality(ambiguous_svg, label_ink)
    apply_source_paint_occlusion(ambiguous, ambiguous_svg)
    assert ambiguous["source_paint_occlusion"]["status"] == "unproven"
    assert not ambiguous["source_paint_occlusion"]["collisions"]

    async def rendered_paint_fixture(svg, **kwargs):
        assert kwargs.get("timeline_ink_probe") is True
        quality = painted_quality(svg, label_ink)
        quality["body_font_floor_px"] = 16.256
        return SimpleNamespace(quality=quality, png=b"png")
    monkeypatch.setattr(renderer_module, "render_svg_with_metrics", rendered_paint_fixture)
    render_ctx.snapshot = frozen_seven
    paint_payload = {"attempts": [{"receipt": seventh.receipt}]}
    async def measured_paint_failure(ordinal):
        return await attempt_render(ctx=render_ctx, request=SimpleNamespace(instructions=None, aspect_ratio="16:9"),
            presentation_id="presentation", index=1, content="", payload=paint_payload,
            svg=paint_svg, ordinal=ordinal, job=None, checkpoint=save_render,
            async_session_maker=None, write_png=lambda _: {})
    assert asyncio.run(measured_paint_failure(0)) is RETRY
    paint_hint = json.loads(paint_payload["repair_hint"])
    assert paint_hint["kind"] == "svg_source_paint_occluded"
    assert paint_hint["safe_body_px"] == 17
    assert paint_hint["witnesses"][0]["text"] == "A"
    assert paint_hint["witnesses"][0]["paint_box"] == {"x": 50, "y": 40, "width": 40, "height": 40}
    assert len(paint_payload["repair_hint"]) <= 1500
    paint_payload["attempts"].append({"receipt": seventh.receipt})
    with pytest.raises(AuthoredStageError) as paint_rejection:
        asyncio.run(measured_paint_failure(1))
    assert paint_rejection.value.code == "svg_source_paint_occluded"
