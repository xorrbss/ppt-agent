#!/usr/bin/env bash
# Create the admin (first run only), generate a deck through the async route
# with PPTX export, and print the outcome. Usage: run_generation.sh [n_slides]
# BODY='<json>' overrides the request body. With FAKE_LLM_CHART_FIXTURES=1 the
# default body's source text carries the chart fixtures' data (see README).
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
SLIDES="${1:-5}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
CRED='{"username":"admin","password":"fake-llm-harness"}'

curl -s -X POST "$BASE/api/v1/auth/setup" -H 'content-type: application/json' -d "$CRED" >/dev/null || true
curl -s -c "$JAR" -X POST "$BASE/api/v1/auth/login" -H 'content-type: application/json' -d "$CRED" >/dev/null

DEFAULT_BODY="{\"content\":\"사내 업무 자동화 도입 계획\",\"n_slides\":$SLIDES,\"language\":\"Korean\",\"export_as\":\"pptx\",\"common_header_mode\":\"free\"}"
if [ "${FAKE_LLM_CHART_FIXTURES:-}" = "1" ]; then
  # The outline's chart lines are kept only when their values occur in the request
  # source (outline_chart_grounding), so the source carries every fixture's data.
  DEFAULT_BODY="$(python3 - "$(dirname "$0")/../../tests/unit/fixtures/svg_chart_paint_native_examples_v1.json" "$SLIDES" <<'PY'
import json, sys
examples = json.load(open(sys.argv[1], encoding="utf-8"))["examples"]
content = "사내 업무 자동화 도입 계획\n\n" + "\n\n".join(e["requestedSource"]["content"] for e in examples)
print(json.dumps({"content": content, "n_slides": int(sys.argv[2]), "language": "Korean", "export_as": "pptx",
                  "common_header_mode": "free"}, ensure_ascii=False))
PY
)"
fi
BODY="${BODY:-$DEFAULT_BODY}"
ADMISSION=$(curl -s -b "$JAR" -X POST "$BASE/api/v1/ppt/presentation/generate/async" -H 'content-type: application/json' -d "$BODY")
TASK=$(printf '%s' "$ADMISSION" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("id",""))')
if [ -z "$TASK" ]; then echo "admission refused: $ADMISSION" >&2; exit 1; fi
start=$(date +%s)
while :; do
  STATUS=$(curl -s -b "$JAR" "$BASE/api/v1/ppt/presentation/status/$TASK")
  STATE=$(printf '%s' "$STATUS" | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  case "$STATE" in completed|error|failed) break;; esac
  sleep 3
done
echo "generation: $STATE after $(( $(date +%s) - start ))s"
printf '%s' "$STATUS" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("error:",d.get("error"));print("data:",d.get("data"))'
