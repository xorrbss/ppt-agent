# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Presenton is an open-source, self-hostable AI presentation generator (Gamma/Beautiful AI alternative). It generates editable slide decks from prompts or uploaded documents, renders them with HTML+Tailwind templates, and exports to fully-editable PPTX and PDF. It ships as a Docker image, an Electron desktop app, and an HTTP API (plus an MCP server).

**This repository is a fork** (`bitbucket.org/ibizsoftware/devax-ppt`) of `github.com/presenton/presenton`. The fork has diverged substantially: Korean UI, Windows-native operation without Docker, a Korean-business template set, and a large second generation pipeline (see "Two generation pipelines" below). Roughly 1,580 source files / 417k lines: `servers/fastapi` ~707 files, `servers/nextjs` ~770, `electron` ~80.

## Architecture: one product, three runtimes

The same FastAPI backend + Next.js frontend pair runs inside two packaging shells. Understand this first — almost every file lives in one of these two servers, and both packagings reuse them:

- **`servers/fastapi/`** — Python 3.11 FastAPI backend (managed by `uv`). All generation logic, LLM/image provider abstraction, persistence, and PPTX/PDF export live here. ~33 endpoint modules and ~96 service modules.
- **`servers/nextjs/`** — Next.js 16 (App Router) frontend + thin API proxy layer. The slide editor, built-in templates, and Redux state live here.
- **`electron/`** — Desktop shell that bundles the two servers above. When packaging desktop builds, FastAPI is frozen with PyInstaller and Next.js is prebuilt. (Upstream `CONTRIBUTING.md` says contributions should live inside `electron/`; that is an upstream statement — in this fork most work happens outside it, and the CI gates all live outside it.)
- **`scripts/` + root `start.js`** — `start.js` is the Docker container entrypoint. It boots **nginx (port 80)** as a reverse proxy in front of **Next.js (3000)** and **FastAPI (8000)**, plus a standalone **MCP server (8001)**, and writes `app_data/userConfig.json` from env vars. `scripts/sync-presentation-export.cjs` downloads the versioned `presentation-export` runtime that performs PPTX/PDF export (version pinned by `presentationExportVersion` in root `package.json`).

Supporting top-level directories that are easy to miss:

- **`contracts/chat-first-workspace/v1/`** — checked-in JSON contracts for the workspace API (bootstrap/assets, commands/receipts, errors/runtime, timeline projection). Verified from both the Python and TypeScript sides.
- **`compatibility/`** — machine-checked ledgers governing fork↔upstream drift: `upstream-compatibility.json` (pinned upstream SHA, export/frontend versions, v2 renderer discriminators), `upstream-byte-parity.json`, `protected-local-patches.json`, intake policy, selective-integration ledgers.
- **`docs/`** — 63 dated design documents and operational runbooks (`*-YYYYMMDD.md`). There is **no index**; it reads as an append-only archive. It is nonetheless the most accurate description of the slidespec/workspace subsystem — better than this file was before, and worth grepping before designing anything in that area.
- **`document-extraction-liteparse/`**, **`tessdata/`** — bundled document parsing runtime and offline OCR data (eng + kor).

Request path in Docker: browser → nginx:80 → (`/api/v1/*` → FastAPI, everything else → Next.js). Inside the container, Next.js reaches FastAPI directly via `FAST_API_INTERNAL_URL` (default `http://127.0.0.1:8000`) rather than bouncing through nginx.

## Two generation pipelines

**Do not assume the upstream "outline → pick a React template → fill content" flow is the whole story.** `POST /presentation/generate` accepts a `strategy` field (`models/generate_presentation_request.py`) with four values, and `endpoints/presentation_generate.py` branches on it:

