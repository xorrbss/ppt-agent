"""Resolve per-slide prompt data from the gate's immutable server context."""

from __future__ import annotations

from services.source_content.placeholder.block_table import build_block_table, placeholder_prompt_contract
from utils.get_env_programme import is_authored_verbatim_placeholder_authoring_enabled


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
    return placeholder_prompt_contract(build_block_table(context.bindings))
