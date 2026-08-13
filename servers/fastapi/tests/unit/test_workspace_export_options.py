from __future__ import annotations

import pytest

from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
)
from services.workspace_quality_export_operation_handlers import (
    _resolve_workspace_export_options,
)


def test_workspace_export_defaults_to_fidelity() -> None:
    assert _resolve_workspace_export_options(
        {"context": {"format": "pptx"}},
        export_format="pptx",
    ) == ("fidelity", False)


def test_workspace_export_preserves_explicit_hybrid_options() -> None:
    assert _resolve_workspace_export_options(
        {
            "context": {
                "format": "pptx",
                "pptx_mode": "hybrid",
                "font_embedding": True,
            }
        },
        export_format="pptx",
    ) == ("hybrid", True)


@pytest.mark.parametrize(
    ("payload", "export_format", "error_code"),
    [
        (
            {"context": {"pptx_mode": "editable"}},
            "pptx",
            "workspace_export_pptx_mode_invalid",
        ),
        (
            {"context": {"pptx_mode": "hybrid", "font_embedding": "yes"}},
            "pptx",
            "workspace_export_font_embedding_invalid",
        ),
        (
            {"context": {"font_embedding": True}},
            "pptx",
            "workspace_export_font_embedding_requires_hybrid",
        ),
    ],
)
def test_workspace_export_rejects_invalid_or_implicit_hybrid_options(
    payload: dict,
    export_format: str,
    error_code: str,
) -> None:
    with pytest.raises(NonRetryableWorkspaceOperationError) as caught:
        _resolve_workspace_export_options(payload, export_format=export_format)

    assert caught.value.code == error_code
