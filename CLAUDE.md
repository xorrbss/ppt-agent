# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Presenton is an open-source, self-hostable AI presentation generator (Gamma/Beautiful AI alternative). It generates editable slide decks from prompts or uploaded documents, renders them with HTML+Tailwind templates, and exports to fully-editable PPTX and PDF. It ships as a Docker image, an Electron desktop app, and an HTTP API (plus an MCP server).

## Architecture: one product, three runtimes

The same FastAPI backend + Next.js frontend pair runs inside two packaging shells. Understand this first — almost every file lives in one of these two servers, and both packagings reuse them:

- **`servers/fastapi/`** — Python 3.11 FastAPI backend (managed by `uv`). All generation logic, LLM/image provider abstraction, persistence, and PPTX/PDF export live here.
- **`servers/nextjs/`** — Next.js 16 (App Router) frontend + thin API proxy layer. The slide editor, built-in templates, and Redux state live here.
- **`electron/`** — Desktop shell that bundles the two servers above. **Per `CONTRIBUTING.md`, contributions are expected to live inside `electron/`; changes outside it "may not be accepted at this time."** When packaging desktop builds, FastAPI is frozen with PyInstaller and Next.js is prebuilt.
- **`scripts/` + root `start.js`** — `start.js` is the Docker container entrypoint. It boots **nginx (port 80)** as a reverse proxy in front of **Next.js (3000)** and **FastAPI (8000)**, plus a standalone **MCP server (8001)**, and writes `app_data/userConfig.json` from env vars. `scripts/sync-presentation-export.cjs` downloads the versioned `presentation-export` runtime that performs PPTX/PDF export (version pinned by `presentationExportVersion` in root `package.json`).

Request path in Docker: browser → nginx:80 → (`/api/v1/*` → FastAPI, everything else → Next.js). Inside the container, Next.js reaches FastAPI directly via `FAST_API_INTERNAL_URL` (default `http://127.0.0.1:8000`) rather than bouncing through nginx.

## Running & developing

**Docker (simplest full-stack run).** Compose services are `production`, `production-gpu`, `development`, `development-gpu`:
```bash
docker compose up development          # hot-reload dev (Dockerfile.dev, source bind-mounted)
docker compose up production           # production image (Dockerfile)
```
App is served at http://localhost:5000. Provider keys come from a `.env` next to `docker-compose.yml` (see the long env table in `README.md`).

**Electron (the supported contribution path).** Requires Node LTS, npm, Python 3.11, and `uv`:
```bash
cd electron
npm run setup:env     # installs electron deps, runs `uv sync` in fastapi, `npm install` in nextjs, fetches export runtime
npm run dev           # tsc + launch Electron with bundled backend/UI
npm run build:all     # full distributable build (Next.js prebuild + PyInstaller FastAPI + electron-builder)
```

**Per-service (fastest iteration on one server):**
```bash
# Backend — APP_DATA_DIRECTORY is REQUIRED or start.js/server.py throws
cd servers/fastapi && uv sync
uv run python server.py --port 8000           # FastAPI app object is api.main:app
uv run python mcp_server.py --port 8001        # MCP wrapper over the OpenAPI spec

# Frontend
cd servers/nextjs && npm install
npm run dev                                     # localhost:3000; set NEXT_PUBLIC_FAST_API / FAST_API_INTERNAL_URL to reach the backend
```

## Build / test / lint commands

**FastAPI** (`servers/fastapi`, pytest; `testpaths = ["tests"]`):
```bash
uv run pytest                                   # full suite
uv run pytest tests/unit/test_dict_utils.py     # single file
uv run pytest tests/unit/test_dict_utils.py::test_name   # single test
uv run pytest --cov                             # with coverage (config in pyproject.toml)
```
Tests are organized as `tests/unit/`, `tests/integration/`, `tests/edge_cases/`, `tests/regression/`, with shared fixtures in `tests/conftest.py` and LLM/image stubs in `tests/mocks/`. CI runs with `DISABLE_IMAGE_GENERATION=true`, `DATABASE_URL=sqlite+aiosqlite:///./test.db`, and `APP_DATA_DIRECTORY=/tmp/app_data`.

**Next.js** (`servers/nextjs`):
```bash
npm run build                                   # next build → output dir is .next-build (NOT .next; see next.config.mjs distDir + output:"standalone")
npm run lint                                    # eslint .
npx cypress run --component                     # component tests (Cypress; no Jest/Vitest)
```

**Electron** (`servers/../electron`): `npm run typecheck` (`tsc --noEmit`), `npm run lint:main`.

