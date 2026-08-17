"""Only supported export boundary for authored vNext generation."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from pydantic import Field

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.deck.composition import AuthoredCompositionPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import ExportPlan, ExportSlidePlan
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.master.compiler_port import OoxmlMasterCompilerPort
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


class CoreBridgeArtifact(StrictCamelModel):
    artifact_digest: Digest
    storage_reference: str = Field(min_length=1)
    compiler_port: str = "AuthoringCoreBridgePort"


def _expected_contract_digest(checkpoint: CoreBridgeCheckpoint) -> str:
    if checkpoint.composition is not None:
        return checkpoint.composition.effective_contract_digest
    return checkpoint.master_digest


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
    ) -> CoreBridgeArtifact: ...


class OoxmlCoreBridgeAdapter:
    """Production adapter from the core bridge to the narrow OOXML port."""

    def __init__(self, compiler: OoxmlMasterCompilerPort) -> None:
        self._compiler = compiler

    def export(
        self,
        *,
        master: DeckMasterContract,
        master_revision: int,
        slides: Sequence[SlideSpecV2],
        checkpoint: CoreBridgeCheckpoint,
    ) -> CoreBridgeArtifact:
        if master.digest != _expected_contract_digest(checkpoint):
            raise ValueError("core bridge master checkpoint mismatch")
        if checkpoint.composition is None:
            if master.font_manifest.digest != checkpoint.font_manifest_digest:
                raise ValueError("core bridge font checkpoint mismatch")
            layout_digest = canonical_digest(
                master.layouts.model_dump(mode="json", by_alias=True)
            )
            if layout_digest != checkpoint.layout_registry_digest:
                raise ValueError("core bridge layout checkpoint mismatch")
        canonical = canonical_digest(
            {
                "checkpoint": checkpoint.model_dump(mode="json", by_alias=True),
                "slides": [
                    slide.model_dump(mode="json", by_alias=True) for slide in slides
                ],
            }
        )
        plan = ExportPlan(
            compiler_port="OoxmlMasterCompilerPort",
            compiler_version="ooxml-master-port/v1",
            master_id=master.master_id,
            master_revision=master_revision,
            master_digest=master.digest,
            canonical_digest=canonical,
            source_digest=checkpoint.source_digest,
            slides=[
                ExportSlidePlan(
                    slide_id=slide.slide_id,
                    layout_id=slide.layout_id,
                    spec_digest=slide.spec_digest,
                )
                for slide in slides
            ],
        )
        artifact = self._compiler.compile(
            master=master,
            slides=tuple(slides),
            export_plan=plan,
        )
        return CoreBridgeArtifact(
            artifact_digest=artifact.artifact_digest,
            storage_reference=artifact.storage_reference,
        )


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
    ) -> CoreBridgeArtifact:
        if master.digest != _expected_contract_digest(checkpoint):
            raise ValueError("deterministic bridge master checkpoint mismatch")
        receipt = {
            "masterRevision": master_revision,
            "checkpoint": checkpoint.model_dump(mode="json", by_alias=True),
            "slideDigests": [slide.spec_digest for slide in slides],
        }
        artifact_digest = canonical_digest(receipt)
        self.calls.append(receipt)
        return CoreBridgeArtifact(
            artifact_digest=artifact_digest,
            storage_reference=f"artifact://local/{artifact_digest.removeprefix('sha256:')}.pptx",
        )


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
    ) -> CoreBridgeArtifact:
        if master.digest != _expected_contract_digest(checkpoint):
            raise ValueError("export plan master checkpoint mismatch")
        receipt = {
            "schemaVersion": "authored-vnext-export-plan-receipt/v1",
            "masterRevision": master_revision,
            "checkpoint": checkpoint.model_dump(mode="json", by_alias=True),
            "slideDigests": [slide.spec_digest for slide in slides],
        }
        digest = canonical_digest(receipt)
        return CoreBridgeArtifact(
            artifact_digest=digest,
            storage_reference=f"export-job://required/{digest.removeprefix('sha256:')}",
            compiler_port="AuthoredVNextExportJob",
        )
