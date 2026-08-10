from collections.abc import AsyncGenerator
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
from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.image_asset import ImageAsset
from models.sql.key_value import KeyValueSqlModel
from models.sql.ollama_pull_status import OllamaPullStatus
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.presentation import PresentationModel
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
from models.sql.access_token import AccessToken
from models.sql.provider_settings import ProviderSettings
from templates.v2.catalog import get_template_v2_default_template_id
from utils.owner_context import get_current_owner_id
from utils.get_env import get_migrate_database_on_startup_env
from utils.db_utils import get_database_url_and_connect_args, get_pool_kwargs


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
)


@event.listens_for(Session, "do_orm_execute")
def _scope_owned_selects(execute_state) -> None:
    """Apply tenant criteria to every ORM SELECT performed during a request."""
    owner_id = get_current_owner_id()
    if (
        owner_id is None
        or not execute_state.is_select
        or execute_state.execution_options.get("skip_owner_scope")
    ):
        return

    statement = execute_state.statement
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
    execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def _stamp_new_owned_rows(session, _flush_context, _instances) -> None:
    owner_id = get_current_owner_id()
    if owner_id is None:
        return
    owner_models = _STRICT_OWNER_MODELS + (TemplateV2,)
    for instance in session.new:
        if isinstance(instance, owner_models):
            instance.owner_id = owner_id


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
                        WorkspaceArtifact.__table__,
                        WorkspaceCleanupJournal.__table__,
                        AuthoredExactProvenance.__table__,
                        PresentationGenerationJob.__table__,
                        AuthoredSlideCheckpoint.__table__,
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
                        AccessToken.__table__,
                        ProviderSettings.__table__,
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
