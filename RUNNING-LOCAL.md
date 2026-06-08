# Running Presenton locally on Windows

This fork of [Presenton](https://github.com/presenton/presenton) adds a Korean UI and
a few fixes needed to run the **web** and **Electron** versions natively on Windows
(without Docker). It tracks `upstream` = `presenton/presenton`.

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

## Web version (two servers + a reverse proxy)
The browser frontend assumes a **single origin**; in Docker that is nginx. Locally,
run the bundled dependency-free proxy that mirrors `nginx.conf`:

```powershell
# backend (APP_DATA_DIRECTORY + USER_CONFIG_PATH are required)
cd servers/fastapi; uv sync
$env:APP_DATA_DIRECTORY="..\..\app_data"; $env:USER_CONFIG_PATH="..\..\app_data\userConfig.json"
uv run python server.py --port 8000 --reload false

# frontend (USER_CONFIG_PATH is required here too — see note below)
cd servers/nextjs; npm install
$env:FAST_API_INTERNAL_URL="http://127.0.0.1:8000"; $env:USER_CONFIG_PATH="..\..\app_data\userConfig.json"; npm run build; npm run start -- -p 3000

# single-origin proxy (serves http://localhost:5000 -> next:3000 / fastapi:8000 / mcp:8001)
node scripts/presenton-local-proxy.mjs
```
Open http://localhost:5000. First boot shows a one-time admin login (`/api/v1/auth/setup`).

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
runs on system Node.

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
(default `"Korean (한국어)"`), `--template`, `--export pptx|pdf`, `--instructions`,
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
- **CLI / API:** add `--mode authored` (maps to `template:"authored"`). Use `--async`
  for long decks (the synchronous path can hit a ~5-min client header timeout).
  Optional: `--vision-qa` (self-correct flagged slides), and brand tokens
  `--primary-color <hex>` / `--fonts <family>` / `--wordmark <text>`.
  ```powershell
  node scripts/ppt-agent.mjs --content "2026 AI 도입 전략" --mode authored --slides 6 --async --out ./out
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

## Notes
- API keys live in `app_data/userConfig.json` (gitignored) — never committed.
- Next.js build output is `.next-build` (not `.next`).
- Generation+export is synchronous and can take minutes per deck (LLM + headless render);
  authored mode is even longer — use the async endpoint / CLI `--async`.
