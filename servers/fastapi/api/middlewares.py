from fastapi import Request
from api.main import APP
from fastapi.middleware.cors import CORSMiddleware

from utils.get_env import get_can_change_keys_env
from utils.user_config import update_env_with_user_config


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
