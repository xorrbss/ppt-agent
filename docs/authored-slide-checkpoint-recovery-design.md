# Authored 슬라이드 체크포인트 및 실패 복구 설계

상태: 구현·검증 완료
대상: `template="authored"` 비동기 durable generation
호환 제약: 기존 30분 foreground timeout, `Idempotency-Key`, durable job lease/heartbeat/reconciliation 유지

## 1. 현재 코드와 실패 원인

현재 `authored_presentation_service._author_illustrate_render_pipeline()`은 모든 슬라이드 coroutine을 한 번에 `asyncio.gather()`로 시작하고, 전부 끝난 뒤 `generate_authored_presentation()`이 PNG와 presentation/slide row를 한 번에 저장한다. 따라서 마지막 슬라이드나 export 직전 실패도 앞서 성공한 author·illustrate·render 결과를 잃는다. render 실패는 placeholder로 바뀌어 불완전 deck이 publish될 수도 있다.

`generate_presentation_handler()`의 최외곽 예외 처리는 원래 예외를 generic 500으로 바꾸며, durable task에는 단계와 슬라이드가 없다. 실패 job은 terminal `failed`가 되어 같은 `Idempotency-Key`를 다시 보내도 기존 error task만 반환한다. UI는 status 응답의 처리량이 아니라 경과 시간으로 95%까지 추정한다.

## 2. 상태 모델과 저장 계약

`authored_slide_checkpoints`를 추가하며 `(job_id, slide_index)`를 유일 키로 사용한다. 슬라이드 번호는 API에서 1-based, DB index는 기존 slide 모델과 같은 0-based다.

각 단계 `author`, `illustrate`, `render`, `persist`는 다음 상태를 가진다.

- `pending`: 아직 실행하지 않았거나 안전하게 재실행해야 함
- `running`: 현재 lease attempt가 소유함
- `completed`: 결과 파일과 DB 체크포인트가 함께 durable함
- `failed`: 정제된 오류가 저장되었고 재개 가능 여부가 결정됨
- `skipped`: 해당 style에서 illustration처럼 수행할 작업이 없음

단계는 순서대로만 전이한다. 각 단계의 산출물은 job별 private checkpoint 디렉터리에 원자적 임시 파일 교체로 쓴 다음, 현재 job의 `attempt_token`과 일치할 때만 DB 상태를 commit한다. author/illustrate HTML, render PNG 경로는 DB에 통제된 상대 경로만 저장한다. `persist=completed`는 해당 slide의 HTML과 private render artifact가 hash 검증을 통과해 재개에 사용할 수 있다는 뜻이다. 공개 image 디렉터리 복사와 Presentation/Slide row 생성은 모든 slide가 persisted된 후의 final publish gate에서만 수행한다.

job에는 `resume_allowed`, `last_error`, `completed_slides`, `total_slides`를 additive하게 추가한다. 기존 job은 기본값 `false/null/0/null`로 읽힌다. 진행 집계의 authoritative source는 checkpoint row이며, job counters는 polling 비용을 줄이기 위한 단조 증가 projection이다.

최초 authored 시도에서 확정된 outline도 job의 nullable `authored_outline`에 저장한다. 재개는 이 outline을 재사용해 slide index/role이 흔들리지 않게 하며, content brief와 outline provider 호출도 다시 과금하지 않는다. 기존 job과 authored가 아닌 경로에서는 값이 `null`이다.

## 3. 오류 정보와 민감정보 제거

DB/API가 저장·노출하는 오류는 다음 allowlist 구조만 사용한다.

```json
{
  "code": "authored_slide_render_failed",
  "message": "Browser renderer failed after retries",
  "stage": "render",
  "slide_number": 47,
  "exception_type": "TimeoutError",
  "fingerprint": "sha256-prefix",
  "retryable": true,
  "request_id": "optional"
}
```

원본 exception 객체, traceback, prompt/HTML, provider response body, request payload, headers/cookies, 로컬 절대 경로는 저장하지 않는다. message는 길이를 제한하고 API key/Bearer token/credential assignment/URL query/절대 경로를 정규식으로 치환한다. `exception_type`과 정제 message의 hash를 남겨 원인 상관 분석이 가능하게 한다. 로그에는 traceback을 남길 수 있지만 기존 로깅 접근제어의 대상이며 API/DB에는 넣지 않는다.

## 4. 체크포인트와 재개

작업 시작 시 요청 outline으로 필요한 checkpoint row를 idempotent하게 생성한다. worker는 각 슬라이드에서 첫 미완료 단계를 찾고 이전 단계 파일을 검증한다. `completed`로 표시됐지만 파일이 없거나 해시가 다르면 그 단계와 후속 단계를 `pending`으로 되돌린다.

