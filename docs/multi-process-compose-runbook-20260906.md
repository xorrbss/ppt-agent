# Multi-process compose profile runbook

- Date: 2026-09-06
- Scope: the additive `multi` profile in `docker-compose.yml`; the four stock services are unchanged.
- Closes: ARCH-02 (workers outside the API process), ARCH-03 / EXT-05 (PostgreSQL cluster profile with durable cross-process provider admission).
- Not verified with Docker on the authoring machine: the wiring below is traced against `start.js`, `Dockerfile`, `api/lifespan.py`, `services/workspace_operation_worker.py`, `services/database.py`, `utils/db_utils.py`, and the three role-granting Alembic migrations. Run section 3 before trusting it.

## 1. Topology

| service | command | database login | notes |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | owner `presenton_owner` (superuser) | volume `presenton_multi_postgres_data`, `pg_isready` healthcheck |
| `migrate` | `python -m scripts.provision_public_v2_local_db_roles` | owner | one-shot; Alembic `upgrade head` under the advisory lock, then roles and grants; builds the shared image |
| `api` | `node /app/start.js` (nginx:80 + Next.js + FastAPI) | `presenton_public_api_login` in group `presenton_public_api` | `WORKSPACE_OPERATION_WORKER_MODE=external`, `WORKSPACE_OPERATION_WORKER_LAUNCH=service` (no bundled worker) |
| `worker` x N | `python -m services.workspace_operation_worker` | `presenton_public_controller_login` in group `presenton_public_controller` | `PPT_PROCESS_PROFILE=generation`; claims `presentation.generate` and `quality.review` |

Start order (compose `depends_on` conditions): `postgres` healthy -> `migrate` exit 0 -> `api` healthy -> `worker`.

Roles are provisioned by `migrate`, not by hand. The migrations `ff2a6c8e0b14`, `b5a0d3e7c9f1`, and `a0d4f7c9e2b6` create the two NOLOGIN groups if missing, `FORCE ROW LEVEL SECURITY`, and grant the controller group DML on the tables that existed when each ran; `a0d4` also limits the API group on `account_deletion_requests` to `SELECT, INSERT` and normalises any `*_login` role to `LOGIN INHERIT NOSUPERUSER NOCREATEROLE NOBYPASSRLS`. The provisioning script runs after the graph, re-grants `SELECT, INSERT, UPDATE, DELETE ON ALL TABLES` and sequence usage to both groups (so tables added by later migrations are covered), re-applies the `account_deletion_requests` narrowing, and (re)sets the two login passwords and their single group membership. At startup `services/database.py` then rejects a superuser or BYPASSRLS login, a NOINHERIT login, a group that can log in, an API login that is also a controller, and vice versa.

## 2. Prerequisites (`.env` next to `docker-compose.yml`)

| variable | required | default |
|---|---|---|
| `PRESENTON_MULTI_POSTGRES_PASSWORD` | yes (image refuses to init without it) | - |
| `PRESENTON_MULTI_API_DB_PASSWORD`, `PRESENTON_MULTI_CONTROLLER_DB_PASSWORD` | yes (`migrate` fails closed when empty) | - |
| `PRESENTON_MULTI_POSTGRES_DB` / `PRESENTON_MULTI_POSTGRES_USER` | no | `presenton` / `presenton_owner` |
| `PRESENTON_MULTI_PORT`, `PRESENTON_MULTI_WORKER_REPLICAS`, `PRESENTON_MULTI_IMAGE` | no | `5000`, `1`, `presenton-multi:local` |
| `PRESENTON_MULTI_DEPLOYMENT_TIER` | no | `development` (see section 8) |
| provider keys (`LLM`, `OPENAI_API_KEY`, ...), `AUTH_USERNAME`, `AUTH_PASSWORD` | as for the stock services | - |

Passwords are embedded in URLs: use only `[A-Za-z0-9_-]`. A `DATABASE_URL` in `.env` is ignored by this profile; `api` and `worker` set it empty and use their role DSNs.

## 3. Bring up and verify

```bash
docker compose --profile multi build
docker compose --profile multi up -d
docker compose --profile multi ps -a        # migrate: Exited (0); api, worker: healthy
docker compose --profile multi logs migrate
docker compose --profile multi exec postgres psql -U presenton_owner -d presenton -c \
  "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit FROM pg_roles WHERE rolname LIKE 'presenton_public%' ORDER BY 1"
docker compose --profile multi exec postgres psql -U presenton_owner -d presenton -c \
  "SELECT r.rolname AS role, m.rolname AS member FROM pg_auth_members am JOIN pg_roles r ON r.oid = am.roleid JOIN pg_roles m ON m.oid = am.member WHERE r.rolname LIKE 'presenton_public%'"
```

