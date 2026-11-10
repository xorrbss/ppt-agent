"""Owner and tenant scoping applied centrally to every ORM session.

Extracted from ``services.database`` so the engine module stays a thin owner of
connection setup.  ``services.database`` imports this module and calls
``register_owner_scope_hooks()`` once at import time, so any code path that
already imports the engine keeps the same hooks with the same behaviour.
"""

from sqlalchemy import event, inspect, text, true
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql.schema import Table
from sqlalchemy.sql.util import find_tables

from models.sql.async_task import AsyncTaskModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_editor_event import AuthoredSlideEditorEvent
from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.image_asset import ImageAsset
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)
from models.sql.template import TemplateModel
from models.sql.template_create_info import TemplateCreateInfoModel
from models.sql.slide import SlideModel
from models.sql.webhook_subscription import WebhookSubscription
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.sql.reference_snapshot import ReferenceSnapshot
from models.sql.svg_template import (SvgTemplate, SvgTemplateVersion, SvgTemplateFrameAdaptation, SvgTemplateAssetBinding)
from models.sql.asset_gateway_idempotency_receipt import (
    AssetGatewayIdempotencyReceipt,
)
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.public_admission import PublicOperationAdmission
from models.sql.public_artifact import PublicArtifact, PublicExportIdentity
from services.presentation_profile_persistence import stamp_presentation_profile
from services.presentation_dashboard_metadata import presentation_dashboard_metadata
from utils.owner_context import (
    get_current_owner_id,
    get_current_tenant_id,
    owner_context_enforced,
    require_owner_context,
)


_STRICT_OWNER_MODELS = (
    SvgTemplate, SvgTemplateVersion, SvgTemplateFrameAdaptation, SvgTemplateAssetBinding,
    PresentationModel,
    SlideModel,
    PresentationLayoutCodeModel,
    TemplateModel,
    ChatHistoryMessageModel,
    ImageAsset,
    TemplateCreateInfoModel,
    AsyncTaskModel,
    AsyncPresentationGenerationTaskModel,
    WebhookSubscription,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceAsset,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    ReferenceSnapshot,
    AssetGatewayIdempotencyReceipt,
    PresentationExportAttempt,
    AuthoredSlideEditorEvent,
    PublicOperationAdmission,
    PublicArtifact,
    PublicExportIdentity,
)

# These public authority rows carry both dimensions.  Owner scoping alone is
# insufficient because an external issuer may legitimately reuse the same
# subject UUID in more than one tenant.
_STRICT_TENANT_MODELS = (
    SvgTemplate, SvgTemplateVersion, SvgTemplateFrameAdaptation, SvgTemplateAssetBinding,
    PublicOperationAdmission,
    PublicArtifact,
    PublicExportIdentity,
)

_OWNER_SCOPED_TABLE_NAMES = frozenset(
    model.__tablename__ for model in _STRICT_OWNER_MODELS
)


def ownerless_rows_predicate(model):
    """The one owner predicate a SELECT on an owner-scoped model still needs.

    With an owner in context, ``_scope_owned_statements`` already restricts
    every SELECT of a ``_STRICT_OWNER_MODELS`` class to that owner
    (``tests/unit/test_owner_scope_hook_coverage.py``), so a hand-rolled
    ``owner_id ==`` filter is redundant. Without one (workers, unauthenticated
    local contexts) the hook adds nothing, and callers that must only see
    ownerless rows keep that restriction through this predicate. UPDATE and
    DELETE statements are not scoped by the hook and keep their own filters.
    """
    if model not in _STRICT_OWNER_MODELS:
        raise ValueError(f"{model.__name__} is not owner-scoped by the ORM hook")
    if get_current_owner_id() is not None:
        return true()
    return model.owner_id.is_(None)


def owner_scoped_table_names() -> tuple[str, ...]:
    """Tables whose rows are owner-scoped by the ORM hooks (RLS parity source)."""
    return tuple(sorted(_OWNER_SCOPED_TABLE_NAMES))


def catalog_shared_table_names() -> tuple[str, ...]:
    """Owner-scoped tables that also hold shared, ownerless catalog rows.

    Their row-level security predicate has to admit ``owner_id IS NULL`` rows,
    because the ORM criteria admit the shared catalog entries too. No current
    model needs this; the last one (``template_v2``) was removed 2026-09-25.
    """
    return ()


def tenant_scoped_table_names() -> tuple[str, ...]:
    return tuple(sorted(model.__tablename__ for model in _STRICT_TENANT_MODELS))


def _statement_targets_owned_table(execute_state) -> bool:
    mapper = execute_state.bind_mapper
    if mapper is not None and mapper.local_table.name in _OWNER_SCOPED_TABLE_NAMES:
        return True
    for clause in find_tables(
        execute_state.statement,
        check_columns=True,
        include_aliases=True,
        include_joins=True,
        include_selects=True,
    ):
        table = getattr(clause, "element", clause)
        if isinstance(table, Table) and table.name in _OWNER_SCOPED_TABLE_NAMES:
            return True
    return False


