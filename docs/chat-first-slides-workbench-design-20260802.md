# Chat-first Slides Workbench 설계

- 상태: 2026-08-02 역사적 설계 기준안 · §16의 P0/P1 표기는 당시 구현 순서이며 현재 blocker 목록이 아님 · 2026-08-03 Gate 1~8 source-tree 동기화는 §24 참조
- 작성일: 2026-08-02
- 보완일: 2026-08-02
- 대상: Presenton 웹 편집기 및 생성 흐름
- 목적: 생성, 수정, 검토, 내보내기, 복구의 모든 작업을 오른쪽 채팅 타임라인에서 시작하고 확인하는 슬라이드 작업 환경
- 관련 문서: `docs/compose-ux-design.md`, `docs/template-v2-chat-parity-matrix-20260731.md`, `docs/chat-first-slides-workbench-design-review-20260802.md`, `docs/chat-first-slides-workbench-design-review-v2-20260802.md`, `docs/chat-first-slides-workbench-runbook-20260803.md`

## 1. 결정 요약

Presenton의 편집 화면을 **Chat-first Slides Workbench**로 전환한다.

사용자는 자연어 입력뿐 아니라 헤더 버튼, 캔버스 선택, 템플릿 선택 등 어떤 진입점에서 작업을 시작하더라도 오른쪽 채팅 타임라인에서 다음 내용을 확인한다.

1. 무엇을 하려는지
2. 현재 어느 단계인지
3. 어떤 슬라이드가 변경됐는지
4. 결과물 또는 오류가 무엇인지
5. 승인, 취소, 재시도, 되돌리기 등 다음 행동이 무엇인지

채팅은 단순한 AI 대화창이 아니라 작업 제어면(control plane)이 된다. 중앙 캔버스는 결과 확인과 정밀 직접 편집을 담당한다. 캔버스에서 발생한 직접 편집도 채팅에 압축된 작업 카드로 기록한다.

UI 구현보다 다음 네 기반을 먼저 확정한다.

1. 소유자와 프레젠테이션에 귀속되는 영속 conversation
2. 임시 경로가 아닌 안정적인 uploaded asset
3. 모든 변경 경로가 공유하는 authoritative deck revision
4. DB 이벤트를 원본으로 사용하는 재생 가능한 durable timeline

최초 생성은 업로드 완료 후 `staging presentation`, conversation, 첫 사용자 메시지, 첨부 연결, `presentation.generate` operation을 하나의 bootstrap 트랜잭션으로 생성한다. 이 기반의 승인 기준을 통과하기 전에는 기존 작업 팝업을 제거하거나 새 워크벤치를 전체 사용자에게 활성화하지 않는다.

팝업을 모두 기계적으로 없애지는 않는다. 다음 원칙을 적용한다.

- 생성·변경·검토·내보내기·복구처럼 상태가 남아야 하는 작업은 채팅 카드에서 처리한다.
- OS 파일 선택기, 브라우저 권한, 키보드 도움말처럼 시스템 종속이거나 일시적인 UI는 오버레이를 허용한다.
- 색상 피커, 차트 축 설정처럼 픽셀 단위의 즉시 조작은 작은 팝오버를 허용하되, 실제 저장 결과는 채팅 타임라인에 기록한다.
- 삭제, 전체 재생성, 버전 복원처럼 영향이 큰 작업의 승인은 별도 모달 대신 채팅의 인라인 승인 카드에서 받는다.

## 2. 목표와 비목표

### 2.1 목표

- 최초 프롬프트부터 완성된 덱까지 하나의 대화 맥락을 유지한다.
- 작업 진행 상태가 팝업이나 토스트에 가려지지 않고 타임라인에 남는다.
- 페이지 새로고침이나 재접속 뒤에도 작업 카드와 결과를 복원한다.
- 사용자가 현재 실행 중인 작업, 대상 슬라이드, 취소 가능 여부를 즉시 알 수 있다.
- AI 작업과 직접 편집이 충돌하지 않도록 덱 리비전과 체크포인트를 사용한다.
- 최초 파일을 임시 경로가 아니라 권한·수명·무결성이 정의된 자산으로 보존한다.
- 타임라인 조회와 실시간 스트림 사이에 이벤트가 누락되지 않도록 동일한 cursor 경계를 사용한다.
- 기존 Chat 도구, 생성 작업, authored/adaptive/template-v2 모드를 단계적으로 수용한다.
- 실패 시 오류 설명, 재시도, 안전한 복구 경로를 같은 카드에서 제공한다.

### 2.2 비목표

- 1차 구현에서 모든 편집 기능을 자연어만으로 강제하지 않는다.
- LLM이 임의의 React 컴포넌트나 실행 가능한 UI를 생성하도록 하지 않는다.
- 기존 슬라이드 렌더러와 편집 엔진을 전면 재작성하지 않는다.
- 기존 비동기 생성 API를 한 번에 제거하지 않는다.
- 여러 사용자의 실시간 동시 편집 전체를 1차 범위에 포함하지 않는다. 다만 리비전 충돌 계약은 미리 둔다.

## 3. UX 원칙

### 3.1 하나의 작업은 하나의 카드 묶음이다

사용자의 명령 하나는 `operation` 하나가 된다. 여러 개의 상태 메시지를 채팅에 흩뿌리지 않고, 하나의 작업 카드가 상태를 갱신한다. 중요한 중간 단계만 카드 내부 활동 목록에 누적한다.

예:

```text
[PPTX 내보내기]
검사 중 · 8/12
✓ 폰트 호환성 확인
✓ 이미지 해상도 확인
● 편집 가능성 검사

[취소]
```

완료 후 같은 카드는 다음과 같이 바뀐다.

```text
[PPTX 내보내기 완료]
12장 · 18.4 MB · 편집 가능 요소 96%

[다운로드] [결과 보기] [다시 내보내기]
```

### 3.2 계획과 실행을 구분한다

- 단순하고 되돌릴 수 있는 편집은 계획을 짧게 표시하고 즉시 실행한다.
- 덱 전체 재생성, 버전 복원, 다수 슬라이드 삭제는 영향 범위를 먼저 보여주고 승인을 기다린다.
- 승인을 기다리는 동안 durable operation 상태는 `awaiting_approval`이다. public projection은 호환을 위해 `approval_required`도 인식하며, 페이지를 떠나도 상태와 `approval_expires_at`이 유지된다.

### 3.3 캔버스와 채팅은 양방향으로 연결한다

- 캔버스에서 객체 또는 슬라이드를 선택하면 채팅 입력 상단에 대상 칩을 표시한다.
- 작업 카드의 슬라이드 번호를 클릭하면 해당 슬라이드로 이동하고 변경 영역을 강조한다.
- AI가 작업 중인 슬라이드를 자동으로 따라가는 기능을 제공하되 사용자가 캔버스를 조작하면 자동 추적을 즉시 중단한다.
- 결과 카드에는 가능한 경우 변경 전/후 요약과 되돌리기 버튼을 제공한다.

### 3.4 완료보다 복구 가능성을 우선한다

변경 작업은 성공 메시지만 남기지 않는다. 적용된 리비전, 체크포인트, 되돌리기 가능 여부를 함께 기록한다. 되돌리기 자체도 새 작업으로 실행하고 타임라인에 남긴다.

### 3.5 토스트는 상태 저장소가 아니다

토스트는 “작업이 채팅에 추가됨” 같은 순간 안내에만 사용한다. 성공, 실패, 다운로드, 재시도처럼 나중에 다시 확인해야 하는 정보는 반드시 작업 카드에 남긴다.

## 4. 정보 구조와 화면 배치

### 4.1 데스크톱 기본 배치

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Header: 제목 · 모드 · Undo/Redo · 발표 · 공유 · 내보내기                  │
├─────────────┬───────────────────────────────────┬──────────────────────────┤
│ Slide Rail  │ Canvas                            │ Workbench Chat           │
│             │                                   │                          │
│ 01          │ 선택·직접 편집·미리보기           │ 대화/작업 타임라인        │
│ 02          │                                   │ 진행 카드                 │
│ 03          │                                   │ 결과/승인/오류 카드        │
│             │                                   │                          │
│ + 새 슬라이드│                                   │ 대상 칩                    │
│             │                                   │ 첨부 + 입력 + 실행         │
└─────────────┴───────────────────────────────────┴──────────────────────────┘
```

- 왼쪽 슬라이드 레일: 현재 구조를 유지한다. 기본 너비 120px.
- 중앙 캔버스: 남은 공간을 사용한다.
- 오른쪽 워크벤치: 기본 440px, 최소 360px, 최대 560px, 드래그로 크기 조절 가능하다.
- 440px은 출시 확정값이 아니라 초기 실험값이다. 1366×768, 1440×900, 1920×1080에서 캔버스 최소 작업 폭과 카드 버튼 줄바꿈을 검증한 뒤 조정한다.
- 중앙 캔버스의 안전한 최소 작업 폭을 침범하면 워크벤치를 자동으로 360px까지 축소하고, 비교 콘텐츠는 세로형 레이아웃으로 전환한다.
- 오른쪽 워크벤치는 접을 수 있지만 실행 중 작업이 있으면 축소 상태에 진행 배지를 표시한다.
- 사용자가 조절한 폭과 접힘 상태는 로컬에 저장한다.

현재 `PresentationPage.tsx`의 고정 최대 폭 370px은 복합 카드, 비교 보기, 오류 복구 버튼에 부족하므로 제거한다.

### 4.2 태블릿과 모바일

- 1024px 미만: 오른쪽 워크벤치를 전체 높이 드로어로 전환한다. 진행 상태 배지는 항상 보인다.
- 768px 미만: 캔버스와 채팅을 탭으로 전환하되 작업 실행 중에는 채팅 탭에 실시간 상태를 표시한다.
- 승인 요청이나 실패는 채팅이 닫혀 있어도 탭 배지와 접근 가능한 알림으로 알린다.

### 4.3 오른쪽 워크벤치 내부

위에서 아래 순서로 구성한다.

1. 대화 제목과 연결 상태
2. 필터: 전체 / 실행 중 / 결과 / 오류
3. 메시지와 작업이 섞인 타임라인
4. 선택 대상 칩과 첨부 파일
5. 입력창, 전송/중지 버튼, 빠른 명령

필터는 보기를 바꿀 뿐 대화 맥락이나 서버 기록을 삭제하지 않는다.

## 5. 타임라인 항목과 카드 체계

### 5.1 항목 종류

| 종류 | 용도 | 예시 |
|---|---|---|
| User message | 자연어 명령, 최초 생성 프롬프트, 첨부 파일 및 UI 액션의 사용자 표현 | “이 자료로 영업 제안서를 만들어줘” + 파일 2개 |
| Assistant message | 설명, 질문, 요약 | “핵심 문장을 3개로 줄였습니다.” |
| Plan card | 실행 전 범위와 예상 결과 | 대상 3, 4, 5번 슬라이드 |
| Progress card | 장기 작업의 현재 단계 | 이미지 생성 2/4 |
| Approval card | 위험 작업 승인/거절 | 전체 덱 재생성 |
| Result card | 변경 결과와 후속 행동 | 3개 슬라이드 수정 완료 |
| Artifact card | 다운로드 가능한 결과물 | PPTX, PDF, 이미지 |
| Error card | 오류와 복구 행동 | 재시도, 상세 보기 |
| Selection card | 캔버스 선택 맥락 | 4번 슬라이드의 차트 |
| Manual edit card | 직접 편집의 압축 기록 | 텍스트 3건 수정, 이미지 1건 이동 |

### 5.2 렌더링 규칙

카드는 서버가 전달한 명령형 데이터와 프론트엔드의 고정 레지스트리로 렌더링한다.

```ts
const KNOWN_OPERATION_TYPES = [
  "presentation.generate",
  "presentation.edit",
  "presentation.delete",
  "slide.edit",
  "slide.regenerate",
  "slide.delete",
  "version.restore",
  "template.apply",
  "quality.review",
  "export.pptx",
  "export.pdf",
] as const;
```

현재 프론트엔드는 이 strict operation allowlist와 고정 label/action projection을 사용한다. 새 operation type은 backend public model, canonical fixture, OpenAPI 검증, frontend decoder/allowlist를 함께 갱신한 뒤에만 전용 카드나 공용 카드에 연결한다.

LLM 출력은 카드의 제목, 설명, 요약 같은 콘텐츠로만 사용한다. 컴포넌트 이름, 이벤트 핸들러, URL 스킴을 LLM이 직접 결정하지 않는다.

- timeline/message/operation/event/attachment와 receipt/error/capability 같은 versioned **응답 projection**에는 wire 필드 `schema_version`을 포함한다. command/action **요청**에는 이를 넣지 않으며 현재 v1 Pydantic 모델은 `extra="forbid"`로 정의되지 않은 필드를 거부한다.
- 프론트엔드가 알지 못하는 버전을 받으면 항목을 버리지 않고 `UnknownOperationCard`로 제목, 상태, 안전한 요약, 추적 ID만 표시한다.
- 서버가 선언하지 않은 카드 행동이나 URL은 렌더링하지 않는다.

### 5.3 카드 공통 구성

- 제목과 상태 아이콘
- 요청자, 시작 시각, 경과 시간
- 대상 슬라이드/요소
- 현재 단계와 진행률
- 접을 수 있는 활동 로그
- 결과 요약 또는 오류 원인
- 허용된 행동 버튼
- 적용된 덱 리비전과 되돌리기 가능 여부

### 5.4 최초 생성 메시지

최초 생성 화면에서 입력한 프롬프트와 첨부 파일은 생성 API에만 전달하고 사라지는 임시 값이 아니다. ChatGPT에서 파일과 함께 질문한 첫 메시지가 대화에 남는 것처럼, 프레젠테이션의 첫 번째 사용자 메시지로 영구 보존한다.

```text
사용자
이 사업계획서와 회사소개서를 바탕으로 투자 제안용 12장 발표자료를 만들어줘.

