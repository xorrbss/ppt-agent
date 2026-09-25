"""Failure raised across the public export operation adapter and its layers.

The runtime-identity layer below the adapter raises the same error the adapter
reports, so the type lives in a module both can import without a cycle.
"""

from __future__ import annotations


class PublicExportOperationError(RuntimeError):
    pass
