# Chat-first Slides Workbench 설계 평가 리포트

- 평가일: 2026-08-02
- 평가 대상: `docs/chat-first-slides-workbench-design-20260802.md`
- 평가 방법: 설계 문서의 제품·UX·기술 계약을 현재 FastAPI/Next.js 구현과 대조
- 판정: **방향 승인 / 구현 조건부 승인**
- 종합 점수: **78/100**

## 1. 총평

Chat-first Slides Workbench는 Presenton의 생성·수정·검토·내보내기 경험을 하나의 작업 타임라인으로 통합한다는 점에서 제품 방향이 명확하다. 특히 최초 프롬프트와 첨부 파일을 첫 사용자 메시지로 남기는 결정, 작업별 영속 카드, 승인·취소·재시도·되돌리기, 팝업 예외 원칙은 사용자 경험 측면에서 완성도가 높다.

그러나 현재 코드에는 설계가 전제로 삼는 다음 핵심 기반이 아직 없다.

1. 독립적인 conversation 레코드와 안전한 타임라인 순번 할당
2. 원본 파일명과 파일 수명을 보장하는 영구 자산 모델
3. 모든 덱 변경이 공유하는 단조 증가 리비전
4. 여러 서버 프로세스에서도 손실 없이 재생 가능한 이벤트 전달 계층

이 기반 없이 카드 UI부터 구현하면 정상 경로 데모는 가능하지만, 새로고침·동시 명령·장기 작업·파일 만료·되돌리기에서 데이터 불일치가 발생할 가능성이 높다. 따라서 제품 방향은 승인하되, P0에서 아래 Blocker를 해결한 다음 UI 마이그레이션을 시작해야 한다.

## 2. 점수표

| 평가 항목 | 가중치 | 점수 | 가중 점수 | 평가 |
|---|---:|---:|---:|---|
| 제품 비전과 사용자 맥락 연속성 | 15 | 93 | 14.0 | 최초 생성부터 편집까지 한 대화라는 원칙이 명확함 |
| 정보 구조와 카드 UX | 15 | 88 | 13.2 | 작업 카드, 예외 팝업, 캔버스 연계가 잘 정의됨 |
| Command/Event 계약 | 15 | 76 | 11.4 | 기본 구조는 좋지만 버전·행동 멱등성·전이 세부가 부족함 |
| 영속성·재접속·타임라인 | 15 | 70 | 10.5 | cursor 개념은 좋지만 스냅샷 경계와 전달 구조가 미정임 |
| 현재 코드와의 구현 적합성 | 15 | 62 | 9.3 | conversation·asset·revision 기반을 새로 만들어야 함 |
| 동시성·복구·되돌리기 | 10 | 72 | 7.2 | 방향은 맞지만 현재 버전 모델과 CAS 계약이 연결되지 않음 |
| 보안·개인정보·접근성 | 5 | 78 | 3.9 | 기본 원칙은 있으나 공유/삭제/보존 정책이 부족함 |
| 테스트·단계 전환·관측성 | 10 | 82 | 8.2 | 테스트 범위는 좋지만 점진 배포와 롤백 계약이 필요함 |
| **합계** | **100** |  | **77.7 → 78** | **조건부 승인** |

보조 지표:

- 제품 경험 설계: **91/100**
- 기술 설계 완성도: **74/100**
- 현재 코드 기준 구현 준비도: **62/100**
- P0 개선 후 예상 점수: **88~91/100**

## 3. 잘된 점

### 3.1 채팅의 역할이 명확하다

채팅을 단순 자연어 입력창이 아니라 작업 제어면으로 정의했다. 헤더, 캔버스, 채팅에서 시작된 명령이 같은 dispatcher와 operation 경로를 사용하는 결정은 기능별 UX와 오류 처리가 분산되는 것을 막는다.

### 3.2 최초 생성 맥락을 보존한다

최초 프롬프트, 파일, 템플릿, 언어, 장수 같은 정보가 생성 후 사라지지 않고 첫 사용자 메시지에 남는다. 사용자가 “무엇을 요청해서 이 덱이 만들어졌는가”를 다시 확인할 수 있어 ChatGPT형 경험의 핵심을 충족한다.

