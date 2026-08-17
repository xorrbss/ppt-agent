# Authored-only vNext local manual-test handoff

Updated: 2026-08-14 KST

This is a local opt-in test runbook. It does not authorize a production
rollout.

## Start here

From `C:\project\PPT-agent\ppt-agent`, run:

```powershell
npm run dev:authored-vnext:windows
```

When the command reports readiness, open
[http://127.0.0.1:5000/authored-vnext](http://127.0.0.1:5000/authored-vnext).
Use this single-origin URL rather than port 3000 so browser API calls pass
through the local proxy. Press Ctrl+C in the launcher terminal to stop only
the FastAPI, Next.js, and proxy process trees started by this command.

If a default port is occupied, select unused ports:

```powershell
npm run dev:authored-vnext:windows -- -FastApiPort 18000 -NextPort 13000 -ProxyPort 15000
```

Then open `http://127.0.0.1:15000/authored-vnext`.

Every launch receives its own SQLite database, Next build directory, and log
directory. The console prints their exact paths, for example:

`C:\project\PPT-agent\ppt-agent\app_data\authored-vnext-dev\session-4020\logs`

This avoids migrating or altering the normal app database and makes a new
launcher session a clean three-slide test run. Browser refreshes within the
running session still restore from that session's durable SQL database.

## Local-only feature boundary

All authored-vNext flags remain OFF by default outside this launcher. The
launcher passes these opt-ins only to its child processes:

- FastAPI and Next.js: `AUTHORED_VNEXT_MASTER_ENABLED=true`,
  `AUTHORED_VNEXT_GENERATION_ENABLED=true`,
  `AUTHORED_VNEXT_EXPORT_ENABLED=true`, and
  `AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED=true`.
- FastAPI and Next.js: `AUTHORED_VNEXT_REPOSITORY_MODE=sql` and
  `AUTHORED_VNEXT_LOCAL_SLIDE_COUNT=3`.
- Next.js browser build: `NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED=true` and
  `NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT=3`.
- Next.js internal worker: `AUTHORED_VNEXT_RENDERER_ENABLED=true`.
- Both application processes receive the same freshly generated
  `AUTHORED_VNEXT_OOXML_WORKER_TOKEN`; it is not printed or committed.

`AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION=false` remains explicit, preserving
the fallback path. Three-slide generation requires both a local/dev/test
environment and the exact local count opt-in; the production 10/90 contract is
unchanged. The launcher disables image generation and anonymous tracking and
does not require a paid provider or customer credentials.

For local auth, `DISABLE_AUTH=true` now establishes the active primary local
administrator as the request owner instead of skipping owner context. It fails
closed with 503 if that bootstrap owner cannot be resolved.

## Startup repair and probes

The reported Windows startup failure was reproduced against existing app data.
Its first traceback came from retired Alembic revision `d1f4a6b8c0e2`:
orphan-revision inference incorrectly applied a presentation-owned Template V2
provenance rule to intentional global default catalog rows. A prior interrupted
SQLite ownership migration could also leave the database partially stamped.

Recovery now recognizes only exact retired schema signatures, returns to the
safe common ancestor, and makes the ownership migration restartable. It does
not accept an ambiguous schema. The affected app-data database upgraded to the
single Alembic head `b6d8e0f2a4c7`. A second startup was a migration no-op and
reached `Application startup complete`; `/docs`, `/openapi.json`, and an
owner-bound authored probe succeeded before `Application shutdown complete`.
The Electron development launcher calls the same checked backend command:
`uv run python server.py --port <port> --reload false`.

Optional probes while the launcher runs:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/metrics/workspace
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/api/v1/ppt/authored-vnext/master/status
```

Both must return HTTP 200. On failure, retain `fastapi.stdout.log` and
`fastapi.stderr.log` from the printed session log directory. Do not edit
`app_data\userConfig.json` to work around startup and do not reuse a worker
token from another run.

## Three-slide manual checklist

1. Open `/authored-vnext`, enter a harmless local topic, and continue to
   Master Designer. Create, validate, preview, and publish the master.
2. Generate three slides. Select a slide, edit its body, refresh, and verify
   the deck, master, selection, and edited body are restored.
3. Prepare and reject one single-page regeneration candidate. Prepare a second
   candidate and accept it; only the selected slide should change.
4. Reorder a slide, use undo/rollback, refresh, and verify rail order and body
   state match the selected revision.
5. Export PPTX and verify the artifact downloads without a 4xx/5xx response.
6. From a stale tab/context, cause one competing edit. The normal CAS conflict
   and rebase/discard UI must appear; it must not silently overwrite.

An authentication redirect, owner denial, restoration failure, unhandled
console error, unexpected 4xx/5xx, or failed export is a P0 manual-test blocker.
Copy and visual micro-polish are not blockers for this local path.

The checked-in live acceptance command covers this flow. Run it against a
freshly started launcher before manually creating the default master:

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\nextjs
$env:AUTHORED_VNEXT_LOCAL_SLIDE_COUNT = "3"
$env:NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED = "true"
$env:NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT = "3"
$env:WORKSPACE_LIVE_BACKEND = "http://127.0.0.1:5000"
$env:CYPRESS_BASE_URL = "http://127.0.0.1:5000"
npm run test:authored-vnext-local-e2e
```

The clean-session run on 2026-08-14 passed 1/1 in 10.97 seconds. Independent
FastAPI logs show real master creation/validation/publication/preview,
three-slide generation, body edit, candidate reject/accept, reorder, an
expected stale CAS 409 followed by rebase, refresh restoration, rollback,
export job execution, and artifact download. A repeat acceptance run requires
a new launcher session because the UI intentionally uses the fixed local
default-master identity.

## Clean PPTX compatibility witness

The actual three-slide happy-path export is available at:

`C:\project\PPT-agent\ppt-agent\app_data\exports\users\4b277b66-577c-4d43-964c-34740fdf21b6\authored-vnext\05\05fdcfecd1a014735c97529d75c65fe9fef202379221089226328bdac6bbbc8d.pptx`

It is 15,697 bytes and contains 3 slides, 1 master, 4 layouts, and no
`customXml` or embedding parts. Microsoft Open XML SDK 3.5.1 validation with
the Microsoft 365 target returned zero errors and verified its digest remained
unchanged. Its prepared isolation receipt is:

`C:\project\PPT-agent\ppt-agent\servers\nextjs\.tmp\authored-vnext-happy-path-powerpoint-prep-receipt.json`

The receipt records `source.unchanged=true`, `writeOpenProbeBlocked=true`, and
distinct source/PowerPoint paths. It correctly remains inconclusive until a
person observes PowerPoint.

Naturally close all existing PowerPoint windows first. Then run:

```powershell
cd C:\project\PPT-agent\ppt-agent\servers\nextjs
powershell.exe -NoLogo -NoProfile -File `
  .\lib\ooxml-vnext\tools\powerpoint-visible-witness.ps1 `
  -InputPath "C:\project\PPT-agent\ppt-agent\app_data\exports\users\4b277b66-577c-4d43-964c-34740fdf21b6\authored-vnext\05\05fdcfecd1a014735c97529d75c65fe9fef202379221089226328bdac6bbbc8d.pptx" `
  -WitnessRoot .\.tmp `
  -OutputJson .\.tmp\authored-vnext-happy-path-open-save-reopen-receipt.json `
  -LaunchOpenSaveReopenWitness
```

The runner holds the source read-only, copies it from the locked handle,
checks matching digests, and launches only the disposable copy with PowerPoint
`/x`. It never attaches to or terminates an existing PowerPoint process. During
the two visible opens, confirm:

- no repair dialog appears on open or reopen;
- slide count is 3;
- the master/logo and cover, section, standard, and long-title layouts are
  connected and editable;
- title/body placeholders and page-number fields are correct;
- Korean/CJK, mixed text, long title, emoji, combining and fallback text render;
- a body edit can be saved and remains editable after reopen.

The legacy 90-slide fixture is not the first product witness. It deliberately
contains synthetic opaque `customXml`, an invalid 29-byte OLE-labelled payload,
and orphan relationships. PowerPoint repair removed those synthetic additions
while preserving all 90 slides, 1 master, and 4 layouts, which narrows the
repair scope but does not identify a single trigger. Six minimal three-slide
fixtures now separate clean, custom XML, orphan external relationship, raw OLE
part, orphan OLE relationship, and combined cases. All six have zero Open XML
SDK errors; after the clean product export passes visibly, test variants 02–06
in order to attribute any PowerPoint-only repair behavior.

## Non-blocking backlog

- Workbench wording, copy, and visual micro-polish.
- Large-deck/90-slide stress and performance validation.
- Final internal-only admission/rollout gate.
- Physical legacy-code deletion, only after a separate stabilization/census
  phase. Existing legacy decks remain frozen-compatible for open, preview,
  export/download, delete, and upgrade-fork.
- Visible PowerPoint observation and opaque-part bisection. Structural/headless
  validators cannot substitute for it.

## Verification record

| Item | Status |
|---|---|
| FastAPI root cause/fix, migration/head, health, shutdown | PASS |
| Next.js + FastAPI + proxy local launch | PASS |
| Three-slide real UI/API path and export/artifact | PASS |
| Refresh restore, owner context, real CAS conflict/rebase | PASS |
| Exported clean three-slide Open XML SDK 3.5.1 validation | PASS (0 errors; unchanged) |
| Focused migration recovery/restartability tests | PASS (2) |
| Next.js TypeScript check | PASS |
| Repository file-size ratchet | PASS |
| Clean three-slide visible PowerPoint open/save/reopen | Pending human witness |

Production defaults remain OFF and new-presentation admission remains false.
