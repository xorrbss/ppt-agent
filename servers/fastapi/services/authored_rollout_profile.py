"""One frozen resolution of the authored rollout switches per operation attempt.

The five switches of the 2026-09-11 verbatim programme -- the admission gate
mode, HTML normalization, placeholder authoring, the single repair loop and its
model-call budget -- used to be read live, at every call, from
``utils.get_env_programme``.  That made a mid-flight flip legal: a deck could
author twenty slides under ``legacy`` normalization, an operator could switch to
``tree``, and the remaining slides were a different representation of the same
deck while every receipt claimed one policy revision
(``artifacts/impl-review-20260911/rereview-normalization-provider-placeholder.md``
R7, and B4 of the review it re-reviews).

This module resolves the five values **once**, when an operation attempt starts,
and freezes them into an immutable :class:`RolloutProfile`.  The attempt carries
the profile in a context variable and in its ``AdmissionSession``; every
consumer reads :func:`current_rollout_profile` instead of an accessor.  The
accessors survive only as this module's source.

The profile is durable: it is written into the generation job's request payload
when the job is staged and restored on every later attempt, so a resumed attempt
runs the switches its earlier attempts ran.  A payload that does not describe a
usable profile is a coded rejection, never a silent fall back to the live
environment -- see :func:`rollout_profile_from_payload`.

The profile also names itself in every receipt: ``policy_revision`` is the
admission policy's content digest plus this profile's digest, so a receipt
minted under one set of switches can never read back as publishable under
another.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from services.authored_admission_policy import (
    POLICY_REVISION_MISMATCH,
    ROLLOUT_PROFILE_INVALID,
    ROLLOUT_PROFILE_MISMATCH,
    policy_revision_for,
)
from utils.get_env_programme import (
    get_authored_admission_gate_mode,
    get_authored_html_normalization,
    get_authored_verbatim_repair_loop_model_calls,
    is_authored_verbatim_placeholder_authoring_enabled,
    is_authored_verbatim_single_repair_loop_enabled,
)

GATE_MODES = ("shadow", "enforce")
NORMALIZATIONS = ("legacy", "tree")
MAX_REPAIR_LOOP_MODEL_CALLS = 3
#: Durable key under which a staged generation job carries its pinned profile.
ROLLOUT_PROFILE_PAYLOAD_KEY = "_internal_authored_rollout_profile"


class RolloutProfileError(ValueError):
    """A coded, non-retryable rollout-profile failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail[:240]
        self.retryable = False
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RolloutProfile:
    """The five switches, as one immutable value an attempt is pinned to."""

    gate_mode: str = "shadow"
    html_normalization: str = "legacy"
    placeholder_authoring: bool = False
    single_repair_loop: bool = False
    repair_loop_model_calls: int = 1

    def __post_init__(self) -> None:
        if self.gate_mode not in GATE_MODES:
            raise RolloutProfileError(ROLLOUT_PROFILE_INVALID, f"gate_mode={self.gate_mode!r}")
        if self.html_normalization not in NORMALIZATIONS:
            raise RolloutProfileError(
                ROLLOUT_PROFILE_INVALID, f"html_normalization={self.html_normalization!r}"
            )
        for name in ("placeholder_authoring", "single_repair_loop"):
            if not isinstance(getattr(self, name), bool):
                raise RolloutProfileError(ROLLOUT_PROFILE_INVALID, name)
        calls = self.repair_loop_model_calls
        if not isinstance(calls, int) or isinstance(calls, bool):
            raise RolloutProfileError(ROLLOUT_PROFILE_INVALID, "repair_loop_model_calls")
        if not 0 <= calls <= MAX_REPAIR_LOOP_MODEL_CALLS:
            raise RolloutProfileError(ROLLOUT_PROFILE_INVALID, f"repair_loop_model_calls={calls}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def policy_revision(self) -> str:
        return policy_revision_for(self.digest)


def rollout_profile_from_payload(payload: Any) -> RolloutProfile:
    """Restore a pinned profile, refusing anything that is not one.

    A resumed attempt must run the switches its predecessor ran.  A payload that
    is not a mapping, or that carries a field the current build no longer
    accepts, is a coded rejection: falling back to the live environment here is
    exactly the mid-flight representation mix the pin exists to prevent.
    """

    if not isinstance(payload, Mapping):
        raise RolloutProfileError(ROLLOUT_PROFILE_INVALID, f"payload={type(payload).__name__}")
    fields = {field.name for field in RolloutProfile.__dataclass_fields__.values()}
    unknown = sorted(set(payload) - fields)
    if unknown:
        raise RolloutProfileError(ROLLOUT_PROFILE_INVALID, f"unknown={unknown[0]}")
    return RolloutProfile(**{key: payload[key] for key in payload})


def resolve_rollout_profile() -> RolloutProfile:
    """Read the five accessors once. The only live environment read there is."""
    return RolloutProfile(
        gate_mode=get_authored_admission_gate_mode(),
        html_normalization=get_authored_html_normalization(),
        placeholder_authoring=is_authored_verbatim_placeholder_authoring_enabled(),
        single_repair_loop=is_authored_verbatim_single_repair_loop_enabled(),
        repair_loop_model_calls=get_authored_verbatim_repair_loop_model_calls(),
    )


_PROFILE: ContextVar[RolloutProfile | None] = ContextVar("authored_rollout_profile", default=None)


def pinned_rollout_profile() -> RolloutProfile | None:
    """The attempt's pinned profile, or ``None`` outside an attempt."""
    return _PROFILE.get()


def current_rollout_profile() -> RolloutProfile:
    """The profile every consumer reads.

    Inside an attempt this is the value frozen when the attempt started.
    Outside one -- the authored-vNext bridge, workspace page remake, a direct
    service call -- there is no attempt to pin to, so the switches are resolved
    on the spot, which is the behaviour those callers already had.
    """
    profile = _PROFILE.get()
    return profile if profile is not None else resolve_rollout_profile()


@contextmanager
def rollout_profile_scope(profile: RolloutProfile):
    token = _PROFILE.set(profile)
    try:
        yield profile
    finally:
        _PROFILE.reset(token)


def pin_rollout_profile(
    profile: RolloutProfile, *, replacing: RolloutProfile | None = None
) -> RolloutProfile:
    """Pin the attempt to the profile its job record carries.

    ``replacing`` is the profile this attempt was already pinned to from a job
    record, if any.  One attempt is pinned once: a second pin that disagrees is
    a mid-flight flip under another name, so it is a coded rejection rather than
    a silent re-pin.
    """

    if replacing is not None and replacing != profile:
        raise RolloutProfileError(
            ROLLOUT_PROFILE_MISMATCH, f"{replacing.digest[:16]} != {profile.digest[:16]}"
        )
    _PROFILE.set(profile)
    return profile


def require_rollout_profile(persisted: Any) -> RolloutProfile:
    """The profile a resumed attempt must run, or a coded rejection.

    ``persisted`` is the job record's pinned profile.  Absent, the attempt keeps
    the profile it resolved at start.  Present, it is authority: it is restored
    and returned even when the live environment now says something else, because
    the deck's earlier slides were produced under it.
    """

    if persisted is None:
        return current_rollout_profile()
    return rollout_profile_from_payload(persisted)


def require_policy_revision(revision: Any, *, profile: RolloutProfile | None = None) -> str:
    """Refuse evidence minted under a different policy or rollout profile."""
    expected = (profile or current_rollout_profile()).policy_revision
    if revision != expected:
        raise RolloutProfileError(POLICY_REVISION_MISMATCH, f"{revision!r} != {expected!r}")
    return expected


def matches_policy_revision(revision: Any, *, profile: RolloutProfile | None = None) -> bool:
    return revision == (profile or current_rollout_profile()).policy_revision


__all__ = [
    "MAX_REPAIR_LOOP_MODEL_CALLS",
    "ROLLOUT_PROFILE_PAYLOAD_KEY",
    "RolloutProfile",
    "RolloutProfileError",
    "current_rollout_profile",
    "matches_policy_revision",
    "pin_rollout_profile",
    "pinned_rollout_profile",
    "require_policy_revision",
    "require_rollout_profile",
    "resolve_rollout_profile",
    "rollout_profile_from_payload",
    "rollout_profile_scope",
    "ROLLOUT_PROFILE_MISMATCH",
]
