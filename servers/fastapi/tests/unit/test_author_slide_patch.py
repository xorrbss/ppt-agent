"""Model-facing contract tests for authored local patch proposals."""

import asyncio
import json

from utils.llm_calls import author_slide_patch as patch_mod

PNG = b"\x89PNG\r\n\x1a\nrendered-slide"
DOM = [
    {
        "qid": "title",
        "tag": "H1",
        "text": "Quarterly result",
        "bbox": {"x": 10, "y": 20, "width": 400, "height": 80, "evil": 1},
        "computedStyle": {
            "fontSize": "40px",
            "color": "rgb(1, 2, 3)",
            "backgroundImage": "url(https://attacker.test/x)",
        },
        "outerHTML": '<h1 onclick="steal()">Quarterly result</h1>',
    },
    {
        "qid": "hero",
        "tag": "img",
        "parentQid": "slide",
        "bbox": {"left": 600, "top": 100, "right": 1100, "bottom": 600},
        "computed_style": {"objectFit": "contain", "objectPosition": "center"},
    },
]


def _run(coro):
    return asyncio.run(coro)


def _install_fake_model(monkeypatch, response=None, error=None):
    calls = []

    async def fake_generate(client, model, **kwargs):
        calls.append((client, model, kwargs))
        if error is not None:
            raise error
        return response

    client = object()
    monkeypatch.setattr(patch_mod, "get_llm_config", lambda: "config")
    monkeypatch.setattr(
        patch_mod,
        "get_client",
        lambda *, config: client if config == "config" else None,
    )
    monkeypatch.setattr(patch_mod, "get_model", lambda: "patch-model")
    monkeypatch.setattr(
        patch_mod,
        "generate_structured_with_schema_retries",
        fake_generate,
    )
    return calls


def _install_fake_model_sequence(monkeypatch, responses):
    calls = []
    pending = iter(responses)

    async def fake_generate(client, model, **kwargs):
        calls.append((client, model, kwargs))
        return next(pending)

    monkeypatch.setattr(patch_mod, "get_llm_config", lambda: "config")
    monkeypatch.setattr(patch_mod, "get_client", lambda *, config: object())
    monkeypatch.setattr(patch_mod, "get_model", lambda: "patch-model")
    monkeypatch.setattr(
        patch_mod,
        "generate_structured_with_schema_retries",
        fake_generate,
    )
    return calls


def test_compact_dom_map_strips_raw_html_unknown_metrics_and_unsafe_styles():
    normalized, qids = patch_mod.compact_dom_map_for_prompt(DOM)

    assert qids == {"title", "hero"}
    assert normalized[0] == {
        "qid": "title",
        "tag": "h1",
        "text": "Quarterly result",
        "bbox": {"x": 10, "y": 20, "width": 400, "height": 80},
        "computedStyle": {"fontSize": "40px", "color": "rgb(1, 2, 3)"},
    }
    encoded = json.dumps(normalized)
    assert "outerHTML" not in encoded
    assert "backgroundImage" not in encoded
    assert "attacker.test" not in encoded


def test_prompt_is_multimodal_bounded_and_never_contains_raw_html():
    messages, qids = patch_mod.build_patch_messages(
        PNG,
        DOM,
        {"needs_fix": True, "issues": ["title clips"]},
    )

    assert qids == {"title", "hero"}
    assert "Never output HTML" in messages[0].content
    assert "computedStyle.position is not static" in messages[0].content
    content = messages[1].content
    assert len(content) == 2
    assert "compactDomMap" in content[0].text
    assert "outerHTML" not in content[0].text
    assert content[1].data == PNG
    assert content[1].mime_type == "image/png"


def test_valid_model_patch_is_returned_with_model_and_contract_metadata(monkeypatch):
    calls = _install_fake_model(
        monkeypatch,
        response={
            "strategy": "patch",
            "actions": [
                {"action": "setStyle", "qid": "title", "styles": {"font-size": "36px"}}
            ],
            "rationale": "remove title clipping",
        },
    )

    result = _run(
        patch_mod.propose_authored_slide_patch(
            PNG,
            DOM,
            {"needs_fix": True, "issues": ["title clips"]},
        )
    )

    assert result.ok is True
    assert result.plan is not None
    assert result.plan.actions[0].qid == "title"
    assert result.model == "patch-model"
    assert result.prompt_version == patch_mod.PATCH_PROMPT_VERSION
    assert len(calls) == 1
    assert calls[0][0:2] == (calls[0][0], "patch-model")
    assert calls[0][2]["validate_schema"] is True


def test_model_can_explicitly_request_full_reauthor(monkeypatch):
    _install_fake_model(
        monkeypatch,
        response={
            "strategy": "full_reauthor",
            "actions": [],
            "fallback_reason": "requires adding a missing chart",
        },
    )

    result = _run(patch_mod.propose_authored_slide_patch(PNG, DOM, "structural defect"))

    assert result.ok is False
    assert result.plan is not None
    assert result.plan.strategy == "full_reauthor"
    assert result.failure_reason == "requires adding a missing chart"
    assert result.fallback_reason == "model_requested_full_reauthor"


