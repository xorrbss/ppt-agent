from collections.abc import AsyncGenerator
import os
import time

from sqlalchemy import BigInteger, event, inspect, or_, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.orm import Session, with_loader_criteria
from sqlmodel import SQLModel

from models.sql.async_task import AsyncTaskModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.authored_slide_editor_event import AuthoredSlideEditorEvent
from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.authored_canonical_revision import AuthoredCanonicalRevision
from models.sql.authored_editable_hybrid_mvp_completion import (
    AuthoredEditableHybridMvpCompletion,
)
from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.image_asset import ImageAsset
from models.sql.key_value import KeyValueSqlModel
from models.sql.ollama_pull_status import OllamaPullStatus
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.presentation_version import PresentationVersionModel
from models.sql.template import TemplateModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_revision import TemplateV2Revision
from models.sql.template_v2_pptx_import import TemplateV2PptxImport
from models.sql.template_create_info import TemplateCreateInfoModel
from models.sql.slide import SlideModel
from models.sql.slidespec_artifact_bundle import SlideSpecArtifactBundle
from models.sql.webhook_subscription import WebhookSubscription
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceCleanupJournal,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.sql.user import User
from models.sql.account_deletion_request import AccountDeletionRequest
from models.sql.access_token import AccessToken
from models.sql.public_principal_binding import PublicPrincipalBinding
from models.sql.provider_settings import ProviderSettings
from models.sql.reference_snapshot import ReferenceSnapshot
from models.sql.asset_gateway_idempotency_receipt import (
    AssetGatewayIdempotencyReceipt,
)
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.provider_usage_receipt import ProviderUsageReceipt
from models.sql.provider_runtime_reservation import (
    ProviderRuntimeBudget,
    ProviderRuntimeCircuit,
    ProviderRuntimeReservation,
)
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
)
from models.sql.public_artifact import (
    PublicArtifact,
    PublicArtifactPublication,
    PublicExportIdentity,
    PublicSlideCheckpoint,
)
from models.sql.authored_vnext_repository import AUTHORED_VNEXT_SQL_TABLES
from templates.v2.catalog import get_template_v2_default_template_id
from services.presentation_profile_persistence import stamp_presentation_profile
from utils.owner_context import get_current_owner_id, get_current_tenant_id
from utils.get_env import get_migrate_database_on_startup_env
from utils.db_utils import (
    get_database_url_and_connect_args,
    get_pool_kwargs,
)


database_url, connect_args = get_database_url_and_connect_args()

# Apply connection-pool settings for server-class databases (PostgreSQL, MySQL).
# SQLite uses a file-lock model and ignores pool configuration, so we skip it.
_pool_kwargs = get_pool_kwargs() if "sqlite" not in database_url else {}

sql_engine: AsyncEngine = create_async_engine(
    database_url, connect_args=connect_args, **_pool_kwargs
)


