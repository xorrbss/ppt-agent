from fastapi import APIRouter

from api.v1.ppt.endpoints.slide_to_html import LAYOUT_MANAGEMENT_ROUTER
from api.v1.ppt.endpoints.presentation import PRESENTATION_ROUTER
from api.v1.ppt.endpoints.presentation_crud import PRESENTATION_CRUD_ROUTER
from api.v1.ppt.endpoints.presentation_generate import PRESENTATION_GENERATE_ROUTER
from api.v1.ppt.endpoints.presentation_versions import PRESENTATION_VERSION_ROUTER
from api.v1.ppt.endpoints.presentation_share import PRESENTATION_SHARE_ROUTER
from api.v1.ppt.endpoints.anthropic import ANTHROPIC_ROUTER
from api.v1.ppt.endpoints.codex_auth import CODEX_AUTH_ROUTER
from api.v1.ppt.endpoints.google import GOOGLE_ROUTER
from api.v1.ppt.endpoints.openai import OPENAI_ROUTER
from api.v1.ppt.endpoints.files import FILES_ROUTER
from api.v1.ppt.endpoints.pptx_slides import PPTX_SLIDES_ROUTER
from api.v1.ppt.endpoints.pdf_slides import PDF_SLIDES_ROUTER
from api.v1.ppt.endpoints.fonts import FONTS_ROUTER
from api.v1.ppt.endpoints.icons import ICONS_ROUTER
from api.v1.ppt.endpoints.images import IMAGES_ROUTER
from api.v1.ppt.endpoints.ollama import OLLAMA_ROUTER
from api.v1.ppt.endpoints.outlines import OUTLINES_ROUTER
from api.v1.ppt.endpoints.slide import SLIDE_ROUTER
from api.v1.ppt.endpoints.chat import CHAT_ROUTER
from api.v1.ppt.endpoints.pptx_slides import PPTX_FONTS_ROUTER
from api.v1.ppt.endpoints.theme import THEMES_ROUTER
from api.v1.ppt.endpoints.theme_generate import THEME_ROUTER
from api.v1.ppt.endpoints.authored import AUTHORED_ROUTER
from api.v1.ppt.endpoints.structured_templates import (
    STRUCTURED_TEMPLATES_ROUTER,
)
from api.v1.ppt.endpoints.structured_template_imports import (
    STRUCTURED_TEMPLATE_IMPORTS_ROUTER,
)
from api.v1.ppt.endpoints.template_v2_compat import (
    TEMPLATE_V2_COMPAT_ROUTER,
)
from templates.router import TEMPLATE_ROUTER


API_V1_PPT_ROUTER = APIRouter(prefix="/api/v1/ppt")

API_V1_PPT_ROUTER.include_router(FILES_ROUTER)
API_V1_PPT_ROUTER.include_router(FONTS_ROUTER)
API_V1_PPT_ROUTER.include_router(OUTLINES_ROUTER)
API_V1_PPT_ROUTER.include_router(PRESENTATION_CRUD_ROUTER)
API_V1_PPT_ROUTER.include_router(PRESENTATION_ROUTER)
API_V1_PPT_ROUTER.include_router(PRESENTATION_GENERATE_ROUTER)
API_V1_PPT_ROUTER.include_router(PRESENTATION_VERSION_ROUTER)
API_V1_PPT_ROUTER.include_router(PRESENTATION_SHARE_ROUTER)
API_V1_PPT_ROUTER.include_router(PPTX_SLIDES_ROUTER)
API_V1_PPT_ROUTER.include_router(SLIDE_ROUTER)
API_V1_PPT_ROUTER.include_router(CHAT_ROUTER)
API_V1_PPT_ROUTER.include_router(LAYOUT_MANAGEMENT_ROUTER)
API_V1_PPT_ROUTER.include_router(IMAGES_ROUTER)
API_V1_PPT_ROUTER.include_router(ICONS_ROUTER)
API_V1_PPT_ROUTER.include_router(OLLAMA_ROUTER)
API_V1_PPT_ROUTER.include_router(PDF_SLIDES_ROUTER)
API_V1_PPT_ROUTER.include_router(OPENAI_ROUTER)
API_V1_PPT_ROUTER.include_router(ANTHROPIC_ROUTER)
API_V1_PPT_ROUTER.include_router(GOOGLE_ROUTER)
API_V1_PPT_ROUTER.include_router(CODEX_AUTH_ROUTER)
API_V1_PPT_ROUTER.include_router(PPTX_FONTS_ROUTER)
API_V1_PPT_ROUTER.include_router(THEMES_ROUTER)
API_V1_PPT_ROUTER.include_router(THEME_ROUTER)
API_V1_PPT_ROUTER.include_router(AUTHORED_ROUTER)
API_V1_PPT_ROUTER.include_router(STRUCTURED_TEMPLATE_IMPORTS_ROUTER)
API_V1_PPT_ROUTER.include_router(STRUCTURED_TEMPLATES_ROUTER)
API_V1_PPT_ROUTER.include_router(TEMPLATE_V2_COMPAT_ROUTER)
API_V1_PPT_ROUTER.include_router(TEMPLATE_ROUTER)
