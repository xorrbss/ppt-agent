# Template V2 canary readiness

Template V2 remains default-OFF. A canary is ready only when the backend flag is
exactly `true` (ignoring case and surrounding whitespace) and the template ID is
present in an explicit, valid allowlist. This procedure prepares a canary; it
does not deploy or enable anything by itself.

Last rehearsed end-to-end on local Windows on **2026-07-25** against
`origin/main` @ `83ec1d21`; see [Rehearsal record](#rehearsal-record).

## Policy inputs

Set both values in the FastAPI process:

```text
ENABLE_TEMPLATE_V2=true
TEMPLATE_V2_TEMPLATE_ALLOWLIST=canary-template-a,canary-template-b
```

The allowlist is comma-separated. Empty entries, duplicate IDs, `*`, control
characters, and IDs longer than 128 characters invalidate the entire
configuration. Values such as `1`, `yes`, and `on` do not merely fail to enable
the feature: they are invalid configuration (`template_v2_flag_invalid`) and
also void an otherwise valid allowlist. Only unset, empty, and `false` are a
clean disabled state. Malformed configuration fails closed: discovery, writes,
imports, and the PPTX dispatcher remain disabled.

Separators other than a comma are not detected. `a;b`, `a b`, and `a|b` are each
accepted as one literal template ID, so the readiness check still exits `0` and
reports `allowlisted_template_count: 1` while every write for `a` and for `b`
returns 403. Always confirm `allowlisted_template_count` equals the number of
IDs you intended.

The public Studio flag `NEXT_PUBLIC_TEMPLATE_V2_STUDIO_ENABLED` is separate and
build-time: `next build` inlines it and the check is a strict `=== "true"`, so
`TRUE` and `" true "` enable the backend flag but not this one. Enabling it does
not override the backend policy, and the backend flag does not expose the Studio
UI.

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

Template V2 ships no PPTX fixture, and the only `.pptx` in the tree
(`servers/fastapi/.venv/Lib/site-packages/pptx/templates/default.pptx`) has zero
slides: it uploads successfully and then fails in the worker with
`pptx_contains_no_slides`. Create a one-slide source first, from
`servers/fastapi`:

```powershell
uv run python -c "from pptx import Presentation; from pptx.util import Inches; p=Presentation(); s=p.slides.add_slide(p.slide_layouts[6]); s.shapes.add_textbox(Inches(1),Inches(1),Inches(6),Inches(1)).text_frame.text='canary'; p.save('canary.pptx')"
```

The first template must be created by importing that file: `POST
/api/v1/ppt/structured-templates/imports` (multipart `template_id` + `pptx_file`,
plus a required `Idempotency-Key` header of 8-200 characters and content type
`application/vnd.openxmlformats-officedocument.presentationml.presentation`),
then `POST /{import_id}/confirm`. `POST /api/v1/ppt/structured-templates` cannot
bootstrap a canary: it requires a presentation already persisted as
`version=v2-standard, mode=template`, and only import confirmation produces one.

1. Confirm the readiness command exits `0` and reports the expected allowlist
   count.
2. Confirm list discovery returns only allowlisted template IDs.
3. Confirm a write for a non-allowlisted ID returns HTTP 403. Use `DELETE
   /api/v1/ppt/structured-templates/<non-allowlisted-id>`; the policy gate runs
   before any body or existence check. `POST` is not a usable probe -- an invalid
   `layouts` body returns 422 before the policy gate is reached. A PPTX import
   for a non-allowlisted ID also returns 403, but only once the
   `Idempotency-Key` header is present; without it the request fails validation
   with 422 first.
4. Exercise one allowlisted Template V2 template workflow, then one existing
   authored and one existing adaptive workflow. The allowlist gates Template V2
   template IDs only; authored and adaptive decks stay `v1-standard` and never
   reach the rollout policy, so their execution paths must be identical with the
   flag on and off.
5. Monitor `template_v2_rollout` events by operation, outcome, and code.
   Template identifiers are hashed; presentation content is never logged.
6. Monitor `template_v2_pptx_queue` events. `dispatch` counts work offered to
   workers, while `recover` counts expired leases returned to the queue. Both
   payloads contain only an operation, outcome, and bounded aggregate count.

Both event families are emitted at INFO on the `services.template_v2_rollout` and
`services.template_v2_pptx_queue_observability` loggers, so `LOG_LEVEL=WARNING`
silently suppresses them.

## Rollback

Set `ENABLE_TEMPLATE_V2=false` (or remove it) and restart the FastAPI process.
This immediately blocks new Template V2 discovery, writes, imports, and
dispatcher startup. Existing persisted Template V2 presentations remain
readable/exportable; rollback is not a data migration. Do not remove stored
Template V2 rows or source artifacts as part of the flag rollback.

Drain in-flight imports **before** flipping the flag. While the flag is off, an
import in `queued`, `processing`, `finalizing`, `review_required`, or `failed` is
read-only: its status stays readable, but `confirm`, `cancel`, and `retry` all
return 403, so the owner can neither finish nor abandon it. Private-source TTL
cleanup is also paused, because it runs only inside the dispatcher; overdue
deadlines are honored on the next enabled start, not during the rollback.

To drain without a full rollback, keep `ENABLE_TEMPLATE_V2=true` and shrink
`TEMPLATE_V2_TEMPLATE_ALLOWLIST` to only the affected template IDs. Confirm,
cancel, and retry keep working for those IDs and TTL cleanup keeps running, while
every other template is blocked exactly as it would be by a full rollback.

## Rehearsal record

Executed 2026-07-25 on local Windows against `origin/main` @ `83ec1d21`, with an
isolated `APP_DATA_DIRECTORY` and a real 3-slide PPTX source. Upstream
compatibility held at 426 checks throughout.

| Step | Result |
|---|---|
| Readiness matrix (19 configurations) | Exit `0` for `true`/`TRUE`/`" true "` and a 128-character ID; exit `2` for `1`/`yes`/`on`, unset/blank allowlist, empty entry, trailing comma, `*`, `a,*`, duplicate, 129 characters, and an embedded newline |
| Discovery / write / import gating | Unauthenticated 401; list returns only allowlisted IDs; write and import on a non-allowlisted ID 403; stale `expected_revision` 409 |
| Import lifecycle | `queued` -> `review_required` in 2 s -> `confirmed`, all three slides parsed into layouts |
| Authored + adaptive regression | Full pytest identical with the flag off and on (831 passed, 3 skipped each); live adaptive produced an editable 3-slide PPTX and live authored a 3-slide image deck, both persisted `v1-standard`; no SQLite contention errors with the dispatcher running |
| Rollback | Writes/imports 403, discovery hidden while the row still existed, reads and exports 200, and every row plus every private source preserved |
| Worker mode | `external` starts no embedded dispatcher (job stayed `queued` for 15 s); the external worker then claimed it and emitted content-free `template_v2_pptx_queue` and `template_v2_pptx_analysis` events |

The rehearsal found one defect: neither event family reached any log sink in the
FastAPI process, because the root logger had no handler. Fixed in the same
change set; `template_v2_rollout` went 0 -> 2 and `template_v2_pptx_queue` 0 -> 10
for an identical request sequence, making steps 5 and 6 above actionable as
written.

Known gap, tracked separately: imports that reach `cancelled` or `confirmed`
never have their private source reclaimed under any flag state, because
`TERMINAL_IMPORT_STATES` in `services/template_v2_pptx_retention_service.py`
covers only `review_required` and `failed`.
