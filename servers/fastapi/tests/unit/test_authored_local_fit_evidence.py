from types import SimpleNamespace

import pytest

from services.authored_local_fit_evidence import (
    AuthoredLocalFitRejected,
    retain_publishable_unresolved_capture,
)
from services.authored_local_fit_policy import html_digest, local_fit_policy


def _run50_restored_capture(*, reason: str = "parent_region_unavailable", fitted_html: str | None = None):
    original_html = '<html><body><main data-qid="s12-content">run50 slide 13</main></body></html>'
    revision = html_digest(original_html)
    policy = local_fit_policy(
        author_revision=revision,
        original_html_digest=revision,
        slide_index=12,
    )
    run = {"run_id": "s12-copy-pairing", "original_pt": 18.0, "final_pt": 18.0}
    capture = SimpleNamespace(
        fitted_html=original_html if fitted_html is None else fitted_html,
        png=b"\x89PNG\r\n\x1a\nrun50",
        browser_metrics={"elements": [], "local_fit_render_identity": "sha256:run50-restored"},
        fit_report={
            "status": "unresolved",
            "unresolved_reasons": [reason],
            "partial": False,
            "error": None,
            "changed": False,
            "operations": [],
            "edits": [],
            "baseline": {
                "complete": True,
                "author_revision": revision,
                "original_html_digest": revision,
                "runs": [run],
            },
            "runs": [run],
        },
    )
    return capture, original_html, policy


def test_run50_parent_region_unavailable_keeps_a_restored_capture_as_a_warning() -> None:
    capture, original_html, policy = _run50_restored_capture()

    accepted = retain_publishable_unresolved_capture(
        capture, original_html=original_html, policy=policy,
    )

    assert accepted.html == original_html
    assert accepted.png == capture.png
    assert accepted.receipt["changed"] is False
    assert accepted.receipt["verifiedFitDisabled"] is False
    assert accepted.receipt["qualityStatus"] == "warning"
    assert accepted.receipt["unresolvedReasons"] == ["parent_region_unavailable"]
    assert accepted.receipt["operations"] == []
    assert accepted.receipt["baseline"]["complete"] is True


def test_retain_publishable_unresolved_capture_rejects_a_restoration_html_mismatch() -> None:
    capture, original_html, policy = _run50_restored_capture(
        fitted_html="<html><body>reconstructed differently</body></html>",
    )

    with pytest.raises(AuthoredLocalFitRejected, match="^reconstruction_mismatch$"):
        retain_publishable_unresolved_capture(
            capture, original_html=original_html, policy=policy,
        )


def test_retain_publishable_unresolved_capture_rejects_an_unknown_visual_reason() -> None:
    capture, original_html, policy = _run50_restored_capture(reason="unexpected_fit_reason")

    with pytest.raises(AuthoredLocalFitRejected, match="^unexpected_fit_reason$"):
        retain_publishable_unresolved_capture(
            capture, original_html=original_html, policy=policy,
        )
