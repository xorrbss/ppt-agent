# Authored-only vNext renderer P0 security gate

현재 결론은 **GA 차단(FAIL)** 이다. local-only baseline에서 FastAPI renderer와
Next 저수준 hybrid runner가 각각 work directory 밖의 임시 canary file을 읽었다.
FastAPI renderer는 ephemeral `127.0.0.1` sink에도 1회 요청하고 응답을
읽었다. 실제 외부 DNS/HTTP 및 metadata endpoint는 호출하지 않았다.

## 확인 결과

| P0 기준 | 현행 결과 | GA 판정 |
|---|---|---|
| host read 0 | FastAPI 1, Next low-level runner 1 | FAIL |
| external egress 0 | 실제 외부 호출 금지로 미관측 | INCONCLUSIVE |
| loopback egress 0 | FastAPI 1, Next deny proxy 0 | FAIL |
| metadata egress 0 | 실제 metadata 호출 금지로 미관측 | INCONCLUSIVE |
| child/process 및 privileged IPC 0 | host-safe 정책상 미실행 | INCONCLUSIVE |
| secret log 0 | probe 결과 marker 0, OS/browser 전체 log 미추적 | INCONCLUSIVE |
| resource escape 0 | 위험 payload 미실행, Docker 동적 harness 미실행 | INCONCLUSIVE |

현재 machine-readable gate 결과는 `FAIL`, `promotion_allowed:false`,
`kill_switch.triggered:true`이며 host read 2, loopback egress 1, 미관측 check
20개다.

## 산출물

- `evidence/runtime-boundaries.md`: FastAPI, Next, iframe, Electron
  실행 옵션/file/network/process/IPC 추적 및 위험 옵션 제거 회귀 가설
- `evidence/verified-baseline.md`: 메인이 실행한 local-only baseline과 안전 제한
- `corpus/manifest.json`: 모두 실행 금지된 14개 hostile semantic fixture
- `policy/html-policy.mjs`: 구조적 HTML tree + tag/attribute/URL/CSS allowlist
- `harness/`: zero-egress Docker harness와 비동적 static self-check
- `ci/security_gate.py`: fail-closed JSON/JUnit/SARIF GA gate
- `results/current-fastapi-baseline.json`, `current-next-baseline.json`: 원시 계측
- `results/current-baseline-gate/`: 현행 baseline JSON/JUnit/SARIF 판정

## 재현 명령

저장소 루트에서 실행한다.

```powershell
# 안전한 정적 corpus/allowlist 확인
node docs/experiments/authored-vnext/04-renderer-security/tests/verify-corpus.mjs

# local-only 현행 경로 계측 (외부 목적지 없음)
uv run --project servers/fastapi python docs/experiments/authored-vnext/04-renderer-security/baseline/measure_current_fastapi.py --output docs/experiments/authored-vnext/04-renderer-security/results/current-fastapi-baseline.json --timeout 20
node docs/experiments/authored-vnext/04-renderer-security/baseline/measure_current_next_runner.mjs --output docs/experiments/authored-vnext/04-renderer-security/results/current-next-baseline.json

# 현행 결과를 P0 gate에 연결 (현재 기대 exit 1/FAIL)
python docs/experiments/authored-vnext/04-renderer-security/ci/baseline_adapter.py --fastapi docs/experiments/authored-vnext/04-renderer-security/results/current-fastapi-baseline.json --next docs/experiments/authored-vnext/04-renderer-security/results/current-next-baseline.json --output docs/experiments/authored-vnext/04-renderer-security/results/current-baseline-gate-input.json
python docs/experiments/authored-vnext/04-renderer-security/ci/security_gate.py --input docs/experiments/authored-vnext/04-renderer-security/results/current-baseline-gate-input.json --output-dir docs/experiments/authored-vnext/04-renderer-security/results/current-baseline-gate

# Docker가 없는 host에서 configuration만 확인(GA evidence 아님)
node docs/experiments/authored-vnext/04-renderer-security/harness/static-self-check.mjs

# disposable Linux Docker worker에서 dynamic harness + gate
powershell -ExecutionPolicy Bypass -File docs/experiments/authored-vnext/04-renderer-security/harness/run-harness.ps1
```

Gate exit code는 PASS `0`, FAIL `1`, INCONCLUSIVE `2`, malformed/error `3`이다.
PASS 이외에는 모두 승격을 막는다.

## Kill switch

다음 조건 하나라도 발생하면 신규 case 예약을 중단하고, 실행 중인 **해당
이름의 격리 container만** 종료하며 결과를 quarantine한다.

- 어떤 경계든 `violations > 0`;
- timeout, crash, monitor error, isolation 손실;
- unsafe sandbox/file/Electron option 또는 preload/IPC 노출;
- 필수 observation/evidence/containment 누락;
- host-stability-risk case의 host 실행;
- 입력/schema/output 오류.

machine-wide firewall, proxy, DNS, browser 설정을 변경하지 않는다. 실제 외부
exfiltration, cloud metadata request, fork bomb, OOM, browser crash exploit,
oversized real font/image/DOM은 이 host에서 실행하지 않는다.
