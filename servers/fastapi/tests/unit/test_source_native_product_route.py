from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from services.authored_vnext.design_pack import ingest_design_pack
from services.authored_vnext.generation_route import (
    GenerationRouteError,
    design_pack_storage_root,
    resolve_generation_route,
)
from services.authored_vnext.narrative_ir.contracts import NarrativeSlideDraftV1
from services.authored_vnext.source_native_generation import _replace_frame_text
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def test_owner_scoped_product_route_resolves_exact_pack_and_replaces_only_server_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        manifest = ingest_design_pack(
            {
                "schemaVersion": "authored-design-source/v1",
                "canvas": {"width": 1280, "height": 720},
                "frames": [
                    {
                        "id": "slide-1",
                        "type": "html",
                        "role": "cover",
                        "html": "<html><body><h1 data-qid='title'>OLD</h1><p data-qid='body'>OLD BODY</p></body></html>",
                        "elements": [
                            {
                                "id": "title",
                                "kind": "text",
                                "editable": True,
                                "semanticRole": "title",
                                "bounds": {"x": 60, "y": 60, "width": 900, "height": 120},
                            }
                        ],
                    }
                ],
            },
            pack_id="devax-source-native",
            package_version="1.0.0",
            storage_root=design_pack_storage_root(owner_id),
        )
        resolution = resolve_generation_route(
            generation_mode="registered-template",
            design_pack_pin={
                "designPackId": manifest.pack_id,
                "version": 1,
                "digest": manifest.package_digest,
            },
            slide_count=1,
        )
        assert resolution == {
            "selectedRoute": "source-native",
            "narrativeIrDigest": None,
            "fallbackReason": None,
        }

        slide = NarrativeSlideDraftV1(
            slide_key="slide-001",
            sequence_index=0,
            role="cover",
            key_message="DevAX AI 개발 플랫폼",
            supporting_points=["품질 우선 모듈 구조", "안전한 배포 자동화"],
            frame_semantics=["source-frame-001"],
            density="sparse",
        )
        compiled, semantic = _replace_frame_text(
            "<html><body><h1 data-qid='title'>OLD</h1><p data-qid='body'>OLD BODY</p></body></html>",
            slide,
        )
        assert "OLD" not in compiled
        assert "DevAX AI 개발 플랫폼" in compiled
        assert [item["qid"] for item in semantic] == ["title", "body", "slide-001-narrative"]

        with pytest.raises(GenerationRouteError, match="design_pack_pin_digest_mismatch"):
            resolve_generation_route(
                generation_mode="registered-template",
                design_pack_pin={
                    "designPackId": manifest.pack_id,
                    "version": 1,
                    "digest": "sha256:" + "0" * 64,
                },
                slide_count=1,
            )
    finally:
        reset_current_owner_id(token)
