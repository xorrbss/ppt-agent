from api.feature_module_registry import FeatureModule, compose_static_feature_modules

from api.v1.ppt.endpoints.presentation_crud import PRESENTATION_CRUD_ROUTER
from api.v1.ppt.endpoints.presentation_generate import PRESENTATION_GENERATE_ROUTER
from api.v1.ppt.endpoints.authored_operations import AUTHORED_OPERATIONS_ROUTER
from api.v1.ppt.endpoints.presentation_blank import PRESENTATION_BLANK_ROUTER
from api.v1.ppt.endpoints.presentation_versions import PRESENTATION_VERSION_ROUTER
from api.v1.ppt.endpoints.presentation_share import PRESENTATION_SHARE_ROUTER
from api.v1.ppt.endpoints.authored_editor_events import (
    AUTHORED_EDITOR_EVENTS_ROUTER,
)
from api.v1.ppt.endpoints.authored_batch_remake import (
    AUTHORED_BATCH_REMAKE_ROUTER,
)
from api.v1.ppt.endpoints.anthropic import ANTHROPIC_ROUTER
from api.v1.ppt.endpoints.codex_auth import CODEX_AUTH_ROUTER
from api.v1.ppt.endpoints.google import GOOGLE_ROUTER
from api.v1.ppt.endpoints.openai import OPENAI_ROUTER
from api.v1.ppt.endpoints.files import FILES_ROUTER
from api.v1.ppt.endpoints.asset_gateway import ASSET_GATEWAY_ROUTER
from api.v1.ppt.endpoints.file_decompose_jobs import FILE_DECOMPOSE_JOBS_ROUTER
from api.v1.ppt.endpoints.fonts import FONTS_ROUTER
from api.v1.ppt.endpoints.icons import ICONS_ROUTER
from api.v1.ppt.endpoints.images import IMAGES_ROUTER
from api.v1.ppt.endpoints.ollama import OLLAMA_ROUTER
from api.v1.ppt.endpoints.outlines import OUTLINES_ROUTER
from api.v1.ppt.endpoints.slide import SLIDE_ROUTER
from api.v1.ppt.endpoints.theme import THEMES_ROUTER
from api.v1.ppt.endpoints.theme_generate import THEME_ROUTER
from api.v1.ppt.endpoints.authored import AUTHORED_ROUTER
from api.v1.ppt.endpoints.authored_vnext.master import (
    AUTHORED_VNEXT_MASTER_ROUTER,
)
from api.v1.ppt.endpoints.authored_vnext.export import (
    AUTHORED_VNEXT_EXPORT_ROUTER,
)
from api.v1.ppt.endpoints.authored_vnext.generation import (
    AUTHORED_VNEXT_GENERATION_ROUTER,
)
from api.v1.ppt.endpoints.authored_vnext.package import (
    AUTHORED_VNEXT_PACKAGE_ROUTER,
)
from api.v1.ppt.endpoints.authored_vnext.semantic import (
    AUTHORED_SEMANTIC_ROUTER,
)
from api.v1.ppt.endpoints.workspace import WORKSPACE_ROUTER
from api.v1.ppt.endpoints.conversations import CONVERSATION_ROUTER
from api.v1.ppt.endpoints.compatibility_migration import (
    COMPATIBILITY_MIGRATION_ROUTER,
)
from api.v1.ppt.endpoints.source_locked import SOURCE_LOCKED_ROUTER
from api.v1.ppt.endpoints.source_content import SOURCE_CONTENT_ROUTER
from api.v1.ppt.endpoints.raster_reconstruction import RASTER_RECONSTRUCTION_ROUTER
from api.v1.ppt.endpoints.presentation_slide_order import PRESENTATION_SLIDE_ORDER_ROUTER
from api.v1.ppt.endpoints.design_packs import DESIGN_PACKS_ROUTER
from api.v1.ppt.endpoints.svg_templates import SVG_TEMPLATES_ROUTER