### 3.3 작업 단위가 사용자 관점과 일치한다

여러 trace 메시지를 흩뿌리지 않고 하나의 operation 카드가 상태를 갱신하는 구조는 장기 작업을 이해하기 쉽다. 계획, 진행, 승인, 결과, artifact, 오류를 구분한 카드 체계도 충분히 확장 가능하다.

### 3.4 LLM과 UI 실행 권한을 분리했다

LLM이 React 컴포넌트나 임의 이벤트를 생성하지 않고 고정 카드 레지스트리의 콘텐츠만 채우도록 제한했다. 이는 보안, 접근성, 테스트 가능성에 모두 유리하다.

### 3.5 팝업 제거 기준이 현실적이다

모든 팝오버를 없애는 극단적 접근 대신 작업 상태가 남아야 하는 기능만 채팅으로 이동하고, OS 파일 선택기나 정밀 조작 UI는 예외로 남겼다. 캔버스 편집성과 채팅 중심 경험 사이의 균형이 좋다.

### 3.6 실패와 복구가 1급 개념이다

재접속, 취소, 재시도, 체크포인트, revision conflict를 정상 흐름과 같은 수준으로 다룬다. 일반적인 데모 중심 설계보다 운영 관점이 강하다.

## 4. 현재 코드와의 핵심 차이

### 4.1 채팅은 아직 덱이 있어야 시작된다

현재 `ChatMessageRequest`는 `presentation_id`를 필수로 받는다.

- `servers/fastapi/models/chat.py:16-20`
- `servers/fastapi/api/v1/ppt/endpoints/chat.py:74-103`

설계의 “프레젠테이션보다 conversation을 먼저 생성” 흐름은 현 API와 바로 호환되지 않는다. 다만 현재 업로드 흐름은 문서 분해 후 `createPresentation()`으로 덱 ID를 만든 다음 outline으로 이동하므로, 실제 사용자 요구를 만족하기 위해 conversation을 반드시 덱보다 먼저 만들 필요는 없다.

### 4.2 conversation은 독립 엔터티가 아니다

현재 conversation은 `chat_history_messages.conversation_id`에 반복 저장되는 UUID일 뿐이다. conversation의 소유자, 연결된 덱, 상태, 제목, 다음 position, 생성 시각을 관리하는 테이블이 없다.

- `servers/fastapi/models/sql/chat_history_message.py:12-49`
- `servers/fastapi/services/chat/sql_chat_history.py:185-214`

이 상태에서는 draft conversation 결합, 한 덱의 기본 conversation 지정, 안전한 순번 할당, 대화 삭제/보존 정책을 구현하기 어렵다.

### 4.3 첨부 파일은 영구 자산이 아니다

현재 파일 업로드는 임시 서버 경로 문자열을 반환하고, 문서 분해도 다른 임시 텍스트 파일 경로를 반환한다.

- `servers/fastapi/api/v1/ppt/endpoints/files.py:35-96`
- `servers/fastapi/api/v1/ppt/endpoints/files.py:99-135`
- `servers/nextjs/app/(presentation-generator)/upload/components/UploadPage.tsx:347-400`

현재 채팅 첨부도 `name + extracted content` 구조이며 history에는 저장하지 않는다.

- `servers/fastapi/models/chat.py:7-20`
- `servers/fastapi/models/chat.py:33-36`

따라서 새로고침 후 최초 파일 카드의 파일명, 용량, 처리 상태, 미리보기 링크를 복원할 근거가 없다.

### 4.4 채팅 history는 카드 정보를 복원하지 못한다

현재 history 응답에는 `role`, `content`, `created_at`만 있다. DB에 `tool_calls`가 있어도 history serializer는 이를 내보내지 않으며 attachment, operation, metadata가 없다.

- `servers/fastapi/models/chat.py:33-44`
- `servers/fastapi/services/chat/sql_chat_history.py:70-99`
- `servers/fastapi/api/v1/ppt/endpoints/chat.py:47-71`

프론트의 메시지와 진행 activity도 현재 `Chat.tsx` 지역 상태에 의존한다.