[📎 2026_사업계획서.pdf · 4.8 MB]
[📎 회사소개서.pptx · 12.1 MB]

[생성 정보 펼치기]
템플릿: Blue Business · 12장 · 한국어 · 16:9
```

표시 규칙:

- 프롬프트는 사용자가 입력한 원문을 첫 사용자 말풍선에 표시한다.
- 첨부 파일은 같은 말풍선 안에 파일 칩 또는 파일 카드로 표시한다.
- 파일 카드에는 파일명, 형식, 용량, 처리 상태를 표시하고 권한이 있으면 미리보기/다운로드를 제공한다.
- 템플릿, 언어, 장수, 화면비, 톤 같은 생성 설정은 기본적으로 접힌 `생성 정보` 영역에 표시한다.
- 문서에서 추출한 전체 텍스트나 내부 파일 경로는 채팅에 노출하지 않는다.
- 프롬프트 원문과 파일명은 저장 값과 표시 값을 구분한다. UI에서는 HTML/Markdown을 escape하고 파일명 길이, bidi 제어문자, 제어문자를 정규화한다.
- 파일 카드의 상태와 미리보기 행동은 키보드와 스크린리더로 동일하게 접근할 수 있어야 한다.
- 파일 처리 실패 시 해당 파일 카드에 오류와 `다시 첨부` 행동을 표시한다.
- 최초 메시지는 생성 완료 후에도 일반 대화 기록과 같은 방식으로 검색·복원한다.
- 오른쪽 패널이 처음 열리면 생성 진행 카드가 보이도록 하단으로 스크롤하되, 사용자는 첫 메시지까지 위로 스크롤해 원본 요청과 파일을 다시 확인할 수 있다.

## 6. 대표 사용자 흐름

### 6.1 새 프레젠테이션 생성

1. 사용자가 시작 화면에서 프롬프트, 참고 파일, 템플릿을 지정한다.
2. 파일은 먼저 업로드·검사하고 임시 경로가 아닌 안정적인 `assetId`를 발급받는다. 추출 텍스트는 원본 asset에 provenance로 연결된 파생 자산으로 저장한다.
3. 제출 시 `POST /workspace/bootstrap`을 한 번 호출한다.
4. bootstrap 서비스는 하나의 DB 트랜잭션에서 `staging presentation`, conversation, 첫 사용자 메시지, 첨부 연결, `presentation.generate` operation을 생성하고 그 operation의 `anchor_position`을 예약한다. 별도 assistant message row는 만들지 않는다.
5. 생성 설정은 첫 메시지의 접힌 `생성 정보` metadata로 저장한다.
6. 응답으로 받은 presentation/conversation/operation ID를 사용해 같은 대화를 오른쪽 워크벤치에서 연다. 별도의 “생성 진행 팝업”은 열지 않는다.
7. 첫 메시지 바로 다음에 생성 계획/진행 카드가 나타난다.
8. 아웃라인을 생성하고 필요한 경우 카드 내부에서 수정 또는 승인한다.
9. 슬라이드가 완성될 때마다 왼쪽 레일과 중앙 캔버스에 추가되고 생성 카드가 갱신된다.
10. 실패한 슬라이드나 처리 실패 파일만 카드에서 재시도할 수 있다.
11. 완료 카드에 생성 요약, 품질 검사, 내보내기, 수정 제안을 표시한다.

생성 중에도 사용자는 이미 완성된 슬라이드를 볼 수 있다. 생성 작업과 충돌하는 편집 명령은 대상 슬라이드 단위로 대기시키거나 사용자에게 충돌을 설명한다.

파일 업로드는 bootstrap 트랜잭션 밖에서 끝내되 bootstrap 실패 시 미연결 asset을 정리하는 만료 정책을 적용한다. bootstrap 재요청은 같은 wire `idempotency_key`로 동일한 presentation/conversation/operation을 반환하며 최초 메시지를 복제하지 않는다. staging 덱이 생성에 실패해도 대화와 첫 메시지는 유지하고 카드에서 재시도하거나 삭제할 수 있다.

### 6.2 선택 후 AI 수정

1. 사용자가 캔버스에서 텍스트, 이미지, 차트 또는 여러 슬라이드를 선택한다.
2. 입력창 위에 `4번 슬라이드 · 제목 텍스트` 같은 대상 칩이 나타난다.
3. 사용자가 “더 설득력 있게 줄여줘”라고 입력한다.
4. 작업 카드가 범위와 변경 계획을 보여준다.
5. 변경이 적용되면 해당 슬라이드로 이동하고 변경된 요소를 강조한다.
6. 결과 카드에서 `되돌리기`, `다른 버전`, `수정 내용 보기`를 선택할 수 있다.

### 6.3 직접 편집

1. 사용자는 중앙 캔버스에서 텍스트를 입력하거나 요소를 이동한다.
2. 잦은 키 입력마다 카드를 만들지 않고 의미 있는 저장 diff를 수집한다.
3. 슬라이드 이탈, 명시적 저장 또는 30초 유휴에서 편집 세션을 닫는다. 같은 슬라이드에서 5분 안에 이어진 세션은 하나의 요약 카드로 합칠 수 있다.
4. 채팅에 “직접 편집 · 4번 슬라이드 · 텍스트 2건”이라는 접힌 카드가 추가된다.
5. 카드에는 체크포인트와 되돌리기 행동이 연결된다.

직접 편집 기록은 기본적으로 압축 표시한다. 사용자가 활동 필터에서 “직접 편집 포함”을 끌 수 있으나 기록 자체는 유지한다.

### 6.4 PPTX/PDF 내보내기

1. 헤더의 내보내기 버튼은 팝오버를 직접 열지 않고 `export.*` 명령을 채팅으로 전달한다.
2. 옵션이 필요한 경우 내보내기 카드 안에서 형식과 품질을 고른다.
3. 사전 검사가 필요하면 같은 카드에 검사 진행률과 경고를 표시한다.
4. 완료 후 다운로드 버튼과 파일 정보를 제공한다.
5. 다운로드 링크 만료 시 카드에서 다시 생성할 수 있다.

브라우저의 실제 파일 저장 UI는 시스템 UI이므로 예외로 허용한다.

### 6.5 품질 검토와 자동 수정

1. 사용자가 헤더의 품질 검토를 누르거나 채팅으로 요청한다.
2. `quality.review` 카드가 검사 항목과 진행률을 표시한다.
3. 결과는 심각도, 슬라이드, 문제 유형으로 묶어 표시한다.
4. 각 항목에는 `이동`, `이 항목 수정`, `모두 수정`, `무시` 행동이 있다.
5. v1의 `quality.review`는 검사 결과와 서버가 허용한 후속 행동만 제시한다. 자동 수정 command는 현재 public command allowlist에 없으므로 별도 계약·fixture·handler가 추가되기 전에는 노출하지 않는다.

### 6.6 전체 재생성과 버전 복원

1. 요청 즉시 영향 범위, 보존되는 항목, 되돌릴 체크포인트를 보여준다.
2. 카드가 durable `awaiting_approval` 상태가 된다(`approval_required` public 호환 상태도 decoder가 인식한다).
3. 사용자가 승인하면 실행하고, 거절하면 `cancelled`로 종료한다.
4. 실행 중에는 새 덱 리비전을 잠그지 않고 서버의 낙관적 리비전 검사를 사용한다.
5. 충돌 시 변경을 덮어쓰지 않고 최신 상태 재읽기 또는 새 분기로 실행하도록 안내한다.

### 6.7 오류와 재접속

- 네트워크가 끊기면 카드를 실패로 확정하지 않고 `reconnecting`으로 표시한다.
- 재접속 시 마지막 수신 시퀀스 다음부터 이벤트를 요청한다.
- 서버 작업이 계속 실행 중이면 같은 카드를 이어서 갱신한다.
- 서버가 작업을 찾지 못하거나 터미널 오류가 확인된 경우에만 실패 카드로 전환한다.

## 7. 명령과 이벤트 계약

### 7.1 명령

모든 진입점은 직접 API를 제각각 호출하지 않고 `WorkspaceCommandDispatcher`를 통한다.

```ts
type WorkspaceCommandRequest = {
  command: string;
  commandType?:
    | "presentation.edit"
    | "presentation.delete"
    | "slide.edit"
    | "slide.regenerate"
    | "slide.delete"
    | "version.restore"
    | "template.apply"
    | "quality.review"
    | "export.pptx"
    | "export.pdf";
  idempotencyKey: string;
  expectedRevision?: number;
  assetIds?: string[];
  context?: Record<string, unknown>;
};
```

위 타입은 프론트엔드 내부 camelCase 모델이다. serializer가 `commandType`, `idempotencyKey`, `expectedRevision`, `assetIds`를 각각 wire의 `command_type`, `idempotency_key`, `expected_revision`, `asset_ids`로 변환한다. presentation scope는 route path에서 정하고 conversation은 서버가 presentation에 귀속해 해석하며, body는 Pydantic `extra="forbid"` 계약을 따른다. bootstrap의 `presentation.generate`는 `WorkspaceBootstrapRequest.operation_type` 경로이며 일반 command request allowlist와 구분한다.

헤더 버튼, 채팅 메시지, 단축키, 컨텍스트 메뉴는 모두 같은 명령을 만든다. UI 진입점은 달라도 실행과 기록 계약은 동일하다.

- bootstrap 이후 command body에는 presentation/conversation ID를 중복하지 않는다. presentation ID는 route path에 있고 conversation은 owner-scoped presentation에서 서버가 결정한다.
- 현재 프론트엔드는 command/action에 projection의 `expectedRevision`을 전달한다. v1 wire 모델의 `expected_revision`은 기존 호출 호환을 위해 optional이며, 생략되면 서버가 잠근 authoritative 현재 revision을 operation에 snapshot한다. 호출자가 보낸 값이 오래됐으면 `409 revision_conflict`로 종료한다.
- 실행 대상의 정체성은 안정적인 `slideId`와 `elementId`로만 표현한다. 슬라이드 index는 사용자 표시용 snapshot이며 실행 시 다시 해석하지 않는다.
- 존재하지 않거나 교체된 ID는 다른 대상을 추측해 수정하지 않고 operation별 중앙 error registry의 안전한 오류로 종료한다.
- 서버는 strict `command_type` allowlist를 검증하고 알 수 없는 요청 필드를 거부한다. 응답 decoder는 `schema_version`을 검증하며 unknown version/operation은 mutation을 허용하지 않는 safe fallback 카드로 투영한다.

### 7.2 작업 상태

```text
create
  → queued | waiting_for_assets | blocked | awaiting_approval

awaiting_approval (public compatibility: approval_required)
  → queued (approve) | cancelled

queued
  → running | cancelled

running
  → succeeded | failed | cancelling | dead_letter

cancelling
  → cancelled | succeeded | failed
