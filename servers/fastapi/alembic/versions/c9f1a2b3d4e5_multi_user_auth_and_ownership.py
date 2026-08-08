"""multi user auth and ownership

Revision ID: c9f1a2b3d4e5
Revises: b8e2f4a7c9d1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c9f1a2b3d4e5"
down_revision: str | None = "b8e2f4a7c9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNED_TABLES = (
    "presentations",
    "slides",
    "presentation_layout_codes",
    "templates",
    "async_tasks",
    "async_presentation_generation_tasks",
    "chat_history_messages",
    "imageasset",
    "template_create_infos",
    "template_v2",
    "webhook_subscriptions",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    if "user" not in existing_tables:
        op.create_table(
            "user",
            sa.Column("username", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "auth_version",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("hashed_password", sa.String(length=1024), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("is_superuser", sa.Boolean(), nullable=False),
            sa.Column("is_verified", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_username", "user", ["username"], unique=True)

    if "access_tokens" not in existing_tables:
        op.create_table(
            "access_tokens",
            sa.Column("token", sa.String(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("token"),
        )
        op.create_index("ix_access_tokens_token", "access_tokens", ["token"])
        op.create_index("ix_access_tokens_user_id", "access_tokens", ["user_id"])

    for table in OWNED_TABLES:
        if table not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "owner_id" in columns:
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("owner_id", sa.Uuid(), nullable=True))
            batch.create_index(f"ix_{table}_owner_id", ["owner_id"])
            batch.create_foreign_key(
                f"fk_{table}_owner_id_user",
                "user",
                ["owner_id"],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    for table in reversed(OWNED_TABLES):
        if table not in existing_tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "owner_id" not in columns:
            continue
        foreign_keys = {
            foreign_key.get("name")
            for foreign_key in inspector.get_foreign_keys(table)
        }
        indexes = {
            index.get("name") for index in inspector.get_indexes(table)
        }
        with op.batch_alter_table(table) as batch:
            constraint_name = f"fk_{table}_owner_id_user"
            if constraint_name in foreign_keys:
                batch.drop_constraint(constraint_name, type_="foreignkey")
            index_name = f"ix_{table}_owner_id"
            if index_name in indexes:
                batch.drop_index(index_name)
            batch.drop_column("owner_id")
    if "access_tokens" in existing_tables:
        access_indexes = {
            index.get("name")
            for index in inspector.get_indexes("access_tokens")
        }
        if "ix_access_tokens_user_id" in access_indexes:
            op.drop_index(
                "ix_access_tokens_user_id", table_name="access_tokens"
            )
        if "ix_access_tokens_token" in access_indexes:
            op.drop_index("ix_access_tokens_token", table_name="access_tokens")
        op.drop_table("access_tokens")
    if "user" in existing_tables:
        user_indexes = {
            index.get("name") for index in inspector.get_indexes("user")
        }
        if "ix_user_username" in user_indexes:
            op.drop_index("ix_user_username", table_name="user")
        op.drop_table("user")