| strategy | What it does |
|---|---|
| `legacy` / `adaptive` | The upstream path: pick a React layout component from `app/presentation-templates/`, fill its Zod-typed schema. `adaptive` is the default template. |
| `template_v2` | Slides are a **validated JSON element tree** instead of a React component. Backend models in `templates/v2/models/elements/`; rendered to HTML by `servers/nextjs/lib/template-v2-json-to-html.ts` + `lib/template-v2-html/`. Enables AI-generated layouts, importing a real `.pptx` as a template, and immutable revisions. |
| `authored` | An LLM writes raw HTML/CSS per slide (`utils/llm_calls/author_slide.py`), stored as `slides.html_content`. A DB check constraint enforces `slides.ui` XOR `slides.html_content`, so authored slides bypass the template schema entirely. |

Four fork-specific terms you will meet constantly in `services/`:

- **authored** — LLM-written slide HTML (above).
- **slidespec** — a canonical, versioned, normalized-0-to-1 geometry IR (`models/slidespec_v1.py`): content nodes, a recursive layout tree, and constraint kinds with hard/soft strength. Authored HTML is imported into a SlideSpec, inspected, repaired via a constraint solver plus LLM-proposed *closed-operation* repair plans (never raw HTML edits), then published back through a content-addressed store.
- **authored-hybrid** — the **export** strategy. `servers/nextjs/lib/authored-hybrid/browser-source.ts` is injected into headless Chrome to measure every element so the assembler can emit **native OOXML shapes** over a transparent raster backplate. This is why exported PPTX is genuinely editable rather than a screenshot.
- **workspace** — a chat-first control plane: its own DB tables (conversations, messages, assets, operations, operation events, outbox, receipts, artifacts), an event-sourced timeline, idempotent commands, an outbox dispatcher and lease fencing. Endpoints in `endpoints/workspace.py`, UI in `app/(presentation-generator)/presentation/workspace/`.

These are **additive**: upstream `presentation`/`slide` tables survive and the new tables are sidecars. Feature gates (all default-off / conservative):

- `CHAT_FIRST_WORKSPACE_ENABLED` (backend) and `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` (frontend) both default `false`; `docker-compose.chat-first-canary.yml` runs the canary on ports 5050–5052 with a ClamAV-equipped image.
- `SLIDESPEC_*` rollout knobs live in `services/slidespec_rollout_policy.py` (import shadow, repair shadow/canary/apply, all-slides).
- Template-v2 exposure is gated by `services/template_v2_rollout.py`.

**Status check before you trust this pipeline:** `docs/slidespec-production-quality-speed-analysis-20260805.md` records a real 90-slide run where 23 slides were flagged, repair candidates were produced for 5, and **0 repairs were applied**. The team's own conclusion is that the static scorecard (100/100) measures the existence of machinery, not outcomes. Treat end-to-end repair as unproven, and read that document plus `docs/slidespec-operations-replay-runbook-20260805.md` before changing anything in `services/slidespec_*`.

## Running & developing

**Docker (simplest full-stack run).** Compose services are `production`, `production-gpu`, `development`, `development-gpu`:
```bash
docker compose up development          # hot-reload dev (Dockerfile.dev, source bind-mounted)
docker compose up production           # production image (Dockerfile)
```
App is served at http://localhost:5000. Provider keys come from a `.env` next to `docker-compose.yml` (see the long env table in `README.md`).

**Electron.** Requires Node LTS, npm, Python 3.11, and `uv`:
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

## 이 포크: Windows 네이티브 + 한글 (`RUNNING-LOCAL.md` 참고)

업스트림 `presenton/presenton`을 포크하여 한글 UI와, Docker 없이 Windows에서 **web**·**Electron** 앱을 네이티브로 실행하는 데 필요한 수정을 추가한 버전이다. 위의 업스트림 기준 Docker 안내도 여전히 동작하지만, 여기서의 일상 개발은 Docker 없는 경로를 사용한다.

