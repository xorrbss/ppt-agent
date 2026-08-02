# Chat-first Slides Workbench 수정 설계 재평가 리포트

- 평가일: 2026-08-02
- 평가 대상: `docs/chat-first-slides-workbench-design-20260802.md`
- 비교 기준: 1차 평가 `78/100`
- 평가 방법: 수정 설계의 제품·UX·데이터·동시성·복구 계약을 현재 FastAPI/Next.js 및 지원 DB 구조와 재대조
- 판정: **핵심 설계 승인 / 개발 조건부 GO**
- 종합 점수: **89/100**

## 1. 최종 판단

수정 설계는 이전 평가의 핵심 Blocker를 대부분 정확하게 반영했다. 특히 다음 결정은 더 이상 다시 설계할 필요가 없다.

- 최초 생성 프롬프트, 첨부 파일, 생성 설정을 오른쪽 채팅의 첫 사용자 메시지로 영구 보존
- 업로드 이후 `staging presentation`을 기준으로 conversation, 첫 메시지, attachment, 생성 operation을 원자적으로 bootstrap
- 일회성 팝업 대신 영속 operation card로 생성·수정·검토·내보내기·복구 상태 표시
- 안정적인 asset ID, conversation 실체, 메시지 position allocator, 덱 authoritative revision 도입
- DB event를 원본으로 하는 재접속 가능한 timeline과 cursor 만료 시 전체 재동기화
- idempotency, operation version CAS, 취소·승인·재시도·되돌리기, worker lease와 복구 정책
- authored/adaptive/template-v2 capability를 서버가 권위 있게 선언
- 공개 공유 DTO에서 prompt, 첨부, 대화, operation 정보를 분리
- feature flag, shadow mode, 롤백, 수용 테스트 게이트

따라서 제품 방향과 핵심 도메인 모델은 승인할 수 있다. 다만 **현재 문서 그대로 전체 구현에 들어가는 것은 권장하지 않는다.** 기본 SQLite 실행 환경과 설계의 PostgreSQL·다중 서버 전제가 아직 하나의 지원 정책으로 정리되지 않았고, 범용 operation runner와 통합 timeline 투영 규칙도 구현자가 해석해야 하는 부분이 남아 있다.

권장 판정은 다음과 같다.

| 범위 | 판정 | 조건 |
|---|---|---|
| Sprint 0: 계약 fixture, mutation inventory, DB/runner spike | **즉시 시작 가능** | 아래 ADR 5개를 산출물로 고정 |
| P0-A 모델·마이그레이션 구현 | **조건부 시작 가능** | DB 지원 프로파일과 position allocator 방식을 먼저 확정 |
| P0-B outbox·worker·durable stream | **아직 착수 보류** | runner 소유권·claim·fencing·wakeup 계약 확정 후 시작 |
| P1-A 읽기 전용 오른쪽 timeline | **병렬 prototype 가능** | anchor 기반 timeline projection fixture를 먼저 고정 |
| 기존 팝업 제거와 전체 사용자 활성화 | **아직 불가** | 문서 21장의 수용 게이트와 본 리포트 Gate A~E 통과 필요 |

한 문장으로 정리하면 **“설계 방향은 맞고 개발을 시작해도 되지만, 구현 방식의 5개 결정을 먼저 잠근 뒤 단계적으로 진행해야 한다.”**

## 2. 재점수표

1차 평가와 같은 가중치를 사용했다.

| 평가 항목 | 가중치 | 1차 | 재평가 | 가중 점수 | 판단 |
|---|---:|---:|---:|---:|---|
| 제품 비전과 사용자 맥락 연속성 | 15 | 93 | 96 | 14.40 | 최초 프롬프트·파일·생성 정보의 첫 메시지 보존이 bootstrap과 수용 기준까지 연결됨 |
| 정보 구조와 카드 UX | 15 | 88 | 94 | 14.10 | 카드 상태, 팝업 예외, 패널 폭, 선택 대상, 직접 편집 집계가 구체화됨 |
| Command/Event/Action 계약 | 15 | 76 | 91 | 13.65 | schema version, 상태 전이, action CAS, idempotency, stable target ID가 보강됨 |
| 영속성·재접속·타임라인 | 15 | 70 | 86 | 12.90 | snapshot high watermark와 DB replay가 보강됐으나 메시지·이벤트 투영 규칙이 한 단계 더 필요 |
| 현재 코드와의 구현 적합성 | 15 | 62 | 74 | 11.10 | 기존 durable generation 기반을 재사용할 수 있으나 DB dialect와 mutation 경로 통합 비용이 큼 |
| 동시성·복구·되돌리기 | 10 | 72 | 86 | 8.60 | authoritative revision, lease, 취소 상태가 좋아졌으나 외부 파일/side effect의 commit 경계가 남음 |
| 보안·개인정보·접근성 | 5 | 78 | 91 | 4.55 | 공유 경계, 안전 렌더링, asset 권한·격리·만료가 명확해짐 |
| 테스트·단계 전환·관측성 | 10 | 82 | 94 | 9.40 | shadow mode, parity, 장애·경쟁 조건 E2E, 롤백 게이트가 충분히 구체적임 |
| **합계** | **100** | **78** |  | **88.70 → 89** | **핵심 설계 승인 / 개발 조건부 GO** |

