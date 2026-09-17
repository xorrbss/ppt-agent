"""The receipt must carry the fitter's own unresolved reason, not a blanket label."""

import asyncio
import base64
import io
import json
from utils.slide_capture_result import RenderCapture

import pytest
from PIL import Image

import services.authored_local_fit_admission as admission
import services.authored_original_capture_checkpoint as original_checkpoint
from services.authored_local_fit_evidence import (
    AuthoredLocalFitRejected,
    accept_unchanged_capture,
)
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_local_fit_policy import html_digest, local_fit_policy
from utils.slide_capture_local_fit import local_fit_render_identity

ORIGINAL_HTML = '<html><body><main data-qid="s7-cards">slide 8</main></body></html>'


def _capture_for(
    html: str, *, fit: bool, policy: dict[str, object] | None = None,
) -> RenderCapture:
    revision = html_digest(html)
    policy = policy or local_fit_policy(
        author_revision=revision,
        original_html_digest=revision,
        slide_index=7,
    )
    manifest = policy["export_scale_manifest"]
    buffer = io.BytesIO()
    Image.new(
        "RGB", (manifest["viewport_width"], manifest["viewport_height"]), "white",
    ).save(buffer, format="PNG")
    run = {
        "run_id": "s7-body-run-0",
        "owner_qid": "s7-cards",
        "text_node_ordinal": 0,
        "start_offset": 0,
        "end_offset": 7,
        "content_digest": "sha256:fixture-body",
        "group_id": "s7-body-group",
        "role": "body",
        "ancestor_qids": ["s7-cards"],
        "original_pt": 18.0,
        "final_pt": 18.0,
    }
    group = {
        "group_id": "s7-body-group",
        "run_ids": ["s7-body-run-0"],
        "role": "body",
        "card_qid": "s7-cards",
        "original_pt": 18.0,
    }
    return RenderCapture.from_payload(
        fitted_html=None,
        html_digest=html_digest(html),
        png=buffer.getvalue(),
        browser_metrics={
            "elements": [],
            "local_fit_mode": fit,
            "local_fit_render_identity": local_fit_render_identity(
                html, policy, fit=fit,
            ),
        },
        runtime_counters={},
        fit_report={
            "requested": fit,
            "fit_state": "final",
            "status": "unchanged",
            "policy_revision": policy["policy_revision"],
            "changed": False,
            "partial": False,
            "error": None,
            "operations": [],
            "edits": [],
            "unresolved_reasons": [],
            "baseline": {
                "complete": True,
                "author_revision": policy["author_revision"],
                "original_html_digest": policy["original_html_digest"],
                "runs": [run],
                "groups": [group],
            },
            "runs": [run],
            "groups": [group],
        },
        prepared_html=html,
        prepared_fit_policy=policy,
    )


def _original_first_kwargs(*, render, evaluate=lambda **_kwargs: (None, None)):
    revision = html_digest(ORIGINAL_HTML)
    return {
        "original_html": ORIGINAL_HTML,
        "policy": local_fit_policy(
            author_revision=revision,
            original_html_digest=revision,
            slide_index=7,
        ),
        "evaluate": evaluate,
        "render": render,
        "render_gate": asyncio.Semaphore(1),
        "slide_deadline": 1e12,
        "verify_seconds": 5.0,
        "deck_scope": None,
        "slide_index": 7,
    }


def _unresolved_fit_capture(*, reasons: list[str], status: str = "unresolved"):
    """A fit=True capture whose fitter restored the original and stopped."""

    revision = html_digest(ORIGINAL_HTML)
    policy = local_fit_policy(
        author_revision=revision, original_html_digest=revision, slide_index=7,
    )
    capture = RenderCapture.from_payload(
        fitted_html=ORIGINAL_HTML,
        html_digest=revision,
        png=b"\x89PNG\r\n\x1a\nunresolved",
        browser_metrics={"elements": [], "local_fit_mode": True},
        runtime_counters={},
        fit_report={
            "requested": True,
            "fit_state": "final",
            "status": status,
            "changed": False,
            "partial": False,
            "error": None,
            "policy_revision": policy["policy_revision"],
            "unresolved_reasons": reasons,
            "search_termination": [
                {"code": "search.candidates_exhausted", "root_qid": "s7-cards",
                 "evaluated_candidates": 32}
            ],
            "operations": [],
            "edits": [],
            "baseline": {},
            "runs": [],
        },
        prepared_html=ORIGINAL_HTML,
        prepared_fit_policy=None,
    )
    return capture, policy


