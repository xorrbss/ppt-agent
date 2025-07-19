from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from sqlmodel import SQLModel

from services import SQL_ENGINE
from utils.model_availability import check_llm_and_image_provider_api_or_model_availability


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    """
    Lifespan context manager for FastAPI application.
    Initializes the application data directory and checks LLM model availability.
    
    """
    os.makedirs(os.getenv("APP_DATA_DIRECTORY"), exist_ok=True)
    SQLModel.metadata.create_all(SQL_ENGINE)
    await check_llm_and_image_provider_api_or_model_availability()
    yield
