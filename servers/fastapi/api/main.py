from contextlib import asynccontextmanager
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel
from api.v1.ppt.router import API_V1_PPT_ROUTER
from services import SQL_ENGINE
from utils.check_llm_model_availability import check_llm_model_availability
from utils.get_env import get_can_change_keys_env
from utils.user_config import update_env_with_user_config


# Lifespan
@asynccontextmanager
async def app_lifespan(_: FastAPI):
    os.makedirs(os.getenv("APP_DATA_DIRECTORY"), exist_ok=True)
    SQLModel.metadata.create_all(SQL_ENGINE)
    await check_llm_model_availability()
    yield


# App
APP = FastAPI(lifespan=app_lifespan)


# Static files
APP.mount("/static", StaticFiles(directory="static"), name="static")
# APP.mount("/static/app-data", StaticFiles(directory=get_app_data_directory_env()))


# Routers
APP.include_router(API_V1_PPT_ROUTER)


# Middlewares
origins = ["*"]
APP.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@APP.middleware("http")
async def update_env_middleware(request: Request, call_next):
    if get_can_change_keys_env() != "false":
        update_env_with_user_config()
    return await call_next(request)
