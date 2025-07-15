from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from api.lifespan import app_lifespan
from api.middlewares import UserConfigEnvUpdateMiddleware
from api.v1.ppt.router import API_V1_PPT_ROUTER


app = FastAPI(lifespan=app_lifespan)


# Routers
app.include_router(API_V1_PPT_ROUTER)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")
# APP.mount("/static/app-data", StaticFiles(directory=get_app_data_directory_env()))


# Middlewares
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(UserConfigEnvUpdateMiddleware)
