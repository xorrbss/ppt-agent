"""svg author prompt caching: revision nine's deck-stable system block.

Revision nine of the svg authoring profile sends the rules and design JSON as
one system block that is byte-identical for every page and repair of a deck,
and moves the per-call correction rules into the user message. On Anthropic
that block is a prompt-cache breakpoint (``cache_control`` on the wire); the
author call runs on an executor thread, which is what these tests drive.
Decks frozen with revision eight keep their original prompt bytes, pinned
below as digests captured from the code before revision nine existed.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
from types import SimpleNamespace

import httpx
from anthropic import Anthropic
from llmai.shared import AnthropicClientConfig, ImageContentPart
from PIL import Image

import utils.llm_config  # noqa: F401  (installs the repository Anthropic client)
from enums.llm_provider import LLMProvider
from services.generation_core.authoring import author_slide_svg as module
from services.ppt_writing_policy import WRITING_POLICY_ID
from services.svg_authoring_profile import (
    ART_DIRECTION_PROFILE_V8, QUALITY_RECOVERY_POLICY_V2, _axis_review_plan,
)
from utils.anthropic_structured_output import RepositoryAnthropicClient
from utils.hashing import canonical_json_digest, sha256_prefixed

SLIDE_COUNT = 3
MODEL = "claude-sonnet-5-5"
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
       '<text x="40" y="80" font-family="Pretendard" font-size="32" fill="#111111">매출 현황</text></svg>')
CORRECTION = "text_overlap: the title and the first body line collide at x=40..420, y=60..96"
DESIGN_CORRECTION = "hierarchy_emphasis failed: the key figure has no visual weight"
PAGES = {1: "# 매출 현황\n- 1분기 매출 30억원\n- 2분기 매출 42억원",
         2: "# 운영 계획\n1. 데이터 수집\n2. 모델 검증\n3. 단계별 확산"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), (240, 240, 240)).save(buffer, format="PNG")
    return buffer.getvalue()


PREVIOUS_PNG = _png()


def _snapshot(profile, *, quality_policy: bool = True) -> dict:
    snapshot = {
        "contract_version": module.SVG_CONTRACT_VERSION, "style_id": "fixture", "references": {},
        "canvas": {"width": 1280, "height": 720},
        "tokens": {"primary": "#1E5EFF", "background": "#FFFFFF",
                   "heading_font": "Pretendard", "body_font": "Pretendard"},
        "authoring_profile": dict(profile),
        "common_header_design_plan": {"schema": "svg-common-header-design-plan/v3",
                                      "seed": "509c092c71b66562813c27e70b49793e",
                                      "plan_digest": "sha256:" + "6" * 64},
    }
    if quality_policy:
        snapshot.update(quality_recovery_policy=dict(QUALITY_RECOVERY_POLICY_V2),
                        quality_recovery_plan=_axis_review_plan(SLIDE_COUNT))
    snapshot["digest"] = canonical_json_digest({k: v for k, v in snapshot.items() if k != "digest"},
                                               allow_nan=True)
    return snapshot


class _Wire:
    """A stubbed Anthropic HTTP transport that keeps every request body."""

    def __init__(self):
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": SVG}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 7}})


def _client(wire: _Wire) -> RepositoryAnthropicClient:
    client = RepositoryAnthropicClient(config=AnthropicClientConfig(api_key="test-key"))
    client._client = Anthropic(api_key="test-key", max_retries=0, timeout=60.0,
                               http_client=httpx.Client(transport=httpx.MockTransport(wire)))
    return client


def _stub(monkeypatch, provider: LLMProvider, wire: _Wire, seen: list):
    """Everything around the provider call is stubbed; the call itself is real."""
    client = _client(wire)
    monkeypatch.setattr(module, "get_client", lambda **_: client)
    monkeypatch.setattr(module, "get_llm_config", lambda: None)
    monkeypatch.setattr(module, "get_model", lambda: MODEL)
    monkeypatch.setattr(module, "authoring_model", lambda model: model)
    monkeypatch.setattr(module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(module, "_authoring_output_profile", lambda **_: (4096, None))
    monkeypatch.setattr(module, "_authoring_extra_body", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "_apply_anthropic_authoring_reasoning", lambda kwargs, *_a, **_k: kwargs)

    def kwargs(*, model, messages, max_tokens):
        seen.append(list(messages))
        return {"model": model, "messages": list(messages), "max_tokens": max_tokens}

    monkeypatch.setattr(module, "get_generate_kwargs", kwargs)

    async def no_wait():
        return None

    async def record(**_kwargs):
        return None

    monkeypatch.setattr(module, "_wait_for_author_rate_slot", no_wait)
    monkeypatch.setattr(module, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(module, "supports_provider_idempotency", lambda *_a, **_k: False)
    monkeypatch.setattr(module, "provider_call_admission", lambda **_k: contextlib.nullcontext())
    # The client's own generate runs on the author executor thread, as in production.
    monkeypatch.setattr(module, "provider_generate_call",
                        lambda client, *, kwargs, **_k: (lambda: client.generate(**kwargs)))
    monkeypatch.setattr(module, "record_provider_call", record)
    monkeypatch.setattr("services.document_canvas_generation.generation_font_floor", lambda *_a: 18)


def _scenarios():
    """One deck's calls: two first drafts, a local repair and a design rewrite, per writing mode."""
    repair = dict(correction_hint=CORRECTION, correction_mode="local_repair", previous_svg=SVG)
    rewrite = dict(correction_hint=DESIGN_CORRECTION, correction_mode="design_rewrite", previous_svg=SVG,
                   previous_render_png=PREVIOUS_PNG, previous_render_png_digest=sha256_prefixed(PREVIOUS_PNG))
    new_copy = dict(writing_policy=WRITING_POLICY_ID, language="Korean")
    # A deck without the quality recovery policy repairs from the hint alone.
    plain_repair = dict(correction_hint=CORRECTION, previous_svg=SVG)
    return {
        "page1": (True, 1, {}), "page2": (True, 2, {}), "page1_repair": (True, 1, repair),
        "page1_rewrite": (True, 1, rewrite),
        "new_copy_page1": (True, 1, new_copy), "new_copy_page2": (True, 2, new_copy),
        "new_copy_page1_repair": (True, 1, {**new_copy, **repair}),
        "no_policy_page1": (False, 1, {}), "no_policy_page2": (False, 2, {}),
        "no_policy_page1_repair": (False, 1, plain_repair),
    }


