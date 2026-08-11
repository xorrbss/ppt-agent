from typing import Optional
from uuid import UUID

from pydantic import BaseModel, model_validator


class DecomposedFileInfo(BaseModel):
    name: str
    # New decomposition responses identify the admitted asset and never expose
    # the worker's private locator. ``file_path`` is retained only for the
    # explicitly legacy /files facade.
    asset_id: Optional[UUID] = None
    content_sha256: Optional[str] = None
    file_path: Optional[str] = None

    @model_validator(mode="after")
    def require_canonical_or_legacy_identity(self):
        if self.asset_id is None and not self.file_path:
            raise ValueError("decomposed_file_identity_required")
        return self
