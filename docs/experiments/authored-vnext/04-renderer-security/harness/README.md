# Hardened zero-egress harness

This harness is an isolated evidence adapter, not a product renderer. It does
not modify a host firewall, proxy, DNS, browser policy, or machine-wide setting.
The container has no host bind mount and is created with:

- non-root UID/GID 65532, read-only root, isolated `/tmp` and `/dev/shm` tmpfs;
- `--network none`, private IPC, all capabilities dropped and
  `no-new-privileges`;
- PID, memory/swap, CPU, file-descriptor, and wall-clock limits;
- Chromium's sandbox left enabled; unsafe sandbox/file-access options cause a
  fail-closed runtime error;
- a `data:` document only, no preload/Electron IPC, and restrictive CSP;
- the structural authored HTML allowlist before any browser launch.

Run from the repository root on a disposable Linux Docker worker:

```powershell
powershell -ExecutionPolicy Bypass -File docs/experiments/authored-vnext/04-renderer-security/harness/run-harness.ps1
```

The script captures the one-shot JSON observation over stdout, invokes the P0 gate,
and returns its exit code. No volume is mounted into the container. A non-PASS
result activates the gate kill switch: stop scheduling new cases, terminate
only the named container, and quarantine its output. Do not weaken the flags to
make Chromium start; a sandbox launch failure is evidence of an incompatible
worker and must remain fail/inconclusive.

The runtime performs only bounded negative probes inside the disposable worker:
reserved `.invalid` DNS, TEST-NET HTTP, the container's loopback discard port,
a metadata-shaped `.invalid` name, an absent fictional host-canary path,
process/IPC admission, synthetic secret-log scanning, and oversized source/DOM/
image/font admission. It also force-terminates two uniquely-created Chromium
process groups to observe crash/timeout cleanup and verifies that their PIDs and
profiles are gone. It never contacts a real metadata endpoint, runs a fork bomb,
or allocates an actual OOM payload.

Host-side `docker inspect` is the authority for zero mounts/devices/provider
secrets/preload, private non-privileged IPC, non-root, read-only root, network
none, dropped capabilities, no-new-privileges, limits, no ports, immutable image
ID, and source-revision label. The runtime separately checks cgroup v2, nofile,
`/proc/self/status`, interfaces, CSP, and active probes. Neither substitutes for
the other. Promotion also requires `-ElectronObservation`; omitting it is
intentionally `INCONCLUSIVE`.

When Docker is absent, only the non-admissible configuration check may run:

```powershell
node docs/experiments/authored-vnext/04-renderer-security/harness/static-self-check.mjs
```

Its JSON says `ga_evidence:false` and `dynamic_harness:NOT_RUN`; it must not be
fed to the GA gate as a passing observation.
