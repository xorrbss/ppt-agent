"""Windows-local entrypoints for the opt-in public v2 product-path proof.

The shared HTTP assertions remain in the original live-contract module so the
route evidence and durable-state observations cannot drift between launchers.
Only this file is selected by the Windows RC launcher.
"""

from test_public_generation_export_live_product_path import (
    test_live_http_fidelity_export_only_uses_existing_presentation,
    test_live_http_generation_then_fidelity_export_reaches_artifact_publication,
)


def test_windows_http_generation_export() -> None:
    test_live_http_generation_then_fidelity_export_reaches_artifact_publication()


def test_windows_export_only() -> None:
    test_live_http_fidelity_export_only_uses_existing_presentation()
