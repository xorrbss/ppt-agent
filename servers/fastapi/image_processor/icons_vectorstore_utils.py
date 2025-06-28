import json
import os
from fastembed import TextEmbedding

from api.utils.utils import get_resource


def get_icons_vectorstore():
    vector_store_path = get_resource("assets/icons_vectorstore.json")

    embedding_model = TextEmbedding()

    # if os.path.exists(vector_store_path):
    #     vector_store = InMemoryVectorStore.load(vector_store_path, embeddings)
    #     return vector_store

    with open(get_resource("assets/icons.json"), "r") as f:
        icons = json.load(f)

    icon_names = [icon["name"] for icon in icons["icons"]]
    bold_icon_names = []
    for each in icon_names:
        if each.split("-")[-1] == "bold":
            bold_icon_names.append(each)

    documents_and_embeddings = {
        "documents": bold_icon_names,
        "embeddings": embedding_model.embed(bold_icon_names),
    }

    with open(vector_store_path, "w") as f:
        json.dump(documents_and_embeddings, f)

    return documents_and_embeddings