if "sqlite" in database_url:
    @event.listens_for(sql_engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        _configure_sqlite_connection(dbapi_connection)


def _configure_sqlite_connection(dbapi_connection) -> None:
    """Apply and verify SQLite process-safety pragmas with bounded WAL retry."""
    cursor = dbapi_connection.cursor()
    try:
        # Bound each journal transition attempt independently; after WAL is
        # established, normal database work receives the longer busy timeout.
        cursor.execute("PRAGMA busy_timeout=250")
        journal_mode_supported = False
        for attempt in range(6):
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                result = cursor.fetchone()
                mode = str(result[0]).strip().lower() if result else ""
                # SQLite's true in-memory databases report ``memory`` and
                # cannot enter WAL mode. They are not a multiprocess target.
                journal_mode_supported = mode in {"wal", "memory"}
                if journal_mode_supported:
                    break
            except Exception:
                if attempt == 5:
                    break
            time.sleep(min(0.01 * (2**attempt), 0.1))
        if not journal_mode_supported:
            raise RuntimeError("sqlite_wal_configuration_failed") from None

        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA busy_timeout")
        busy_timeout = cursor.fetchone()
        if not busy_timeout or int(busy_timeout[0]) < 30000:
            raise RuntimeError("sqlite_busy_timeout_configuration_failed")

        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA foreign_keys")
        foreign_keys = cursor.fetchone()
        if not foreign_keys or int(foreign_keys[0]) != 1:
            raise RuntimeError("sqlite_foreign_keys_configuration_failed")
    finally:
        cursor.close()


async_session_maker = async_sessionmaker(sql_engine, expire_on_commit=False)


PUBLIC_CONTROLLER_DATABASE_ROLE = "presenton_public_controller"
PUBLIC_API_DATABASE_ROLE = "presenton_public_api"


def _local_development_environment() -> bool:
    configured_public_runtime = bool(
        (os.getenv("PUBLIC_DATABASE_PROCESS_ROLE") or "").strip()
        or (os.getenv("PPT_PUBLIC_OIDC_ISSUER") or "").strip()
        or (os.getenv("PUBLIC_V2_INTEGRATION_PROFILE") or "").strip()
        == "postgresql-strict"
    )
    raw_environment = (os.getenv("ENVIRONMENT") or "").strip().lower()
    if configured_public_runtime and not raw_environment:
        raise RuntimeError("public_environment_required")
    return (raw_environment or "development") in {
        "local",
        "development",
        "dev",
        "test",
    }


async def _postgres_role_authority(engine: AsyncEngine) -> dict[str, object]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT current_user AS role_name, session_user AS session_role_name, "
                    "role.rolsuper AS is_superuser, "
                    "role.rolbypassrls AS bypass_rls, "
                    "role.rolinherit AS inherits_role_privileges, "
                    "(SELECT rolsuper FROM pg_roles WHERE rolname = session_user) "
                    "AS session_is_superuser, "
                    "(SELECT rolbypassrls FROM pg_roles WHERE rolname = session_user) "
                    "AS session_bypass_rls, "
                    "(SELECT rolinherit FROM pg_roles WHERE rolname = session_user) "
                    "AS session_inherits_role_privileges, "
                    "(SELECT rolsuper FROM pg_roles WHERE rolname = :controller_role) "
                    "AS controller_group_is_superuser, "
                    "(SELECT rolbypassrls FROM pg_roles WHERE rolname = :controller_role) "
                    "AS controller_group_bypass_rls, "
                    "(SELECT rolcanlogin FROM pg_roles WHERE rolname = :controller_role) "
                    "AS controller_group_can_login, "
                    "(SELECT rolsuper FROM pg_roles WHERE rolname = :api_role) "
                    "AS api_group_is_superuser, "
                    "(SELECT rolbypassrls FROM pg_roles WHERE rolname = :api_role) "
                    "AS api_group_bypass_rls, "
                    "(SELECT rolcanlogin FROM pg_roles WHERE rolname = :api_role) "
                    "AS api_group_can_login, "
                    "pg_has_role(current_user, :controller_role, 'member') "
                    "AS is_controller, "
                    "pg_has_role(current_user, :api_role, 'member') AS is_api "
                    "FROM pg_roles AS role WHERE role.rolname = current_user"
                ),
                {
                    "controller_role": PUBLIC_CONTROLLER_DATABASE_ROLE,
                    "api_role": PUBLIC_API_DATABASE_ROLE,
                },
            )
        ).mappings().one()
    return dict(row)


def validate_public_database_authority(
    authority: dict[str, object], *, controller: bool
) -> None:
    """Reject shared/over-privileged roles at the public process boundary."""

    bypass_keys = (
        "is_superuser",
        "bypass_rls",
        "session_is_superuser",
        "session_bypass_rls",
        "controller_group_is_superuser",
        "controller_group_bypass_rls",
        "api_group_is_superuser",
        "api_group_bypass_rls",
    )
    if any(authority.get(key) is True for key in bypass_keys):
        raise RuntimeError("public_database_role_bypasses_rls")
    if (
        authority.get("inherits_role_privileges") is False
        or authority.get("session_inherits_role_privileges") is False
    ):
        raise RuntimeError("public_database_role_must_inherit")
    if (
        authority.get("controller_group_can_login") is True
        or authority.get("api_group_can_login") is True
    ):
        raise RuntimeError("public_database_group_role_must_not_login")
    is_controller = authority.get("is_controller") is True
    is_api = authority.get("is_api") is True
    if controller and not is_controller:
        raise RuntimeError("public_controller_database_role_required")
    if controller and is_api:
        raise RuntimeError("public_controller_database_role_must_not_be_api")
    if not controller and is_controller:
        raise RuntimeError("public_api_database_role_must_not_be_controller")
    if not controller and not is_api:
        raise RuntimeError("public_api_database_role_required")


