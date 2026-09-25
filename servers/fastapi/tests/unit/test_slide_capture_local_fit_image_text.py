"""Unit tests for image/text evidence transport in slide_capture_local_fit.

Covers two things kept deliberately separate in the module under test:
1. `capture_with_policy` forwards `image_text_evidence` (producer records
   for the exact HTML being rendered) into the browser policy, unless the
   caller already supplied a non-empty list.
2. `merge_image_text_verdicts` copies the runner's verdicts back onto the
   matching elements, without ever deciding anything itself.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services.authored_local_fit_policy import html_digest, local_fit_policy
from utils.slide_capture_local_fit import (
    capture_with_policy,
    merge_image_text_verdicts,
    policy_from_request,
)


def _element(qid: str, **extra: Any) -> dict[str, Any]:
    return {"qid": qid, **extra}


def _report(image_text: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "fit_state": "final",
        "edits": [],
        "image_text": image_text or [],
    }


# --- merge_image_text_verdicts -------------------------------------------------


def test_verified_no_text_writes_no_text_status() -> None:
    metrics = {"elements": [_element("s1-img")]}
    report = _report([
        {"qid": "s1-img", "asset_identity": "digest-1", "verified": True, "scope": "verified_no_text"},
    ])

    merged = merge_image_text_verdicts(metrics, report)

    assert merged["elements"][0]["image_text_evidence"] == {
        "status": "no_text", "scope": "verified_no_text", "asset_identity": "digest-1",
    }


def test_verified_text_size_writes_font_floor_verified_status() -> None:
    metrics = {"elements": [_element("s1-img")]}
    report = _report([
        {"qid": "s1-img", "asset_identity": "digest-2", "verified": True, "scope": "verified_text_size"},
    ])

    merged = merge_image_text_verdicts(metrics, report)

    assert merged["elements"][0]["image_text_evidence"]["status"] == "font_floor_verified"


def test_unverified_record_writes_nothing() -> None:
    metrics = {"elements": [_element("s1-img")]}
    report = _report([
        {"qid": "s1-img", "asset_identity": "digest-3", "verified": False, "scope": "unverifiable"},
    ])

    merged = merge_image_text_verdicts(metrics, report)

    assert "image_text_evidence" not in merged["elements"][0]


def test_element_with_existing_evidence_is_left_untouched() -> None:
    existing = {"status": "font_floor_verified", "scope": "verified_text_size", "asset_identity": "old"}
    metrics = {"elements": [_element("s1-img", image_text_evidence=existing)]}
    report = _report([
        {"qid": "s1-img", "asset_identity": "new", "verified": True, "scope": "verified_no_text"},
    ])

    merged = merge_image_text_verdicts(metrics, report)

    assert merged["elements"][0]["image_text_evidence"] == existing


def test_record_with_no_matching_element_is_ignored() -> None:
    metrics = {"elements": [_element("s1-img")]}
    report = _report([
        {"qid": "s1-other", "asset_identity": "digest-4", "verified": True, "scope": "verified_no_text"},
    ])

    merged = merge_image_text_verdicts(metrics, report)

    assert "image_text_evidence" not in merged["elements"][0]


def test_no_other_element_field_is_modified_and_order_preserved() -> None:
    metrics = {"elements": [
        _element("s1-a", tag="div", css_visible=True),
        _element("s1-img", tag="img", css_visible=True),
        _element("s1-c", tag="span"),
    ]}
    report = _report([
        {"qid": "s1-img", "asset_identity": "digest-5", "verified": True, "scope": "verified_no_text"},
    ])

    merged = merge_image_text_verdicts(metrics, report)

    qids = [item["qid"] for item in merged["elements"]]
    assert qids == ["s1-a", "s1-img", "s1-c"]
    assert merged["elements"][0] == _element("s1-a", tag="div", css_visible=True)
    assert merged["elements"][1]["tag"] == "img"
    assert merged["elements"][1]["css_visible"] is True
    assert merged["elements"][2] == _element("s1-c", tag="span")


def test_inputs_are_not_mutated() -> None:
    element = _element("s1-img")
    metrics = {"elements": [element]}
    report = _report([
        {"qid": "s1-img", "asset_identity": "digest-6", "verified": True, "scope": "verified_no_text"},
    ])

    result = merge_image_text_verdicts(metrics, report)

    assert result is not metrics
    assert "image_text_evidence" not in element
    assert "image_text_evidence" not in metrics["elements"][0]


def test_no_verdicts_returns_a_copy_unchanged() -> None:
    metrics = {"elements": [_element("s1-img")], "other": 1}
    report = _report([])

    merged = merge_image_text_verdicts(metrics, report)

    assert merged == metrics
    assert merged is not metrics


def test_non_list_elements_returns_metrics_copy() -> None:
    metrics = {"elements": "not-a-list", "other": 1}
    report = _report([
        {"qid": "s1-img", "asset_identity": "digest-7", "verified": True, "scope": "verified_no_text"},
    ])

    merged = merge_image_text_verdicts(metrics, report)

    assert merged == metrics
    assert merged is not metrics


# --- capture_with_policy: image_text_evidence forwarding ----------------------


def _base_policy() -> dict[str, Any]:
    revision = html_digest("<html><body><main data-qid=\"s1-content\">hi</main></body></html>")
    return local_fit_policy(author_revision=revision, original_html_digest=revision)


def _install_capture_stub(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]):
    async def fake_render(request_html, **_kwargs):
        captured["request_html"] = request_html
        return SimpleNamespace(
            png=b"png-bytes",
            browser_metrics={"elements": [], "fit": {"fit_state": "final", "edits": [], "image_text": []}},
        )

    import utils.slide_capture as capture

    monkeypatch.setattr(capture, "render_html_to_png_with_metrics", fake_render)


@pytest.mark.anyio
async def test_capture_with_policy_forwards_producer_evidence_for_the_rendered_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = '<html><body><main data-qid="s1-content">hello</main></body></html>'
    produced = [{"qid": "s1-img", "asset_digest": "abc", "text_free": True,
                 "review_digest": "rev-1", "provenance": "icon_pack"}]

    def fake_producer(requested_html: str, **_: Any) -> list[dict[str, Any]]:
        assert requested_html == html
        return produced

    import services.authored_local_fit_policy as policy_module

    monkeypatch.setattr(policy_module, "image_text_evidence_for_html", fake_producer, raising=False)

    captured: dict[str, Any] = {}
    _install_capture_stub(monkeypatch, captured)

    await capture_with_policy(
        html, 5000, deck_scope=None, fit=True, policy=_base_policy(), fresh=False,
    )

    sent_policy = policy_from_request(captured["request_html"])
    assert sent_policy is not None
    assert sent_policy["image_text_evidence"] == produced


@pytest.mark.anyio
async def test_capture_with_policy_keeps_caller_supplied_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = '<html><body><main data-qid="s1-content">hello</main></body></html>'
    caller_supplied = [{"qid": "s1-img", "asset_digest": "caller", "text_free": True,
                        "review_digest": "rev-2", "provenance": "icon_pack"}]

    def fake_producer(requested_html: str, **_: Any) -> list[dict[str, Any]]:
        raise AssertionError("producer must not be called when caller already supplied evidence")

    import services.authored_local_fit_policy as policy_module

    monkeypatch.setattr(policy_module, "image_text_evidence_for_html", fake_producer, raising=False)

    captured: dict[str, Any] = {}
    _install_capture_stub(monkeypatch, captured)

    policy = {**_base_policy(), "image_text_evidence": caller_supplied}
    await capture_with_policy(
        html, 5000, deck_scope=None, fit=True, policy=policy, fresh=False,
    )

    sent_policy = policy_from_request(captured["request_html"])
    assert sent_policy is not None
    assert sent_policy["image_text_evidence"] == caller_supplied


@pytest.fixture
def anyio_backend():
    return "asyncio"
