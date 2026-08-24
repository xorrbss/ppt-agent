"""Wire contracts for importing an externally sealed editable MVP Gate 1E result."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
import uuid

from pydantic import Field

from models.authored_vnext.master.base import Digest, StrictCamelModel


class EditableMvpCompletionSealImportRequestV1(StrictCamelModel):
    schema_version: Literal["authored-editable-hybrid-mvp-seal-import-request/v1"] = (
        "authored-editable-hybrid-mvp-seal-import-request/v1"
    )
    expected_deck_revision: int = Field(ge=1)
    expected_deck_digest: Digest
    expected_scene_digest: Digest
    expected_hybrid_export_attempt_id: str = Field(min_length=8, max_length=256)
    sealed_result_set_base64: str = Field(min_length=4, max_length=6_000_000)
    validation_receipt_base64: str = Field(min_length=4, max_length=100_000)
    verifier_trust_base64: str = Field(min_length=4, max_length=100_000)


class EditableMvpCompletionSealImportResponseV1(StrictCamelModel):
    schema_version: Literal["authored-editable-hybrid-mvp-seal-import-result/v1"] = (
        "authored-editable-hybrid-mvp-seal-import-result/v1"
    )
    completion_id: uuid.UUID
    deck_id: str
    deck_revision: int
    deck_digest: Digest
    scene_digest: Digest
    hybrid_export_attempt_id: str
    profile_id: Literal["authored-editable-hybrid-ko-en-basic-v1"]
    result_set_digest: Digest
    artifact_inventory_digest: Digest
    expires_at: datetime
    cas_version: Literal[1] = 1
    state: Literal["sealed"] = "sealed"
    idempotent: bool
    editable_hybrid_mvp_ready: Literal[True] = True
    native_editable_ready: Literal[True] = True
    promotion_ready: Literal[False] = False
    deployment_authorized: Literal[False] = False