def _run(monkeypatch, profile, provider=LLMProvider.ANTHROPIC):
    wire, seen, results = _Wire(), [], {}
    _stub(monkeypatch, provider, wire, seen)
    for name, (quality_policy, index, kwargs) in _scenarios().items():
        snapshot = _snapshot(profile, quality_policy=quality_policy)
        result = asyncio.run(module.author_slide_svg(PAGES[index], snapshot, "content", index, SLIDE_COUNT,
                                                     **kwargs))
        results[name] = (seen[-1], result.receipt)
    return results, wire


def _messages_digest(messages) -> str:
    def part(item):
        if isinstance(item, str):
            return {"text": item}
        if isinstance(item, ImageContentPart):
            return {"image": sha256_prefixed(item.data)}
        return {"text": item.text}

    return canonical_json_digest([{"role": type(message).__name__,
                                   "content": [part(item) for item in ([message.content]
                                               if isinstance(message.content, str) else message.content)]}
                                  for message in messages])


# Captured from origin/main before revision nine: a revision-eight deck must keep these bytes.
REVISION_EIGHT_PROMPT_DIGESTS = {
    "page1": "sha256:e1bac91bd4c0e997db7c29d275db217607c03d25623a322055d46eb3d7ba1b86",
    "page2": "sha256:7779d40228cbedc7fe9715911c2076f8ac22990d2c41c1e3283a55d90d068735",
    "page1_repair": "sha256:8d683a6bab38f33add461aefc606c09a7cd61fc0f7a8261d46112bd58494d606",
    "page1_rewrite": "sha256:536ed40b917f42df3c3df8ad52bd26a61562da2cd6ff8536d6fdefcb66f7d338",
    "new_copy_page1": "sha256:7ae30ba7462f61c9df00751f364e280b11c26349c6bf8d69d1a0792fd8407457",
    "new_copy_page2": "sha256:4a6b6954bc14922b22135851de40466e2c0e92c5bf0706d7563e23d56729612d",
    "new_copy_page1_repair": "sha256:ba78bc31e13d5ee3d00b8c1fb0362e0129fcbbf0fa8d9f427194f71319c98972",
    "no_policy_page1": "sha256:0c476606e0dbe253be4333b8cc20ca0a0ca49390eb180b1ecc97ff469ac40d61",
    "no_policy_page2": "sha256:3b5e3bfda324f9ee6e9b8278769e808c0eb482bba1687fecc57e6ac9da4b91ff",
    "no_policy_page1_repair": "sha256:a495686fdfd7739413f426be547ba9806781a387d5668042f9501c75a95f68f2",
}


