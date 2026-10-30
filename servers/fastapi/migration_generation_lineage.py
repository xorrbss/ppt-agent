"""Frozen inventory and projection for revision e4b7c9d2a1f0."""

from collections import Counter
import hashlib
import re
import time
import uuid

import sqlalchemy as sa

from models.workspace_generation_lineage import previous_attempt_from_raw_payload


_OWNER_POLICY = re.compile(
    r"\(*\s*(?:public\.)?presenton_owner_row_visible\(owner_id,\s*false\)\s*\)*"
)


def assert_global_migration_visibility(connection) -> None:
    """Prove the existing migration role and policy can see the global table."""
    identity = connection.execute(sa.text(
        "SELECT current_user, current_setting('presenton.owner_id', true), "
        "current_setting('presenton.request_scope', true), "
        "current_setting('row_security'), current_setting('transaction_read_only'), "
        "has_table_privilege(current_user, 'public.workspace_operations', 'SELECT'), "
        "has_table_privilege(current_user, 'public.workspace_operations', 'UPDATE'), "
        "has_schema_privilege(current_user, 'public', 'CREATE')"
    )).one()
    if (
        identity[0] in {"presenton_public_api", "presenton_public_controller"}
        or identity[1] not in {None, ""}
        or identity[2] not in {None, ""}
        or identity[3] != "on"
        or identity[4] != "off"
        or identity[5] is not True
        or identity[6] is not True
        or identity[7] is not True
    ):
        raise RuntimeError("generation_lineage_migration_principal_invalid")

    role = connection.execute(sa.text(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )).one_or_none()
    if role is None or not all(isinstance(flag, bool) for flag in role):
        raise RuntimeError("generation_lineage_migration_role_invalid")

    table = connection.execute(sa.text(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = 'workspace_operations'"
    )).one_or_none()
    if table is None or tuple(table) != (True, True):
        raise RuntimeError("generation_lineage_migration_rls_invalid")

    policies = connection.execute(sa.text(
        "SELECT policyname, permissive, roles, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' "
        "AND tablename = 'workspace_operations'"
    )).mappings().all()
    by_name = {row["policyname"]: row for row in policies}
    request = by_name.get("presenton_owner_scope")
    controller = by_name.get("presenton_owner_controller_authority")
    if len(policies) != 2 or request is None or controller is None:
        raise RuntimeError("generation_lineage_migration_policy_invalid")
    if (
        request["permissive"] != "PERMISSIVE"
        or request["cmd"] != "ALL"
        or set(request["roles"]) != {"public"}
        or not _OWNER_POLICY.fullmatch(request["qual"] or "")
        or not _OWNER_POLICY.fullmatch(request["with_check"] or "")
        or controller["permissive"] != "PERMISSIVE"
        or controller["cmd"] != "ALL"
        or set(controller["roles"]) != {"presenton_public_controller"}
        or controller["qual"] != "true"
        or controller["with_check"] != "true"
    ):
        raise RuntimeError("generation_lineage_migration_policy_invalid")

    function = connection.execute(sa.text(
        "SELECT p.prosecdef, p.provolatile, p.prosrc, p.proconfig "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = 'presenton_owner_row_visible' "
        "AND pg_get_function_identity_arguments(p.oid) = 'row_owner_id uuid, allow_shared boolean'"
    )).one_or_none()
    if function is None:
        raise RuntimeError("generation_lineage_migration_visibility_function_invalid")
    body = function[2]
    if (
        function[0] is not True or function[1] != "s"
        or function[3] is None or "search_path=pg_catalog" not in function[3]
        or hashlib.sha256(body.encode()).hexdigest()
        != "43b491824644f644501dcef02c75a7d4a73fbf8d075462c7b455efede6b71385"
    ):
        raise RuntimeError("generation_lineage_migration_visibility_function_invalid")
    # The known bootstrap owner has rolsuper/rolbypassrls. If a different
    # authorized migration owner has neither flag, the exact policy/function
    # contract above is the visibility proof for an unscoped transaction.


def _scope_counts(connection):
    rows = connection.execute(sa.text(
        "SELECT owner_scope, owner_id, count(*) AS rows, "
        "count(previous_presentation_id) AS projected "
        "FROM workspace_operations WHERE kind = 'presentation.generate' "
        "GROUP BY owner_scope, owner_id"
    )).all()
    return {(scope, owner): (int(count), int(projected))
            for scope, owner, count, projected in rows}