async def require_public_controller_database_authority(
    engine: AsyncEngine = sql_engine,
) -> None:
    """Require the dedicated cross-tenant queue role for a controller process."""

    local_development = _local_development_environment()
    if not local_development and (
        os.getenv("PUBLIC_DATABASE_PROCESS_ROLE") or ""
    ).strip().lower() != "controller":
        raise RuntimeError("public_controller_database_process_role_required")
    if engine.dialect.name != "postgresql":
        if local_development and engine.dialect.name == "sqlite":
            return
        raise RuntimeError("public_controller_postgresql_required")
    validate_public_database_authority(
        await _postgres_role_authority(engine),
        controller=True,
    )


async def require_public_api_database_authority(
    engine: AsyncEngine = sql_engine,
) -> None:
    """Ensure a remote public API cannot inherit controller or BYPASSRLS power."""

    local_development = _local_development_environment()
    public_runtime = bool(
        (os.getenv("PUBLIC_DATABASE_PROCESS_ROLE") or "").strip()
        or (os.getenv("PPT_PUBLIC_OIDC_ISSUER") or "").strip()
        or (os.getenv("PUBLIC_V2_INTEGRATION_PROFILE") or "").strip()
        == "postgresql-strict"
    )
    if local_development and (
        engine.dialect.name == "sqlite" or not public_runtime
    ):
        return
    if not public_runtime:
        return
    if (os.getenv("PUBLIC_DATABASE_PROCESS_ROLE") or "").strip().lower() != "api":
        raise RuntimeError("public_api_database_process_role_required")
    if engine.dialect.name != "postgresql":
        raise RuntimeError("public_api_postgresql_required")
    validate_public_database_authority(
        await _postgres_role_authority(engine),
        controller=False,
    )


_STRICT_OWNER_MODELS = (
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
    AuthoredCanonicalRevision,
    AuthoredSlideEditorEvent,
    PublicOperationAdmission,
    PublicArtifact,
    PublicExportIdentity,
)

# These public authority rows carry both dimensions.  Owner scoping alone is
# insufficient because an external issuer may legitimately reuse the same
# subject UUID in more than one tenant.
_STRICT_TENANT_MODELS = (
    PublicOperationAdmission,
    PublicArtifact,
    PublicExportIdentity,
)


@event.listens_for(Session, "after_begin")
def _set_postgres_transaction_principal(_session, _transaction, connection) -> None:
    """Expose request identity to PostgreSQL RLS without widening DB authority."""

    if connection.dialect.name != "postgresql":
        return
    owner_id = get_current_owner_id()
    tenant_id = get_current_tenant_id()
    connection.execute(text("SET LOCAL row_security = on"))
    connection.execute(
        text(
            "SELECT "
            "set_config('presenton.owner_id', :owner_id, true), "
            "set_config('presenton.tenant_id', :tenant_id, true)"
        ),
        {
            "owner_id": str(owner_id) if owner_id is not None else "",
            "tenant_id": tenant_id or "",
        },
    )


