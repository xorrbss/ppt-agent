import asyncio
import json
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions


class IconFinderService:
    def __init__(self):
        self.collection_name = "icons"
        self.vector_store = self.get_icons_vectorstore()

    def get_icons_vectorstore(self):

        client = chromadb.PersistentClient(
            settings=Settings(anonymized_telemetry=False)
        )
        embedding_function = embedding_functions.ONNXMiniLM_L6_V2()

        try:
            collection = client.get_collection(
                self.collection_name, embedding_function=embedding_function
            )
        except:
            collection = client.create_collection(
                self.collection_name, embedding_function=embedding_function
            )

            with open("assets/icons.json", "r") as f:
                icons = json.load(f)

            documents = []
            ids = []

            for each in icons["icons"]:
                if each["name"].split("-")[-1] == "bold":
                    doc_text = f"{each['name']} {each['tags']}"
                    documents.append(doc_text)
                    ids.append(each["name"])

            if documents:
                collection.add(documents=documents, ids=ids)

        return collection

    async def search_icons(self, query: str, k: int = 1):
        result = await asyncio.to_thread(
            self.vector_store.query, query_texts=[query], n_results=k
        )

        icon_names = []
        if result["ids"] and result["ids"][0]:
            icon_names = result["ids"][0]

        return [f"/static/icons/bold/{icon_name}.png" for icon_name in icon_names]
