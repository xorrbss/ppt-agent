"""T2 gate hook and shared source-token context boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.source_content.placeholder.block_table import build_block_table
from services.source_content.placeholder.substitute import expand_placeholders
from services.source_content.placeholder.tree_slots import original_text_slots
from utils.authored_html_tree.parser_adapter import parse_authored_html
from services.authored_rollout_profile import current_rollout_profile

if TYPE_CHECKING:
    from services.authored_admission import TextStepResult
    from services.authored_admission_context import AdmissionContext


def placeholder_mode(context: AdmissionContext) -> bool:
    return (
        current_rollout_profile().placeholder_authoring
        and context.source_reflow and context.reference_mode == "source_content_restyle"
    )


def check_placeholder_context(context: AdmissionContext, html: str) -> None:
    """Call before rewrites can erase forbidden original token contexts."""
    if placeholder_mode(context) and context.html_repr == "token":
        original_text_slots(parse_authored_html(html, profile="document").root)


def select_text_step(context: AdmissionContext, html: str) -> TextStepResult:
    from services.authored_admission import TextStepResult, bind_current_source
    from services.source_content.browser_readback import stamp_source_image_block_ids

    if not placeholder_mode(context):
        return bind_current_source(context, html)
    # The representation is authority, not a token substring heuristic. A
    # captured source string can itself literally contain '{{q:...}}'.
    if context.html_repr != "token":
        return TextStepResult(html)
    result = expand_placeholders(html, build_block_table(context.bindings), context=context)
    expanded = result.html
    if context.image_bindings and not context.allow_illustration_slots:
        expanded = stamp_source_image_block_ids(expanded, context.image_bindings)
    return TextStepResult(expanded, normalizations=(result.receipt, *result.deviations))
