"""Every env knob the backend reads through the get_env API is accounted for.

``constants/settings_registry.json`` cannot represent an internal knob: a
registered ``fastapi.runtime`` integer is coerced at snapshot time, so a
malformed value would fail process start and the admin endpoint would
expose it. Internal knobs therefore live in ``tests/support/internal_env_names.py``
with a one-line purpose each. This test scans the accessor surfaces and
asserts every name is either registered or listed, and that nothing listed
has gone stale.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.support.internal_env_names import INTERNAL_ENV_NAMES, OS_LEVEL_ENV_NAMES
from constants.settings_registry import get_settings_by_key

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("services", "utils", "api", "models")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Callees whose first positional argument is an env name. The four public
# parsers plus the thin per-module wrappers that forward to them.
ENV_READ_CALLEES = {
    "env_int",
    "env_float",
    "env_bool",
    "get_setting_env",
    "_int_setting",
    "_optional_positive_int_setting",
    "_optional_nonnegative_float_setting",
    "_positive_int_env",
    "_positive_float_env",
    "_positive_environment_integer",
    "_configured_positive_int",
    "_int_env",
    "_env_int",
    "_env_nonnegative_int",
    "_env_nonnegative_float",
    "_positive_int",
    "_configured_limit",
    "_configured_mib",
}


def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _iter_python_files():
    for root in SCAN_ROOTS:
        for path in (BACKEND_ROOT / root).rglob("*.py"):
            parts = set(path.parts)
            if parts & {".venv", "vendor", "dist", "__pycache__"}:
                continue
            yield path


def _literal_env_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    constants = _module_string_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = _callee_name(node)
        if callee is None:
            continue
        if callee not in ENV_READ_CALLEES and not callee.endswith("_env"):
            continue
        first = node.args[0]
        value: str | None = None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            value = first.value
        elif isinstance(first, ast.Name):
            value = constants.get(first.id)
        if value is not None and ENV_NAME_RE.match(value):
            names.add(value)
    return names


def _module_string_constants(tree: ast.AST) -> dict[str, str]:
    """Module-level ``NAME = "LITERAL"`` bindings, so ENV_NAMES may reference them."""
    found: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value.value
    return found


def _declared_env_names(tree: ast.AST) -> set[str]:
    """``ENV_NAMES`` tuples in accessor modules and ``_GETTER_KEYS`` in get_env."""
    names: set[str] = set()
    constants = _module_string_constants(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = {node.target.id}
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        else:
            continue
        if node.value is None:
            continue
        if "ENV_NAMES" in targets and isinstance(node.value, (ast.Tuple, ast.List)):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)
                elif isinstance(elt, ast.Name) and elt.id in constants:
                    names.add(constants[elt.id])
        if "_GETTER_KEYS" in targets and isinstance(node.value, ast.Dict):
            for value in node.value.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    names.add(value.value)
    return names


def collect_accessed_env_names() -> set[str]:
    names: set[str] = set()
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names |= _literal_env_names(tree)
        names |= _declared_env_names(tree)
    return names


def test_every_accessed_env_name_is_registered_or_listed_internal():
    accessed = collect_accessed_env_names()
    assert accessed, "scanner found nothing; the accessor surfaces moved"
    registered = set(get_settings_by_key())
    unaccounted = sorted(
        name
        for name in accessed
        if name not in registered
        and name not in INTERNAL_ENV_NAMES
        and name not in OS_LEVEL_ENV_NAMES
    )
    assert not unaccounted, (
        "env names read through the get_env API but neither registered in "
        "constants/settings_registry.json nor listed in "
        f"tests/support/internal_env_names.py: {unaccounted}"
    )


def test_internal_list_does_not_overlap_the_registry_or_go_stale():
    registered = set(get_settings_by_key())
    overlap = sorted(set(INTERNAL_ENV_NAMES) & registered)
    assert not overlap, f"registered names must not be listed as internal: {overlap}"
    accessed = collect_accessed_env_names()
    stale = sorted(name for name in INTERNAL_ENV_NAMES if name not in accessed)
    assert not stale, f"internal names no accessor reads any more: {stale}"


def test_internal_entries_have_a_purpose():
    for name, purpose in INTERNAL_ENV_NAMES.items():
        assert ENV_NAME_RE.match(name), name
        assert isinstance(purpose, str) and purpose.strip(), name
        assert "\n" not in purpose, f"{name}: one line only"
