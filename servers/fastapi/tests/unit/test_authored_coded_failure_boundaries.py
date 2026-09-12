"""A coded authored failure must not lose its code when it crosses a boundary.

``admit_slide_html`` classifies by exception type: ``RendererPolicyError`` and
``BrowserSourceReadbackError`` carry a ``.code``, and the ``except ValueError``
tail re-raises anything that does not.  Re-raising is correct -- an unclassified
error is not a slide rejection -- but it means that any coded failure raised as a
plain ``ValueError`` is reported to the recovery layers as an uncoded
``authored_unexpected_failure`` instead of the code it actually carried.

Tree normalization raises exactly such a failure: ``RawAuthoredHtmlViolation``
is a ``ValueError`` built from a list of coded findings (R1 of
``artifacts/impl-review-20260911/rereview-normalization-provider-placeholder.md``).
The test below drives the product path that reaches it rather than the exception
class directly, so the assertion is about the boundary, not the constructor.
"""

from __future__ import annotations

import pytest

from services import authored_admission as gate
from services.authored_admission_context import context_from_source
from services.authored_admission_policy import AuthoredConfigurationError
from services.authored_rollout_profile import (
    RolloutProfile,
    RolloutProfileError,
    current_rollout_profile,
    resolve_rollout_profile,
    rollout_profile_scope,
)
from services.source_content.browser_readback import BrowserSourceReadbackError

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
HTML = (
    "<!doctype html><html><head><style>body{margin:0}</style></head>"
    "<body><h1>원문 제목</h1></body></html>"
)


def context(**kwargs):
    values = dict(
        presentation_id="p1",
        operation_id="op1",
        slide_index=0,
        source_unit_id="u1",
        policy_revision=current_rollout_profile().policy_revision,
        fenced_revision=4,
        attempt_token="attempt-1",
        reference_mode="source_content_restyle",
        source_reflow=True,
        terminal_fallback_eligible=False,
        bindings=(("b1", "원문 제목"),),
    )
    values.update(kwargs)
    return context_from_source(manifest=MANIFEST, projection=OUTLINE, **values)


def test_a_raw_violation_raised_after_expansion_keeps_its_code(monkeypatch):
    """The post-expansion policy pass is where inserted source text overflows.

    The gate's own raw step already cleared the model's bytes, so the first
    place a renderer limit can be crossed is the second
    ``validate_authored_slide_html``, which runs on the *expanded* document.  In
    tree mode that call diagnoses raw findings inside the normalizer and raises
    ``RawAuthoredHtmlViolation``.  ``MAX_TEXT_BYTES`` is 1 MiB and
    ``MAX_HTML_BYTES`` 2 MiB, so the expansion below crosses the text ceiling
    while staying a document the size check admits -- the exact shape R1 names.
    """

    oversized = HTML.replace("원문 제목", "원문 제목" + "가" * 400_000)
    monkeypatch.setattr(gate, "text_step", lambda ctx, html: gate.TextStepResult(oversized))
    with rollout_profile_scope(RolloutProfile(html_normalization="tree")):
        admission = gate.admit_slide_html(context(html_repr="token"), HTML)

    assert admission.verdict == "rejected"
    assert admission.predicate == "post_expansion_policy"
    assert admission.code == "html_text_limit_exceeded"
    assert admission.receipt.as_dict()["code"] == "html_text_limit_exceeded"

    # And across the second boundary: the one place a verdict becomes an
    # exception, which is what every recovery lane actually reads.
    with pytest.raises(BrowserSourceReadbackError) as raised:
        admission.require()
    assert raised.value.code == "html_text_limit_exceeded"


def test_an_incoherent_rollout_profile_surfaces_as_a_configuration_failure(monkeypatch):
    """An operator's knob mistake must not be reported as a slide rejection.

    ``AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP`` is only coherent under
    ``gate_mode=enforce`` (``_require_coherent_switches``).  Left at the
    default ``shadow`` gate mode, this combination makes ``resolve_rollout_profile``
    raise ``RolloutProfileError`` -- a :class:`AuthoredConfigurationError` --
    the moment something calls ``current_rollout_profile()`` with no profile
    already pinned to the attempt, exactly as ``raw_diagnostics_step`` does.

    Before this fix, that error carried a ``.code`` like any coded content
    violation, so ``admit_slide_html``'s ``except ValueError`` swallowed it
    into ``Admission(verdict="rejected", retryable=False)`` -- a config mistake
    reported as every slide being permanently, non-retryably wrong. It must
    instead propagate out of ``admit_slide_html`` unmodified, so the caller
    sees the run itself could not start.
    """

    monkeypatch.setenv("AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP", "true")
    monkeypatch.delenv("AUTHORED_ADMISSION_GATE_MODE", raising=False)

    with pytest.raises(RolloutProfileError):
        resolve_rollout_profile()

    with pytest.raises(RolloutProfileError) as raised:
        gate.admit_slide_html(context(html_repr="token"), HTML)
    assert raised.value.code == "authored_rollout_profile_invalid"


def test_a_future_configuration_error_is_never_classified_as_content(monkeypatch):
    """The boundary is a marker check, not a list of exception classes.

    This is the general guard the report asked for: any exception meaning
    "this run cannot proceed" is recognized by carrying
    :class:`AuthoredConfigurationError`, regardless of where in the admission
    call graph it is raised or whether admission's code has ever heard of its
    concrete type.  A dummy configuration error, raised from an arbitrary step
    ``admit_slide_html`` calls, must propagate exactly like ``RolloutProfileError``
    does -- proving a *new* exception type joining this family needs no edit to
    the classification block to stay out of the content branch.
    """

    class _FutureConfigurationError(AuthoredConfigurationError, ValueError):
        code = "some_future_configuration_code"

    monkeypatch.setattr(
        gate,
        "validate_authored_slide_html",
        lambda *a, **k: (_ for _ in ()).throw(_FutureConfigurationError("nope")),
    )

    with rollout_profile_scope(RolloutProfile()):
        with pytest.raises(_FutureConfigurationError):
            gate.admit_slide_html(context(), HTML)


def test_a_plain_coded_content_violation_still_lands_as_rejected(monkeypatch):
    """A content violation that is not a configuration error is unaffected.

    Guards against the fix over-widening: an ordinary ``ValueError`` with a
    ``.code`` that does *not* carry the configuration marker is exactly the
    shape the ``except ValueError`` tail exists for, and must keep landing as
    a coded, non-retryable rejection.
    """

    class _ContentViolation(ValueError):
        code = "some_content_violation_code"

    monkeypatch.setattr(
        gate,
        "validate_authored_slide_html",
        lambda *a, **k: (_ for _ in ()).throw(_ContentViolation("bad content")),
    )

    with rollout_profile_scope(RolloutProfile()):
        admission = gate.admit_slide_html(context(), HTML)

    assert admission.verdict == "rejected"
    assert admission.code == "some_content_violation_code"
    assert admission.retryable is False
