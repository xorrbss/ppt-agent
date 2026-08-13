# Current FastAPI authored-renderer baseline

This bounded probe uses only a temporary local file and an ephemeral
`127.0.0.1` HTTP sink. It deliberately exercises the production one-shot
fallback (`file://` plus `--no-sandbox`) without contacting DNS, an external
host, or a cloud metadata address.

Run from the repository root:

```powershell
uv run --project servers/fastapi python docs/experiments/authored-vnext/04-renderer-security/baseline/measure_current_fastapi.py `
  --output docs/experiments/authored-vnext/04-renderer-security/results/current-fastapi-baseline.json

node docs/experiments/authored-vnext/04-renderer-security/baseline/measure_current_next_runner.mjs `
  --output docs/experiments/authored-vnext/04-renderer-security/results/current-next-baseline.json
```

The process/IPC, external-network, and metadata fields remain `inconclusive`
when proving them would require a host-risky escape attempt or a prohibited
external request. The GA gate must fail closed on those fields.
