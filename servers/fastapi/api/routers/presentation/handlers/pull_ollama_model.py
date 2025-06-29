import json
import aiohttp
from fastapi import BackgroundTasks, HTTPException
from api.models import LogMetadata
from api.routers.presentation.handlers.list_supported_ollama_models import (
    SUPPORTED_OLLAMA_MODELS,
)
from api.routers.presentation.models import OllamaModelStatusResponse
from api.services.instances import REDIS_SERVICE
from api.services.logging import LoggingService
from api.utils.model_utils import get_llm_api_key_or, get_llm_provider_url_or


class PullOllamaModelHandler:

    def __init__(self, name: str):
        self.name = name

    async def get(
        self,
        logging_service: LoggingService,
        log_metadata: LogMetadata,
        background_tasks: BackgroundTasks,
    ):
        logging_service.logger.info(
            logging_service.message(self.name),
            extra=log_metadata.model_dump(),
        )

        if self.name not in SUPPORTED_OLLAMA_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"Model {self.name} is not supported",
            )

        # Check if model is already pulled using LLM_PROVIDER_URL/api/tags
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{get_llm_provider_url_or()}/api/tags",
                    headers={"Authorization": f"Bearer {get_llm_api_key_or()}"},
                ) as response:
                    if response.status == 200:
                        pulled_models = await response.json()
                        filtered_models = [
                            model
                            for model in pulled_models["models"]
                            if model["model"] == self.name
                        ]

                        # If the model is already pulled, return the model
                        if filtered_models:
                            return OllamaModelStatusResponse(
                                name=self.name,
                                size=filtered_models[0]["size"],
                                status="pulled",
                                downloaded=filtered_models[0]["size"],
                                done=True,
                            )
        except Exception as e:
            logging_service.logger.warning(
                f"Failed to check pulled models: {e}",
                extra=log_metadata.model_dump(),
            )

        saved_model_status = REDIS_SERVICE.get(f"ollama_models/{self.name}")

        # If the model is being pulled, return the model
        if saved_model_status:
            return json.loads(saved_model_status)

        # If the model is not being pulled, pull the model
        background_tasks.add_task(self.pull_model_in_background)

        return OllamaModelStatusResponse(
            name=self.name,
            status="pulling",
            done=False,
        )

    async def pull_model_in_background(self):
        await self.pull_model()

    async def pull_model(self):
        saved_model_status = OllamaModelStatusResponse(
            name=self.name,
            status="pulling",
            done=False,
        )
        log_event_count = 0

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{get_llm_provider_url_or()}/api/pull",
                    json={"model": self.name},
                    headers={"Authorization": f"Bearer {get_llm_api_key_or()}"},
                ) as response:
                    if response.status != 200:
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Failed to pull model: {await response.text()}",
                        )

                    async for line in response.content:
                        if not line.strip():
                            continue

                        try:
                            event = json.loads(line.decode("utf-8"))
                        except json.JSONDecodeError:
                            continue

                        log_event_count += 1
                        if log_event_count != 1 and log_event_count % 20 != 0:
                            continue

                        if "completed" in event:
                            saved_model_status.downloaded = event["completed"]

                        if not saved_model_status.size and "total" in event:
                            saved_model_status.size = event["total"]

                        if "status" in event:
                            saved_model_status.status = event["status"]

                        REDIS_SERVICE.set(
                            f"ollama_models/{self.name}",
                            json.dumps(saved_model_status.model_dump(mode="json")),
                        )

        except Exception as e:
            saved_model_status.status = "error"
            saved_model_status.done = True
            REDIS_SERVICE.set(
                f"ollama_models/{self.name}",
                json.dumps(saved_model_status.model_dump(mode="json")),
            )
            raise e

        saved_model_status.done = True
        saved_model_status.status = "pulled"
        saved_model_status.downloaded = saved_model_status.size

        REDIS_SERVICE.set(
            f"ollama_models/{self.name}",
            json.dumps(saved_model_status.model_dump(mode="json")),
        )

        return saved_model_status
