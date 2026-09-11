"""The one point that selects which admission profile a document is judged by.

``utils.renderer_isolation`` holds three fail-closed policies, not one: the
strict authored policy the model writes against, the source-bound internal
probe, and the static-output policy that renderer-produced documents are
written against.  Choosing between them is a decision, and it has to be *one*
decision.  When the renderer picked the static-output policy for a document and
the checkpoint gate re-judged the same bytes under the authored policy, a deck
could render successfully and then be refused at its first checkpoint write --
the gate rejecting bytes its own renderer had just accepted.

Every caller that needs to know "which policy applies to these exact bytes"
calls :func:`select_render_profile`, so "rendered" implies "admissible" for
identical bytes by construction rather than by review.

The capability for the internal probe is passed as a callable because it is
single-use: only the renderer, which issued it, may consume it, and only after
the authored policy has already refused.  Callers that merely ask which policy
applies pass nothing and therefore consume nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from utils.renderer_isolation import (
    AdmittedHtml,
    RendererPolicyError,
    admit_authored_html,
    admit_internal_instrumented_html,
    admit_static_output_html,
)


AUTHORED_PROFILE = "authored"
INTERNAL_INSTRUMENTED_PROFILE = "internal_instrumented"
STATIC_OUTPUT_PROFILE = "static_output"


@dataclass(frozen=True)
class RenderProfile:
    """The policy that admits ``source``, and what that policy made of it."""

    name: str
    admitted: AdmittedHtml | None
    error: RendererPolicyError | None = None

    @property
    def renderable(self) -> bool:
        return self.admitted is not None

    def require(self) -> AdmittedHtml:
        """Return the admitted document, or re-raise the policy's own refusal."""
        if self.admitted is None:
            raise self.error if self.error is not None else RendererPolicyError(
                "no renderer admission profile accepted the source"
            )
        return self.admitted


def select_render_profile(
    source: str, *, internal_capability: Callable[[], str | None] | None = None
) -> RenderProfile:
    """Select the fail-closed admission profile for the exact in-memory source.

    Order is the renderer's own: the strict authored policy first, then the
    source-bound internal probe, then the static-output policy.  A document
    that no profile admits is reported under the profile that refused it last,
    which keeps every caller failing closed on the same error it would have
    seen before this selection was shared.
    """

    try:
        return RenderProfile(AUTHORED_PROFILE, admit_authored_html(source))
    except RendererPolicyError as authored_error:
        refusal = authored_error
    capability = internal_capability() if internal_capability is not None else None
    if capability is not None:
        return RenderProfile(
            INTERNAL_INSTRUMENTED_PROFILE,
            admit_internal_instrumented_html(source, capability),
        )
    try:
        return RenderProfile(STATIC_OUTPUT_PROFILE, admit_static_output_html(source))
    except RendererPolicyError as static_error:
        refusal = static_error
    return RenderProfile(AUTHORED_PROFILE, None, refusal)


__all__ = [
    "AUTHORED_PROFILE",
    "INTERNAL_INSTRUMENTED_PROFILE",
    "STATIC_OUTPUT_PROFILE",
    "RenderProfile",
    "select_render_profile",
]