def test_accept_unchanged_capture_keeps_the_fitter_search_outcome() -> None:
    capture, policy = _unresolved_fit_capture(reasons=["candidates_exhausted"])

    with pytest.raises(AuthoredLocalFitRejected, match="^candidates_exhausted$"):
        accept_unchanged_capture(capture, original_html=ORIGINAL_HTML, policy=policy)


def test_accept_unchanged_capture_still_flags_an_unexplained_unresolved_report() -> None:
    capture, policy = _unresolved_fit_capture(reasons=[])

    with pytest.raises(AuthoredLocalFitRejected, match="^reconstruction_mismatch$"):
        accept_unchanged_capture(capture, original_html=ORIGINAL_HTML, policy=policy)


def test_admission_preserves_the_original_under_the_fitter_reason(monkeypatch) -> None:
    capture, policy = _unresolved_fit_capture(reasons=["candidates_exhausted"])
    seen: dict[str, object] = {}
    sentinel = object()

    async def fake_preserve_original_capture(**kwargs):
        seen.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        admission, "preserve_original_capture", fake_preserve_original_capture
    )

    def never_evaluate(**_kwargs):  # pragma: no cover - unchanged capture skips it
        raise AssertionError("evaluate must not run for an unchanged capture")

    accepted = asyncio.run(
        admission.admit_local_fit_capture(
            capture,
            original_html=ORIGINAL_HTML,
            policy=policy,
            evaluate=never_evaluate,
            render=never_evaluate,
            render_gate=asyncio.Semaphore(1),
            slide_deadline=1e12,
            verify_seconds=5.0,
            deck_scope=None,
        )
    )

    assert accepted is sentinel
    assert seen["reason"] == "candidates_exhausted"
    assert seen["rejected_report"] is capture.fit_report
    assert seen["original_html"] == ORIGINAL_HTML


def test_changed_fit_renders_canonical_bytes_without_relabeling_proposal(monkeypatch):
    from services import authored_admission as gate
    from services.authored_admission_capture import canonical_capture_html
    from services.authored_rollout_profile import RolloutProfile, rollout_profile_scope
    from tests.unit.test_authored_admission import HTML, context, metrics
    from utils.authored_fit_edits import apply_fit_edits
    from services.source_content.browser_readback import deterministic_source_qid
    from utils.slide_capture_result import RenderCapture

    with rollout_profile_scope(RolloutProfile(gate_mode="enforce", html_normalization="tree")):
        original = gate.admit_slide_html(context(), HTML.replace("<h1>", '<h1 title="heading">')).html
        proposed = apply_fit_edits(original, [{
            "qid": deterministic_source_qid("b1"), "declarations": "font-size:20px",
        }])
        canonical = canonical_capture_html(proposed)
        first = RenderCapture.from_payload(
            html_digest=html_digest(proposed), png=b"proposal", browser_metrics=metrics(proposed),
            runtime_counters={}, fitted_html=proposed, fit_report={"changed": True},
        )
        seen = []

        async def render(html, **kwargs):
            assert html == canonical != proposed
            assert kwargs["fit"] is False and kwargs["fresh"] is True
            seen.append(html)
            return RenderCapture.from_payload(
                html_digest=html_digest(html), png=b"verified", browser_metrics=metrics(html),
                runtime_counters={},
            )

        def accept(proposal, verified, **kwargs):
            assert proposal is first and proposal.fitted_html == proposed
            assert proposal.html_digest == html_digest(proposed)
            assert verified.html_digest == html_digest(canonical)
            result = gate.admit_slide_html(
                context(html_repr="captured"), canonical,
                metrics=verified.browser_metrics, png=verified.png,
            )
            assert result.verdict == "admitted"
            return result

        monkeypatch.setattr(admission, "validate_changed_capture", lambda *args, **kwargs: None)
        monkeypatch.setattr(admission, "accept_verified_capture", accept)
        result = asyncio.run(admission.admit_local_fit_capture(
            first, original_html=original, policy={}, evaluate=lambda **kwargs: (None, None),
            render=render, render_gate=asyncio.Semaphore(1), slide_deadline=1e12,
            verify_seconds=5, deck_scope=None,
        ))
        assert seen == [canonical] and result.html == canonical


