import asyncio
from types import SimpleNamespace
import uuid

import pytest

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services import authored_deck_plans
from services.authored_generation_performance import authored_provider_concurrency
from services import slidespec_authored_executor as executor
from utils.llm_calls.author_slide import (
    AUTHOR_MAX_TOKENS,
    Brand,
    EXACT_RECONSTRUCTION,
    _authoring_output_profile,
)
from utils.llm_calls import author_slide as author_slide_mod


def test_provider_cap_and_role_profiles_preserve_strict_authoring(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_PROVIDER_CONCURRENCY", "20")
    monkeypatch.setenv("AUTHORED_PROVIDER_OPENAI_CONCURRENCY", "6")
    assert authored_provider_concurrency("openai") == 6
    assert authored_provider_concurrency("anthropic") == 8

    assert _authoring_output_profile(
        role="COVER",
        reference_mode="style_reference",
        structured_context=None,
        correction_hint=None,
    ) == (10_000, "minimal")
    assert _authoring_output_profile(
        role="CLOSING",
        reference_mode=EXACT_RECONSTRUCTION,
        structured_context=None,
        correction_hint=None,
    ) == (AUTHOR_MAX_TOKENS, "low")
    assert _authoring_output_profile(
        role="SECTION",
        reference_mode="style_reference",
        structured_context={"zones": []},
        correction_hint=None,
    ) == (AUTHOR_MAX_TOKENS, "low")
    assert _authoring_output_profile(
        role="AGENDA",
        reference_mode="style_reference",
        structured_context=None,
        correction_hint="repair",
    ) == (AUTHOR_MAX_TOKENS, "low")


def test_codex_model_reasoning_capability_normalizes_once_at_author_egress(
    monkeypatch, tmp_path
) -> None:
    calls = []
    receipts = []

    class FakeClient:
        def generate(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("provider unavailable")

    async def no_rate_limit() -> None:
        return None

    async def record_receipt(**kwargs) -> None:
        receipts.append(kwargs)

    monkeypatch.setattr(author_slide_mod, "is_codex_selected", lambda: True)
    assert author_slide_mod._authoring_extra_body(
        "minimal", model="gpt-5.6-sol"
    ) == {"reasoning": {"effort": "none"}}
    assert author_slide_mod._authoring_extra_body(
        "minimal", model="gpt-5.6-terra"
    ) == {"reasoning": {"effort": "minimal"}}

    monkeypatch.setattr(author_slide_mod, "get_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(author_slide_mod, "get_llm_config", lambda: {})
    monkeypatch.setattr(author_slide_mod, "get_model", lambda: "gpt-5.6-sol")
    monkeypatch.setattr(author_slide_mod, "get_llm_provider", lambda: SimpleNamespace(value="codex"))
    monkeypatch.setattr(author_slide_mod, "get_generate_kwargs", lambda **kwargs: kwargs)
    monkeypatch.setattr(author_slide_mod, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(author_slide_mod, "_wait_for_author_rate_slot", no_rate_limit)
    monkeypatch.setattr(author_slide_mod, "record_provider_call", record_receipt)
    monkeypatch.setattr(author_slide_mod, "handle_llm_client_exceptions", lambda error: error)
    reference_image = tmp_path / "reference.png"
    reference_image.write_bytes(b"\x89PNG\r\n\x1a\nreference")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(
            author_slide_mod.author_slide_html(
                "content",
                "DESIGN",
                Brand(topic="topic", language="English", primary="#2563EB", fonts="Arial"),
                "COVER",
                0,
                1,
                reference_images=[reference_image],
                allow_provider_fallback=False,
                receipt_retry_class="recovery",
                receipt_attempt_ordinal=2,
            )
        )

    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 10_000
    assert calls[0]["extra_body"] == {"reasoning": {"effort": "none"}}
    assert len(receipts) == 1
    assert receipts[0]["retry_class"] == "recovery"


def test_final_outline_illustration_resolver_is_separate_from_outline_expansion(
    monkeypatch,
) -> None:
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="one")])
    request = SimpleNamespace(slides_markdown="fixed", instructions="keep it")
    style = SimpleNamespace(reference_mode="style_reference", illustration_prompt="art")
    sentinel = object()

    async def fake_illustration(received_outline, received_brand, received_style, **kwargs):
        assert received_outline is outline
        assert received_brand is sentinel
        assert received_style is style
        assert kwargs["instructions"] == "keep it"
        return "illustration-plan"

    monkeypatch.setattr(
        authored_deck_plans, "resolve_illustration_plan", fake_illustration
    )
    plans = asyncio.run(
        authored_deck_plans.resolve_deck_plans(
            request, outline, sentinel, style, ["CONTENT"], resolve_illustration=False
        )
    )
    assert plans.outline is outline
    assert plans.illustration_plan is None
    assert asyncio.run(
        authored_deck_plans.resolve_final_outline_illustration_plan(
            request, plans.outline, sentinel, style
        )
    ) == "illustration-plan"


def test_baseline_cache_key_includes_renderer_full_identity(monkeypatch) -> None:
    request = SimpleNamespace(
        source=None,
        spec=SimpleNamespace(
            specRevision=1,
            digests=SimpleNamespace(spec="spec", content="content"),
        ),
        fonts=SimpleNamespace(model_dump=lambda **_kwargs: {"font": "A"}),
        renderer=SimpleNamespace(model_dump=lambda **_kwargs: {"renderer": "A"}),
    )
    identity = SimpleNamespace(digest="render-identity-a")
    monkeypatch.setattr(executor, "_render_cache_identity", lambda _html: identity)
    first = executor._baseline_inspection_cache_key(
        slide_id=uuid.uuid4(), request=request, html="<main />", adjacent_digest="adj"
    )
    identity.digest = "render-identity-b"
    second = executor._baseline_inspection_cache_key(
        slide_id=uuid.uuid4(), request=request, html="<main />", adjacent_digest="adj"
    )
    assert first != second


def test_publication_reuse_fingerprint_is_attempt_scoped_and_manifest_bound() -> None:
    artifact = SimpleNamespace(
        render_input_hash="input-a",
        html_hash="html-a",
        render_hash="render-a",
        image_hash="image-a",
        manifest={"assets": "a", "renderer": "r"},
    )
    first_attempt = uuid.uuid4()
    first = executor._attempt_immutable_artifact_fingerprint(
        attempt_id=first_attempt, artifact=artifact
    )
    assert first == executor._attempt_immutable_artifact_fingerprint(
        attempt_id=first_attempt, artifact=artifact
    )
    assert first != executor._attempt_immutable_artifact_fingerprint(
        attempt_id=uuid.uuid4(), artifact=artifact
    )
    artifact.manifest = {"assets": "changed", "renderer": "r"}
    assert first != executor._attempt_immutable_artifact_fingerprint(
        attempt_id=first_attempt, artifact=artifact
    )
