"""The rollout profile is the single place the authored switches are read.

``services/authored_rollout_profile.py`` freezes the five switches of the
2026-09-11 verbatim programme once per operation attempt.  The value of that pin
is only as good as the absence of a second reader: a consumer that still calls
``utils.get_env_programme`` directly re-represents the rest of an in-progress
deck the moment an operator flips a knob, while every receipt for that deck goes
on claiming one policy revision (R7 of
``artifacts/impl-review-20260911/rereview-normalization-provider-placeholder.md``).

So the two things asserted here are the pin and the absence of a bypass.
"""

from __future__ import annotations

import asyncio
import os
from functools import wraps
from pathlib import Path

from services import authored_admission_session as sessions
from services.authored_rollout_profile import (
    RolloutProfile,
    current_rollout_profile,
    resolve_rollout_profile,
)
from utils.authored_html_normalize import normalize_authored_html


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


#: A fragment html5lib repairs, so the two representations differ in bytes and
#: the assertion below reads the mode out of the output instead of trusting a
#: field.  ``legacy`` returns it unchanged; ``tree`` closes the ``<main>``.
UNCLOSED = "<main><p>pinned</p>"


@sync_async
async def test_a_knob_flipped_mid_attempt_cannot_re_represent_the_rest_of_the_deck(monkeypatch):
    """Tree mode rewrites every slide's bytes, so a mid-flight flip is a mix.

    The attempt resolves the switches once, at ``admission_scope``.  Flipping
    the environment variable after that must not reach the normalizer, which is
    the one consumer that turns the mode into persisted bytes.
    """

    monkeypatch.setenv("AUTHORED_HTML_NORMALIZATION", "tree")
    observed: list[tuple[str, str]] = []

    @sessions.admission_scope
    async def author_two_slides() -> str:
        for _ in range(2):
            observed.append(
                (
                    current_rollout_profile().html_normalization,
                    normalize_authored_html(UNCLOSED, profile="fragment").html,
                )
            )
            # An operator flips the knob while the deck is half authored.
            monkeypatch.setenv("AUTHORED_HTML_NORMALIZATION", "legacy")
        return sessions.current_session().profile.policy_revision

    revision = await author_two_slides()

    first, second = observed
    assert first == second, "the attempt changed representation under its own receipts"
    assert first[0] == "tree" and first[1] == "<main><p>pinned</p></main>"
    # The receipts of both slides name the representation that produced them.
    assert revision == RolloutProfile(html_normalization="tree").policy_revision
    assert revision != RolloutProfile(html_normalization="legacy").policy_revision
    # The flip is not swallowed either: it is the *next* attempt's profile.
    assert resolve_rollout_profile().html_normalization == "legacy"
    assert normalize_authored_html(UNCLOSED, profile="fragment").html == UNCLOSED


#: Pruned rather than filtered: walking a virtualenv on Windows costs more than
#: the rest of this suite put together.
_SKIP_DIRS = {".venv", "__pycache__", "build", "dist", "node_modules", "tests", ".pytest_cache"}


def _modules_reading_the_normalization_knob() -> set[str]:
    root = Path(__file__).resolve().parents[2]
    found: set[str] = set()
    for parent, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(parent, filename)
            if path == root / "utils" / "get_env_programme.py":
                continue  # the accessor's own definition and ``__all__``
            if "get_authored_html_normalization" in path.read_text(
                encoding="utf-8", errors="ignore"
            ):
                found.add(path.relative_to(root).as_posix())
    return found


#: ``authored_rollout_profile`` is the one module allowed to read the accessor:
#: it is where the attempt's profile is resolved.  ``substitute.py`` is a known
#: unrouted residual of R7 -- it picks its splice path from a live read inside
#: ``expand_placeholders``, i.e. inside an admission that is already pinned --
#: and is owned by the source-content surface, not by this one.  Removing it
#: from the set below is the fix; adding anything is a new bypass.
_ROLLOUT_PROFILE = "services/authored_rollout_profile.py"
_KNOWN_UNROUTED = {_ROLLOUT_PROFILE, "services/source_content/placeholder/substitute.py"}


def test_no_new_consumer_reads_the_normalization_knob_live():
    """The pin holds only while the profile stays the sole reader."""

    readers = _modules_reading_the_normalization_knob()
    assert _ROLLOUT_PROFILE in readers, "the profile must still resolve the switch it pins"
    new = readers - _KNOWN_UNROUTED
    assert not new, (
        "these modules read AUTHORED_HTML_NORMALIZATION live instead of through "
        f"current_rollout_profile(), so a mid-attempt flip reaches them: {sorted(new)}"
    )
