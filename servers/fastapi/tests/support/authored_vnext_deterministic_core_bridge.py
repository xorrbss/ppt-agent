"""Deterministic authored vNext core bridge for tests (no provider, browser or network)."""

from __future__ import annotations

from typing import Sequence

from models.authored_vnext.deck.scene import DeckSceneIR
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.generation.core_bridge import (
    CoreBridgeArtifact,
    CoreBridgeCheckpoint,
    _expected_contract_digest,
    _validate_scene,
)
from services.authored_vnext.master.digests import canonical_digest


class DeterministicCoreBridge:
    """Local fixture bridge with no provider, browser, PowerPoint, or network use."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
            raise ValueError("deterministic bridge master checkpoint mismatch")
        _validate_scene(checkpoint, scene, consumer="deterministic bridge")
        receipt = {
            "masterRevision": master_revision,
            "checkpoint": checkpoint.model_dump(mode="json", by_alias=True),
            "slideDigests": [slide.spec_digest for slide in slides],
            "sceneDigest": scene.scene_digest if scene is not None else None,
        }
        artifact_digest = canonical_digest(receipt)
        self.calls.append(receipt)
        return CoreBridgeArtifact(
            artifact_digest=artifact_digest,
            storage_reference=f"artifact://local/{artifact_digest.removeprefix('sha256:')}.pptx",
        )

