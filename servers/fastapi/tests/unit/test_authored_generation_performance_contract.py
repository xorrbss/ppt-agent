import os
from types import SimpleNamespace

import pytest

from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from services.authored_generation_progress_poller import (
    advance_authored_progress_heartbeat,
    describe_authored_progress,
)
from services.authored_presentation_generation import (
    _artifact_bytes_metric,
    _gate_failure_signature,
    _outline_stage_started_at,
    _use_single_slide_header_fast_path,
    _worker_handle_count_metric,
    _worker_peak_memory_metric,
)
from services.presentation_generation_operation_handler import (
    _begin_upstream_outline_timing,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows process metrics only")
def test_windows_worker_resource_metrics_use_valid_process_handle() -> None:
    memory = _worker_peak_memory_metric()
    handles = _worker_handle_count_metric()

    assert memory["state"] == "observed"
    assert int(memory["value"]) > 0
    assert handles["state"] == "observed"
    assert int(handles["value"]) > 0


def test_artifact_bytes_metric_observes_source_and_fails_closed(tmp_path) -> None:
    source_path = tmp_path / "export.pptx"
    source_bytes = b"exact-export-bytes"
    source_path.write_bytes(source_bytes)

    observation = _artifact_bytes_metric(source_path)

    assert observation == {"state": "observed", "value": len(source_bytes)}
    with pytest.raises(RuntimeError, match="^artifact_bytes_collection_failed$"):
        _artifact_bytes_metric(tmp_path / "missing.pptx")


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
