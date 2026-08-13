from __future__ import annotations

import posixpath
from pathlib import PurePosixPath

from .package_reader import UnsafePptxPackage


def resolve_internal_part_target(source_part: str, target: str) -> str:
    """Resolve a local OPC relationship target without allowing package escape.

    OPC permits a single leading slash to identify a part from the package root.
    Exporters such as Walnut use that form for every internal relationship.  It is
    distinct from a protocol-relative URI (``//host/path``), which remains
    rejected together with Windows paths, URI schemes and traversal.
    """

    if (
        not target
        or "\\" in target
        or target.startswith("//")
        or ":" in target
        or "\x00" in target
    ):
        raise UnsafePptxPackage("unsafe_relationship_target")

    if target.startswith("/"):
        unresolved = target[1:]
        resolved = posixpath.normpath(unresolved)
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_part), target)
        )

    if (
        resolved in {"", ".", ".."}
        or resolved.startswith("../")
        or resolved.startswith("/")
    ):
        raise UnsafePptxPackage("unsafe_relationship_target")
    return str(PurePosixPath(resolved))