보조 지표:

- 제품 경험 설계: **95/100**
- 기술 설계 완성도: **88/100**
- 현재 코드 기준 구현 준비도: **74/100**
- 아래 ADR 반영 후 예상 설계 점수: **93/100**
- 1차 대비 개선: **+11점**

## 3. 이전 Blocker 반영 평가

| 1차 지적 | 수정 결과 | 상태 |
|---|---|---|
| conversation 실체와 최초 생성 원자성 부재 | staging presentation 기반 `POST /workspace/bootstrap`과 `workspace_conversations` 정의 | **해결** |
| 임시 파일 경로와 최초 첨부 복원 불가 | `uploaded_assets`, attachment snapshot metadata, provenance·만료·격리 정의 | **해결** |
| 덱 전체 authoritative revision 부재 | `presentations.revision`, expected revision CAS, 모든 mutation의 공용 revision service 정의 | **설계상 해결** |
| timeline snapshot/stream 사이 이벤트 유실 | snapshot high watermark와 `after_cursor` replay 정의 | **해결** |
| 여러 서버의 SSE 이벤트 전달 불명확 | DB event source of truth, wake-up은 힌트, heartbeat DB 재조회 정의 | **설계상 해결** |
| `max(position)+1` 경쟁 조건 | conversation row의 `next_position` 예약과 unique constraint 정의 | **해결, dialect 구현 결정 필요** |
| schema/state/action 계약 부족 | versioned schema, strict registry, cancelling/expired, action idempotency와 CAS 정의 | **해결** |
| stable slide/element ID와 capability 부재 | ID 기반 target과 서버 authoritative capability 정의 | **해결** |
| 공유·보존·롤아웃 정책 부족 | public DTO 분리, 보존·압축, feature flag·shadow·rollback 정의 | **해결** |

## 4. 남은 구현 전 결정 — P0 ADR

아래 항목은 설계 방향을 뒤집는 문제가 아니라, 구현자가 서로 다른 방식으로 해석하면 장애가 생기는 경계다. 코드 작성 전에 ADR로 확정해야 한다.

### ADR-001. DB 및 배포 프로파일

- 심각도: **P0 Decision Blocker**
- 영향: migration 호환성, 메시지 순번 경쟁, job claim, 다중 서버 wake-up

현재 애플리케이션은 `DATABASE_URL`이 없으면 SQLite를 기본으로 사용하고 PostgreSQL과 MySQL URL도 허용한다.

- `servers/fastapi/utils/db_utils.py:59-71`

반면 수정 설계는 conversation row에 `SELECT ... FOR UPDATE`를 사용하고, 다중 서버 wake-up 예시로 PostgreSQL `LISTEN/NOTIFY`를 제시한다.

- `docs/chat-first-slides-workbench-design-20260802.md:576-577`
- `docs/chat-first-slides-workbench-design-20260802.md:609-615`

SQLite에는 같은 방식의 row lock이 없다. 기존 durable generation 코드는 이미 이 차이를 PostgreSQL advisory transaction lock과 SQLite `BEGIN IMMEDIATE`로 분기한다.

- `servers/fastapi/services/presentation_generation_job_service.py:73-99`

권장 결정:

| 항목 | Embedded/Local 프로파일 | Managed/Cluster 프로파일 |
|---|---|---|
| DB | SQLite | PostgreSQL |
| API/worker 규모 | 단일 노드, 복수 프로세스는 제한적으로 검증 | 다중 API·worker |
| position 예약 | `BEGIN IMMEDIATE` 또는 원자적 counter update abstraction | row lock 또는 원자적 `UPDATE ... RETURNING` |
| job wake-up | 짧은 DB polling | PostgreSQL notify 또는 Redis, DB polling fallback |
| event 원본 | DB row | DB row |
| SSE replay 계약 | 동일 | 동일 |

