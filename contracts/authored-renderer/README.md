# Authored renderer production isolation contract

This contract runs `utils.authored_renderer_worker` as an OCI worker. The author
process sends exactly one versioned newline-delimited JSON request on stdin and
receives the PNG artifact as base64 in the worker's single JSON stdout response.
There is no HTTP listener, host artifact mount, provider environment, secret,
preload, or privileged IPC surface.

## Framed serve mode (many renders per container)

The same stdin also accepts a **framed** stream: an 8-byte big-endian payload
length followed by that many bytes of JSON, per request and per response. The
launcher and the worker both detect it from the first eight bytes with no
handshake and no argv change — a legacy payload starts with ASCII JSON, whose
first eight bytes are far larger than any admissible frame length — so a
deployment that has not moved to frames keeps the exact one-shot behaviour.
`invoke.py serve` selects the framed protocol explicitly for manual use.

In framed mode `invoke.py` starts **one** container for the whole stream and
proxies frames to it in lock step, and the worker keeps **one** Chromium alive
and serves requests until stdin EOF, `max_requests` renders (sent on the first
request, default 200), or the first failure. Each response carries the same
fields as the single-shot protocol plus `request_ordinal`. The parent gates this
on `AUTHORED_RENDERER_FRAMED_PROTOCOL_ENABLED` (default off) and opens at most
`AUTHORED_RENDERER_LEASE_POOL_SIZE` containers per deck scope.

Security posture, stated exactly: framed mode uses the **same image, the same
compose service, `network_mode: none`, read-only root, non-root uid, dropped
capabilities and seccomp profile**, and the same tmpfs `/tmp` and `/dev/shm`.
The one property that changes is the *lifetime* of that tmpfs and of the browser
process: per container instead of per render. Three rules keep the boundary
meaningful under reuse.

1. **Fresh page state per request.** Every request gets a new disposable CDP
   browser context and target, disposed before the response is written; no
   cookie jar, storage, cache entry, or JavaScript context outlives a request.
2. **One deck scope per container.** The parent's lease keys sessions by an
   opaque `deck_scope` and never routes two scopes to one process, so a reused
   container is never shared between tenants or decks.
3. **Retire on error.** A failed render, protocol violation, or timeout kills
   the container and the lease; the next slide pays a cold start rather than
   inheriting a possibly poisoned browser.

A per-request watchdog still bounds each exchange to `timeout +
STARTUP_GRACE_SECONDS` and force-removes the container when it expires.

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

On an approved disposable Linux runner with Docker Engine and cgroup v2, run
the repository-contained dynamic gate:

```bash
python contracts/authored-renderer/run_dynamic_gate.py \
  --output-dir "$PWD/artifacts/authored-renderer-security" \
  --source-revision "$(git rev-parse HEAD)"
```

The command exits nonzero unless every OCI, browser, deployment, and resilience
observation passes. The Electron half of this gate was retired with the desktop
shell, so the container observation is now the whole of the evidence and decides
promotion on its own.