def test_capture_original_first_persists_before_starting_fit(monkeypatch) -> None:
    events: list[str] = []
    accepted = object()

    async def render(html, **kwargs):
        events.append("fit" if kwargs["fit"] else "original")
        return _capture_for(html, fit=kwargs["fit"])

    async def persist_original(capture, policy):
        assert capture.prepared_html
        assert capture.html_digest == html_digest(capture.prepared_html)
        assert policy["original_html_digest"] == capture.html_digest
        events.append("persist")

    async def admit_candidate(*_args, **kwargs):
        assert kwargs["retained_original"].prepared_html == ORIGINAL_HTML
        events.append("admit")
        return accepted

    monkeypatch.setattr(admission, "_retained_original", lambda *args, **kwargs: object())
    monkeypatch.setattr(admission, "admit_local_fit_capture", admit_candidate)

    result = asyncio.run(admission.capture_original_first(
        **_original_first_kwargs(
            render=render,
            evaluate=lambda **_kwargs: (
                object(), {"issues": [{"category": "capacity"}]},
            ),
        ),
        persist_original=persist_original,
    ))

    assert result is accepted
    assert events == ["original", "persist", "fit", "admit"]


def test_capture_original_first_reuses_persisted_passing_original() -> None:
    original = _capture_for(ORIGINAL_HTML, fit=False)

    async def never_render(*_args, **_kwargs):
        raise AssertionError("a restored original must not be rendered again")

    async def never_persist(*_args, **_kwargs):
        raise AssertionError("a restored original is already persisted")

    result = asyncio.run(admission.capture_original_first(
        **_original_first_kwargs(
            render=never_render,
            evaluate=lambda **_kwargs: (object(), None),
        ),
        persist_original=never_persist,
        restored_original=original,
    ))

    assert result.html == ORIGINAL_HTML
    assert result.png == original.png
    assert result.receipt["status"] == "unchanged"


def test_fit_timeout_uses_retained_original_without_recovery_capture() -> None:
    render_calls: list[bool] = []

    async def render(html, **kwargs):
        render_calls.append(kwargs["fit"])
        if kwargs["fit"]:
            raise TimeoutError("fit render timed out")
        return _capture_for(html, fit=False)

    result = asyncio.run(admission.capture_original_first(
        **_original_first_kwargs(
            render=render,
            evaluate=lambda **_kwargs: (
                object(), {"issues": [{"category": "capacity"}]},
            ),
        ),
    ))

    assert render_calls == [False, True]
    assert result.png
    assert result.receipt["selectionStatus"] == "preserved_original_review"
    assert result.receipt["unresolvedReasons"] == ["candidate_capture_timeout"]


def test_restored_original_with_invalid_identity_hard_fails() -> None:
    invalid = _capture_for(ORIGINAL_HTML, fit=False)
    invalid = RenderCapture.from_payload(
        html_digest=invalid.html_digest,
        png=invalid.png,
        browser_metrics=invalid.browser_metrics,
        runtime_counters={},
        fit_report=invalid.fit_report,
        prepared_html=ORIGINAL_HTML + "<!-- changed -->",
    )

    async def never_render(*_args, **_kwargs):
        raise AssertionError("invalid restored evidence must not trigger rendering")

    with pytest.raises(AuthoredLocalFitRejected, match="^original_invalid_output$"):
        asyncio.run(admission.capture_original_first(
            **_original_first_kwargs(render=never_render),
            restored_original=invalid,
        ))


def test_original_capture_retries_one_transient_timeout() -> None:
    attempts = 0

    async def render(html, **kwargs):
        nonlocal attempts
        attempts += 1
        assert kwargs["fit"] is False
        if attempts == 1:
            raise TimeoutError("transient timeout")
        return _capture_for(html, fit=False)

    result = asyncio.run(admission.capture_original_first(
        **_original_first_kwargs(
            render=render,
            evaluate=lambda **_kwargs: (object(), None),
        ),
    ))

    assert result.png
    assert attempts == 2


