from typing import List, Optional

from api.utils.utils import get_resource
from ppt_generator.models.query_and_prompt_models import (
    IconCategoryEnum,
    IconQueryCollectionWithData,
)
from fastembed_vectorstore import FastembedVectorstore


async def get_icon(
    vector_store: FastembedVectorstore,
    input: IconQueryCollectionWithData,
) -> str:
    try:
        query = input.icon_query
        results = vector_store.search(query, 1)
        icon_name = results[0][0].split("||")[0]
        return get_resource(f"assets/icons/bold/{icon_name}.png")
    except Exception as e:
        print("Error finding icon: ", e)
        return get_resource("assets/icons/placeholder.png")


async def get_icons(
    vector_store: FastembedVectorstore,
    query: str,
    page: int,
    limit: int,
    category: Optional[IconCategoryEnum],
    temp_dir: str,
) -> List[str]:

    results = vector_store.search(query, limit)
    icon_names = [result[0].split("||")[0] for result in results]

    return [get_resource(f"assets/icons/bold/{each}.png") for each in icon_names]
