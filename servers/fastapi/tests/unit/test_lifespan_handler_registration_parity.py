"""api/lifespan.py's embedded-worker handler list must not drift from the
complete list services/workspace_operation_worker.py::register_workspace_operation_handlers
already maintains for the external-worker path.

Found live (2026-09-26): register_workspace_authored_preview_operation_handlers
was present in the external-worker list but missing from app_lifespan's
hand-duplicated embedded list, so a batch (and single-page) remake preview
operation was admitted and then sat "queued" forever -- no handler was ever
registered for it in the embedded worker mode most local/dev/single-process
deployments use.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import api.lifespan as lifespan_module
import services.workspace_operation_worker as worker_module


def _register_calls_in(source: str) -> set[str]:
    """Every bare ``register_*_operation_handler(...)``/``register_*_handlers(...)``
    call name at any statement position in ``source``."""

    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("register_")
            and ("_operation_handler" in node.func.id or "_operation_handlers" in node.func.id)
        ):
            names.add(node.func.id)
    return names


def test_embedded_lifespan_registers_every_handler_the_external_worker_does():
    complete = _register_calls_in(
        inspect.getsource(worker_module.register_workspace_operation_handlers)
    )
    # register_presentation_chat_operation_handlers and
    # register_workspace_authored_template_operation_handler are called in
    # app_lifespan but outside register_workspace_operation_handlers's body
    # (the latter is the external-worker's own complete list, and a couple of
    # handlers were added to the embedded path only) -- exclude anything the
    # embedded path is not expected to mirror one-for-one, and assert only
    # that nothing in the complete list is missing from the embedded one.
    assert complete, "the complete external-worker registration list must not be empty"

    lifespan_source = Path(inspect.getfile(lifespan_module)).read_text(encoding="utf-8")
    embedded = _register_calls_in(lifespan_source)

    missing = complete - embedded
    assert not missing, (
        f"api/lifespan.py's embedded registration list is missing {sorted(missing)}; "
        "an operation of this kind will be admitted and never dispatched "
        "(queued forever) under the embedded worker mode"
    )


def test_the_regression_kind_is_registered_in_both_lists():
    assert "register_workspace_authored_preview_operation_handlers" in _register_calls_in(
        inspect.getsource(worker_module.register_workspace_operation_handlers)
    )
    lifespan_source = Path(inspect.getfile(lifespan_module)).read_text(encoding="utf-8")
    assert "register_workspace_authored_preview_operation_handlers" in _register_calls_in(
        lifespan_source
    )
