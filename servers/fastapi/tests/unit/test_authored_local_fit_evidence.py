import io
from types import SimpleNamespace

import pytest
from PIL import Image

from services.authored_local_fit_evidence import (
    AuthoredLocalFitRejected,
    retain_publishable_unresolved_capture,
)
from services.authored_local_fit_policy import html_digest, local_fit_policy
from utils.slide_capture_local_fit import local_fit_render_identity


def _run50_restored_png(policy: dict) -> bytes:
    """A real PNG at the policy's export scale, as a fit-disabled render would produce."""
    manifest = policy["export_scale_manifest"]
    image = Image.new("RGB", (manifest["viewport_width"], manifest["viewport_height"]))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
        fitted_html=fitted_html,
        html_digest=revision,
        png=_run50_restored_png(policy),
        browser_metrics={
            "elements": [],
            "local_fit_mode": False,
            "local_fit_render_identity": local_fit_render_identity(
                original_html, policy, fit=False,
            ),
        },
        fit_report={
            "status": "unresolved",
            "requested": False,
            "fit_state": "final",
            "policy_revision": policy["policy_revision"],
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


def test_retain_publishable_unresolved_capture_keeps_any_fitter_reason_verbatim() -> None:
    # The 2026-09-09 revision (docs/authored-local-repair-design-20260909.md) widened
    # the narrow PUBLISHABLE_VISUAL_REASONS allow-list into a general rule: whenever
    # the fit-disabled original renders, it is kept as a warning and the fitter's
    # own reason travels with it unchanged. An unknown reason is therefore not a
    # rejection any more; the receipt must carry it as-is so the count stays honest.
    capture, original_html, policy = _run50_restored_capture(reason="unexpected_fit_reason")

    accepted = retain_publishable_unresolved_capture(
        capture, original_html=original_html, policy=policy,
    )

    assert accepted.html == original_html
    assert accepted.receipt["qualityStatus"] == "warning"
    assert accepted.receipt["selectionStatus"] == "preserved_original_review"
    assert accepted.receipt["unresolvedReasons"] == ["unexpected_fit_reason"]
