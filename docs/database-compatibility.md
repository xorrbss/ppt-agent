# Database compatibility

This is the supported-database contract for the FastAPI backend. It moved here
from `compatibility/README.md` when the upstream-tracking ledgers in that
directory were removed (2026-10-22). Statements that described the retired
Template V2 import queue and its canary were dropped rather than carried over,
because the code they described no longer exists.

## SQLite

- SQLite is the default runtime and unit-test database. Presentation deletion
  rolls back and returns a retryable `503` with `Retry-After: 1` for SQLite
  `BUSY`/`LOCKED`; permanent ownership or foreign-key conflicts remain `409`.
- SQLite serializes writers, so concurrent writes contend. Measured 2026-07-25
  on Windows with 12 concurrent workers looping create/read/delete for 45s,
  three paired rounds: throughput ~88 ops/s and about **0.5% of operations
  fail**. Deletion surfaces this as the documented retryable `503`; creation has
  no equivalent guard, so the same `database is locked` reaches the ASGI layer
  as an opaque `500` (1–6 per round). This is a known SQLite concurrency limit
  and not a supported multi-writer configuration — use PostgreSQL where
  concurrent writers are expected.
- SQLite remains supported for the default `local`, `development`, and `test`
  deployment tiers.

## PostgreSQL

- PostgreSQL compatibility is verified by the
  `servers/fastapi/tests/integration/test_postgresql_*` suite (owner row-level
  security, provider durable runtime, provider settings sync, workspace, public
  tenant security, async-task read facade, authored cluster quota). Run it
  against a disposable database whose name ends in `test` or `tests`, e.g.
  `uv run pytest -q --no-header tests/integration/test_postgresql_workspace.py`
  with `PPT_AGENT_POSTGRES_TEST_URL` set.
- Without `PPT_AGENT_POSTGRES_TEST_URL`, local collection skips these
  destructive integration tests with an explicit reason. Setting
  `PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1` makes a missing URL fail instead of
  skip; use it in any gate that is meant to prove PostgreSQL compatibility.
- No Bitbucket pipeline runs this suite today. The job that did
  (`postgresql-integration`, in the GitHub-only `upstream-compatibility.yml`)
  never ran for this fork and was deleted with `.github/`, so PostgreSQL
  compatibility has to be verified locally before a change that touches it.

## MySQL / MariaDB

MySQL and MariaDB are not supported by this contract. Upstream URL adapters and
defensive dialect branches are retained, but there is no documented
server/version/CI contract, and a clean Alembic SQL compilation fails on
unbounded `String` columns (`VARCHAR requires a length`).
