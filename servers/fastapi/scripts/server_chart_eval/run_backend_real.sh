#!/usr/bin/env bash
# Start the real-provider (Anthropic) backend on $PORT (default 8000) with a given app-data dir.
# Usage: scripts/server_chart_eval/run_backend_real.sh <app-data-dir>   (any cwd)
#
# This is scripts/fake_llm_e2e/run_backend.sh with FAKE_LLM_CLIENTS=0, so the only difference from the
# fake harness is that the real Anthropic client answers. The key is read from
# $PRESENTON_ANTHROPIC_API_KEY and handed to the backend process only (never printed or written).
# Chart knobs pass through the environment, read at admission:
#   SVG_SERVER_CHARTS=on|off   SVG_NATIVE_CHART_INTEGRITY_V5=on|off
set -euo pipefail

APP="${1:?usage: run_backend_real.sh <app-data-dir>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${PRESENTON_ANTHROPIC_API_KEY:?PRESENTON_ANTHROPIC_API_KEY is not set}"

MODEL="claude-sonnet-5-5"
# ASSUMED PRICES, NOT A PUBLISHED RATE CARD: plausible Sonnet pricing, $3 / $15 per million input /
# output tokens (microunits = 1e-6 USD), cached-input read 10% of input, cache write 125% of input.
# The backend's pricing gate needs a priced model; the harness cost figures are only as accurate as
# these numbers. Change them here (and the revision string) if the real rate differs.
PRICING_REVISION="server-chart-eval-assumed-20261109"

export FAKE_LLM_CLIENTS=0 FAKE_LLM_LOG_PROVIDER_ERRORS=1
export LLM=anthropic ANTHROPIC_MODEL="$MODEL" ANTHROPIC_API_KEY="$PRESENTON_ANTHROPIC_API_KEY"
unset PRESENTON_ANTHROPIC_API_KEY
export PUBLIC_PROVIDER_PRICING_REVISION="$PRICING_REVISION"
export PUBLIC_PROVIDER_PRICING_CATALOG="{\"schemaVersion\":\"public-provider-pricing-catalog/v1\",\"revision\":\"$PRICING_REVISION\",\"entries\":[{\"providerId\":\"anthropic\",\"modelId\":\"$MODEL\",\"inputMicrounitsPerMillionTokens\":3000000,\"outputMicrounitsPerMillionTokens\":15000000,\"cachedInputMicrounitsPerMillionTokens\":300000,\"cacheWriteInputMicrounitsPerMillionTokens\":3750000,\"currency\":\"USD\"}]}"

cd "$HERE/../.."
exec bash scripts/fake_llm_e2e/run_backend.sh "$APP"
