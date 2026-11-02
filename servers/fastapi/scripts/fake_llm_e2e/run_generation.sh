#!/usr/bin/env bash
# Create the admin (first run only), generate a deck through the async route
# with PPTX export, and print the outcome. Usage: run_generation.sh [n_slides]
# BODY='<json>' overrides the request body.
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
SLIDES="${1:-5}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
CRED='{"username":"admin","password":"fake-llm-harness"}'

curl -s -X POST "$BASE/api/v1/auth/setup" -H 'content-type: application/json' -d "$CRED" >/dev/null || true
curl -s -c "$JAR" -X POST "$BASE/api/v1/auth/login" -H 'content-type: application/json' -d "$CRED" >/dev/null

DEFAULT_BODY="{\"content\":\"사내 업무 자동화 도입 계획\",\"n_slides\":$SLIDES,\"language\":\"Korean\",\"export_as\":\"pptx\",\"common_header_mode\":\"free\"}"
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
