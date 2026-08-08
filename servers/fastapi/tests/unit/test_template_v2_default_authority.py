import asyncio
from copy import deepcopy
import json
import uuid

import pytest
from pydantic import ValidationError

from services.template_v2_service import (
    TemplateV2CatalogMutationError,
    TemplateV2DefaultMutationError,
    TemplateV2Service,
)
from models.sql.template_v2 import TemplateV2
from templates.v2 import catalog
from templates.v2.catalog import (
    DEFAULT_CATALOG_PATH,
    TemplateV2CatalogManifest,
)


def _catalog_payload() -> dict:
    return json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))


def test_catalog_default_must_reference_a_published_entry():
    payload = _catalog_payload()
    payload["default_template_id"] = "missing-template"

    with pytest.raises(
        ValidationError,
        match="default_template_id must reference an entry",
    ):
        TemplateV2CatalogManifest.model_validate(payload)

    payload = _catalog_payload()
    payload["default_template_id"] = payload["entries"][0]["id"]

    with pytest.raises(
        ValidationError,
        match="catalog default template must be published",
    ):
        TemplateV2CatalogManifest.model_validate(payload)


def test_catalog_default_accepts_one_published_entry():
    payload = deepcopy(_catalog_payload())
    selected = payload["entries"][0]
    selected["publication_status"] = "published"
    selected["provenance"]["license"].update(
        {
            "embedded_assets_license_verified": True,
            "redistribution_approved": True,
        }
    )
    payload["default_template_id"] = selected["id"]

    manifest = TemplateV2CatalogManifest.model_validate(payload)

    assert manifest.default_template_id == selected["id"]


def test_default_authority_requires_selected_release_ready_entry(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "get_template_v2_default_template_id",
        lambda: "selected-template",
    )
    monkeypatch.setattr(
        catalog,
        "is_template_v2_release_ready",
        lambda template_id, assets: assets == {"release": "ready"},
    )

    assert catalog.is_template_v2_default_release(
        "selected-template",
        {"release": "ready"},
    )
    assert not catalog.is_template_v2_default_release(
        "other-template",
        {"release": "ready"},
    )
    assert not catalog.is_template_v2_default_release(
        "selected-template",
        {"release": "blocked"},
    )


def test_service_rejects_default_mutation_before_touching_storage():
    service = TemplateV2Service(object())

    with pytest.raises(TemplateV2DefaultMutationError):
        asyncio.run(
            service.create(
                presentation_id=uuid.uuid4(),
                template_id="user-default",
                name="User default",
                description=None,
                layouts={},
                assets=None,
                is_default=True,
            )
        )

    with pytest.raises(TemplateV2DefaultMutationError):
        asyncio.run(
            service.update(
                "user-default",
                changes={"is_default": False},
                expected_revision=1,
            )
        )


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_service_rejects_catalog_identity_mutation_before_touching_storage(
    operation,
):
    catalog_id = _catalog_payload()["entries"][0]["id"]
    service = TemplateV2Service(object())

    if operation == "create":
        action = service.create(
            presentation_id=uuid.uuid4(),
            template_id=catalog_id,
            name="Catalog preemption",
            description=None,
            layouts={},
            assets=None,
            is_default=False,
        )
    elif operation == "update":
        action = service.update(catalog_id, changes={"name": "Tampered"})
    else:
        action = service.delete(catalog_id)

    with pytest.raises(TemplateV2CatalogMutationError):
        asyncio.run(action)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_service_rejects_ownerless_database_default_mutation(operation):
    template = TemplateV2(
        id="legacy-public-default",
        owner_id=None,
        name="Legacy public default",
        is_default=True,
    )

    class Session:
        async def get(self, _model, template_id):
            assert template_id == template.id
            return template

    service = TemplateV2Service(Session())
    action = (
        service.update(template.id, changes={"name": "Tampered"})
        if operation == "update"
        else service.delete(template.id)
    )

    with pytest.raises(TemplateV2DefaultMutationError):
        asyncio.run(action)