- `servers/nextjs/app/(presentation-generator)/presentation/components/Chat.tsx:508-509`

### 4.5 덱 전체에 적용되는 리비전이 없다

`PresentationVersionModel`은 덱 스냅샷을 저장하지만, 현재 덱에 대한 단조 증가 `revision` 필드와 모든 mutation이 공유하는 compare-and-swap 계약은 없다.

- `servers/fastapi/models/sql/presentation.py:25-96`
- `servers/fastapi/models/sql/presentation_version.py:11-37`

설계의 `expectedRevision`은 현재 상태에서는 검증할 기준이 없다.

### 4.6 현재 SSE는 내구 이벤트 스트림이 아니다

현재 `/chat/message/stream`은 요청 연결 안에서 chunk/status/trace/complete를 바로 전송한다. 이벤트 ID, 저장 cursor, heartbeat, replay가 없다.

- `servers/fastapi/api/v1/ppt/endpoints/chat.py:94-133`

기존 trace를 adapter로 변환하는 방향은 맞지만, adapter만으로 영속성과 재연결이 생기지는 않는다.

### 4.7 현재 UI에는 이동해야 할 작업 UI가 실제로 많다

오른쪽 Chat은 고정 최대 370px이며, 헤더에 품질 검토, 템플릿 변경, 버전 기록, 공유, 생성 정보, 내보내기와 여러 Dialog/Popover가 분산돼 있다.

- `servers/nextjs/app/(presentation-generator)/presentation/components/PresentationPage.tsx:422-430`
- `servers/nextjs/app/(presentation-generator)/presentation/components/PresentationHeader.tsx:652-907`

따라서 UX 마이그레이션의 필요성 자체는 충분히 입증된다.

## 5. 구현 전 차단 문제 — P0 Blocker

### B1. conversation 실체와 bootstrap 트랜잭션이 없다

- 심각도: **Blocker**
- 영향: 최초 메시지 중복/유실, draft conversation 고아화, 대화 순서 충돌

설계는 conversation을 먼저 만든다고 하지만 conversation 테이블이 없고 command는 `presentationId`를 필수로 정의한다. 서로 상충한다.

권장 개선:

1. `workspace_conversations` 테이블을 추가한다.
2. `presentation_id`, `owner_id`, `status`, `title`, `next_position`, `created_at`, `updated_at`을 가진다.
3. 현재 흐름에서는 presentation을 `staging`으로 만든 직후 conversation, 최초 user message, `deck.generate` operation을 하나의 bootstrap 서비스에서 생성한다.
4. `POST /workspace/bootstrap`이 위 DB 레코드를 한 트랜잭션으로 만들도록 한다.
5. 파일 업로드 자체는 트랜잭션 밖에서 먼저 끝내고 안정적인 asset ID만 bootstrap에 전달한다.

프레젠테이션보다 conversation을 먼저 만드는 것은 제품 요구가 아니라 구현 선택이다. “최초 입력이 첫 채팅 메시지에 남는다”는 요구만 보존하면서 현재 데이터 모델과 더 잘 맞는 staging presentation 우선 방식을 권장한다.

### B2. 안정적인 업로드 자산 모델이 없다

- 심각도: **Blocker**
- 영향: 새로고침 후 파일 카드 복원 불가, 원본 파일명 소실, 임시 경로 만료, 권한/보존 정책 불명확

권장 개선:

- `uploaded_assets`를 먼저 도입한다.
- 최소 필드: `id`, `owner_id`, `original_name`, `safe_name`, `mime_type`, `size_bytes`, `storage_key`, `sha256`, `status`, `expires_at`, `created_at`.
- `chat_message_attachments.file_id`는 임시 경로가 아니라 `uploaded_assets.id`를 참조한다.
- 추출 텍스트는 별도 파생 artifact로 관리하고 원본 asset과 provenance로 연결한다.
- 파일 삭제, 만료, 재첨부, 중복 업로드, 악성 파일 검사 정책을 명시한다.

### B3. authoritative deck revision이 없다

- 심각도: **Blocker**
- 영향: AI와 직접 편집의 조용한 덮어쓰기, 잘못된 undo, stale approval 실행

