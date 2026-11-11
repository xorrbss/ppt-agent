#!/usr/bin/env bash
# Start the fake-LLM backend on :8000 with a throwaway app-data directory.
# Usage: scripts/fake_llm_e2e/run_backend.sh <app-data-dir>   (run from servers/fastapi)
# See README.md for what this proves and which settings it fakes.
set -euo pipefail

APP="${1:?usage: run_backend.sh <app-data-dir>}"
APP="$(mkdir -p "$APP" && cd "$APP" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
FASTAPI="$(cd "$HERE/../.." && pwd)"
PY="$FASTAPI/.venv/bin/python"
REPO="$(cd "$FASTAPI/../.." && pwd)"

mkdir -p "$APP/public-artifacts"
[ -f "$APP/public-worker-capability.key" ] || "$PY" -c "import os,sys;open(sys.argv[1],'wb').write(os.urandom(32))" "$APP/public-worker-capability.key"

export APP_DATA_DIRECTORY="$APP" USER_CONFIG_PATH="$APP/userConfig.json"
export DATABASE_URL="${FAKE_LLM_DATABASE_URL:-sqlite+aiosqlite:///$APP/fake-llm.db}"
export MIGRATE_DATABASE_ON_STARTUP=true DISABLE_IMAGE_GENERATION=true ENVIRONMENT=local

# The fake replaces every client, so provider and key are placeholders. The
# pricing gate still refuses an unpriced model, hence a zero-rate entry.
# With FAKE_LLM_CLIENTS=0 the caller supplies LLM, the provider key/model and
# the pricing catalog for a real-provider run instead.
if [ "${FAKE_LLM_CLIENTS:-1}" != "0" ]; then
  export LLM=anthropic ANTHROPIC_API_KEY=fake-llm-harness-not-a-key ANTHROPIC_MODEL=claude-sonnet-4-20250514
  export PUBLIC_PROVIDER_PRICING_REVISION=fake-llm-harness
  export PUBLIC_PROVIDER_PRICING_CATALOG='{"schemaVersion":"public-provider-pricing-catalog/v1","revision":"fake-llm-harness","entries":[{"providerId":"anthropic","modelId":"claude-sonnet-4-20250514","inputMicrounitsPerMillionTokens":0,"outputMicrounitsPerMillionTokens":0,"currency":"USD"}]}'
fi

# Local artifact store and isolated workers (see README: "settings it needs").
export PUBLIC_ARTIFACT_STORE_BACKEND=local-development
export PUBLIC_WORKER_OBJECT_STORE_ROOT="$APP/public-artifacts"
export PUBLIC_WORKER_CAPABILITY_KEY_FILE="$APP/public-worker-capability.key"
export PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY="${PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY:-$("$PY" -c 'import secrets;print(secrets.token_urlsafe(48))')}"
export PUBLIC_EXPORT_WORKER_COMMAND_JSON="[\"$PY\",\"-m\",\"workers.public_export_worker\"]"
# Development-only owned local worker: linux-local on Linux, windows-local on Windows.
case "$(uname -s)" in
  Linux) export PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=linux-local ;;
  *) export PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=windows-local ;;
esac
export PUBLIC_TRUSTED_WORKER_LOCAL_POLICY=development

# Static Noto instances PPTX font embedding loads instead of instancing per
# export (the Docker image builds them; a no-op once built here).
"$PY" "$FASTAPI/scripts/build_svg_pptx_font_instances.py" >/dev/null

cd "$FASTAPI"
exec "$PY" "$HERE/fake_llm_backend.py"