def test_original_capture_exposes_bounded_timeout_cause_after_retry() -> None:
    attempts = 0

    async def render(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("transient timeout")

    with pytest.raises(
        AuthoredLocalFitRejected, match="^original_capture_timeout$",
    ) as captured:
        asyncio.run(admission.capture_original_first(
            **_original_first_kwargs(render=render),
        ))

    safe = AuthoredStageError(
        slide_index=7,
        stage="local_fit",
        code="authored_local_fit_original_capture_timeout",
        cause=captured.value,
    ).as_safe_dict()
    assert attempts == 2
    assert safe["capture_cause_types"] == ["TimeoutError"]


def test_original_capture_does_not_swallow_cancellation() -> None:
    async def cancelled_render(*_args, **_kwargs):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(admission.capture_original_first(
            **_original_first_kwargs(render=cancelled_render),
        ))


def test_original_pass_with_font_evidence_rejection_runs_fitter(monkeypatch) -> None:
    render_calls: list[bool] = []
    accepted = object()

    async def render(html, **kwargs):
        render_calls.append(kwargs["fit"])
        return _capture_for(html, fit=kwargs["fit"])

    async def admit_candidate(*_args, **_kwargs):
        return accepted

    monkeypatch.setattr(admission, "_retained_original", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        admission,
        "accept_original_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AuthoredLocalFitRejected("unverifiable_font_size")
        ),
    )
    monkeypatch.setattr(admission, "admit_local_fit_capture", admit_candidate)

    result = asyncio.run(admission.capture_original_first(
        **_original_first_kwargs(
            render=render,
            evaluate=lambda **_kwargs: (object(), None),
        ),
    ))

    assert result is accepted
    assert render_calls == [False, True]


def test_unavailable_quality_still_runs_fitter(monkeypatch) -> None:
    render_calls: list[bool] = []

    async def render(html, **kwargs):
        render_calls.append(kwargs["fit"])
        return _capture_for(html, fit=kwargs["fit"])

    async def admit_candidate(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(admission, "admit_local_fit_capture", admit_candidate)

    result = asyncio.run(admission.capture_original_first(
        **_original_first_kwargs(render=render),
    ))

    assert render_calls == [False, True]
    assert result.receipt["selectionStatus"] == "preserved_original_review"
    assert result.receipt["unresolvedReasons"] == ["quality_evaluation_unavailable"]


def test_source_content_failure_skips_persist_and_fit() -> None:
    render_calls: list[bool] = []
    persisted = False

    async def render(html, **kwargs):
        render_calls.append(kwargs["fit"])
        return _capture_for(html, fit=kwargs["fit"])

    async def persist_original(*_args, **_kwargs):
        nonlocal persisted
        persisted = True

    with pytest.raises(AuthoredLocalFitRejected, match="^original_safety_failed$"):
        asyncio.run(admission.capture_original_first(
            **_original_first_kwargs(
                render=render,
                evaluate=lambda **_kwargs: (
                    object(), {"issues": [{"category": "content_integrity"}]},
                ),
            ),
            persist_original=persist_original,
        ))

    assert render_calls == [False]
    assert persisted is False


def test_pipeline_imports_original_first_checkpoint_integration() -> None:
    import services.authored_presentation_pipeline as pipeline

    assert callable(pipeline.capture_original_first)
    assert callable(pipeline.load_original_capture_checkpoint)
    assert callable(pipeline.persist_original_capture_checkpoint)


def test_original_checkpoint_roundtrip_restores_reusable_capture() -> None:
    capture = _capture_for(ORIGINAL_HTML, fit=False)
    policy = dict(capture.prepared_fit_policy or {})

    payload, _ = original_checkpoint._encode_bundle(
        slide_index=7, capture=capture, policy=policy,
    )
    restored = original_checkpoint._decode_bundle(
        payload,
        slide_index=7,
        expected_html=ORIGINAL_HTML,
        policy=policy,
    )

    assert restored is not None
    assert restored.fit_report == capture.fit_report
    accepted = admission._retained_original(
        restored,
        original_html=ORIGINAL_HTML,
        policy=policy,
        reason="candidate_capture_timeout",
    )
    assert accepted.png == capture.png
    assert accepted.receipt["unresolvedReasons"] == ["candidate_capture_timeout"]


@pytest.mark.parametrize("corruption", ["html", "policy", "png"])
def test_original_checkpoint_rejects_content_corruption(corruption: str) -> None:
    capture = _capture_for(ORIGINAL_HTML, fit=False)
    policy = dict(capture.prepared_fit_policy or {})
    payload, _ = original_checkpoint._encode_bundle(
        slide_index=7, capture=capture, policy=policy,
    )
    document = json.loads(payload)
    document.pop("checkpointDigest")
    if corruption == "html":
        document["html"] += "<!-- corrupted -->"
    elif corruption == "policy":
        document["policy"]["fit_budget_ms"] += 1
    else:
        document["pngBase64"] = base64.b64encode(capture.png + b"corrupted").decode()
    document["checkpointDigest"] = original_checkpoint._sha256(
        original_checkpoint._canonical_json(document)
    )
    corrupted = original_checkpoint._canonical_json(document)

    restored = original_checkpoint._decode_bundle(
        corrupted,
        slide_index=7,
        expected_html=ORIGINAL_HTML,
        policy=policy,
    )

    assert restored is None
