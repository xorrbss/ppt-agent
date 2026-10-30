"""Historical lineage projection includes every owner scope and raw JSON form."""

import json
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