재시도 시:

1. `persist=completed`인 슬라이드는 외부 author/image/render 호출을 하지 않는다.
2. render만 실패했다면 illustrated HTML부터 render/persist만 수행한다.
3. illustrate가 실패했다면 author HTML부터 illustrate/render/persist를 수행한다.
4. author가 실패했다면 그 슬라이드 author부터 수행한다.
5. 모든 slide가 persisted된 뒤에만 export를 임시 경로에 만들고 atomic rename한다.
6. export가 존재하고 presentation이 이미 `published`라면 crash-after-commit 재조정으로 즉시 완료 응답을 복원한다.

vision QA는 기존 opt-in 동작을 유지하되, QA 수정 결과도 render 결과를 교체한 뒤 persist checkpoint를 갱신해야 한다. QA 자체의 provider 호출은 4단계 모델 밖이므로 프로세스가 정확히 그 구간에서 죽으면 재호출될 수 있다는 한계를 잔여 위험으로 기록한다.

## 5. Idempotency와 동시 실행 방지

기존 owner-scoped `Idempotency-Key` hash unique index와 payload hash 충돌 검사를 유지한다. retryable authored failure인 경우 같은 key와 같은 payload의 새 POST는 동일 job/task를 `failed -> queued`로 조건부 update하고 task를 pending으로 바꾼다. `resume_allowed=false`인 validation failure나 명시적 user cancel은 재큐잉하지 않는다.

DB job claim은 기존 lease와 새 random `attempt_token`을 사용한다. slide stage claim/update도 같은 token을 검증해 stale worker가 상태를 덮어쓰지 못하게 한다. 조건부 job state update와 unique checkpoint row가 두 요청의 동시 재개 중 하나만 이기게 한다. 외부 provider가 자체 idempotency를 지원하지 않는 호출에서 응답 직후 프로세스가 죽는 경우 exactly-once billing은 보장할 수 없지만, 정상 중복 요청·동시 worker·완료 checkpoint 재시도에 의한 중복 과금은 막는다.

슬라이드 하나가 실패해도 이미 시작한 sibling provider 호출을 즉시 취소하지 않는다. 새 슬라이드 claim을 중단하고 in-flight 호출의 성공 결과를 token-fenced checkpoint로 commit할 때까지 배수한 후 실패를 전파한다. 외부 응답과 DB commit 사이에 cancellation이 들어와도 commit task를 완료하고 worker를 join한 뒤 attempt를 종료해, 반환된 성공 호출을 재개 시 다시 과금하는 경쟁 조건을 막는다.

## 6. 재시작, timeout, 취소

- FastAPI shutdown/worker cancellation: `CancelledError`를 실패로 바꾸지 않고 기존 `release_generation_job()`으로 queued/awaiting_resume 처리한다. 완료 checkpoint와 파일은 유지한다.
- 비정상 프로세스 종료: lease expiry 후 startup reconciliation/dispatcher가 같은 job을 claim한다. 남은 `running` slide 단계는 새 attempt가 artifact를 검증한 뒤 재개한다.
- 30분 timeout: UI foreground overlay만 닫고 polling은 background에서 계속하는 기존 의미를 보존한다. 서버 job을 취소하지 않는다.
- user cancel: job을 terminal failed, `resume_allowed=false`, task error code 499로 만든다. 실행 handle을 취소하고 자동 recovery 대상에서 제외한다.
- heartbeat/lease 상실: stage commit은 attempt token 불일치로 거절하고 worker는 중단한다.

## 7. 부분 파일 보존과 publish 원칙

checkpoint artifact는 `app_data`의 공개 URL namespace가 아닌 job별 내부 디렉터리에 저장하며 실패·timeout·재시작 동안 보존한다. 성공 시 최종 slide images/export를 원자적으로 완성하고 presentation/slides를 단일 DB transaction으로 `published` 처리한 다음 checkpoint artifact를 정리한다. 공개 image 복사가 중간에 실패하면 아직 DB에서 참조하지 않는 해당 presentation 디렉터리만 즉시 제거한다. 모든 image 복사 후 DB commit 결과가 불확실한 경우에는 이미 완성된 파일을 재조정·재시도에 쓰도록 보존한다. 정리 실패는 publish를 되돌리지 않고 경고/후속 TTL 정리 대상으로 남긴다.

