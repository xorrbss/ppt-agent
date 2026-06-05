# Compose UX 재설계 — Claude.ai 스타일 메인 화면

> 상태: **설계 완료, 구현 전.** 제품 결정 4가지 미확정(§11). 코드 변경 없음.
> 작성 근거: 현행 5개 영역(진입/라우팅·업로드·템플릿·채팅·생성 파이프라인) 병렬 코드 탐색.

## 1. 목표

메인 화면을 Claude.ai식 단일 **컴포즈 화면**으로 만든다:

```
[템플릿 픽커] + [큰 프롬프트 박스] + [📎 파일 첨부]  → '생성'
   → (개요는 화면 뒤에서 스트리밍, 진행바만)
   → /presentation 에디터에서 수정(기존 대화형 Chat)
```

- **템플릿 선택을 메인 화면으로** 옮긴다(현재는 `/outline`에 있음).
- **파일 업로드를 컴포즈 입력의 📎 첨부**로 통합한다.
- **수정은 에디터의 기존 채팅**으로 한다(두 번째 채팅을 만들지 않는다).

## 2. 현행 흐름 (구현 전 반드시 코드로 재확인)

```
/upload (프롬프트 + 파일 + 설정, 템플릿 없음)
  → /documents-preview (파일 첨부 시 분해 결과 미리보기)
  → /outline (개요 SSE 스트리밍 + ★템플릿 선택이 지금 여기★)
  → /presentation?id=… 에디터 (우측 Chat.tsx 툴콜 수정 에이전트)
```

핵심 파일:

| 영역 | 파일 |
|---|---|
| 진입/리다이렉트 | `app/page.tsx` → `components/Auth/AuthGate.tsx` (인증 후 `/upload`로 `replace`) |
| 입력 | `app/(presentation-generator)/upload/components/UploadPage.tsx` (+ `PromptInput`, `SupportingDoc`, `ConfigurationSelects`, `AdvanceSettings`) |
| 파일 첨부 | `upload/components/SupportingDoc.tsx` — **이미 클립 + 드래그드롭 + 검증(최대 8개, word/pdf/spreadsheet/image/text/presentation) 완비** |
| 템플릿 선택 | `app/(presentation-generator)/outline/components/TemplateSelection.tsx` (+ `BuiltInTemplateCard`) |
| 개요 | `outline/components/OutlinePage.tsx`, `outline/hooks/useOutlineStreaming.ts`, `outline/hooks/usePresentationGeneration.ts` |
| 에디터 | `presentation/page.tsx` → `PresentationPage.tsx`, `presentation/components/Chat.tsx`(첨부 버튼 현재 비활성), `presentation/hooks/usePresentationStreaming.ts` |
| 내비 | `(dashboard)/Components/DashboardNav.tsx` — `/generate` 링크가 **깨져 있음(404)** |
| 상태 | `store/slices/presentationGenUpload.ts`(`{config, files}`), `store/slices/presentationGeneration.ts`(`presentation_id`, `outlines`, `presentationData`) |
| 백엔드 | `api/v1/ppt/endpoints/presentation.py`(`create`~232줄, `prepare`, `stream`, 동기 one-shot `generate`~1018줄은 **별개**), `outlines.py` |

**백엔드 생성 계약:** `create → outlines/stream → prepare → presentation/stream` 4단계. (동기 `POST /presentation/generate`는 렌더+익스포트를 동기로 하고 파일 경로를 반환 — 에디터 전환 경로가 아니므로 혼동 금지.)

## 3. 제안 흐름 (타깃)

기존 파이프라인을 **그대로 재사용**하고, 깔때기 앞단 UI만 통합한다.

1. `/upload`(컴포즈 홈)에 중앙 컴포즈 카드: 상단 템플릿 스트립, 중앙 프롬프트, 📎 첨부.
2. 사용자가 (a) 템플릿 선택 (b) 프롬프트 입력 (c) 선택적 파일 첨부.
3. '생성' 클릭:
   - 파일 있으면 `uploadDoc()` + `decomposeDocuments()` → `createPresentation({content, file_paths, ...})`.
   - 없으면 `createPresentation()` 직접.
   - 선택 템플릿을 Redux(`presentationGenUpload.selectedTemplate`)에 보존, `setPresentationId()`, 생성 브리지로 이동.
