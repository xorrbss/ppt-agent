import json
import os
from fastembed_vectorstore import FastembedVectorstore, FastembedEmbeddingModel


class IconFinderService:
    def __init__(self):
        self.vector_store = self.get_icons_vectorstore()

    def get_icons_vectorstore(self):
        vector_store_path = "assets/icons_vectorstore.json"
        embedding_model = FastembedEmbeddingModel.BGESmallENV15

        if os.path.exists(vector_store_path):
            return FastembedVectorstore.load(embedding_model, vector_store_path)

        vector_store = FastembedVectorstore(embedding_model)
        with open("assets/icons.json", "r") as f:
            icons = json.load(f)
        documents = []
        for each in icons["icons"]:
            if each["name"].split("-")[-1] == "bold":
                documents.append(f"{each['name']}||{each['tags']}")

        vector_store.embed_documents(documents)
        vector_store.save(vector_store_path)

        return vector_store

    def search_icons(self, query: str, k: int = 1):
        result = self.vector_store.search(query, k)
        return [ f"/static/icons/bold/{result[0].split("||")[0]}.png" for result in result]
