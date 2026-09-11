"""Resolve per-slide prompt data from the gate's immutable server context."""

from __future__ import annotations

from services.source_content.placeholder.block_table import build_block_table, placeholder_prompt_contract
from services.source_content.source_stamp_dom import BrowserSourceReadbackError
from utils.get_env_programme import is_authored_verbatim_placeholder_authoring_enabled


class AuthoredPlaceholderPromptRejected(RuntimeError):
    """A bounded, non-retryable failure building this slide's placeholder table.

    ``build_block_table`` raises a coded ``BrowserSourceReadbackError`` (a
    ``reject()``ed token-grammar violation or duplicate id) -- the
    browser-observed *readback* pipeline's own exception shape.
    ``placeholder_prompt_contract`` raises a bare, uncoded ``ValueError`` for
    the 64,000-char table bound. Left uncaught, either propagates out of
    *prompt construction*, before any provider call, where the author stage's
    retry-and-correct loop does not expect a source-readback error -- it would
    spend a wasted correction attempt retrying a deterministic rejection the
    model cannot fix. Re-coding here at the prompt boundary keeps the original
    code where one exists and a bounded detail for diagnostics either way,
    while making the outcome explicitly non-retryable, so the author stage
    maps it directly to a coded slide outcome instead of an unexpected
    failure.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.retryable = False
        self.detail = detail[:240]
        super().__init__(self.code)


def placeholder_prompt_for_slide(index: int) -> str:
    if not is_authored_verbatim_placeholder_authoring_enabled():
        return ""
    # Lazy import avoids prompt -> admission -> author_slide -> prompt cycles.
    from services.authored_admission_session import current_session

    session = current_session()
    if session is None or not 0 <= index < len(session.contexts):
        return ""
    context = session.contexts[index]
    if not context.source_reflow or context.reference_mode != "source_content_restyle":
        return ""
    try:
        return placeholder_prompt_contract(build_block_table(context.bindings))
    except BrowserSourceReadbackError as error:
        raise AuthoredPlaceholderPromptRejected(error.code, error.detail) from error
    except ValueError as error:
        raise AuthoredPlaceholderPromptRejected(
            "authored_placeholder_prompt_rejected", str(error)
        ) from error