최종 publish는 job의 현재 `attempt_token`을 조건으로 `running -> staging` 전이시키는 DB fence를 먼저 잡는다. presentation/slides, async task `completed`, job `published` 전이는 같은 transaction에서 commit하며, token이 바뀌었거나 취소된 stale worker는 이 경계를 통과할 수 없다. process 종료로 transaction이 끊기면 DB row는 publish되지 않고, 만료 lease 재조정 후 기존 slide checkpoint에서 다시 시작한다.

재시도 불가 실패와 user cancel은 DB의 정제 오류/단계 상태는 보존하되 artifact는 보존기간 후 정리한다. 초기 구현은 즉시 성공 정리를 제공하고, cancel·비정상 orphan·failed artifact의 TTL sweeper는 후속 운영 작업으로 분리한다.

불완전 presentation row는 discovery/share 대상이 되면 안 된다. export를 먼저 임시 파일로 완성하고, 최종 image 파일이 모두 존재하는 것을 확인한 뒤에만 presentation/slides transaction을 commit한다. 최종 task `completed`와 job `published`는 그 이후다.

## 8. 진행률 API와 UI

기존 status 필드는 그대로 두고 optional `progress`와 정제 `failure`를 추가한다.

```json
{
  "progress": {
    "completed_slides": 46,
    "total_slides": 90,
    "percent": 51,
    "current_slide": 47,
    "current_stage": "render",
    "stage_counts": {"author": 90, "illustrate": 90, "render": 46, "persist": 46},
    "finalizing": false
  },
  "failure": null
}
```

`completed_slides`는 `persist=completed` row 수다. 실행 중 `90/90`이어도 export/DB publish 전에는 `percent=99`, task completed 이후만 100이다. UI는 authored status에 progress가 있으면 controlled value와 `46/90 슬라이드`를 표시하고, 구버전 서버/다른 생성 모드는 기존 duration fallback을 사용한다. localStorage durable v1에는 optional last progress/failure만 additive하게 저장해 재조정을 유지한다. retryable failure는 local task/key를 지우지 않아 같은 key POST로 재개할 수 있게 한다.

## 9. 마이그레이션과 하위 호환

새 Alembic revision은 기존 head 뒤에 테이블/nullable 또는 server-default column/index를 additive하게 만든다. 기존 `presentation_generation_jobs`, async task, presentation row를 재작성하지 않는다. 새 status 응답 필드는 optional이라 구 클라이언트가 무시할 수 있다. authored가 아닌 template/adaptive 경로는 checkpoint를 만들지 않으며 기존 durable state machine을 그대로 사용한다.

startup migration schema inference/테스트의 expected column/table 목록을 새 revision에 맞춘다. SQLite와 PostgreSQL 모두에서 FK/index/default를 검증한다. downgrade는 새 checkpoint table/index를 먼저 제거한 뒤 job columns를 제거한다.

## 10. 동시성/rate limit

90개 coroutine을 한 번에 만들지 않고 고정 worker queue를 사용한다. 기본 deck slide worker 수는 환경변수로 제한하며 author(기존 5), illustration(기존 4), render별 process-wide semaphore 중 가장 보수적인 한도를 존중한다. authored deck job 자체도 process-wide semaphore로 제한해 dispatcher가 여러 90장 job을 동시에 시작해 provider quota를 소진하지 않게 한다.

429/5xx/timeout은 bounded exponential backoff와 jitter 후 `retryable=true`로 checkpoint한다. 인증/invalid request/콘텐츠 정책 같은 영구 오류는 `retryable=false`로 처리한다. 재시도 횟수와 총 sleep은 단계 helper의 기존 제한을 유지하고, 무한 retry는 하지 않는다.

## 11. 검증 계획과 publish gate

- 단위: 상태 전이, token fencing, artifact atomic write/hash 검증, sanitizer redaction, stage별 실패/재개, completed slide skip, bounded concurrency, cancellation.
- API: status progress/failure, same-key conflict, same-key failed→queued race, cancel non-resume, old task/job compatibility, 90/90 finalizing=99.
- migration: upgrade/downgrade와 SQLite/PostgreSQL schema 계약, 기존 DB row 보존.
- frontend component/Cypress: controlled progress/count, fallback progress, retryable failure key 보존, remount reconciliation, 30분 background, cancel, completed 후만 navigation.
- 장시간 회귀: 90장 fake provider에서 동시성 상한과 author/illustrate/render 호출 수를 기록하고 slide N 실패 후 재개 시 1..N-1 호출 수가 증가하지 않는지 확인한다.
- 로컬 실제 앱: test-only failure injection으로 slide N/stage를 한 번 실패시키고 GET status의 정제 오류/부분 count를 확인한다. 동일 key POST 후 서버 재기동을 포함해 완료될 때까지 polling하고, 최종 PPTX 존재·slide 수·published row·중복 호출 없음·checkpoint cleanup을 확인한다.

