"""Skip marker for tests that need the fixed Windows symbol face.

The svg template fallback font is the digest-checked OS face Segoe UI Symbol
(``services.authored_vnext.semantic.typography.load_bounded_windows_symbol_registry``).
It is proprietary and not vendored, so hosts without it cannot run these tests.
"""
import pytest

from services.authored_vnext.semantic.typography import load_bounded_windows_symbol_registry


def _available() -> bool:
    try:
        load_bounded_windows_symbol_registry()
    except Exception:
        return False
    return True


requires_windows_symbol_font = pytest.mark.skipif(
    not _available(), reason="requires the Segoe UI Symbol face (C:\\Windows\\Fonts\\seguisym.ttf)"
)
