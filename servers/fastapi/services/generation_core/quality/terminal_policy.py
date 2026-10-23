"""TerminalPolicy: what a deck does with a page that ran out of attempts.

Until this seam existed the SVG driver had exactly one answer -- the first page
that exhausted its attempts cancelled every sibling and failed the deck -- while
the authored (HTML) driver has published a flagged page instead since D7
(``terminal_slide_policy.terminal_fallback_active``). The decision of *whether*
a deck class may publish a flagged page is the same rollout switch for both
drivers (``terminal_slide_policy.publish_flagged_allowed``); this module turns
it into the outcomes a page collector can act on:

* ``fallback``  -- rescue the page (``drivers/svg/terminal.py``): keep its last
  render when only its layout failed, else publish a deterministic page with the
  slide's whole outline, else a failure placeholder -- always flagged;
* ``flag_only`` -- the same rescue for a source-reflow deck, whose verbatim text
  may not be re-authored: keep the last verbatim render, else a plain verbatim
  layout of the page's source units, else a failure placeholder;
* ``fail``      -- fail-closed: an exact reconstruction, or a rollout switch off.

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

    Mirrors the authored split: an ordinary deck may re-lay a terminal page
    from its outline (authored: remake, then placeholder), a source-reflow deck
    only from its verbatim source units (authored: verbatim flag-only), and an
    exact reconstruction never publishes a substitute.
    """

    if not publish_flagged_allowed(
        source_reflow=source_reflow, exact_reconstruction=exact_reconstruction
    ):
        return TerminalPolicy.FAIL
    return TerminalPolicy.FLAG_ONLY if source_reflow else TerminalPolicy.FALLBACK


__all__ = ["TerminalPolicy", "resolve_terminal_policy"]
