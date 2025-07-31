import asyncio
from typing import List

from models.llm_message import LLMMessage
from services.llm_client import LLMClient
from utils.llm_provider import get_model


sysmte_prompt = """
Generate a blog-style summary of the provided document in **more than 2000 words**.
Maintain as much information as possible.

### Output Format

- Provide the summary in a **blog format** with an **engaging introduction** and a **clear structure**.
- Ensure the **logical flow** of the document is preserved.

### Notes

- **Retain the main ideas and essential details** from the document.
- **Show line-breaks** clearly.
- If **slides structure is mentioned** in document, structure the summary in the same way.
"""


async def generate_document_summary(documents: List[str]):
    client = LLMClient()
    model = get_model()

    coroutines = []
    for document in documents:
        truncated_text = document[:200000]
        coroutine = client.generate(
            model=model,
            messages=[
                LLMMessage(role="system", content=sysmte_prompt),
                LLMMessage(role="user", content=truncated_text),
            ],
        )
        coroutines.append(coroutine)

    completions: List[str] = await asyncio.gather(*coroutines)
    combined = "\n\n\n\n".join(completions)
    return combined