- **한글 UI / 기본값:** 앱 화면(chrome)이 번역되어 있고, 기본 프레젠테이션 언어는 한국어다(`upload/components/UploadPage.tsx`). API 값, 프로바이더/모델 ID, 로직 리터럴은 영어로 유지한다. 사용자 표시 문자열은 `next-intl`과 `i18n/messages/{en,ko}.json`을 사용하고, 새 감사 대상 화면은 `tests/i18n-audited-surfaces.test.mjs` 및 `npm run check:i18n` 게이트에 포함한다.
- **단일 오리진 프록시 (로컬에서 nginx 대체):** `node scripts/presenton-local-proxy.mjs`가 `nginx.conf`를 그대로 반영하여 `http://localhost:5000` → Next.js:3000 / FastAPI:8000 / MCP:8001로 라우팅한다. 포트는 `PROXY_PORT`/`NEXT_PORT`/`FASTAPI_PORT`/`MCP_PORT` 환경변수로 바꾼다. 브라우저 프론트엔드는 단일 오리진을 전제로 하므로, web 플로우에서는 (raw `npm run start`가 아니라) 이 프록시를 실행한다.
- **Windows 백엔드는** `APP_DATA_DIRECTORY` **와** `USER_CONFIG_PATH` **둘 다 필요**하며, uvicorn은 `--reload false`로 실행한다(이 포크에서 reloader 비활성화). 리로더가 없으므로 백엔드 코드를 바꾸면 프로세스를 직접 재시작해야 한다.
- **기존 `app_data`를 재사용해 업그레이드할 때는** `MIGRATE_DATABASE_ON_STARTUP=true` **를 반드시 설정한다.** 이 값이 정확히 `"true"`가 아니면 마이그레이션이 조용히 건너뛰어지고, 새 DB는 `create_db_and_tables()`로 생성되기 때문에 오류 없이 스키마만 어긋난다.
- **`npm run dev` (electron)** 는 크로스플랫폼이다 — `rm -rf` 대신 `node scripts/rmrf.cjs`(`electron/scripts/rmrf.cjs`)를 써서 Windows에서도 동작한다. Electron은 `DISABLE_AUTH=true`로 실행된다. LiteParse가 시스템 Node에서 동작하도록 `PRESENTON_DEV_NODE_BINARY`에 시스템 `node.exe` 경로를 설정한다.
- **Export 수정:** `utils/export_utils.py`가 Next.js 프록시를 통해 export 세션 쿠키를 주입한다(최신 Chromium은 CDP로 설정한 `Cookie` 헤더를 무시함).
- **자동화 CLI:** `scripts/ppt-agent.mjs`는 `POST /api/v1/ppt/presentation/generate`를 호출하는 의존성 없는 Node 18+ 클라이언트다(기본 한국어). 단건 또는 `--batch <파일>`, `--slides <n|auto>`, `--async`, `--language`, `--template`, `--style`, `--tone`, `--verbosity`, `--web-search`, `--toc`, `--no-title`, `--export pptx|pdf`, `--base`/`--ui-base`, `--user`/`--password`, `--out <디렉터리>`, `--timeout` 지원. Electron/`DISABLE_AUTH` 백엔드 대상에서는 자격 증명이 필요 없다. 재시도·재개 기능은 없으므로 배치 중간 실패 시 전체를 다시 돌려야 한다.

## Build / test / lint commands

**FastAPI** (`servers/fastapi`, pytest; `testpaths = ["tests"]`):
```bash
uv run pytest                                   # full suite (~1,880 tests)
uv run pytest tests/unit/test_dict_utils.py     # single file
uv run pytest tests/unit/test_dict_utils.py::test_name   # single test
uv run pytest --cov                             # coverage config exists in pyproject; NOT run in CI, no threshold
```
Tests are organized as `tests/unit/`, `tests/integration/`, `tests/edge_cases/`, `tests/regression/`, with shared fixtures in `tests/conftest.py` and LLM/image stubs in `tests/mocks/`. CI runs with `DISABLE_IMAGE_GENERATION=true`, `DATABASE_URL=sqlite+aiosqlite:///./test.db`, and `APP_DATA_DIRECTORY=/tmp/app_data`.

