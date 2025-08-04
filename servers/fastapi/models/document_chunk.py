from pydantic import BaseModel


class DocumentChunk(BaseModel):
    heading: str
    content: str
    heading_index: int
    score: float

    def to_slide_outline(self) -> str:
        return f"{self.heading}\n{self.content}"