4. **개요 단계는 사용자 경로에서 숨기되 파이프라인엔 유지**: `/outline?auto=1`(또는 동일 훅을 직접 구동)에서 개요가 그대로 스트리밍되고 `presentationPrepare()`도 그대로 호출 — 단 편집형 2탭 UI 대신 진행바(OverlayLoader)만 표시. 개요 완료 즉시 `usePresentationGeneration.handleSubmit()`가 Redux 템플릿으로 자동 1회 실행 → `router.replace("/presentation?id=…&stream=true")`.
5. **에디터는 그대로**: 슬라이드 스트리밍 + 우측 `Chat.tsx`로 수정.

순효과: 사용자에겐 컴포즈 → 진행바 → 에디터(Claude식). 백엔드 계약과 에디터는 무변경. 개요 편집 UI는 삭제가 아니라 파워유저용으로 강등.

## 4. 정보 구조 (IA)

- **`/upload`(컴포즈 홈)** — 단일 카드:
  - **템플릿 픽커(상단):** 내장 템플릿 카드 가로 스트립 + "더 보기"로 `TemplateSelection.tsx` 전체 그리드 다이얼로그. 기본 선택값 존재 → 박스가 항상 제출 가능.
  - **프롬프트 박스(중앙):** `PromptInput.tsx`를 포컬 요소로.
  - **첨부(📎):** `SupportingDoc.tsx`의 compact 변형(파일 칩 + 카드 전체 드래그드롭).
  - **보조 설정(접힘):** 언어/슬라이드 인라인, 고급은 기존 `AdvanceSettings.tsx` 모달. 기능 제거 없이 시각적으로만 약화.
  - **단일 CTA:** '생성'.
- **생성 진행 상태:** `OverlayLoader`(두 페이지에서 이미 사용) 재사용.
- **`/presentation?id=`(에디터):** 무변경. 우측 `Chat.tsx`가 "수정" 대화 표면.
- **`/dashboard`, `/templates`, `/theme`, `/settings`:** 무변경. "새 프레젠테이션" 링크는 `/upload`로.

> 컴포즈 화면엔 **대화형 채팅을 두지 않는다** — 컴포즈는 프롬프트+첨부 폼, 대화는 에디터의 역할.

## 5. 재사용 vs 신규

**재사용(신규 파일 0):**
- `Chat.tsx`(에디터) — "수정" 표면 그대로. 두 번째 컴포즈용 채팅 만들지 않음. (Chat을 presentationId에서 분리해 생성용 툴셋을 만드는 안은 대규모·고위험 → YAGNI로 기각.)
- `SupportingDoc.tsx` — 📎+드래그드롭+검증 보유. `compact` 변형으로 재사용.
- `TemplateSelection.tsx` + `BuiltInTemplateCard` — 컴포즈에 인라인 스트립+다이얼로그로 마운트.
- `useOutlineStreaming.ts` + `usePresentationGeneration.ts` + 백엔드 4단계 계약 — 그대로 재사용(load-bearing).
- `presentationGeneration` / `presentationGenUpload` 슬라이스 — 재사용, 필드 1개만 추가.
- `OverlayLoader` — 진행 상태 재사용.

**신규(정당화됨):**
- `UploadPage.tsx` 내부 컴포즈 레이아웃 조합(신규 파일 아님, 기존 컴포넌트 확장). 500라인 초과 시 템플릿 스트립만 작은 표현 컴포넌트로 분리.
- `SupportingDoc.tsx`의 `compact` 분기(작은 추가 prop, 신규 파일 아님).

**순계:** 백엔드 코드 0, 신규 API 0, 신규 슬라이스 0, Redux 필드 +1, 나머지는 기존 컴포넌트의 UI 통합.

## 6. 컴포넌트별 변경

