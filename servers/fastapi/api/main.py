import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

from api.error_handling import install_api_error_handling
from api.lifespan import app_lifespan
from api.middlewares import RuntimeSettingsContextMiddleware, SessionAuthMiddleware
from api.v1.auth.router import API_V1_AUTH_ROUTER
from api.v1.admin.router import API_V1_ADMIN_ROUTER
from api.v1.async_tasks.router import API_V1_ASYNC_TASKS_ROUTER
from api.v1.mock.router import API_V1_MOCK_ROUTER
from api.v1.ppt.router import API_V1_PPT_ROUTER
from api.v1.webhook.router import API_V1_WEBHOOK_ROUTER
from api.v2 import API_V2_PUBLIC_ROUTER
from services.workspace_metrics import WORKSPACE_METRICS
from services.database import async_session_maker
from services.public_operation_admission import public_prometheus_metrics_text
from services.public_v2_orchestrator import (
    create_public_v2_orchestrator,
    resolve_public_v2_integration_profile,
)
from services.provider_telemetry_runtime import maybe_init_sentry
from utils.get_env import (
    get_app_data_directory_env,
    get_enable_mock_endpoints_env,
)
from utils.path_helpers import get_resource_path
from utils.slide_capture import get_render_runtime_snapshot


def _maybe_init_sentry() -> None:
    maybe_init_sentry()


_maybe_init_sentry()

app = FastAPI(lifespan=app_lifespan)
install_api_error_handling(app)
_public_v2_integration_profile = resolve_public_v2_integration_profile(os.environ)
if _public_v2_integration_profile is not None:
    app.state.public_v2_orchestrator = create_public_v2_orchestrator(
        async_session_maker,
        integration_profile=_public_v2_integration_profile,
    )

# Routers
app.include_router(API_V1_PPT_ROUTER)
app.include_router(API_V1_ASYNC_TASKS_ROUTER)
app.include_router(API_V1_WEBHOOK_ROUTER)
# Mock endpoints are a dev-only surface; keep them off production deployments.
if (get_enable_mock_endpoints_env() or "").lower() == "true":
    app.include_router(API_V1_MOCK_ROUTER)
app.include_router(API_V1_AUTH_ROUTER)
app.include_router(API_V1_ADMIN_ROUTER)
if _public_v2_integration_profile is not None:
    app.include_router(API_V2_PUBLIC_ROUTER)

# Mount app_data and static assets (direct FastAPI access; nginx also serves /static in Docker).
app_data_dir = get_app_data_directory_env()
if app_data_dir:
    os.makedirs(app_data_dir, exist_ok=True)
    app.mount("/app_data", StaticFiles(directory=app_data_dir), name="app_data")

static_dir = get_resource_path("static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Middlewares
# CORS was allow_origins=["*"] WITH allow_credentials=True — Starlette then reflects
# any caller's Origin and returns Access-Control-Allow-Credentials:true, so any site
# could make credentialed calls. The UI is same-origin (nginx / the local proxy), so
# CORS is only needed for local dev cross-origin; use a tight allowlist (extend via
# CORS_ALLOWED_ORIGINS, comma-separated) instead of a credentialed wildcard.
def _cors_origins() -> list[str]:
    raw = (os.getenv("CORS_ALLOWED_ORIGINS") or "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Chat-First-Workspace-Projected"],
)

app.add_middleware(RuntimeSettingsContextMiddleware)
app.add_middleware(SessionAuthMiddleware)


@app.get(
    "/metrics/workspace",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def workspace_metrics_scrape() -> PlainTextResponse:
    """Expose only the closed, metadata-free workspace Prometheus schema.

    The path intentionally lives outside ``/api`` so Prometheus does not need
    an interactive user session.  Deployment network policy remains the
    authorization boundary for this aggregate-only scrape endpoint.
    """

    async with async_session_maker() as session:
        public_metrics = await public_prometheus_metrics_text(session)
    return PlainTextResponse(
        WORKSPACE_METRICS.prometheus_text() + public_metrics,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get(
    "/metrics/authored-render-runtime",
    response_class=JSONResponse,
    include_in_schema=False,
)
async def authored_render_runtime_metrics() -> JSONResponse:
    """Expose metadata-only Authored renderer cache/pool telemetry."""

    return JSONResponse(
        get_render_runtime_snapshot(),
        headers={"Cache-Control": "no-store"},
    )


@app.middleware("http")
async def static_icon_fallback_middleware(request: Request, call_next):
    """Serve placeholder when icon paths are missing (e.g. renamed Phosphor icons)."""
    response = await call_next(request)
    if response.status_code != 404:
        return response
    path = request.url.path
    if not path.startswith("/static/icons/"):
        return response
    placeholder = get_resource_path("static/icons/placeholder.svg")
    if not os.path.isfile(placeholder):
        return response
    return FileResponse(placeholder, media_type="image/svg+xml")