@event.listens_for(Session, "do_orm_execute")
def _scope_owned_selects(execute_state) -> None:
    """Apply independent owner and tenant criteria to request ORM SELECTs."""
    owner_id = get_current_owner_id()
    tenant_id = get_current_tenant_id()
    if not execute_state.is_select:
        return

    statement = execute_state.statement
    if owner_id is not None and not execute_state.execution_options.get(
        "skip_owner_scope"
    ):
        catalog_default_id = get_template_v2_default_template_id()
        for model in _STRICT_OWNER_MODELS:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda row: row.owner_id == owner_id,
                    include_aliases=True,
                )
            )
        statement = statement.options(
            with_loader_criteria(
                TemplateV2,
                lambda row: or_(
                    row.owner_id == owner_id,
                    (row.owner_id.is_(None) & row.is_default.is_(True)),
                    (
                        row.owner_id.is_(None)
                        & (row.id == catalog_default_id)
                    ),
                ),
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


@event.listens_for(Session, "before_flush")
def _stamp_new_owned_rows(session, _flush_context, _instances) -> None:
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
    for instance in session.dirty:
        if not isinstance(instance, PresentationModel):
            continue
        if instance.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED:
            # A tombstone is not a deck. Deletion deliberately redacts the
            # theme and the mode, which are two of the identity fields below,
            # so re-deriving a profile here asked the resolver to describe a
            # row that has just been emptied on purpose -- and it refused,
            # which made deleting an authored presentation raise.
            continue
        state = inspect(instance)
        if any(
            state.attrs[field].history.has_changes()
            for field in profile_identity_fields
        ):
            stamp_presentation_profile(instance, allow_driver_transition=True)

    owner_id = get_current_owner_id()
    tenant_id = get_current_tenant_id()
    if owner_id is None and tenant_id is None:
        return
    owner_models = _STRICT_OWNER_MODELS + (TemplateV2,)
    for instance in session.new:
        if owner_id is not None and isinstance(instance, owner_models):
            instance.owner_id = owner_id
        if tenant_id is not None and isinstance(instance, _STRICT_TENANT_MODELS):
            supplied_tenant = getattr(instance, "tenant_id", None)
            if supplied_tenant not in {None, "", tenant_id}:
                raise RuntimeError("tenant_scope_write_mismatch")
            instance.tenant_id = tenant_id


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


# Create Database and Tables
async def create_db_and_tables():
    should_run_alembic = get_migrate_database_on_startup_env() in ["true", "True"]
    if not should_run_alembic:
        async with sql_engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: SQLModel.metadata.create_all(
                    sync_conn,
                    tables=[
                        PresentationModel.__table__,
                        WorkspaceConversation.__table__,
                        WorkspaceMessage.__table__,
                        WorkspaceAsset.__table__,
                        WorkspaceMessageAttachment.__table__,
                        WorkspaceOperation.__table__,
                        WorkspaceOperationEvent.__table__,
                        WorkspaceOutbox.__table__,
                        WorkspaceActionReceipt.__table__,
                        PresentationExportAttempt.__table__,
                        WorkspaceArtifact.__table__,
                        WorkspaceCleanupJournal.__table__,
                        AuthoredExactProvenance.__table__,
                        AuthoredCanonicalRevision.__table__,
                        PresentationGenerationJob.__table__,
                        AuthoredSlideCheckpoint.__table__,
                        AuthoredSlideEditorEvent.__table__,
                        PresentationVersionModel.__table__,
                        SlideModel.__table__,
                        SlideSpecArtifactBundle.__table__,
                        KeyValueSqlModel.__table__,
                        ChatHistoryMessageModel.__table__,
                        ImageAsset.__table__,
                        PresentationLayoutCodeModel.__table__,
                        TemplateCreateInfoModel.__table__,
                        TemplateModel.__table__,
                        TemplateV2.__table__,
                        TemplateV2LocalState.__table__,
                        TemplateV2Revision.__table__,
                        TemplateV2PptxImport.__table__,
                        WebhookSubscription.__table__,
                        AsyncTaskModel.__table__,
                        AsyncPresentationGenerationTaskModel.__table__,
                        OllamaPullStatus.__table__,
                        User.__table__,
                        AccountDeletionRequest.__table__,
                        AccessToken.__table__,
                        ProviderSettings.__table__,
                        ReferenceSnapshot.__table__,
                        AssetGatewayIdempotencyReceipt.__table__,
                        ProviderUsageReceipt.__table__,
                        ProviderRuntimeBudget.__table__,
                        ProviderRuntimeReservation.__table__,
                        ProviderRuntimeCircuit.__table__,
                        PublicPolicyProfile.__table__,
                        PublicQuotaCounter.__table__,
                        PublicOperationAdmission.__table__,
                        PublicProviderLogicalCall.__table__,
                        PublicArtifact.__table__,
                        PublicExportIdentity.__table__,
                        *AUTHORED_VNEXT_SQL_TABLES,
                    ],
                )
            )
            await conn.run_sync(_ensure_provider_settings_revision_column)


def _ensure_provider_settings_revision_column(connection) -> None:
    """Top up metadata-created legacy databases when Alembic is disabled."""
    inspector = inspect(connection)
    if "provider_settings" not in inspector.get_table_names():
        return
    columns = {
        column["name"]: column
        for column in inspector.get_columns("provider_settings")
    }
    if "revision" not in columns:
        connection.execute(
            text(
                "ALTER TABLE provider_settings "
                "ADD COLUMN revision BIGINT NOT NULL DEFAULT 0"
            )
        )
        return

    revision = columns["revision"]
    default = (
        str(revision.get("default"))
        .lower()
        .replace("::bigint", "")
        .strip("'\"() ")
    )
    if (
        not isinstance(revision.get("type"), BigInteger)
        or revision.get("nullable", True)
        or default != "0"
    ):
        raise RuntimeError("Provider settings revision column is incompatible")


async def dispose_engines():
    """Dispose all engine connection pools.

    Call this during application shutdown (e.g. in a FastAPI ``shutdown``
    event or lifespan context) to release every connection back to the
    database and prevent stale / leaked connections.
    """
    await sql_engine.dispose()
