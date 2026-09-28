from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from services.authored_vnext.design_pack import DesignPackRegistry, ingest_design_pack
from services.authored_vnext.design_pack.compiler import compile_design_source
from services.authored_vnext.design_pack.models import (
    DesignPackManifest,
    PackageFile,
    canonical_digest,
    seal_manifest,
)
from services.authored_vnext.design_pack.registry import DesignPackRegistryError
from services.authored_vnext.design_pack.variant_source import VariantDraftError
from services.authored_vnext.design_pack.variant_store import (
    VARIANT_SOURCE_FILENAME,
    approve_variant,
    draft_variant,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
BASE_PACK = "variant-base"
BASE_VERSION = "1.0.0"


def authored_source(width: int = 1280, height: int = 720) -> dict[str, Any]:
    return {
        "schemaVersion": "authored-design-source/v1",
        "canvas": {"width": width, "height": height},
        "frames": [
            {
                "id": "cover",
                "type": "scene",
                "role": "cover",
                "elements": [
                    {
                        "id": "headline",
                        "kind": "text",
                        "semanticRole": "title",
                        "editable": True,
                        "slotApproved": True,
                        "slotId": "slot:cover-headline",
                        "required": True,
                        "bounds": {"x": 80, "y": 90, "width": 720, "height": 120},
                        "textStyle": {"latinFont": "Inter", "fontSize": 32, "color": "#102A43"},
                    },
                    {
                        "id": "band",
                        "kind": "shape",
                        "bounds": {"x": 0, "y": 660, "width": 1280, "height": 60},
                        "paint": {"fill": "#FF6600"},
                    },
                ],
            }
        ],
    }


async def scaling_relayout(brief: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    assert schema["required"] == ["frames"]
    source, target = brief["sourceCanvas"], brief["targetCanvas"]
    sx, sy = target["width"] / source["width"], target["height"] / source["height"]
    return {
        "frames": [
            {
                "frameId": frame["frameId"],
                "elements": [
                    {
                        "key": element["key"],
                        "x": element["bounds"]["x"] * sx,
                        "y": element["bounds"]["y"] * sy,
                        "width": element["bounds"]["width"] * sx,
                        "height": element["bounds"]["height"] * sy,
                        "fontSize": 28 if element["fontSize"] else None,
                    }
                    for element in frame["elements"]
                ],
            }
            for frame in brief["frames"]
        ]
    }


def ingest_base(root: Path) -> DesignPackManifest:
    return ingest_design_pack(
        authored_source(), pack_id=BASE_PACK, package_version=BASE_VERSION, storage_root=root
    )


def draft(root: Path, canvas_id: str, relayout=scaling_relayout):
    return asyncio.run(
        draft_variant(
            root,
            pack_id=BASE_PACK,
            package_version=BASE_VERSION,
            canvas_id=canvas_id,
            relayout=relayout,
        )
    )


def approve(root: Path, canvas_id: str):
    return approve_variant(root, pack_id=BASE_PACK, package_version=BASE_VERSION, canvas_id=canvas_id)


def _inventory() -> tuple[PackageFile, ...]:
    return (
        PackageFile(path="structure.json", sha256="3" * 64, size_bytes=10,
                    media_type="application/json", role="structure"),
        PackageFile(path="source/reference.json", sha256="1" * 64, size_bytes=5,
                    media_type="application/json", role="source"),
    )


def _v1_values() -> dict[str, Any]:
    return {
        "pack_id": "golden-pack",
        "package_version": "1.2.3",
        "source_kind": "authored",
        "source_filename": "authored-source.json",
        "source_digest": "sha256:" + "1" * 64,
        "structure_digest": "sha256:" + "2" * 64,
        "inventory": _inventory(),
    }


def test_v1_manifest_seal_is_byte_identical_to_pre_v2_contract() -> None:
    manifest = seal_manifest(**_v1_values())
    wire = manifest.model_dump(mode="json", by_alias=True)
    # Digest captured from the pre-v2 model on 2026-09-29.
    assert manifest.package_digest == "sha256:bb278d4463af52e001ceae6fd4a712a76b4868ba9ad8cb77b5fed44ea0090cfd"
    assert not {"canvasId", "variantOf", "status"} & set(wire)
    assert manifest.effective_status == "approved"
    assert DesignPackManifest.model_validate(wire) == manifest


def test_v1_manifest_rejects_v2_fields_and_v2_requires_canvas_and_status() -> None:
    with pytest.raises(ValueError, match="design_pack_manifest_v2_fields_on_v1"):
        seal_manifest(**_v1_values(), status="approved")
    v2 = {**_v1_values(), "schema_version": "source-native-design-pack/v2"}
    with pytest.raises(ValueError, match="design_pack_canvas_id_invalid"):
        seal_manifest(**v2, canvas_id="letter-landscape", status="draft")
    with pytest.raises(ValueError, match="design_pack_status_required"):
        seal_manifest(**v2, canvas_id="a4-landscape")
    manifest = seal_manifest(**v2, canvas_id="a4-landscape", status="draft")
    wire = manifest.model_dump(mode="json", by_alias=True)
    assert wire["canvasId"] == "a4-landscape" and wire["status"] == "draft"
    assert DesignPackManifest.model_validate(wire).package_digest == manifest.package_digest


def _on_disk_manifests() -> list[Path]:
    users = REPO_ROOT / "app_data" / "design-packs" / "users"
    return sorted(users.glob("*/*/*/manifest.json")) if users.is_dir() else []


@pytest.mark.skipif(not _on_disk_manifests(), reason="no local design-pack store")
def test_every_on_disk_v1_pack_still_loads_and_seals() -> None:
    manifests = _on_disk_manifests()
    for owner_root in sorted({path.parents[2] for path in manifests}):
        registry = DesignPackRegistry(owner_root)
        assert registry.list()
    for path in manifests:
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifest = DesignPackManifest.model_validate(raw)
        wire = manifest.model_dump(mode="json", by_alias=True)
        assert wire == raw
        payload = {key: value for key, value in wire.items() if key != "packageDigest"}
        assert canonical_digest(payload) == manifest.package_digest


def test_structural_render_uses_pack_canvas_and_keeps_16x9_bytes() -> None:
    headline = {"id": "headline", "kind": "text", "semanticRole": "title", "editable": True,
                "slotApproved": True, "bounds": {"x": 80, "y": 90, "width": 720, "height": 120}}
    wide = compile_design_source({
        "schemaVersion": "authored-design-source/v1",
        "canvas": {"width": 1280, "height": 720},
        "frames": [{"id": "cover", "type": "scene", "role": "cover", "elements": [headline]}],
    })
    svg = wide.representative_assets["representative/frame-1.svg"]
    # Hash captured from the fixed-1280x720 renderer on 2026-09-29.
    assert hashlib.sha256(svg).hexdigest() == "1e7eb6127ffec2b3242730ad0a3940598e3a18503f5787450af78bc3e0666586"
    a4 = compile_design_source(authored_source(1188, 840))
    assert a4.representative_assets["representative/frame-1.svg"].startswith(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1188" height="840" viewBox="0 0 1188 840">'
    )


def test_registry_keeps_bases_and_variants_apart(tmp_path: Path) -> None:
    base_manifest = ingest_base(tmp_path)
    draft(tmp_path, "tall-9x16")
    draft(tmp_path, "a4-landscape")
    approve(tmp_path, "a4-landscape")
    registry = DesignPackRegistry(tmp_path)
    assert registry.get(BASE_PACK, BASE_VERSION).manifest == base_manifest
    assert registry.latest(BASE_PACK).manifest == base_manifest
    assert [package.manifest for package in registry.list()] == [base_manifest]
    assert registry.get(BASE_PACK, BASE_VERSION).canvas_id == "wide-16x9"
    assert list(registry.variants(BASE_PACK, BASE_VERSION)) == ["a4-landscape"]
    assert list(registry.variants(BASE_PACK, BASE_VERSION, include_drafts=True)) == ["a4-landscape", "tall-9x16"]
    assert [package.canvas_id for package in registry.pinnable()] == ["wide-16x9", "a4-landscape"]


def test_draft_writes_source_and_sealed_draft_then_approve_reseals(tmp_path: Path) -> None:
    ingest_base(tmp_path)
    calls: list[dict[str, Any]] = []

    async def relayout(brief: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        calls.append(brief)
        return await scaling_relayout(brief, schema)

    drafted = draft(tmp_path, "tall-9x16", relayout)
    assert len(calls) == 1
    assert calls[0]["targetCanvas"]["canvasId"] == "tall-9x16"
    assert {element["key"] for element in calls[0]["frames"][0]["elements"]} == {"s1", "c1"}
    assert drafted.manifest.schema_version == "source-native-design-pack/v2"
    assert (drafted.status, drafted.canvas_id) == ("draft", "tall-9x16")
    assert drafted.manifest.variant_of.pack_id == BASE_PACK
    assert (drafted.structure.canvas.width, drafted.structure.canvas.height) == (720, 1280)
    slot = drafted.structure.frames[0].slots[0]
    assert slot.slot_id == "slot:cover-headline"
    assert (slot.text_style.latin_font, slot.text_style.color, slot.text_style.font_size) == ("Inter", "#102A43", 28)
    assert drafted.structure.frames[0].chrome[0].paint.fill == "#FF6600"
    source = json.loads((drafted.root / VARIANT_SOURCE_FILENAME).read_text(encoding="utf-8"))
    assert source["canvas"] == {"width": 720, "height": 1280}
    assert drafted.structure.source_digest == compile_design_source(source).structure.source_digest

    approved = approve(tmp_path, "tall-9x16")
    assert approved.status == "approved"
    assert approved.manifest.structure_digest == drafted.manifest.structure_digest
    assert approved.manifest.package_digest != drafted.manifest.package_digest
    with pytest.raises(VariantDraftError, match="design_pack_variant_approved_exists"):
        draft(tmp_path, "tall-9x16")


def test_incomplete_or_out_of_canvas_relayout_writes_nothing(tmp_path: Path) -> None:
    ingest_base(tmp_path)

    async def drop_one(brief: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        payload = await scaling_relayout(brief, schema)
        payload["frames"][0]["elements"].pop()
        return payload

    async def overflow(brief: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        payload = await scaling_relayout(brief, schema)
        payload["frames"][0]["elements"][0]["width"] = 5000
        return payload

    with pytest.raises(VariantDraftError, match="design_pack_variant_relayout_incomplete"):
        draft(tmp_path, "tall-9x16", drop_one)
    with pytest.raises(VariantDraftError, match="design_pack_variant_relayout_out_of_canvas"):
        draft(tmp_path, "tall-9x16", overflow)
    with pytest.raises(VariantDraftError, match="design_pack_variant_same_canvas"):
        draft(tmp_path, "wide-16x9")
    assert not (tmp_path / BASE_PACK / BASE_VERSION / "variants" / "tall-9x16").exists()


def test_variant_in_wrong_location_is_rejected(tmp_path: Path) -> None:
    ingest_base(tmp_path)
    drafted = draft(tmp_path, "tall-9x16")
    drafted.root.rename(drafted.root.with_name("square-1x1"))
    with pytest.raises(DesignPackRegistryError, match="design_pack_variant_identity_mismatch"):
        DesignPackRegistry(tmp_path)
