from typing import Optional

from pydantic import BaseModel
import uuid


class PresentationAndPath(BaseModel):
    presentation_id: uuid.UUID
    path: str


class PresentationPathAndEditPath(PresentationAndPath):
    edit_path: str
    #: Durable export operation chained onto this generation because the
    #: request carried ``export_as``. Clients follow it to the export artifact;
    #: see ``services/presentation_generation_export_chain.py``.
    export_operation_id: Optional[uuid.UUID] = None
    #: Stable code set instead of an operation id when the chained export could
    #: not be submitted. The presentation itself is published regardless.
    export_error: Optional[str] = None
