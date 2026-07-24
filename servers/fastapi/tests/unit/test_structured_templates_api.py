import asyncio
from copy import deepcopy
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.v1.ppt.endpoints.structured_templates import (
    STRUCTURED_TEMPLATES_ROUTER,
    StructuredTemplateCreate,
    StructuredTemplateUpdate,
    create_structured_template,
    delete_structured_template,
    get_structured_template,
    list_structured_templates,
    update_structured_template,
)
from api.v1.ppt.router import API_V1_PPT_ROUTER
from api.middlewares import SessionAuthMiddleware
from models.sql.presentation import PresentationModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from services.database import get_async_session
from services.template_v2_rollout import TemplateV2RolloutService
from templates.v2.constants import (
    LEGACY_PRESENTATION_VERSION,
    TEMPLATE_V2_VERSION,
)
from templates.v2.policy import StructuredTemplatePolicy

SOURCE_PRESENTATION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


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


def _source_presentation(
    presentation_id: uuid.UUID = SOURCE_PRESENTATION_ID,
    *,
    version: str = TEMPLATE_V2_VERSION,
    mode: str | None = "template",
) -> PresentationModel:
    return PresentationModel(
        id=presentation_id,
        content="Template V2 provenance",
        n_slides=1,
        language="en",
        version=version,
        mode=mode,
        layout={"name": "native"},
    )


def _template(template_id: str = "existing") -> TemplateV2:
    return TemplateV2(
        id=template_id,
        presentation_id=SOURCE_PRESENTATION_ID,
        name="Existing",
        description="Persisted while the flag was enabled",
        layouts=_layouts(),
        assets={"logo": {"path": "asset://logo"}},
    )


def test_create_is_default_off(fake_async_session, monkeypatch):
    monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
    monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            create_structured_template(
                StructuredTemplateCreate(
                    presentation_id=SOURCE_PRESENTATION_ID,
                    id="disabled",
                    name="Disabled",
                    layouts=_layouts(),
                ),
                fake_async_session,
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail == "template_v2_creation_disabled"
    assert fake_async_session.added == []


def test_create_schema_requires_explicit_id():
    with pytest.raises(ValidationError) as error:
        StructuredTemplateCreate.model_validate(
            {
                "presentation_id": SOURCE_PRESENTATION_ID,
                "name": "Missing id",
                "layouts": _layouts(),
            }
        )

    assert error.value.errors()[0]["loc"] == ("id",)
    assert error.value.errors()[0]["type"] == "missing"


def test_create_persists_validated_native_layout_without_projection(
    fake_async_session,
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "allowed")
    fake_async_session._get_results[SOURCE_PRESENTATION_ID] = _source_presentation()
    payload = StructuredTemplateCreate(
        presentation_id=SOURCE_PRESENTATION_ID,
        id="allowed",
        name="Native template",
        description="Phase 1",
        layouts=_layouts(),
        assets={"logo": {"path": "asset://logo"}},
    )

    response = asyncio.run(create_structured_template(payload, fake_async_session))

    assert response.id == "allowed"
    assert response.layouts == payload.layouts
    assert fake_async_session.added[0].layouts == response.layouts
    assert fake_async_session.commit_count == 1

    payload.layouts["layouts"][0]["components"][0]["elements"][0]["runs"][0][
        "text"
    ] = "later"
    assert response.layouts["layouts"][0]["components"][0]["elements"][0]["runs"][
        0
    ]["text"] == "Original"


def test_existing_template_read_remains_available_when_flag_is_off(
    monkeypatch,
    fake_async_session,
):
    monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)
    template = _template()
    fake_async_session._get_results["existing"] = template

    response = asyncio.run(
        get_structured_template("existing", fake_async_session)
    )

    assert response.id == "existing"
    assert response.layouts == template.layouts


def test_draft_template_with_null_layouts_remains_readable_and_discoverable(
    monkeypatch,
    fake_async_session,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "draft")
    template = _template("draft")
    template.layouts = None
    fake_async_session._get_results["draft"] = template

    response = asyncio.run(get_structured_template("draft", fake_async_session))

    class ListedSession:
        async def execute(self, *_args, **_kwargs):
            class Result:
                def scalars(self):
                    return self

                def all(self):
                    return [template]

            return Result()

    discovered = asyncio.run(list_structured_templates(sql_session=ListedSession()))

    assert response.layouts is None
    assert len(discovered) == 1
    assert discovered[0].layouts is None


