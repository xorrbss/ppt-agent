# Renderer isolation production contract implementation

## 2026-08-13 release-gate completion

승격 기준은 정적 계약이 아니라 두 disposable 관측을 결합한 결과다. Docker
관측은 실제 container inspect와 cgroup/runtime negative probe를, Electron
관측은 실제 renderer OS PID, Linux seccomp/no-new-privileges, secure preference
입력과 Node/Electron bridge 부재를 기록한다. 하나라도 없으면
`promotion_allowed=false`이며 상태는 `INCONCLUSIVE` 또는 `FAIL`이다.

이 Windows 호스트에는 Docker CLI/daemon과 설치된 Linux Electron runtime이 없어
동적 PASS를 생성하지 않았다. 정적 PASS를 승격 근거로 사용해서는 안 된다.
재현 가능한 원격 disposable runner 명령은 다음과 같다.

```powershell
$Commit = (git rev-parse HEAD).Trim()
gh workflow run authored-renderer-security.yml --ref $Commit
$RunId = gh run list --workflow authored-renderer-security.yml --commit $Commit --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $RunId --exit-status
gh run download $RunId --name "authored-renderer-security-$Commit" --dir "artifacts/authored-renderer-security-$Commit"
```

직접 관리하는 disposable Linux VM에서는 Docker Engine, cgroup v2, Chromium
user namespace/seccomp, Node 22, Electron lockfile dependencies, Xvfb와 PowerShell
7을 준비하고 다음 gate를 실행한다. Electron 관측을 생략하면 승격되지 않는다.

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File docs/experiments/authored-vnext/04-renderer-security/harness/run-harness.ps1 `
  -OutputDirectory "$PWD/artifacts/authored-renderer-security" `
  -ElectronObservation "$PWD/artifacts/electron-observation.json" `
  -SourceRevision ((git rev-parse HEAD).Trim())
```

GitHub branch protection에서는
`Authored renderer security / disposable isolation release gate`를 required check로
별도 설정해야 한다. workflow는 외부 branch-protection 상태를 변경하지 않는다.

## 결과

제품 worker 계약은
[`contracts/authored-renderer`](../../../../contracts/authored-renderer)에 연결했다.
network author process는 shell을 사용하지 않는
`AUTHORED_RENDERER_WORKER_COMMAND_JSON` argv로 고정 launcher를 호출하고,
launcher는 newline JSON을 stdin으로 `utils.authored_renderer_worker`에 전달한다.
PNG artifact는 host mount가 아니라 stdout JSON의 `png_base64`로만 돌아온다.

전용 service 계약은 다음을 고정한다.

- UID/GID `65532:65532`, read-only root, `/tmp`와 `/dev/shm`만 tmpfs
- `network_mode: none`, private IPC, published port 없음
- `cap_drop: ALL`, `no-new-privileges`, privileged/device/host mount 없음
- PID 64, memory/swap 512 MiB, CPU 1, nofile 256, worker timeout
- provider key/secret/env file 없음, preload/privileged IPC 없음
- browser sandbox disable/file-access option 없음
- 기본 worker의 Chromium 제어는 loopback port가 아닌 CDP pipe
- `AUTHORED_RENDERER_MODE=isolated`, legacy default-off, 실패 시 downgrade 없음
- Next production runner용 exact OS-isolation attestation
- kill switch 시 새 render 거절 및 last-known-good artifact 유지 계약
- runtime image pull 없음, timeout 시 해당 요청의 고유 이름 container만 제거

## 수행한 host-safe 검증

2026-08-13 Windows host에서 다음만 실행했다.

```powershell
python contracts/authored-renderer/verify_contract.py
uv run --project servers/fastapi pytest servers/fastapi/tests/unit/test_authored_renderer_deployment_contract.py -q
node docs/experiments/authored-vnext/04-renderer-security/tests/verify-corpus.mjs
uv run --project servers/fastapi pytest servers/fastapi/tests/unit/test_renderer_isolation.py -q
Push-Location servers/nextjs
node --test lib/authored-hybrid/security.test.mjs lib/authored-hybrid/renderer-isolation.test.mjs "app/(presentation-generator)/components/authored-render-isolation.test.mjs"
Pop-Location
python docs/experiments/authored-vnext/04-renderer-security/ci/security_gate.py `
  --input docs/experiments/authored-vnext/05-renderer-isolation-implementation/results/product-local-canary-observation.json `
  --output-dir docs/experiments/authored-vnext/05-renderer-isolation-implementation/results/product-local-canary-gate
```

- static contract check: PASS 42/42, `ga_evidence:false`
- focused deployment contract tests: PASS 4/4
- immutable publication/LKG focused tests: PASS 10/10 (including deployment contract)
- FastAPI product isolation + local canary: PASS 28/28
- Next product isolation + safe CDP render: PASS 18/18
- Next cleanup/font/security broad focused: PASS 43, SKIP 1 POSIX-only case on Windows
- authored preview component runtime: PASS 6/6, including Electron PNG-only branch
- reused 04 hostile corpus structural checks: PASS 14 cases + 5 smuggling checks
- local canary host read: attempts 3, blocked 3, violations 0
- local canary page-origin loopback: attempts 3, blocked 3, violations 0
- reused 04 GA gate: INCONCLUSIVE, `promotion_allowed:false`, kill switch triggered

## 미검증 경계

이 host에는 Docker/VM이 없었다. 제품 admission 경계에서 local file/host read와
page-origin loopback 요청은 각각 3/3 차단됐지만, 실제 container의 OS-level
external, metadata, process/IPC, secret-log, resource containment은 실행하지
않았고 `INCONCLUSIVE`로 기록했다. container self-loopback capability와 다른
containment 항목도 미관측이다. static 계약 PASS를 runtime containment PASS로
바꾸지 않았다. 실제 hostile corpus 실행, cloud metadata 요청, fork/OOM,
browser crash payload는 host 안정성 때문에 실행하지 않았다.

기본 browser control이 CDP pipe라는 정적 사실은 self-loopback socket
capability 자체가 0이라는 동적 증거가 아니다. page-origin loopback 요청 0과
OS/container socket capability 0도 disposable worker 관측에서 분리해 기록한다.

배포 승격 전 승인된 disposable Linux worker에서 이미 존재하는 04 harness와
gate를 실행해야 한다. browser sandbox가 해당 worker에서 시작되지 않으면
unsafe flag를 추가하지 말고 격리 worker 호환성 실패로 fail closed 해야 한다.