| 컴포넌트 | 변경 | 파일 | 종류 |
|---|---|---|---|
| UploadPage | 중앙 컴포즈 레이아웃으로 전환; 템플릿 픽커+프롬프트+📎 한 카드; `selectedTemplate` 상태+기본값; 제출 시 템플릿 Redux 보존 후 자동생성 경로로. 기존 설정/검증/Mixpanel 유지 | `upload/components/UploadPage.tsx` | extend |
| TemplateSelection | "더 보기" 다이얼로그로 그대로 재사용 + 앞 N개를 인라인 스트립으로(`BuiltInTemplateCard`) | `outline/components/TemplateSelection.tsx` | reuse |
| SupportingDoc | `compact` prop 추가(📎 트리거+칩 리스트). 검증/최대 8개/타입셋 재사용 | `upload/components/SupportingDoc.tsx` | extend |
| PromptInput | 컴포즈 textarea로 재사용, 포컬 박스 스타일 | `upload/components/PromptInput.tsx` | reuse |
| ConfigurationSelects / AdvanceSettings | 컴포즈 카드 푸터/고급 모달로 재배치. 로직 무변경 | `upload/components/ConfigurationSelects.tsx` | reuse |
| usePresentationGeneration | auto 모드 트리거 추가: auto이고 개요 완료 + Redux 템플릿 존재 시 기존 `handleSubmit()` 1회 호출. prepare/레이아웃 로직 무변경 | `outline/hooks/usePresentationGeneration.ts` | extend |
| OutlinePage | `?auto=1` 모드: 진행바 뷰만 렌더, 편집 2탭 스킵, Redux 템플릿 사용. 비-auto 경로는 전체 2탭 유지 | `outline/components/OutlinePage.tsx` | extend |
| useOutlineStreaming | 무변경 재사용 | `outline/hooks/useOutlineStreaming.ts` | reuse |
| DashboardNav | 깨진 `href="/generate"` → `/upload` | `(dashboard)/Components/DashboardNav.tsx` | extend |
| Chat(에디터) | 무변경 재사용. 컴포즈에 전용 금지. (후속 단계에서 첨부 버튼 활성화는 별개·YAGNI) | `presentation/components/Chat.tsx` | reuse |

## 7. API 계약 변경

**없음.** 전부 그대로 재사용:

| 엔드포인트 | 변경 |
|---|---|
| `POST /api/v1/ppt/files/upload` | 없음 (컴포즈 첨부에 재사용) |
| `POST /api/v1/ppt/files/decompose` | 없음 (제출 시 조용히 분해) |
| `POST /api/v1/ppt/presentation/create` | 없음 (이미 content+file_paths+config 수용, ~232줄). 템플릿은 `/prepare`에서 적용 → 새 컬럼/마이그레이션 불필요 |
| `GET /api/v1/ppt/outlines/stream/{id}` | 없음 (UI만 진행바로 교체) |
| `POST /api/v1/ppt/presentation/prepare` | 없음 (선택 템플릿으로 만든 `PresentationLayoutModel` 그대로 수신) |
| `GET /api/v1/ppt/presentation/stream/{id}` | 없음 |
| `POST /api/v1/ppt/presentation/generate`(동기) | 없음 + 컴포즈 흐름에서 미사용 |

## 8. 상태(Redux) 변경

- **`presentationGenUpload`**: 선택적 필드 `selectedTemplate` **1개 추가**(+ `setPptGenUploadState` 페이로드 처리). 가산적·하위호환. 컴포즈에서 고른 템플릿을 자동생성 단계로 운반. 테마 상태는 추가 안 함(테마는 에디터측 CSS 변수 관심사, 생성 계약 밖 → YAGNI).
- **`presentationGeneration`**: 구조 변경 없음. `setPresentationId`/`clearOutlines`/`setOutlines`/`clearPresentationData` 그대로 사용.

## 9. 라우팅 변경

- **`/upload` 유지(이름 변경 금지)** — `AuthGate`, 대시보드 `/upload` 링크, `Header.tsx` 뒤로가기 로직이 전부 `/upload`를 가리킴. `/compose` 개명은 ~4파일을 건드리며 이득 0(YAGNI). 원하면 후속(P4)에 얇은 별칭만 추가.
- `/upload`: 단일 컴포즈 화면. 기본 경로에서 `/documents-preview`를 가시 단계로 안 보냄.
- `/outline`: 비가시 `?auto=1` 모드(개요 스트리밍+자동 prepare를 진행바 아래) 추가. 편집 2탭은 비-auto 진입으로 유지.
- `/documents-preview`: 기본 경로에서 제외. 라우트는 당분간 유지(딥링크 보호), 사용량 확인 후 삭제 검토.
- `DashboardNav.tsx`: `/generate` → `/upload`.
- 에디터 라우팅 무변경: `/presentation?id=…&stream=true`.