def test_non_null_malformed_layouts_fail_closed(fake_async_session):
    template = _template()
    template.layouts = {"layouts": [{"id": "missing-required-fields"}]}
    fake_async_session._get_results["existing"] = template

    with pytest.raises(HTTPException) as error:
        asyncio.run(get_structured_template("existing", fake_async_session))

    assert error.value.status_code == 409
    assert error.value.detail == "template_v2_layouts_invalid"


def test_update_and_delete_honor_allowlist(
    monkeypatch,
    fake_async_session,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "existing")
    template = _template()
    original = deepcopy(template.layouts)
    fake_async_session._get_results["existing"] = template

    class Updated:
        rowcount = 1

    async def apply_update(*_args, **_kwargs):
        template.name = "Renamed"
        template.assets = None
        template.revision = 2
        return Updated()

    fake_async_session.execute = apply_update

    response = asyncio.run(
        update_structured_template(
            "existing",
            StructuredTemplateUpdate(
                name="Renamed",
                assets=None,
                expected_revision=1,
            ),
            fake_async_session,
        )
    )
    delete_response = asyncio.run(
        delete_structured_template(
            "existing",
            fake_async_session,
        )
    )

    assert response.name == "Renamed"
    assert response.assets is None
    assert response.layouts == original
    assert response.revision == 2
    assert fake_async_session.deleted == [template]
    assert fake_async_session.commit_count == 2
    assert delete_response.status_code == 204


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_mutation_integrity_error_rolls_back_and_returns_stable_conflict(
    operation,
    monkeypatch,
    fake_async_session,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "existing")
    fake_async_session._get_results["existing"] = _template()

    async def fail_commit():
        fake_async_session.commit_count += 1
        raise IntegrityError("mutation", {}, RuntimeError("private database detail"))

    fake_async_session.commit = fail_commit
    if operation == "update":
        class Updated:
            rowcount = 1

        async def apply_update(*_args, **_kwargs):
            return Updated()

        fake_async_session.execute = apply_update

    with pytest.raises(HTTPException) as error:
        if operation == "update":
            asyncio.run(
                update_structured_template(
                    "existing",
                    StructuredTemplateUpdate(name="Renamed", expected_revision=1),
                    fake_async_session,
                )
            )
        else:
            asyncio.run(
                delete_structured_template("existing", fake_async_session)
            )

    assert error.value.status_code == 409
    assert error.value.detail == "Structured template persistence conflict"
    assert "private database detail" not in error.value.detail
    assert fake_async_session.rollback_count == 1


@pytest.mark.parametrize("field", ["name", "layouts", "is_default"])
def test_update_rejects_null_for_required_persisted_fields(field):
    with pytest.raises(ValidationError, match=f"{field} cannot be null"):
        StructuredTemplateUpdate.model_validate(
            {field: None, "expected_revision": 1}
        )


def test_expected_revision_without_a_mutation_is_rejected():
    with pytest.raises(ValidationError, match="at least one mutable field"):
        StructuredTemplateUpdate(expected_revision=1)


def test_update_requires_expected_revision():
    with pytest.raises(ValidationError) as error:
        StructuredTemplateUpdate(name="Missing optimistic concurrency token")

    assert error.value.errors()[0]["loc"] == ("expected_revision",)
    assert error.value.errors()[0]["type"] == "missing"


def test_discovery_is_hidden_without_executing_query_when_flag_is_off(
    monkeypatch,
):
    monkeypatch.delenv("ENABLE_TEMPLATE_V2", raising=False)

    class NoQuerySession:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("disabled discovery must not query templates")

    assert (
        asyncio.run(list_structured_templates(sql_session=NoQuerySession())) == []
    )


