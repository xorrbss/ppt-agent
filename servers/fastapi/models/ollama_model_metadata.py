from pydantic import BaseModel


class OllamaModelMetadata(BaseModel):
    label: str
    value: str
    icon: str
    size: str