권장 개선:

- `presentations.revision BIGINT NOT NULL DEFAULT 0`을 추가한다.
- 모든 서버 mutation은 `WHERE id=? AND revision=?` 조건으로 변경하고 성공 시 `revision=revision+1`로 갱신한다.
- tool service, 직접 저장, 템플릿/테마 적용, 버전 복원 모두 같은 계약을 사용한다.
- 응답과 `deck.revision_changed` 이벤트에 이전/새 리비전을 포함한다.
- 읽기 전용 작업은 검사 시작 리비전을 결과에 기록한다.

### B4. 타임라인 snapshot과 stream 사이의 유실 구간이 정의되지 않았다

- 심각도: **Blocker**
- 영향: 초기 timeline 조회 직후 발생한 이벤트 누락 또는 중복

현재 설계는 `GET /timeline`과 `GET /stream?after_cursor=`를 따로 정의하지만 두 호출 사이의 일관된 경계가 없다.

권장 개선:

```text
GET /timeline
→ { items, high_watermark_cursor }

GET /stream?after_cursor={high_watermark_cursor}
→ 이후 이벤트 replay + live stream
```

- timeline 응답을 만드는 동일한 DB snapshot에서 high watermark를 함께 반환한다.
- 클라이언트는 먼저 timeline을 그린 다음 그 cursor 이후를 구독한다.
- reconnect도 같은 cursor를 사용한다.
- cursor 만료 시 `CURSOR_EXPIRED`와 전체 재동기화 경로를 제공한다.

### B5. 여러 서버 프로세스에서 이벤트를 전달할 구조가 없다

- 심각도: **Blocker**
- 영향: worker A가 기록한 진행 상태를 SSE가 연결된 worker B가 알지 못함

“DB에 기록 후 전송”만으로는 다중 worker/instance 전달이 해결되지 않는다.

권장 개선:

- DB event row를 source of truth로 유지한다.
- Postgres LISTEN/NOTIFY 또는 Redis pub/sub은 깨우기 신호로만 사용한다.
- SSE worker는 신호를 받으면 DB cursor 이후 이벤트를 읽는다.
- 신호가 유실돼도 heartbeat 시 DB를 재확인한다.
- 외부 side effect와 event 기록이 필요한 handler에는 transactional outbox 패턴을 사용한다.

### B6. 대화 position 할당이 동시 요청에 안전하지 않다

- 심각도: **Blocker**
- 영향: 동시에 실행된 두 명령이 같은 position을 사용하거나 카드 순서가 뒤섞임

현재 `append_turn()`은 `max(position) + 1`을 계산하지만 `(conversation_id, position)` 유일 제약과 conversation row lock이 없다.

권장 개선:

- `workspace_conversations.next_position`을 `SELECT ... FOR UPDATE`로 증가시킨다.
- `(conversation_id, position)`에 unique constraint를 둔다.
- user message, assistant operation anchor, operation 생성은 같은 트랜잭션에서 순번을 예약한다.
- retry 시 새 position을 만들기보다 idempotency key로 기존 결과를 반환한다.

## 6. 높은 우선순위 개선 — P1

### H1. 스키마 버전과 forward compatibility가 없다

command, event, metadata, card payload에 `schema_version`을 추가한다. 프론트엔드는 알 수 없는 command/event를 버리지 말고 제목, 상태, 원문 요약을 보여주는 `UnknownOperationCard`로 fallback해야 한다.

### H2. 작업 상태에 `cancelling`과 `expired`가 필요하다

외부 생성 작업은 취소 요청 직후 즉시 멈추지 않을 수 있다. `running → cancelling → cancelled|succeeded|failed`를 지원해야 한다. 승인 카드는 만료 시각을 가지며, 승인 순간 expected revision과 권한을 다시 검사한다.

### H3. 작업 행동에도 멱등성과 상태 전제조건이 필요하다

`approve`, `cancel`, `retry`, `undo` 요청에 `action_idempotency_key`와 `expected_operation_version`을 포함한다. 이중 클릭이나 네트워크 재전송으로 승인·복원이 두 번 실행되지 않아야 한다.

