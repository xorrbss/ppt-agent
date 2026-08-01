# Authored 슬라이드 체크포인트 운영 런북

이 문서는 authored PPT 체크포인트·재개 기능의 운영 설정, 관측, 장애 대응,
staging 검증, 배포와 롤백 절차를 정의한다. 상태 모델과 API 계약은
`authored-slide-checkpoint-recovery-design.md`, 클러스터 동시성과 provider 중복
과금 경계는 `authored-provider-idempotency-and-cluster-quota.md`를 함께 참고한다.

## 안전 불변조건

- 모든 슬라이드의 `persist_status`가 `completed`이고 최종 publish fence를 획득한
  시도만 PPTX를 publish한다.
- 재시도는 완료된 stage를 건너뛴다. render 파일은 SHA-256을 검증하고, 없거나
  변경됐으면 해당 stage부터 다시 실행한다.
- 동일한 Idempotency-Key는 같은 durable job을 반환한다. attempt lease와
  PostgreSQL advisory transaction lock이 중복 실행을 제한한다.
- 로그에는 prompt, HTML, 이미지, 사용자·job·request·presentation 식별자, 파일
  경로, 인증정보, 원본 예외 문자열을 기록하지 않는다. 상세 실패 정보는 DB의
  정제된 `last_error`와 checkpoint `error`만 사용한다.
- checkpoint 디렉터리는 애플리케이션 전용 로컬 또는 공유 볼륨이어야 한다.
  시스템 임시 디렉터리 전체나 다른 애플리케이션 경로를 지정하지 않는다.

## 운영 설정

| 설정 | 기본값 | 의미 |
|---|---:|---|
| `AUTHORED_JOB_CONCURRENCY` | `1` | 프로세스 내부 동시 authored job 수 |
| `AUTHORED_CLUSTER_JOB_CONCURRENCY` | 위 값 | DB 기준 전체 replica 동시 authored job 수 |
| `AUTHORED_SLIDE_PIPELINE_CONCURRENCY` | `8` | job 하나의 slide worker 수 |
| `AUTHORED_AUTHOR_CONCURRENCY` | `5` | 프로세스 전체 author provider in-flight 상한 |
| `AUTHORED_AUTHOR_REQUESTS_PER_MINUTE` | `0` | author 호출 시작 속도. `0`은 비활성화 |
| `AUTHORED_AUTHOR_RATE_LIMIT_COOLDOWN_SECONDS` | `10` | Retry-After가 없는 429의 cooldown(초) |
| `AUTHORED_AUTHOR_RATE_LIMIT_MAX_COOLDOWN_SECONDS` | `60` | Retry-After를 포함한 cooldown 상한(초) |
| `AUTHORED_CHECKPOINT_DIRECTORY` | OS temp 하위 전용 경로 | checkpoint artifact 저장소 |
| `AUTHORED_CHECKPOINT_ARTIFACT_TTL_SECONDS` | `604800` | 상태별 값이 없을 때 공통 TTL(7일) |
| `AUTHORED_CHECKPOINT_FAILED_ARTIFACT_TTL_SECONDS` | 공통 TTL | 실패 job artifact TTL |
| `AUTHORED_CHECKPOINT_PUBLISHED_ARTIFACT_TTL_SECONDS` | 공통 TTL | publish job 중간 artifact TTL |
| `AUTHORED_CHECKPOINT_SWEEP_INTERVAL_SECONDS` | `3600` | 프로세스별 sweep 최소 간격 |
| `AUTHORED_CHECKPOINT_SWEEP_BATCH_SIZE` | `50` (최대 `500`) | 한 번에 검토하는 terminal job 수 |
| `AUTHORED_CHECKPOINT_SWEEP_MAX_BYTES` | `1073741824` | sweep 1회의 삭제 검사 byte 예산 |
| `AUTHORED_CHECKPOINT_SWEEP_MAX_FILES` | `10000` (최대 `100000`) | sweep 1회의 파일 수 예산 |
| lease / heartbeat | `300초` / `30초` | attempt 소유권과 생존 신호 |
| 브라우저 foreground deadline | `30분` | UI 대기 한계. durable server job은 계속될 수 있음 |