Ruff is locked in `servers/fastapi/uv.lock` and configured in `pyproject.toml` for `E`, `F`, `B`, and `ASYNC`. Both GitHub and Bitbucket run `uv run ruff check .` as a required Python lint gate. The per-file ignores are the audited legacy baseline and should be reduced when a touched file can be fixed safely. There is still no required mypy or Black gate.

**Next.js** (`servers/nextjs`):
```bash
npm run build                  # runs `node scripts/run-next-build.mjs` (NOT bare `next build`)
npm run lint                   # eslint .
npm run test:ci-node           # node --test; auto-discovers *.test.ts/tsx/mjs under app/ components/ lib/ scripts/ utils/
npm run test:template-v2-export-fidelity
npm run test:workspace-e2e     # spawns a real browser
npx cypress run --component    # component tests (*.cy.tsx)
```
Both Cypress **and** `node:test` suites exist — the frontend is not Cypress-only. `scripts/run-ci-node-tests.mjs` walks `app/`, `components/`, `lib/`, `scripts/`, `tests/`, and `utils/`; the explicit `servers/nextjs/tests/` contract files therefore run in the shared CI Node suite. Only the runner's printed dedicated-flow exclusions are omitted.

**Root-level:**
```bash
npm run test:local -- --help        # local CI-parity runner (scripts/test-local.mjs), core/all profiles
npm run test:next-ci                # root node:test suites, then chains servers/nextjs test:ci-node
npm run test:upstream-byte-parity
npm run check:presentation-export   # verify the pinned export runtime is present
```

**Electron:** `npm run typecheck` (`tsc --noEmit`), `npm run lint:main`. Neither runs in CI.

**CI gates.** `.github/workflows/test-all.yml` runs three jobs on PRs to `main`: `test-fastapi` (Ruff, full pytest with `uv sync --frozen`, plus a PyInstaller build), `test-nextjs` (`test:next-ci` → `tsc --noEmit` → eslint → `next build` → 26 named Cypress specs), and `workspace-e2e` (xvfb browser run). Five more workflows are path-filtered (`template-v2-export-fidelity`, `upstream-compatibility`, `upstream-intake`, `cross-platform-packaging-security`, `g4-pptx-roundtrip`). `bitbucket-pipelines.yml` requires the shared Next Node suite, Dashboard Cypress component gate, upstream byte parity, and chat-first E2E on pull requests and `main`. `scripts/required-ci-contract.test.mjs` is a meta-test that asserts these workflow contracts — if you change them, update the contract test too.

## FastAPI backend internals

