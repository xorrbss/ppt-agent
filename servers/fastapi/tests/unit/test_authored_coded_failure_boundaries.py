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
from services.authored_rollout_profile import (
    RolloutProfile,
    current_rollout_profile,
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
