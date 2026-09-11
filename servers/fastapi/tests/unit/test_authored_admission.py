from __future__ import annotations

from functools import wraps

import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import re
from unittest.mock import AsyncMock

import pytest

from services import authored_admission as gate
from services.authored_admission_capture import capture_once
from services.authored_admission_context import context_from_source, digest_html
from services.authored_admission_policy import CONSUMED_GLOBAL_ATTRIBUTES
from services.authored_admission_receipt import (
    AdmissionCaptureStore,
    AdmissionReceiptError,
    require_admission_receipt,
)
from services.authored_admission_session import AdmissionSession, _SESSION, check_producer
from services.authored_admission_shadow import AdmissionShadow
from services.source_content.browser_readback import BrowserSourceReadbackResult, deterministic_source_qid
from utils.renderer_markup_policy import GLOBAL_ATTRIBUTES, SERVER_STAMPED_ATTRIBUTES
from utils.slide_capture_result import RenderCapture


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


MANIFEST = {
    "units": [
        {
            "sourceUnitId": "u1",
            "sourceIndex": 0,
            "blocks": [
                {"sourceBlockId": "b1", "kind": "text", "order": 0, "payload": {"text": "원문 제목"}},
            ],
        }
    ]
}
OUTLINE = {"slides": [{"sourceUnitId": "u1", "sourceIndex": 0, "readingOrder": ["b1"]}]}
HTML = "<!doctype html><html><head><style>body{margin:0}</style></head><body><h1>원문 제목</h1></body></html>"


def context(**kwargs):
    values = dict(
        presentation_id="p1",
        operation_id="op1",
        slide_index=0,
        source_unit_id="u1",
        policy_revision="test/v1",
        fenced_revision=4,
        attempt_token="attempt-1",
        reference_mode="source_content_restyle",
        source_reflow=True,
        terminal_fallback_eligible=False,
        bindings=(("b1", "원문 제목"),),
    )
    values.update(kwargs)
    return context_from_source(manifest=MANIFEST, projection=OUTLINE, **values)


def metrics(html, **changes):
    element = {
        "sourceBlockId": "b1",
        "qid": deterministic_source_qid("b1"),
        "semanticRole": "title",
        "renderedText": "원문 제목",
        "bbox": {"x": 20, "y": 20, "width": 200, "height": 24},
        "cssVisible": True,
        "effectiveOpacity": 1,
        "fontLoaded": True,
        "clipped": False,
        "glyphClipped": False,
        "lineBoxClipped": False,
        "occludedFraction": 0,
        "continuousOccludedFraction": 0,
        "computedStyle": {"display": "block", "visibility": "visible"},
        "measurement": {"occlusion": True},
        "occlusionMeasurementEvidence": {"status": "measured"},
    }
    element.update(changes)
    return {
        "width": 1280,
        "height": 720,
        "fontsReady": True,
        "source_digest": digest_html(html),
        "render_digest": "sha256:" + "a" * 64,
        "elements": [element],
    }


@pytest.fixture(autouse=True)
def legacy_representation(monkeypatch):
    monkeypatch.setattr(gate, "get_authored_html_normalization", lambda: "legacy")
    monkeypatch.setattr(gate, "text_step", gate.bind_current_source)


def test_context_is_immutable_and_pins_source_digest():
    pinned = context()
    with pytest.raises(FrozenInstanceError):
        pinned.slide_index = 2
    with pytest.raises(ValueError, match="manifest_digest_mismatch"):
        replace(pinned, manifest_digest="changed")
    assert pinned.readback_documents() == (MANIFEST, OUTLINE)
    assert replace(pinned, attempt_token="other").identity_digest != pinned.identity_digest