### H4. slide index를 실행 대상의 정체성으로 사용하면 안 된다

슬라이드 추가·삭제·재정렬 중 index가 바뀔 수 있다. command target은 안정적인 `slide_id`와 `element_id`를 필수로 사용하고 index는 사용자 표시용 snapshot으로만 사용한다. 오래된 element ID는 명시적인 `TARGET_STALE`로 종료한다.

### H5. mode capability의 권위 있는 출처가 필요하다

authored/adaptive/template-v2의 기능 차이를 프론트가 추측하면 drift가 생긴다. 덱 조회 응답에 서버 계산 `capabilities`와 `capability_version`을 포함하고, 각 command handler도 실행 시 재검증해야 한다.

### H6. public share와 채팅 데이터의 경계를 명시해야 한다

공개 공유 링크는 슬라이드 결과만 제공하고 prompt, 첨부 파일명, operation, 오류 추적 ID를 절대 포함하지 않는다고 명시한다. 편집 권한이 없는 사용자에게 workspace API가 404 또는 403 중 어떤 정책을 쓰는지도 통일한다.

### H7. 점진 전환과 롤백 계획이 부족하다

기능 플래그 없이 기존 Chat/Popover를 한 번에 제거하면 회귀 시 되돌리기 어렵다.

권장 순서:

1. workspace read path를 shadow mode로 실행
2. 기존 trace와 새 event의 parity 측정
3. 사용자/덱 단위 feature flag로 새 패널 활성화
4. 기능별 dispatcher 전환
5. 안정화 후 legacy UI 제거

### H8. operation과 worker 실행의 lease가 필요하다

worker 장애 후 `running` 작업이 영원히 남지 않도록 `lease_owner`, `lease_expires_at`, heartbeat, 재획득 정책을 둔다. side effect가 있는 handler는 재실행 가능 여부를 명시한다.

## 7. 중간 우선순위 개선 — P2

### M1. 직접 편집 2초 세션은 카드가 너무 많아질 수 있다

2초 유휴만으로 세션을 끊으면 문서 작성 중 카드가 과도하게 생성된다. 다음 중 하나를 권장한다.

- 슬라이드 이탈/저장/30초 유휴 시 세션 종료
- 5분 동안 같은 슬라이드의 직접 편집을 하나의 요약 카드로 합치기
- 텍스트 입력은 글자 수가 아니라 의미 있는 저장 diff 단위로 기록

### M2. 타임라인 보존과 압축 정책을 P5보다 앞당겨야 한다

manual edit와 trace가 누적되면 P1부터 데이터가 빠르게 증가한다. 원본 operation/event는 보존하되 UI용 activity는 완료 후 요약하고, 상세 이벤트 보존 기간과 감사 로그 보존 기간을 분리해야 한다.

### M3. 오른쪽 440px 기본값은 실제 화면 검증이 필요하다

노트북 화면에서 캔버스가 지나치게 좁아질 수 있다. 1366×768, 1440×900, 1920×1080에서 다음을 검증한다.

- 캔버스 최소 작업 폭
- 패널 자동 축소 기준
- 카드 버튼 줄바꿈
- 비교 보기의 대체 레이아웃

### M4. artifact 무결성과 만료 상태가 필요하다

내보내기 카드에 파일 크기뿐 아니라 `sha256`, 생성 리비전, 생성 시각, 만료 시각을 저장한다. 덱이 변경된 뒤에는 “현재 덱보다 오래된 내보내기”라고 표시한다.

### M5. 최초 프롬프트와 파일명의 안전한 렌더링 기준이 필요하다

원문은 DB에 보존하더라도 화면에서는 Markdown/HTML을 안전하게 escape한다. 파일명 길이 제한, bidi 제어문자 처리, 키보드 접근 가능한 미리보기, 스크린리더용 파일 상태 문구를 명시한다.

### M6. LLM 컨텍스트 정책을 별도 계약으로 분리해야 한다

화면에 보이는 timeline 전체를 LLM에 넣지 않는다. 다음을 분리한다.

- 사용자에게 보이는 원본 메시지
- LLM에 제공하는 요약 대화
- 문서 검색으로 선택된 attachment chunk
- operation 결과 요약

