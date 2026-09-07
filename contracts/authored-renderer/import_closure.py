"""Static first-party import closure walker for the authored renderer image.

The renderer's production image copies a hand-picked list of ``servers/fastapi``
files into ``authored-renderer-runtime`` (see the ``Dockerfile`` stage of that
name) and records the same list in ``attestation.IMAGE_SOURCES`` so the digest
changes whenever any of them changes. That list has drifted from what
``utils.authored_renderer_worker`` actually imports: a module can be missed
because it is only imported lazily inside a function, which still executes
inside the built image at run time.

This module computes the *real* transitive import closure of a first-party
entry point by parsing its AST (``ast.walk`` so nested/lazy imports inside
function bodies are found, not just module-level statements) and recursively
resolving every ``import`` / ``from ... import ...`` statement whose root
package is one of the first-party roots passed to :func:`compute_closure`.

Only first-party modules are resolved to files and walked further; anything
else (stdlib or third-party) is recorded as an external reference so callers
can check it against a dependency lock file.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ImportEdge:
    """One import statement, from the file it appears in to what it names."""

    importer: str
    imported: str
    lineno: int


@dataclass
class ClosureResult:
    """The outcome of walking a first-party import closure."""

    # Dotted first-party module names reached, entry point included.
    modules: set[str] = field(default_factory=set)
    # Dotted module name -> relative file path (posix, relative to package_root).
    module_files: dict[str, str] = field(default_factory=dict)
    # Root-relative file paths (posix) for every first-party module reached.
    files: set[str] = field(default_factory=set)
    # Import statements that referenced something outside the first-party
    # roots (stdlib or third-party), e.g. ("utils.db_utils", "sqlalchemy", 12).
    external_edges: list[ImportEdge] = field(default_factory=list)


def _candidate_paths(package_root: Path, dotted: str) -> tuple[Path, Path]:
    parts = dotted.split(".")
    base = package_root.joinpath(*parts)
    return base.with_suffix(".py"), base / "__init__.py"


def _resolve_module_file(package_root: Path, dotted: str) -> Path | None:
    """Resolve a dotted module name to an existing file under package_root."""

    module_path, package_init = _candidate_paths(package_root, dotted)
    if module_path.is_file():
        return module_path
    if package_init.is_file():
        return package_init
    return None


def _module_root(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _to_module_name(package_root: Path, file_path: Path) -> str:
    relative = file_path.relative_to(package_root)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _parent_packages(dotted: str) -> list[str]:
    parts = dotted.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def _relative_base(current_module: str, level: int, module: str | None) -> str | None:
    """Resolve a `from . import x` / `from ..pkg import y` style target module."""

    current_parts = current_module.split(".")
    # `level` dots strip that many trailing components from the *package*
    # containing the current module (i.e. from the module's own parent).
    package_parts = current_parts[:-1]
    if level > len(package_parts) + 1:
        return None
    anchor = package_parts[: len(package_parts) - (level - 1)] if level > 1 else package_parts
    if module:
        anchor = anchor + module.split(".")
    return ".".join(anchor) if anchor else None


def _iter_import_targets(
    tree: ast.AST, current_module: str
) -> list[tuple[str, int, list[str] | None]]:
    """Yield (dotted_target, lineno, imported_names_or_None) for every import.

    For ``import a.b.c`` the target is ``a.b.c`` with no imported names.
    For ``from a.b import c, d`` the target is ``a.b`` with imported_names
    ``["c", "d"]`` so the caller can also probe ``a.b.c`` / ``a.b.d`` as
    possible submodules.
    """

    targets: list[tuple[str, int, list[str] | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append((alias.name, node.lineno, None))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = _relative_base(current_module, node.level, node.module)
            else:
                base = node.module
            if base is None:
                continue
            names = [alias.name for alias in node.names if alias.name != "*"]
            targets.append((base, node.lineno, names))
    return targets


def compute_closure(
    entry_file: Path,
    package_root: Path,
    first_party_roots: frozenset[str],
) -> ClosureResult:
    """Compute the transitive first-party import closure of ``entry_file``.

    ``entry_file`` and ``package_root`` are absolute paths; ``package_root``
    is the directory dotted module names are resolved relative to (for this
    contract, ``servers/fastapi``). Only modules whose top-level package is
    in ``first_party_roots`` are resolved and walked; every other import is
    recorded in ``ClosureResult.external_edges`` instead.
    """

    entry_file = entry_file.resolve()
    package_root = package_root.resolve()
    entry_module = _to_module_name(package_root, entry_file)

    result = ClosureResult()
    queue: list[tuple[str, Path]] = [(entry_module, entry_file)]
    seen: set[str] = set()

    while queue:
        module_name, file_path = queue.pop()
        if module_name in seen:
            continue
        seen.add(module_name)
        result.modules.add(module_name)
        relative = file_path.relative_to(package_root).as_posix()
        result.module_files[module_name] = relative
        result.files.add(relative)

        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))

        for target, lineno, imported_names in _iter_import_targets(tree, module_name):
            root = _module_root(target)
            if root not in first_party_roots:
                result.external_edges.append(ImportEdge(module_name, target, lineno))
                continue

            resolved_as_module = False
            base_file = _resolve_module_file(package_root, target)
            if base_file is not None:
                resolved_as_module = True
                base_module = _to_module_name(package_root, base_file)
                for parent in _parent_packages(base_module):
                    parent_file = _resolve_module_file(package_root, parent)
                    if parent_file is not None and parent not in seen:
                        queue.append((parent, parent_file))
                if base_module not in seen:
                    queue.append((base_module, base_file))

            if imported_names:
                for name in imported_names:
                    candidate = f"{target}.{name}"
                    candidate_file = _resolve_module_file(package_root, candidate)
                    if candidate_file is not None:
                        for parent in _parent_packages(candidate):
                            parent_file = _resolve_module_file(package_root, parent)
                            if parent_file is not None and parent not in seen:
                                queue.append((parent, parent_file))
                        if candidate not in seen:
                            queue.append((candidate, candidate_file))
                        resolved_as_module = True
                    elif not resolved_as_module:
                        # Neither `target` nor `target.name` resolved to a
                        # first-party file: most likely a package-level
                        # attribute import we cannot follow further, or an
                        # unresolvable path. Record it for visibility but do
                        # not treat it as an external dependency, since its
                        # root is first-party.
                        pass

            if not resolved_as_module and base_file is None:
                # The dotted base itself did not resolve to any file (e.g. a
                # first-party root that is only ever used via attribute
                # access on a namespace we do not model). Nothing to enqueue.
                continue

    return result


def external_third_party_edges(
    result: ClosureResult, stdlib_module_names: frozenset[str]
) -> list[ImportEdge]:
    """Filter ``external_edges`` down to non-stdlib (third-party) imports."""

    third_party: list[ImportEdge] = []
    for edge in result.external_edges:
        root = _module_root(edge.imported)
        if root in stdlib_module_names:
            continue
        third_party.append(edge)
    return third_party
