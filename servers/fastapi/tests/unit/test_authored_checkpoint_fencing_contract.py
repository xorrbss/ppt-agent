"""Every checkpoint write from the authored pipeline must carry its fence.

``_owned_job`` rejects a workspace-linked checkpoint write whose ``fencing_token``
is not an integer, so a call site that forgets the keyword fails the whole
generation with ``authored_workspace_operation_fencing_token_required`` instead
of doing its work. That is exactly what happened when the batch-authoring path
and the fencing change landed in parallel: the batch path's ``start_stage``,
``reserve_provider_attempt``, ``get_checkpoint``, ``complete_stage`` and
``fail_stage`` calls were never given the token, and every linked authored
generation died at its first authored slide.

A unit test with mocks would only cover the paths it happens to drive, so this
reads the modules instead and requires the keyword at every call site.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Checkpoint entry points that reach ``_owned_job`` and therefore fence.
FENCED_CALLS = frozenset({
    "initialize_checkpoints",
    "start_stage",
    "complete_stage",
    "_complete_stage_after_external_success",
    "fail_stage",
    "reserve_provider_attempt",
    "get_checkpoint",
})

# Modules that write checkpoints for a workspace-linked generation job.
FENCED_MODULES = (
    "services/authored_presentation_pipeline.py",
    "services/authored_illustration_stage.py",
)


def _service_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _unfenced_calls(relative_path: str) -> list[tuple[str, int]]:
    source = (_service_root() / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else None
        )
        if name not in FENCED_CALLS:
            continue
        keywords = {keyword.arg for keyword in node.keywords}
        if "fencing_token" not in keywords:
            offenders.append((name, node.lineno))
    return offenders


@pytest.mark.parametrize("relative_path", FENCED_MODULES)
def test_every_checkpoint_write_passes_its_fencing_token(relative_path: str) -> None:
    offenders = _unfenced_calls(relative_path)

    assert offenders == [], (
        f"{relative_path} calls a fenced checkpoint entry point without "
        f"fencing_token at: {offenders}"
    )


def test_the_guard_detects_a_missing_fencing_token() -> None:
    # Guard the guard: the walk must actually reject an unfenced call.
    tree = ast.parse("await start_stage(job_id=1, slide_index=0, attempt_token='t')")
    names = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "start_stage" in names
    assert all(
        "fencing_token" not in {keyword.arg for keyword in node.keywords}
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )
