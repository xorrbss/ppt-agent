"""TerminalPolicy: what a deck does with a page that ran out of attempts.

Until this seam existed the SVG driver had exactly one answer -- the first page
that exhausted its attempts cancelled every sibling and failed the deck -- while
the authored (HTML) driver has published a flagged page instead since D7
(``terminal_slide_policy.terminal_fallback_active``). The decision of *whether*
a deck class may publish a flagged page is the same rollout switch for both
drivers (``terminal_slide_policy.publish_flagged_allowed``); this module turns
it into the three outcomes a page collector can act on:

* ``fallback``  -- publish a deterministic page rendered from the slide's own
  outline in place of the one that could not be authored, flagged;
* ``flag_only`` -- publish the last page that rendered, unchanged, flagged
  (a source-reflow deck: its verbatim text may not be re-authored);
* ``fail``      -- today's fail-closed behaviour.

The policy is resolved once per deck attempt into the runtime profile
(``generation_core/runtime/profile.py``); the authored driver keeps its own
resolution through ``terminal_fallback_active``, which reads the same switch.
"""

from __future__ import annotations

from enum import Enum

from services.generation_core.quality.terminal_slide_policy import publish_flagged_allowed


class TerminalPolicy(str, Enum):
    FALLBACK = "fallback"
    FLAG_ONLY = "flag_only"
    FAIL = "fail"


def resolve_terminal_policy(*, source_reflow: bool, exact_reconstruction: bool) -> TerminalPolicy:
    """The deck's terminal-page policy, from the shared rollout switches.

    Mirrors the authored split: an ordinary deck rescues a terminal page with a
    fallback page (authored: remake, then placeholder), a source-reflow deck
    may only flag the page it has (authored: verbatim flag-only), and an exact
    reconstruction never publishes a substitute.
    """

    if not publish_flagged_allowed(
        source_reflow=source_reflow, exact_reconstruction=exact_reconstruction
    ):
        return TerminalPolicy.FAIL
    return TerminalPolicy.FLAG_ONLY if source_reflow else TerminalPolicy.FALLBACK


__all__ = ["TerminalPolicy", "resolve_terminal_policy"]
