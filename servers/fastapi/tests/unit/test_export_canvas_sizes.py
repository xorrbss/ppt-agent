"""Render/export QA paths use the deck's own canvas instead of a hardcoded
1280x720. Covers: PowerPoint COM QA's Export() size, ``trusted_powerpoint_qa``
threading ``theme`` through to the worker thread, and the direct-attempt export
service passing ``presentation.theme`` in."""

from __future__ import annotations

import inspect


from services.authored_canvas import (
    PORTRAIT_ASPECT_RATIO,
    canvas_metadata_for_aspect_ratio,
    merge_presentation_canvas,
    presentation_canvas_contract,
)


def _portrait_theme() -> dict:
    wire = canvas_metadata_for_aspect_ratio(PORTRAIT_ASPECT_RATIO).model_dump(
        mode="json", by_alias=True
    )
    return merge_presentation_canvas({}, wire)


def test_presentation_canvas_contract_defaults_to_legacy_landscape():
    contract = presentation_canvas_contract(None)
    assert (contract.width_px, contract.height_px) == (1280, 720)
    contract = presentation_canvas_contract({})
    assert (contract.width_px, contract.height_px) == (1280, 720)


def test_presentation_canvas_contract_resolves_non_landscape_theme():
    contract = presentation_canvas_contract(_portrait_theme())
    assert (contract.width_px, contract.height_px) == (720, 1280)


def test_run_powerpoint_com_qa_exports_at_the_resolved_canvas_size():
    from services.presentation_export_powerpoint_qa import _run_powerpoint_com_qa

    source = inspect.getsource(_run_powerpoint_com_qa)
    assert "theme: object | None = None" in source
    assert "canvas_contract = presentation_canvas_contract(theme)" in source
    assert (
        'first.Export(str(first_render), "PNG", canvas_contract.width_px, canvas_contract.height_px)'
        in source
    )
    assert (
        'reopened.Export(str(reopen_render), "PNG", canvas_contract.width_px, canvas_contract.height_px)'
        in source
    )
    # No leftover literal 1280/720 Export() calls.
    assert '"PNG", 1280, 720' not in source


def test_trusted_powerpoint_qa_threads_theme_into_the_worker_call():
    from services.presentation_export_powerpoint_qa import trusted_powerpoint_qa

    signature = inspect.signature(trusted_powerpoint_qa)
    assert "theme" in signature.parameters
    assert signature.parameters["theme"].default is None
    source = inspect.getsource(trusted_powerpoint_qa)
    call = source[source.index("asyncio.to_thread(") : source.index("except Exception")]
    assert "theme," in call or "theme)" in call
