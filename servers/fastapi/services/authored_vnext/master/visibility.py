"""Visibility rules for server-owned Authored master records."""

from __future__ import annotations


INTERNAL_AUTHORED_MASTER_PREFIX = "master:internal-authored-"


def is_internal_authored_master_id(master_id: str) -> bool:
    """Return whether a master is durable infrastructure, not a user choice."""

    return master_id.startswith(INTERNAL_AUTHORED_MASTER_PREFIX)


__all__ = ["INTERNAL_AUTHORED_MASTER_PREFIX", "is_internal_authored_master_id"]
