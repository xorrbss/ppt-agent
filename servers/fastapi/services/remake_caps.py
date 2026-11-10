"""Remake budgets shared by generation, admission deadlines and receipts.

Moved unchanged from ``authored_terminal_slide_fallback``, which enforces them
and re-exports both names, so the public deadline policy and the timing
receipt read them without importing the fallback (and its capture stack).
"""

from __future__ import annotations

#: Remake attempts one deck may spend, shared by the remake-first round and
#: the terminal round. Each one is a full authoring call, so a deck whose
#: gate flagged a dozen slides must not turn into a dozen sequential provider
#: calls. Slides past the budget are flagged with ``remake_budget_exhausted``
#: rather than dropped.
MAX_TERMINAL_REMAKES_PER_DECK = 8

#: Remake attempts one slide may spend across those two rounds. Bench run 10
#: measured the single remake as the binding limit: 4 of 8 terminal slides
#: were published flagged with the budget already spent.
MAX_REMAKES_PER_SLIDE = 2
