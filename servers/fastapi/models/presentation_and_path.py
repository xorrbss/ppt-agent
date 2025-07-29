from pydantic import BaseModel


class PresentationAndPath(BaseModel):
    presentation_id: str
    path: str


class PresentationPathAndEditPath(PresentationAndPath):
    edit_path: str
