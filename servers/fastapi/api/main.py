from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from api.lifespan import app_lifespan
from api.middlewares import UserConfigEnvUpdateMiddleware
from api.v1.ppt.router import API_V1_PPT_ROUTER
from utils.asset_directory_utils import get_exports_directory, get_images_directory, get_uploads_directory
import os
from utils.get_env import get_app_data_directory_env
# Import models to ensure they are registered with SQLModel
from models.sql.presentation_layout_code import PresentationLayoutCodeModel


app = FastAPI(lifespan=app_lifespan)


# Routers
app.include_router(API_V1_PPT_ROUTER)

# Helper function to get fonts directory
def get_fonts_directory() -> str:
    """Get the fonts directory path, create if it doesn't exist"""
    app_data_dir = get_app_data_directory_env() or "/tmp/presenton"
    fonts_dir = os.path.join(app_data_dir, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    return fonts_dir

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount(
    "/app_data/images",
    StaticFiles(directory=get_images_directory()),
    name="app_data/images",
)
app.mount(
    "/app_data/exports",
    StaticFiles(directory=get_exports_directory()),
    name="app_data/exports",
)
app.mount(
    "/app_data/uploads",
    StaticFiles(directory=get_uploads_directory()),
    name="app_data/uploads",
)
app.mount(
    "/app_data/fonts",
    StaticFiles(directory=get_fonts_directory()),
    name="app_data/fonts",
)


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
