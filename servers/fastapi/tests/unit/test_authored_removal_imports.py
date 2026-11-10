"""Tests for the authored-removal static import checker."""

from __future__ import annotations

from pathlib import Path

from scripts.authored_removal_imports import Tree, check, closure


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(root: Path) -> Tree:
    tree = Tree.scan(root)
    tree.load_facades()
    return tree


def test_closure_follows_package_init_and_records_every_importer(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "from pkg.heavy import Pipeline\n")
    _write(tmp_path, "pkg/heavy.py", "class Pipeline: ...\n")
    _write(tmp_path, "pkg/light.py", "VALUE = 1\n")
    _write(tmp_path, "svc/__init__.py")
    _write(tmp_path, "svc/a.py", "from pkg.light import VALUE\nimport svc.b\n")
    _write(tmp_path, "svc/b.py", "from pkg import light\n")

    reached = closure(_tree(tmp_path), ["svc.a"], "top")

    assert "pkg.heavy" in reached  # only reachable through pkg/__init__.py
    assert sorted(i[0] for i in reached["pkg.light"]) == ["svc.a", "svc.b"]


def test_closure_top_scope_skips_function_local_imports(tmp_path: Path) -> None:
    _write(tmp_path, "svc/__init__.py")
    _write(tmp_path, "svc/a.py", "def f():\n    from svc.b import X\n    return X\n")
    _write(tmp_path, "svc/b.py", "X = 1\n")
    tree = _tree(tmp_path)

    assert "svc.b" not in closure(tree, ["svc.a"], "top")
    assert "svc.b" in closure(tree, ["svc.a"], "all")


def test_check_reports_references_in_every_scope(tmp_path: Path) -> None:
    _write(tmp_path, "svc/__init__.py")
    _write(tmp_path, "svc/gone.py", "X = 1\n")
    _write(
        tmp_path,
        "svc/user.py",
        "from typing import TYPE_CHECKING\n"
        "import importlib\n"
        "if TYPE_CHECKING:\n"
        "    from svc.gone import X\n"
        "def f():\n"
        "    from svc.gone import X\n"
        "    return importlib.import_module('svc.gone')\n",
    )
    # A regular package: a bare namespace ``tests`` dir would be shadowed by an installed ``tests``.
    _write(tmp_path, "tests/__init__.py")
    _write(tmp_path, "tests/test_user.py", "PATCH = 'svc.gone.X'\n")

    report = check(_tree(tmp_path), {"svc.gone"})
    kinds = sorted((h["kind"], h["scope"], h["test"]) for h in report["hits"])

    assert kinds == [
        ("dynamic", "nested", False),
        ("from", "nested", False),
        ("from", "top", False),
        ("string", "nested", True),
    ]


def test_check_reports_names_that_no_longer_resolve(tmp_path: Path) -> None:
    _write(tmp_path, "svc/__init__.py")
    _write(tmp_path, "svc/shared.py", "def kept(): ...\n")
    _write(tmp_path, "svc/user.py", "from svc.shared import kept, moved_away\n")

    report = check(_tree(tmp_path), set())

    assert [(u["module"], u["name"]) for u in report["unresolved"]] == [("svc.shared", "moved_away")]


def test_check_resolves_facades_and_globals_update(tmp_path: Path) -> None:
    _write(tmp_path, "svc/__init__.py")
    _write(tmp_path, "svc/real/__init__.py", "def thing(): ...\n")
    _write(tmp_path, "svc/real/sub.py", "def subthing(): ...\n")
    _write(
        tmp_path,
        "svc/old/__init__.py",
        "import sys\nfrom svc import real as _moved\nsys.modules[__name__] = _moved\n",
    )
    _write(
        tmp_path,
        "svc/env.py",
        "_KEYS = {'get_a': 'A'}\nglobals().update({name: key for name, key in _KEYS.items()})\n",
    )
    _write(
        tmp_path,
        "svc/user.py",
        "from svc.old import thing\nfrom svc.old.sub import subthing\nfrom svc.env import get_a, get_b\n",
    )

    report = check(_tree(tmp_path), set())

    assert [(u["module"], u["name"]) for u in report["unresolved"]] == [("svc.env", "get_b")]


def test_check_follows_facade_onto_a_deleted_module(tmp_path: Path) -> None:
    _write(tmp_path, "svc/__init__.py")
    _write(tmp_path, "svc/real.py", "X = 1\n")
    _write(tmp_path, "svc/old.py", "import sys\nfrom svc import real as _moved\nsys.modules[__name__] = _moved\n")
    _write(tmp_path, "svc/user.py", "from svc.old import X\n")

    report = check(_tree(tmp_path), {"svc.real"})

    assert {h["file"] for h in report["hits"]} == {"svc/old.py", "svc/user.py"}
