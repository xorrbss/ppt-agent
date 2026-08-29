from types import SimpleNamespace

from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from services.authored_generation_progress_poller import (
    advance_authored_progress_heartbeat,
    describe_authored_progress,
)
from services.authored_presentation_generation import (
    _gate_failure_signature,
    _outline_stage_started_at,
    _use_single_slide_header_fast_path,
)
from services.presentation_generation_operation_handler import (
    _begin_upstream_outline_timing,
)


def test_progress_fast_path_and_terminal_signature_contracts() -> None:
    outline = PresentationOutlineModel(
        slides=[SlideOutlineModel(content="One focused slide")]
    )
    request = SimpleNamespace(
        instructions=None,
        _source_content_authored_reflow=False,
    )
    style = SimpleNamespace(reference_mode="style_reference")
    assert _use_single_slide_header_fast_path(request, outline, style) is True
    request.instructions = "Keep the requested header wording"
    assert _use_single_slide_header_fast_path(request, outline, style) is False

    first = describe_authored_progress(0, 1, "outline")
    assert first[:2] == ("authored_outline", 8)
    heartbeat = advance_authored_progress_heartbeat(first, first, "outline")
    assert heartbeat[:2] == ("authored_outline", 9)
    author = describe_authored_progress(0, 1, "author")
    assert author[0] == "authored_author"
    assert author[1] >= heartbeat[1]

    failure = {
        "slide_index": 0,
        "issues": [{"message": "browser_body:299>270"}],
    }
    original = _gate_failure_signature(["<main>same</main>"], failure)
    repeated = _gate_failure_signature(["<main>same</main>"], failure)
    changed = _gate_failure_signature(["<main>changed</main>"], failure)
    assert original == repeated
    assert original != changed


def test_outline_receipt_timing_includes_upstream_outline_provider_boundary(
    monkeypatch,
) -> None:
    request = SimpleNamespace(template="authored")
    monkeypatch.setattr(
        "services.presentation_generation_operation_handler.time.monotonic",
        lambda: 100.0,
    )
    _begin_upstream_outline_timing(request)

    monkeypatch.setattr(
        "services.authored_presentation_generation.time.monotonic",
        lambda: 175.0,
    )
    started_at = _outline_stage_started_at(request)

    assert started_at == 100.0
    assert round((175.0 - started_at) * 1000) == 75_000