MySQL을 Chat-first v1에서 지원하려면 같은 migration·claim·cursor 경쟁 테스트를 추가한다. 그렇지 않으면 feature capability에서 명시적으로 비활성화해야 한다. **SQL 문법을 문서의 계약으로 삼지 말고 `PositionAllocator`, `JobClaimer`, `EventCursorStore`의 dialect adapter 계약으로 표현하는 것이 최선이다.**

### ADR-002. 범용 operation runner와 outbox 소유권

- 심각도: **P0 Decision Blocker**
- 영향: 재시작 후 작업 유실, 중복 side effect, 취소·retry·lease 일관성

수정 설계는 transactional outbox와 worker lease를 요구하지만 다음 세부가 아직 없다.

- outbox row를 누가, 어떤 조건으로 claim하는가
- delivery attempt와 operation execution attempt를 같은 번호로 볼 것인가
- claim fencing token을 handler와 artifact publication까지 어떻게 전달하는가
- 재시도 지연, 최대 시도, dead-letter 또는 `RECOVERY_REQUIRED` 전환 조건
- wake-up이 없어도 polling으로 작업이 실행되는 최대 지연
- 프로세스 종료 시 lease·in-process task의 종료 순서

현재 코드에는 DB job row, attempt token, lease, heartbeat, 시작 시 복구, polling dispatcher가 이미 존재한다.

- `servers/fastapi/services/presentation_generation_job_service.py:173-273`
- `servers/fastapi/services/presentation_generation_job_service.py:276-305`
- `servers/fastapi/services/presentation_generation_job_service.py:380-438`
- `servers/fastapi/services/presentation_generation_job_service.py:540-592`

생성 endpoint는 여전히 FastAPI `BackgroundTasks`로 launch를 깨우지만 실행 상태 자체는 durable job row에 보존된다.

- `servers/fastapi/api/v1/ppt/endpoints/presentation_generate.py:1397-1518`

권장 결정은 새 큐 체계를 병렬로 만드는 것이 아니라, 기존 `PresentationGenerationJob`의 검증된 claim/lease/recovery 패턴을 `WorkspaceOperationRunner`로 일반화하는 것이다. outbox는 operation 생성 트랜잭션에서 쓰고, dispatcher는 outbox를 claim한 뒤 동일한 runner를 깨운다. wake-up은 지연 최적화일 뿐 실행 보장의 원본이 아니어야 한다.

### ADR-003. 메시지와 operation event의 통합 timeline 투영

- 심각도: **P0 Decision Blocker**
- 영향: 새로고침 후 순서 변경, 카드 중복, 실시간 완료 카드의 이동, pagination 오류

수정 설계에는 두 개의 순서 축이 있다.

- conversation 메시지의 `position`
- operation event의 presentation 단위 `stream_cursor`

현재 문서의 가장 자연스러운 해석은 operation anchor 메시지가 timeline의 고정 위치를 차지하고, event는 그 카드의 상태만 갱신하는 것이다. 이 규칙을 API fixture로 명시해야 한다.

권장 projection 계약:

1. top-level timeline 순서는 오직 message `position`으로 정한다.
2. operation 시작 시 assistant anchor가 position 하나를 예약한다.
3. operation event는 새로운 top-level 행을 만들지 않고 `operation_id` 카드 projection만 변경한다.
4. 작업이 나중에 완료돼도 anchor 위치는 이동하지 않는다.
5. progress detail은 카드 내부 `sequence`로 정렬한다.
6. 사용자 직접 편집을 top-level 기록으로 보여주려면 반드시 별도 anchor message를 예약한다.
7. `high_watermark_cursor`는 카드 최신 상태의 replay 경계이며 message pagination cursor를 대신하지 않는다.

이 모델을 선택하지 않고 메시지와 모든 event를 같은 레벨에 섞으려면 별도의 전역 `timeline_sequence`가 필요하다. 제품 UX에는 anchor 모델이 더 단순하고 작업 카드의 정체성과도 잘 맞는다.

### ADR-004. authoritative revision의 실제 commit 경계