def backfill_global_generation_lineage(connection) -> tuple[int, int, int, int]:
    """Scan every owner/scope pair, including anonymous, under the DDL lock."""
    # A historical request_payload can be hundreds of MiB. Keep the metadata
    # page small and materialize at most one raw JSON value in Python at once.
    max_raw_payload_bytes = 896 * 1024 * 1024
    deadline = time.monotonic() + 300
    before = _scope_counts(connection)
    expected = sum(count for count, _ in before.values())
    observed: Counter = Counter()
    projected: Counter = Counter()
    last_id = None
    while True:
        if time.monotonic() > deadline:
            raise RuntimeError("generation_lineage_migration_backfill_timeout")
        statement = (
            "SELECT id, owner_scope, owner_id "
            "FROM workspace_operations WHERE kind = 'presentation.generate' "
        )
        if last_id is not None:
            statement += "AND id > :last_id "
        statement += "ORDER BY id LIMIT 500"
        query = sa.text(statement)
        if last_id is not None:
            query = query.bindparams(sa.bindparam("last_id", type_=sa.Uuid()))
        rows = connection.execute(query, {"last_id": last_id} if last_id else {}).all()
        if time.monotonic() > deadline:
            raise RuntimeError("generation_lineage_migration_backfill_timeout")
        if not rows:
            break
        for operation_id, owner_scope, owner_id in rows:
            if time.monotonic() > deadline:
                raise RuntimeError("generation_lineage_migration_backfill_timeout")
            key = (owner_scope, owner_id)
            observed[key] += 1
            raw_bytes = connection.execute(sa.text(
                "SELECT octet_length(CAST(request_payload AS TEXT)) "
                "FROM workspace_operations WHERE id = :operation_id"
            ).bindparams(sa.bindparam("operation_id", type_=sa.Uuid())),
                {"operation_id": uuid.UUID(str(operation_id))}).scalar_one()
            if not isinstance(raw_bytes, int) or not 0 < raw_bytes <= max_raw_payload_bytes:
                raise RuntimeError("generation_lineage_migration_payload_over_budget")
            if time.monotonic() > deadline:
                raise RuntimeError("generation_lineage_migration_backfill_timeout")
            raw_result = connection.execute(sa.text(
                "SELECT CAST(request_payload AS TEXT) "
                "FROM workspace_operations WHERE id = :operation_id"
            ).bindparams(sa.bindparam("operation_id", type_=sa.Uuid())),
                {"operation_id": uuid.UUID(str(operation_id))})
            try:
                raw_payload = raw_result.scalar_one()
            finally:
                raw_result.close()
                del raw_result
            if time.monotonic() > deadline:
                del raw_payload
                raise RuntimeError("generation_lineage_migration_backfill_timeout")
            # This matches the old raw LIKE prefilter plus decoded exact key;
            # unrelated UUIDs and escaped NULs do not alter the result.
            try:
                parent = previous_attempt_from_raw_payload("presentation.generate", raw_payload)
            finally:
                del raw_payload
            if time.monotonic() > deadline:
                raise RuntimeError("generation_lineage_migration_backfill_timeout")
            if parent is not None:
                changed = connection.execute(sa.text(
                    "UPDATE workspace_operations SET previous_presentation_id = :parent "
                    "WHERE id = :operation_id AND kind = 'presentation.generate' "
                    "AND previous_presentation_id IS NULL "
                    "RETURNING previous_presentation_id"
                ).bindparams(
                    sa.bindparam("parent", type_=sa.Uuid()),
                    sa.bindparam("operation_id", type_=sa.Uuid()),
                ), {"parent": parent, "operation_id": uuid.UUID(str(operation_id))})
                stored_parent = changed.scalar_one_or_none()
                if stored_parent is None or uuid.UUID(str(stored_parent)) != parent:
                    raise RuntimeError("generation_lineage_migration_projection_changed")
                projected[key] += 1
            last_id = uuid.UUID(str(operation_id))
    if (
        sum(observed.values()) != expected
        or {key: observed[key] for key in before}
        != {key: count for key, (count, _) in before.items()}
    ):
        raise RuntimeError("generation_lineage_migration_inventory_mismatch")
    if time.monotonic() > deadline:
        raise RuntimeError("generation_lineage_migration_backfill_timeout")
    after = _scope_counts(connection)
    if time.monotonic() > deadline:
        raise RuntimeError("generation_lineage_migration_backfill_timeout")
    if after != {key: (observed[key], projected[key]) for key in before}:
        raise RuntimeError("generation_lineage_migration_projection_mismatch")
    anonymous = sum(count for (_, owner), (count, _) in before.items() if owner is None)
    return expected, sum(projected.values()), len(before), anonymous


