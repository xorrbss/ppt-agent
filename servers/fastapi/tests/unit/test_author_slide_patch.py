"""Model-facing contract tests for authored local patch proposals."""

import asyncio
import json
from pathlib import Path

import pytest

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
STRUCTURAL_DOM = [
    {
        "qid": "slide",
        "tag": "div",
        "bbox": {"x": 0, "y": 0, "width": 1280, "height": 720},
        "computedStyle": {"display": "flex", "position": "relative"},
    },
    {
        "qid": "cards",
        "tag": "section",
        "parentQid": "slide",
        "bbox": {"x": 80, "y": 180, "width": 1120, "height": 400},
        "computedStyle": {
            "display": "grid",
            "gridTemplateColumns": "1fr 1fr",
            "gap": "20px",
            "position": "relative",
        },
    },
    {
        "qid": "card-a",
        "tag": "article",
        "parentQid": "cards",
        "sourceBlockId": "block-a",
        "bbox": {"x": 80, "y": 180, "width": 550, "height": 188},
        "computedStyle": {"display": "block", "position": "static"},
    },
    {
        "qid": "copy-a",
        "tag": "p",
        "parentQid": "card-a",
        "sourceBlockId": "block-a",
        "text": "First source block",
        "bbox": {"x": 96, "y": 200, "width": 518, "height": 160},
        "computedStyle": {"display": "block", "position": "static"},
    },
    {
        "qid": "card-b",
        "tag": "article",
        "parentQid": "cards",
        "sourceBlockId": "block-b",
        "bbox": {"x": 650, "y": 180, "width": 550, "height": 188},
        "computedStyle": {"display": "block", "position": "static"},
    },
    {
        "qid": "copy-b",
        "tag": "p",
        "parentQid": "card-b",
        "sourceBlockId": "block-b",
        "text": "Second source block",
        "bbox": {"x": 666, "y": 200, "width": 518, "height": 160},
        "computedStyle": {"display": "block", "position": "static"},
    },
    {
        "qid": "footer",
        "tag": "footer",
        "parentQid": "slide",
        "text": "Protected footer",
        "bbox": {"x": 80, "y": 680, "width": 1120, "height": 24},
        "computedStyle": {"display": "block", "position": "static"},
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


def test_structural_scope_selects_layout_owner_and_complete_sibling_group():
    scope = patch_mod.build_structural_repair_scope(
        STRUCTURAL_DOM,
        {"issues": [{"qids": ["copy-a"], "detail": "copy clips card"}]},
    )

    assert scope is not None
    assert scope.layout_owner_qid == "cards"
    assert set(scope.editable_qids) == {"cards", "card-a", "card-b", "copy-a"}
    assert "copy-b" in scope.affected_qids
    assert "copy-b" not in scope.protected_qids
    assert scope.affected_source_block_ids == ("block-a", "block-b")
    assert "footer" in scope.protected_qids


def test_structural_scope_merges_nested_card_owners_at_shared_layout_owner():
    nested = json.loads(json.dumps(STRUCTURAL_DOM))
    for item in nested:
        if item["qid"] in {"card-a", "card-b"}:
            item["computedStyle"]["display"] = "flex"

    scope = patch_mod.build_structural_repair_scope(
        nested,
        issue_qids=("copy-a", "copy-b"),
    )

    assert scope is not None
    assert scope.layout_owner_qid == "cards"
    assert set(scope.editable_qids) == {
        "cards",
        "card-a",
        "card-b",
        "copy-a",
        "copy-b",
    }


def test_structural_scope_accepts_layout_owner_as_issue_target():
    scope = patch_mod.build_structural_repair_scope(
        STRUCTURAL_DOM,
        issue_qids=("cards",),
    )

    assert scope is not None
    assert scope.layout_owner_qid == "cards"


def test_structural_scope_does_not_treat_arbitrary_top_level_qid_as_owner():
    top_level = [
        {
            "qid": "generated-canvas-42",
            "tag": "main",
            "computedStyle": {"display": "grid", "position": "relative"},
        },
        {
            "qid": "card",
            "tag": "article",
            "parentQid": "generated-canvas-42",
            "computedStyle": {"display": "block", "position": "static"},
        },
    ]

    assert (
        patch_mod.build_structural_repair_scope(
            top_level,
            issue_qids=("card",),
        )
        is None
    )


def test_structural_scope_rejects_missing_target_instead_of_partial_scope():
    scope = patch_mod.build_structural_repair_scope(
        STRUCTURAL_DOM,
        issue_qids=("copy-a", "missing-qid"),
    )

    assert scope is None


def test_structural_scope_uses_full_dom_when_prompt_would_drop_late_targets():
    dom = json.loads(json.dumps(STRUCTURAL_DOM))
    for index in range(70):
        dom.append({
            "qid": f"source-{index}", "tag": "p", "parentQid": "card-b",
            "sourceBlockId": f"block-{index}", "text": "full source " * 100,
            "computedStyle": {key: "x" * 100 for key in patch_mod._STYLE_KEYS},
        })
    _, truncated_qids = patch_mod.compact_dom_map_for_prompt(dom)
    assert "source-69" not in truncated_qids
    scope = patch_mod.build_structural_repair_scope(dom, issue_qids=("source-69",))
    assert scope is not None
    assert "source-69" in scope.affected_qids
    assert "block-69" in scope.affected_source_block_ids
    graph = patch_mod.build_full_dom_graph(dom)
    assert len(graph.nodes) == len(dom)


def test_parentless_content_owner_requires_html_and_keeps_system_roots_readonly():
    dom = [dict(node) for node in STRUCTURAL_DOM if node["qid"] != "slide"]
    for node in dom:
        if node.get("parentQid") == "slide":
            node.pop("parentQid")
    html = '<html><body><section data-qid="cards"><article data-qid="card-a"><p data-qid="copy-a"></p></article><article data-qid="card-b"><p data-qid="copy-b"></p></article></section><footer data-qid="footer"></footer></body></html>'
    assert patch_mod.build_structural_repair_scope(dom, issue_qids=("copy-a",)) is None
    scope = patch_mod.build_structural_repair_scope(dom, issue_qids=("copy-a",), html=html)
    assert scope is not None
    assert scope.layout_owner_qid == "cards"
    root_html = html.replace('<section data-qid="cards">', '<section class="slide-canvas" data-qid="cards">')
    assert patch_mod.build_structural_repair_scope(dom, issue_qids=("copy-a",), html=root_html) is None


def test_full_dom_rejects_missing_parent_cycles_and_safety_limit():
    with pytest.raises(ValueError, match="parent absent"):
        patch_mod.build_full_dom_graph([{ "qid": "child", "tag": "p", "parentQid": "absent"}])
    with pytest.raises(ValueError, match="parent cycle"):
        patch_mod.build_full_dom_graph([{ "qid": "child", "tag": "p", "parentQid": "child"}])
    with pytest.raises(ValueError, match="safety limit"):
        patch_mod.build_full_dom_graph([{ "qid": f"q-{index}", "tag": "p"} for index in range(257)])


def test_large_group_preserves_all_affected_nodes_with_owner_only_edit_permission():
    dom = [STRUCTURAL_DOM[0], STRUCTURAL_DOM[1]] + [
        {"qid": f"card-{index}", "tag": "article", "parentQid": "cards"}
        for index in range(20)
    ]
    scope = patch_mod.build_structural_repair_scope(dom, issue_qids=("card-19",))
    assert scope is not None
    assert scope.editable_qids == ("cards",)
    assert len(scope.affected_qids) == 21


def test_many_text_defects_keep_complete_card_geometry_permissions():
    dom = [STRUCTURAL_DOM[0], STRUCTURAL_DOM[1]]
    targets = []
    for index in range(8):
        dom.append({"qid": f"card-{index}", "tag": "article", "parentQid": "cards"})
        for leaf in ("title", "copy"):
            qid = f"{leaf}-{index}"
            targets.append(qid)
            dom.append({"qid": qid, "tag": "p", "parentQid": f"card-{index}"})
    scope = patch_mod.build_structural_repair_scope(dom, issue_qids=targets)
    assert scope is not None
    assert set(scope.editable_qids) == {"cards", *(f"card-{index}" for index in range(8))}
    assert set(targets) <= set(scope.affected_qids)
    assert not set(targets) & set(scope.editable_qids)


def test_structural_prompt_compacts_values_without_losing_any_qid(monkeypatch):
    dom = json.loads(json.dumps(STRUCTURAL_DOM))
    for node in dom:
        node["text"] = "long text " * 100
    scope = patch_mod.build_structural_repair_scope(dom, issue_qids=("copy-b",))
    monkeypatch.setattr(patch_mod, "_MAX_DOM_JSON_CHARS", 2300)
    messages, qids = patch_mod.build_patch_messages(PNG, dom, "fix", allow_source_reflow_safe_styles=True, repair_scope=scope)
    payload = json.loads(messages[1].content[0].text.split("Input data:\n", 1)[1])
    assert {node["qid"] for node in payload["compactDomMap"]} == qids == {node["qid"] for node in dom}
    assert len(json.dumps(payload["compactDomMap"], ensure_ascii=False, separators=(",", ":"))) <= 2300
    monkeypatch.setattr(patch_mod, "_MAX_DOM_JSON_CHARS", 10)
    with pytest.raises(ValueError, match="repair_prompt_budget_exceeded"):
        patch_mod.build_patch_messages(PNG, dom, "fix", allow_source_reflow_safe_styles=True, repair_scope=scope)


def test_independent_groups_are_stable_and_shared_reflow_groups_merge():
    dom = json.loads(json.dumps(STRUCTURAL_DOM))
    for item in dom:
        if item["qid"] in {"card-a", "card-b"}:
            item["computedStyle"]["display"] = "flex"
    merged = patch_mod.build_structural_repair_scopes(dom, issue_qids=("copy-b", "copy-a"))
    assert len(merged) == 1
    assert merged[0].layout_owner_qid == "cards"
    for item in dom:
        if item["qid"] in {"card-a", "card-b"}:
            item["parentQid"] = "slide"
    independent = patch_mod.build_structural_repair_scopes(dom, issue_qids=("copy-b", "copy-a"))
    assert tuple(scope.layout_owner_qid for scope in independent) == ("card-a", "card-b")


def test_overlapping_targets_are_grouped_before_protected_title_check():
    dom = [STRUCTURAL_DOM[0], {
        "qid": "header-content", "tag": "section", "parentQid": "slide",
        "computedStyle": {"display": "block", "position": "relative"},
    }, {
        "qid": "eyebrow", "tag": "div", "parentQid": "header-content",
        "bbox": {"x": 64, "y": 18, "width": 908, "height": 21},
        "semanticRole": "supporting",
    }, {
        "qid": "heading", "tag": "h1", "parentQid": "header-content",
        "bbox": {"x": 64, "y": 24, "width": 908, "height": 38},
        "semanticRole": "title",
    }]
    assert patch_mod.build_structural_repair_scopes(dom, issue_qids=("eyebrow",)) == ()
    scopes = patch_mod.build_structural_repair_scopes(dom, issue_qids=("eyebrow", "heading"))
    assert len(scopes) == 1
    assert scopes[0].layout_owner_qid == "header-content"
    assert {"eyebrow", "heading"} <= set(scopes[0].editable_qids)


def test_slide_protected_inventory_excludes_all_independent_repair_groups():
    dom = json.loads(json.dumps(STRUCTURAL_DOM))
    for item in dom:
        if item["qid"] in {"card-a", "card-b"}:
            item["parentQid"] = "slide"
            item["computedStyle"]["display"] = "flex"
    scopes = patch_mod.build_structural_repair_scopes(dom, issue_qids=("copy-a", "copy-b"))
    assert len(scopes) == 2
    assert "copy-b" in scopes[0].protected_qids
    protected = patch_mod.protected_qids_for_repair_scopes(dom, scopes)
    assert protected == ("cards", "footer", "slide")
    assert protected == patch_mod.protected_qids_for_repair_scopes(dom, tuple(reversed(scopes)))
    assert "copy-b" in scopes[0].protected_qids  # Per-candidate guards stay intact.
    assert set(patch_mod.protected_qids_for_repair_scopes(dom, ())) == {node["qid"] for node in dom}


@pytest.mark.parametrize("slide", (3, 8, 14))
def test_exact_header_overlap_repair_scopes_preserve_independent_groups(slide):
    from utils.llm_calls.author_vision_qa import _structural_compact_dom

    artifact = Path(__file__).resolve().parents[4] / "artifacts/authored-full-product-validation-20260910/run-2-repair-six"
    if not (artifact / f"slide-{slide}-baseline.html").is_file():
        pytest.skip("identical source regression capture is available only locally")
    html = (artifact / f"slide-{slide}-baseline.html").read_text(encoding="utf-8")
    metrics = json.loads((artifact / f"slide-{slide}-baseline-metrics.json").read_text(encoding="utf-8"))
    report = json.loads((artifact / f"slide-{slide}-baseline-report.json").read_text(encoding="utf-8"))
    targets = {qid for issue in report["issues"] for qid in issue["qids"]}
    scopes = patch_mod.build_structural_repair_scopes(
        _structural_compact_dom(html, metrics), issue_qids=targets, html=html,
    )
    assert scopes
    assert "q-div-75dcf98740c9" in {scope.layout_owner_qid for scope in scopes}
    assert targets <= {qid for scope in scopes for qid in scope.affected_qids}
    if slide == 14:
        assert len(scopes) == 2
        assert "q-takeaway-13" in {scope.layout_owner_qid for scope in scopes}


def test_bounded_structural_prompt_exposes_scope_and_style_protections():
    scope = patch_mod.build_structural_repair_scope(
        STRUCTURAL_DOM,
        issue_qids=("copy-a",),
    )
    messages, _qids = patch_mod.build_patch_messages(
        PNG,
        STRUCTURAL_DOM,
        "repair clipping",
        allow_source_reflow_safe_styles=True,
        repair_scope=scope,
    )

    assert "BOUNDED STRUCTURAL REPAIR CONTRACT" in messages[0].content
    assert "Preserve the established font family" in messages[0].content
    prompt = messages[1].content[0].text
    assert '"layout_owner_qid":"cards"' in prompt
    assert '"protected_qids"' in prompt
    assert '"allowedStructuralStyleProperties"' in prompt


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
            allow_source_reflow_safe_styles=True,
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


def test_bounded_structural_proposal_uses_one_physical_call_and_no_schema_retry(
    monkeypatch,
):
    calls = _install_fake_model(
        monkeypatch,
        response={
            "strategy": "patch",
            "actions": [
                {
                    "action": "setStyle",
                    "qid": "cards",
                    "styles": {"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr"},
                }
            ],
        },
    )
    scope = patch_mod.build_structural_repair_scope(
        STRUCTURAL_DOM,
        issue_qids=("copy-a",),
    )

    result = _run(
        patch_mod.propose_authored_slide_patch(
            PNG,
            STRUCTURAL_DOM,
            "repair clipping",
            allow_source_reflow_safe_styles=True,
            retry_invalid_model_patch=False,
            repair_scope=scope,
        )
    )

    assert result.ok is True
    assert len(calls) == 1
    assert calls[0][2]["validate_schema_max_loop_count"] == 1
    assert calls[0][2]["call_budget"].limit == 1


def test_bounded_structural_proposal_rejects_action_outside_scope(monkeypatch):
    _install_fake_model(
        monkeypatch,
        response={
            "strategy": "patch",
            "actions": [
                {"action": "resize", "qid": "footer", "height": "12px"}
            ],
        },
    )
    scope = patch_mod.build_structural_repair_scope(
        STRUCTURAL_DOM,
        issue_qids=("copy-a",),
    )

    result = _run(
        patch_mod.propose_authored_slide_patch(
            PNG,
            STRUCTURAL_DOM,
            "repair clipping",
            allow_source_reflow_safe_styles=True,
            repair_scope=scope,
        )
    )

    assert result.ok is False
    assert result.fallback_reason == "target_outside_repair_scope"


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