보수적인 provider in-flight 상한은
`AUTHORED_CLUSTER_JOB_CONCURRENCY × AUTHORED_AUTHOR_CONCURRENCY`다. RPM 설정은
in-flight가 아니라 호출 시작 간격을 제한하며 프로세스별로 적용된다. 여러 replica가
하나의 계정 quota를 공유하면 각 프로세스 RPM의 합이 계정 상한 아래가 되도록 나누거나
외부 분산 rate limiter를 사용한다.

테스트 전용 `AUTHORED_TEST_FAILURES_ENABLED`, `AUTHORED_TEST_FAILURE_SLIDE`,
`AUTHORED_TEST_FAILURE_STAGE`는 운영 환경에 설정하지 않는다.

## 구조화 관측 이벤트

로그의 `authored_generation ` 접두사 뒤 JSON을 수집한다. schema version은 `1`이며
허용된 필드만 출력하므로 사용자 내용과 고카디널리티 식별자가 섞이지 않는다.

| `name` | 필드 | 발생 조건 |
|---|---|---|
| `slide_failure` | `stage`, `code`, `slide_number`, `retryable` | 정제된 checkpoint 실패를 DB에 commit한 뒤 |
| `resume` | `attempt_number`, `resume_count`, `completed_slides`, `total_slides` | 두 번째 이상 attempt가 claim될 때 |
| `quota_wait` | `duration_ms`, `polls` | cluster slot 대기가 끝났을 때 |
| `artifact_sweep` | `outcome`과 scan/delete/error/budget 카운트 | TTL/orphan 정리 후 또는 정리 자체 실패 시 |
| `stalled_recovery` | `prior_state`, `recovered_jobs` | 만료된 running/staging job을 복구했을 때 |

등록되지 않은 실패 코드는 `authored_unexpected_failure`로 축약한다. 관측 로깅 때문에
실패 기록이나 복구 흐름이 중단되어서는 안 된다.

초기 경보 기준은 다음과 같이 보수적으로 시작하고 1주일 baseline 후 조정한다.

- `persist` 또는 `authored_unexpected_failure`: 1건부터 즉시 조사
- 15분 실패율 5% 초과 또는 같은 stage/code 3건 이상: 경고
- `quota_wait.duration_ms` p95가 120초 초과: capacity, RPM, 429 동시 확인
- 연속 두 sweep의 `outcome=failure`, 또는 성공 이벤트의 `errors>0`: 경고
- `skipped_budget>0`: 비정상적으로 큰 checkpoint 디렉터리 조사
- `stalled_recovery.recovered_jobs>0`: 배포, OOM, event-loop 지연과 대조
- checkpoint 볼륨 여유 공간 20%/10% 이하: 경고/심각 경보

## 장애 확인 절차

먼저 구조화 이벤트를 배포·재시작·OOM 기록과 대조한다. 사용자 내용이 든 컬럼을
조회하거나 원본 예외를 티켓과 채팅에 복사하지 않고, 아래처럼 집계 상태만 확인한다.

```sql
SELECT state, current_stage, COUNT(*)
FROM presentation_generation_jobs
GROUP BY state, current_stage
ORDER BY state, current_stage;

SELECT failed_stage, error->>'code' AS code, COUNT(*)
FROM authored_slide_checkpoints
WHERE error IS NOT NULL
GROUP BY failed_stage, error->>'code'
ORDER BY COUNT(*) DESC;
```

- stalled job: lease 만료와 heartbeat 지연을 확인한다. dispatcher가 다음 pass에서
  자동으로 재조정하게 두고 DB 상태를 수동 변경하지 않는다.
- 반복 slide 실패: slide 번호, stage, 정제된 code, fingerprint만 기록한다.
- quota wait/429: cluster job 수를 먼저 확인하고 author concurrency와 프로세스별 RPM을
  낮춘다. provider 정책보다 cooldown 상한을 짧게 설정하지 않는다.
- sweep 오류: 전용 볼륨 권한, symlink, 여유 공간을 확인한다. active job 디렉터리를
  수동 삭제하지 않는다. budget skip은 read-only로 파일 수와 크기를 조사한다.
- 사용자 취소/30분 UI timeout: 취소는 lease를 해제하고 publish를 금지한다. UI timeout만
  발생한 경우 server job 상태를 조회하며 중간 artifact를 삭제하지 않는다.

## Staging soak

production과 같은 PostgreSQL major version, 공유 checkpoint 볼륨, FastAPI replica 2개
이상에서 다음 시나리오를 실행한다.