@pytest.mark.parametrize("mode", ["ordinary", "verbatim", "terminal"])
@pytest.mark.parametrize("predicate", ["raw", "static", "integrity", "visibility", "telemetry", "diagnostic"])
@pytest.mark.parametrize("flagged", [False, True])
def test_three_mode_table(monkeypatch, mode, predicate, flagged):
    ctx = context(
        state="captured",
        source_reflow=mode != "ordinary",
        terminal_fallback_eligible=mode == "terminal",
        already_admitted_flags=("source_readback:visibility",) if flagged else (),
    )
    html = gate.admit_slide_html(context(), HTML).html
    sample = metrics(html)
    if predicate == "raw":
        html += "\x00"
    elif predicate == "static":
        html = html.replace("원문 제목", "변경됨")
        sample = metrics(html)
    else:
        result = BrowserSourceReadbackResult(
            1,
            1,
            **{
                "integrity": {"integrity_failures": ("b1",)},
                "visibility": {"visibility_failures": ("b1",)},
                "telemetry": {"telemetry_failures": ("b1",)},
                "diagnostic": {"diagnostic_error": "missing capture"},
            }[predicate],
        )
        monkeypatch.setattr(gate, "inspect_browser_source_readback", lambda **_: result)
    admission = gate.admit_slide_html(ctx, html, metrics=sample, png=b"png")
    expected = "rejected"
    if mode == "ordinary" and predicate != "raw":
        expected = "admitted"
    elif mode == "terminal" and flagged and predicate == "visibility":
        expected = "flag_only"
    assert admission.verdict == expected
    if mode != "ordinary" and predicate in {"telemetry", "diagnostic"}:
        assert admission.retryable


def test_fixed_gate_order_and_post_expansion_policy(monkeypatch):
    calls = []
    monkeypatch.setattr(gate, "raw_diagnostics_step", lambda _: calls.append("raw"))
    monkeypatch.setattr(gate, "validate_authored_slide_html", lambda html, **_: calls.append("policy") or html)
    monkeypatch.setattr(gate, "text_step", lambda ctx, html: calls.append("text") or gate.TextStepResult(html))
    monkeypatch.setattr(
        gate,
        "inspect_browser_source_readback",
        lambda **_: calls.append("readback") or BrowserSourceReadbackResult(1, 1),
    )
    assert gate.admit_slide_html(context(state="token"), HTML).verdict == "admitted"
    assert calls == ["raw", "policy", "text", "policy"]
    calls.clear()
    assert gate.admit_slide_html(context(state="captured", source_reflow=False), HTML, metrics={}).verdict == "admitted"
    assert calls == ["raw", "policy", "policy"]


def test_duplicate_attributes_fail_before_rewrite(monkeypatch):
    normalizer = AsyncMock()
    monkeypatch.setattr(gate, "validate_authored_slide_html", normalizer)
    admission = gate.admit_slide_html(context(), '<div id="a" ID="b">text</div>')
    assert admission.verdict == "rejected" and admission.predicate == "raw_diagnostics"
    normalizer.assert_not_called()


def test_text_step_normalizations_are_carried_in_receipt(monkeypatch):
    entry = {"rule": "source_placeholder_expanded", "blocks": 1, "deviations": 0}
    monkeypatch.setattr(gate, "text_step", lambda ctx, html: gate.TextStepResult(html, normalizations=(entry,)))
    admission = gate.admit_slide_html(context(source_reflow=False, state="token"), HTML)
    assert entry in admission.normalizations
    assert entry in admission.receipt.as_dict()["normalizations"]


@pytest.mark.parametrize(
    "run,change",
    [
        ("run-13", {"sourceBlockId": ""}),
        ("run-14", {"cssVisible": False}),
        ("run-17", {"renderedText": "changed"}),
        ("run-19", {"sourceBlockId": "wrong-id"}),
        ("run-23", {"renderedText": "short title changed"}),
    ],
)
def test_synthetic_historical_failure_classes_remain_rejected(run, change):
    html = gate.admit_slide_html(context(), HTML).html
    result = gate.admit_slide_html(context(state="captured"), html, metrics=metrics(html, **change))
    assert result.verdict == "rejected", run


def test_run18_void_end_tag_keeps_existing_normalization_semantics():
    result = gate.admit_slide_html(context(), HTML.replace("</h1>", "</h1></img>"))
    assert result.verdict == "admitted" and "</img>" not in result.html


