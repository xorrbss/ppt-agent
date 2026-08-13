# Authored renderer P0 security gate

## Active-observation rule

Every observed boundary must include integer `attempts` and `blocked` counters.
Promotion requires `attempts >= 1`, `blocked == attempts`, `violations == 0`,
and at least one `sha256:<64 lowercase hex>` evidence reference. A missing active
probe is `INCONCLUSIVE`; an unblocked probe is `FAIL`. The `deployment` and
`electron` objects are mandatory promotion inputs and are never inferred from
static source configuration.

이 디렉터리의 판정기는 hostile HTML을 실행하지 않는다. 격리된 zero-egress harness가 수집한 JSON outcome만 읽어 authored-only GA 승격 여부를 fail-closed로 결정한다. 실제 외부 DNS/HTTP, metadata 주소, loopback 서비스, host 파일, child process, Electron IPC를 이 fixture나 판정기로 호출하지 않는다.

## 실행

저장소 루트 `C:\project\PPT-agent\ppt-agent`에서 다음처럼 실행한다.

```powershell
python docs/experiments/authored-vnext/04-renderer-security/ci/security_gate.py --input docs/experiments/authored-vnext/04-renderer-security/ci/fixtures/pass.json --output-dir docs/experiments/authored-vnext/04-renderer-security/results/fixture-pass
```

좁은 로컬 검증은 Python 표준 라이브러리만 사용한다.

```powershell
python -m unittest discover -s docs/experiments/authored-vnext/04-renderer-security/ci -p "test_*.py" -v
```

CI는 `security_gate.py`의 종료 코드를 그대로 job 종료 코드로 사용해야 한다.

| 코드 | 상태 | GA 승격 |
|---:|---|---|
| `0` | `PASS` | 허용 |
| `1` | `FAIL` | 금지 |
| `2` | `INCONCLUSIVE` | 금지 |
| `3` | `ERROR` (입력/출력 오류) | 금지 |

각 실행은 `gate-result.json`, `gate-junit.xml`, `gate.sarif`를 원자적으로 쓴다. JUnit은 `INCONCLUSIVE`를 `<error>`로 기록하고 SARIF는 `PASS`가 아닌 모든 항목을 `error` level로 기록하므로, 판정 JSON을 무시하는 소비자도 녹색으로 오인하기 어렵다.

## 입력 계약

입력 `schema_version`은 `authored-security-observation/v1`이다. 한 문서는 하나의 완결된 격리 실행을 나타낸다.

- `execution`: 완료/timeout/crash, monitor 건강성, isolation 유지 여부를 boolean으로 기록한다. `unsafe_launch_options`, `host_stability_risk_cases_executed`, `observer_errors`, `reported_kill_switch_events`는 빈 문자열 배열이어야 한다.
- `containment`: non-root, read-only root, isolated tmpfs, resource limits, browser sandbox, file URL/preload/IPC 차단, OS egress deny, CSP, DOM allowlist가 모두 관측된 `true`여야 한다.
- `boundaries`: `host_read`, `external_egress`, `loopback_egress`, `metadata_egress`, `child_process`, `ipc`, `secret_log`, `resource_escape`를 모두 포함한다. 각 항목은 `observed`, `violations`, `evidence`를 기록하고 선택적으로 `attempts`, `blocked`를 기록한다.

경계의 `violations`는 차단된 시도가 아니라 성공한 경계 통과 수다. 따라서 local canary/deny sink가 포착한 공격 시도는 `attempts > 0`, `blocked > 0`, `violations = 0`으로 표현할 수 있다. `evidence`에는 비밀이나 원문 로그를 넣지 않고 격리 artifact의 불투명한 참조 ID만 넣는다. 판정 결과는 참조 문자열을 복사하지 않고 개수만 기록한다.

합격은 모든 경계의 `violations = 0`, 모든 관측과 증거 존재, 실행 완결성, containment 제어 충족을 동시에 요구한다. `observed = false`, 누락된 counter/evidence/control은 `INCONCLUSIVE`, 0보다 큰 violation이나 unsafe 실행 상태는 `FAIL`이다. 둘 다 승격을 차단한다. malformed JSON이나 잘못된 타입은 `ERROR`다.

## Kill switch

출력의 `kill_switch.triggered`는 `PASS` 외에는 항상 `true`다. 다음 중 하나가 발생하면 harness/CI orchestrator는 새 case 예약을 중단하고 **해당 격리 harness만** 종료하며 evidence를 quarantine해야 한다.

- host read, external/loopback/metadata egress, child process/IPC, secret log, resource escape 중 하나라도 `violations > 0`
- timeout, crash, monitor 실패, isolation 손실
- `--no-sandbox`, file access 허용, `webSecurity=false`, `sandbox=false`, preload/IPC 노출 등 unsafe launch option 보고
- host 안정성 위험 case 실행, observer error, 기존 kill-switch event
- 필수 관측, evidence 또는 containment 제어 누락
- 입력 파싱/스키마 오류

판정기는 이미 실행 중인 프로세스를 직접 죽이지 않는다. CI가 종료 코드를 받고 격리 runtime의 명시적 ID를 대상으로 종료해야 하며 machine-wide firewall, proxy, DNS 또는 host 보안 설정을 변경해서는 안 된다. 안정성 위험 case는 이 로컬 fixture 검증 범위 밖이며 전용 disposable VM/container에서만 별도 승인 후 실행한다.

## 현재 host-safe baseline 연결

`baseline_adapter.py`는 현재 FastAPI/Next baseline 요약을 보수적으로 합쳐 gate 입력으로 만든다. 확인되지 않은 값을 0으로 추정하지 않는다. 현재 probe에서 증명하지 못한 external/metadata/process/IPC/secret-log/resource 경계와 hardened containment는 `INCONCLUSIVE`로 남는다.

```powershell
python docs/experiments/authored-vnext/04-renderer-security/ci/baseline_adapter.py --fastapi docs/experiments/authored-vnext/04-renderer-security/results/current-fastapi-baseline.json --next docs/experiments/authored-vnext/04-renderer-security/results/current-next-baseline.json --output docs/experiments/authored-vnext/04-renderer-security/results/current-baseline-gate-input.json
python docs/experiments/authored-vnext/04-renderer-security/ci/security_gate.py --input docs/experiments/authored-vnext/04-renderer-security/results/current-baseline-gate-input.json --output-dir docs/experiments/authored-vnext/04-renderer-security/results/current-baseline-gate
```

두 번째 명령은 현재 확인된 host read와 FastAPI loopback 성공 때문에 `FAIL`/종료 코드 `1`이어야 한다. 동시에 미관측 항목도 결과에 보존하므로, 이 baseline을 hardened harness의 합격 증거로 오인할 수 없다.