1. 90장 job을 동시에 2개 이상 제출하고 실제 완료 slide 수 기반 진행률과 cluster/provider
   상한을 기록한다.
2. author, illustrate, render, persist 각각의 중간·마지막 slide 실패를 주입한다. DB/API에
   번호·단계·정제 코드가 남고 불완전 PPTX가 publish되지 않아야 한다.
3. 동일 Idempotency-Key로 재시도한다. 완료 checkpoint의 provider 호출 수는 증가하지
   않고 실패 stage부터 재개해 최종 PPTX가 한 번만 publish되어야 한다.
4. render 중 process kill, FastAPI rolling restart, lease 만료를 실행한다. 한 winner만
   재개하고 stale attempt는 persist/publish할 수 없어야 한다.
5. UI 30분 timeout과 사용자 취소를 각각 확인한다. timeout 후에는 API 재조정, 취소
   후에는 publish 금지와 lease 해제를 검증한다.
6. provider 429와 Retry-After를 주입한다. 호출 시작 간격, 공유 cooldown과 quota wait가
   적용되고 요청 폭주가 없어야 한다.
7. terminal/orphan 디렉터리를 TTL 전후로 만들고 byte/file budget을 낮춘다. active와
   신규 artifact는 보존되고 관측 카운트가 실제 삭제와 일치해야 한다.
8. 수집 로그에 prompt, HTML, 식별자, 경로, credential, 원본 예외가 없는지 자동 검색한다.

backend unit/API/Cypress, 90장 회귀, process restart 회귀와 staging soak를 모두 통과하고,
checkpoint 손실·중복 provider 호출·불완전 publish가 0건일 때만 release gate를 승인한다.
실 provider soak는 별도 테스트 계정과 비용 상한을 사용한다.

## 배포와 롤백

1. checkpoint 볼륨을 백업하고 권한·소유자·여유 공간을 확인한다.
2. Alembic head `a3c5e7f9b1d2`를 적용한다. 마이그레이션은 새 table, column, index를
   추가하는 하위 호환 변경이다.
3. canary 1개를 `AUTHORED_CLUSTER_JOB_CONCURRENCY=1`과 provider quota 이하 RPM으로
   시작한다.
4. migration head, dispatcher, 구조화 이벤트, DB 상태와 볼륨 증가율을 확인한다.
5. 90장 canary의 재개와 publish 불변조건을 확인한 뒤 replica 또는 cluster concurrency를
   한 번에 하나씩 올리고 quota wait, 429, heartbeat, 메모리를 관찰한다.

문제가 생기면 신규 요청 유입을 canary에서 제거하고 진행 중 job을 drain하거나 정상
취소한다. 애플리케이션은 이전 버전으로 롤백하되 DB migration은 유지하는 것이 기본이다.
이전 코드가 추가 schema를 무시할 수 있어 checkpoint와 artifact를 보존한다. schema
downgrade가 꼭 필요하면 모든 generation worker를 중지하고 active job이 없음을 확인한 뒤
새 DB 백업에서만 검토하며, 진행 중 job이 있는 상태에서는 downgrade하지 않는다.

불완전 PPTX publish나 중복 과금이 관측되면 즉시 rollout을 중단한다. quota wait만 증가하고
데이터 불변조건이 유지되면 concurrency를 마지막 안정값으로 낮춘 뒤 원인을 조사한다.

## 잔여 위험

- provider가 idempotency를 지원하지 않으면 provider 응답 성공과 checkpoint commit 사이의
  process death에서 그 한 호출은 재과금될 수 있다.
- SQLite는 로컬 테스트용 fallback이다. 다중 replica release gate는 PostgreSQL에서 실행한다.
- 파일시스템과 DB는 단일 transaction이 아니다. SHA-256과 상태 재조정으로 복구하지만
  공유 볼륨 자체의 monitoring과 backup은 별도로 필요하다.
- process-local RPM은 여러 replica의 계정 전체 quota를 직접 조정하지 않는다. cluster job
  quota를 1보다 높일 때는 분산 token bucket 또는 보수적인 replica별 RPM 분할이 필요하다.
- 실제 provider latency, 429와 비용 특성은 mock 장시간 회귀만으로 증명되지 않는다.
  별도 staging soak 결과를 배포 승인 자료에 포함한다.