```

추가 표시 상태인 `reconnecting`은 프론트엔드 연결 상태이며 서버 작업의 영구 상태가 아니다.

상태 전이 규칙:

- 현재 durable operation terminal 상태는 `succeeded`, `failed`, `cancelled`, `dead_letter`이다. `expired`는 asset/artifact 상태나 approval error code로 쓰일 수 있지만 operation state가 아니다.
- 터미널 상태에서 원래 작업을 다시 실행하지 않는다. 재시도는 `retryOfOperationId`를 가진 새 작업이다.
- `awaiting_approval`/`approval_required`에서만 승인 행동을 허용하고, 승인하면 runner가 claim할 수 있도록 `queued`로 전이한다. 거절 전용 wire action은 없으며 현재 UI의 취소는 `cancel` action을 사용한다.
- 승인 대기 작업에는 `approval_expires_at`을 두고 승인 순간 권한, 만료, operation version, presentation revision을 다시 검사한다. 기한이 지나면 상태를 임의로 `expired`로 바꾸지 않고 typed `approval_expired` 오류를 반환한다.
- `running`에서 취소 요청을 받으면 즉시 `cancelled`로 단정하지 않고 `cancelling`으로 바꾼다. 외부 실행이 이미 완료된 경쟁 상황에서는 최종 상태가 `succeeded`일 수 있다.
- 각 전이는 `operationVersion`을 1 증가시키는 비교 후 갱신(CAS)으로 수행한다.
- 진행률은 뒤로 가지 않는다. 작업 단계를 바꾸는 경우 단계별 진행률과 전체 진행률을 구분한다.
- worker는 실행 중인 작업에 lease를 유지한다. lease 만료 후 재획득은 handler가 재실행 안전 또는 복구 가능하다고 선언한 경우에만 허용한다.

### 7.3 이벤트

```ts
type WorkspaceOperationEventV1 = {
  schema_version: 1;
  id: string;
  operation_id: string;
  operation_version: number;
  sequence: number;
  cursor?: string | null;
  operation_anchor_position?: number | null;
  type: string;
  status?: string | null;
  message?: string | null;
  progress?: number | null;
  error?: string | null;
  safe_message?: string | null;
  retryable?: boolean | null;
  result?: unknown;
  created_at?: string | null;
};
```

`sequence`는 작업 안에서 단조 증가하고 `(operation_id, sequence)`는 유일하다. `cursor`는 durable event log의 저장 순서를 나타내는 불투명하고 단조 증가하는 값이며, API는 owner/presentation 권한으로 이 로그를 필터링해 투영한다. 프론트엔드는 이벤트 ID로 중복을 제거하고 마지막 `cursor` 다음부터 재연결한다.

현재 revision 변경 완료 신호는 `operation.result`와 operation card의 `expected_revision`/`result_revision` projection을 사용한다. 이벤트 payload는 종류별 allowlist와 크기 제한을 가지며 원본 문서, 내부 경로, 예외 스택을 포함하지 않는다. actor, target, activity summary, 허용 행동은 개별 event가 아니라 operation timeline item에 서버가 투영한다.

### 7.4 행동

카드 버튼은 서버가 허용한 행동만 렌더링한다.

```ts
type WorkspaceAllowedActionV1 = {
  id: "approve" | "reject" | "cancel" | "retry" | "undo" | "download" | "focus";
  label: string;
  variant: "primary" | "secondary" | "danger";
  enabled: boolean;
  reason?: string | null;
};

