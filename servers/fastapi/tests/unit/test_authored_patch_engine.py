"""Safety and atomicity contracts for the authored local patch engine."""

import pytest
from pydantic import ValidationError

from utils.authored_dom_quality import (
    build_compact_dom_map,
    ensure_authored_qids,
    is_valid_authored_qid,
)
from utils.authored_patch_engine import (
    EXACT_RECONSTRUCTION,
    AuthoredPatchPlan,
    apply_authored_patch,
)

HTML = """<!doctype html>
<html><body>
  <main data-qid="slide" style="position: relative; width: 1280px; height: 720px">
    <h1 data-qid="title" style="color: #111; font-size: 40px">Old &amp; title</h1>
    <div data-qid="box" style="position: absolute; left: 10px; top: 20px; width: 100px; height: 80px"></div>
    <img data-qid="hero" src="data:image/png;base64,AA==" style="object-fit: contain; object-position: center">
  </main>
</body></html>"""


def _plan(*actions):
    return {"strategy": "patch", "actions": list(actions), "rationale": "repair"}


def test_all_allowlisted_actions_apply_atomically_and_report_before_after():
    result = apply_authored_patch(
        HTML,
        _plan(
            {"action": "setText", "qid": "title", "text": "New <safe> & clear"},
            {
                "action": "setStyle",
                "qid": "title",
                "styles": {"color": "#024", "font-size": "36px"},
            },
            {"action": "move", "qid": "box", "left": "24px", "top": "32px"},
            {"action": "resize", "qid": "box", "width": "160px", "height": "96px"},
            {
                "action": "crop",
                "qid": "hero",
                "fit": "cover",
                "position": "50% 25%",
            },
        ),
    )

    assert result.ok is True
    assert result.path == "local_patch"
    assert [change.action for change in result.changes] == [
        "setText",
        "setStyle",
        "move",
        "resize",
        "crop",
    ]
    assert result.changes[0].before == {"text": "Old & title"}
    assert result.changes[0].after == {"text": "New <safe> & clear"}
    assert "New &lt;safe&gt; &amp; clear" in result.html
    assert "font-size: 36px" in result.html
    assert "left: 24px" in result.html
    assert "width: 160px" in result.html
    assert "object-fit: cover" in result.html
    for qid in ("slide", "title", "box", "hero"):
        assert result.html.count(f'data-qid="{qid}"') == 1


@pytest.mark.parametrize(
    "styles",
    [
        {"background-color": "url(https://attacker.test/a)"},
        {"color": "red; position: fixed"},
        {"color": "expression(alert(1))"},
        {"transform": "translateX(4px)"},
        {"--payload": "red"},
    ],
)
def test_unsafe_or_non_allowlisted_css_fails_closed(styles):
    result = apply_authored_patch(
        HTML,
        _plan({"action": "setStyle", "qid": "title", "styles": styles}),
    )

    assert result.ok is False
    assert result.html == HTML
    assert result.changes == []
    assert result.fallback_reason == "invalid_patch_schema"
    assert result.path == "full_fallback"


def test_target_must_be_an_exact_qid_not_a_selector():
    with pytest.raises(ValidationError):
        AuthoredPatchPlan.model_validate(
            _plan({"action": "setText", "qid": "#title", "text": "changed"})
        )


def test_late_missing_target_rolls_back_earlier_actions():
    result = apply_authored_patch(
        HTML,
        _plan(
            {"action": "setText", "qid": "title", "text": "would change"},
            {"action": "move", "qid": "missing", "left": "25px"},
        ),
    )

    assert result.ok is False
    assert result.html == HTML
    assert result.changes == []
    assert result.fallback_reason == "target_not_found"


def test_malformed_html_is_rejected_without_mutation():
    malformed = '<div data-qid="one"><span data-qid="two"></div>'
    patch = _plan({"action": "setText", "qid": "one", "text": "new"})
    malformed_result = apply_authored_patch(malformed, patch)

    assert malformed_result.html == malformed
    assert malformed_result.fallback_reason == "invalid_html_structure"


