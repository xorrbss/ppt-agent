"""Per-invocation LibreOffice user profile isolation.

LibreOffice shares a single user profile directory per OS user by default.
When two ``soffice`` processes are launched concurrently against that shared
profile, the second either attaches to the already-running instance or fails
outright with a non-descriptive exit code and empty stdout/stderr — the
failure mode does not clearly say "profile lock", it just looks like an
opaque conversion failure. Every caller that spawns ``soffice`` must
therefore give each invocation its own throwaway profile directory via
``-env:UserInstallation=<file:// URI>``, which makes concurrent conversions
independent LibreOffice instances instead of contenders for one profile.

This mirrors the pattern already used by
``services/source_locked/conversion.py``; this module exists so ``DocumentConversionService`` (the primary upload/
decompose conversion path) shares the same isolation instead of reinventing
it a third time.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path
from typing import Iterator


@contextlib.contextmanager
def unique_user_installation_arg(
    *, prefix: str = "soffice-profile-"
) -> Iterator[str]:
    """Yield a ``-env:UserInstallation=file:///...`` argument for one soffice call.

    A fresh, empty temporary directory backs the argument; LibreOffice
    populates it on first use. The directory is removed unconditionally when
    the context exits, whether the conversion succeeded, failed, or timed
    out, so nothing accumulates across calls.
    """
    profile_dir = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        # Path.as_uri() produces a well-formed file:// URI (correctly
        # percent-encoding spaces and normalizing a Windows drive letter),
        # which a hand-built f"file:///{path}" string would not.
        yield f"-env:UserInstallation={profile_dir.resolve().as_uri()}"
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
