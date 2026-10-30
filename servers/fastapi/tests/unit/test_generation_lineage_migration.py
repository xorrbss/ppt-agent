"""Historical lineage projection includes every owner scope and raw JSON form."""

import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine, event, text

from migration_generation_lineage import backfill_global_generation_lineage


def test_global_generation_lineage_inventory_and_projection():
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def _sqlite_octet_length(dbapi_connection, _record):
        dbapi_connection.create_function(
            "octet_length", 1, lambda value: len(value.encode("utf-8"))
        )

    parent = uuid.uuid4()
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    canonical = str(parent)
    escaped = "".join(f"\\u{ord(char):04x}" for char in canonical)
    rows = [
        (uuid.uuid4(), str(owner_a), str(owner_a),
         json.dumps({"previous_presentation_id": canonical, "prompt": "\x00"}),
         "presentation.generate"),
        (uuid.uuid4(), str(owner_b), str(owner_b),
         '{"previous_presentation_id":"' + escaped + '","prompt":"other"}',
         "presentation.generate"),
        (uuid.uuid4(), str(owner_b), str(owner_b),
         '{"previous_presentation_id":"' + escaped + '","other":"' + canonical + '"}',
         "presentation.generate"),
        (uuid.uuid4(), "anonymous", None,
         json.dumps({"previous_presentation_id": canonical}),
         "presentation.generate"),
        (uuid.uuid4(), "anonymous", None,
         json.dumps({"previous_presentation_id": "invalid", "other": canonical}),
         "presentation.generate"),
        (uuid.uuid4(), str(owner_a), str(owner_a),
         json.dumps({"previous_presentation_id": canonical}), "presentation.edit"),
    ]
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE workspace_operations (id CHAR(32) PRIMARY KEY, "
            "owner_scope TEXT NOT NULL, owner_id CHAR(32), kind TEXT NOT NULL, "
            "request_payload TEXT NOT NULL, previous_presentation_id CHAR(32))"
        ))
        for operation_id, scope, owner, payload, kind in rows:
            connection.execute(text(
                "INSERT INTO workspace_operations "
                "(id, owner_scope, owner_id, kind, request_payload) "
                "VALUES (:id, :scope, :owner, :kind, :payload)"
            ), {"id": operation_id.hex, "scope": scope, "owner": owner,
                "kind": kind, "payload": payload})
        assert backfill_global_generation_lineage(connection) == (5, 3, 3, 2)
        stored = connection.execute(text(
            "SELECT id, previous_presentation_id FROM workspace_operations"
        )).all()
        by_id = {operation_id: projected for operation_id, projected in stored}
        assert all(by_id[rows[i][0].hex] == parent.hex for i in (0, 2, 3))
        assert all(by_id[rows[i][0].hex] is None for i in (1, 4, 5))


def test_upgrade_builds_parent_index_before_deferred_backfill_events(monkeypatch):
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/e4b7c9d2a1f0_index_generation_attempt_parent.py"
    )
    spec = importlib.util.spec_from_file_location("generation_lineage_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    events = []

    class DeferredTriggerConnection:
        dialect = SimpleNamespace(name="postgresql")
        pending_trigger_events = False

        def execute(self, _statement):
            return None

    connection = DeferredTriggerConnection()

    class Operations:
        def get_bind(self):
            return connection

        def add_column(self, *_args, **_kwargs):
            events.append("column")

        def create_index(self, *_args, **_kwargs):
            if connection.pending_trigger_events:
                raise RuntimeError("cannot create index with pending trigger events")
            events.append("index")

        def create_table(self, *_args, **_kwargs):
            events.append("marker_table")

        def execute(self, *_args, **_kwargs):
            events.append("marker_row")

    def backfill(_connection):
        connection.pending_trigger_events = True
        events.append("backfill")
        return 5, 3, 3, 2

    monkeypatch.setattr(migration, "op", Operations())
    monkeypatch.setattr(migration, "assert_global_migration_visibility", lambda _connection: None)
    monkeypatch.setattr(migration, "backfill_global_generation_lineage", backfill)
    monkeypatch.setattr(migration, "install_writer_gate", lambda _connection: events.append("gate"))
    monkeypatch.setattr(migration, "grant_readiness_only", lambda _connection: events.append("acl"))

    migration.upgrade()
    assert events == ["column", "index", "backfill", "marker_table", "marker_row", "gate", "acl"]
