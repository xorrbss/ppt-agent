from typing import Optional
from pydantic import BaseModel


class OllamaModelStatusResponse(BaseModel):
    name: str
    size: Optional[int] = None
    downloaded: Optional[int] = None
    status: str
    done: bool
