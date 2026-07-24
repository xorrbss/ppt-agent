# Template V2 canary readiness

Template V2 remains default-OFF. A canary is ready only when the backend flag is
exactly `true` (ignoring case and surrounding whitespace) and the template ID is
present in an explicit, valid allowlist. This procedure prepares a canary; it
does not deploy or enable anything by itself.

## Policy inputs

Set both values in the FastAPI process:

```text
ENABLE_TEMPLATE_V2=true
TEMPLATE_V2_TEMPLATE_ALLOWLIST=canary-template-a,canary-template-b
```

The allowlist is comma-separated. Empty entries, duplicate IDs, `*`, control
characters, and IDs longer than 128 characters invalidate the entire
configuration. Values such as `1`, `yes`, and `on` do not enable the feature.
Malformed configuration fails closed: discovery, writes, imports, and the PPTX
dispatcher remain disabled.

The public Studio flag is separate and build-time. Enabling it does not override
the backend policy, and the backend flag does not expose the Studio UI.

## Worker mode

The default `TEMPLATE_V2_PPTX_WORKER_MODE=embedded` keeps the durable dispatcher
inside the FastAPI process for single-node deployments. For multi-node
deployments, set `TEMPLATE_V2_PPTX_WORKER_MODE=external` in both the API and a
dedicated worker process, then start the worker from `servers/fastapi`:

```powershell
uv run python -m services.template_v2_pptx_worker
```

The external worker consumes the same database-backed queue with compare-and-set
claims, leases, heartbeats, stale-attempt recovery, and graceful requeue. Invalid
worker-mode values fail closed by starting no embedded dispatcher.

## Offline readiness check

From `servers/fastapi`, with the intended environment loaded:

```powershell
uv run python scripts/check_template_v2_canary.py
```

The command prints content-free JSON and exits `0` only for
`template_v2_canary_ready`. It never prints template IDs. Exit `2` is a NO-GO;
use the `code` field to distinguish a disabled feature, missing allowlist, or
invalid configuration.

Before a canary handoff, run:

```powershell
uv run pytest tests/unit/test_template_v2_policy.py
uv run pytest tests/unit/test_template_v2_strategies.py tests/unit/test_structured_templates_api.py
```

## Canary verification

Use only the existing authenticated `/api/v1/ppt/structured-templates` routes.
There is no `/api/v2` rollout surface.

1. Confirm the readiness command exits `0` and reports the expected allowlist
   count.
2. Confirm list discovery returns only allowlisted template IDs.
3. Confirm a write or PPTX import for a non-allowlisted ID returns HTTP 403.
4. Exercise one allowlisted adaptive/template workflow and one existing authored
   workflow. Authored and adaptive execution paths must remain unchanged.
5. Monitor `template_v2_rollout` events by operation, outcome, and code.
   Template identifiers are hashed; presentation content is never logged.

## Rollback

Set `ENABLE_TEMPLATE_V2=false` (or remove it) and restart the FastAPI process.
This immediately blocks new Template V2 discovery, writes, imports, and
dispatcher startup. Existing persisted Template V2 presentations remain
readable/exportable; rollback is not a data migration. Do not remove stored
Template V2 rows or source artifacts as part of the flag rollback.
