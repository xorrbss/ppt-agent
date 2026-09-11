"""One switch for how much an ordinary authored deck is allowed to carry.

Between 2026-08-28 and 2026-09-03 the ordinary authored deck acquired a set of
hard density ceilings it did not have before: a server-frozen composition
envelope (per-role character and semantic-target budgets, a 15px floor), a
blocking DOM quality gate, and a deck-wide figure plan drawn from a component
library. Each of those was later given its own boolean, and two of the three
now default off -- but there was no single, named way to ask for "the deck the
way August authored it", so the A/B the ceilings were meant to be judged
against could not be run.

This module is that switch. ``AUTHORED_COMPOSITION_ENVELOPE_PROFILE`` resolves
once, to an immutable :class:`CompositionEnvelopeProfile`, which is then read
by the composition policy (envelope enforcement, strict DOM gate) and by the
deck plans (figure plan). ``current`` is the default and states the existing
behaviour exactly: every field either repeats today's constant or defers to the
per-rule knob that already decided it, so resolving the profile changes
nothing. ``august`` is the pre-2026-08-28 contract for ORDINARY decks.

Scope, deliberately: ``august`` binds ordinary decks only. ``source-verbatim``
and ``source-outline`` keep their envelope and their gate under both profiles,
because theirs is a fidelity contract -- the envelope is what makes an
impossible source page fail closed instead of silently losing source text --
and not the density preference this switch is about.

Design: ``docs/authored-composition-envelope-profiles-design-20260912.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Optional

from utils.get_env import (
    is_authored_composition_envelope_enabled,
    is_authored_infographic_plan_enabled,
)
from utils.get_env_programme import get_authored_composition_envelope_profile
from utils.runtime_settings import RuntimeSettings

EnvelopeProfileName = Literal["current", "august"]


@dataclass(frozen=True, slots=True)
class CompositionEnvelopeProfile:
    """The density contract one generation is authored under.

    Every field is scoped to the ordinary deck. ``None`` means "this profile
    states nothing; defer to the per-rule knob that already decided it", which
    is how ``current`` stays byte-identical to the behaviour it replaces.
    """

    name: EnvelopeProfileName
    #: Bind ordinary decks to the server-frozen text/qid/font envelope.
    ordinary_envelope_enforced: Optional[bool]
    #: Run the blocking authored DOM quality gate on ordinary decks.
    ordinary_strict_dom_gate: bool
    #: Resolve the deck-wide figure plan and draw from the component library.
    ordinary_infographic_plan: Optional[bool]


#: Today's behaviour, stated as a profile. Nothing here is a new decision.
CURRENT_ENVELOPE_PROFILE = CompositionEnvelopeProfile(
    name="current",
    ordinary_envelope_enforced=None,
    ordinary_strict_dom_gate=True,
    ordinary_infographic_plan=None,
)

#: The ordinary deck as it was authored before 2026-08-28: the model owns the
#: composition, no server budget is stated or checked, DOM findings are not a
#: publication gate, and no figure is planned for it deck-wide.
AUGUST_ENVELOPE_PROFILE = CompositionEnvelopeProfile(
    name="august",
    ordinary_envelope_enforced=False,
    ordinary_strict_dom_gate=False,
    ordinary_infographic_plan=False,
)

_PROFILES: Mapping[str, CompositionEnvelopeProfile] = {
    CURRENT_ENVELOPE_PROFILE.name: CURRENT_ENVELOPE_PROFILE,
    AUGUST_ENVELOPE_PROFILE.name: AUGUST_ENVELOPE_PROFILE,
}


#: Per-role ``(title, body, visible, qids)`` budgets for a BOUND envelope.
#:
#: These numbers are shared by both profiles and are not part of the switch.
#: There are no "August budgets" to restore: before 2026-08-28 no envelope
#: existed at all, so what ``august`` changes is whether the envelope binds an
#: ordinary deck, never what its numbers are. Under a bound envelope they still
#: apply, and the source-fidelity profiles read them under both profiles.
_ROLE_BUDGETS: dict[str, tuple[int, int, int, int]] = {
    # Cover slides commonly carry presenter/date metadata plus one compact
    # evidence panel. Count browser-owned content targets, not layout wrappers.
    "COVER": (58, 220, 280, 22),
    # Closing slides combine the final claim, release rule, and audit/operations
    # copy. Chromium geometry remains the hard overflow authority.
    # Dense Korean proposal styles use many short editable labels rather than
    # a few long paragraphs.  Keep the 15px/Chromium geometry gates as the
    # visual authority, but do not reject those legitimate semantic owners
    # before the first render merely because the generic prose envelope was
    # tuned for sparse keynote layouts.
    "CLOSING": (62, 380, 460, 36),
    "SECTION": (46, 150, 220, 12),
    "AGENDA": (58, 330, 420, 36),
    "PROBLEM": (64, 380, 460, 40),
    "PILLARS": (58, 380, 460, 40),
    "OUTCOMES": (54, 340, 420, 36),
    "ROADMAP": (56, 360, 450, 40),
    "ARCHITECTURE": (54, 390, 460, 48),
}

#: The budget for a role the table does not name.
DEFAULT_ROLE_BUDGET: tuple[int, int, int, int] = (60, 380, 460, 40)


def role_budget(role: str) -> tuple[int, int, int, int]:
    """The ``(title, body, visible, qids)`` budget for one outline role."""

    normalized = (role or "").strip().upper()
    return _ROLE_BUDGETS.get(normalized, DEFAULT_ROLE_BUDGET)


def resolve_envelope_profile(
    settings: RuntimeSettings | None = None,
) -> CompositionEnvelopeProfile:
    """Resolve the envelope profile for one operation.

    This is a pure read of an already-loaded settings snapshot, so resolving it
    twice inside one generation cannot yield two different profiles; the
    generation still resolves it once, alongside the composition profile, and
    passes the object down.
    """

    return _PROFILES[get_authored_composition_envelope_profile(settings)]


def ordinary_envelope_enforced(
    envelope: CompositionEnvelopeProfile,
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether an ordinary deck is bound by the server-frozen envelope."""

    if envelope.ordinary_envelope_enforced is None:
        return is_authored_composition_envelope_enabled(settings)
    return envelope.ordinary_envelope_enforced


def ordinary_infographic_plan_enabled(
    envelope: CompositionEnvelopeProfile,
    settings: RuntimeSettings | None = None,
) -> bool:
    """Whether an ordinary deck resolves a deck-wide figure plan."""

    if envelope.ordinary_infographic_plan is None:
        return is_authored_infographic_plan_enabled(settings)
    return envelope.ordinary_infographic_plan


__all__ = [
    "AUGUST_ENVELOPE_PROFILE",
    "CURRENT_ENVELOPE_PROFILE",
    "CompositionEnvelopeProfile",
    "DEFAULT_ROLE_BUDGET",
    "EnvelopeProfileName",
    "ordinary_envelope_enforced",
    "ordinary_infographic_plan_enabled",
    "resolve_envelope_profile",
    "role_budget",
]
