"""The pipeline accepts progress, while the operation runner owns its lease."""

import inspect

from services import presentation_generation_pipeline as pipeline


def test_pipeline_has_no_unused_lease_extension_capability():
    parameters = inspect.signature(pipeline.generate_presentation_handler).parameters
    assert "extend_lease" not in parameters
    assert not hasattr(pipeline, "GenerationLeaseExtender")
    assert "progress_reporter" in parameters
