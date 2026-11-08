#!/usr/bin/env bash
# Admit one more export.pptx workspace operation for an existing deck and wait
# for it. The v5 chart paint verification is budgeted per export, so a deck with
# more charts than one export can verify needs this to verify the rest.
# Usage: run_export.sh <presentation-id> [expected-revision=1]
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
PRESENTATION="${1:?usage: run_export.sh <presentation-id> [expected-revision]}"
REVISION="${2:-1}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
CRED='{"username":"admin","password":"fake-llm-harness"}'

curl -s -c "$JAR" -X POST "$BASE/api/v1/auth/login" -H 'content-type: application/json' -d "$CRED" >/dev/null
KEY="harness-export-$(date +%s%N)"
BODY="{\"command\":\"Export presentation as PowerPoint\",\"command_type\":\"export.pptx\",\"idempotency_key\":\"$KEY\",\"expected_revision\":$REVISION,\"context\":{\"pptx_mode\":\"fidelity\"},\"asset_ids\":[]}"
RECEIPT=$(curl -s -b "$JAR" -X POST "$BASE/api/v1/ppt/workspace/presentations/$PRESENTATION/commands" -H 'content-type: application/json' -d "$BODY")
OPERATION=$(printf '%s' "$RECEIPT" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("operation",{}).get("id") or d.get("operation_id") or "")')
if [ -z "$OPERATION" ]; then echo "export refused: $RECEIPT" >&2; exit 1; fi
echo "export operation: $OPERATION"
start=$(date +%s)
while :; do
  TIMELINE=$(curl -s -b "$JAR" "$BASE/api/v1/ppt/workspace/presentations/$PRESENTATION/timeline")
  STATE=$(printf '%s' "$TIMELINE" | python3 -c '
import json, sys
op = sys.argv[1]
def walk(node):
    if isinstance(node, dict):
        if node.get("operation_id") == op or node.get("id") == op:
            for key in ("state", "status", "public_state"):
                if isinstance(node.get(key), str):
                    return node[key]
        for value in node.values():
            found = walk(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = walk(value)
            if found:
                return found
    return None
print(walk(json.load(sys.stdin)) or "unknown")' "$OPERATION")
  case "$STATE" in succeeded|completed|failed|error|cancelled) break;; esac
  sleep 5
done
echo "export: $STATE after $(( $(date +%s) - start ))s"
