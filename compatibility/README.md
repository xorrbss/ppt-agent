# Upstream compatibility control

This directory records the U0 compatibility contract against upstream commit
`57b194b234b42c8b28f8a507a30322de200e3e83`.

- `upstream-compatibility.json` pins application/export/frontend versions, the
  11 Template V2 renderer discriminators, the lossless upstream payload
  boundary, and key `/api/v1` endpoints.
- `migration-translation-ledger.json` records the upstream and local Alembic
  chains after their common revision, including explicit non-translations.
- `protected-local-patches.json` identifies authored-hybrid, Windows runtime
  sync, and export security behavior that must survive upstream rebases.
- `upstream-test-contracts.json` records each reviewed upstream default-template
  and generic async-task test, including machine-readable exclusion reasons and
  the non-conflicting local regression contracts retained in its place.

Run `node scripts/verify-upstream-compatibility.mjs` before and after an
upstream merge. The verifier is dependency-free and reports all detected drift
in one pass. Update a contract only when the related upstream or local design
decision is reviewed; do not silence drift by changing expected values alone.

## Database compatibility

- SQLite is the default runtime and unit-test database. Presentation deletion
  rolls back and returns a retryable `503` with `Retry-After: 1` for SQLite
  `BUSY`/`LOCKED`; permanent ownership or foreign-key conflicts remain `409`.
- PostgreSQL is verified by a dedicated live-database CI gate. It runs the
  official Alembic lineage from an empty database and covers Template V2
  upgrade/downgrade/re-upgrade, legacy and populated-data preservation,
  foreign keys, unique constraints, indexes, and child-first delete safety.
- MySQL and MariaDB are not supported by this compatibility contract. Upstream
  URL adapters and defensive dialect branches are retained, but there is no
  documented server/version/CI contract and a clean Alembic SQL compilation
  fails on unbounded `String` columns (`VARCHAR requires a length`).

Run the PostgreSQL gate locally only against a disposable database whose name
ends in `test` or `tests`:

```bash
cd servers/fastapi
export PPT_AGENT_POSTGRES_TEST_URL='postgresql+psycopg://user:password@127.0.0.1:5432/presenton_test'
uv run pytest -q --no-header tests/integration/test_postgresql_template_v2_migrations.py
```

Without the URL, local collection skips this destructive integration test with
an explicit reason. CI also sets `PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1`, so
the dedicated job fails instead of skipping if its service URL is missing.
