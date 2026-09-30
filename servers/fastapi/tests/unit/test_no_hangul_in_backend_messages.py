"""The backend emits message keys; Korean text lives in the Next.js catalog.

``constants/user_messages.py`` is the single contract for user-facing text.
This gate walks the AST of every module under ``services``, ``api``, ``utils``,
``models`` and ``constants`` and fails on any Hangul string literal outside
``_ALLOWED_HANGUL_MODULES`` -- docstrings and comments are not literals that
reach a client, so they are ignored.

The allow-list names modules whose Hangul is *by design*: prompt text and
examples the LLM reads, language-detection tables and Korean linguistic
heuristics (regexes, particles, unit suffixes), and catalog data records with
an explicit locale field.  Each entry must still contain Hangul: an entry that
no longer does fails the gate so the list can only shrink.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path



BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCANNED_TREES = ("services", "api", "utils", "models", "constants")
_HANGUL = re.compile(r"[가-힣]")

# Modules whose Hangul string literals are prompt text, linguistic heuristics
# or locale-tagged catalog data rather than user-facing UI messages.
_ALLOWED_HANGUL_MODULES: frozenset[str] = frozenset(
    {
        # --- LLM prompt text and examples (the model reads Korean) ---
        "constants/presentation.py",
        "services/authored_title_recovery_contract.py",
        "services/authored_vnext/body_grammar.py",
        "services/authored_vnext/generation/ai_text_zones.py",
        "services/authored_vnext/generation/legacy_bridge.py",
        "services/authored_vnext/generation/retained_reference.py",
        "services/authored_vnext/generation/scene_compiler.py",
        "services/authored_vnext/master/ai_import.py",
        "services/authored_vnext/master/catalog_seed.py",
        "services/authored_vnext/source_native_generation.py",
        "services/authored_vnext/source_unitization.py",
        "services/chat/service.py",
        "models/authored_illustration_plan.py",
        "utils/authored_infographic/icons.py",
        "utils/authored_style_converter.py",
        "utils/llm_calls/author_chapter_plan.py",
        "services/generation_core/authoring/author_deck.py",
        "utils/llm_calls/author_header_prompt_clauses.py",
        "utils/llm_calls/author_infographic_plan.py",
        "utils/llm_calls/author_slide_brief.py",
        "utils/llm_calls/author_slide_rules.py",
        # --- Korean linguistic heuristics and language-detection tables ---
        "services/authored_quality_ocr.py",
        "services/authored_source_images.py",
        "services/authored_vnext/deck_ir/product_adapter.py",
        "services/generation_core/drivers/svg/native_chart.py",
        "services/generation_core/style/build.py",
        "services/source_content/raster_structure_emit.py",
        "utils/authored_font_policy.py",
        "services/authored_semantic_role_repair.py",
        "utils/ocr_language.py",
        # --- Catalog data records with an explicit locale field ---
        "constants/provider_registry_generated.py",
        "utils/artifact_style_builder.py",
        "utils/authored_styles.py",
    }
)


def _ignored_constant_ids(tree: ast.AST) -> set[int]:
    """Docstrings and ``re.*`` pattern arguments never reach a client."""

    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
        ):
            # A regex over Korean particles or unit suffixes is a heuristic.
            for child in ast.walk(node):
                if isinstance(child, ast.Constant):
                    ids.add(id(child))
    return ids


def hangul_string_literals(path: Path) -> list[tuple[int, str]]:
    """``(line, literal)`` for every Hangul string constant that is a message."""

    source = path.read_text(encoding="utf-8")
    if not _HANGUL.search(source):
        return []
    tree = ast.parse(source, filename=str(path))
    ignored = _ignored_constant_ids(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in ignored
        and _HANGUL.search(node.value)
    ]


def _scanned_modules() -> list[Path]:
    modules: list[Path] = []
    for tree in SCANNED_TREES:
        for path in sorted((BACKEND_ROOT / tree).rglob("*.py")):
            if "tests" in path.relative_to(BACKEND_ROOT).parts:
                continue
            modules.append(path)
    return modules


def _relative(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def test_backend_modules_emit_message_keys_not_hangul():
    offenders: list[str] = []
    for path in _scanned_modules():
        relative = _relative(path)
        if relative in _ALLOWED_HANGUL_MODULES:
            continue
        for line, literal in hangul_string_literals(path):
            offenders.append(f"{relative}:{line}: {literal[:60]!r}")
    assert not offenders, (
        "Hangul string literal outside the allow-list. User-facing text must be "
        "a constants.user_messages key rendered by the frontend; prompt text or "
        "a linguistic heuristic belongs in _ALLOWED_HANGUL_MODULES with a "
        "reason:\n" + "\n".join(offenders)
    )


def test_hangul_allow_list_only_shrinks():
    scanned = {_relative(path) for path in _scanned_modules()}
    missing = sorted(_ALLOWED_HANGUL_MODULES - scanned)
    assert not missing, f"allow-listed modules no longer exist: {missing}"
    clean = sorted(
        relative
        for relative in _ALLOWED_HANGUL_MODULES
        if not hangul_string_literals(BACKEND_ROOT / relative)
    )
    assert not clean, (
        "allow-listed modules contain no Hangul literal any more; remove them "
        f"from _ALLOWED_HANGUL_MODULES: {clean}"
    )
