"""Design-pack manifest seal, on-disk compatibility and structural render (16:9 packs only)."""

from __future__ import annotations

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
    assert DesignPackManifest.model_validate(wire) == manifest


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


def _ingest_base(root: Path) -> DesignPackManifest:
    return ingest_design_pack(
        authored_source(), pack_id=BASE_PACK, package_version=BASE_VERSION, storage_root=root
    )


@pytest.mark.parametrize("malformed", [False, True])
def test_registry_ignores_leftover_variants_directory(tmp_path: Path, malformed: bool) -> None:
    base = _ingest_base(tmp_path)
    leftover = tmp_path / BASE_PACK / BASE_VERSION / "variants" / "a4-landscape"
    leftover.mkdir(parents=True)
    (leftover / "manifest.json").write_text(
        "{not json" if malformed else json.dumps({"schemaVersion": "source-native-design-pack/v2"}),
        encoding="utf-8",
    )
    registry = DesignPackRegistry(tmp_path)
    assert [package.manifest for package in registry.list()] == [base]
    assert registry.get(BASE_PACK, BASE_VERSION).canvas_id == "wide-16x9"
    assert not hasattr(registry, "variants") and not hasattr(registry, "pinnable")


def test_v2_manifest_is_rejected(tmp_path: Path) -> None:
    _ingest_base(tmp_path)
    manifest_path = tmp_path / BASE_PACK / BASE_VERSION / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["schemaVersion"] = "source-native-design-pack/v2"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DesignPackRegistryError, match="design_pack_manifest_invalid"):
        DesignPackRegistry(tmp_path)