def _set_postgres_transaction_principal(_session, _transaction, connection) -> None:
    """Expose request identity to PostgreSQL RLS without widening DB authority."""

    if connection.dialect.name != "postgresql":
        return
    owner_id = get_current_owner_id()
    tenant_id = get_current_tenant_id()
    connection.execute(text("SET LOCAL row_security = on"))
    # The lineage migration rejects old application writers after its commit.
    # This stamp is transaction-local and is present for ORM and Core writes
    # issued through an application Session, including activation CAS updates.
    connection.execute(text("SET LOCAL presenton.generation_lineage_writer_revision = 'e4b7c9d2a1f0'"))
    # ``presenton.request_scope`` lets the owner-table policies deny an
    # authenticated request that carries no identity while process-owned
    # workers and maintenance loops (which never set it) keep their
    # cross-owner reads.
    connection.execute(
        text(
            "SELECT "
            "set_config('presenton.owner_id', :owner_id, true), "
            "set_config('presenton.tenant_id', :tenant_id, true), "
            "set_config('presenton.request_scope', :request_scope, true)"
        ),
        {
            "owner_id": str(owner_id) if owner_id is not None else "",
            "tenant_id": tenant_id or "",
            "request_scope": "on" if owner_context_enforced() else "",
        },
    )


def _scope_owned_statements(execute_state) -> None:
    """Guard every owned-table statement; scope request ORM SELECTs."""
    owner_id = get_current_owner_id()
    tenant_id = get_current_tenant_id()
    skip_owner_scope = bool(execute_state.execution_options.get("skip_owner_scope"))
    if (
        owner_id is None
        and not skip_owner_scope
        # Ask the cheap question first.  Worker loops run with enforcement off,
        # and walking the statement for owned tables is the expensive part of
        # this hook -- it used to run on every SELECT they issued.
        and owner_context_enforced()
        and _statement_targets_owned_table(execute_state)
    ):
        # A tenant is not a substitute identity here: the tenant criteria below
        # cover only ``_STRICT_TENANT_MODELS``, so an ownerless request would
        # otherwise read every owner's rows out of the owner-scoped tables.
        # The guard covers UPDATE and DELETE as well, which matters on SQLite
        # where no row-level security stands behind it.
        require_owner_context()
    if not execute_state.is_select:
        return

    statement = execute_state.statement
    if owner_id is not None and not skip_owner_scope:
        for model in _STRICT_OWNER_MODELS:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda row: row.owner_id == owner_id,
                    include_aliases=True,
                )
            )
    if tenant_id is not None and not execute_state.execution_options.get(
        "skip_tenant_scope"
    ):
        for model in _STRICT_TENANT_MODELS:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda row: row.tenant_id == tenant_id,
                    include_aliases=True,
                )
            )
    execute_state.statement = statement


def _pending_owned_instances(session, owner_models) -> bool:
    """Does this flush touch an owned row -- inserted, changed or deleted?"""
    if not owner_context_enforced():
        return False
    return any(
        isinstance(instance, owner_models)
        for collection in (session.new, session.dirty, session.deleted)
        for instance in collection
    )


def _stamp_new_owned_rows(session, _flush_context, _instances) -> None:
    owner_id = get_current_owner_id()
    tenant_id = get_current_tenant_id()
    owner_models = _STRICT_OWNER_MODELS
    if owner_id is None and _pending_owned_instances(session, owner_models):
        # An enforced request must not persist, mutate or delete ownerless
        # rows; check before any profile derivation so the failure names the
        # real cause.
        require_owner_context()
    profile_identity_fields = {
        "generation_mode",
        "mode",
        "version",
        "theme",
        "community_design_ids",
        "deck_plan",
        "profile_driver_id",
        "profile_schema_version",
    }
    for instance in session.new:
        if isinstance(instance, PresentationModel):
            stamp_presentation_profile(instance)
            instance.dashboard_metadata = presentation_dashboard_metadata(instance.theme)
    for instance in session.dirty:
        if not isinstance(instance, PresentationModel):
            continue
        if instance.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED:
            # A tombstone is not a deck. Deletion deliberately redacts the
            # theme and the mode, which are two of the identity fields below,
            # so re-deriving a profile here asked the resolver to describe a
            # row that has just been emptied on purpose -- and it refused,
            # which made deleting an authored presentation raise.
            instance.dashboard_metadata = None
            continue
        state = inspect(instance)
        if state.attrs.theme.history.has_changes():
            instance.dashboard_metadata = presentation_dashboard_metadata(instance.theme)
        if any(
            state.attrs[field].history.has_changes()
            for field in profile_identity_fields
        ):
            stamp_presentation_profile(instance, allow_driver_transition=True)

    if owner_id is None and tenant_id is None:
        return
    for instance in session.new:
        if owner_id is not None and isinstance(instance, owner_models):
            instance.owner_id = owner_id
        if tenant_id is not None and isinstance(instance, _STRICT_TENANT_MODELS):
            supplied_tenant = getattr(instance, "tenant_id", None)
            if supplied_tenant not in {None, "", tenant_id}:
                raise RuntimeError("tenant_scope_write_mismatch")
            instance.tenant_id = tenant_id


_HOOKS = (
    ("after_begin", _set_postgres_transaction_principal),
    ("do_orm_execute", _scope_owned_statements),
    ("before_flush", _stamp_new_owned_rows),
)


def register_owner_scope_hooks() -> None:
    """Attach the scoping hooks to every Session; safe to call more than once."""
    for identifier, handler in _HOOKS:
        if not event.contains(Session, identifier, handler):
            event.listen(Session, identifier, handler)
