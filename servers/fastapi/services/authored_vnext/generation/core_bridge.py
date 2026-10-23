"""Only supported export boundary for authored vNext generation."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from pydantic import Field

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.deck.composition import AuthoredCompositionPin
from models.authored_vnext.deck.scene import DeckSceneIR
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.master.digests import canonical_digest


class CoreBridgeCheckpoint(StrictCamelModel):
    """Digests that must remain fixed across render, export, retry, and resume."""

    master_digest: Digest
    layout_registry_digest: Digest
    font_manifest_digest: Digest
    source_digest: Digest
    composition: AuthoredCompositionPin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    scene_digest: Digest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class CoreBridgeArtifact(StrictCamelModel):
    artifact_digest: Digest
    storage_reference: str = Field(min_length=1)
    compiler_port: str = "AuthoringCoreBridgePort"


def _expected_contract_digest(checkpoint: CoreBridgeCheckpoint) -> str:
    if checkpoint.composition is not None:
        return checkpoint.composition.effective_contract_digest
    return checkpoint.master_digest


def _validate_scene(
    checkpoint: CoreBridgeCheckpoint,
    scene: DeckSceneIR | None,
    *,
    consumer: str,
) -> None:
    if checkpoint.scene_digest is None:
        if scene is not None:
            raise ValueError(f"{consumer} received an unpinned scene")
        return
    if scene is None or scene.scene_digest != checkpoint.scene_digest:
        raise ValueError(f"{consumer} scene checkpoint mismatch")


@runtime_checkable
class AuthoringCoreBridgePort(Protocol):
    """Core-owned export port; generation never imports an OOXML implementation."""

    def export(
        self,
        *,
        master: DeckMasterContract,
        master_revision: int,
        slides: Sequence[SlideSpecV2],
        checkpoint: CoreBridgeCheckpoint,
        scene: DeckSceneIR | None = None,
    ) -> CoreBridgeArtifact: ...


class DurableExportPlanBridge:
    """Seal the export input while leaving binary production to the durable export job.

    This adapter deliberately does not claim that a PPTX exists. The public flow
    must create and run an authored-vNext export job before showing download
    success; ``storage_reference`` is only a typed hand-off marker.
    """

    def export(
        self,
        *,
        master: DeckMasterContract,
        master_revision: int,
        slides: Sequence[SlideSpecV2],
        checkpoint: CoreBridgeCheckpoint,
        scene: DeckSceneIR | None = None,
    ) -> CoreBridgeArtifact:
        if master.digest != _expected_contract_digest(checkpoint):
            raise ValueError("export plan master checkpoint mismatch")
        _validate_scene(checkpoint, scene, consumer="export plan")
        if scene is not None:
            scene.require_capability(consumer="nativePptxExport")
        receipt = {
            "schemaVersion": "authored-vnext-export-plan-receipt/v1",
            "masterRevision": master_revision,
            "checkpoint": checkpoint.model_dump(mode="json", by_alias=True),
            "slideDigests": [slide.spec_digest for slide in slides],
            "sceneDigest": scene.scene_digest if scene is not None else None,
        }
        digest = canonical_digest(receipt)
        return CoreBridgeArtifact(
            artifact_digest=digest,
            storage_reference=f"export-job://required/{digest.removeprefix('sha256:')}",
            compiler_port="AuthoredVNextExportJob",
        )
