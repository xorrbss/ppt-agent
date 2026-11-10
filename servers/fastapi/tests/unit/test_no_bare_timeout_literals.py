"""Gate: timeout, join, wait and sleep durations are named, never bare literals.

A numeric literal passed as a ``timeout``-style keyword, or as the sole argument
of ``sleep``/``join``/``wait``, hides a policy value inside logic. Name it at the
top of the module, or in ``constants/timeouts.py`` when several modules share it.
``sleep(0)`` (a scheduler yield) and ``timeout=0`` are not durations and pass.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("services", "utils", "api")
SKIPPED_PARTS = {"vendor", "dist", ".venv", "__pycache__"}

TIMEOUT_KEYWORDS = {
    "timeout",
    "timeout_seconds",
    "call_timeout_seconds",
    "read_timeout",
    "connect_timeout",
    "deadline",
}
SOLE_ARGUMENT_CALLS = {"sleep", "join", "wait"}

# Modules owned by concurrent work; each entry is scanned once that work lands.
# This list may only shrink.
FROZEN_FILES = {
    "services/authored_canvas.py",
    "services/source_content/raster_text_guards.py",
    "services/source_content/raster_title_reread.py",
}
FROZEN_PREFIXES = (
    "utils/llm_calls/",
    "services/generation_core/authoring/",
    "services/source_content/raster_",
)

# "relative/path.py:line-independent call text" -> reason. Sites deliberately left
# bare. This allowlist may only shrink.
ALLOWED_BARE_LITERALS: dict[str, str] = {}


def _is_duration(node: ast.AST) -> bool:
    if isinstance(node, ast.IfExp):
        return _is_duration(node.body) or _is_duration(node.orelse)
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_frozen(relative: str) -> bool:
    return relative in FROZEN_FILES or relative.startswith(FROZEN_PREFIXES)


def _scan() -> dict[str, str]:
    """Map allowlist-style site key to a human-readable location."""
    found: dict[str, str] = {}
    for directory in SCANNED_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if SKIPPED_PARTS & set(path.parts):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if _is_frozen(relative):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node.func)
                hits = [
                    f"{keyword.arg}=<literal>"
                    for keyword in node.keywords
                    if keyword.arg in TIMEOUT_KEYWORDS and _is_duration(keyword.value)
                ]
                if (
                    name in SOLE_ARGUMENT_CALLS
                    and len(node.args) == 1
                    and not node.keywords
                    and _is_duration(node.args[0])
                ):
                    hits.append(f"{name}(<literal>)")
                for hit in hits:
                    found[f"{relative}:{name}:{hit}"] = (
                        f"{relative}:{node.lineno} {name}() {hit}"
                    )
    return found


def test_no_bare_timeout_literals() -> None:
    violations = [
        where for site, where in _scan().items() if site not in ALLOWED_BARE_LITERALS
    ]
    message = (
        "Name these durations (module constant, or constants/timeouts.py when shared):"
        + "\n"
        + "\n".join(violations)
    )
    assert not violations, message


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(set(ALLOWED_BARE_LITERALS) - set(_scan()))
    assert not stale, f"Remove allowlist entries that no longer match: {stale}"


def test_frozen_files_exist() -> None:
    missing = [name for name in FROZEN_FILES if not (ROOT / name).exists()]
    assert not missing, f"Drop deleted files from FROZEN_FILES: {missing}"
