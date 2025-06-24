import asyncio
from typing import List
from langchain_core.documents import Document
from langchain_text_splitters import CharacterTextSplitter
from openai.types.chat.chat_completion import ChatCompletion

from api.utils.model_utils import get_llm_client, get_nano_model

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


async def generate_document_summary(documents: List[Document]):
    client = get_llm_client()
    model = get_nano_model()

    text_splitter = CharacterTextSplitter(chunk_size=200000, chunk_overlap=0)

    coroutines = []
    for document in documents:
        text = document.page_content
        truncated_text = text_splitter.split_text(text)[0]
        coroutine = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sysmte_prompt},
                {"role": "user", "content": truncated_text},
            ],
        )
        coroutines.append(coroutine)

    completions: List[ChatCompletion] = await asyncio.gather(*coroutines)
    combined = "\n\n\n\n".join(
        [completion.choices[0].message.content for completion in completions]
    )
    return combined
