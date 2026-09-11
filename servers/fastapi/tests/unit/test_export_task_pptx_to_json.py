"""`ExportTaskService.convert_pptx_to_json` transport contract.

Both details pinned here were measured against the pinned runtime (v0.4.2) rather
than read from upstream, because upstream's own caller disagrees with the binary:
it omits `session_id`, which the task schema rejects.
"""

import asyncio
import json
import os

import pytest
from fastapi import HTTPException

from services.export_task_service import ExportTaskService, PptxToJsonDocument


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        ExportTaskService, "_resolve_export_dir", staticmethod(lambda: "/export")
    )
    monkeypatch.setattr(
        ExportTaskService,
        "_resolve_entrypoint_path",
        staticmethod(lambda _d: "/export/index.cjs"),
    )
    monkeypatch.setattr(
        ExportTaskService,
        "_resolve_converter_path",
        staticmethod(lambda _d: "/export/py/convert"),
    )
    return ExportTaskService()


def _capture(service, monkeypatch, output_path):
    """Stub the runtime spawn, returning the `file://` response shape it really emits."""
    seen: dict = {}

    async def fake_run_task(payload, _detail):
        seen.update(payload)
        return {"url": "file:///" + str(output_path).replace(os.sep, "/")}

    monkeypatch.setattr(service, "_run_task", fake_run_task)
    monkeypatch.setattr(
        ExportTaskService, "_resolve_output_path", staticmethod(lambda _r: str(output_path))
    )
    return seen


def _source(tmp_path):
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"PK\x03\x04")
    return source


def test_task_payload_carries_session_id(service, monkeypatch, tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "presentation.json"
    output.write_text(
        json.dumps({"layouts": [{"id": "slide_1", "elements": []}]}), encoding="utf-8"
    )
    seen = _capture(service, monkeypatch, output)

    asyncio.run(service.convert_pptx_to_json(str(source), session_id="import-42"))

    assert seen["type"] == "pptx-to-json"
    assert seen["pptx_path"] == str(source)
    assert seen["session_id"] == "import-42", "the converter's task schema requires it"


def test_output_dir_defaults_to_the_response_location(service, monkeypatch, tmp_path):
    """Extracted media sits beside the JSON, and the runtime picks that directory itself."""
    source = _source(tmp_path)
    run_dir = tmp_path / "pptx-to-json" / "8d1f"
    run_dir.mkdir(parents=True)
    output = run_dir / "presentation.json"
    output.write_text(json.dumps({"layouts": []}), encoding="utf-8")
    _capture(service, monkeypatch, output)

    document = asyncio.run(
        service.convert_pptx_to_json(str(source), session_id="import-42")
    )

    assert document.output_dir == str(run_dir)


def test_missing_source_is_rejected_before_spawning(service, monkeypatch, tmp_path):
    async def explode(*_args, **_kwargs):
        raise AssertionError("runtime must not be spawned for a missing source")

    monkeypatch.setattr(service, "_run_task", explode)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            service.convert_pptx_to_json(str(tmp_path / "absent.pptx"), session_id="x")
        )

    assert error.value.status_code == 400


def test_invalid_json_output_is_a_500(service, monkeypatch, tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "presentation.json"
    output.write_text("{not json", encoding="utf-8")
    _capture(service, monkeypatch, output)

    with pytest.raises(HTTPException) as error:
        asyncio.run(service.convert_pptx_to_json(str(source), session_id="x"))

    assert error.value.status_code == 500


def test_output_missing_layouts_is_a_500(service, monkeypatch, tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "presentation.json"
    output.write_text(json.dumps({"slides": []}), encoding="utf-8")
    _capture(service, monkeypatch, output)

    with pytest.raises(HTTPException) as error:
        asyncio.run(service.convert_pptx_to_json(str(source), session_id="x"))

    assert error.value.status_code == 500


def test_document_keeps_unknown_element_fields():
    """A converter upgrade must not fail at the transport layer."""
    document = PptxToJsonDocument(
        layouts=[{"id": "slide_1", "elements": [{"type": "text", "future_field": 1}]}]
    )

    assert document.layouts[0]["elements"][0]["future_field"] == 1