def test_invalid_model_css_fails_closed(monkeypatch):
    _install_fake_model(
        monkeypatch,
        response={
            "strategy": "patch",
            "actions": [
                {
                    "action": "setStyle",
                    "qid": "title",
                    "styles": {"background-color": "url(https://attacker.test/x)"},
                }
            ],
        },
    )

    result = _run(patch_mod.propose_authored_slide_patch(PNG, DOM, "fix"))

    assert result.ok is False
    assert result.plan is None
    assert result.fallback_reason == "invalid_model_patch"


def test_source_reflow_reproposes_invalid_patch_once_with_validator_feedback(
    monkeypatch,
):
    calls = _install_fake_model_sequence(
        monkeypatch,
        [
            {
                "strategy": "patch",
                "actions": [
                    {
                        "action": "setStyle",
                        "qid": "title",
                        "styles": {
                            "display": "none",
                            "borderTopColor": "rgb(23 58 99 / 20%)",
                        },
                    }
                ],
            },
            {
                "strategy": "patch",
                "actions": [
                    {
                        "action": "setStyle",
                        "qid": "title",
                        "styles": {
                            "display": "grid",
                            "borderTopColor": "#173A63",
                        },
                    }
                ],
            },
        ],
    )

    result = _run(
        patch_mod.propose_authored_slide_patch(
            PNG,
            DOM,
            "repair border collision",
            retry_invalid_model_patch=True,
        )
    )

    assert result.ok is True
    assert result.plan.actions[0].styles == {
        "display": "grid",
        "border-top-color": "#173A63",
    }
    assert len(calls) == 2
    assert calls[0][2].get("is_retry") is None
    assert calls[1][2]["is_retry"] is True
    retry_messages = calls[1][2]["messages"]
    feedback = retry_messages[-1].content[0].text
    assert "rejectedProposal" in feedback
    assert "unsupported CSS value 'none' for display" in feedback
    assert '"border-top-color"' in feedback
    assert '"validDisplayValues":["grid","flex"]' in feedback
    assert "display is permitted only as exact grid or flex" in feedback
    assert "integer channels 0..255" in feedback


def test_ordinary_patch_neither_retries_nor_allows_side_border_color(monkeypatch):
    calls = _install_fake_model(
        monkeypatch,
        response={
            "strategy": "patch",
            "actions": [
                {
                    "action": "setStyle",
                    "qid": "title",
                    "styles": {
                        "display": "flex",
                        "borderTopColor": "#173A63",
                    },
                }
            ],
        },
    )

    result = _run(patch_mod.propose_authored_slide_patch(PNG, DOM, "fix"))

    assert result.ok is False
    assert result.fallback_reason == "invalid_model_patch"
    assert len(calls) == 1


def test_unknown_model_target_fails_closed(monkeypatch):
    _install_fake_model(
        monkeypatch,
        response={
            "strategy": "patch",
            "actions": [{"action": "move", "qid": "not-in-map", "left": "5px"}],
        },
    )

    result = _run(patch_mod.propose_authored_slide_patch(PNG, DOM, "fix"))

    assert result.ok is False
    assert result.fallback_reason == "target_not_in_dom_map"


def test_exact_reconstruction_rejects_model_text_patch(monkeypatch):
    _install_fake_model(
        monkeypatch,
        response={
            "strategy": "patch",
            "actions": [{"action": "setText", "qid": "title", "text": "different"}],
        },
    )

    result = _run(
        patch_mod.propose_authored_slide_patch(
            PNG,
            DOM,
            "text mismatch",
            reference_mode=patch_mod.EXACT_RECONSTRUCTION,
        )
    )

    assert result.ok is False
    assert result.fallback_reason == "invalid_model_patch"


def test_model_failure_returns_observable_fallback(monkeypatch):
    _install_fake_model(monkeypatch, error=RuntimeError("provider unavailable"))

    result = _run(patch_mod.propose_authored_slide_patch(PNG, DOM, "fix"))

    assert result.ok is False
    assert "provider unavailable" in result.failure_reason
    assert result.fallback_reason == "model_call_failed"
    assert result.model == "patch-model"


def test_invalid_image_or_empty_dom_is_rejected_before_model_call(monkeypatch):
    calls = _install_fake_model(monkeypatch, response={})

    bad_image = _run(
        patch_mod.propose_authored_slide_patch(b"not-an-image", DOM, "fix")
    )
    bad_dom = _run(patch_mod.propose_authored_slide_patch(PNG, [], "fix"))

    assert bad_image.fallback_reason == "invalid_patch_input"
    assert bad_dom.fallback_reason == "invalid_patch_input"
    assert calls == []


def test_duplicate_dom_ids_are_rejected_before_model_call(monkeypatch):
    calls = _install_fake_model(monkeypatch, response={})
    duplicate = [DOM[0], dict(DOM[0])]

    result = _run(patch_mod.propose_authored_slide_patch(PNG, duplicate, "fix"))

    assert result.fallback_reason == "invalid_patch_input"
    assert calls == []