publish gate는 `모든 checkpoint persisted && export atomic rename 성공 && presentation/slides DB commit 성공`이다. 하나라도 아니면 task/job은 completed/published가 될 수 없다.

## 12. 구현 위험 검토

- 가장 큰 정합성 위험은 DB commit과 파일 rename이 하나의 transaction이 아니라는 점이다. deterministic path, atomic replace, 재개 시 파일 검증, published row 재조정으로 보상한다.
- provider가 idempotency를 제공하지 않으면 외부 응답과 checkpoint commit 사이의 process kill에서 중복 과금을 완전히 제거할 수 없다. 이는 문서화하고, 지원 provider에는 slide/job/stage 기반 idempotency key를 전달하는 후속 개선이 필요하다.
- 여러 API process의 semaphore는 process-local이다. DB lease는 동일 job 중복은 막지만 전체 cluster quota는 막지 않는다. 다중 replica 운영에서는 Redis/DB 기반 quota coordinator가 필요하다.
- checkpoint HTML에는 사용자 콘텐츠가 있으므로 공개 image 경로와 분리하고 성공/cancel cleanup 및 운영 TTL이 필요하다.
- 기존 render placeholder 허용을 authored durable 경로에서 실패로 바꾸면 이전보다 엄격하다. 불완전 deck publish 금지 조건을 충족하기 위한 의도적 변경이다.

## 13. 잔여 위험 보강 결과

- terminal checkpoint artifact는 dispatcher 시작 시 주기적으로 정리한다. 기본 보존 기간은 7일이며 기존 `AUTHORED_CHECKPOINT_ARTIFACT_TTL_SECONDS`가 failed, published, orphan의 공통 fallback이다. failed와 published는 각각 `AUTHORED_CHECKPOINT_FAILED_ARTIFACT_TTL_SECONDS`, `AUTHORED_CHECKPOINT_PUBLISHED_ARTIFACT_TTL_SECONDS`로 별도 보존 기간을 설정할 수 있다. batch와 실행 주기는 `AUTHORED_CHECKPOINT_SWEEP_BATCH_SIZE`, `AUTHORED_CHECKPOINT_SWEEP_INTERVAL_SECONDS`로 조정한다.
- 한 번의 sweep은 directory 수뿐 아니라 `AUTHORED_CHECKPOINT_SWEEP_MAX_BYTES`(기본 1 GiB), `AUTHORED_CHECKPOINT_SWEEP_MAX_FILES`(기본 10,000개)로 filesystem 작업량을 제한한다. 예산을 넘는 디렉터리는 삭제하지 않고 `skipped_budget`으로 집계해 운영자가 예산을 상향하거나 수동 정리할 수 있게 한다. 삭제 실패는 해당 job만 건너뛰고 오류 카운트와 UUID·예외 유형만 기록하며 다음 sweep에서 재시도한다.
- `running`, `staging`, `awaiting_resume`, `queued`는 정리 대상이 아니며 terminal row도 `attempt_token IS NULL`일 때만 conditional write lock 후 삭제한다. UUID canonical child만 허용하고 symlink와 작업 루트 밖 경로는 삭제하지 않는다. 따라서 dispatcher/reconciliation과 sweep이 겹쳐도 active/recoverable attempt의 artifact는 보존된다.
- authored job의 cluster 동시 실행 상한을 `AUTHORED_CLUSTER_JOB_CONCURRENCY`로 제한한다. PostgreSQL은 transaction advisory lock, SQLite는 `BEGIN IMMEDIATE`로 count와 claim을 직렬화한다. 알 수 없는 dialect는 기존 process-local 제한과 per-job CAS만 유지한다.
- 별도 Uvicorn OS process를 render 도중 강제 종료하고 lease 만료 후 새 process에서 동일 Idempotency-Key로 재개하는 통합 테스트를 추가했다.
- DB commit은 성공했지만 응답 직전에 연결이 끊긴 상황을 주입하여 새 session이 published 결과로 수렴하고 provider를 재호출하지 않는지 검증한다.
- 현재 provider adapter의 공개 계약에는 중복 억제가 보장된 idempotency 기능이 없다. provider 응답 후 checkpoint commit 전 강제 종료 시 단일 stage 호출이 재과금될 가능성은 남으며, 지원이 공식화되기 전 임의 header 주입은 하지 않는다.
- 실제 외부 provider 429 부하 시험과 production PostgreSQL advisory-lock 경합 시험은 CI의 deterministic fake-provider 범위 밖 운영 검증 항목으로 유지한다.
