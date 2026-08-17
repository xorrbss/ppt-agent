from __future__ import annotations

import pytest

from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
)
from services.workspace_quality_export_operation_handlers import (
    _resolve_workspace_export_options,
    _validate_authored_vnext_export_request,
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


def test_native_master_export_requires_exact_sealed_pin_and_fidelity_mode() -> None:
    digest = f"sha256:{'a' * 64}"
    layout_digest = f"sha256:{'b' * 64}"
    font_digest = f"sha256:{'c' * 64}"
    projection = {
        "masterPin": {
            "masterId": "master:company",
            "masterRevision": 7,
            "masterDigest": digest,
            "layoutRegistryDigest": layout_digest,
            "fontManifestDigest": font_digest,
        }
    }
    request_pin = {
        "master_id": "master:company",
        "revision": 7,
        "digest": digest,
        "layout_registry_digest": layout_digest,
        "font_manifest_digest": font_digest,
    }

    _validate_authored_vnext_export_request(
        {"context": {"master_pin": request_pin}},
        projection=projection,
        export_format="pptx",
        pptx_mode="fidelity",
        font_embedding=False,
    )

    with pytest.raises(
        NonRetryableWorkspaceOperationError,
        match="authored_vnext_export_master_changed",
    ):
        _validate_authored_vnext_export_request(
            {
                "context": {
                    "master_pin": {
                        **request_pin,
                        "font_manifest_digest": f"sha256:{'d' * 64}",
                    }
                }
            },
            projection=projection,
            export_format="pptx",
            pptx_mode="fidelity",
            font_embedding=False,
        )

    with pytest.raises(
        NonRetryableWorkspaceOperationError,
        match="authored_vnext_native_export_mode_required",
    ):
        _validate_authored_vnext_export_request(
            {},
            projection=projection,
            export_format="pptx",
            pptx_mode="hybrid",
            font_embedding=False,
        )
