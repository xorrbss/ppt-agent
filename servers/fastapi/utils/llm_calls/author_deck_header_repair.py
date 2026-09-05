"""H2: spend a deterministic header repair before spending a re-author call.

``docs/authored-quality-speed-programme-design-20260905.md`` section 6 (H2). A
header-contract violation used to cost the slide its one correction call, and a
second violation killed the deck (``authored_header_contract_budget_exhausted``)
for drift no reader would notice -- a band one pixel off its declared height, a
duplicated part, an unpinned body wrapper. ``utils.authored_header_repair``
fixes exactly those deterministically; only what survives the repair is worth a
provider call.

The repair module is imported lazily so this wiring is inert (and testable)
wherever it is not present.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from utils.get_env_programme import is_authored_header_deterministic_repair_enabled

logger = logging.getLogger(__name__)


def _record(receipt: list[dict] | None, codes: Sequence[str]) -> None:
    if receipt is None or not codes:
        return
    receipt.append(
        {"rule": "header_repair:" + ",".join(sorted(codes)), "count": len(codes)}
    )


_EYEBROW_PREFIX_MISSING = "header_eyebrow_prefix_missing"


def _repair_eyebrow_prefix(
    html: str,
    contract: Any,
    *,
    role: str,
    content: str,
    chapter_prefix: str | None,
) -> str:
    """Re-insert a dropped chapter prefix, then re-validate against it.

    Only called when ``header_eyebrow_prefix_missing`` survived
    ``repair_header_contract`` above -- that module has no view of the
    chapter plan, so it can never fix this one on its own. A failure here is
    swallowed the same way a failure in the main repair is: a repair may
    never fail the slide, it may only fail to help it.
    """
    if not chapter_prefix:
        return html
    try:
        from utils.authored_header_eyebrow_repair import repair_eyebrow_prefix
        from utils.authored_header_restyle import header_qids
        from utils.authored_header_validation import validate_header_contract
    except ImportError:
        return html
    try:
        eyebrow_qid = header_qids(html).parts.get("eyebrow")
        repaired, changed = repair_eyebrow_prefix(
            html, chapter_prefix=chapter_prefix, eyebrow_qid_or_locator=eyebrow_qid
        )
        if not changed:
            return html
        after = validate_header_contract(
            repaired,
            contract,
            role=role,
            content=content,
            chapter_prefix=chapter_prefix,
        )
    except Exception:  # noqa: BLE001 -- a repair may never fail the slide
        logger.warning("authored header eyebrow repair failed", exc_info=True)
        return html
    if _EYEBROW_PREFIX_MISSING in after:
        # The insertion did not actually clear the violation (an unexpected
        # shape the validator does not recognise as the prefix) -- keep the
        # candidate the model wrote rather than one edited for nothing.
        return html
    return repaired


def repair_header_violations(
    html: str,
    contract: Any,
    *,
    role: str,
    content: str = "",
    violations: Sequence[str],
    receipt: list[dict] | None = None,
    chapter_prefix: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Return the repaired HTML and the violations a provider call must still fix.

    ``violations`` is the already-computed validation result, so the caller
    never validates twice for nothing. The returned violations are the input
    ones unchanged whenever the knob is off, the repair module is absent, or the
    repair changed nothing.

    ``chapter_prefix`` is this slide's frozen chapter-breadcrumb opener (see
    ``AuthoredChapterPlan.eyebrow_prefix_at``); it is only consulted when
    ``header_eyebrow_prefix_missing`` is among the violations, since that is
    the one violation ``repair_header_contract`` cannot fix on its own.
    """

    remaining = tuple(violations)
    if not remaining or contract is None:
        return html, remaining
    if not is_authored_header_deterministic_repair_enabled():
        return html, remaining
    try:
        from utils.authored_header_repair import repair_header_contract
    except ImportError:
        return html, remaining

    # ``repair_header_contract`` recomputes its own violations without ever
    # being told about the chapter plan, so its ``remaining`` can never carry
    # this code either way -- it would otherwise look silently fixed by a
    # repair that cannot touch the eyebrow at all. Track it independently and
    # only clear it once the eyebrow step below actually resolves it.
    eyebrow_missing = _EYEBROW_PREFIX_MISSING in remaining

    try:
        result = repair_header_contract(html, contract, role=role, content=content)
    except Exception:  # noqa: BLE001 -- a repair may never fail the slide
        logger.warning("authored header deterministic repair failed", exc_info=True)
        return html, remaining

    if getattr(result, "changed", False):
        current_html = getattr(result, "html", html)
        fixed = tuple(getattr(result, "fixed", ()) or ())
        # ``remaining`` is the repairer's own fresh validation of the repaired
        # document, so it is authoritative for every code it knows about;
        # nothing is validated twice here.
        revalidated = tuple(getattr(result, "remaining", remaining))
        if eyebrow_missing and _EYEBROW_PREFIX_MISSING not in revalidated:
            revalidated = (*revalidated, _EYEBROW_PREFIX_MISSING)
    else:
        current_html, fixed, revalidated = html, (), remaining

    if eyebrow_missing:
        repaired_html = _repair_eyebrow_prefix(
            current_html,
            contract,
            role=role,
            content=content,
            chapter_prefix=chapter_prefix,
        )
        if repaired_html != current_html:
            current_html = repaired_html
            fixed = (*fixed, _EYEBROW_PREFIX_MISSING)
            revalidated = tuple(
                code for code in revalidated if code != _EYEBROW_PREFIX_MISSING
            )

    if len(revalidated) >= len(remaining):
        # The repair did not reduce the contract debt; keep the original
        # candidate so the correction call sees what the model actually wrote.
        return html, remaining
    _record(receipt, fixed)
    logger.info(
        "authored_header_repair fixed %s; remaining %s",
        ",".join(fixed) or "-",
        ",".join(revalidated) or "-",
    )
    return current_html, revalidated


__all__ = ["repair_header_violations"]