def test_enabled_with_empty_allowlist_hides_discovery_and_rejects_writes(
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.delenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", raising=False)

    class NoQuerySession:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("empty allowlist discovery must not query templates")

    assert (
        asyncio.run(list_structured_templates(sql_session=NoQuerySession())) == []
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            create_structured_template(
                StructuredTemplateCreate(
                    presentation_id=SOURCE_PRESENTATION_ID,
                    id="blocked",
                    name="Blocked",
                    layouts=_layouts(),
                ),
                NoQuerySession(),
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail == "template_v2_allowlist_required"


def test_structured_template_routes_stay_under_authenticated_api_v1_namespace():
    app = FastAPI()
    app.include_router(API_V1_PPT_ROUTER)
    paths = set(app.openapi()["paths"])

    assert "/api/v1/ppt/structured-templates" in paths
    assert "/api/v1/ppt/structured-templates/{template_id}" in paths
    assert not any(path.startswith("/api/v2/") for path in paths)
    auth = SessionAuthMiddleware(app=None)
    assert auth._requires_auth("/api/v1/ppt/structured-templates") is True


def test_http_post_contract_returns_201_and_native_payload(
    fake_async_session,
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "http-template")
    fake_async_session._get_results[SOURCE_PRESENTATION_ID] = _source_presentation()
    app = FastAPI()
    app.include_router(STRUCTURED_TEMPLATES_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_async_session] = lambda: fake_async_session

    with TestClient(app) as client:
        missing_id = client.post(
            "/api/v1/ppt/structured-templates",
            json={
                "presentation_id": str(SOURCE_PRESENTATION_ID),
                "name": "Missing id",
                "layouts": _layouts(),
            },
        )
        response = client.post(
            "/api/v1/ppt/structured-templates",
            json={
                "presentation_id": str(SOURCE_PRESENTATION_ID),
                "id": "http-template",
                "name": "HTTP native template",
                "layouts": _layouts(),
            },
        )

    assert missing_id.status_code == 422
    assert response.status_code == 201
    assert response.json()["id"] == "http-template"
    assert response.json()["layouts"] == StructuredTemplateCreate(
        presentation_id=SOURCE_PRESENTATION_ID,
        id="http-template",
        name="HTTP native template",
        layouts=_layouts(),
    ).layouts


def test_temp_sqlite_asgi_auth_crud_contract_and_off_mutation_gate(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv(
        "TEMPLATE_V2_TEMPLATE_ALLOWLIST",
        "api-template,missing-template",
    )
    monkeypatch.setattr(
        "api.middlewares.is_disable_auth_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "api.middlewares.get_session_token_from_request",
        lambda request: request.headers.get("x-test-token"),
    )
    monkeypatch.setattr(
        "api.middlewares.get_auth_status",
        lambda token: {
            "configured": True,
            "authenticated": token == "valid",
            "username": "tester" if token == "valid" else None,
        },
    )

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'structured-api.db'}",
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    source_presentation = _source_presentation()
    legacy_source = _source_presentation(
        uuid.uuid4(),
        version=LEGACY_PRESENTATION_VERSION,
    )

    async def setup_database() -> None:
        async with async_engine.begin() as connection:
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
            session.add_all([source_presentation, legacy_source])
            await session.commit()

    async def get_test_session():
        async with session_factory() as session:
            yield session

    asyncio.run(setup_database())
    app = FastAPI()
    app.add_middleware(SessionAuthMiddleware)
    app.include_router(STRUCTURED_TEMPLATES_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_async_session] = get_test_session
    headers = {"x-test-token": "valid"}
    create_body = {
        "presentation_id": str(source_presentation.id),
        "id": "api-template",
        "name": "API native template",
        "layouts": _layouts(),
    }
    lossless_layouts = create_body["layouts"]
    lossless_layouts["upstream_envelope_extension"] = {"version": 3}
    layout = lossless_layouts["layouts"][0]
    layout["upstream_layout_extension"] = "layout-vNext"
    component = layout["components"][0]
    component["upstream_component_extension"] = "component-vNext"
    component["position"]["upstream_position_extension"] = "position-vNext"
    element = component["elements"][0]
    element["upstream_element_extension"] = "text-vNext"
    element["runs"][0]["upstream_run_extension"] = {"locale": "ko-KR"}

    with TestClient(app) as client:
        assert (
            client.get("/api/v1/ppt/structured-templates/api-template").status_code
            == 401
        )
        assert (
            client.get(
                "/api/v1/ppt/structured-templates/missing-template",
                headers=headers,
            ).status_code
            == 404
        )

        missing_presentation = deepcopy(create_body)
        missing_presentation.pop("presentation_id")
        assert (
            client.post(
                "/api/v1/ppt/structured-templates",
                headers=headers,
                json=missing_presentation,
            ).status_code
            == 422
        )
        nonexistent = deepcopy(create_body)
        nonexistent["id"] = "missing-template"
        nonexistent["presentation_id"] = str(uuid.uuid4())
        assert (
            client.post(
                "/api/v1/ppt/structured-templates",
                headers=headers,
                json=nonexistent,
            ).status_code
            == 404
        )
        wrong_identity = deepcopy(create_body)
        wrong_identity["id"] = "missing-template"
        wrong_identity["presentation_id"] = str(legacy_source.id)
        assert (
            client.post(
                "/api/v1/ppt/structured-templates",
                headers=headers,
                json=wrong_identity,
            ).status_code
            == 409
        )
        disallowed_missing_source = deepcopy(create_body)
        disallowed_missing_source["id"] = "not-allowlisted"
        disallowed_missing_source["presentation_id"] = str(uuid.uuid4())
        assert (
            client.post(
                "/api/v1/ppt/structured-templates",
                headers=headers,
                json=disallowed_missing_source,
            ).status_code
            == 403
        )

        created = client.post(
            "/api/v1/ppt/structured-templates",
            headers=headers,
            json=create_body,
        )
        duplicate = client.post(
            "/api/v1/ppt/structured-templates",
            headers=headers,
            json=create_body,
        )
        assert created.status_code == 201
        assert created.json()["presentation_id"] == str(source_presentation.id)
        assert created.json()["revision"] == 1
        assert created.json()["layouts"] == lossless_layouts
        assert duplicate.status_code == 409
        fetched = client.get(
            "/api/v1/ppt/structured-templates/api-template",
            headers=headers,
        )
        assert fetched.status_code == 200
        assert fetched.json()["layouts"] == lossless_layouts
        assert (
            client.patch(
                "/api/v1/ppt/structured-templates/missing-template",
                headers=headers,
                json={"name": "still missing", "expected_revision": 1},
            ).status_code
            == 404
        )
        missing_revision = client.patch(
            "/api/v1/ppt/structured-templates/api-template",
            headers=headers,
            json={"name": "Unsafe blind overwrite"},
        )
        assert missing_revision.status_code == 422
        assert missing_revision.json()["detail"][0]["loc"] == [
            "body",
            "expected_revision",
        ]

        updated_lossless_layouts = deepcopy(lossless_layouts)
        updated_lossless_layouts["upstream_envelope_extension"]["version"] = 4
        updated_lossless_layouts["layouts"][0]["components"][0]["elements"][0][
            "upstream_save_extension"
        ] = {"editor": "konva-vNext"}
        saved = client.patch(
            "/api/v1/ppt/structured-templates/api-template",
            headers=headers,
            json={
                "name": "First Studio save",
                "layouts": updated_lossless_layouts,
                "expected_revision": 1,
            },
        )
        stale = client.patch(
            "/api/v1/ppt/structured-templates/api-template",
            headers=headers,
            json={
                "name": "Stale Studio save",
                "expected_revision": 1,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 2
        assert saved.json()["layouts"] == updated_lossless_layouts
        assert stale.status_code == 409
        assert stale.json()["detail"] == {
            "code": "template_v2_revision_conflict",
            "expected_revision": 1,
            "current_revision": 2,
        }

        monkeypatch.setenv("ENABLE_TEMPLATE_V2", "false")
        assert (
            client.patch(
                "/api/v1/ppt/structured-templates/api-template",
                headers=headers,
                json={"name": "blocked update", "expected_revision": 2},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                "/api/v1/ppt/structured-templates/api-template",
                headers=headers,
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/v1/ppt/structured-templates/api-template",
                headers=headers,
            ).status_code
            == 200
        )

    async def assert_provenance() -> None:
        async with session_factory() as session:
            template = await session.get(TemplateV2, "api-template")
            assert template is not None
            assert template.presentation_id == source_presentation.id
            assert template.layouts == updated_lossless_layouts

    asyncio.run(assert_provenance())
    asyncio.run(async_engine.dispose())


def test_concurrent_duplicate_post_rolls_back_loser_and_returns_409(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "concurrent-template")

    async def scenario() -> None:
        async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'structured-concurrent.db'}",
            poolclass=NullPool,
        )
        session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
        source_presentation = _source_presentation()
        async with async_engine.begin() as connection:
            await connection.execute(text("PRAGMA journal_mode=WAL"))
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
            session.add(source_presentation)
            await session.commit()

        barrier = asyncio.Barrier(2)
        winner_committed = asyncio.Event()

        class RacingSession:
            def __init__(self, session, *, winner: bool):
                self.session = session
                self.winner = winner
                self.rollback_count = 0
                self.template_get_count = 0

            async def get(self, model, key):
                result = await self.session.get(model, key)
                if model is PresentationModel:
                    return result
                self.template_get_count += 1
                if self.template_get_count == 1:
                    # End each read transaction before the competing inserts.
                    await self.session.rollback()
                    await barrier.wait()
                return result

            def add(self, value):
                self.session.add(value)

            async def commit(self):
                if not self.winner:
                    await winner_committed.wait()
                try:
                    await self.session.commit()
                finally:
                    if self.winner:
                        winner_committed.set()

            async def rollback(self):
                self.rollback_count += 1
                await self.session.rollback()

            async def refresh(self, value):
                await self.session.refresh(value)

        async with (
            session_factory() as winner_session,
            session_factory() as loser_session,
        ):
            winner = RacingSession(winner_session, winner=True)
            loser = RacingSession(loser_session, winner=False)
            payload = {
                "presentation_id": str(source_presentation.id),
                "id": "concurrent-template",
                "name": "Concurrent template",
                "layouts": _layouts(),
            }
            results = await asyncio.gather(
                create_structured_template(
                    StructuredTemplateCreate.model_validate(payload),
                    winner,
                ),
                create_structured_template(
                    StructuredTemplateCreate.model_validate(payload),
                    loser,
                ),
                return_exceptions=True,
            )

            responses = [
                result for result in results if not isinstance(result, Exception)
            ]
            errors = [result for result in results if isinstance(result, Exception)]
            assert len(responses) == 1
            assert len(errors) == 1
            assert isinstance(errors[0], HTTPException)
            assert errors[0].status_code == 409
            assert errors[0].detail == "Structured template already exists"
            assert loser.rollback_count == 1
            assert (
                await loser_session.scalar(
                    select(func.count()).select_from(TemplateV2)
                )
                == 1
            )

        await async_engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("conflict", "expected_status", "expected_detail"),
    [
        ("duplicate", 409, "Structured template already exists"),
        ("source_missing", 404, "Source presentation not found"),
        ("other", 409, "Structured template persistence conflict"),
    ],
)
def test_create_integrity_error_is_classified_after_rollback(
    conflict,
    expected_status,
    expected_detail,
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", "allowed")
    source = _source_presentation()

    class IntegritySession:
        def __init__(self):
            self.rolled_back = False
            self.rollback_count = 0

        async def get(self, model, key):
            if model is TemplateV2:
                if not self.rolled_back:
                    return None
                return _template("allowed") if conflict == "duplicate" else None
            if model is PresentationModel:
                if self.rolled_back and conflict == "source_missing":
                    return None
                return source
            raise AssertionError(f"unexpected lookup: {model!r} {key!r}")

        def add(self, _value):
            return None

        async def commit(self):
            raise IntegrityError("INSERT", {}, RuntimeError("constraint"))

        async def rollback(self):
            self.rolled_back = True
            self.rollback_count += 1

    session = IntegritySession()
    payload = StructuredTemplateCreate(
        presentation_id=SOURCE_PRESENTATION_ID,
        id="allowed",
        name="Classified conflict",
        layouts=_layouts(),
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(create_structured_template(payload, session))

    assert error.value.status_code == expected_status
    assert error.value.detail == expected_detail
    assert session.rollback_count == 1


def test_official_template_v2_telemetry_hashes_identifier_and_accepts_no_content():
    events: list[dict[str, object]] = []
    policy = StructuredTemplatePolicy(
        creation_enabled=True,
        allowed_template_ids=frozenset({"raw-secret-template-id"}),
    )
    observer = TemplateV2RolloutService(
        policy,  # structurally compatible rollout policy
        events.append,
        format_marker=TEMPLATE_V2_VERSION,
    )

    observer.record_outcome(
        operation="create",
        outcome="success",
        template_id="raw-secret-template-id",
    )

    assert events[0]["format_marker"] == TEMPLATE_V2_VERSION
    assert events[0]["template_id_hash"] != "raw-secret-template-id"
    assert "raw-secret-template-id" not in repr(events[0])
    assert set(events[0]) == {
        "schema_version",
        "event",
        "operation",
        "outcome",
        "format_marker",
        "template_id_hash",
        "creation_enabled",
    }
