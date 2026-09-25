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
    # ``font_embedding`` is tri-state now: ``None`` means the command context
    # named no such key, so the handler applies the CJK default once the deck
    # language is known. An explicit ``True``/``False`` still wins.
    assert _resolve_workspace_export_options(
        {"context": {"format": "pptx"}},
        export_format="pptx",
    ) == ("fidelity", None)


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


def test_svg_font_embedding_does_not_require_hybrid_mode() -> None:
    """svg has no pptx_mode concept; its own font_embedding request is
    validated inside execute_svg_workspace_export/render_and_export_svgs,
    not here. Regression: this used to reject every real svg font_embedding
    request with workspace_export_font_embedding_requires_hybrid, since svg
    never sets pptx_mode="hybrid" (an authored-only value) -- found live on
    a real request (2026-09-26)."""
    assert _resolve_workspace_export_options(
        {"context": {"font_embedding": True}},
        export_format="pptx",
        is_svg=True,
    ) == ("fidelity", True)


def test_authored_font_embedding_still_requires_hybrid_mode_when_is_svg_is_false() -> None:
    with pytest.raises(NonRetryableWorkspaceOperationError) as caught:
        _resolve_workspace_export_options(
            {"context": {"font_embedding": True}},
            export_format="pptx",
            is_svg=False,
        )
    assert caught.value.code == "workspace_export_font_embedding_requires_hybrid"


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
