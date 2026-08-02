from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from models.sql.slide import SlideModel
from services import template_v2_generation_service as generation
from templates.v2.models.layouts import SlideLayout
from templates.v2.pptx.native_compiler import TemplateV2NativePptxCompileError


def _layout() -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": "native-layout",
            "description": "Native clone generation gate layout",
            "components": [
                {
                    "id": "hero",
                    "description": "Editable native clone title",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "text",
                            "position": {"x": 0, "y": 0},
                            "size": {"width": 640, "height": 80},
                            "runs": [{"text": "Original"}],
                            "decorative": False,
                            "name": "title",
                            "min_length": 1,
                            "max_length": 80,
                        }
                    ],
                }
            ],
        }
    )


def _target() -> generation.TemplateV2GenerationTarget:
    layout = _layout()
    return generation.TemplateV2GenerationTarget(
        template_id="owned-template",
        revision=1,
        name="Owned Template",
        snapshot_sha256="a" * 64,
        layouts=(
            generation.TemplateV2GenerationLayout(
                layout=layout,
                content_schema={"type": "object"},
            ),
        ),
        preservation_mode="native_clone",
        native_source=generation.TemplateV2NativeSource(
            storage_key=(
                "template-v2/native/owned-template/revision-1/source.pptx"
            ),
            source_sha256="b" * 64,
            source_size_bytes=4,
            layout_source_parts={"native-layout": "ppt/slides/slide1.xml"},
            capabilities={
                "native_clone": True,
                "text_patch": True,
                "table_patch": True,
                "master_layout_theme": True,
                "relationships_media": True,
            },
            publication_status="publishable",
        ),
    )


def _slide() -> SlideModel:
    return SlideModel(
        presentation=uuid.uuid4(),
        layout_group="native",
        layout="native-layout",
        index=0,
        content={"hero": {"title": "Updated"}},
        ui=_layout().model_dump(mode="json"),
    )


def test_native_clone_unsupported_is_a_fail_closed_generation_error(monkeypatch):
    monkeypatch.setattr(generation, "read_native_source", lambda *args, **kwargs: b"PK")

    def reject(*args, **kwargs):
        raise TemplateV2NativePptxCompileError(
            "template_v2_native_clone_shape_missing",
            path="slides.0.components.0.elements.0",
        )

    monkeypatch.setattr(generation, "compile_template_v2_native_clone", reject)

    with pytest.raises(generation.TemplateV2GenerationError) as rejected:
        generation.preflight_template_v2_native_pptx(
            target=_target(),
            slides=[_slide()],
            select_for_export=True,
        )
    assert rejected.value.code == "template_v2_native_pptx_unsupported"


def test_native_export_writes_the_exact_preflight_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(generation, "get_exports_directory", lambda: str(tmp_path))
    payload = b"PK\x03\x04exact-native-clone"
    presentation_id = uuid.uuid4()

    result = generation.export_template_v2_native_pptx(
        presentation_id=presentation_id,
        title="Native clone",
        preflight=generation.TemplateV2NativePptxPreflight(
            status="compiled",
            preservation_mode="native_clone",
            selected_for_export=True,
            pptx=payload,
        ),
    )

    exported = Path(result.path)
    assert exported.parent == tmp_path
    assert exported.name == f"Native clone-{str(presentation_id)[:8]}.pptx"
    assert result.presentation_id == presentation_id
    assert exported.read_bytes() == payload


@pytest.mark.parametrize(
    ("select_for_export", "expected_selected"),
    ((False, False), (True, True)),
)
def test_native_preflight_selected_for_export_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    select_for_export: bool,
    expected_selected: bool,
):
    payload = b"PK\x03\x04compiled-native-clone"
    monkeypatch.setattr(generation, "read_native_source", lambda *args, **kwargs: b"PK")
    monkeypatch.setattr(
        generation,
        "compile_template_v2_native_clone",
        lambda *args, **kwargs: SimpleNamespace(
            pptx=payload,
            structural_sha256="c" * 64,
            package_sha256="d" * 64,
        ),
    )

    preflight = generation.preflight_template_v2_native_pptx(
        target=_target(),
        slides=[_slide()],
        select_for_export=select_for_export,
    )

    assert preflight.selected_for_export is expected_selected
    assert preflight.pptx == payload
    assert preflight.to_provenance()["selected_for_export"] is expected_selected
