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
- **PPTX/PDF export fix** — `servers/fastapi/utils/export_utils.py` seeds the export
  session cookie via the Next.js proxy so the headless render authenticates (modern
  Chromium drops CDP-set `Cookie` headers).
- **Electron dev fixes** (`electron/app/`) — use system Node for LiteParse, disable
  uvicorn `--reload`, raise server-readiness timeout to 300s.

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

# frontend
cd servers/nextjs; npm install
$env:FAST_API_INTERNAL_URL="http://127.0.0.1:8000"; npm run build; npm run start -- -p 3000

# single-origin proxy (serves http://localhost:5000 -> next:3000 / fastapi:8000 / mcp:8001)
node scripts/presenton-local-proxy.mjs
```
Open http://localhost:5000. First boot shows a one-time admin login (`/api/v1/auth/setup`).

## Electron version
```powershell
cd electron
npm run setup:env      # electron deps + uv sync + nextjs npm install + export runtime
# npm run dev uses `rm -rf` (Unix); on Windows run the steps directly:
node_modules\.bin\tsc.cmd
node_modules\.bin\electron.cmd . --no-sandbox
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

## Notes
- API keys live in `app_data/userConfig.json` (gitignored) — never committed.
- Next.js build output is `.next-build` (not `.next`).
- Generation+export is synchronous and can take minutes per deck (LLM + headless render).