Expected: four roles, `rolsuper = f` and `rolbypassrls = f` everywhere, `rolinherit = t`, the two groups `rolcanlogin = f`, the two logins `rolcanlogin = t`; membership rows `presenton_public_api -> presenton_public_api_login` and `presenton_public_controller -> presenton_public_controller_login` only.

Startup failures are stable codes in the container log:

| code | cause |
|---|---|
| `public_role_database_url_required` / `public_generic_database_url_forbidden` | the role DSN is missing, or a generic `DATABASE_URL` leaked into `api`/`worker` |
| `public_database_role_bypasses_rls`, `public_controller_database_role_required`, `public_api_database_role_must_not_be_controller` | a login was edited by hand; re-run `docker compose --profile multi up migrate` |
| `workspace_operation_external_controller_required` | a worker without `PPT_PROCESS_PROFILE=generation` |
| `chat_first_asset_malware_scan_required` | `PRESENTON_MULTI_DEPLOYMENT_TIER=production` on the stock (scanner-less) image |

## 4. Run a generation

```bash
node scripts/ppt-agent.mjs --base http://localhost:${PRESENTON_MULTI_PORT:-5000} \
  --user "$AUTH_USERNAME" --password "$AUTH_PASSWORD" --slides 8 --export pptx --out ./out "분기 실적 보고"
docker compose --profile multi logs -f worker      # claim, heartbeat and stage lines appear here
docker compose --profile multi logs api | grep -c "workspace operation"   # the API only admits
```

The `api` log must not show slide authoring; the `worker` log must. The operation row can be inspected as the owner: `SELECT id, kind, state, claimed_by, attempt FROM workspace_operations ORDER BY created_at DESC LIMIT 5`.

## 5. Restart the API while a deck is generating

```bash
node scripts/ppt-agent.mjs --base http://localhost:5000 --user ... --password ... --slides 20 --async "긴 덱"
docker compose --profile multi restart api
docker compose --profile multi logs -f worker
```

Expected: the worker keeps heartbeating and authoring during the restart; the `workspace_operations` row stays `running` with the same `claimed_by` and `attempt`; once `api` is healthy again the timeline/SSE reconnect resumes from its cursor and the deck completes. This is the ARCH-02 contrast: with the stock `production` service the bundled worker lives in the same container and dies with it.

Restarting a worker instead (`docker compose --profile multi restart worker`) drops its lease; after `WORKSPACE_OPERATION_LEASE_SECONDS` (default 300 s) any live worker recovers the operation (`presentation.generate` has the `automatic` recovery policy) and `attempt` increments.

## 6. Scale workers to 2

```bash
docker compose --profile multi up -d --scale worker=2     # or PRESENTON_MULTI_WORKER_REPLICAS=2 in .env
docker compose --profile multi ps worker
```

Claims are row leases taken with `SKIP LOCKED`, so two replicas never execute the same operation; submit two decks and confirm two distinct `claimed_by` values. Each replica exposes `/metrics` and `/healthz` on its own loopback only (`docker compose --profile multi exec --index 2 worker curl -s 127.0.0.1:9464/metrics`); set `WORKSPACE_OPERATION_METRICS_HOST=0.0.0.0` on the service to scrape it over the compose network, and aggregate per replica as the workbench runbook section 13 requires.

## 7. Upgrade

`docker compose --profile multi build && docker compose --profile multi up -d`. `migrate` re-runs first (no-op at head, grants re-applied), then `api`, then the workers. A populated database crossing `e7a9c2d4f6b8` needs `PPT_AGENT_ASYNC_TASK_LEGACY_TIMEZONE` in `.env`; `migrate` forwards it.

## 8. Known limits

- Workers claim only `presentation.generate` and `quality.review`. Keep `CHAT_FIRST_WORKSPACE_ENABLED` and `AUTHORED_VNEXT_GENERATION_ENABLED` off: their other operation kinds have no executor in `external` mode on Linux (the export controller needs the Windows-local sandbox backend). PPTX/PDF export from the UI runs inside `api` (direct attempt service).
- `PRESENTON_MULTI_DEPLOYMENT_TIER=production` requires a scanner-equipped image (`WORKSPACE_ASSET_MALWARE_SCAN_MODE=required`, see `docker-compose.chat-first-canary.yml`); the stock image runs the `development` tier with `PROVIDER_DURABLE_RUNTIME_REQUIRED=true` set explicitly.
- `ENVIRONMENT=production` disables the loopback development principal. The standard UI and `/api/v1/ppt` need nothing more; the public v2 API (`/api/v1/public/...`) additionally needs `PPT_PUBLIC_OIDC_*`.
- The owner login is the postgres image superuser. Keep it out of `api`/`worker`; the startup authority probe rejects it there.
- `app_data` is a bind mount shared by `api` and every worker; multi-host deployments need a shared filesystem or the public artifact store.
