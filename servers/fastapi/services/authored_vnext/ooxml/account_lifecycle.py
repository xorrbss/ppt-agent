"""Account deletion hook for owner-scoped OOXML file repositories."""

from __future__ import annotations

from pathlib import Path

from .service import OoxmlPackageService


def fence_authored_vnext_ooxml_packages_for_account(
    root: Path,
    *,
    owner_id: str,
    expected_generation: int,
) -> int:
    """Fence against the durable deletion marker generation.

    ``mark_account_deleting`` increments the database generation before this
    hook runs. The file repository still holds the previously admitted active
    generation, so a marker of N must transition exactly N-1 to N.
    """

    if expected_generation < 1:
        raise ValueError("account deletion marker generation must be positive")

    return OoxmlPackageService(root, owner_id=owner_id).fence_account_deletion(
        expected_generation=expected_generation - 1
    )


__all__ = ["fence_authored_vnext_ooxml_packages_for_account"]
