from fastapi import APIRouter

from api.v1.ppt.endpoints.files import FILES_ROUTER
from api.v1.ppt.endpoints.images import IMAGES_ROUTER
from api.v1.ppt.endpoints.outlines import OUTLINES_ROUTER
from api.v1.ppt.endpoints.presentation import PRESENTATION_ROUTER


API_V1_PPT_ROUTER = APIRouter(prefix="/api/v1/ppt")

API_V1_PPT_ROUTER.include_router(FILES_ROUTER)
API_V1_PPT_ROUTER.include_router(OUTLINES_ROUTER)
API_V1_PPT_ROUTER.include_router(PRESENTATION_ROUTER)
API_V1_PPT_ROUTER.include_router(IMAGES_ROUTER)