각 입력의 토큰 한도와 provenance를 기록해야 비용 증가와 prompt injection 위험을 통제할 수 있다.

## 8. 권장 데이터 구조 보강안

### 8.1 `workspace_conversations`

```text
id
owner_id
presentation_id
status: active | archived | deleted
title
next_position
latest_cursor
created_at
updated_at
```

### 8.2 `uploaded_assets`

```text
id
owner_id
original_name
mime_type
size_bytes
storage_key
sha256
status
expires_at
created_at
```

### 8.3 `workspace_operations` 추가 필드

```text
schema_version
operation_version
lease_owner
lease_expires_at
approval_expires_at
cancel_requested_at
input_asset_ids
```

### 8.4 `workspace_operation_events` 추가 필드

```text
schema_version
stream_cursor
actor_type
actor_id
payload_size
```

### 8.5 `presentations` 추가 필드

```text
revision BIGINT NOT NULL DEFAULT 0
```

## 9. 수정 권장 구현 순서

기존 P0~P5를 다음 순서로 세분화하는 것을 권장한다.

### P0-A. 데이터 기반

- `workspace_conversations`
- `uploaded_assets`
- `chat_message_attachments`
- `presentations.revision`
- 순번/unique/CAS 마이그레이션

### P0-B. 계약 기반

- versioned command/event/action schema
- bootstrap endpoint
- timeline snapshot + high watermark
- durable event append/replay
- worker lease와 action idempotency

### P1-A. 읽기 전용 수직 슬라이스

- 새 오른쪽 패널
- 최초 프롬프트/파일 복원
- `quality.review` 작업 카드
- 재접속과 새로고침 복원

읽기 전용 작업으로 event, persistence, card UX를 먼저 검증한다.

### P1-B. artifact 수직 슬라이스

- `export.pptx`
- artifact 저장, 만료, 다운로드
- 덱 리비전과 artifact 리비전 비교

### P1-C. 변경 작업 수직 슬라이스

- 단일 `slide.edit`
- expected revision CAS
- 체크포인트
- 결과 강조와 undo operation

### P2 이후

- 재생성, 템플릿, 버전 복원, 공유
- 최초 생성 전체 진행 통합
- 직접 편집 카드
- 기존 modal/popover 제거

## 10. 필수 승인 기준

다음 조건을 만족하기 전에는 작업 팝업 제거와 전체 사용자 활성화를 시작하지 않는 것을 권장한다.

- [ ] 최초 프롬프트와 파일 2개가 새로고침 후 동일하게 복원된다.
- [ ] 원본 asset이 만료돼도 파일 카드가 깨지지 않고 상태를 설명한다.
- [ ] timeline 조회와 SSE 연결 사이에 발생한 이벤트가 누락되지 않는다.
- [ ] 같은 명령/승인/취소를 두 번 보내도 side effect가 한 번만 실행된다.
- [ ] 두 변경 명령이 같은 덱 리비전을 수정하려 하면 하나는 명시적 충돌로 종료된다.
- [ ] 동시에 추가된 두 메시지의 position이 중복되지 않는다.
- [ ] worker 종료 후 lease가 만료된 작업을 안전하게 복구하거나 실패 처리한다.
- [ ] 공개 공유 API에서 prompt, 첨부, operation 정보가 노출되지 않는다.
- [ ] authored/adaptive/template-v2 capability가 서버와 UI에서 동일하다.
- [ ] feature flag를 꺼 기존 UI로 즉시 복귀할 수 있다.

## 11. 최종 판정

현재 설계는 제품 UX 기준으로는 개발을 진행할 가치가 충분하며, 사용자 요구에도 잘 맞는다. 다만 구현 준비도는 62점으로 평가한다. 가장 먼저 카드 UI를 만드는 것보다 conversation, asset, revision, durable event의 네 기반을 먼저 확정해야 한다.

권장 결정:

- 제품 방향: **승인**
- P0-A/P0-B 기반 구현: **진행 권장**
- 모든 팝업 제거 및 전 사용자 배포: **기반 검증 전 보류**
- Blocker 해결 후 재평가 목표: **88점 이상**
