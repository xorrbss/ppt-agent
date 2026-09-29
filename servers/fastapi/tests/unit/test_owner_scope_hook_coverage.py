"""The central owner-scope hook is load-bearing for every model whose endpoint
or service predicate was deleted in docs/refactoring-audit-20260929.md section 4,
architecture item 7.

Each case inserts one row per owner, then selects the whole table as owner A
with no ``owner_id`` predicate at all: only A's row may come back, and the
same statement with ``skip_owner_scope`` must return both rows, which proves
the hook (not the data, not the query) is what hides owner B. An enforced
request with no owner is refused outright, so no ``owner_id IS NULL``
predicate is needed for that case either.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.reference_snapshot import ReferenceSnapshot
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import WorkspaceAsset, WorkspaceConversation, WorkspaceOperation
from services.database_owner_scope import _STRICT_OWNER_MODELS, register_owner_scope_hooks
from utils.owner_context import (
    OwnerContextRequiredError,
    reset_current_owner_id,
    reset_request_scope_active,
    set_current_owner_id,
    set_request_scope_active,
)

OWNER_A = uuid.uuid5(uuid.NAMESPACE_URL, "owner-scope-hook-a")
OWNER_B = uuid.uuid5(uuid.NAMESPACE_URL, "owner-scope-hook-b")
PRESENTATION_ID = uuid.uuid4()


def _image_asset(owner):
    return ImageAsset(path=f"/app_data/images/{owner}.png")


def _slide(owner):
    return SlideModel(presentation=PRESENTATION_ID, layout_group="g", layout="l", index=0, content={})


def _reference_snapshot(owner):
    return ReferenceSnapshot(
        provider_id="uploaded_document", source_revision="r", source_fingerprint="f",
        normalized_payload={}, asset_bindings=[], content_sha256="0" * 64,
    )


def _presentation(owner):
    return PresentationModel(
        content="c", n_slides=1, language="ko", layout=None, mode="authored",
        theme={"mode": "authored"},
    )


def _conversation(owner):
    return WorkspaceConversation(presentation_id=PRESENTATION_ID)


def _workspace_asset(owner):
    return WorkspaceAsset(original_name="a.png", storage_key=f"assets/{owner}")


def _operation(owner):
    return WorkspaceOperation(
        conversation_id=uuid.uuid4(), presentation_id=PRESENTATION_ID, anchor_position=1,
        kind="presentation.generate", idempotency_key_hash="k" * 64, request_hash="r" * 64,
    )


def _async_task(owner):
    return AsyncPresentationGenerationTaskModel(id=f"task-{owner}", status="pending")


#: (model, row factory) for every model whose hand-rolled predicate was removed.
COVERED = [
    (ImageAsset, _image_asset),
    (SlideModel, _slide),
    (ReferenceSnapshot, _reference_snapshot),
    (PresentationModel, _presentation),
    (WorkspaceConversation, _conversation),
    (WorkspaceAsset, _workspace_asset),
    (WorkspaceOperation, _operation),
    (AsyncPresentationGenerationTaskModel, _async_task),
]


@contextmanager
def _owner(owner_id):
    token = set_current_owner_id(owner_id)
    try:
        yield
    finally:
        reset_current_owner_id(token)


@pytest.fixture()
def engine(tmp_path):
    register_owner_scope_hooks()
    engine = create_engine(f"sqlite:///{tmp_path / 'owner-scope.db'}")
    SQLModel.metadata.create_all(
        engine, tables=[User.__table__] + [model.__table__ for model, _ in COVERED]
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(("model", "factory"), COVERED, ids=[m.__name__ for m, _ in COVERED])
def test_hook_hides_the_other_owner_without_a_hand_rolled_predicate(engine, model, factory):
    assert model in _STRICT_OWNER_MODELS
    with Session(engine) as session:
        for owner in (OWNER_A, OWNER_B):
            with _owner(owner):
                session.add(factory(owner))
                session.commit()
    with Session(engine) as session, _owner(OWNER_A):
        scoped = session.execute(select(model)).scalars().all()
        assert [row.owner_id for row in scoped] == [OWNER_A]
        unscoped = session.execute(
            select(model).execution_options(skip_owner_scope=True)
        ).scalars().all()
        assert sorted(str(row.owner_id) for row in unscoped) == sorted([str(OWNER_A), str(OWNER_B)])


@pytest.mark.parametrize(("model", "factory"), COVERED, ids=[m.__name__ for m, _ in COVERED])
def test_enforced_request_without_an_owner_is_refused(engine, model, factory, monkeypatch):
    monkeypatch.setenv("DISABLE_AUTH", "false")
    with Session(engine) as session:
        with _owner(OWNER_B):
            session.add(factory(OWNER_B))
            session.commit()
    scope = set_request_scope_active(True)
    try:
        with Session(engine) as session, pytest.raises(OwnerContextRequiredError):
            session.execute(select(model)).scalars().all()
    finally:
        reset_request_scope_active(scope)


def test_ownerless_rows_predicate_is_a_no_op_with_an_owner(engine):
    from services.database_owner_scope import ownerless_rows_predicate

    with Session(engine) as session:
        with _owner(None):
            session.add(_image_asset("none"))
            session.commit()
        for owner in (OWNER_A, OWNER_B):
            with _owner(owner):
                session.add(_image_asset(owner))
                session.commit()
    with Session(engine) as session:
        with _owner(OWNER_A):
            rows = session.execute(
                select(ImageAsset).where(ownerless_rows_predicate(ImageAsset))
            ).scalars().all()
            assert [row.owner_id for row in rows] == [OWNER_A]
        with _owner(None):
            rows = session.execute(
                select(ImageAsset).where(ownerless_rows_predicate(ImageAsset))
            ).scalars().all()
            assert [row.owner_id for row in rows] == [None]


def test_ownerless_rows_predicate_refuses_a_model_the_hook_does_not_scope():
    from models.sql.user import User as UnscopedModel
    from services.database_owner_scope import ownerless_rows_predicate

    with pytest.raises(ValueError):
        ownerless_rows_predicate(UnscopedModel)
