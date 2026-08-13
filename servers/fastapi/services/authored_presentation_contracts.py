from dataclasses import dataclass
from typing import List

from utils.llm_calls.author_deck import AuthoredDeckResult


AUTHORED_TEMPLATE = "authored"


@dataclass(frozen=True)
class AuthoredGenerationPipelineResult:
    deck: AuthoredDeckResult
    pngs: List[bytes]
    elapsed: float
    author_work: float
    illustration_work: float
    render_work: float