- **Entry/routing:** `server.py` → `api/main.py`. Routers: `api/v1/ppt` (the bulk), `api/v1/auth`, `api/v1/admin`, `api/v1/async_tasks`, `api/v1/webhook`, and `api/v1/mock` (registered conditionally). REST endpoints live under `api/v1/ppt/endpoints/` — ~33 modules including `presentation.py`, `presentation_generate.py`, `presentation_crud.py`, `presentation_share.py`, `presentation_versions.py`, `workspace.py`, `authored.py`, `structured_templates.py`, `template_v2_compat.py`, `outlines.py`, `slide.py`, `slide_to_html.py`, `pptx_slides.py`/`pdf_slides.py`, `theme.py`/`theme_generate.py`, `images.py`, `icons.py`, `fonts.py`, `files.py`, `file_decompose_jobs.py`, `chat.py`, `layouts.py`, `prompts.py`, plus provider auth modules (`openai.py`, `anthropic.py`, `google.py`, `ollama.py`, `codex_auth.py`).
- **Auth:** `api/middlewares.py`. `SessionAuthMiddleware` treats **every** `/api/` and `/app_data/` path as protected; the only exemptions are `/api/v1/auth/` and the token-only public share prefix. `/docs`, `/openapi.json`, `/redoc` are explicitly protected, not exempt. Auth decisions run on the **normalized** path so dot-segment traversal cannot ride an exemption. `DISABLE_AUTH=true` bypasses the whole middleware (Electron uses this).
- **Generation pipeline** (trace from `endpoints/presentation_generate.py`): parse inputs (`services/documents_loader.py`, `services/liteparse_service.py`) → outline (`utils/llm_calls/generate_presentation_outlines.py`) → strategy branch (see "Two generation pipelines") → per-slide content (`generate_slide_content.py`, or `author_slide.py` for authored) → images (`services/image_generation_service.py`) → persist (`models/sql/*`) → export (`services/export_task_service.py`, which renders via headless Chromium / the bundled export runtime).
- **Background workers** run **in-process** inside FastAPI via `api/lifespan.py`: presentation-generation dispatcher, template-v2 pptx dispatcher and cleanup, workspace operation runner + outbox dispatcher, workspace cleanup/asset/event maintenance, export retention sweeper. Some can be externalized — see `get_workspace_operation_worker_mode()`.
- **LLM provider abstraction:** `utils/llm_provider.py` + `utils/llm_config.py` wrap the `llmai` library and select among 15 providers (`enums/llm_provider.py`). Image providers are in `enums/image_provider.py` (10, including local ComfyUI/Open WebUI and a `DISABLE_IMAGE_GENERATION` placeholder path). Add provider logic here, not in endpoints.
- **Persistence:** SQLModel models in `models/sql/` (26 modules). Multi-tenant scoping is applied centrally, not per query: `services/database.py:109` installs a `do_orm_execute` hook that applies `with_loader_criteria` to owner-scoped models — do not hand-roll owner filters. Alembic migrations in `alembic/`, run on startup when `MIGRATE_DATABASE_ON_STARTUP=true` (orchestrated by `migrations.py`). `DATABASE_URL` accepts any async SQLAlchemy URL; falls back to SQLite under app data.
- **Errors:** `api/error_handling.py` provides a centralized error envelope with request IDs for HTTPException, validation errors, and unhandled exceptions. Prefer it over ad-hoc try/except in endpoints.
- **Configuration:** `constants/settings_registry.json` is the authoritative registry for managed/public provider settings and generates the shared Compose/docs surfaces via `npm run generate:settings-registry`; CI checks drift with `npm run test:settings-registry`. Runtime reads use `utils/get_env.py`, while some legacy direct `os.getenv` call sites remain outside it. New managed knobs must be registered first and consumed through the existing access layer. Per-instance runtime keys are persisted in `app_data/userConfig.json` (written by `start.js`); they are only writable when `CAN_CHANGE_KEYS != "false"`. Single-admin auth lives in `utils/simple_auth.py`.
- **Runtime settings synchronization:** external API and standalone worker processes share the database-backed monotonic revision described in `docs/runtime-settings-multiworker-sync.md`. PostgreSQL is the cluster profile; SQLite remains a local/multiprocess profile. Do not add request-time file/DB reads or transmit setting values in refresh notifications.

## Next.js frontend internals

