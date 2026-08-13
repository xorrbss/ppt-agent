import importlib.util
from pathlib import Path

from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    FASTAPI_ROOT
    / "alembic"
    / "versions"
    / "d1a4c7e9b2f6_reconcile_post_c0_schema.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("post_c0_reconciliation", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Inspector:
    def __init__(self, sqltext: str):
        self.sqltext = sqltext

    def get_pk_constraint(self, _table: str) -> dict[str, object]:
        return {
            "name": "asset_gateway_idempotency_receipts_pkey",
            "constrained_columns": ["id"],
        }

    def get_unique_constraints(self, _table: str) -> list[dict[str, object]]:
        return [
            {
                "name": "uq_asset_gateway_receipts_owner_operation_key",
                "column_names": ["owner_scope", "operation", "key_hash"],
            }
        ]

    def get_check_constraints(self, _table: str) -> list[dict[str, object]]:
        return [
            {
                "name": "ck_asset_gateway_receipts_state",
                "sqltext": self.sqltext,
            }
        ]

    def get_foreign_keys(self, _table: str) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "constrained_columns": list(local),
                "referred_table": target,
                "referred_columns": list(remote),
                "options": {"ondelete": ondelete},
            }
            for name, (local, target, remote, ondelete) in self.foreign_keys.items()
        ]

    def get_indexes(self, _table: str) -> list[dict[str, object]]:
        return [
            {"name": name, "column_names": list(columns), "unique": False}
            for name, columns in self.indexes.items()
        ]


class _EmptyResult:
    def first(self):
        return None


class _RecordingPostgreSQLConnection:
    dialect = postgresql_dialect()

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(str(statement))
        return _EmptyResult()


def test_receipt_row_validation_quotes_postgresql_reserved_identifiers() -> None:
    migration = _migration_module()
    connection = _RecordingPostgreSQLConnection()

    migration._validate_receipt_rows(connection)

    owner_query = next(
        statement
        for statement in connection.statements
        if 'receipts."owner_id"' in statement
    )
    assert 'LEFT JOIN "user" AS target' in owner_query


def test_receipt_contract_accepts_postgresql_reflection_of_frozen_check() -> None:
    migration = _migration_module()
    inspector = _Inspector(
        "state::text = ANY (ARRAY['pending'::character varying, "
        "'complete'::character varying]::text[])"
    )
    inspector.foreign_keys = migration.RECEIPT_FOREIGN_KEYS
    inspector.indexes = migration.RECEIPT_INDEXES

    state = migration._receipt_contract_state(inspector)

    assert state["check"] == {"ck_asset_gateway_receipts_state"}


def test_receipt_contract_rejects_broadened_postgresql_check() -> None:
    migration = _migration_module()
    inspector = _Inspector(
        "state::text = ANY (ARRAY['pending'::character varying, "
        "'complete'::character varying, 'failed'::character varying]::text[])"
    )
    inspector.foreign_keys = migration.RECEIPT_FOREIGN_KEYS
    inspector.indexes = migration.RECEIPT_INDEXES

    try:
        migration._receipt_contract_state(inspector)
    except RuntimeError as error:
        assert str(error) == "Asset Gateway idempotency state check is incompatible"
    else:
        raise AssertionError("broadened state check must fail closed")