- 심각도: **P0 Decision Blocker**
- 영향: DB revision은 증가했지만 슬라이드 파일은 이전 상태인 부분 성공, stale artifact 공개

설계의 CAS 예시는 `presentations.content`와 `revision`을 같은 SQL update로 변경한다.

- `docs/chat-first-slides-workbench-design-20260802.md:789-808`

현재 프레젠테이션 row에는 `content`뿐 아니라 `deck_plan`, `theme`, `mode`, `version`, `lifecycle_status`가 있고, Template V2 revision과 authored checkpoint 파일은 별도 저장 경계를 가진다.

- `servers/fastapi/models/sql/presentation.py:36-96`
- `servers/fastapi/services/template_v2_revision_service.py:14-69`
- `servers/fastapi/services/authored_checkpoint_service.py:226-272`

권장 결정:

- authoritative revision은 **사용자에게 보이는 덱 스냅샷의 publication commit token**으로 정의한다.
- 중간 생성 checkpoint는 덱 revision을 올리지 않는다.
- 최종 publication에서 presentation row, operation 결과, revision event, outbox를 하나의 DB transaction으로 commit한다.
- 외부 파일·object storage는 먼저 immutable key에 쓰고 hash를 검증한 뒤 DB pointer를 publication한다.
- worker attempt token을 checkpoint와 publication에 fencing token으로 사용한다.
- DB commit 후 cleanup 실패는 데이터 commit 실패로 되돌리지 않고 보상 작업으로 처리한다.
- 모든 기존 mutation endpoint 목록과 실제 저장 필드를 표로 만들고, revision service 우회가 0개일 때 feature flag를 연다.

### ADR-005. asset readiness와 bootstrap 실패 정책

- 심각도: **P0 Decision Blocker**
- 영향: 최초 메시지 유실, 검사 중 파일의 생성 시작, 격리 파일 접근, 무한 대기 카드

현재 문서는 비동기 추출 시 bootstrap이 준비를 기다리거나 processing 카드를 보일 수 있다고 두 가지 선택을 남겨 둔다.

- `docs/chat-first-slides-workbench-design-20260802.md:492`

권장 상태 계약:

- `uploaded/scanning/processing/ready`: bootstrap을 허용하고 첫 메시지와 생성 operation을 즉시 만든다.
- `ready`: 생성 handler가 바로 실행한다.
- `uploaded/scanning/processing`: operation은 `waiting_for_assets` 단계에 머물며 파일별 상태를 카드에 표시한다.
- `failed/quarantined/expired/deleted`: worker가 원본을 읽지 않고 operation을 `blocked` 또는 `failed`로 전환한다. 카드에서 해당 파일 제거·재첨부·재처리를 제공한다.
- 처리 제한 시간을 넘기면 `ASSET_PROCESSING_TIMEOUT`으로 종료하고 무한 polling하지 않는다.
- 동일 bootstrap idempotency key의 재전송은 같은 첫 메시지와 operation을 반환한다.

이 방식은 파일 처리 실패가 있어도 사용자가 처음 입력한 프롬프트와 파일 카드가 채팅에서 사라지지 않는다는 제품 요구를 보존한다.

## 5. “현재 설계가 최선인가?”에 대한 평가

### 제품·UX 관점

현재 방향은 요구사항에 거의 최선이다. ChatGPT/Genspark Slides와 유사하게 최초 요청부터 결과·수정·재시도·내보내기까지 오른쪽 대화가 작업의 기억이 되고, 중앙 캔버스는 결과와 직접 조작에 집중한다. 모든 팝업을 없애지 않고 OS picker와 정밀 편집 popover를 예외로 둔 것도 현실적이다.

### 시스템 설계 관점

아직 절대적인 최선은 아니다. 이유는 기능이 부족해서가 아니라 **단일 사용자·SQLite 데스크톱과 다중 서버 PostgreSQL 운영을 같은 구현 방식으로 설명하기 때문**이다. 현재 문서를 core contract와 deployment profile로 나누면 복잡도를 크게 낮출 수 있다.

최선의 형태는 다음 세 층이다.

```text
Product Contract
  최초 메시지 + operation card + command/action + revision + replay
        ↓
Domain Services
  BootstrapService / RevisionService / TimelineProjection / OperationRunner
        ↓
Runtime Adapters
  SQLite Local                         PostgreSQL Cluster
  immediate transaction + polling     row/advisory lock + notify/Redis + polling
```