## FastAPI backend internals

- **Entry/routing:** `server.py` → `api/main.py` (FastAPI app). REST endpoints live under `api/v1/ppt/endpoints/` — key ones: `presentation.py` (generate/CRUD/export), `outlines.py`, `slide.py`, `pptx_slides.py`/`pdf_slides.py`, `theme.py`/`theme_generate.py`, `images.py`, `icons.py`, `files.py`, `chat.py`. Auth + per-request config refresh are in `api/middlewares.py`.
- **Generation pipeline** (trace from `endpoints/presentation.py`): parse inputs (`services/documents_loader.py`, `services/liteparse_service.py`) → outline (`utils/llm_calls/generate_presentation_outlines.py`) → slide structure/layout selection (`generate_presentation_structure.py`) → per-slide content (`generate_slide_content.py`) → images (`services/image_generation_service.py`) → persist (`models/sql/*`) → export (`services/export_task_service.py`, which renders via headless Chromium / the bundled export runtime).
- **LLM provider abstraction:** `utils/llm_provider.py` + `utils/llm_config.py` wrap the `llmai` library and select among openai/google/vertex/azure/bedrock/openrouter/fireworks/together/cerebras/anthropic/litellm/lmstudio/ollama/custom/codex. Add provider logic here, not in endpoints.
- **Persistence:** SQLModel (SQLAlchemy + Pydantic) models in `models/sql/`; Alembic migrations in `alembic/`, run on startup when `MIGRATE_DATABASE_ON_STARTUP=true` (orchestrated by `migrations.py`). `DATABASE_URL` accepts any async SQLAlchemy URL; falls back to SQLite under app data.
- **Configuration:** all env vars are read through `utils/get_env.py` — treat it as the source of truth for supported variables. Per-instance runtime keys are persisted in `app_data/userConfig.json` (written by `start.js`); they are only writable when `CAN_CHANGE_KEYS != "false"`. Single-admin auth lives in `utils/simple_auth.py`.

## Next.js frontend internals

- **App Router** under `app/`, grouped by segment: `(presentation-generator)/` holds the dashboard, upload/outline flows, and the slide **editor** at `presentation/page.tsx?id=<uuid>`. Inline editing components (`PresentationRender.tsx`, `EditableLayoutWrapper.tsx`, `TiptapText.tsx`) live in `(presentation-generator)/components/`.
- **Built-in slide templates:** `app/presentation-templates/` — many React components (grouped: `general/`, `Code/`, `Education/`, `Report/`, `pitch-deck/`, `neo-*/`, etc.). Each layout exports a TSX component (HTML + Tailwind) plus a `Schema` (JSON/Zod) used both for editing forms and for server-side schema extraction (`lib/compile-template-schema.ts`). This is where you add or modify slide designs.
- **Backend calls:** typed API clients in `app/(presentation-generator)/services/api/*.ts` call `/api/v1/*`. URL resolution (`utils/api.ts`) and the auth/forwarding middleware (`proxy.ts`) decide same-origin vs. direct-FastAPI based on `FAST_API_INTERNAL_URL`/`NEXT_PUBLIC_FAST_API`. Route handlers under `app/api/*` are thin proxies or serve built-in template/layout data and the bundled export (`/api/export-presentation`).
- **State:** Redux Toolkit in `store/` — slices `presentationGeneration`, `presentationGenUpload`, `userConfig`, `undoRedo`.
- **UI:** Tailwind + shadcn/ui (`components/ui/`, config in `components.json`); rich text via TipTap, charts via Recharts, diagrams via Mermaid.

## Conventions & gotchas

- Next.js build artifacts go to **`.next-build`**, not `.next` — the Dockerfile and `next.config.mjs` depend on this.
- Running FastAPI (directly or via `start.js`) **requires `APP_DATA_DIRECTORY`**; generated files, uploads, fonts, exports, and `userConfig.json` all live under it.
- The PPTX/PDF export runtime is an external, versioned package synced by `scripts/sync-presentation-export.cjs`. If export breaks after a checkout, run `npm run sync:presentation-export` (root) or rebuild the image.
- When changing public LLM/image config knobs, keep four places consistent: `utils/get_env.py`, `start.js` (`setupUserConfigFromEnv`), `docker-compose.yml`, and the env table in `README.md`.
- Honor the global engineering invariants in the user's `~/.claude/CLAUDE.md` (KISS/YAGNI, prefer extending existing structure, keep files within ~500 lines, no hidden hacks — stop and leave a `TODO: [BLOCKED]` on principle conflicts).