type WorkspaceOperationActionRequestV1 = {
  action: "approve" | "cancel" | "retry";
  idempotency_key: string;
  action_id?: string;
  expected_operation_version?: number;
  expected_revision?: number;
};
```

`WorkspaceAllowedActionV1`은 표시/비변경 행동까지 포함하는 server projection이고, action POST의 mutation allowlist는 `approve`, `cancel`, `retry` 세 가지다. 프론트엔드 내부 camelCase 모델은 API serializer에서 위 snake_case wire body로 변환되며 요청 자체에는 `schema_version`을 보내지 않는다.

프론트엔드가 작업 상태를 추측해 위험한 버튼을 활성화하지 않는다.

- 행동 실행은 `(owner_scope, operation_id, action_type, idempotency_key_hash)` receipt 제약으로 멱등성을 보장한다. `action_id`가 있으면 `(operation_id, action_type, action_id)`도 유일하다.
- wire의 `expected_operation_version`이 오래됐으면 typed `409 operation_state_conflict`를 반환하므로 클라이언트는 timeline을 다시 읽어 최신 operation projection을 사용한다.
- `approve`와 `retry`처럼 실행 또는 새 실행을 허용할 수 있는 행동은 처리 직전에 operation version, 덱 revision과 권한을 다시 검사한다. `undo`는 server projection에 미지원 사유를 가진 disabled action으로만 노출되며 v1 action POST allowlist에는 없다.
- 알 수 없는 action ID는 무시하지 않고 명시적인 4xx 오류로 종료한다.

## 8. 서버 API 설계

### 8.1 자산 업로드와 bootstrap

```http
POST /api/v1/ppt/workspace/assets
GET  /api/v1/ppt/workspace/assets/{asset_id}
POST /api/v1/ppt/workspace/presentations/{presentation_id}/bootstrap
```

`POST /api/v1/ppt/workspace/bootstrap?presentation_id=...`는 초기 클라이언트를 위한 호환 경로다. asset upload는 multipart form의 `presentation_id`를 필수로 받아 owner와 presentation 범위를 함께 검증한다.

`POST /assets`는 multipart 업로드를 받아 저장·파일명 정규화·MIME 확인·크기 제한·악성 파일 검사를 수행하고 안정적인 `asset_id`를 반환한다. 원본 추출이 비동기이면 `processing` 상태를 반환하며 bootstrap은 정책에 따라 준비를 기다리거나 파일 처리 단계를 생성 카드에 표시한다.

bootstrap 요청 예:

```json
{
  "idempotency_key": "bootstrap-demo-0001",
  "prompt": "이 자료로 투자 제안용 12장 발표자료를 만들어줘.",
  "asset_ids": ["66666666-6666-4666-8666-666666666666"],
  "generation_settings": {
    "template_id": "blue-business",
    "language": "ko",
    "n_slides": 12,
    "aspect_ratio": "16:9"
  },
  "operation_type": "presentation.generate",
  "expected_revision": 0
}
```

bootstrap 요청도 `extra="forbid"`이며 응답 projection 전용인 `schema_version`은 보내지 않는다. 위 필드와 값의 기준 fixture는 `contracts/chat-first-workspace/v1/bootstrap-and-assets.json`이다.

bootstrap 응답 예:

```json
{
  "schema_version": 1,
  "receipt_id": "44444444-4444-4444-8444-444444444444",
  "conversation_id": "22222222-2222-4222-8222-222222222222",
  "first_user_message_id": "33333333-3333-4333-8333-333333333333",
  "operation_id": "44444444-4444-4444-8444-444444444444",
  "status": "queued",
  "operation_state": "queued",
  "duplicate": false,
  "timeline": {
    "schema_version": 1,
    "conversation_id": "22222222-2222-4222-8222-222222222222",
    "items": [],
    "next_before_position": null,
    "event_high_watermark": "1",
    "has_more": false,
    "revision": 0
  }
}
```

bootstrap은 다음을 보장한다.

- 요청자의 asset 소유권과 상태를 먼저 검증한다.
- staging presentation, conversation, 첫 user message, attachment rows와 `presentation.generate` operation을 한 DB 트랜잭션으로 생성하고 operation의 `anchor_position`을 함께 예약한다.
- conversation의 메시지 position을 row lock으로 예약한다.
- 같은 idempotency key의 재요청은 기존 묶음을 반환한다.
- 작업 큐 발행은 transactional outbox를 사용해 DB commit과 실행 요청 사이의 유실을 막는다.

### 8.2 명령, 조회, 행동 API

```http
POST /api/v1/ppt/workspace/presentations/{presentation_id}/commands
GET  /api/v1/ppt/workspace/presentations/{presentation_id}/timeline?before_position=...
GET  /api/v1/ppt/workspace/presentations/{presentation_id}/events?after=...&stream=1
POST /api/v1/ppt/workspace/operations/{operation_id}/actions
```

`POST /commands` 응답 예:

```json
{
  "schema_version": 1,
  "receipt_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "operation_id": "99999999-9999-4999-8999-999999999999",
  "status": "queued",
  "duplicate": false,
  "timeline": {
    "schema_version": 1,
    "conversation_id": "22222222-2222-4222-8222-222222222222",
    "items": [],
    "next_before_position": null,
    "event_high_watermark": "1042",
    "has_more": false,
    "revision": 3
  }
}
```

command/action 응답은 위 versioned receipt envelope를 기준으로 한다. 단계적 클라이언트 전환을 위해 v1에서는 timeline의 `conversation_id`, `items`, `next_before_position`, `event_high_watermark`, `has_more`, `revision`을 최상위에도 동일하게 투영한다.

동일한 소유자, 프레젠테이션, 명령 종류, `idempotency_key` 조합이 반복되면 새 작업을 만들지 않고 기존 작업을 반환한다.

timeline 응답은 조회 시점의 일관된 snapshot과 스트림 경계를 함께 반환한다.

```json
{
  "schema_version": 1,
  "conversation_id": "22222222-2222-4222-8222-222222222222",
  "items": [],
  "next_before_position": null,
  "event_high_watermark": "1042",
  "has_more": false,
  "revision": 3
}
```

클라이언트는 timeline을 먼저 렌더링한 뒤 `event_high_watermark` 다음부터 event stream을 구독한다. 페이지네이션으로 과거 항목을 더 읽어도 현재 실시간 cursor는 되돌리지 않는다.

### 8.3 Durable SSE 전송

- 기존 `/chat/message/stream`의 전송 형식과 프론트 파서를 호환 어댑터에서 재사용할 수 있지만, 프로세스 메모리를 이벤트 원본으로 사용하지 않는다.
- 이벤트 이름은 `workspace_event`, 연결 유지용 `heartbeat`, 복구 불가 오류용 `stream_error`로 제한한다.
- 각 SSE의 `id`에는 저장된 `cursor`를 넣어 `Last-Event-ID` 또는 `after` query로 재연결한다.
- 이벤트를 먼저 DB에 기록한 뒤 전송한다. 전송 성공을 영속화 조건으로 삼지 않는다.
- 연결이 끊겨도 작업 실행은 서버에서 계속된다. 사용자의 명시적 취소만 작업을 중단한다.
- DB event row가 source of truth다. Postgres `LISTEN/NOTIFY` 또는 Redis pub/sub은 새 이벤트를 읽도록 깨우는 신호로만 사용한다.
- SSE 프로세스는 신호를 받으면 자신의 마지막 cursor 이후 DB rows를 조회한다. 신호가 유실돼도 heartbeat마다 DB를 다시 확인한다.
- 보존 기간 밖의 cursor에는 `CURSOR_EXPIRED`를 반환하고 클라이언트는 timeline snapshot부터 전체 재동기화한다.
- 외부 side effect와 이벤트 기록을 함께 보장해야 하는 handler는 transactional outbox 또는 동등한 전달 보장 패턴을 사용한다.

### 8.4 기존 서비스와의 호환

1차 구현에서 기존 기능을 다시 작성하지 않는다.

- 대화형 슬라이드 도구 실행: 현재 `chat.py`와 `services/chat/service.py`의 trace를 `WorkspaceEvent`로 변환한다.
- 최초 덱 생성: 기존 생성 작업을 조회하고 진행 상태를 작업 이벤트로 투영한다.
- 내보내기, 품질 검사: 기존 엔드포인트를 command handler가 호출하고 결과를 작업 카드 계약으로 감싼다.
- 기존 `async_tasks`는 읽기 전용 생성 작업 조회 계층으로 유지한다. 범용 작업 저장소로 확장하지 않는다.

호환 어댑터가 안정화된 뒤 기능별 UI에서 기존 API 직접 호출을 제거한다.

## 9. 영속 데이터 모델

### 9.1 `workspace_conversations`

conversation은 메시지의 임의 UUID 묶음이 아니라 소유권, 생명주기, 메시지 순서, 최신 이벤트 위치를 가진 실체다.

| 필드 | 설명 |
|---|---|
| id | 대화 ID |
| owner_id | 소유자 |
| presentation_id | staging을 포함한 덱 ID |
| status | active / archived / deleted |
| title | 사용자 표시 제목 |
| next_position | 다음 메시지 순번 |
| latest_cursor | 마지막으로 저장된 workspace event cursor |
| created_at / updated_at | 시각 |

메시지를 추가할 때 conversation row를 `SELECT ... FOR UPDATE`로 잠그고 `next_position`을 예약한다. `workspace_messages`에는 다음 유일 제약을 둔다.

```text
(conversation_id, position)
```

user message와 operation을 함께 만드는 요청은 하나의 트랜잭션에서 message `position`과 operation `anchor_position`에 필요한 위치를 예약한다. `max(position) + 1` 방식은 사용하지 않는다.

### 9.2 `workspace_assets`

| 필드 | 설명 |
|---|---|
| id | 안정적인 자산 ID |
| owner_id | 업로드 소유자 |
| original_name | 정규화·검증 후 보존하는 사용자 표시 파일명 |
| media_type | 서버가 확인한 MIME |
| size_bytes | 파일 크기 |
| storage_key | 외부에 노출하지 않는 저장소 키 |
| sha256 | 무결성 및 중복 판별 해시 |
| state | uploaded / scanning / processing / ready / failed / quarantined / expired / deleted |
| failure_code / failure_detail | 안전한 기계 코드와 내부 진단 상세 |
| version | asset 상태 전이 CAS 버전 |
| expires_at | 원본 만료 시각, 영구 보존이면 null |
| deleted_at | 삭제 상태 전이 시각 |
| created_at / updated_at | 시각 |

- 원본, 추출 텍스트, 미리보기의 파생 관계가 필요하면 public 계약을 추가하기 전까지 내부 metadata/별도 artifact 경계에서 관리한다. 현재 `workspace_assets` schema에 존재하지 않는 provenance column을 가정하지 않는다.
- asset ID를 아는 것만으로 접근할 수 없으며 모든 조회에서 owner/덱 권한을 재검증한다.
- 동일 소유자의 같은 sha256 업로드를 재사용할 수 있지만, 새 메시지의 attachment row는 별도로 만든다.
- 격리·만료·삭제된 파일은 다운로드하지 못하더라도 최초 파일 카드의 표시 metadata와 상태는 유지한다.
- bootstrap과 연결되지 않은 asset은 설정된 유예 시간 후 정리한다.

### 9.3 채팅 기록과 최초 첨부 파일

새 Chat-first timeline은 legacy chat table을 증설하는 대신 `workspace_messages`와 operation anchor projection을 사용한다. legacy chat 기록은 기존 경로의 호환 데이터로 남고 자동으로 같은 ID 공간이라고 가정하지 않는다.

- `workspace_messages`는 `conversation_id`, 단조 증가 `position`, `role`, `content`, `schema_version`, `metadata`를 보존한다.
- 최초 생성 프롬프트도 일반 `role="user"` workspace message로 저장하며 `metadata.source="initial_generation"`으로 구분한다.
- UI 버튼으로 시작한 작업도 사람이 이해할 수 있는 사용자 행동 메시지를 만든다. 예: “PPTX로 내보내기”.
- 작업 카드는 별도 assistant message row를 만들지 않는다. `workspace_operations.anchor_position`에 예약된 operation anchor를 timeline에서 `anchor_type="operation"`, `operation_id`로 투영한다.
- 대화 기록 API는 message anchor의 message ID·position과 operation anchor의 operation ID·position을 구분하고, 안전하게 필터된 metadata를 반환한다.
- retry는 기존 idempotency 결과를 반환하거나 명시적인 새 작업 메시지를 만들며 동일 position을 중복 예약하지 않는다.

최초 첨부 파일은 메시지 metadata에 파일명만 복사하지 않고 `workspace_message_attachments`로 연결한다.

| 필드 | 설명 |
|---|---|
| id | 첨부 연결 ID |
| message_id | 최초 사용자 메시지 ID |
| asset_id | nullable live pointer인 `workspace_assets.id`; 삭제 시 `ON DELETE SET NULL` |
| asset_id_snapshot | 과거 첨부 identity인 immutable asset ID |
| ordinal | 메시지 안의 안정적 표시 순서 |
| display_name | 사용자에게 보여줄 정규화된 파일명 |
| media_type | 업로드 당시 파일 형식 |
| size_bytes | 업로드 당시 크기 |
| asset_state | uploaded / scanning / processing / ready / failed / quarantined / expired / deleted |
| asset_sha256 | 업로드 당시 무결성 해시 |
| asset_failure_code / asset_failure_detail | 당시 실패 코드와 안전하게 투영할 진단 원본 |
| created_at | 첨부 시각 |

- 파일의 실제 저장 경로나 추출 텍스트를 attachment에 중복 저장하지 않는다.
- timeline은 immutable snapshot을 역사적 source of truth로 사용하고 live asset의 현재 상태·version·download/preview 가능 여부를 별도 필드로 투영한다.
- 대화 기록 API는 접근 권한을 확인한 뒤 표시 metadata와 만료 가능한 미리보기/다운로드 경로만 반환한다.
- 원본 row가 변경·만료·삭제돼도 파일명, MIME, 크기, sha256, 당시 상태와 실패 표시는 바뀌지 않는다. 현재 상태에 따라 다운로드/미리보기 또는 재첨부·retry 경로를 제공한다.

### 9.4 `workspace_operations`

| 필드 | 설명 |
|---|---|
| id | 작업 ID |
| owner_id / owner_scope | 로그인 소유자 FK와 anonymous를 포함한 멱등성·권한 scope |
| presentation_id | 덱 ID |
| conversation_id | 대화 ID |
| anchor_position | conversation 안의 operation card 위치 |
| kind | public projection의 `operation_type`/command 종류 |
| state | 현재 durable 상태 |
| version | 상태·행동 CAS용 `operation_version` projection 원본 |
| request_payload / request_hash | 정규화한 command·target·asset 입력과 hash |
| result | 최종 결과 JSON |
| error_code / error_detail | 중앙 registry용 기계 코드와 내부 진단 |
| progress | 최신 진행 상태 JSON |
| idempotency_key_hash | 원문을 저장하지 않는 중복 실행 방지 hash |
| expected_revision | 실행 시작 전 기대 덱 리비전 |
| result_revision | 성공 후 authoritative 덱 리비전 |
| retry_of_operation_id | 재시도 원본 작업 |
| approval_expires_at | 승인 만료 시각 |
| attempt_number / attempt_token / fencing_token | worker attempt와 fencing |
| lease_expires_at / heartbeat_at | worker 실행 lease와 heartbeat |
| retry_count / max_attempts / next_attempt_at / backoff_seconds | 재시도 정책과 다음 claim 시각 |
| cancel_requested_at / cancelled_at | 취소 요청·완료 시각 |
| next_event_sequence | 다음 operation-local event 순번 |
| created_at / updated_at / started_at / completed_at | 수명주기 시각 |

권장 유일 제약:

```text
(owner_scope, presentation_id, kind, idempotency_key_hash)
```

worker는 heartbeat로 lease를 연장한다. 만료된 lease를 재획득할 때는 마지막 영속 단계에서 복구하며, 외부 side effect를 재호출해도 안전하지 않은 handler는 자동 재실행하지 않고 `dead_letter`로 종료한다. 이 경우 외부 계약에는 안전한 `operation_recovery_required` 오류 코드와 `manual_recovery_required` 사유를 투영한다.

작업 행동의 멱등성은 별도 `workspace_action_receipts`에 다음 키로 기록한다.

```text
(owner_scope, operation_id, action_type, idempotency_key_hash)
(operation_id, action_type, action_id)
```

### 9.5 `workspace_operation_events`

| 필드 | 설명 |
|---|---|
| cursor | 전역 증가 event cursor이자 storage PK |
| owner_id | 권한 필터용 소유자 FK |
| schema_version | 이벤트 payload 스키마 버전 |
| operation_id | 작업 ID |
| operation_version | 이벤트 당시 작업 버전 |
| sequence | 작업 내부 순번 |
| event_type | 이벤트 종류 |
| payload | 구조화 세부 정보 |
| created_at | 시각 |

권장 유일 제약:

```text
(operation_id, sequence)
```

`cursor`는 API 외부에서 의미를 해석하지 않는 불투명 문자열로 직렬화한다. storage에서는 DB 증가 정수를 사용하며 연속적일 필요는 없지만 저장 순서는 항상 비교 가능해야 한다. presentation/conversation 경계는 event row에 중복 저장하지 않고 operation join으로 권한을 확인한다. event append와 operation 최신 상태 갱신은 같은 트랜잭션에서 수행한다.

### 9.6 `workspace_artifacts`

| 필드 | 설명 |
|---|---|
| id | 결과물 ID |
| operation_id | 생성 작업 ID |
| presentation_id | 덱 ID |
| kind / mime_type | pptx / pdf / image 등 |
| storage_key | 내부 저장소 키 |
| size_bytes / sha256 | 크기와 무결성 |
| source_revision | 결과물을 생성한 덱 리비전 |
| status | ready / expired / deleted / failed |
| expires_at | 다운로드 만료 시각 |
| created_at | 생성 시각 |

서명 URL 자체는 영속화하지 않고 요청 시 발급한다. `source_revision`이 현재 덱보다 낮으면 카드에 `현재 덱보다 오래된 내보내기`를 표시한다.

### 9.7 `presentations` 보강

`presentations`에 다음 필드를 추가한다.

```text
revision BIGINT NOT NULL DEFAULT 0
lifecycle_status: staging | active | generation_failed | deleted
```

bootstrap은 빈 덱을 `staging`으로 만들고 최소 편집 가능 상태가 되면 `active`로 전환한다. 생성 실패 시 `generation_failed`로 남겨 conversation에서 재시도 또는 삭제할 수 있게 한다.

모든 서버 mutation은 `revision`을 authoritative revision으로 사용한다. 기존 snapshot 버전 번호나 클라이언트 undo index를 현재 리비전으로 대신 사용하지 않는다.

### 9.8 보존과 압축

- user/assistant 메시지, operation 최종 요약, attachment 표시 metadata는 덱 삭제 또는 명시적 보존 정책까지 유지한다.
- 고빈도 progress/activity event는 터미널 상태가 된 뒤 UI용 요약으로 압축한다. 원본 상세 이벤트의 온라인 보존 기간과 감사 로그 보존 기간은 별도 설정한다.
- 기본 제안은 상세 progress 30일, 보안 감사 로그 180일이며 배포 환경의 개인정보·규제 정책으로 재정의할 수 있다.
- cursor 보존 범위를 벗어난 클라이언트는 부분 replay를 시도하지 않고 전체 timeline을 재동기화한다.
- 삭제 요청은 asset 원본, 파생 자산, 메시지 표시 metadata, operation 감사 기록별 법적 보존 예외를 구분하고 사용자에게 결과를 설명한다.

### 9.9 기존 데이터와 migration 경계

- additive migration은 legacy `chat_history_messages`를 `workspace_messages`로 자동 import하거나 기존 conversation ID를 재사용한다고 가정하지 않는다.
- legacy 기록 import가 필요하면 owner/presentation 귀속, 안정 정렬, attachment 가용성을 검증하는 별도 감사 가능한 migration과 dry-run을 설계한다. 해석 불가 row를 조용히 합치지 않는다.
- 현재 workspace timeline의 ID와 position은 bootstrap/command 경로에서 생성된 `workspace_conversations`·`workspace_messages` row가 권위 원본이다.
- 기존 presentation revision/lifecycle backfill은 Alembic migration과 staging 검증 결과를 따른다. 임시 경로만 남은 과거 첨부는 존재하는 asset처럼 꾸미지 않고 별도 legacy-unavailable/재첨부 정책이 명시된 경우에만 투영한다.

## 10. 동시성, 리비전, 되돌리기

### 10.1 덱 리비전

shipped 프론트엔드는 변경 command/action에 `expectedRevision`을 전달한다. 서버 wire 필드는 호환을 위해 optional이지만, 누락되면 잠근 presentation의 현재 revision을 operation의 `expected_revision`으로 snapshot하고, 명시된 stale 값은 실행하지 않는다. 실제 content publication은 같은 저장 트랜잭션의 CAS와 `result_revision`으로 검증한다.

```sql
UPDATE presentations
SET content = :content,
    revision = revision + 1,
    updated_at = now()
WHERE id = :presentation_id
  AND revision = :expected_revision;
```

- 서버의 현재 리비전과 일치하면 실행한다.
- 영향받은 row가 0개면 저장을 성공으로 처리하지 않는다.
- 불일치하고 안전하게 재적용 가능한 변경이면 최신 상태에 재계획한다.
- 재적용이 위험하면 `REVISION_CONFLICT` 오류와 충돌 슬라이드를 반환한다.
- 프론트엔드는 “최신 버전에서 다시 시도”와 “변경 내용 검토” 행동을 제공한다.
- tool service, 직접 저장, 템플릿/테마 적용, 생성 결과 반영, 버전 복원은 모두 같은 revision service를 통한다.
- 성공 이벤트는 `previousRevision`과 `newRevision`을 포함한다.
- 품질 검사 같은 읽기 전용 작업은 `inspectedRevision`을 결과에 기록하고 현재 리비전과 달라지면 `검사 후 덱이 변경됨`을 표시한다.

### 10.2 체크포인트

- 다중 슬라이드, 템플릿, 테마, 재생성, 자동 수정 작업은 실행 전 서버 체크포인트를 만든다.
- 단일 요소 편집도 서버가 지원하면 체크포인트를 만들고, 그렇지 않으면 기존 클라이언트 undo 기록과 연결한다.
- `undo`는 과거 상태를 직접 덮어쓰는 버튼이 아니라 `version.restore` 또는 역변경 작업을 생성한다.
- 되돌리기 후에도 원래 카드와 복원 카드가 모두 남는다.
- 체크포인트에는 `baseRevision`, snapshot hash, 생성 operation ID를 기록한다.
- 복원도 새 expected revision을 요구하며 오래된 승인 카드가 현재 덱을 덮어쓰지 못한다.

현재 `undoRedoSlice`는 클라이언트 편집에 유효하지만 서버 기반 AI 작업의 유일한 복구 수단으로 사용하지 않는다.

### 10.3 병렬 실행 정책

- 읽기 전용 작업(품질 검사, 분석)은 변경 작업과 병렬 실행할 수 있다. 결과에는 검사한 리비전을 표시한다.
- 서로 다른 슬라이드의 독립 변경은 서버가 안전성을 보장할 때만 병렬 실행한다.
- 같은 슬라이드의 변경 작업은 대기열에 넣는다.
- 덱 전체 변경은 다른 변경 작업과 병렬 실행하지 않는다.
- UI는 대기 이유와 앞선 작업을 카드에 표시한다.
- slide index는 잠금이나 충돌 판정에 사용하지 않고 안정적인 slide ID 집합을 사용한다.

## 11. 프론트엔드 구조

### 11.1 목표 컴포넌트

```text
PresentationPage
├─ PresentationHeader
├─ SidePanel
├─ Canvas
└─ WorkspaceChatPanel
   ├─ WorkspaceConnectionStatus
   ├─ WorkspaceTimeline
   │  ├─ ChatMessageItem
   │  └─ OperationCardRegistry
   │     ├─ PlanCard
   │     ├─ ProgressCard
   │     ├─ ApprovalCard
   │     ├─ ResultCard
   │     ├─ ArtifactCard
   │     ├─ ErrorCard
   │     └─ ManualEditCard
   ├─ SelectionContextBar
   └─ WorkspaceComposer
