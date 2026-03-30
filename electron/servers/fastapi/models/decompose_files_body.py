from typing import List, Optional

from pydantic import BaseModel, Field


class DecomposeFilesBody(BaseModel):
    file_paths: List[str]
    language: Optional[str] = Field(
        default=None,
        description="Presentation language from the UI; used as LiteParse/Tesseract OCR language hint.",
    )