def install_writer_gate(connection) -> None:
    """Old binaries fail closed when writing a lineage-bearing operation."""
    connection.execute(sa.text("""
        CREATE FUNCTION presenton_generation_lineage_writer_gate()
        RETURNS trigger LANGUAGE plpgsql AS $presenton_generation_lineage_writer_gate$
        DECLARE needs_stamp boolean := false;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                needs_stamp := NEW.kind = 'presentation.generate';
            ELSIF NEW.kind = 'presentation.generate'
               OR OLD.kind = 'presentation.generate' THEN
                needs_stamp := NEW.kind IS DISTINCT FROM OLD.kind
                    OR NEW.request_payload::text IS DISTINCT FROM OLD.request_payload::text
                    OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
                    OR NEW.owner_scope IS DISTINCT FROM OLD.owner_scope
                    OR NEW.previous_presentation_id IS DISTINCT FROM OLD.previous_presentation_id;
            END IF;
            IF needs_stamp THEN
                IF current_setting('presenton.generation_lineage_writer_revision', true)
                   IS DISTINCT FROM 'e4b7c9d2a1f0' THEN
                    RAISE EXCEPTION 'generation_lineage_writer_revision_required'
                        USING ERRCODE = '55000';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $presenton_generation_lineage_writer_gate$
    """))
    connection.execute(sa.text("""
        CREATE TRIGGER trg_workspace_generation_lineage_writer_gate
        BEFORE INSERT OR UPDATE OF kind, request_payload, owner_id, owner_scope,
            previous_presentation_id ON workspace_operations
        FOR EACH ROW EXECUTE FUNCTION presenton_generation_lineage_writer_gate()
    """))


def grant_readiness_only(connection) -> None:
    """Keep global inventory counts private to the migration owner."""
    connection.execute(sa.text(
        "REVOKE ALL ON TABLE workspace_generation_lineage_state FROM PUBLIC"
    ))
    roles = (
        "presenton_public_api",
        "presenton_public_api_login",
        "presenton_public_controller",
        "presenton_public_controller_login",
    )
    present_roles = set(connection.execute(sa.text(
        "SELECT rolname FROM pg_roles WHERE rolname IN "
        "('presenton_public_api', 'presenton_public_api_login', "
        "'presenton_public_controller', 'presenton_public_controller_login')"
    )).scalars())
    if not {"presenton_public_api", "presenton_public_controller"} <= present_roles:
        raise RuntimeError("generation_lineage_migration_runtime_roles_missing")
    for role in roles:
        if role in present_roles:
            connection.execute(sa.text(
                "REVOKE ALL ON TABLE workspace_generation_lineage_state FROM " + role
            ))
    connection.execute(sa.text(
        "GRANT SELECT (id, complete) ON TABLE workspace_generation_lineage_state "
        "TO presenton_public_api"
    ))
    rights_query = sa.text(
        "SELECT has_table_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'SELECT'), "
        "has_column_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'id', 'SELECT'), "
        "has_column_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'complete', 'SELECT'), "
        "has_column_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'generation_rows', 'SELECT'), "
        "has_column_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'projected_rows', 'SELECT'), "
        "has_column_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'owner_scope_count', 'SELECT'), "
        "has_column_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'anonymous_rows', 'SELECT'), "
        "has_column_privilege(CAST(:role AS name), "
        "'public.workspace_generation_lineage_state', 'completed_at', 'SELECT')"
    )
    api_rights = (False, True, True, False, False, False, False, False)
    controller_rights = (False,) * 8
    for role in present_roles:
        rights = tuple(connection.execute(rights_query, {"role": role}).one())
        expected = api_rights if role.startswith("presenton_public_api") else controller_rights
        if rights != expected:
            raise RuntimeError("generation_lineage_migration_readiness_privileges_invalid")
