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



def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


#: A fragment html5lib repairs, so the two representations differ in bytes and
#: the assertion below reads the mode out of the output instead of trusting a
#: field.  ``legacy`` returns it unchanged; ``tree`` closes the ``<main>``.
UNCLOSED = "<main><p>pinned</p>"


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
#: it is where the attempt's profile is resolved.  Every other consumer goes
#: through ``current_rollout_profile()``.  Adding anything here is a new bypass:
#: one live reader is enough to re-represent the rest of an in-flight deck.
_ROLLOUT_PROFILE = "services/authored_rollout_profile.py"
_KNOWN_UNROUTED = {_ROLLOUT_PROFILE}


def test_no_new_consumer_reads_the_normalization_knob_live():
    """The pin holds only while the profile stays the sole reader."""

    readers = _modules_reading_the_normalization_knob()
    assert _ROLLOUT_PROFILE in readers, "the profile must still resolve the switch it pins"
    new = readers - _KNOWN_UNROUTED
    assert not new, (
        "these modules read AUTHORED_HTML_NORMALIZATION live instead of through "
        f"current_rollout_profile(), so a mid-attempt flip reaches them: {sorted(new)}"
    )
