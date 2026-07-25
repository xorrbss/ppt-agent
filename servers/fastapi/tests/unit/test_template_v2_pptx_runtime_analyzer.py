"""Wiring between the analyzer selector and the two extraction backends.

The two paths deliberately converge at the confirmed draft rather than at the
analysis: the deterministic one replays parser candidates through the assembler,
while the runtime one stores already-validated layouts.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from types import SimpleNamespace
from services import template_v2_pptx_ingestion_service as ingestion


def _runtime_layout(data_url: str) -> dict:
    return {
        "id": "slide_1",
        "elements": [
            {
                "type": "image",
                "position": {"x": 10.0, "y": 20.0},
                "size": {"width": 100.0, "height": 50.0},
                "data": data_url,
                "fit": "fill",
                "decorative": True,
                "name": "picture_1",
                "is_icon": False,
            }
        ],
    }


class _Relocated:
    """Stands in for the storage layer's relocation result."""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def reference_for(self, url: str) -> str | None:
        return self._mapping.get(url)


def test_image_urls_are_repointed_at_the_relocated_private_assets():
    runtime_url = "http://host/app_data/pptx-to-json/abc/images/x.png"
    reference = "/api/v1/ppt/structured-templates/imports/i/assets/x.png"

    rewritten = ingestion._with_private_asset_references(
        [_runtime_layout(runtime_url)], _Relocated({runtime_url: reference})
    )

    assert rewritten[0]["elements"][0]["data"] == reference


def test_unrelocated_urls_are_left_alone():
    """Only media the storage layer actually moved may be rewritten."""
    runtime_url = "http://host/app_data/images/shared.png"

    rewritten = ingestion._with_private_asset_references(
        [_runtime_layout(runtime_url)], _Relocated({})
    )

    assert rewritten[0]["elements"][0]["data"] == runtime_url


def test_runtime_analysis_is_marked_and_carries_both_projections(monkeypatch, tmp_path):
    import_id = uuid.uuid4()
    runtime_url = "http://host/app_data/pptx-to-json/abc/images/x.png"
    reference = "/api/v1/ppt/structured-templates/imports/i/assets/x.png"
    source = tmp_path / "source.pptx"
    source.write_bytes(b"PK\x03\x04")

    class _Document:
        layouts = [_runtime_layout(runtime_url)]
        output_dir = str(tmp_path)

    async def fake_convert(_path, *, session_id):
        assert session_id == str(import_id)
        return _Document()

    monkeypatch.setattr(ingestion, "verify_private_source", lambda *a, **k: source)
    monkeypatch.setattr(ingestion.EXPORT_TASK_SERVICE, "convert_pptx_to_json", fake_convert)
    monkeypatch.setattr(
        ingestion, "relocate_runtime_assets", lambda *a, **k: _Relocated({runtime_url: reference})
    )

    analysis, suggestions, inventory = asyncio.run(
        ingestion._analyze_import_source_via_runtime(
            "key",
            "a" * 64,
            import_id=import_id,
            source_filename="deck.pptx",
            source_media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            source_size_bytes=4,
        )
    )

    assert analysis["analyzer"] == ingestion.RUNTIME_ANALYSIS_MARKER
    assert analysis["layouts"]["layouts"][0]["id"] == "slide_1"
    assert analysis["raw_layouts"]["layouts"][0]["id"] == "slide_1"
    # repeat blocks are candidate-derived, so this path has none
    assert suggestions == []
    assert "source_path" not in repr(inventory)


def test_confirm_rebuilds_the_draft_from_stored_runtime_layouts():
    analysis = {
        "analyzer": ingestion.RUNTIME_ANALYSIS_MARKER,
        "raw_layouts": {
            "layouts": [
                {
                    "id": "slide_1",
                    "description": "Runtime PPTX import of layout slide_1.",
                    "elements": [],
                }
            ]
        },
        "layouts": {
            "layouts": [
                {
                    "id": "slide_1",
                    "description": "Runtime PPTX import of layout slide_1.",
                    "components": [],
                }
            ]
        },
    }
    job = SimpleNamespace(analysis_result=analysis)

    draft = ingestion._assemble_confirmed_candidate(job, [])

    assert [layout.id for layout in draft.layouts.layouts] == ["slide_1"]
    # one empty mapping per layout: the converter marks everything decorative, so
    # the template schema is empty and the layout is taken as-is
    assert draft.contents == [{}]


def test_confirm_rejects_a_runtime_analysis_missing_its_layouts():
    job = SimpleNamespace(analysis_result={"analyzer": ingestion.RUNTIME_ANALYSIS_MARKER})

    with pytest.raises(ValueError, match="template_v2_import_runtime_layouts_missing"):
        ingestion._assemble_confirmed_candidate(job, [])


def test_deterministic_analysis_still_replays_candidates():
    """The default path must be untouched by the new branch."""
    job = SimpleNamespace(analysis_result={"summary": {}})

    with pytest.raises(ValueError, match="template_v2_import_candidate_missing"):
        ingestion._assemble_confirmed_candidate(job, [])
