"""Compatibility re-export for the package-neutral body grammar contract."""

from __future__ import annotations

from services.authored_vnext.body_grammar import (
    BODY_GRAMMAR_ROLE_PREFIX,
    BODY_GRAMMAR_SCHEMA_VERSION,
    BODY_GRAMMAR_SCHEMA_VERSION_V3,
    BODY_GRAMMAR_VALUE_KEY,
    authored_body_grammar_style_id,
    build_body_grammar_snapshot,
)

__all__ = [
    "BODY_GRAMMAR_ROLE_PREFIX",
    "BODY_GRAMMAR_SCHEMA_VERSION",
    "BODY_GRAMMAR_SCHEMA_VERSION_V3",
    "BODY_GRAMMAR_VALUE_KEY",
    "authored_body_grammar_style_id",
    "build_body_grammar_snapshot",
]
