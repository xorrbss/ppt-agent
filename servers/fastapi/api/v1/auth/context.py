"""Backward-compatibility shim: the owner context moved to utils.owner_context.

Import from utils.owner_context in new code; this module only re-exports so any
remaining external references keep working.
"""
from utils.owner_context import (
    get_current_owner_id,
    get_current_owner_is_admin,
    reset_current_owner_id,
    reset_current_owner_is_admin,
    set_current_owner_id,
    set_current_owner_is_admin,
)

__all__ = [
    "get_current_owner_id",
    "get_current_owner_is_admin",
    "reset_current_owner_id",
    "reset_current_owner_is_admin",
    "set_current_owner_id",
    "set_current_owner_is_admin",
]
