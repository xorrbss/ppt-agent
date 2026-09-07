# Running Presenton locally on Windows

This fork of [Presenton](https://github.com/presenton/presenton) adds a Korean UI and
a few fixes needed to run the **web** and **Electron** versions natively on Windows
(without Docker). Its authoritative upstream is the official Presenton project
(`presenton/presenton`). The configured `origin` remote and local worktree branches
are collaboration and delivery copies, not upstream sources of truth.

> The git history was slimmed: large regenerable artifacts (PyInstaller builds, the
> FastEmbed/ONNX model caches, downloaded export runtimes) are **not** committed.
> They are fetched/built on first run, so a fresh clone needs network access the
> first time the backend starts.

## Changes in this fork
- **Korean UI** — app chrome (nav, dashboard, upload, outline, settings, auth,
  onboarding, editor, toasts) translated to Korean. API values, provider/model IDs
  and logic literals are kept in English.
- **Default presentation language = Korean** (`upload/components/UploadPage.tsx`).
- **Offline Korean OCR** — bundled Tesseract models live in `tessdata/`
  (`eng.traineddata`, `kor.traineddata`). `LiteParseService` auto-detects this
  directory, so scanned-PDF OCR works without a CDN. Override with
  `LITEPARSE_TESSDATA_PATH` to add languages.
- **PPTX/PDF export fix** — `servers/fastapi/utils/export_utils.py` seeds the export
  session cookie via the Next.js proxy so the headless render authenticates (modern
  Chromium drops CDP-set `Cookie` headers).
- **Electron dev fixes** (`electron/app/`) — use system Node for LiteParse, disable
  uvicorn `--reload`, raise server-readiness timeout to 300s.
- **Cross-platform npm scripts** — `electron/package.json` `dev`/`build:*`/`clean:build`
  use `node scripts/rmrf.cjs` instead of Unix `rm -rf`, so `npm run dev` runs natively
  on Windows.

## Prerequisites
Node LTS, Python 3.11, [uv](https://docs.astral.sh/uv/). For export/parsing:
LibreOffice + ImageMagick + a Chrome/Chromium (the Electron app installs these on
first run; for the web flow install them yourself).

## Web version (supervised local stack)

The browser frontend assumes a **single origin**; in Docker that is nginx. Locally
that is the bundled dependency-free proxy, which mirrors `nginx.conf`. The stack
is five processes around a persistent PostgreSQL, and `scripts/start-local-stack.ps1`
owns all of them:

```powershell
# first time only: install dependencies
cd servers/fastapi; uv sync
cd ../nextjs; npm install

# bring the whole stack up (PostgreSQL -> FastAPI -> controllers -> Next -> proxy)
.\scripts\start-local-stack.ps1

.\scripts\start-local-stack.ps1 -Status   # what is running
.\scripts\start-local-stack.ps1 -Stop     # stop it, in reverse order
```

The script starts each component with the environment it needs, waits for each
one to answer before starting the next, and reports on every start how far the
database schema is behind the code. Its environment lives in a DPAPI-protected
store under `app_data/public-v2-local/`, seeded once with
`-ImportEnvironment <directory>`; a stack that is already running can be handed
over with `-Adopt`.

**Do not start these processes by hand.** Doing so was how the local stack came
to be a hybrid of this script and `start-public-v2-rc.ps1` that neither could
restart or stop: the environment existed only inside the running processes, and
the schema drifted six migrations behind the code without anything saying so.

`start-public-v2-rc.ps1` is a different tool: it creates a throwaway PostgreSQL
under the temp directory for RC verification, on ports 8010/3010/5110. It is not
for daily work, and the database it makes does not survive the run.

Open http://localhost:5000. First boot shows a one-time admin login (`/api/v1/auth/setup`).

Startup migrations should remain enabled for normal native runs. Re-running the
backend is safe: Alembic upgrades an older database to the current head and a
subsequent startup is a no-op. Set `MIGRATE_DATABASE_ON_STARTUP=false` only for a
controlled workflow that applies migrations separately.

### Authored renderer: the unattested local native profile

`npm run start` runs with `NODE_ENV=production`, and production authored
rendering normally demands the isolated renderer worker described in
`contracts/authored-renderer/` — a non-root, read-only, tmpfs, network-none
container. A Windows desktop cannot supply that, so authored slides would all
fall back to images and the backend would reject the export as
`authored_hybrid_export_not_editable`.

For a single-user local run, declare the absence of that guarantee explicitly:

| Variable | Value |
|---|---|
| `AUTHORED_RENDERER_OS_ISOLATION` | `local-native-unattested` |
| `AUTHORED_RENDERER_EXECUTION_BOUNDARY` | `local-native` |
| `AUTHORED_VNEXT_RENDERER_ENABLED` | `true` |

Both of the first two are required: declaring one alone fails closed, so a real
deployment cannot drift into the local profile by setting a stray variable. The
CSP, CDP-pipe input, network abortion, and browser sandbox are unchanged — only
the OS-level isolation attestation is waived, and every export produced this way
records `rendererIsolation.osIsolation = "local-native-unattested"` with
`attested: false` in its `.quality.json` sidecar and API response.

**Never set these in a shared or hosted deployment.** Run the isolated worker
there instead (`AUTHORED_RENDERER_OS_ISOLATION=nonroot-readonly-tmpfs-network-none`
with `AUTHORED_RENDERER_EXECUTION_BOUNDARY=external-worker`).
`AUTHORED_RENDERER_KILL_SWITCH=true` still disables the renderer in either mode.

### Optional MCP server

MCP is opt-in. For a direct native run, starting `mcp_server.py` is the explicit
opt-in (use a separate terminal):

```powershell
cd servers/fastapi
uv run python mcp_server.py --port 8001 --reload false
```

The local proxy binds to loopback and can route `/mcp` to that process. Docker's
`start.js` does not start MCP unless `ENABLE_MCP_SERVER=true`; its production MCP
process always uses `--reload false`. Docker nginx authenticates both `/mcp` and
`/mcp/` with the Presenton session cookie or an admin API-key bearer token before
forwarding the request and credential to MCP. The native proxy does not reproduce
that nginx authentication gate, so keep it loopback-only. Never combine an
externally reachable MCP endpoint with `DISABLE_AUTH=true`.

The bundled PPTX/PDF exporter must render through the single-origin proxy because
authored slides load `/app_data` images. It uses `NEXT_PUBLIC_URL`, then
`NEXT_INTERNAL_URL`, and otherwise falls back to `http://127.0.0.1:5000` (or the
frontend process's `PROXY_PORT`).
The exporter also receives `APP_DATA_DIRECTORY`; for compatibility with older
native commands it derives that directory from `USER_CONFIG_PATH` when omitted.

> **`USER_CONFIG_PATH` must be set on the Next.js process too, not only the backend.**
> The route handlers `/api/user-config` and `/api/can-change-keys` read
> `process.env.USER_CONFIG_PATH` directly from the Next process. Omit it and the
> frontend gets an empty config → `hasValidLLMConfig` fails (no `LLM` / no image
> provider) → `ConfigurationInitializer` treats the config as invalid and pushes
> dashboard routes to `/`, where `AuthGate` then `replace()`s to `/upload`.
> Symptom: deep-linking a dashboard route (e.g. `/theme`) bounces to `/upload` and
> never renders. The same applies to `npm run dev` and to headless verification
> (Chromium/CDP screenshots) — start Next with `USER_CONFIG_PATH` set.

## Electron version
```powershell
cd electron
npm run setup:env      # electron deps + uv sync + nextjs npm install + export runtime
npm run dev            # cleans app_dist, runs tsc, launches Electron (cross-platform)
```
Electron runs with `DISABLE_AUTH=true` (no login). Provider keys are set in-app
(Settings) or via env at launch (`LLM`, `OPENAI_API_KEY`, `OPENAI_MODEL`, …).
On Windows dev, set `PRESENTON_DEV_NODE_BINARY` to your system `node.exe` so LiteParse
runs on system Node. Electron enables `MIGRATE_DATABASE_ON_STARTUP` for both the
native development server and the packaged FastAPI binary, and keeps backend reload
disabled.

## Automation: `scripts/ppt-agent.mjs`
A zero-dependency Node 18+ CLI that drives deck generation over the API
(`POST /api/v1/ppt/presentation/generate`). Defaults to Korean.

```powershell
# single deck (Electron / DISABLE_AUTH backend — no credentials):
node scripts/ppt-agent.mjs --content "인공지능 개요와 활용 사례" --slides 3 --out ./out

# web/Docker with HTTP Basic auth:
node scripts/ppt-agent.mjs --content "회사 소개" --user admin --password s3cret --base http://localhost:5000 --out ./out

# batch (one topic per line; '#' comments skipped; continues on failure):
node scripts/ppt-agent.mjs --batch topics.txt --export pdf --out ./decks
```
Key flags: `--content` / `--batch`, `--slides <n|auto>` (default 8), `--language`
(default `"Korean (한국어)"`), `--template`, `--style <id>` (authored-mode style preset), `--export pptx|pdf`, `--instructions`,
`--tone`, `--verbosity`, `--web-search`, `--toc`, `--no-title`, `--base`,
`--user`/`--password`, `--out <dir>`, `--timeout <sec>`. `--flag=value` also works.
Verified end-to-end: a 3-slide Korean PPTX (valid OOXML) generated and downloaded.

## AI 저작(authored) 고품질 모드

A high-quality generation mode where the **model authors a bespoke HTML layout per
slide** (instead of filling a fixed React template), which is rendered to images and
assembled into an **image-per-slide PPTX/PDF**. Decks are **view-only in-app** — edit
the exported PPTX in PowerPoint. The default adaptive/template path is unchanged.

- **Web UI:** on the upload or outline screen, pick the **"AI 저작 (고품질)"** template
  card, then generate. Generation runs async (minutes) with a progress overlay and opens
  the viewer when done.
- **CLI / API:** add `--mode authored` (maps to `template:"authored"`). Use `--style <id>`
  to select an authored style preset (sent as `authored_style`), and `--async` for long decks
  (the synchronous path can hit a ~5-min client header timeout). Optional: `--vision-qa`
  (self-correct flagged slides), and brand tokens
  `--primary-color <hex>` / `--fonts <family>` / `--wordmark <text>`.
  ```powershell
  node scripts/ppt-agent.mjs --content "2026 AI 도입 전략" --mode authored --style strategic-navy --slides 6 --async --out ./out
  ```
- **스타일 카탈로그 API:** `GET /api/v1/ppt/authored/styles`는 UI와 자동화에서 쓸 공개
  메타데이터만 반환하며(생성 브리프는 반환하지 않음), `--style <id>`는 이 응답의 `id`를
  `authored_style`로 전송합니다. 스타일을 생략하면 서버 기본값을 사용하고, `--style`만
  쓰고 값을 생략하면 CLI가 요청 전에 오류로 종료합니다.
- **최신 `main` 반영 후 스택 재기동:**
  ```powershell
  .\scripts\start-local-stack.ps1 -Stop
  git fetch origin; git switch main; git pull --ff-only origin main
  .\scripts\start-local-stack.ps1
  ```
  백엔드에는 리로더가 없으므로 코드를 받은 뒤 반드시 재기동해야 반영됩니다. 기동 로그가
  스키마가 코드보다 몇 개 밀려 있는지 알려주니, 마이그레이션이 필요한 변경을 받았는지
  거기서 확인합니다.
- **30종 카탈로그 확인:** web/Docker 인증이 켜진 경우 `-Credential`에 관리자 계정을
  전달합니다. 인증이 꺼진 Electron 백엔드는 `$credential` 줄과 `-Credential` 인수를
  생략합니다. 결과가 정확히 `30`이고, `strategic-navy` 같은 반환 `id`만 `--style`에
  사용합니다.
  ```powershell
  $base = "http://localhost:5000"
  $credential = Get-Credential
  $styles = Invoke-RestMethod "$base/api/v1/ppt/authored/styles" -Credential $credential
  $styles.Count
  $styles.id | Sort-Object
  ```
- **라이브 QA:** 실제 설정된 로컬 LLM과 Chrome을 사용하므로 외부 API 키가 필요하며
  완료까지 수 분이 걸릴 수 있습니다. 생성 뒤 출력된 edit URL을 열어 각 슬라이드의
  렌더링을 확인하고, `./out`의 PPTX도 PowerPoint에서 열어 확인합니다.
  ```powershell
  node scripts/ppt-agent.mjs --content "2026 AI 도입 전략" --mode authored --style strategic-navy --slides 3 --async --base http://localhost:5000 --out ./out
  ```
- **Rendering needs a headless Chrome.** Local Windows uses installed Chrome
  (auto-detected; override with `CHROME_PATH`). The **Docker images already bundle
  `chromium`** and set `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium`, which the renderer
  uses — so authored mode works in the standard Docker deploy out of the box. If no
  Chrome is found, slides render as blank placeholders and a warning is logged.
- **Provider:** runs on the configured LLM provider (any). Quality is strongest with a
  design-capable model (e.g. `anthropic` + key); `codex` also produces frontier-grade
  output. Switching is config-only (no code change).
- **Concurrency knobs (per deploy):** `AUTHORED_RENDER_CONCURRENCY` (default 4) bounds
  total concurrent headless-chrome processes process-wide; `AUTHORED_AUTHOR_CONCURRENCY`
  (default 5) bounds concurrent authoring calls. Lower them (e.g. 2) on low-RAM hosts.
- `AUTHORED_HYBRID_CONCURRENCY` overrides hybrid PPTX slide concurrency; its default is `clamp(4, os.cpus().length - 2, 8)`.
- `EXPORT_RETENTION_DAYS` controls export retention by last access (default 7 days); `EXPORT_SWEEP_INTERVAL_SECONDS` controls the startup/background sweep interval (default 3600 seconds), and files modified within the last hour are always retained.

## Editable (byte) PPTX/PDF export on Windows

The `adaptive`/template paths export a fully-editable PPTX by DOM-walking the rendered
`/pdf-maker` page through the external `presentation-export` runtime. On Windows this
needs two things the prebuilt runtime doesn't fully ship (authored mode is unaffected —
it assembles an image PPTX in pure Python):

- **Converter binary** — `npm run sync:presentation-export` now downloads the archive for
  the current OS/arch (`export-Windows-X64.zip` → `presentation-export/py/convert-win32-x64.exe`).
  If you synced before this fix, re-run with `--force`.
- **`sharp` native binary** — the runtime bundles sharp's JS but not its libvips native
  addon. `npm run sync:presentation-export` now installs it automatically on Windows/macOS
  (matching the bundle's sharp version). If that step is skipped (e.g. offline), install it
  manually: `npm install --prefix presentation-export --no-save sharp@0.34.4`.
- **Chrome** — the runtime renders via puppeteer. The backend now auto-points it at the
  detected system Chrome (same as authored mode; override with `CHROME_PATH`/
  `PUPPETEER_EXECUTABLE_PATH`), so puppeteer won't try to download its own.

Verified end-to-end on Windows: adaptive and korean-biz decks export to valid multi-slide
editable `.pptx` (opens in PowerPoint).

## Notes
- API keys live in `app_data/userConfig.json` (gitignored) — never committed.
- Next.js build output is `.next-build` (not `.next`).
- Generation+export is synchronous and can take minutes per deck (LLM + headless render);
  authored mode is even longer — use the async endpoint / CLI `--async`.