def test_revision_eight_decks_keep_their_original_prompt_bytes(monkeypatch):
    results, wire = _run(monkeypatch, ART_DIRECTION_PROFILE_V8)
    assert {name: _messages_digest(messages) for name, (messages, _) in results.items()} \
        == REVISION_EIGHT_PROMPT_DIGESTS
    # Revision eight sends no cache breakpoint: its system prompt stays a plain string.
    assert all(isinstance(body["system"], str) for body in wire.bodies)


def test_revision_nine_system_block_is_deck_stable_and_cached_on_anthropic(monkeypatch):
    from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V9

    results, wire = _run(monkeypatch, ART_DIRECTION_PROFILE_V9)
    systems = {name: messages[0].content for name, (messages, _) in results.items()}
    source_preserving = {systems[name] for name in ("page1", "page2", "page1_repair", "page1_rewrite")}
    new_copy = {systems[name] for name in ("new_copy_page1", "new_copy_page2", "new_copy_page1_repair")}
    no_policy = {systems[name] for name in ("no_policy_page1", "no_policy_page2", "no_policy_page1_repair")}
    # Two pages, a local repair and a design rewrite share one byte-identical system block.
    assert len(source_preserving) == 1 and len(new_copy) == 1 and len(no_policy) == 1
    system = source_preserving.pop()
    design = module.svg_design_text(_snapshot(ART_DIRECTION_PROFILE_V9))
    assert system.endswith(module.BLOCK_SEPARATOR + design)
    assert "quality_recovery_plan" not in design and "common_header_design_plan" not in design
    assert "art_direction" in json.loads(design)
    for _, receipt in results.values():
        assert receipt["authoring_profile"] == ART_DIRECTION_PROFILE_V9
    assert len({receipt["prefix_digest"] for name, (_, receipt) in results.items()
                if not name.startswith(("new_copy", "no_policy"))}) == 1

    # The per-call rules travel in the user message, after the cached block.
    local = module.SVG_QUALITY_FIRST_CORRECTION_RULES["local_repair"].strip()
    rewrite = module.SVG_QUALITY_FIRST_CORRECTION_RULES["design_rewrite"].strip()
    assert local not in system and rewrite not in system
    for name, rule in (("page1_repair", local), ("page1_rewrite", rewrite)):
        user = results[name][0][1].content
        text = user if isinstance(user, str) else user[0].text
        assert text.startswith(module.SVG_CALL_RULES_HEADING) and rule in text
    first = results["page1"][0][1].content
    assert not (first if isinstance(first, str) else first[0].text).startswith(module.SVG_CALL_RULES_HEADING)

    # On the wire the deck-stable block carries the breakpoint; nothing else does.
    assert len(wire.bodies) == len(results)
    for body, (messages, _) in zip(wire.bodies, results.values(), strict=True):
        assert body["system"] == [{"type": "text", "text": messages[0].content,
                                   "cache_control": {"type": "ephemeral"}}]
        assert "cache_control" not in json.dumps(body["messages"])


def test_revision_nine_rules_keep_every_revision_eight_rule(monkeypatch):
    """The split only moves rules: system plus per-call text holds every V8 rule sentence."""
    from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V9

    eight, _ = _run(monkeypatch, ART_DIRECTION_PROFILE_V8)
    nine, _ = _run(monkeypatch, ART_DIRECTION_PROFILE_V9)
    for name in _scenarios():
        old_rules = eight[name][0][0].content
        old_rules = old_rules[:old_rules.rindex(module.BLOCK_SEPARATOR)]
        new_messages = nine[name][0]
        user = new_messages[1].content
        new_text = new_messages[0].content + (user if isinstance(user, str) else user[0].text)
        for line in filter(None, (line.strip() for line in old_rules.splitlines())):
            assert line in new_text, (name, line)


def test_other_providers_get_the_same_request_without_a_cache_breakpoint(monkeypatch):
    from services.svg_authoring_profile import ART_DIRECTION_PROFILE_V9
    from utils import anthropic_structured_output

    marks = []
    original = anthropic_structured_output.RepositoryAnthropicClient._get_system_prompt

    def spy(self, messages):
        marks.append(anthropic_structured_output._CACHE_SYSTEM_PROMPT.get())
        return original(self, messages)

    monkeypatch.setattr(anthropic_structured_output.RepositoryAnthropicClient, "_get_system_prompt", spy)
    anthropic, _ = _run(monkeypatch, ART_DIRECTION_PROFILE_V9)
    assert marks and all(marks)
    marks.clear()
    other, _ = _run(monkeypatch, ART_DIRECTION_PROFILE_V9, provider=LLMProvider.OPENAI)
    assert marks and not any(marks)
    assert ({name: _messages_digest(messages) for name, (messages, _) in anthropic.items()}
            == {name: _messages_digest(messages) for name, (messages, _) in other.items()})


