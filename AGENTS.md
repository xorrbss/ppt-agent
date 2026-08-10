# AGENTS.md

## Global policy

Follow the global `~/.codex/AGENTS.md` working policy. During development, run the
smallest checks relevant to the changed layer. Do not run a complete test suite
unless the user explicitly requests it in the current request. Commands listed
below are references, not authorization for an automatic full-suite run.

## Project overview

Presenton is a self-hostable AI presentation generator. It uses a Python 3.11
FastAPI backend, a Next.js 16/React 19 frontend, and an Electron desktop shell.
It generates presentations from prompts or documents and exports editable PPTX
and PDF files.

This fork adds Korean UI defaults, Windows-native development fixes, offline
Korean OCR assets, and an authored high-quality slide mode. Preserve API values,
provider/model IDs, and other logic literals in English unless a task explicitly
requires changing them.

## Upstream authority

The authoritative upstream for this fork is the official Presenton project
([`presenton/presenton`](https://github.com/presenton/presenton)). The configured
`origin` remote and local branches or worktrees are collaboration and delivery
copies; they are not the upstream source of truth. Review and import upstream
changes against the official Presenton project and follow
`compatibility/upstream-intake-policy.json`.

## Repository map

- `servers/fastapi/`: backend, generation pipelines, persistence, auth, export,
  provider integrations, and tests. Dependencies are managed with `uv`.
- `servers/nextjs/`: App Router frontend, editor, templates, Redux state, and API
  proxy routes.
- `electron/`: desktop shell and packaging. Electron reuses both servers.
- `presentation-export/`: versioned PPTX/PDF export runtime. Treat generated or
  downloaded contents carefully; sync it through the root npm scripts.
- `scripts/`: local proxy, automation CLI, and repository maintenance scripts.
- `app_data/`: local runtime data and secrets. Never commit its contents.
- `RUNNING-LOCAL.md`: authoritative notes for this fork's Windows workflows.
- `CLAUDE.md`: detailed architecture and implementation notes that also apply to
  work in this repository.

## Setup and run commands

Run commands from the indicated directory.

### Electron (preferred full local workflow)

```powershell
cd electron
npm run setup:env
npm run dev
```

Prerequisites: Node.js LTS, npm, Python 3.11, and `uv`. The first setup may need
network access for untracked generated dependencies and export assets.

### Backend

```powershell
cd servers/fastapi
uv sync
$env:APP_DATA_DIRECTORY="..\..\app_data"
$env:USER_CONFIG_PATH="..\..\app_data\userConfig.json"
$env:NEXT_INTERNAL_URL="http://localhost:5000"
$env:MIGRATE_DATABASE_ON_STARTUP="true"
uv run python server.py --port 8000 --reload false
```

`APP_DATA_DIRECTORY` is required. On Windows, keep uvicorn reload disabled unless
the task specifically needs it. Keep startup migrations enabled for normal local
and packaged runs; Alembic upgrades are idempotent. Electron injects this setting
for both its development and packaged FastAPI launch paths.

### Frontend

```powershell
cd servers/nextjs
npm install
$env:FAST_API_INTERNAL_URL="http://127.0.0.1:8000"
$env:USER_CONFIG_PATH="..\..\app_data\userConfig.json"
npm run dev
```

For the complete web flow, run `node scripts/presenton-local-proxy.mjs` from the
repository root and use `http://localhost:5000` as the single origin.

### Optional MCP server

The Docker `start.js` entrypoint does not start MCP by default. Set
`ENABLE_MCP_SERVER=true` to opt in; production still passes `--reload false`.
Direct local execution is itself an explicit opt-in:

```powershell
cd servers/fastapi
uv run python mcp_server.py --port 8001 --reload false
```

The Docker nginx `/mcp` and `/mcp/` routes require a valid Presenton session or
admin API-key bearer credential before a request is proxied to MCP.

## Verification

Use the narrowest relevant check first, then broaden it when warranted.

```powershell
# Backend
cd servers/fastapi
uv run pytest tests/unit/test_dict_utils.py
uv run pytest  # Full backend suite: explicit user request only

# Frontend
cd servers/nextjs
npm run lint
npm run build

# Electron
cd electron
npm run typecheck
npm run lint:main

# Export runtime consistency (repository root)
npm run check:presentation-export
```

Next.js build output is `.next-build`, not `.next`.

## Engineering conventions

- Keep changes scoped and extend existing architecture before adding parallel
  abstractions.
- Do not edit generated build output, caches, downloaded runtimes, or local app
  data unless the task explicitly targets them.
- Never expose or commit API keys from `.env` or `app_data/userConfig.json`.
- Backend REST endpoints live in `servers/fastapi/api/v1/ppt/endpoints/`.
- Put provider selection/configuration logic in `utils/llm_provider.py` and
  `utils/llm_config.py`, not in endpoints.
- When adding or renaming public provider settings, keep `utils/get_env.py`,
  `start.js`, `docker-compose.yml`, and the README environment table aligned.
- Built-in slide templates belong in
  `servers/nextjs/app/presentation-templates/`; keep each component's schema in
  sync with its editable/rendered structure.
- Preserve same-origin routing assumptions: `/api/v1/*` reaches FastAPI and other
  application routes reach Next.js through the proxy in full web runs.
- Prefer tests near the affected layer. Backend tests are grouped under `unit/`,
  `integration/`, `edge_cases/`, and `regression/`.
- Preserve unrelated user changes in a dirty worktree.

## Export and rendering notes

- If editable export assets are missing after a checkout, run
  `npm run sync:presentation-export` from the repository root.
- Rendering requires Chrome/Chromium; `CHROME_PATH` or
  `PUPPETEER_EXECUTABLE_PATH` can override auto-detection.
- Authored mode produces image-per-slide output and has different editing/export
  behavior from adaptive/template mode. Do not conflate the two pipelines.
- `USER_CONFIG_PATH` must be present in the Next.js process as well as FastAPI;
  omitting it can cause configuration checks and route redirects to fail.