def test_qid_migration_and_patch_share_a_valid_unique_id_contract():
    source = (
        '<div data-qid="bad qid">old</div>'
        '<p data-qid="same">keep</p><p data-qid="same">replace</p><img>'
    )
    migrated = ensure_authored_qids(source)
    target_qid = build_compact_dom_map(migrated)[0].qid

    result = apply_authored_patch(
        source,
        _plan({"action": "setText", "qid": target_qid, "text": "new"}),
    )

    result_qids = [node.qid for node in build_compact_dom_map(result.html)]
    assert result.ok is True
    assert result.html.count("new") == 1
    assert len(result_qids) == len(set(result_qids)) == 4
    assert all(is_valid_authored_qid(qid) for qid in result_qids)


def test_exact_reconstruction_forbids_text_changes():
    result = apply_authored_patch(
        HTML,
        _plan({"action": "setText", "qid": "title", "text": "not allowed"}),
        reference_mode=EXACT_RECONSTRUCTION,
    )

    assert result.ok is False
    assert result.html == HTML
    assert result.fallback_reason == "invalid_patch_schema"


def test_set_text_cannot_destroy_descendant_stable_ids():
    nested = '<div data-qid="parent">before <span data-qid="child">child</span></div>'
    result = apply_authored_patch(
        nested,
        _plan({"action": "setText", "qid": "parent", "text": "replacement"}),
    )

    assert result.ok is False
    assert result.html == nested
    assert result.fallback_reason == "unsafe_patch"


def test_full_reauthor_request_is_explicitly_routed_to_fallback():
    plan = {
        "strategy": "full_reauthor",
        "actions": [],
        "fallback_reason": "requires a new chart element",
    }

    result = apply_authored_patch(HTML, plan)

    assert result.ok is False
    assert result.html == HTML
    assert result.failure_reason == "requires a new chart element"
    assert result.fallback_reason == "model_requested_full_reauthor"


def test_crop_is_img_only_and_script_targets_are_never_editable():
    crop_result = apply_authored_patch(
        HTML,
        _plan({"action": "crop", "qid": "box", "fit": "cover"}),
    )
    script_html = '<script data-qid="logic">alert(1)</script>'
    script_result = apply_authored_patch(
        script_html,
        _plan({"action": "setStyle", "qid": "logic", "styles": {"color": "red"}}),
    )

    assert crop_result.html == HTML
    assert crop_result.fallback_reason == "unsafe_patch"
    assert script_result.html == script_html
    assert script_result.fallback_reason == "unsafe_patch"


def test_no_effect_patch_is_rejected_for_safe_fallback():
    result = apply_authored_patch(
        HTML,
        _plan({"action": "move", "qid": "box", "left": "10px"}),
    )

    assert result.ok is False
    assert result.html == HTML
    assert result.fallback_reason == "patch_not_applicable"


def test_unknown_action_fields_are_forbidden():
    with pytest.raises(ValidationError):
        AuthoredPatchPlan.model_validate(
            _plan(
                {
                    "action": "setText",
                    "qid": "title",
                    "text": "safe",
                    "onclick": "alert(1)",
                }
            )
        )


def test_local_patch_preserves_unrelated_svg_and_document_markup_byte_for_byte():
    source = (
        '<!DOCTYPE html><html><body data-note="keep-single-quotes">'
        '<svg viewBox="0 0 20 20" preserveAspectRatio="xMidYMid meet">'
        '<linearGradient id="paint"><stop offset="0%" /></linearGradient></svg>'
        '<div data-qid="target" class="original" style="left: 1px">text</div>'
        '<script>window.fixture = "<unchanged>";</script></body></html>'
    )
    result = apply_authored_patch(
        source,
        _plan({"action": "move", "qid": "target", "left": "2px"}),
    )

    assert result.ok is True
    canonical_source = ensure_authored_qids(source)
    before, after = result.html.split('<div data-qid="target"', 1)
    source_before, source_after = canonical_source.split('<div data-qid="target"', 1)
    assert before == source_before
    assert after.split(">", 1)[1] == source_after.split(">", 1)[1]
    assert 'class="original"' in result.html
    assert 'style="left: 2px"' in result.html