```

### 11.2 상태 경계

- 서버 상태: TanStack Query 또는 기존 데이터 계층을 통해 timeline, operations, artifacts를 캐시한다.
- 스트림 상태: `useWorkspaceEventStream`이 연결, 재연결, 이벤트 중복 제거를 담당한다.
- 프레젠테이션 상태: 기존 Redux presentation state를 유지한다.
- 선택 상태: slide IDs, element IDs, selection snapshot을 별도 작은 store로 관리한다.
- 카드 UI 상태: 접힘, 필터, 패널 폭은 로컬 UI 상태다.

작업 상태를 `Chat.tsx`의 지역 상태에만 두지 않는다. 현재의 `activityMapRef`, active mutation count, message-local tool trace는 호환 어댑터의 입력으로 사용하고 최종적으로 영속 작업 상태로 이동한다.

### 11.3 캔버스 갱신

- terminal `operation.result`와 operation card의 단조 증가한 `result_revision`을 받으면 해당 리비전을 한 번만 가져온다. late/lower operation version이나 revision은 이미 적용한 canvas 상태를 되돌리지 않는다.
- 여러 tool trace가 연속되면 현재 Chat의 coalesced refresh 동작을 보존한다.
- 대상 슬라이드 메타데이터가 있으면 변경된 슬라이드만 갱신할 수 있도록 API를 확장한다.
- 사용자가 캔버스를 직접 탐색 중이면 자동 포커스를 하지 않고 “AI가 5번을 수정함” 배지를 제공한다.

### 11.4 기능 역량(capability)

편집 모드별 지원 기능 차이를 서버가 선언한다.

```ts
type PresentationCapabilities = {
  capabilityVersion: string;
  elementTextEdit: boolean;
  elementGeometryEdit: boolean;
  chartEdit: boolean;
  themeApply: boolean;
  templateApply: boolean;
  slideImageRegenerate: boolean;
  exportEditablePptx: boolean;
};
```

- capabilities는 덱 조회 응답에서 서버가 계산해 제공하는 권위 있는 값이다.
- adaptive/template-v2는 요소 단위 편집 기능을 노출한다.
- authored 이미지 기반 슬라이드는 지원하지 않는 요소 단위 명령을 숨기거나 이미지 재생성 대안으로 바꾼다.
- 카드에는 “현재 모드에서는 전체 슬라이드 이미지 재생성으로 적용됩니다”처럼 실제 영향을 명확히 적는다.
- command handler는 클라이언트 표시와 무관하게 실행 시 capability를 다시 계산하고 검증한다.
- capability version이 달라졌으면 `CAPABILITY_CHANGED`와 최신 capabilities를 반환한다.

## 12. 기존 UI의 이동 계획

| 현재 UI | 목표 UI | 우선순위 |
|---|---|---|
| Chat의 일회성 activity 목록 | 영속 Progress/Result card | P1-A |
| AuthoredQualityReview dialog | Quality Review/Fix card | P1-A/P2 |
| Export popover + toast + quality dialog | Export/Artifact card | P1-B |
| 선택 후 AI 수정 UI | `slide.edit` 결과/충돌/복원 카드 | P1-C |
| Regenerate confirmation dialog | 인라인 Approval card | P2 |
| AuthoredTemplateChanger dialog | Template gallery/apply card | P2 |
| VersionHistoryPopover | Timeline/restore card | P2 |
| ThemeSelector | Theme 선택 카드, 정밀 색상 팝오버는 유지 가능 | P2 |
| SharePopover | 공유 설정/결과 카드 | P2 |
| GenerationInfoDialog | 첫 메시지의 생성 정보 + 생성 결과 카드 | P1-A/P2 |
| 저장 실패 toast | 영속 Error card + 재시도 | P2 |
| 최초 생성 진행 화면 | 같은 워크벤치의 Deck Generation card | P2 |
| 차트/색상 정밀 조절 팝오버 | 유지, 저장 결과는 Manual edit card | P3 |
| OS 파일 선택/저장 UI | 시스템 예외로 유지 | 예외 |
| 키보드 단축키 도움말 | 정보성 오버레이로 유지 | 예외 |

## 13. 보안과 권한

- 모든 command, operation, event 조회에서 `owner_id`와 presentation 접근 권한을 재검증한다.
- 다운로드 artifact는 만료되는 서명 URL을 사용하고 카드에는 파일 식별자만 영속화한다.
- operation input과 event payload에 원본 파일 내용, API 키, 내부 예외 스택을 저장하지 않는다.
- 오류 카드에는 사용자 안전 메시지와 추적 ID만 표시한다.
- 승인 행동은 작업 생성 사용자와 덱 편집 권한을 가진 사용자만 수행할 수 있다.
- 카드의 URL과 버튼 행동은 서버 allowlist 및 프론트 레지스트리로 제한한다.
- 공개 공유 API는 렌더링된 슬라이드 결과와 명시적으로 허용된 발표 metadata만 제공한다. prompt, 첨부 파일명, conversation, operation, 오류 추적 ID, 내부 revision 기록은 포함하지 않는다.
- 편집 권한이 없는 인증 사용자는 정책상 `403`, 존재 자체를 숨겨야 하는 다른 소유자의 workspace 리소스는 `404`를 반환하도록 전 API에서 통일한다.
- 업로드는 확장자가 아니라 서버 확인 MIME, 파일 signature, 크기, 악성 파일 검사 결과로 허용 여부를 결정한다.
- 프롬프트와 파일명은 저장 시 원문을 보존하더라도 화면 렌더링 전에 escape·정규화한다.

### 13.1 LLM 컨텍스트 경계

화면에 보이는 timeline 전체를 LLM 입력으로 사용하지 않는다. 다음 네 입력을 별도로 조립하고 provenance와 토큰 사용량을 기록한다.

1. 사용자가 실제로 작성한 대화 메시지의 제한된 최근 구간
2. 오래된 대화의 서버 생성 요약
3. 현재 요청에 검색으로 선택된 attachment chunk
4. operation의 사용자용 상세 로그가 아닌 짧은 결과 요약

- 각 입력 종류에 독립적인 토큰 한도와 신뢰 수준을 둔다.
- attachment 내용은 지시문이 아니라 비신뢰 참고 데이터로 표시해 prompt injection 경계를 유지한다.
- 원본 문서 전체, tool trace 전체, 오류 스택은 자동으로 컨텍스트에 넣지 않는다.
- 모델 입력 감사 정보에는 원문 전체 대신 asset/chunk ID, hash, 요약 버전 같은 provenance를 우선 기록한다.

## 14. 접근성과 사용성

- 상태를 색상만으로 표현하지 않고 아이콘과 텍스트를 함께 사용한다.
- 작업 상태 변경은 `aria-live="polite"`, 승인 요청과 치명적 오류는 `assertive`를 제한적으로 사용한다.
- 진행 카드가 매 이벤트마다 전체 문장을 읽지 않도록 단계가 바뀔 때만 알린다.
- 모든 카드 행동은 키보드로 접근 가능해야 한다.
- 자동 포커스는 사용자가 입력 중이거나 캔버스를 조작 중일 때 이동하지 않는다.
- 애니메이션 감소 설정을 존중한다.

## 15. 관측성과 운영 지표

각 작업에 `operation_id`, `presentation_id`, `conversation_id`, `command_type`, `schema_version`, `duration`, `result_status`, `error_code`, `retry_count`, `revision_conflict`, `lease_recovery_count`, `event_lag_ms`를 기록한다.

주요 제품 지표:

- 명령 시작 대비 성공률
- 작업별 중앙/상위 95% 완료 시간
- 승인 대기 후 이탈률
- 실패 후 재시도 성공률
- 완료 후 되돌리기 비율
- 생성 시작부터 첫 슬라이드 표시까지 시간
- 생성 시작부터 첫 사용자 편집까지 시간
- 팝업 대체 후 작업 발견성과 완료율
- timeline snapshot부터 stream 연결까지의 누락/중복 보정 횟수
- 만료 cursor로 인한 전체 재동기화율
- orphan asset 정리량과 파일 처리 실패율
- legacy trace와 workspace event의 parity율

로그의 operation ID는 오류 카드의 추적 ID와 연결한다.

### 15.1 점진 전환과 롤백

새 UI는 한 번에 기존 Chat과 팝업을 제거하지 않고 다음 단계로 전환한다.

1. workspace read path를 shadow mode로 실행하고 사용자에게는 기존 UI를 표시한다.
2. 동일 작업의 기존 trace와 새 event를 비교해 상태·결과 parity를 측정한다.
3. 내부 사용자와 테스트 덱에 `chat_first_workspace` feature flag를 켠다.
4. `quality.review`, `export.pptx`, `slide.edit` 순으로 기능별 dispatcher를 전환한다.
5. 오류율, 누락 이벤트, 완료율 기준을 만족한 기능만 기본 활성화한다.
6. frontend의 `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED`는 build-time gate라 값 변경에는 rebuild/redeploy가 필요하다. 해당 bundle이 배포된 뒤에는 backend feature-off, kill switch 또는 cohort 제외라는 authoritative runtime 판정으로 데이터 마이그레이션을 되돌리지 않고 SharePopover를 포함한 legacy UI와 legacy write 경로를 즉시 복원한다. runtime/profile 불건전이나 일반 command capability 실패는 legacy write 우회 조건이 아니며, write가 꺼져도 지원 dialect의 timeline/history와 artifact download read는 독립적으로 유지한다.
7. 안정화와 보존 기간 검증 후 legacy modal/popover와 직접 호출 코드를 제거한다.

### 15.2 운영 복구

- worker는 lease 만료 전에 heartbeat하며 만료 작업 감시기가 재획득 또는 `dead_letter`와 `operation_recovery_required` 투영을 결정한다.
- 장시간 `queued`, `cancelling`, lease 만료, outbox 미전달, cursor lag 증가에 경보를 둔다.
- 동일 side effect의 실행 여부가 불명확한 경우 자동 재실행하지 않고 외부 job ID나 artifact hash로 먼저 조정한다.
- 배포 중 schema version 혼재를 허용하는 호환 기간을 두고 구버전 이벤트는 fallback 카드로 읽을 수 있어야 한다.

## 16. 구현 단계 — 2026-08-02 역사적 계획

### P0-A. 데이터 기반

- `workspace_conversations`, `workspace_messages`, `workspace_assets`, `workspace_message_attachments`를 추가한다.
- `workspace_operations`, `workspace_action_receipts`, `workspace_operation_events`, `workspace_artifacts`를 추가한다.
- `presentations.revision`과 모든 FK, unique, CAS 제약을 추가한다.
- conversation row lock 기반 position 예약과 orphan asset 정리를 구현한다.
- 기존 Chat history와 presentation 저장 경로를 특성 테스트로 고정한다.

완료 기준:

- 동시에 추가된 메시지가 같은 position을 가질 수 없다.
- asset 원본명, 상태, 권한, 만료가 새로고침 뒤에도 복원된다.
- 모든 서버 변경 경로가 같은 authoritative revision을 증가시킨다.
- 기존 데이터 마이그레이션과 롤백 절차가 staging DB에서 검증된다.

### P0-B. 계약과 실행 기반

- versioned command/event/action schema와 공용 fixture를 만든다.
- bootstrap endpoint와 transaction/outbox를 구현한다.
- timeline snapshot + high watermark, durable event append/replay, cursor expiry를 구현한다.
- 다중 서버 wake-up, heartbeat DB 재확인, worker lease를 구현한다.
- action idempotency, operation version CAS, 상태 전이 서비스를 구현한다.
- strict card registry와 `UnknownOperationCard` fallback 계약을 만든다.

완료 기준:

- 이벤트 스키마와 상태 전이가 프론트/백엔드에서 같은 fixture로 검증된다.
- timeline 조회와 stream 연결 사이에 생성된 이벤트가 누락되지 않는다.
- wake-up 신호를 의도적으로 유실해도 heartbeat 이후 이벤트가 전달된다.
- 동일 bootstrap/command/action 재전송이 side effect를 한 번만 만든다.
- worker 종료 후 lease 정책에 따라 작업이 복구되거나 명시적으로 실패한다.

### P1-A. 읽기 전용 수직 슬라이스

- 새 오른쪽 `WorkspaceChatPanel`과 통합 timeline을 feature flag 뒤에 구현한다.
- 최초 프롬프트, 첨부 파일, 접힌 생성 정보를 첫 사용자 메시지로 복원한다.
- `quality.review`를 첫 operation card로 연결한다.
- timeline 새로고침, 재접속, cursor 만료 전체 동기화를 구현한다.
- 1366×768, 1440×900, 1920×1080에서 패널 폭을 검증한다.

완료 기준:

- 읽기 전용 검사가 덱을 변경하지 않고 inspected revision을 표시한다.
- 최초 프롬프트와 파일 2개가 생성 완료·새로고침 뒤 같은 첫 메시지로 보인다.
- 알 수 없는 카드 버전도 timeline을 깨뜨리지 않고 fallback으로 표시된다.
- shadow trace와 새 event의 터미널 상태·결과 parity가 99.9% 이상이고 silent loss가 없다.

### P1-B. artifact 수직 슬라이스

- `export.pptx`를 dispatcher와 Artifact card로 이동한다.
- artifact hash, source revision, 만료, 서명 다운로드 URL 재발급을 구현한다.
- 덱 변경 후 오래된 내보내기 표시와 재생성을 구현한다.

완료 기준:

- 내보내기 진행, 실패, 완료, 다운로드가 팝업 없이 같은 카드에서 복원된다.
- 만료 링크는 원본 artifact 권한을 재검증한 뒤 재발급되거나 다시 생성된다.
- 다운로드한 파일의 sha256이 저장된 artifact와 일치한다.

### P1-C. 변경 작업 수직 슬라이스

- 단일 `slide.edit`에 stable target ID, expected revision CAS, 체크포인트를 적용한다.
- 결과 강조, typed revision/target 오류, 서버가 `allowed_actions`로 제공하는 undo 또는 명시적 미지원 사유 UI를 구현한다.
- authored/adaptive/template-v2의 서버 capabilities를 적용한다.

완료 기준:

- 두 변경 명령이 같은 revision을 수정하려 하면 하나만 성공하고 다른 하나는 명시적 충돌로 끝난다.
- 재정렬 후에도 slide index가 아니라 slide ID로 정확한 대상을 수정한다.
- undo는 새 operation과 새 revision을 만들며 이전 기록을 지우지 않는다.

### P2. 작업과 생성 흐름 확장

- 전체 재생성, 템플릿/테마, 버전 복원, 공유, 저장 오류를 카드로 이동한다.
- 아웃라인, 슬라이드 생성, 품질 검사, 부분 실패 재시도를 하나의 `presentation.generate` 카드에 연결한다.
- 위험 작업의 승인 만료, 승인 시 revision/권한 재검사를 적용한다.
- 헤더의 기능별 직접 API 호출을 순서대로 dispatcher로 전환한다.

완료 기준:

- 최초 생성부터 편집 완료까지 presentation/conversation ID가 유지된다.
- 별도 진행 팝업 없이 생성 진행률과 현재 슬라이드를 확인하고 재접속할 수 있다.
- 공개 공유 응답에 prompt, 첨부, operation 정보가 포함되지 않는다.

### P3. 직접 편집과 UI 정리

- 선택 컨텍스트 칩과 요소/다중 슬라이드 타겟팅을 일반화한다.
- 직접 편집을 저장 diff 세션으로 묶어 Manual edit card로 기록한다.
- 기능 플래그로 충분히 검증된 task modal, 중복 toast, 직접 API 호출만 제거한다.
- timeline 가상화와 보존·압축 작업을 운영한다.

완료 기준:

- 직접 편집 20회의 키 입력이 20개 카드가 아니라 의미 있는 한 개 요약 카드로 기록된다.
- authored와 adaptive 모드의 미지원 행동이 실행 전에 정확한 대안과 함께 표시된다.
- feature flag를 끄면 데이터 손실 없이 legacy UI로 즉시 복귀한다.

## 17. 테스트 전략

### 17.1 백엔드

- bootstrap 원자성, 실패 rollback, idempotent 재요청 테스트
- asset 소유권, 파일명 정규화, 만료, 격리, orphan 정리 테스트
- conversation position 동시 예약과 unique constraint 테스트
- 상태 전이 허용/거부 단위 테스트
- command/action idempotency와 operation version CAS 테스트
- 권한 및 다른 사용자 operation 접근 차단 테스트
- timeline snapshot high watermark와 stream 경계 경쟁 조건 테스트
- 이벤트 기록 후 SSE 전송 순서 및 wake-up 신호 유실 테스트
- 중단/재접속/취소 경쟁 조건 테스트
- worker lease 만료, 재획득, 재실행 불가 handler 복구 테스트
- expected revision 일치/충돌 테스트
- 모든 mutation 경로의 revision 증가 계약 테스트
- 기존 chat trace → workspace event 매핑 테스트
- public share에서 workspace 데이터가 제외되는 계약 테스트
- artifact hash/source revision/expiry 테스트

### 17.2 프론트엔드

- operation 종류별 카드 레지스트리 테스트
- unknown schema/version fallback 카드 테스트
- 진행 이벤트 중복/역순 방어 테스트
- timeline high watermark 기반 초기 연결과 cursor 만료 전체 동기화 테스트
- 최초 프롬프트/첨부/생성 정보의 안전한 렌더링과 복원 테스트
- 캔버스 자동 포커스와 사용자 탐색 우선순위 테스트
- 승인, 취소, 재시도, 되돌리기 버튼 상태 테스트
- action 이중 클릭과 operation state conflict 테스트
- 패널 리사이즈와 반응형 전환 테스트
- 스크린리더 상태 안내 테스트
- 서버 capability와 명령 노출 일치 테스트

### 17.3 E2E

핵심 시나리오:

1. 최초 프롬프트 + 파일 2개 bootstrap → 편집 화면 첫 메시지 → 새로고침 후 동일 복원
2. 생성 → 첫 슬라이드 표시 → 생성 중 허용된 수정 → 완료
3. 요소 선택 → 자연어 수정 → 결과 강조 → 되돌리기
4. PPTX 내보내기 → 검사 경고 → 완료 → 다운로드 → 덱 변경 후 오래됨 표시
5. 전체 재생성 → 승인 → 실행 중 취소 요청 → 최종 상태 확정
6. timeline 조회 직후 이벤트 발생 → stream 연결 → 누락 없이 한 번 표시
7. SSE가 다른 서버 인스턴스에 연결된 상태에서 worker event 수신
8. 동일 bootstrap/명령/승인 이중 전송 → side effect 한 번
9. 두 탭의 리비전 충돌 → 덮어쓰기 없이 하나만 성공
10. 슬라이드 재정렬 후 기존 slide ID 수정 → 정확한 슬라이드만 변경
11. authored 모드의 미지원 요소 편집 → 슬라이드 이미지 재생성 대안
12. 공개 공유 링크 조회 → prompt/첨부/operation 정보 없음
13. worker 강제 종료 → lease 만료 후 안전한 복구 또는 명시적 오류

## 18. 권장 구현 티켓

### Backend

- conversation/asset/attachment/revision 기반 모델 및 마이그레이션
- `WorkspaceOperation`/Action/Event/Artifact 모델 및 마이그레이션
- conversation position allocator와 bootstrap transaction/outbox
- command/event Pydantic 모델과 상태 전이 서비스
- workspace command router와 권한 검증
- timeline high watermark와 durable SSE replay/wake-up
- worker lease/heartbeat/recovery service
- command/action idempotency와 operation/revision CAS
- existing chat trace adapter
- generation task adapter
- export/quality/template/version command handlers
- authoritative revision/checkpoint contract의 모든 mutation 적용
- asset scan/expiry/orphan cleanup과 artifact integrity service
- public share DTO 분리와 LLM context builder

### Frontend

- `WorkspaceCommandDispatcher`
- `useWorkspaceEventStream`
- `WorkspaceChatPanel`과 리사이즈 가능한 레이아웃
- message/operation 통합 timeline
- strict operation card registry와 unknown-version fallback
- 최초 prompt/attachment/generation metadata 메시지
- selection context bridge
- header action migration
- reconnect/cursor-expiry/idempotency/revision conflict UI
- authoritative capability consumer
- artifact stale/expired/integrity UI
- manual edit session aggregator

### QA/운영

- 공용 contract fixture
- API/컴포넌트/E2E 테스트
- operation 지표 대시보드
- trace/event parity와 stream lag 대시보드
- feature flag/shadow mode/rollback runbook
- lease/outbox/orphan asset 감시와 경보
- 기존 popup 사용 여부 회귀 검사

## 19. 권장 기본값

구현 중 추가 결정이 없으면 다음을 기본값으로 사용한다.

- 오른쪽 패널: 440px, 360~560px 조절
- 패널 폭: 위 값으로 시작하되 1366×768, 1440×900, 1920×1080 검증 결과를 출시 기준으로 사용
- 덱당 기본 대화: 1개, 사용자가 명시적으로 새 대화를 만들 수 있음
- 최초 생성 프롬프트와 첨부 파일: 같은 대화의 첫 사용자 메시지로 영구 표시
- 최초 생성 저장: asset 업로드 후 staging presentation 기반 bootstrap transaction
- 실행 정책: 되돌릴 수 있는 단일/소수 슬라이드 변경은 즉시 실행
- 승인 정책: 삭제, 전체 재생성, 버전 복원, 외부 공유 범위 확대에만 필수 승인
- 승인 만료: 위험도별 서버 설정, 승인 시 operation version/revision/권한 재검사
- 작업 기록: 영구 저장, 세부 activity는 기본 접힘
- 상세 activity 보존: 기본 30일, 감사 로그 기본 180일, 배포 정책으로 재정의
- 직접 편집: 의미 있는 저장 diff를 슬라이드 이탈/저장/30초 유휴 기준 세션으로 기록하고 5분 내 연속 세션은 합칠 수 있음
- 자동 포커스: 기본 켜짐, 사용자 조작 시 즉시 해제
- 다운로드: 완료 카드에 만료 가능한 링크 제공
- 오류: 영속 카드 + 추적 ID + 안전한 재시도
- 공개 공유: 슬라이드 결과만 제공하고 workspace 대화와 작업 기록은 제외
- 전환: 사용자/덱 단위 `chat_first_workspace` feature flag 사용

## 20. 전체 완료 정의

다음 조건을 모두 만족하면 Chat-first Slides Workbench 전환이 완료된 것으로 본다.

- 최초 생성부터 편집, 검토, 내보내기, 복구까지 같은 오른쪽 타임라인에서 확인할 수 있다.
- 최초 생성에 사용한 프롬프트 원문, 첨부 파일, 생성 설정을 오른쪽 채팅의 첫 메시지에서 다시 확인할 수 있다.
- 실행 상태를 알기 위해 task modal이나 일회성 toast를 다시 열 필요가 없다.
- 모든 장기 작업은 재접속 가능한 operation ID와 영속 이벤트를 가진다.
- timeline snapshot과 stream 사이에 저장된 이벤트가 누락되지 않으며 cursor 만료 시 전체 동기화된다.
- 변경 작업은 대상, 결과 리비전, 복구 행동을 보여준다.
- 모든 변경 경로는 하나의 authoritative revision과 CAS 계약을 사용한다.
- 작업 시작 위치가 헤더, 캔버스, 채팅 중 어디든 동일한 명령/이벤트 경로를 사용한다.
- 모드별 기능 차이가 실패 후가 아니라 실행 전에 설명된다.
- 네트워크 단절, 중복 클릭, 리비전 충돌에서 데이터가 중복되거나 조용히 덮어써지지 않는다.
- worker와 SSE가 서로 다른 서버 프로세스에 있어도 DB에 저장된 이벤트가 전달된다.
- 공개 공유에서 prompt, 첨부, 대화, 작업 정보가 노출되지 않는다.
- 배포된 frontend bundle에서 backend feature-off, kill switch 또는 cohort 제외를 적용하면 데이터 손실 없이 검증된 legacy UI/write로 복귀하고 read-only history/download는 유지된다. build-time frontend flag 자체를 변경하는 경우에는 rebuild/redeploy한다.
- 기존 슬라이드 편집 및 생성 기능의 회귀 테스트가 통과한다.

### 20.1 작업 팝업 제거 전 승인 게이트

다음 조건을 모두 만족하기 전에는 기존 작업 팝업 제거와 전체 사용자 활성화를 진행하지 않는다.

- [ ] 최초 프롬프트와 파일 2개가 새로고침 후 같은 첫 사용자 메시지로 복원된다.
- [ ] 원본 asset이 만료돼도 파일 카드가 깨지지 않고 상태와 재첨부 경로를 설명한다.
- [ ] timeline snapshot과 stream 사이에 발생한 이벤트가 누락되지 않는다.
- [ ] 같은 bootstrap, command와 지원 action(`approve`, `cancel`, `retry`)을 두 번 보내도 side effect가 한 번만 실행된다. `undo`는 v1에서 미지원 사유를 가진 disabled projection으로 검증한다.
- [ ] 두 변경 명령이 같은 덱 revision을 수정하려 하면 하나는 명시적 충돌로 종료된다.
- [ ] 동시에 추가된 두 메시지의 conversation position이 중복되지 않는다.
- [ ] worker 종료 후 lease가 만료된 작업이 안전하게 복구되거나 명시적으로 실패한다.
- [ ] 공개 공유 API에서 prompt, 첨부, operation, 오류 추적 정보가 노출되지 않는다.
- [ ] authored/adaptive/template-v2 capability가 서버와 UI에서 동일하다.
- [ ] 3개 기준 해상도에서 캔버스와 카드 행동을 사용할 수 있다.
- [ ] feature flag를 끄면 기존 UI로 즉시 복귀한다.

## 21. 기존 설계와의 관계

`docs/compose-ux-design.md`의 간결한 첫 입력 화면, 템플릿 선택, 첨부 경험은 유지할 수 있다. 다만 다음 결정은 이 문서가 대체한다.

- 생성 후 기존 Chat을 별도 기능으로 유지한다는 결정
- compose 단계에는 대화형 진행 타임라인이 없다는 결정
- 편집기 헤더의 작업 팝업을 그대로 유지한다는 전제

앞으로의 기준은 **한 conversation, 한 workspace, 영속 operation timeline**이다.

## 22. 설계 평가 반영 추적표

`chat-first-slides-workbench-design-review-20260802.md`의 개선 항목을 다음과 같이 반영했다.

| 평가 항목 | 반영 위치 | 반영 결과 |
|---|---|---|
| B1 conversation/bootstrap | 6.1, 8.1, 9.1 | staging presentation 기반 원자적 bootstrap으로 확정 |
| B2 uploaded asset | 6.1, 8.1, 9.2~9.3 | 안정 ID, 무결성, 권한, 만료, provenance 정의 |
| B3 deck revision | 9.7, 10.1 | 모든 mutation의 authoritative revision/CAS 정의 |
| B4 snapshot/stream race | 8.2~8.3 | 동일 snapshot high watermark와 replay 경계 정의 |
| B5 multi-worker event | 8.3, 15.2 | DB 원본, wake-up 신호, heartbeat 재조회 정의 |
| B6 message position | 8.1, 9.1 | conversation row lock과 unique position 정의 |
| H1~H3 schema/state/action | 5.2, 7.2~7.4 | response schema version, fallback, `awaiting_approval`/`cancelling`/`dead_letter`, action CAS 정의 |
| H4~H5 target/capability | 7.1, 11.4 | stable ID와 서버 권위 capability 정의 |
| H6 share boundary | 13, 17 | 공개 공유의 workspace 데이터 차단과 테스트 정의 |
| H7 rollout | 15.1 | shadow, parity, feature flag, rollback 순서 정의 |
| H8 worker lease | 7.2, 9.4, 15.2 | lease, heartbeat, 재획득·복구 정책 정의 |
| M1 direct edit density | 6.3, 19 | 저장 diff 기반 30초/5분 집계 정의 |
| M2 retention | 9.8 | 상세 event와 감사 로그 보존 정책 분리 |
| M3 panel width | 4.1, P1-A | 3개 기준 해상도 검증 게이트 추가 |
| M4 artifact integrity | 9.6, P1-B | hash, source revision, expiry, stale 상태 정의 |
| M5 safe rendering | 5.4, 13 | 프롬프트·파일명 escape/정규화 정의 |
| M6 LLM context | 13.1 | 화면 timeline과 모델 입력을 분리하고 provenance 정의 |

## 23. 확정 ADR과 구현 게이트 (2026-08-03)

`chat-first-slides-workbench-design-review-v2-20260802.md`의 다섯 결정 항목은 더 이상 선택지가 아니며, Chat-first v1의 아래 확정 계약으로 구현한다. 코드, 마이그레이션, 공용 fixture, 운영 절차가 충돌할 때 이 절을 우선한다.

### ADR-001 Runtime profile

- core persistence/runner 계약은 dialect-neutral SQLAlchemy로 작성한다.
- local 프로필은 SQLite, cluster 프로필은 PostgreSQL만 지원한다.
- MySQL/MariaDB는 conversation position 예약, claim/lease/fencing, outbox dequeue, revision publication의 parity가 검증되기 전까지 `chat_first_workspace`를 시작 단계에서 명시적으로 거부한다. 조용한 기능 축소나 best-effort 실행은 허용하지 않는다.
- capability 응답은 `runtime_profile`, `chat_first_supported`, 거부 사유를 제공하며 UI 활성화와 worker 시작은 같은 판정을 사용한다.

### ADR-002 Runner/outbox

- `PresentationGenerationJob`에서 이미 검증된 durable claim/lease/heartbeat/recovery와 attempt token fencing을 일반 `WorkspaceOperation` 실행 계약으로 추출·재사용한다.
- 별도의 병렬 job 프레임워크는 만들지 않는다. 기존 generation 실행도 workspace operation adapter를 통해 같은 operation/event/outbox 타임라인에 투영한다.
- 모든 side effect는 action receipt/idempotency key와 fencing token으로 보호하며, outbox는 DB commit 이후 dispatch되고 재전송을 허용한다.

### ADR-003 Timeline projection

- top-level timeline은 user/assistant message와 operation anchor의 conversation `position`만으로 정렬한다.
- operation event의 cursor/sequence는 기존 operation card projection만 갱신하며 top-level 항목을 새로 끼워 넣거나 재정렬하지 않는다.
- 과거 메시지 pagination cursor와 operation event high-watermark를 별도 필드·별도 API 의미로 유지한다. snapshot의 event high-watermark 이후만 replay해 조회-연결 경쟁에서 누락/중복을 막는다.

### ADR-004 Revision publication

- presentation `revision`은 publication commit token이다. 실행 중 checkpoint/progress 저장은 revision을 올리지 않으며 성공한 최종 publish transaction에서 정확히 한 번 증가한다.
- publish는 `expected_revision`과 operation fencing token을 함께 검사한다. stale worker나 두 탭의 동시 명령은 덮어쓰지 않고 명시적 conflict로 종료한다.
- 외부 artifact는 immutable 경로에 쓴 뒤 hash/size를 검증하고, DB pointer와 새 revision을 한 transaction으로 commit한다. commit 실패 또는 fencing 상실 시 artifact는 orphan cleanup 대상으로 기록하며 기존 published pointer는 유지한다.

### ADR-005 Asset lifecycle

- workspace asset 상태는 `uploaded`, `scanning`, `processing`, `ready`, `failed`, `quarantined`, `expired`, `deleted`만 허용한다.
- bootstrap은 asset row와 first user message attachment snapshot을 먼저 영속화한다. `ready`는 즉시 실행, `uploaded/scanning/processing`은 operation을 durable `blocked`/waiting 상태로 두고 event를 남긴 뒤 준비 완료 시 재개, `failed`는 안전한 retry/re-upload action, `quarantined/expired/deleted`는 실행 차단과 사용자 조치 카드를 제공한다.
- 메시지에 보이는 파일명·크기·MIME·hash·당시 상태는 attachment snapshot으로 보존하므로 원본 만료/삭제 후에도 첫 메시지 기록은 깨지지 않는다.

### Gate 적용 순서

이 순서는 설계 당시의 수용 모델이며 현재 release candidate의 실행 증거나 통과 수치를 뜻하지 않는다.

1. Gate A — 이 ADR, capability/error/API schema, 공용 contract fixture를 고정한다.
2. Gate B — additive migration, bootstrap transaction, operation/event/outbox/receipt, runner recovery, revision/asset 상태 머신 테스트를 통과시킨다.
3. Gate C — read-only snapshot/backfill과 첫 메시지/operation card projection을 feature flag 뒤에 제공한다.
4. Gate D — 생성·수정 command adapter를 순차 전환한다. 해당 기능의 parity gate가 통과하기 전에는 기존 popup을 제거하지 않는다.
5. Gate E — reconnect, worker crash, duplicate receipt, two-tab revision conflict, partial external write와 공개 공유 차단을 검증한 뒤 기본 활성화한다.

롤백은 backend의 authoritative feature-off/kill-switch/cohort 판정으로 write를 닫고 legacy 진입점을 다시 노출하는 방식이며, additive workspace 기록은 보존한다. `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED`는 build-time 값이므로 그 값 자체를 바꾸는 롤백에는 rebuild/redeploy가 필요하다. 스키마 downgrade나 대화 기록 삭제는 롤백 절차에 포함하지 않는다.

## 24. Gate 1~8 remediation addendum — 구현 동기화 (2026-08-03)

이 절은 §23의 ADR-001~005와 authored/template/adaptive 저장·publication 경계를 변경하지 않는다. §6의 Gate 0~4와 §23의 Gate A~E는 역사적 설계·구현 순서를 설명하고, 아래 Gate 1~8은 2026-08-03 감사 항목을 현재 source-tree 계약에 매핑한다. 이 문서는 특정 release candidate의 최종 통과 수치나 commit SHA를 증명하지 않는다.

### Gate 1 — SSE 무손실·EOF backoff

- timeline snapshot의 `event_high_watermark` 다음부터 durable event를 구독하며 query `after`와 `Last-Event-ID`를 지원한다. access/cursor는 streaming header 전에 검증한다.
- malformed 또는 unknown-version frame은 적용하거나 cursor를 전진시키지 않고 reconnect/backfill/rebase한다. 정상 EOF도 상태 전이와 jittered exponential backoff를 거쳐 busy reconnect loop를 막는다.
- request/action 오류는 해당 요청에만 투영하고 fatal timeline 오류와 분리한다. late/lower cursor·sequence·version event는 상태를 되돌리지 못하며 pagination 중 event high-watermark는 단조 증가한다.
- SSE는 DB를 source of truth로 사용하고 heartbeat, `no-cache`, `no-transform`, `X-Accel-Buffering: no`를 보낸다. cursor expiry나 불연속은 부분 이어붙이기 대신 snapshot rebase로 복구한다.

### Gate 2 — presentation/account 삭제와 durable cleanup

- presentation 삭제는 direct cascade가 아니라 승인된 `presentation.delete` Command → Operation → Event → Action 경로다. 실행 시 owner/edit 권한, approval TTL, expected revision, operation version, lease와 fencing을 다시 검사한다.
- 같은 DB transaction에서 presentation을 tombstone/redact하고 durable audit history를 보존하며, 삭제할 immutable asset/artifact를 `workspace_cleanup_journal`에 snapshot한다. 같은 commit에 terminal operation 상태·`result` projection·`operation.result` event도 함께 기록해 tombstone만 먼저 확정되는 새 split-state를 만들지 않는다. staging/`generation_failed`와 authored/template/adaptive/Template V2 종속 row도 같은 경계를 따른다.
- 이전 구현에서 tombstone/effect만 commit되고 operation이 비터미널로 남은 legacy split-state는 같은 operation의 durable effect와 cleanup intent를 확인한 뒤 외부 삭제를 반복하지 않고 terminal result/event를 멱등 복구한다. duplicate dispatch와 두 worker는 operation lease/fencing 및 journal idempotency로 하나의 결과에 수렴한다.
- 삭제된 presentation은 durable tombstone으로 남지만 live presentation guard가 legacy read/write·stream 경로에서 이를 `404`로 숨긴다. Chat-first write가 활성화된 legacy mutation은 DB 조회 전에 `409 CHAT_FIRST_COMMAND_REQUIRED`로 차단하고, feature-off/kill-switch로 legacy UI가 복원된 경우에도 tombstone을 재활성화하거나 다시 삭제하지 않는다.
- generation/export publication과 일반 operation의 최종 성공 commit도 presentation lifecycle을 다시 fence한다. 준비된 immutable artifact가 있는 사이 tombstone이 확정되면 `presentation_deleted`로 거부하고 staged file을 보상 삭제 또는 durable cleanup 상태로 수렴시키며, presentation revision이나 늦은 `operation.result` event를 되살리지 않는다.
- 관리자 계정 삭제는 admission marker를 별도 commit해 token을 먼저 무효화하고, duplicate 요청이 같은 cleanup generation을 resume하게 한다. marker를 잠근 뒤 첫 cleanup target snapshot을 flush하고 한 번 더 rescan한 다음 restricted dependent와 user cascade를 commit한다. 이 두 pass가 구성된 asset/artifact/user tree의 정확한 owner 경로와 root snapshot을 journal에 남기므로 marker 직전 publication, root rotation 또는 외부 삭제 실패 뒤에도 durable intent로 복구할 수 있다. 최종 `204` 응답만 유실된 replay는 account-owned cleanup tombstone이 있을 때만 멱등 성공하며 임의의 존재하지 않는 UUID는 `404`다.
- 실제 외부 삭제는 commit 이후 maintenance sweeper가 lease, fencing, idempotency hash, retry/backoff, `dead_letter`로 수행한다. sweeper는 chat-first flag와 독립된 app lifecycle에서 동작하고 missing target은 성공으로 수렴하며 traversal·symlink·root 이탈은 fail-close한다. `dead_letter`, 활성 cleanup token/lease 없음, `last_error.retryable=true`를 모두 만족하는 row만 관리자 `POST /api/v1/admin/workspace-cleanup/dead-letters/{journal_id}/redrive`가 단일 CAS로 `retry_wait`에 되돌리고 retry budget·fence·redrive 감사 필드를 갱신한다. 두 관리자 동시 요청 중 하나만 redrive하며 non-retryable 또는 분류가 없는 legacy row는 fail-close한다.

### Gate 3 — runtime rollback과 read/write capability 분리

- 지원 dialect에서는 workspace timeline/history/artifact read를 기능 플래그와 write profile 선언 여부와 독립적으로 유지한다. 기존 artifact 다운로드 같은 비변경 동작은 read-only에서도 허용한다.
- write는 `CHAT_FIRST_WORKSPACE_ENABLED`, runtime profile/dialect 일치, kill switch와 deck cohort eligibility를 모두 통과해야 한다. UI는 `reads_supported`와 `commands_enabled`를 별도로 판정한다.
- `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED`는 build-time bundle gate이므로 값을 바꾸면 rebuild/redeploy가 필요하다. 이미 Chat-first bundle이 배포된 상태에서는 backend의 explicit feature-off, authoritative kill-switch 또는 cohort 제외 capability가 SharePopover를 포함한 legacy UI를 rebuild 없이 복원한다. additive workspace record는 보존하고 capability refresh를 focus/visibility/online/pageshow와 `BroadcastChannel`로 두 탭에 전파한다.
- timeline/history/artifact read capability는 commands write와 독립적으로 유지한다. legacy write는 explicit feature-off/kill-switch/cohort rollback에서만 복원하고, read-only timeline은 그와 동시에 계속 접근 가능하다.
- runtime 불건전, unsupported 또는 불완전한 command capability는 legacy write로 우회하지 않고 blocked 상태로 표시한다.

### Gate 4 — immutable attachment snapshot과 복구 UX

- `f8b0d3e5a7c9`는 `e7a9c2d4f6b8` 뒤에 presentation tombstone 필드, immutable attachment snapshot, `workspace_cleanup_journal`을 추가하고, 현재 Alembic sole head `fa1b2c3d4e5f`는 계정 삭제 admission/publication fence를 추가한다.
- first-message attachment는 `asset_id_snapshot`, filename, MIME, size, SHA-256, state-at-time과 안전한 failure code/detail을 보존한다. live `asset_id`는 nullable + `ON DELETE SET NULL`이고 timeline은 snapshot을 주 데이터, 현재 asset 상태를 별도 projection으로 사용한다.
- UI는 당시/현재 상태, 형식·크기·파일명과 안전 오류를 구분해 표시하고 가능한 다운로드/미리보기 또는 재첨부·retry 경로를 제공한다. 원본 row 변경·삭제는 과거 메시지를 바꾸지 않는다.
- f8 upgrade는 snapshot column을 추가하기 전에 attachment의 `asset_id`가 null이거나 대응 live asset row가 없는지 preflight한다. dangling attachment가 하나라도 있으면 이전 Alembic revision과 schema를 그대로 유지한 채 fail-close하며, FK를 의도적으로 끈 SQLite fixture가 이 무부분변경 경계를 고정한다. 정보 손실이 예상되는 f8 downgrade 대신 flag/kill switch rollback으로 additive history를 보존한다.

### Gate 5 — Workspace E2E required CI

- live browser 경계는 immutable attachment snapshot의 당시 `uploaded` 상태를 먼저 확인한 뒤 live asset을 명시적으로 `failed`로 전이하고, current-state failure·safe failure code·generation `blocked` 투영을 검증한다. harness의 backfill gate는 marker가 있는 시나리오에만 적용하고 `afterEach` release로 실패한 spec이 후속 spec을 멈추지 않게 하며, 일반 성공 명령은 즉시 완료된다.

- CI workflow는 `npm run test:workspace-e2e`를 관련 PR의 독립 job으로 실행하고 production lifecycle test를 backend FastAPI job의 비선택 step으로 실행한다. `required-CI` contract test는 이 topology와 timeout 뒤 서버/Cypress 로그·진단 artifact 수집을 검사한다. 실제 merge 차단에 필요한 required status 지정은 repository branch/merge-check 설정에서 별도로 확인한다.
- 표준 local runner도 같은 browser E2E를 기본 profile에 포함하고 선택 capability는 `NOT RUN`으로 명시해 성공으로 세지 않는다.
- 모든 run은 격리 temp/database/port와 cleanup trap을 사용한다. Windows child는 retained supervisor가 `CreateProcessW(CREATE_SUSPENDED)`로 만든 뒤 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object에 할당하고서만 resume한다. 정상 cleanup은 Node가 보유한 supervisor `ChildProcess` handle을 종료하고 실제 exit를 기다리며 기록된 PID, PPID 탐색 또는 `taskkill`로 process tree를 신호하지 않는다. lock metadata의 `processIdentity.birthId`는 `Get-Process -Id ... .StartTime.ToUniversalTime().ToFileTimeUtc()`의 exact FILETIME decimal string이고 `executable`은 `.Path`다. supervisor는 retained parent HANDLE에서 `GetProcessTimes`와 `QueryFullProcessImageName`으로 동일 identity를 다시 검증한다. stale recovery에서 정확한 supervisor가 살아 있거나 identity/legacy metadata를 검증할 수 없으면 project restore·lock release를 fail-close하고, supervisor가 사라졌거나 PID가 다른 process로 재사용된 경우에만 어떤 PID도 종료하지 않은 채 비파괴 recovery를 진행한다. 실제 supervisor create/exit와 retained-handle termination probe는 이 identity·Job Object 경계를 검증하지만 전체 stale recovery나 candidate RC 실행 증거는 아니다. `.cmd`/`.exe` 선택과 locked-handle `EPERM`/`EACCES`/`EBUSY` retry·진단 보존도 runner가 소유한 경로의 원인 추적을 돕는다. 다만 pytest 자체의 전역 numbered-temp/`pytest-current` atexit 정리는 별도 host 경계다. 해당 `PermissionError`/`WinError 5`가 남으면 종료 코드 0만으로 Windows cleanup Gate를 닫지 않고, 실제 Windows에서 고유 `--basetemp` 재실행과 잠금 process·ACL/handle 진단을 release evidence에 남긴다.

### Gate 6 — receipt/error/schema-version 계약

- command/action은 `schema_version`, `receipt_id`, `operation_id`, `duplicate`, `status`, `timeline`을 가진 versioned envelope를 반환하고 기존 flat field를 호환 기간 동안 함께 제공한다. durable duplicate receipt가 wire에서도 `duplicate=true`로 관찰된다.
- 중앙 registry가 `{error:{status_code,code,safe_message,retryable},request_id}`와 OpenAPI response를 생성한다. raw exception, 내부 경로, secret은 public projection에 포함하지 않는다.
- schema-version과 operation kind는 단일 immutable catalog의 strict allowlist로 입구에서 검증한다. command/bootstrap은 catalog에 없는 kind를 durable row 생성 전에 거부하고, 이미 저장된 legacy unknown operation은 action이 없는 non-retryable unsupported card로만 투영한다. 서버 `allowed_actions`가 UI action을 결정하며 클라이언트 상태 추측을 제거한다.
- operation card는 actor, 시간, 대상, revision, activity, undo 또는 미지원 사유를 표시한다. action/re-export Promise와 history load 실패를 명시적으로 처리하고 설정/result raw key는 사용자 문구로 humanize한다.
- `contracts/chat-first-workspace/v1/*.json`이 canonical wire fixture다. backend는 public Pydantic model의 exact round-trip과 생성된 OpenAPI `$ref`/response shape를 fixture에 대조하고, frontend Node suite는 같은 파일을 실제 response/error/timeline decoder로 통과시킨다. fixture 파일 존재만 확인하는 검사는 계약 증거로 보지 않는다.

### Gate 7 — production-shaped lifecycle

- `WORKSPACE_OPERATION_WORKER_MODE=external`이면 API lifespan의 embedded runner 대신 `python -m services.workspace_operation_worker` 프로세스가 claim, heartbeat, recovery와 handler execution을 소유한다.
- production lifecycle integration test는 Alembic-upgraded 빈 SQLite DB와 실제 ASGI app lifespan, 별도 external worker process에서 bootstrap/duplicate receipt, stale revision, worker 종료와 marker의 lease-owner PID 기록, lease expiry 뒤 recovery, SSE disconnect + `Last-Event-ID` backfill을 검증한다. 별도 browser용 실제 HTTP/SQLite harness는 embedded durable runner와 결정론적 edit handler를 사용하며 `uv run python -m tests.support.workspace_browser_live_harness --port 8765`로 재현한다.
- partial external publication/write는 production lifecycle 한 건이 아니라 deterministic fault/compensation tests에서 검증한다. PostgreSQL은 test URL이 있을 때 별도 dialect/concurrency suite 대상이며, 이 문서는 실제 PostgreSQL 배포 topology나 실제 LLM, production object store/scanner 실행을 완료했다고 주장하지 않는다.

### Gate 8 — rollout·관측성·Windows 패키징·접근성

- 서버 rollout은 kill switch, owner + deck ID의 salted stable cohort, shadow/parity 판정을 제공한다. partial rollout에서 deck scope가 없으면 fail-close하고 kill switch가 cohort보다 우선한다.
- low-cardinality metrics와 SLO는 durable non-duplicate operation admission을 분모로 한 allowlisted operation/rollout/event lag/shadow parity/silent loss/dead-letter/lease-lost/revision-conflict와 cleanup/outbox claim·terminal outcome만 노출한다. API/worker scrape absent·down, shadow silent loss/divergence, telemetry sink failure와 event lag breach/stall을 Prometheus rule로 감시하며, lag-stall 분모는 chat-memory delivery가 아니라 `workspace.operation.event`의 `event_delivered`만 사용한다.
- standalone packaging은 symlink 권한을 사전 점검하고 copy fallback, 격리 output, 명확한 진단을 제공한다. PR Windows workflow는 preflight와 link-free staging 계약을 자동 검사하며, 전체 standalone package/RC 작업은 명시적 workflow dispatch 대상이다. 이 source-tree 문서는 실제 Windows RC 실행 완료를 주장하지 않는다.
- `/Logo.png`는 명시적 priority/size 경계로 LCP 경고를 제거한다. route E2E는 axe 위반 0, destructive approval의 제한된 assertive live region, 단일 polite status, tab/disclosure/progress ARIA와 기준 viewport를 검증한다.

### 보존되는 모드 경계

`template.apply` authored 경로는 원본 presentation timeline에서 작업을 추적하고 별도 생성된 새 덱 링크를 결과로 노출한다. authored/template/adaptive/Template V2의 내부 저장 모델은 합치지 않으며, 최종 publish만 expected revision, attempt/fencing, immutable artifact 검증이라는 공통 계약을 통과한다. public event payload는 operation-kind allowlist로 내부 generation task, storage path, cookie/header를 숨긴다.

### 활성화와 롤백 판정

기본 활성화 전에는 Gate 1~8 각각의 evidence가 같은 candidate에서 재현되어야 한다. 특히 capability가 read/write를 정확히 분리하고, cleanup journal backlog/`dead_letter`, SSE lag/rebase, shadow parity/silent loss와 lease/revision conflict가 SLO 안에 있어야 한다. legacy popup은 대응 command의 parity가 확인된 기능부터 제거한다.

긴급 롤백은 kill switch로 새 write ingress를 먼저 차단하고 backend feature/cohort capability를 legacy로 전환한 뒤 external worker/operation/outbox 상태를 조정한다. `NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED` 자체를 내리는 절차는 rebuild/redeploy가 필요하므로 즉시 runtime 제어로 표현하지 않는다. 단, `workspace_cleanup_journal` sweeper는 chat-first write flag와 독립된 app lifecycle이므로 flag-off 중에도 계속 실행해 presentation/account deletion의 durable cleanup을 복구한다. 정상 app shutdown 때만 중단한다. 이미 commit된 timeline, attachment snapshot, receipt, artifact metadata와 cleanup intent는 지우지 않으며 f8/fa1 schema downgrade는 일반 롤백에 포함하지 않는다.