# This tuple is intentionally static.  Feature discovery through settings or
# module-path imports would make the deployed API depend on unreviewed input.
PPT_FEATURE_MODULES = (
    FeatureModule("svg-templates", SVG_TEMPLATES_ROUTER, frozenset({"ppt-api", "assets", "templates"})),
    FeatureModule("files", FILES_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("asset-gateway", ASSET_GATEWAY_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("file-decompose-jobs", FILE_DECOMPOSE_JOBS_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("fonts", FONTS_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("outlines", OUTLINES_ROUTER, frozenset({"ppt-api"})),
    FeatureModule("presentation-crud", PRESENTATION_CRUD_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation-generate", PRESENTATION_GENERATE_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("authored-operations", AUTHORED_OPERATIONS_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation-blank", PRESENTATION_BLANK_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("presentation-versions", PRESENTATION_VERSION_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule(
        "authored-batch-remake",
        AUTHORED_BATCH_REMAKE_ROUTER,
        frozenset({"ppt-api", "presentation"}),
    ),
    FeatureModule(
        "authored-editor-events",
        AUTHORED_EDITOR_EVENTS_ROUTER,
        frozenset({"ppt-api", "presentation"}),
    ),
    FeatureModule("presentation-share", PRESENTATION_SHARE_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("slide", SLIDE_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("images", IMAGES_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("icons", ICONS_ROUTER, frozenset({"ppt-api", "assets"})),
    FeatureModule("ollama", OLLAMA_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("openai", OPENAI_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("anthropic", ANTHROPIC_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("google", GOOGLE_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("codex-auth", CODEX_AUTH_ROUTER, frozenset({"ppt-api", "providers"})),
    FeatureModule("themes", THEMES_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("theme-generate", THEME_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule("authored", AUTHORED_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule(
        "authored-vnext-master",
        AUTHORED_VNEXT_MASTER_ROUTER,
        frozenset({"ppt-api", "presentation"}),
    ),
    FeatureModule(
        "authored-vnext-export",
        AUTHORED_VNEXT_EXPORT_ROUTER,
        frozenset({"ppt-api", "presentation", "export"}),
    ),
    FeatureModule(
        "authored-vnext-generation",
        AUTHORED_VNEXT_GENERATION_ROUTER,
        frozenset({"ppt-api", "presentation", "export"}),
    ),
    FeatureModule(
        "authored-vnext-package",
        AUTHORED_VNEXT_PACKAGE_ROUTER,
        frozenset({"ppt-api", "presentation", "export"}),
    ),
    FeatureModule(
        "authored-semantic-emission",
        AUTHORED_SEMANTIC_ROUTER,
        frozenset({"ppt-api", "presentation", "export"}),
    ),
    FeatureModule("workspace", WORKSPACE_ROUTER, frozenset({"ppt-api", "workspace"})),
    FeatureModule("conversations", CONVERSATION_ROUTER, frozenset({"ppt-api", "workspace"})),
    FeatureModule(
        "compatibility-migration",
        COMPATIBILITY_MIGRATION_ROUTER,
        frozenset({"ppt-api", "presentation"}),
    ),
    FeatureModule("source-locked", SOURCE_LOCKED_ROUTER, frozenset({"ppt-api", "presentation"})),
    FeatureModule(
        "source-content",
        SOURCE_CONTENT_ROUTER,
        frozenset({"ppt-api", "assets", "presentation"}),
    ),
    FeatureModule(
        "raster-reconstruction",
        RASTER_RECONSTRUCTION_ROUTER,
        frozenset({"ppt-api", "assets", "presentation"}),
    ),
    FeatureModule(
        "presentation-slide-order",
        PRESENTATION_SLIDE_ORDER_ROUTER,
        frozenset({"ppt-api", "presentation"}),
    ),
    FeatureModule(
        "design-packs",
        DESIGN_PACKS_ROUTER,
        frozenset({"ppt-api", "assets", "presentation", "templates"}),
    ),
)

API_V1_PPT_ROUTER = compose_static_feature_modules(
    PPT_FEATURE_MODULES,
    available_capabilities={
        "ppt-api",
        "assets",
        "export",
        "presentation",
        "providers",
        "renderer",
        "templates",
        "workspace",
    },
    prefix="/api/v1/ppt",
)
