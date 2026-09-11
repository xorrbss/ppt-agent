"""The receipt must carry the fitter's own unresolved reason, not a blanket label."""

import asyncio
from types import SimpleNamespace

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
    capture = SimpleNamespace(
        fitted_html=ORIGINAL_HTML,
        html_digest=revision,
        png=b"\x89PNG\r\n\x1a\nunresolved",
        browser_metrics={"elements": [], "local_fit_mode": True},
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
