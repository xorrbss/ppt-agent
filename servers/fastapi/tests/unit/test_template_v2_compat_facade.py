import asyncio
from collections import Counter
import re
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.v1.ppt.endpoints.structured_templates import (
    STRUCTURED_TEMPLATES_ROUTER,
)
from api.v1.ppt.endpoints import template_v2_compat
from api.v1.ppt.endpoints.template_v2_compat import (
    TEMPLATE_V2_COMPAT_ROUTER,
    UpdateTemplateMetadataRequest,
)
from api.v1.ppt.router import API_V1_PPT_ROUTER
from models.sql.presentation import PresentationModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from services.database import get_async_session
from services.template_v2_service import (
    TemplateV2Record,
    TemplateV2RevisionConflictError,
)
from templates.v2.constants import TEMPLATE_V2_VERSION


SOURCE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _layouts(title: str = "Original") -> dict:
    return {
        "layouts": [
            {
                "id": "title-slide",
                "description": "Native editable title slide",
                "components": [
                    {
                        "id": "hero",
                        "description": "Editable hero title component",
                        "position": {"x": 0, "y": 0},
                        "elements": [
                            {
                                "type": "text",
                                "position": {"x": 1, "y": 1},
                                "size": {"width": 8, "height": 1},
                                "runs": [{"text": title}],
                                "decorative": False,
                                "name": "title",
                                "min_length": 1,
                                "max_length": 80,
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _normalized_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{param}", path)


def test_template_routes_have_no_method_path_collision():
    routes = [
        route
        for route in API_V1_PPT_ROUTER.routes
        if route.path.startswith("/api/v1/ppt/template/")
    ]
    pairs = [
        (method, _normalized_path(route.path))
        for route in routes
        for method in route.methods
    ]
    duplicates = {
        pair: count
        for pair, count in Counter(pairs).items()
        if count > 1
    }

    assert duplicates == {}

    core = {
        (method, _normalized_path(route.path)): route.endpoint.__module__
        for route in routes
        for method in route.methods
        if _normalized_path(route.path)
        in {
            "/api/v1/ppt/template/all",
            "/api/v1/ppt/template/{param}",
        }
    }
    assert core == {
        ("GET", "/api/v1/ppt/template/all"): "templates.handler",
        ("GET", "/api/v1/ppt/template/{param}"): "templates.handler",
        (
            "PATCH",
            "/api/v1/ppt/template/{param}",
        ): "api.v1.ppt.endpoints.template_v2_compat",
        (
            "DELETE",
            "/api/v1/ppt/template/{param}",
        ): "api.v1.ppt.endpoints.template_v2_compat",
    }


def test_compat_patch_uses_fetched_revision_as_cas(monkeypatch):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv(
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST",
        "compat-template",
    )
    template = TemplateV2(
        id="compat-template",
        presentation_id=SOURCE_ID,
        name="Original",
        revision=1,
        assets={"thumbnail": "https://example.test/original.png"},
    )
    record = TemplateV2Record(
        template=template,
        presentation_id=SOURCE_ID,
        revision=7,
    )
    observed: dict[str, object] = {}

    class RecordingService:
        def __init__(self, _session):
            pass

        async def get(self, template_id):
            assert template_id == "compat-template"
            return record

        async def update(self, template_id, *, changes, expected_revision=None):
            observed.update(
                template_id=template_id,
                changes=changes,
                expected_revision=expected_revision,
            )
            return record

    monkeypatch.setattr(
        template_v2_compat,
        "TemplateV2Service",
        RecordingService,
    )
    asyncio.run(
        template_v2_compat.update_template_metadata(
            template_id="compat-template",
            request=UpdateTemplateMetadataRequest(
                thumbnail="https://example.test/updated.png"
            ),
            sql_session=object(),
        )
    )

    assert observed["expected_revision"] == 7
    assert observed["changes"] == {
        "assets": {"thumbnail": "https://example.test/updated.png"}
    }
    conflict = template_v2_compat._service_http_error(
        TemplateV2RevisionConflictError(
            expected_revision=7,
            current_revision=8,
        )
    )
    assert conflict.status_code == 409


def test_structured_and_compat_routes_share_sidecar_facade(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv(
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST",
        "compat-template,missing-template",
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'compat-facade.db'}",
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    source = PresentationModel(
        id=SOURCE_ID,
        content="Template V2 source",
        n_slides=1,
        language="en",
        version=TEMPLATE_V2_VERSION,
        mode="template",
        layout={"name": "native"},
    )

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: (
                    PresentationModel.__table__.create(
                        sync_connection,
                        checkfirst=True,
                    ),
                    TemplateV2.__table__.create(
                        sync_connection,
                        checkfirst=True,
                    ),
                    TemplateV2LocalState.__table__.create(
                        sync_connection,
                        checkfirst=True,
                    ),
                )
            )
        async with session_factory() as session:
            session.add(source)
            await session.commit()

    async def session_dependency():
        async with session_factory() as session:
            yield session

    asyncio.run(setup())
    app = FastAPI()
    app.include_router(STRUCTURED_TEMPLATES_ROUTER, prefix="/api/v1/ppt")
    app.include_router(TEMPLATE_V2_COMPAT_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_async_session] = session_dependency

    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/ppt/structured-templates",
                json={
                    "presentation_id": str(SOURCE_ID),
                    "id": "compat-template",
                    "name": "Created through structured API",
                    "layouts": _layouts(),
                },
            )
            assert created.status_code == 201
            assert created.json()["revision"] == 1

            patched = client.patch(
                "/api/v1/ppt/template/compat-template",
                json={
                    "id": "compat-template",
                    "name": "Patched through upstream contract",
                    "description": "Shared persistence",
                    "thumbnail": "https://example.test/thumbnail.png",
                    "fonts": {
                        " Inter ": " https://example.test/inter.woff2 "
                    },
                    "layouts": _layouts("Patched"),
                },
            )
            assert patched.status_code == 200
            assert set(patched.json()) == {
                "id",
                "name",
                "description",
                "layout_count",
                "thumbnail",
                "is_default",
                "created_at",
                "updated_at",
                "merged_components",
                "layouts",
                "fonts",
            }
            assert patched.json()["name"] == "Patched through upstream contract"
            assert patched.json()["layout_count"] == 1
            assert patched.json()["fonts"] == {
                "Inter": "https://example.test/inter.woff2"
            }

            structured = client.get(
                "/api/v1/ppt/structured-templates/compat-template"
            )
            assert structured.status_code == 200
            assert structured.json()["name"] == patched.json()["name"]
            assert structured.json()["revision"] == 2

            async def persisted_revisions() -> tuple[int, int]:
                async with session_factory() as session:
                    canonical = await session.get(
                        TemplateV2,
                        "compat-template",
                    )
                    local_state = await session.get(
                        TemplateV2LocalState,
                        "compat-template",
                    )
                    assert canonical is not None
                    assert local_state is not None
                    return canonical.revision, local_state.revision

            assert asyncio.run(persisted_revisions()) == (2, 2)

            mismatch = client.patch(
                "/api/v1/ppt/template/compat-template",
                json={"id": "other"},
            )
            assert mismatch.status_code == 400
            assert mismatch.json()["detail"] == (
                "Template ID in path does not match request body ID"
            )

            deleted = client.delete(
                "/api/v1/ppt/template/compat-template"
            )
            assert deleted.status_code == 204
            assert deleted.content == b""

            missing = client.delete(
                "/api/v1/ppt/template/missing-template"
            )
            assert missing.status_code == 404
            assert missing.json() == {"detail": "Template not found"}
    finally:
        asyncio.run(engine.dispose())
