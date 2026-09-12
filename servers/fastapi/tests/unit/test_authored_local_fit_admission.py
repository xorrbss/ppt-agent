"""The receipt must carry the fitter's own unresolved reason, not a blanket label."""

import asyncio
from utils.slide_capture_result import RenderCapture

import pytest

import services.authored_local_fit_admission as admission
from services.authored_local_fit_evidence import (
    AuthoredLocalFitRejected,
    accept_unchanged_capture,
)
from services.authored_local_fit_policy import html_digest, local_fit_policy

ORIGINAL_HTML = '<html><body><main data-qid="s7-cards">slide 8</main></body></html>'


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
