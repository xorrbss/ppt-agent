from api.feature_module_registry import FeatureModule, compose_static_feature_modules

from api.v1.ppt.endpoints.slide_to_html import LAYOUT_MANAGEMENT_ROUTER
from api.v1.ppt.endpoints.presentation import PRESENTATION_ROUTER
from api.v1.ppt.endpoints.presentation_crud import PRESENTATION_CRUD_ROUTER
from api.v1.ppt.endpoints.presentation_generate import PRESENTATION_GENERATE_ROUTER
from api.v1.ppt.endpoints.presentation_blank import PRESENTATION_BLANK_ROUTER
from api.v1.ppt.endpoints.presentation_versions import PRESENTATION_VERSION_ROUTER
from api.v1.ppt.endpoints.presentation_share import PRESENTATION_SHARE_ROUTER
from api.v1.ppt.endpoints.anthropic import ANTHROPIC_ROUTER
from api.v1.ppt.endpoints.codex_auth import CODEX_AUTH_ROUTER
from api.v1.ppt.endpoints.community import COMMUNITY_ROUTER
from api.v1.ppt.endpoints.google import GOOGLE_ROUTER
from api.v1.ppt.endpoints.openai import OPENAI_ROUTER
from api.v1.ppt.endpoints.files import FILES_ROUTER
from api.v1.ppt.endpoints.asset_gateway import ASSET_GATEWAY_ROUTER
from api.v1.ppt.endpoints.file_decompose_jobs import FILE_DECOMPOSE_JOBS_ROUTER
from api.v1.ppt.endpoints.pptx_slides import PPTX_SLIDES_ROUTER
from api.v1.ppt.endpoints.pdf_slides import PDF_SLIDES_ROUTER
from api.v1.ppt.endpoints.fonts import FONTS_ROUTER
from api.v1.ppt.endpoints.icons import ICONS_ROUTER
from api.v1.ppt.endpoints.images import IMAGES_ROUTER
from api.v1.ppt.endpoints.ollama import OLLAMA_ROUTER
from api.v1.ppt.endpoints.outlines import OUTLINES_ROUTER
from api.v1.ppt.endpoints.slide import SLIDE_ROUTER
from api.v1.ppt.endpoints.pptx_slides import PPTX_FONTS_ROUTER
from api.v1.ppt.endpoints.theme import THEMES_ROUTER
from api.v1.ppt.endpoints.theme_generate import THEME_ROUTER
from api.v1.ppt.endpoints.authored import AUTHORED_ROUTER
from api.v1.ppt.endpoints.workspace import WORKSPACE_ROUTER
from api.v1.ppt.endpoints.conversations import CONVERSATION_ROUTER
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


# This tuple is intentionally static.  Feature discovery through settings or
# module-path imports would make the deployed API depend on unreviewed input.
PPT_FEATURE_MODULES = (
    FeatureModule("files", FILES_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("asset-gateway", ASSET_GATEWAY_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("file-decompose-jobs", FILE_DECOMPOSE_JOBS_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("fonts", FONTS_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("outlines", OUTLINES_ROUTER, frozenset({"ppt-api"})),
    FeatureModule("presentation-crud", PRESENTATION_CRUD_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation", PRESENTATION_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation-generate", PRESENTATION_GENERATE_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation-blank", PRESENTATION_BLANK_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation-versions", PRESENTATION_VERSION_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation-share", PRESENTATION_SHARE_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("pptx-slides", PPTX_SLIDES_ROUTER, frozenset({"ppt-api", "export"})),
    FeatureModule("slide", SLIDE_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("layout-management", LAYOUT_MANAGEMENT_ROUTER, frozenset({"ppt-api", "renderer"})),
    FeatureModule("images", IMAGES_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("icons", ICONS_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("ollama", OLLAMA_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("pdf-slides", PDF_SLIDES_ROUTER, frozenset({"ppt-api", "export"})),
    FeatureModule("openai", OPENAI_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("anthropic", ANTHROPIC_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("google", GOOGLE_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("codex-auth", CODEX_AUTH_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("pptx-fonts", PPTX_FONTS_ROUTER, frozenset({"ppt-api", "assets", "export"})),
    FeatureModule("themes", THEMES_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("theme-generate", THEME_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("authored", AUTHORED_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("workspace", WORKSPACE_ROUTER, frozenset({"ppt-api", "workspace"})),
    FeatureModule("conversations", CONVERSATION_ROUTER, frozenset({"ppt-api", "workspace"})),
    FeatureModule(
        "structured-template-imports",
        STRUCTURED_TEMPLATE_IMPORTS_ROUTER,
        frozenset({"ppt-api", "templates"}),
    ),
    FeatureModule("structured-templates", STRUCTURED_TEMPLATES_ROUTER, frozenset({"ppt-api", "templates"})),
    FeatureModule(
        "template-v2-compat", TEMPLATE_V2_COMPAT_ROUTER, frozenset({"ppt-api", "templates"})
    ),
    FeatureModule("templates", TEMPLATE_ROUTER, frozenset({"ppt-api", "templates"})),
    FeatureModule("community", COMMUNITY_ROUTER, frozenset({"ppt-api", "community"})),
)

API_V1_PPT_ROUTER = compose_static_feature_modules(
    PPT_FEATURE_MODULES,
    available_capabilities={
        "ppt-api",
        "assets",
        "community",
        "export",
        "presentation",
        "providers",
        "renderer",
        "templates",
        "workspace",
    },
    prefix="/api/v1/ppt",
)