def test_real_captured_integrity_and_html_binding():
    expanded = gate.admit_slide_html(context(), HTML)
    assert expanded.verdict == "admitted"
    captured = context(state="captured")
    assert gate.admit_slide_html(captured, expanded.html, metrics=metrics(expanded.html)).verdict == "admitted"
    damaged = metrics(expanded.html, renderedText="손상된 원문")
    assert gate.admit_slide_html(captured, expanded.html, metrics=damaged).predicate == "rendered_integrity"
    assert gate.admit_slide_html(captured, HTML, metrics=metrics(HTML)).verdict == "rejected"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"html": "tampered"}, "html_digest_mismatch"),
        ({"attempt_token": "stale"}, "context_mismatch"),
        ({"fenced_revision": 3}, "context_mismatch"),
        ({"slide_index": 1}, "context_mismatch"),
        ({"operation_id": "other"}, "context_mismatch"),
        ({"png": b"other"}, "png_digest_mismatch"),
        ({"metrics": {}}, "metrics_digest_mismatch"),
    ],
)
def test_writer_receipt_mismatch(change, code):
    html = gate.admit_slide_html(context(), HTML).html
    captured = gate.admit_slide_html(context(state="captured"), html, metrics=metrics(html), png=b"png")
    kwargs = dict(
        html=html,
        stage="render",
        slide_index=0,
        attempt_token="attempt-1",
        fenced_revision=4,
        operation_id="op1",
        metrics=metrics(html),
        png=b"png",
    )
    require_admission_receipt(captured.receipt, **kwargs)
    with pytest.raises(AdmissionReceiptError, match=code):
        require_admission_receipt(captured.receipt, **dict(kwargs, **change))


def test_rejected_capture_can_only_be_nonpublishable_evidence():
    html = gate.admit_slide_html(context(), HTML).html
    sample = metrics(html, cssVisible=False)
    admission = gate.admit_slide_html(context(state="captured"), html, metrics=sample, png=b"png")
    kwargs = dict(
        html=html,
        stage="render",
        slide_index=0,
        attempt_token="attempt-1",
        fenced_revision=4,
        metrics=sample,
        png=b"png",
    )
    with pytest.raises(AdmissionReceiptError, match="receipt_rejected"):
        require_admission_receipt(admission.receipt, **kwargs)
    evidence = replace(admission.receipt, persistence_class="evidence")
    require_admission_receipt(evidence, **kwargs)
    assert evidence.as_dict()["publishable"] is False


@sync_async
async def test_checkpoint_writer_refuses_unadmitted_html_before_io(monkeypatch):
    """B1: a producer without a session is admitted at the writer, not exempted.

    ``enforce_receipts`` falls back to the global knob when no admission session
    is open, which is how the authored-vNext legacy bridge and the workspace
    page-remake handlers reach ``complete_stage``. The writer obtains their
    receipt by admitting the HTML on the same predicate, so bytes the gate
    rejects still never reach the database -- ``job_id`` here is not a real job,
    so any database access would fail loudly instead.
    """

    from services import authored_checkpoint_state as state
    from services import authored_admission_checkpoint as checkpoint

    monkeypatch.setattr(checkpoint, "enforce_receipts", lambda: True)
    with pytest.raises(AdmissionReceiptError, match="receipt_rejected"):
        await state.complete_stage(
            job_id="job",
            slide_index=0,
            stage="author",
            attempt_token="attempt-1",
            fencing_token=4,
            html=HTML.replace("<h1>", '<h1 id="a" id="b">'),
        )


def test_same_profile_capture_reuse_is_attempt_bound():
    html = gate.admit_slide_html(context(), HTML).html
    ctx = context(state="captured")
    sample = metrics(html)
    captured = gate.admit_slide_html(ctx, html, metrics=sample, png=b"png")
    store = AdmissionCaptureStore()
    store.remember(captured.receipt, sample, b"png")
    assert store.reuse(ctx, html).receipt.capture_reused
    assert store.reuse(replace(ctx, capture_profile="other"), html) is None
    assert store.reuse(replace(ctx, attempt_token="other"), html) is None
    assert store.reuse(ctx, html + " ") is None


