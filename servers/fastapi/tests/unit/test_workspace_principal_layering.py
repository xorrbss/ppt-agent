"""Workspace principals share identity without importing the HTTP boundary."""

import ast
from pathlib import Path


def test_workspace_principal_has_no_api_dependency():
    source = Path(__file__).resolve().parents[2] / "services/workspace_operation_principal.py"
    imports = ast.walk(ast.parse(source.read_text(encoding="utf-8")))
    assert not any(
        isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "api"
        or isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "api" for alias in node.names)
        for node in imports
    )


def test_api_principal_reexports_the_shared_identity():
    from api.v1.auth import principal as api_principal
    from models import principal

    for name in ("PrincipalEnvelope", "is_loopback_development_request", "local_development_tenant_id"):
        assert getattr(api_principal, name) is getattr(principal, name)
