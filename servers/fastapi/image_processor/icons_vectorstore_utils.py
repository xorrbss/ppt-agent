import json
import os

from api.utils.utils import get_resource
from fastembed_vectorstore import FastembedVectorstore, FastembedEmbeddingModel


def get_icons_vectorstore():
    vector_store_path = get_resource("assets/icons_vectorstore.json")
    embedding_model = FastembedEmbeddingModel.BGESmallENV15

    if os.path.exists(vector_store_path):
        return FastembedVectorstore.load(embedding_model, vector_store_path)

    vector_store = FastembedVectorstore(embedding_model)
    with open(get_resource("assets/icons.json"), "r") as f:
        icons = json.load(f)
    documents = []
    for each in icons["icons"]:
        if each["name"].split("-")[-1] == "bold":
            documents.append(f"{each['name']}||{each['tags']}")

    vector_store.embed_documents(documents)
    vector_store.save(vector_store_path)

    return vector_store
