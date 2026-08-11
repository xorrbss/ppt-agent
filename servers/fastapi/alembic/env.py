import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Make sure all models can be imported when alembic runs standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import every SQL model so they register with SQLModel.metadata before
# autogenerate or migration execution reads it.
from models.sql.async_presentation_generation_status import (  # noqa: F401, E402
    AsyncPresentationGenerationTaskModel,
)
from models.sql.async_task import AsyncTaskModel  # noqa: F401, E402
from models.sql.authored_slide_checkpoint import (  # noqa: F401, E402
    AuthoredSlideCheckpoint,
)
from models.sql.authored_exact_provenance import (  # noqa: F401, E402
    AuthoredExactProvenance,
)
from models.sql.chat_history_message import ChatHistoryMessageModel  # noqa: F401, E402
from models.sql.image_asset import ImageAsset  # noqa: F401, E402
from models.sql.key_value import KeyValueSqlModel  # noqa: F401, E402
from models.sql.ollama_pull_status import OllamaPullStatus  # noqa: F401, E402
from models.sql.presentation import PresentationModel  # noqa: F401, E402
from models.sql.presentation_generation_job import (  # noqa: F401, E402
    PresentationGenerationJob,
)
from models.sql.presentation_version import (  # noqa: F401, E402
    PresentationVersionModel,
)
from models.sql.presentation_layout_code import (  # noqa: F401, E402
    PresentationLayoutCodeModel,
)
from models.sql.slide import SlideModel  # noqa: F401, E402
from models.sql.slidespec_artifact_bundle import (  # noqa: F401, E402
    SlideSpecArtifactBundle,
)
from models.sql.slidespec_publication_attempt import (  # noqa: F401, E402
    SlideSpecPublicationAttempt,
)
from models.sql.template import TemplateModel  # noqa: F401, E402
from models.sql.template_v2 import TemplateV2  # noqa: F401, E402
from models.sql.template_v2_local_state import (  # noqa: F401, E402
    TemplateV2LocalState,
)
from models.sql.template_v2_pptx_import import (  # noqa: F401, E402
    TemplateV2PptxImport,
)
from models.sql.template_v2_revision import TemplateV2Revision  # noqa: F401, E402
from models.sql.template_create_info import TemplateCreateInfoModel  # noqa: F401, E402
from models.sql.webhook_subscription import WebhookSubscription  # noqa: F401, E402
from models.sql.workspace import (  # noqa: F401, E402
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
from models.sql.user import User  # noqa: F401, E402
from models.sql.access_token import AccessToken  # noqa: F401, E402
from models.sql.provider_settings import ProviderSettings  # noqa: F401, E402
from models.sql.reference_snapshot import ReferenceSnapshot  # noqa: F401, E402
from models.sql.asset_gateway_idempotency_receipt import (  # noqa: F401, E402
    AssetGatewayIdempotencyReceipt,
)
from models.sql.presentation_export_attempt import (  # noqa: F401, E402
    PresentationExportAttempt,
)

alembic_config = context.config

if alembic_config.config_file_name is not None:
    # Alembic runs in-process during startup and integration tests. The
    # ``fileConfig`` default disables every already-imported non-Alembic logger,
    # which makes application telemetry disappear after a migration.
    fileConfig(
        alembic_config.config_file_name,
        disable_existing_loggers=False,
    )

target_metadata = SQLModel.metadata

_ALEMBIC_OWNED_FOREIGN_KEYS = {
    (
        "presentations",
        "fk_presentations_owner_id_user",
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    (
        "slides",
        "fk_slides_owner_id_user",
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    (
        "async_tasks",
        "fk_async_tasks_owner_id_user",
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    (
        "async_presentation_generation_tasks",
        "fk_async_presentation_generation_tasks_owner_id_user",
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    (
        "chat_history_messages",
        "fk_chat_history_messages_owner_id_user",
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    (
        "template_v2",
        "fk_template_v2_owner_id_user",
        ("owner_id",),
        "user",
        ("id",),
        "CASCADE",
    ),
    (
        "imageasset",
        "fk_imageasset_canonical_asset_id",
        ("canonical_asset_id",),
        "workspace_assets",
        ("id",),
        "SET NULL",
    ),
    (
        "presentation_generation_jobs",
        "fk_presentation_generation_jobs_workspace_operation_id",
        ("workspace_operation_id",),
        "workspace_operations",
        ("id",),
        "SET NULL",
    ),
    # SQLite's Inspector omits ON DELETE for this inline ADD COLUMN FK even
    # though sqlite_master and PRAGMA retain and enforce SET NULL.
    (
        "presentation_generation_jobs",
        "fk_presentation_generation_jobs_workspace_operation_id",
        ("workspace_operation_id",),
        "workspace_operations",
        ("id",),
        "",
    ),
    (
        "presentation_generation_jobs",
        None,
        ("workspace_operation_id",),
        "workspace_operations",
        ("id",),
        "",
    ),
}

_ALEMBIC_OWNED_UNIQUE_CONSTRAINTS = {
    (
        "template_v2_pptx_imports",
        "uq_template_v2_pptx_imports_task_id",
        ("task_id",),
    ),
    # The canonical database contract is the named unique index created by
    # e1; the declarative model can only express this field as an unnamed UC.
    ("user", None, ("admin_slot",)),
}

_ALEMBIC_OWNED_INDEXES = {
    ("user", "uq_user_admin_slot", ("admin_slot",), True),
}


def _foreign_key_signature(constraint) -> tuple[object, ...]:
    elements = tuple(constraint.elements)
    return (
        constraint.table.name,
        constraint.name,
        tuple(column.name for column in constraint.columns),
        elements[0].column.table.name if elements else "",
        tuple(element.column.name for element in elements),
        str(constraint.ondelete or "").upper(),
    )


def _include_object(obj, _name, type_, reflected, compare_to) -> bool:
    if type_ == "foreign_key_constraint" and (
        _foreign_key_signature(obj) in _ALEMBIC_OWNED_FOREIGN_KEYS
    ):
        return False
    if type_ == "unique_constraint" and (
        obj.table.name,
        obj.name,
        tuple(column.name for column in obj.columns),
    ) in _ALEMBIC_OWNED_UNIQUE_CONSTRAINTS:
        return False
    if type_ == "index" and (
        obj.table.name,
        obj.name,
        tuple(column.name for column in obj.columns),
        bool(obj.unique),
    ) in _ALEMBIC_OWNED_INDEXES:
        return False
    return True

# alembic.ini sets this so Config validates; treat it as "unset" for URL resolution.
_CLI_PLACEHOLDER_DB_URL = "sqlite:///placeholder"


def _get_url() -> str:
    """
    Prefer the URL injected by migrations.py via config.set_main_option,
    falling back to the DATABASE_URL environment variable or a local SQLite DB.
    """
    configured = alembic_config.get_main_option("sqlalchemy.url")
    if configured and configured != _CLI_PLACEHOLDER_DB_URL:
        return configured

    from utils.db_utils import get_database_url_and_connect_args, to_sync_sqlalchemy_url

    url, _ = get_database_url_and_connect_args()
    return to_sync_sqlalchemy_url(url)


def run_migrations_offline() -> None:
    """Generate SQL script without connecting to the database."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live database."""
    configuration = dict(alembic_config.get_section(alembic_config.config_ini_section) or {})
    configuration["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
