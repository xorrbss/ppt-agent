# Authored renderer production isolation contract

This contract runs `utils.authored_renderer_worker` as a one-shot OCI worker.
The author process sends exactly one versioned newline-delimited JSON request on stdin and
receives the PNG artifact as base64 in the worker's single JSON stdout response.
There is no HTTP listener, host artifact mount, provider environment, secret,
preload, or privileged IPC surface.

The worker service is non-root, read-only, `network_mode: none`, private-IPC,
capability-free, and constrained by `/tmp` and `/dev/shm` tmpfs, PID, memory, CPU, file-descriptor,
and wall-clock limits. Chromium control uses a CDP pipe, not a loopback debugging
port. Sandbox failures are errors; the contract never adds `--no-sandbox` or
file-access flags.

The service applies `chromium-seccomp.json`, a fail-closed profile derived from
the pinned Moby `seccomp/v0.2.1` default. It permits the namespace and mount
syscalls Chromium needs to enter its own Linux sandbox while every container
capability remains dropped and `no-new-privileges` remains enabled. The profile
is included in the deployment contract digest and is never replaced by
`seccomp=unconfined`.

## Deployment

Build the immutable image from the exact product revision before starting the
author service:

```powershell
$Revision = (git rev-parse HEAD).Trim()
$env:AUTHORED_RENDERER_SOURCE_REVISION = $Revision
$Attestation = python contracts/authored-renderer/attestation.py | ConvertFrom-Json
$env:AUTHORED_RENDERER_SOURCE_TREE_DIGEST = $Attestation.AUTHORED_RENDERER_SOURCE_TREE_DIGEST
$env:AUTHORED_RENDERER_CONTRACT_DIGEST = $Attestation.AUTHORED_RENDERER_CONTRACT_DIGEST
docker compose -f contracts/authored-renderer/compose.authored-renderer.json build authored-render-worker
$ImageId = (docker image inspect presenton-authored-render-worker:local --format '{{.Id}}').Trim()
$env:AUTHORED_RENDERER_IMAGE_REF = $ImageId
$Deployment = python contracts/authored-renderer/attestation.py `
  --image-id $ImageId --source-revision $Revision | ConvertFrom-Json
$env:AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION = $Deployment.AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION
```

Production must use the observed immutable `sha256:<64>` image ID or a registry
`name@sha256:<64>` digest and the exact 40-character source revision. Mutable
tags and `unknown` revisions are rejected in production-strict mode. The
dedicated `authored-renderer-runtime` target contains Chromium and the worker
only; it does not inherit the monolithic server entrypoint or author environment.

Configure the FastAPI author process with a JSON argv array. It must spawn this
without a shell:

```powershell
$env:AUTHORED_RENDERER_MODE = "isolated"
$env:AUTHORED_RENDERER_OS_ISOLATION = "nonroot-readonly-tmpfs-network-none"
$env:AUTHORED_RENDERER_EXECUTION_BOUNDARY = "external-worker"
$env:AUTHORED_RENDERER_KILL_SWITCH = "false"
$env:AUTHORED_RENDERER_LEGACY_ENABLED = "false"
$env:AUTHORED_RENDERER_WORKER_COMMAND_JSON = ConvertTo-Json -Compress @(
  (Get-Command python).Source,
  "C:\deploy\ppt-agent\contracts\authored-renderer\invoke.py"
)
```

On Linux use an absolute Python executable and repository path, for example
`["/usr/bin/python3","/opt/presenton/contracts/authored-renderer/invoke.py"]`.
The launcher forwards neither provider variables nor arbitrary argv into the
container. The production command is mandatory; an absent container runtime,
missing prebuilt image, timeout, malformed result, or non-zero worker exit is a
fail-closed render error. It does not retry through legacy rendering.
Runtime pulls are disabled. On timeout the launcher force-removes only the
uniquely named worker container created for that request.
`TEMPLATE_V2_DEPLOYMENT_TIER=production` makes the FastAPI boundary reject a
missing command or a command whose executable path is not absolute.
The exact OS-isolation attestation is also required by the direct Next
production runner; absence or any other value is fail closed.

The input budget accepts up to 2 MiB of UTF-8 HTML. The wire budget is larger
because JSON escaping can expand a valid HTML string; it is not a larger HTML
or DOM allowance.

Do not mount the Docker socket into the monolithic Presenton container. Deploy
the network author process and this launcher at an orchestrator boundary that
can create only this fixed worker specification. A deployment that cannot
provide that boundary must leave authored rendering unavailable.

The legacy path requires both `AUTHORED_RENDERER_MODE=legacy` and
`AUTHORED_RENDERER_LEGACY_ENABLED=true`; it is never an automatic fallback.
Setting `AUTHORED_RENDERER_KILL_SWITCH=true` rejects new work and preserves the
previously published/last-known-good artifact in the product service.

## Verification

Host-safe static verification:

```powershell
python contracts/authored-renderer/verify_contract.py
uv run --project servers/fastapi pytest servers/fastapi/tests/unit/test_authored_renderer_deployment_contract.py
```

Static success is not runtime or GA evidence. Run the 04 hostile corpus and gate
only in an approved disposable Linux Docker/VM worker. Missing Docker/VM
observations remain `INCONCLUSIVE`; never translate a static PASS into dynamic
containment evidence.

On an approved disposable Linux runner with Docker Engine, cgroup v2, and a
real Electron observation, run the repository-contained dynamic gate:

```bash
python contracts/authored-renderer/run_dynamic_gate.py \
  --output-dir "$PWD/artifacts/authored-renderer-security" \
  --electron-observation "$PWD/artifacts/electron-observation.json" \
  --source-revision "$(git rev-parse HEAD)"
```

The command exits nonzero unless every OCI, browser, deployment, resilience,
and Electron observation passes. Omitting `--electron-observation` is permitted
for OCI diagnosis only and remains `INCONCLUSIVE` for promotion.