- **App Router** under `app/`, grouped by segment: `(presentation-generator)/` holds the dashboard, upload/outline flows, and the slide **editor** at `presentation/page.tsx?id=<uuid>`; `presentation/workspace/` holds the chat-first workbench; `template-v2-studio/[templateId]` is the v2 template editor; `(export)/` and `p/` serve rendering and public share pages. Inline editing components (`PresentationRender.tsx`, `EditableLayoutWrapper.tsx`, `TiptapText.tsx`) live in `(presentation-generator)/components/`.
- **Built-in slide templates:** `app/presentation-templates/` — six groups in this fork: `adaptive`, `comparison-table`, `financial-chart`, `korean-biz`, `org-chart`, `roadmap`. (The upstream `general/`, `Code/`, `Education/`, `Report/`, `pitch-deck/`, `neo-*` groups are **not** present here.) Each layout exports a TSX component (HTML + Tailwind) plus a `Schema` (JSON/Zod) used both for editing forms and for server-side schema extraction (`lib/compile-template-schema.ts`). `layouts.generated.json` is generated by `npm run generate:layout-schemas` (also the `prebuild` hook) — do not hand-edit it.
- **Template-v2 rendering** lives in `lib/`: `template-v2-json-to-html.ts` is now a thin facade over `lib/template-v2-html/`, and slide-editor import is a facade over `components/slide-editor/importing/template-v2/`. Both are accompanied by golden tests. Element and chart discriminators come from the Python Pydantic models through `contracts/template_v2/generate.py`; run `npm run generate:template-v2-contracts` after model changes and keep `compatibility/upstream-compatibility.json` aligned as the reviewed upstream baseline.
- **Backend calls:** typed API clients in `app/(presentation-generator)/services/api/*.ts` call `/api/v1/*`. URL resolution (`utils/api.ts`) and the auth/forwarding middleware (`proxy.ts`) decide same-origin vs. direct-FastAPI based on `FAST_API_INTERNAL_URL`/`NEXT_PUBLIC_FAST_API`. Route handlers under `app/api/*` are thin proxies or serve built-in template/layout data and the bundled export (`/api/export-presentation`).
- **State:** Redux Toolkit in `store/` — slices `presentationGeneration`, `presentationGenUpload`, `userConfig`, `undoRedo`.
- **UI:** Tailwind + shadcn/ui (`components/ui/`, config in `components.json`); rich text via TipTap, charts via Recharts, diagrams via Mermaid. The application chrome is intentionally light-only: there is no appearance theme provider, toggle, or `dark:` contract. Presentation/deck themes (including dark deck designs) are a separate content feature. UI locale conventions are documented in `docs/ui-localization.md`.

## Conventions & gotchas

- Next.js build artifacts go to **`.next-build`**, not `.next` — the Dockerfile and `next.config.mjs` depend on this. (Fidelity tests override it via `PRESENTON_TEST_NEXT_DIST_DIR` to isolate their distDir per run; normal builds must leave it unset.)
- Running FastAPI (directly or via `start.js`) **requires `APP_DATA_DIRECTORY`**; generated files, uploads, fonts, exports, and `userConfig.json` all live under it. Missing it on the direct `server.py` path fails with an unhelpful `TypeError` rather than a named error.
- The PPTX/PDF export runtime is an external, versioned package synced by `scripts/sync-presentation-export.cjs`. If export breaks after a checkout, run `npm run sync:presentation-export` (root) or rebuild the image.
- When changing public LLM/image config knobs, keep four places consistent: `utils/get_env.py`, `start.js` (`setupUserConfigFromEnv`), `docker-compose.yml`, and the env table in `README.md`. This invariant is already violated for many fork-added variables (`SLIDESPEC_*`, `CHAT_FIRST_*`, `AUTHORED_*`, `EXPORT_RETENTION_DAYS`, `CHROME_PATH`) — do not add to the backlog.
- **FastAPI calls back into Next.js over HTTP** to fetch v1 layout schemas (`templates/get_layout_by_name.py` → `{NEXT_INTERNAL_URL}/api/template`). Backend generation cannot complete with the frontend down; `proxy.ts` auth-exempts that route deliberately.
- Keep source files at or under **500 lines** (aim ≤350). `npm run test:file-size-ratchet` enforces this in CI: existing exceptions are frozen in `scripts/file-size-ratchet-baseline.json`, and new or growing exceptions fail. Tests, generated output, vendor code, and migrations are excluded; see `docs/file-size-policy.md`. Prefer extracting when you touch an exception. For the pattern to follow, see `lib/template-v2-html/`, `components/slide-editor/importing/template-v2/`, and `templates/v2/models/elements/` — recent splits where every new module stayed under ~315 lines behind a thin facade.
- Honor the global instructions in the user's `~/.claude/CLAUDE.md` (auto-loaded every session; do not duplicate its contents here).
