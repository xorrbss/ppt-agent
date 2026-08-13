"""OOXML compiler boundary; package serialization belongs to task 04."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import ExportPlan
from models.authored_vnext.master.slide import SlideSpecV2


class OoxmlCompileArtifact(StrictCamelModel):
    artifact_digest: Digest
    media_type: str = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    storage_reference: str = Field(min_length=1)


@runtime_checkable
class OoxmlMasterCompilerPort(Protocol):
    """Compile canonical master/slide data without accepting authored HTML."""

    def compile(
        self,
        *,
        master: DeckMasterContract,
        slides: tuple[SlideSpecV2, ...],
        export_plan: ExportPlan,
    ) -> OoxmlCompileArtifact: ...
