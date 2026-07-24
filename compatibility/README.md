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
- `upstream-intake-policy.json` controls the separate, read-only observation of
  official `presenton/presenton:main`, including the pinned commit metadata,
  review categories, and contract-risk escalation rules.

The verifier derives renderer, endpoint, and reviewed-test totals from these
registries instead of pinning duplicate numeric counts. Baseline SHA,
repository, commit timestamp (normalized across time zones), and subject must
agree between the compatibility manifest and intake policy. Protected files may
also declare `forbiddenContains` anchors for negative invariants such as
loopback exposure or insecure Electron web preferences.

The two commands deliberately have different responsibilities:

- `node scripts/verify-upstream-compatibility.mjs` verifies the checked-out
  pinned local contract, migration ledger, test dispositions, and protected
  source anchors. It does not access the network.
- `node scripts/intake-upstream-main.mjs --fail-on-risk` resolves the official
  remote `main` ref to an exact SHA, verifies that commit's metadata, and, only
  when the SHA differs, classifies GitHub compare evidence. It does not modify
  the checkout, fetch a Git remote, merge, cherry-pick, download an exporter,
  transplant code, or update any compatibility registry.
  It never consults a local named Git remote, so a fork without an `upstream`
  remote is not interpreted as upstream movement.

Use a fixture for a deterministic offline run:

```bash
node scripts/intake-upstream-main.mjs \
  --fixture scripts/fixtures/upstream-intake/changed-contract-risk.json \
  --output upstream-intake.md \
  --json --fail-on-risk
```

The intake status/exit contract is:

| Status | Meaning | Default exit | With `--fail-on-risk` |
| --- | --- | ---: | ---: |
| `unchanged` | SHA and pinned metadata match | `0` | `0` |
| `change-detected`, `info`/`review` | reviewable delta, no configured contract risk | `0` | `0` |
| `change-detected`, `contract-risk` | backend v2, destructive API/migration/schema evidence, exporter version, protected-path overlap, or incomplete/non-forward compare | `0` | `2` |
| `intake-error` | network, timeout, rate limit, missing ref, invalid response/config, ref race, or baseline metadata failure | `1` | `1` |

An intake error always records `changeDetected: false`; it is never reported as
an upstream change. A `/api/v2` string in experimental docs or a Next
proxy/client file is reported separately from an actual FastAPI backend route.
A protected-path overlap means manual reconciliation is required, not that the
local patch is already broken. The pinned verifier and behavioral tests make
that determination.

When official `main` changes, review the report and scope a manual, minimal
port. Recompute API endpoints, the complete Alembic revision graph and heads,
Template V2 discriminators/schema, `presentationExportVersion`, export
strategy boundaries, and protected invariants. Only after review should the
baseline SHA/metadata and all affected ledger, protected-patch, and test
disposition entries be changed together. Never silence a warning by updating
only an expected SHA, and never use wholesale merge or cherry-pick as the
baseline-update procedure.

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