@sync_async
async def test_capture_once_coalesces_identical_concurrent_calls():
    html = gate.admit_slide_html(context(), HTML).html
    sample = metrics(html)
    capture = AsyncMock(
        return_value=RenderCapture.from_payload(
            html_digest=digest_html(html),
            png=b"png",
            browser_metrics=sample,
            runtime_counters={},
        )
    )
    session = AdmissionSession(mode="enforce", contexts=(context(),))
    token = _SESSION.set(session)
    try:
        await asyncio.gather(*(capture_once(html, index=0, capture=capture) for _ in range(2)))
        assert capture.await_count == 1
        assert session.capture_reuses == 1
        from services.authored_admission_checkpoint import prepare_checkpoint_write

        prepared = prepare_checkpoint_write(
            dict(
                html=html,
                stage="render",
                slide_index=0,
                browser_metrics=sample,
                png=b"png",
            )
        )
        assert prepared["admission_receipt"].capture_reused
        assert prepared["admission_receipt"].as_dict()["metricsStorage"] == "checkpoint_bundle"
        await capture_once(html, index=0, capture=capture, fit=False)
        assert capture.await_count == 2
    finally:
        _SESSION.reset(token)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_shadow_keeps_legacy_verdict_and_enforce_uses_gate_only(mode):
    session = AdmissionSession(mode=mode, contexts=(context(),))
    token = _SESSION.set(session)
    calls = []
    try:

        def legacy(html):
            calls.append(html)
            return "legacy result"

        if mode == "shadow":
            result, admission = check_producer(HTML + "\x00", index=0, producer="author", legacy=legacy)
            assert result == "legacy result" and admission.verdict == "rejected"
            assert session.shadow.as_dict()["rows"][0]["existingVerdict"] == "admitted"
        else:
            with pytest.raises(gate.BrowserSourceReadbackError):
                check_producer(HTML + "\x00", index=0, producer="author", legacy=legacy)
            assert not calls
    finally:
        _SESSION.reset(token)


def test_shadow_bound_and_stable_error_codes():
    from services.authored_generation_observability import log_authored_slide_failure_observation

    shadow = AdmissionShadow(max_rows=1)
    for producer in ("author", "author_batch"):
        shadow.record(
            producer=producer,
            existing_verdict="admitted",
            existing_code="",
            gate_verdict="rejected",
            gate_code="source_placeholder_missing",
        )
    assert shadow.as_dict()["dropped"] == 1
    assert len(json.dumps(shadow.as_dict())) < 1024
    observation = log_authored_slide_failure_observation(
        stage="author",
        code="source_placeholder_missing",
        slide_number=1,
        retryable=False,
    )
    assert observation["code"] == "source_placeholder_missing"


# Explicitly grandfathered dead readers; none is renderer permission.
KNOWN_DEAD_READS = {
    "services/source_content/browser_readback.py": {"data-source-image", "data-decorative", "data-decoration"},
    "utils/slide_capture_fit_observe.py": {"data-role"},
}


def test_consumer_attribute_vocabulary_is_in_renderer_policy():
    root = Path(__file__).resolve().parents[2]
    consumers = [
        *root.glob("utils/slide_capture_fit*.py"),
        root / "services/source_content/browser_readback.py",
        root / "services/authored_terminal_slide_policy.py",
        root / "services/authored_flag_classification.py",
    ]
    assert CONSUMED_GLOBAL_ATTRIBUTES <= GLOBAL_ATTRIBUTES
    for path in consumers:
        source = path.read_text(encoding="utf-8")
        names = set(re.findall(r"(?:getAttribute|hasAttribute|attrs\.get)\(['\"](data-[a-z-]+)['\"]", source))
        relative = path.relative_to(root).as_posix()
        assert names <= GLOBAL_ATTRIBUTES | set(SERVER_STAMPED_ATTRIBUTES) | KNOWN_DEAD_READS.get(relative, set()), (
            relative
        )