## 10. 단계별 계획 (각 독립 머지)

- **P0 — 라우팅 위생:** `DashboardNav`의 `/generate` → `/upload`. (`(dashboard)/Components/DashboardNav.tsx`)
- **P1 — 컴포즈 박스(흐름 무변경):** `UploadPage`를 중앙 카드로(큰 `PromptInput`, `compact` `SupportingDoc` 📎, 설정 약화). 제출은 기존 2경로 유지. 위험 0, 즉시 UX 개선.
- **P2 — 템플릿 픽커:** `TemplateSelection` 인라인 스트립+다이얼로그 마운트, `selectedTemplate` 기본값+Redux 보존.
- **P3 — 자동생성 브리지:** `OutlinePage`에 `?auto=1`(OverlayLoader + 개요 완료 시 `handleSubmit()` 1회) 추가, 컴포즈 제출을 이 브리지로. 편집 개요 UI는 비-auto로 유지.
- **P4(선택):** `/compose` 별칭, 에디터 `Chat` 첨부 버튼 활성화, 사용량 확인 후 `/documents-preview` 정리.

## 11. 열린 제품 결정 (구현 전 확정 필요)

| # | 결정 | 추천 |
|---|---|---|
| 1 | 개요 검토/수정 단계 | **숨기되 `/outline` 유지**(파워유저용). 동작 기능을 지우지 않음(YAGNI) |
| 2 | 메인 입력 방식 | **단발 입력 후 생성**. 대화형 다듬기는 에디터 Chat에서. (다회차 생성용 채팅은 대규모·YAGNI) |
| 3 | 템플릿 선택 | **명시적 기본값(general/korean-biz)+선택 가능**. Claude식 UX는 픽커에서 막지 않음 |
| 4 | 첨부 문서 처리 | **조용히 분해 후 생성**. `/documents-preview`는 사용량 확인 전까지 오프-패스 유지 |
| (보조) | `/upload`→`/compose` 개명 | 당분간 `/upload` 유지, 별칭은 P4 선택 |
| (보조) | 컴포즈에 테마 픽커 | 아니오(테마는 에디터/대시보드 관심사, YAGNI) |
| (보조) | 에디터 Chat 첨부 버튼 활성화 | 이번 이니셔티브에선 비활성 유지(별개, YAGNI) |

## 12. 리스크

- **자동 제출 타이밍:** 개요 스트리밍 완료 후 `handleSubmit()`가 **정확히 1회**만 실행돼야 함(재렌더 재발화 방지 — single-shot ref 가드). 스트림 에러/재시도 시 prepare 조기 호출 금지.
- **기본 템플릿 정확성:** `templates[0]`이 아니라 **명시적 id**(`general`/`korean-biz`)로 고정. 레지스트리 순서 변경 시 기본값이 조용히 바뀌는 것 방지.
- **파일만/프롬프트 없음 경로:** 현재 파일만 제출 허용 — 컴포즈도 보존(분해 후 빈 content로 create), 파일 있을 때 `language=Auto` 차단 로직 유지.
- **UploadPage 500라인 제한:** 템플릿 픽커+compact 첨부 추가로 초과 시 템플릿 스트립만 작은 자식으로 분리(과분할 금지).
- **개요 편집 제거 영향:** 일부 사용자 의존 기능 — 비-auto `/outline`을 "개요 편집" 진입으로 유지해 완화(발견 경로는 결정 필요).
- **커스텀 템플릿 로딩:** `useCustomTemplateSummaries`(네트워크) — 인라인 스트립은 내장 먼저, 지연 로드.
- **`/documents-preview` 오프-패스 유지:** 유지보수 혼동 방지를 위해 "사용량 확인 후 폐기 예정" TODO 명시.

## 13. 참고

이 설계와 별개로, 한글 OCR 오프라인 / UI 한글화(토스트 ~66건) / 크로스플랫폼 Electron 스크립트 / 툴링·코드헬스 정리가 브랜치 `feat/korean-windows-improvements`로 **로컬 `main`에 머지(미push)** 되어 있다. `git push origin main`은 소유자 승인 후.
