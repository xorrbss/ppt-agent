import asyncio
import json
from qdrant_client import QdrantClient


class IconFinderService:
    def __init__(self):
        self.collection_name = "icons"
        self.client = QdrantClient(path="qdrant")
        print("Initializing icons collection...")
        self.client.set_model("BAAI/bge-small-en-v1.5")
        self._initialize_icons_collection()
        print("Icons collection initialized")

    def _initialize_icons_collection(self):
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self._populate_icons_collection()

    def _populate_icons_collection(self):
        with open("assets/icons.json", "r") as f:
            icons = json.load(f)

        documents = []
        metadata = []

        for each in icons["icons"]:
            if each["name"].split("-")[-1] == "bold":
                doc_text = f"{each['name']} {each['tags']}"
                documents.append(doc_text)
                metadata.append({"name": each["name"]})

        if documents:
            self.client.add(
                collection_name=self.collection_name,
                documents=documents,
                metadata=metadata,
            )

    async def search_icons(self, query: str, k: int = 1):
        result = await asyncio.to_thread(
            self.client.query,
            collection_name=self.collection_name,
            query_text=query,
            limit=k,
        )
        return [f"/static/icons/bold/{each.metadata['name']}.png" for each in result]
