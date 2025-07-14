from fastapi import FastAPI
from api.v1.ppt.router import API_V1_PPT_ROUTER
from api.lifespan import app_lifespan


APP = FastAPI(lifespan=app_lifespan)

APP.include_router(API_V1_PPT_ROUTER)
