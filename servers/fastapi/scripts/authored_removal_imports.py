"""Static import checks for the authored removal (P3).

See ``docs/authored-removal-execution-20261109.md`` ("P3 plan"). Two commands:

``closure``
    Import closure from the svg entry points. Unlike the design's Appendix A
    tool it follows package ``__init__`` modules (importing ``a.b.c`` runs
    ``a/__init__`` and ``a/b/__init__``) and records every importer of each
    reached module, not only the first one found.

``check``
    The per-batch done criterion. Given a delete set D (file paths), report
    every remaining module that still references D in any scope: module level,
    ``TYPE_CHECKING`` blocks, function-local imports, ``import_module`` /
    ``__import__`` strings and dotted string constants (``patch()`` targets,
    registries). It also reports ``from X import name`` statements whose name
    does not resolve in the remaining tree. Names that already fail to resolve
    before the batch go into a baseline file so only new failures count.

Run from ``servers/fastapi``::

    uv run python scripts/authored_removal_imports.py closure --scope top --out top.json
    uv run python scripts/authored_removal_imports.py check --delete delete.txt \
        --baseline unresolved-baseline.json --out check.json
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENTRIES = (
    "services.svg_presentation_service",
    "services.svg_generation_admission",
    "services.svg_export_operation",
)
_SKIP_DIR_NAMES = {"__pycache__", "node_modules", "build", "dist", "app_data", "artifacts"}
_DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
_AUTHORED_NAME = re.compile(r"authored|slidespec")


@dataclass(frozen=True)
class Ref:
    """One reference from a source file to a dotted name."""

    target: str
    line: int
    kind: str  # "import", "from", "dynamic" or "string"
    scope: str  # "top" or "nested"
    names: tuple[str, ...] = ()


@dataclass
class Tree:
    """The Python modules under one root, keyed by dotted module name."""

    root: Path
    files: dict[str, Path] = field(default_factory=dict)
    packages: set[str] = field(default_factory=set)  # regular and namespace packages
    roots: set[str] = field(default_factory=set)  # first segments that are local
    facades: dict[str, str] = field(default_factory=dict)  # facade module -> module it aliases

    def load_facades(self) -> None:
        for module, path in self.files.items():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "sys.modules[__name__]" not in text:
                continue
            mod_ast = parse(path)
            target = _facade_target(module, self.is_package_file(module), mod_ast) if mod_ast else None
            if target:
                self.facades[module] = target

    def alias(self, dotted: str) -> str:
        """Follow facades: ``a.facade.sub`` -> ``a.real.sub`` (a facade package aliases its submodules)."""

        seen: set[str] = set()
        while dotted not in seen:
            seen.add(dotted)
            parts = dotted.split(".")
            for end in range(len(parts), 0, -1):
                prefix = ".".join(parts[:end])
                if prefix in self.facades:
                    dotted = ".".join([self.facades[prefix]] + parts[end:])
                    break
            else:
                return dotted
        return dotted

    @classmethod
    def scan(cls, root: Path) -> "Tree":
        tree = cls(root=root)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _err: None):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIR_NAMES and not d.endswith(".egg-info")
            )
            rel = Path(dirpath).relative_to(root)
            parts = [] if rel == Path(".") else list(rel.parts)
            if parts and not all(p.isidentifier() for p in parts):
                dirnames[:] = []
                continue
            if parts:
                tree.packages.add(".".join(parts))
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                stem = name[:-3]
                if stem == "__init__":
                    if parts:
                        tree.files[".".join(parts)] = Path(dirpath) / name
                    continue
                if not stem.isidentifier():
                    continue
                tree.files[".".join(parts + [stem])] = Path(dirpath) / name
        roots = {m.split(".", 1)[0] for m in tree.files} | {p.split(".", 1)[0] for p in tree.packages}
        # A top-level directory without __init__.py is only a namespace package; an
        # installed regular package of the same name (e.g. ``alembic``) wins at runtime.
        shadowed = {r for r in roots if r not in tree.files and r in tree.packages and _installed(r)}
        tree.roots = roots - shadowed
        tree.files = {m: p for m, p in tree.files.items() if m.split(".", 1)[0] not in shadowed}
        tree.packages = {p for p in tree.packages if p.split(".", 1)[0] not in shadowed}
        return tree

    def module_name(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.root.resolve())
        parts = list(rel.parts)
        parts[-1] = parts[-1][:-3]
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def is_package_file(self, module: str) -> bool:
        path = self.files.get(module)
        return path is not None and path.name == "__init__.py"

    def exists(self, module: str) -> bool:
        return module in self.files or module in self.packages

    def longest_module(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join(parts[:end])
            if self.exists(candidate):
                return candidate
        return None


def _installed(name: str) -> bool:
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    return spec is not None and spec.origin not in (None, "namespace")


def _resolve_relative(module: str, is_package: bool, level: int, base: str | None) -> str:
    pkg_parts = module.split(".") if is_package else module.split(".")[:-1]
    if level > 1:
        pkg_parts = pkg_parts[: len(pkg_parts) - (level - 1)]
    return ".".join(pkg_parts + ([base] if base else []))


def _top_level_nodes(body: list[ast.stmt]) -> set[int]:
    ids: set[int] = set()
    stack = list(body)
    while stack:
        node = stack.pop()
        ids.add(id(node))
        if isinstance(node, (ast.If, ast.Try, ast.With)):
            for attr in ("body", "orelse", "finalbody"):
                stack += getattr(node, attr, [])
            for handler in getattr(node, "handlers", []):
                stack += handler.body
    return ids


def parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def references(tree: Tree, module: str, mod_ast: ast.Module) -> list[Ref]:
    """Every reference from ``module`` to a dotted name, in every scope."""

    is_package = tree.is_package_file(module)
    top_ids = _top_level_nodes(mod_ast.body)
    dynamic_args: set[int] = set()
    refs: list[Ref] = []
    for node in ast.walk(mod_ast):
        scope = "top" if id(node) in top_ids else "nested"
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(Ref(alias.name, node.lineno, "import", scope))
        elif isinstance(node, ast.ImportFrom):
            base = node.module
            if node.level:
                base = _resolve_relative(module, is_package, node.level, base)
            names = tuple(a.name for a in node.names)
            refs.append(Ref(base or "", node.lineno, "from", scope, names))
        elif isinstance(node, ast.Call):
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if fname in {"import_module", "__import__"} and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    refs.append(Ref(arg.value, node.lineno, "dynamic", "nested"))
                    dynamic_args.add(id(arg))  # ast.walk is breadth-first: the call precedes its args
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in dynamic_args:
            value = node.value
            if _DOTTED.match(value) and value.split(".", 1)[0] in tree.roots:
                refs.append(Ref(value, getattr(node, "lineno", 0), "string", "nested"))
    return refs


def _with_ancestors(tree: Tree, module: str) -> list[str]:
    parts = module.split(".")
    out = [".".join(parts[:end]) for end in range(1, len(parts))]
    out.append(module)
    return [m for m in out if m in tree.files]


def import_targets(tree: Tree, ref: Ref) -> list[str]:
    """Modules executed by an import reference, package ``__init__`` files included."""

    if ref.kind == "string":
        return []
    targets: list[str] = []
    for base in dict.fromkeys([ref.target, tree.alias(ref.target)]):
        if ref.kind == "from":
            targets += _with_ancestors(tree, base)
            for name in ref.names:
                sub = f"{base}.{name}" if base else name
                if sub in tree.files:
                    targets.append(sub)
        else:
            found = tree.longest_module(base)
            if found:
                targets += _with_ancestors(tree, found)
    return list(dict.fromkeys(targets))


def closure(tree: Tree, entries: list[str], scope: str) -> dict[str, list[list[object]]]:
    """Map each reached module to every ``[importer, line]`` that reaches it."""

    reached: dict[str, list[list[object]]] = {}
    queue: list[str] = []
    for entry in entries:
        for module in _with_ancestors(tree, entry):
            if module not in reached:
                reached[module] = []
                queue.append(module)
    cache: dict[str, list[Ref]] = {}
    while queue:
        module = queue.pop()
        if module not in cache:
            mod_ast = parse(tree.files[module])
            cache[module] = references(tree, module, mod_ast) if mod_ast else []
        for ref in cache[module]:
            if ref.kind == "string" or (scope == "top" and ref.scope != "top"):
                continue
            for target in import_targets(tree, ref):
                importers = reached.setdefault(target, [])
                if [module, ref.line] not in importers:
                    importers.append([module, ref.line])
                if len(importers) == 1 and target not in queue:
                    queue.append(target)
    return dict(sorted(reached.items()))


def _facade_target(module: str, is_package: bool, mod_ast: ast.Module) -> str | None:
    """The module a ``sys.modules[__name__] = alias`` facade replaces itself with."""

    aliases: dict[str, str] = {}
    replaced: str | None = None
    for node in mod_ast.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = _resolve_relative(module, is_package, node.level, node.module)
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Call)
                and getattr(value.func, "attr", getattr(value.func, "id", "")) == "import_module"
                and value.args
                and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)
            ):
                aliases[target.id] = value.args[0].value
            elif (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "modules"
                and isinstance(target.slice, ast.Name)
                and target.slice.id == "__name__"
                and isinstance(value, ast.Name)
            ):
                replaced = value.id
    return aliases.get(replaced) if replaced else None


def _globals_update_keys(node: ast.stmt, dicts: dict[str, set[str]]) -> set[str] | None:
    """Keys a module-level ``globals().update(...)`` binds, ``None`` when unknown."""

    call = node.value if isinstance(node, ast.Expr) else None
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "update"
        and isinstance(call.func.value, ast.Call)
        and getattr(call.func.value.func, "id", "") == "globals"
    ):
        return set()
    arg = call.args[0] if call.args else None
    if isinstance(arg, ast.DictComp) and len(arg.generators) == 1:
        gen = arg.generators[0]
        it = gen.iter
        if (
            isinstance(it, ast.Call)
            and isinstance(it.func, ast.Attribute)
            and it.func.attr == "items"
            and isinstance(it.func.value, ast.Name)
            and it.func.value.id in dicts
            and isinstance(gen.target, ast.Tuple)
            and isinstance(arg.key, ast.Name)
            and isinstance(gen.target.elts[0], ast.Name)
            and arg.key.id == gen.target.elts[0].id
        ):
            return dicts[it.func.value.id]
    return None


def defined_names(mod_ast: ast.Module) -> tuple[set[str], bool]:
    """Names bound at module level, and whether the set is open (``*``, ``__getattr__``, unknown globals)."""

    names: set[str] = set()
    open_set = False
    dicts: dict[str, set[str]] = {}
    for node in mod_ast.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Dict)
            and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.value.keys)
        ):
            dicts[node.targets[0].id] = {k.value for k in node.value.keys}  # type: ignore[union-attr]
        keys = _globals_update_keys(node, dicts)
        if keys is None:
            open_set = True
        else:
            names |= keys
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Subscript) and getattr(getattr(t.value, "func", None), "id", "") == "globals"
            for t in node.targets
        ):
            open_set = True

    def bind(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                bind(elt)
        elif isinstance(target, ast.Starred):
            bind(target.value)

    stack = list(mod_ast.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if node.name == "__getattr__":
                open_set = True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                bind(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            bind(node.target)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    open_set = True
                else:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind(node.target)
            stack += node.body + node.orelse
        elif isinstance(node, (ast.If, ast.While)):
            stack += node.body + node.orelse
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    bind(item.optional_vars)
            stack += node.body
        elif isinstance(node, ast.Try):
            stack += node.body + node.orelse + node.finalbody
            for handler in node.handlers:
                stack += handler.body
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.NamedExpr):
            bind(node.value.target)
    return names, open_set


def _load_delete_set(tree: Tree, delete_file: Path | None, repo_root: Path) -> set[str]:
    deleted: set[str] = set()
    if delete_file is None:
        return deleted
    for raw in delete_file.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        matches = sorted(repo_root.glob(line)) or sorted(tree.root.glob(line))
        if not matches:
            raise SystemExit(f"delete set entry matches nothing: {line}")
        for path in matches:
            paths = sorted(path.rglob("*.py")) if path.is_dir() else [path]
            for py in paths:
                try:
                    deleted.add(tree.module_name(py))
                except ValueError:
                    raise SystemExit(f"delete set entry is outside the root: {py}") from None
    return deleted


def _is_test(module: str) -> bool:
    first = module.split(".", 1)[0]
    leaf = module.rsplit(".", 1)[-1]
    return first == "tests" or leaf.startswith("test_") or leaf == "conftest"


def check(tree: Tree, deleted: set[str]) -> dict[str, object]:
    def is_deleted(module: str) -> bool:
        return any(module == d or module.startswith(d + ".") for d in deleted)

    remaining = {m: p for m, p in tree.files.items() if not is_deleted(m)}
    asts = {m: parse(p) for m, p in remaining.items()}
    own = {m: defined_names(a) for m, a in asts.items() if a is not None}

    def exists(module: str) -> bool:
        return tree.exists(module) and not is_deleted(module)

    def names_of(module: str) -> tuple[set[str], bool]:
        if module in tree.facades:  # a facade's own names never matter, only its target's
            return names_of(tree.alias(module)) if exists(tree.alias(module)) else (set(), False)
        return own.get(module, (set(), module not in remaining))

    hits: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    for module, mod_ast in sorted(asts.items()):
        if mod_ast is None:
            continue
        rel = str(remaining[module].relative_to(tree.root)).replace(os.sep, "/")
        for ref in references(tree, module, mod_ast):
            touched: list[str] = []
            for base in dict.fromkeys([ref.target, tree.alias(ref.target)]):
                if not base:
                    continue
                target = base if ref.kind == "from" else tree.longest_module(base)
                if target and is_deleted(target):
                    touched.append(target)
                if ref.kind == "from":
                    for name in ref.names:
                        sub = f"{base}.{name}"
                        if sub in tree.files and is_deleted(sub):
                            touched.append(sub)
            if touched:
                hits.append(
                    {
                        "file": rel,
                        "line": ref.line,
                        "kind": ref.kind,
                        "scope": ref.scope,
                        "target": sorted(set(touched)),
                        "test": _is_test(module),
                    }
                )
                continue
            if ref.kind != "from" or not ref.target or ref.target.split(".", 1)[0] not in tree.roots:
                continue
            real = tree.alias(ref.target)
            if not exists(real):
                unresolved.append({"file": rel, "line": ref.line, "module": ref.target, "name": "*"})
                continue
            names, open_set = names_of(real)
            if open_set:
                continue
            for name in ref.names:
                if name == "*" or name in names or exists(tree.alias(f"{real}.{name}")):
                    continue
                unresolved.append({"file": rel, "line": ref.line, "module": ref.target, "name": name})
    return {"deleted_modules": sorted(deleted), "hits": hits, "unresolved": unresolved}


def _unresolved_key(item: dict[str, object]) -> str:
    return f"{item['file']}::{item['module']}::{item['name']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    p_closure = sub.add_parser("closure")
    p_closure.add_argument("--entries", default=",".join(DEFAULT_ENTRIES))
    p_closure.add_argument("--scope", choices=["top", "all"], default="top")
    p_closure.add_argument("--out", type=Path)
    p_check = sub.add_parser("check")
    p_check.add_argument("--delete", type=Path, help="file of paths or globs (repo- or root-relative)")
    p_check.add_argument("--baseline", type=Path, help="JSON list of already-unresolved keys to ignore")
    p_check.add_argument("--write-baseline", type=Path, help="write the current unresolved keys and exit 0")
    p_check.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    tree = Tree.scan(root)
    tree.load_facades()
    if args.command == "closure":
        result = closure(tree, [e for e in args.entries.split(",") if e], args.scope)
        authored = sorted(m for m in result if _AUTHORED_NAME.search(m))
        if args.out:
            args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print(f"scope={args.scope} modules={len(result)} authored-or-slidespec-named={len(authored)}")
        return 0

    report = check(tree, _load_delete_set(tree, args.delete, root.parents[1]))
    keys = sorted({_unresolved_key(u) for u in report["unresolved"]})
    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(keys, indent=1) + "\n", encoding="utf-8")
        print(f"baseline written: {len(keys)} unresolved names")
        return 0
    known = set(json.loads(args.baseline.read_text(encoding="utf-8"))) if args.baseline else set()
    new_unresolved = [u for u in report["unresolved"] if _unresolved_key(u) not in known]
    report["new_unresolved"] = new_unresolved
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    source_hits = [h for h in report["hits"] if not h["test"]]
    test_hits = [h for h in report["hits"] if h["test"]]
    for hit in source_hits:
        print(f"HIT {hit['file']}:{hit['line']} [{hit['kind']}/{hit['scope']}] -> {', '.join(hit['target'])}")
    for item in new_unresolved:
        print(f"UNRESOLVED {item['file']}:{item['line']} from {item['module']} import {item['name']}")
    print(
        f"deleted={len(report['deleted_modules'])} source_hits={len(source_hits)} "
        f"test_hits={len(test_hits)} new_unresolved={len(new_unresolved)}"
    )
    return 1 if source_hits or new_unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