핵심 계약은 두 프로파일에서 동일하고, lock·claim·wake-up만 adapter로 달라져야 한다. 이것이 현재 저장소의 self-hosted·Electron 성격과 향후 서버 확장성을 함께 만족하는 가장 좋은 절충안이다.

## 6. 권장 구현 순서

### Gate 0. ADR과 fixture

1. ADR-001~005 승인
2. command/event/action JSON fixture와 timeline projection fixture 작성
3. 모든 presentation mutation endpoint·service inventory 작성
4. SQLite/PostgreSQL의 position·job claim 경쟁 spike
5. 최초 prompt + 파일 2개의 bootstrap API contract test 작성

### Gate 1. P0-A 데이터 기반

1. conversation, asset, attachment, operation/event/artifact, outbox additive migration
2. 기존 conversation backfill dry-run과 중복 position 정리
3. dialect별 position allocator와 unique constraint
4. authoritative revision service를 읽기/쓰기 경로에 shadow 적용
5. 기존 UI는 그대로 유지

### Gate 2. P0-B 실행 기반

1. 기존 durable generation runner 패턴의 범용화
2. outbox claim·delivery·dead-letter·fencing
3. event append + operation state transaction
4. timeline snapshot/high watermark/SSE replay
5. process restart, lost wake-up, duplicate action, lease expiry 테스트

### Gate 3. P1-A 수직 슬라이스

1. 오른쪽 `WorkspaceChatPanel` 읽기 전용 timeline
2. 최초 프롬프트·첨부·접힌 생성 정보 복원
3. 기존 `quality.review` 하나만 operation adapter로 연결
4. shadow parity 99.9%와 silent loss 0 검증
5. 그 다음 export, slide edit, generation 순으로 확대

### Gate 4. UI 전환

1. 기능별 direct API 호출 제거
2. 채팅 카드 행동으로 점진 이동
3. legacy modal 제거는 마지막에 수행
4. feature flag off 시 같은 저장 데이터로 legacy UI 복귀 확인

## 7. 개발 착수 전 최종 체크리스트

### Gate A — DB

- [ ] SQLite와 PostgreSQL 지원 범위를 문서와 capability에 명시했다.
- [ ] 두 dialect에서 position 중복 0, idempotency 중복 side effect 0을 검증했다.
- [ ] 지원하지 않는 DB는 startup 또는 feature capability에서 명확히 차단한다.

### Gate B — Runner

- [ ] outbox row, claim 조건, fencing token, retry, dead-letter 계약이 있다.
- [ ] API 프로세스 재시작과 worker 강제 종료 후 작업 결과가 명시적으로 복구된다.
- [ ] wake-up 신호를 유실해도 polling으로 처리된다.

### Gate C — Timeline

- [ ] message position과 event cursor의 역할이 fixture로 고정돼 있다.
- [ ] 작업 완료 시 카드 위치가 이동하거나 중복 생성되지 않는다.
- [ ] snapshot과 stream 경계에서 누락 0, 중복 표시 0이다.

### Gate D — Revision

- [ ] template/adaptive/authored/Template V2의 publication commit 경계가 정의돼 있다.
- [ ] 모든 mutation 경로가 revision service를 통한다.
- [ ] 외부 파일 저장 실패와 DB commit 실패의 보상 정책이 있다.

### Gate E — Asset

- [ ] 모든 asset 상태에 대한 bootstrap 허용·대기·차단 행렬이 있다.
- [ ] 격리·만료 파일을 worker가 읽지 않는다.
- [ ] 파일 실패 후에도 최초 프롬프트와 파일 상태가 첫 메시지에 남는다.

## 8. 최종 결론

현재 수정 설계는 **89점이며, 제품 방향과 핵심 도메인 설계는 승인**할 수 있다. 1차 평가의 78점 설계보다 실제 운영 실패를 훨씬 잘 다루며, 최초 생성 정보가 오른쪽 채팅 안에 남는 ChatGPT형 경험도 정확하게 반영했다.

다만 “이 문서 그대로 모든 개발을 시작해도 되는가”에 대한 답은 **아니다**. Sprint 0과 P0-A의 준비 작업은 바로 시작하되, P0-B와 UI 전환 전에 DB 프로파일, operation runner/outbox, timeline projection, revision publication, asset readiness의 5개 ADR을 확정해야 한다.

이 다섯 결정을 반영하면 예상 점수는 **93/100**이며, 그 시점부터는 재설계보다 구현과 검증에 집중하는 것이 맞다.
