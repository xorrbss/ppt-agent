"""Unit tests for the authored renderer's static import closure walker.

``contracts/authored-renderer/import_closure.py`` is not part of the FastAPI
package (it lives in the contracts directory so it can also be imported by
the standalone ``verify_contract.py`` script). It is loaded here by file path,
the same way ``test_authored_renderer_deployment_contract.py`` loads
``verify_contract.py``.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "authored-renderer"


def _load_import_closure_module():
    path = CONTRACT_ROOT / "import_closure.py"
    module_name = "authored_renderer_import_closure"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The module must be registered in sys.modules before exec_module runs:
    # its dataclasses look up `sys.modules[__module__]` while resolving
    # annotations, which fails on a module that exists only as a bare object.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


import_closure = _load_import_closure_module()


@pytest.fixture()
def two_level_chain_with_lazy_import(tmp_path: Path) -> tuple[Path, Path, frozenset[str]]:
    """A tiny first-party tree: entry -> mod_a -> mod_b (eager), plus a
    third module reached only through an import nested inside a function
    body of ``mod_a`` (a stand-in for the worker's ``from utils import
    slide_capture`` inside its request handler).
    """

    package_root = tmp_path
    pkg = package_root / "pkgroot"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    entry = pkg / "entry.py"
    entry.write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            from pkgroot import mod_a


            def run() -> str:
                return mod_a.call_lazy()
            """
        ),
        encoding="utf-8",
    )

    mod_a = pkg / "mod_a.py"
    mod_a.write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            from pkgroot.mod_b import VALUE


            def call_lazy() -> str:
                # A lazy import nested inside a function body, mirroring the
                # renderer worker's `from utils import slide_capture` inside
                # its request handler: it still runs inside the built image
                # the first time this function executes, so it belongs in
                # the closure even though it is not a top-level import.
                from pkgroot.mod_c import LAZY_VALUE

                return VALUE + LAZY_VALUE
            """
        ),
        encoding="utf-8",
    )

    mod_b = pkg / "mod_b.py"
    mod_b.write_text("VALUE = 'b'\n", encoding="utf-8")

    mod_c = pkg / "mod_c.py"
    mod_c.write_text("LAZY_VALUE = 'c'\n", encoding="utf-8")

    return entry, package_root, frozenset({"pkgroot"})


def test_closure_includes_transitive_and_lazy_imports(
    two_level_chain_with_lazy_import: tuple[Path, Path, frozenset[str]],
) -> None:
    entry, package_root, first_party_roots = two_level_chain_with_lazy_import

    result = import_closure.compute_closure(entry, package_root, first_party_roots)

    assert "pkgroot.entry" in result.modules
    assert "pkgroot.mod_a" in result.modules
    # Two-level transitive import (entry -> mod_a -> mod_b).
    assert "pkgroot.mod_b" in result.modules
    # Import nested inside a function body (mod_a -> mod_c).
    assert "pkgroot.mod_c" in result.modules

    assert result.module_files["pkgroot.mod_b"] == "pkgroot/mod_b.py"
    assert result.module_files["pkgroot.mod_c"] == "pkgroot/mod_c.py"
    assert "pkgroot/mod_b.py" in result.files
    assert "pkgroot/mod_c.py" in result.files


def test_closure_records_external_imports_without_resolving_them(
    two_level_chain_with_lazy_import: tuple[Path, Path, frozenset[str]],
) -> None:
    entry, package_root, first_party_roots = two_level_chain_with_lazy_import
    (package_root / "pkgroot" / "mod_a.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            import json

            from pkgroot.mod_b import VALUE


            def call_lazy() -> str:
                from pkgroot.mod_c import LAZY_VALUE

                return json.dumps(VALUE + LAZY_VALUE)
            """
        ),
        encoding="utf-8",
    )

    result = import_closure.compute_closure(entry, package_root, first_party_roots)

    imported_names = {edge.imported for edge in result.external_edges}
    assert "json" in imported_names
    assert "json" not in result.modules
