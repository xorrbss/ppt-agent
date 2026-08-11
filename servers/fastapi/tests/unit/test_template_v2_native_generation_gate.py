from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from models.sql.slide import SlideModel
from api.v1.ppt.endpoints import presentation_generate as endpoint
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


def test_native_storage_details_collapse_to_safe_generation_error(monkeypatch):
    def reject(*args, **kwargs):
        raise generation.TemplateV2NativeStorageError(
            "template_v2_native_source_missing"
        )

    monkeypatch.setattr(generation, "read_native_source", reject)

    with pytest.raises(generation.TemplateV2GenerationError) as rejected:
        generation.preflight_template_v2_native_pptx(
            target=_target(),
            slides=[_slide()],
            select_for_export=True,
        )

    assert rejected.value.code == "template_v2_native_source_invalid"


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


def test_sync_generation_reuses_stable_export_identity_and_separates_render_target(
    monkeypatch: pytest.MonkeyPatch,
):
    render_id = uuid.uuid4()
    target_id = uuid.uuid4()
    calls: list[dict] = []
    sentinel = object()

    async def capture(_session, **kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(endpoint, "execute_presentation_export", capture)

    class Session:
        async def get(self, *_args, **_kwargs):
            raise AssertionError("sync export must not load a workspace operation")

    request = SimpleNamespace(
        export_as="pptx",
        model_dump=lambda **_kwargs: {
            "content": "same request",
            "export_as": "pptx",
        },
    )
    publication_target = SimpleNamespace(
        id=target_id,
        revision=4,
        owner_id=None,
    )
    render = SimpleNamespace(
        id=render_id,
        revision=0,
        title="Isolated render",
    )

    async def scenario():
        first = await endpoint._execute_generation_export(
            Session(),
            publication_target=publication_target,
            render_presentation=render,
            request=request,
            export_cookie_header=None,
            generation_job=None,
        )
        second = await endpoint._execute_generation_export(
            Session(),
            publication_target=publication_target,
            render_presentation=render,
            request=request,
            export_cookie_header=None,
            generation_job=None,
        )
        assert first is second is sentinel

    asyncio.run(scenario())

    assert len(calls) == 2
    assert calls[0]["render_presentation_id"] == render_id
    assert calls[0]["attempt_presentation_id"] == target_id
    assert calls[0]["expected_render_revision"] == 0
    assert calls[0]["source_revision"] == 5
    assert calls[0]["idempotency_key_hash"] == calls[1]["idempotency_key_hash"]
    assert calls[0]["request_hash"] == calls[1]["request_hash"]


def test_generation_publication_passes_exact_router_digest_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    target_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    expected_digest = "a" * 64

    async def prepare(_session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            artifact_id=artifact_id,
            public_path="/app_data/exact.pptx",
            source_revision=5,
        )

    async def publish(_session, **_kwargs):
        return 5

    monkeypatch.setattr(endpoint, "prepare_workspace_generation_artifact", prepare)
    monkeypatch.setattr(endpoint, "publish_workspace_generation_artifact", publish)

    class Session:
        async def rollback(self):
            return None

    execution = SimpleNamespace(
        attempt_id=attempt_id,
        source_snapshot_sha256="b" * 64,
        provenance={"schema_version": 1},
        route=SimpleNamespace(
            result=SimpleNamespace(
                artifact_sha256=expected_digest,
                artifact_size_bytes=1234,
                selected_adapter="general",
            )
        ),
    )
    presentation = SimpleNamespace(
        id=target_id,
        revision=4,
        lifecycle_status="staging",
    )
    generation_job = SimpleNamespace(
        workspace_operation_id=operation_id,
        attempt_token="attempt",
    )

    published = asyncio.run(
        endpoint._publish_staged_presentation(
            Session(),
            presentation,
            "C:/temp/rendered.pptx",
            generation_job,
            export_execution=execution,
        )
    )

    assert published == "/app_data/exact.pptx"
    assert captured["expected_source_sha256"] == expected_digest
    assert captured["expected_source_size_bytes"] == 1234
