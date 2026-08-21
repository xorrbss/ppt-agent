"""Canonical persistence and CAS editing for the Authored product path."""

from .service import (
    AuthoredCanonicalConflict,
    AuthoredCanonicalError,
    AuthoredCanonicalService,
)

__all__ = [
    "AuthoredCanonicalConflict",
    "AuthoredCanonicalError",
    "AuthoredCanonicalService",
]