def test_cached_system_prompt_wrapper_marks_only_the_wrapped_call():
    from llmai.shared import SystemMessage, UserMessage

    from utils.anthropic_structured_output import with_cached_system_prompt

    client = RepositoryAnthropicClient.__new__(RepositoryAnthropicClient)
    messages = [SystemMessage(content="deck rules"), UserMessage(content="page")]
    loop = asyncio.new_event_loop()
    try:
        marked = loop.run_until_complete(loop.run_in_executor(
            None, with_cached_system_prompt(lambda: client._get_system_prompt(messages))))
        plain = loop.run_until_complete(loop.run_in_executor(None, lambda: client._get_system_prompt(messages)))
    finally:
        loop.close()
    assert marked == [{"type": "text", "text": "deck rules", "cache_control": {"type": "ephemeral"}}]
    assert plain == "deck rules"


def test_fresh_admission_freezes_revision_nine_and_revision_eight_still_validates():
    from services.svg_authoring_profile import (
        ART_DIRECTION_PROFILE_V9, behaviour_profile, new_svg_admission_profile, read_quality_recovery_policy,
        validate_authoring_profile,
    )

    assert new_svg_admission_profile(SimpleNamespace()) == ART_DIRECTION_PROFILE_V9
    assert validate_authoring_profile("art-direction-v1@9") == ART_DIRECTION_PROFILE_V9
    assert validate_authoring_profile("art-direction-v1@8") == ART_DIRECTION_PROFILE_V8
    for profile in (ART_DIRECTION_PROFILE_V8, ART_DIRECTION_PROFILE_V9):
        assert read_quality_recovery_policy(_snapshot(profile)) == QUALITY_RECOVERY_POLICY_V2
        assert behaviour_profile(profile) == ART_DIRECTION_PROFILE_V8


def test_vision_design_review_marks_its_system_prompt_on_anthropic_only(monkeypatch):
    from services.generation_core.quality import vision_advisory
    from utils import anthropic_structured_output

    marks = []

    class FakeCall:
        terminal_outcome = None
        receipt = {}

        def __call__(self):
            marks.append(anthropic_structured_output._CACHE_SYSTEM_PROMPT.get())
            return SimpleNamespace(content=json.dumps({"slides": []}))

    monkeypatch.setattr("utils.provider_idempotency.provider_generate_call", lambda *_a, **_k: FakeCall())
    monkeypatch.setattr("utils.llm_config.get_client", lambda **_k: object())
    monkeypatch.setattr("utils.llm_config.get_llm_config", lambda: None)
    monkeypatch.setattr("utils.llm_config.enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr("utils.llm_provider.get_model", lambda: MODEL)
    monkeypatch.setattr("utils.llm_utils.get_generate_kwargs", lambda **kwargs: dict(kwargs))
    monkeypatch.setattr(vision_advisory, "_advisory_extra_body", lambda: None)
    monkeypatch.setattr(vision_advisory, "_check_design_input", lambda *_a, **_k: None)
    monkeypatch.setattr(vision_advisory, "build_advisory_messages", lambda *_a, **_k: [])
    monkeypatch.setattr(vision_advisory, "design_review_json_schema", lambda **_k: {"type": "object"})

    async def record(**_kwargs):
        return None

    monkeypatch.setattr("services.provider_usage_instrumentation.record_provider_call", record)
    monkeypatch.setattr("services.provider_usage_instrumentation.provider_call_admission",
                        lambda **_k: contextlib.nullcontext())
    for provider, expected in ((LLMProvider.ANTHROPIC, True), (LLMProvider.OPENAI, False)):
        monkeypatch.setattr("utils.llm_provider.get_llm_provider", lambda provider=provider: provider)
        asyncio.run(vision_advisory._request_advisory(b"sheet", slide_count=1, pngs=[b"png"],
                                                      design_context={"reviewScope": "local"}))
        assert marks[-1] is expected
    # The authored (non-design) advisory path keeps its plain system prompt.
    asyncio.run(vision_advisory._request_advisory(b"sheet", slide_count=1))
    assert marks[-1] is False
