# Genspark형 업로드·템플릿 카탈로그 UI/UX 설계

- 작성일: 2026-08-12
- 상태: 구현 전 설계 확정안
- 적용 범위: `/upload` 프런트엔드 진입 화면
- 기준: Genspark AI 슬라이드 라이브 화면·390/768/1048/1280/1536/1920 CSS px 구간 실측, 현재 PPT Agent 코드 구조

> 이 문서는 `docs/compose-ux-design.md` 중 업로드 진입 화면의 레이아웃 설계를 대체한다. 생성 이후의 편집 경험은 `docs/chat-first-slides-workbench-design-20260802.md`, 생성 파이프라인과 API 계약은 기존 구현을 유지한다.

## 1. 결정 요약

업로드 화면을 `설정 폼 + 하단 고정 생성 버튼` 구조에서 `상단 생성 Composer + 하단 탐색형 템플릿 카탈로그` 구조로 바꾼다.

가장 중요한 원칙은 **해상도별 고정 템플릿 수가 아니라 실제 콘텐츠 컨테이너 폭에 따라 카드 열 수가 자동 결정**되는 것이다.

- Composer는 최대 760px이며 좁은 화면에서는 좌우 16px 여백을 남기고 유동 축소한다.
- 카탈로그는 별도의 고정 최대 폭을 두지 않고 사용 가능한 콘텐츠 폭을 모두 쓴다.
- 카드 목표 폭은 약 300–330px이며 컨테이너가 넓어지면 열이 자동으로 추가된다.
- 모바일에서는 카드 폭보다 탐색 밀도를 우선해 2열을 유지한다.
- 기존 `selectedTemplate`, `selectedTheme`, `authoredStyle`, 프롬프트·파일·생성 설정과 생성 API는 그대로 재사용한다.
- 카드 선택과 생성 실행은 하나의 화면 안에서 완료하며, 기존 하단 고정 CTA는 제거한다.

## 2. 목표와 비목표

### 목표

1. 첫 화면에서 사용자가 프롬프트 입력과 템플릿 탐색을 동시에 이해한다.
2. 데스크톱·울트라와이드에서 빈 공간을 남기지 않고 카드 열이 자연스럽게 늘어난다.
3. 좁은 화면에서도 수평 스크롤 없이 Composer와 카탈로그를 사용할 수 있다.
4. 현재 생성 로직을 바꾸지 않고 UI 구조만 단계적으로 교체할 수 있다.
5. 템플릿 카드의 시각적 밀도를 높이고 선택·미리보기·키보드 접근성을 함께 제공한다.

### 비목표

- 이번 1차 설계에서 생성 API, 백엔드 스키마, 편집 워크벤치를 변경하지 않는다.
- 데이터가 없는 `인기순`을 임의의 점수로 구현하지 않는다.
- 음성 입력 백엔드가 준비되지 않은 상태에서 마이크 버튼을 장식용으로 노출하지 않는다.
- Genspark의 브랜드, 문구, 아이콘을 그대로 복제하지 않는다.
- 한 화면에 다수의 미리보기 이미지를 선로딩하는 방식은 복제하지 않는다.

## 3. 라이브 화면 분석 결과

스크린샷의 물리 픽셀을 그대로 CSS 값으로 사용하면 안 된다. 실측 환경 중 하나는 DPR 1.25였으며, 물리 950px로 보이는 Composer는 실제 CSS 기준 약 760px였다.

### 3.1 반응형 동작

| CSS viewport | 실제 콘텐츠 폭 | 사이드바 | 카드 열 | 카드 폭 |
|---:|---:|---|---:|---:|
| 390 | 약 343px | 숨김 | 2 | 약 147px |
| 768 | 약 721px | 숨김 | 2 | 약 336px |
| 1048 | 약 1001px | 숨김 | 3 | 약 312px |
| 1280 | 약 1135px | 66px | 3 | 약 356px |
| 1536 | 약 1391px | 66px | 4 | 약 319px |
| 1920 | 약 1775px | 66px | 5 | 약 329px |

관찰 결과는 `1024px이면 3열` 같은 고정 해상도 규칙이 아니다. **사이드바·패딩을 제외한 실제 콘텐츠 폭에서 적정 카드 폭을 몇 개 배치할 수 있는지가 결과로 나타난 것**이다.

### 3.2 주요 시각 수치

- 데스크톱 사이드바: 66px, 약 1280px 미만에서 숨김
- 상단 헤더: 64px
- 모바일/태블릿 콘텐츠 좌우 패딩: 16px
- 데스크톱 콘텐츠 좌우 패딩: 32px
- 제목: 32px / 48px, 700
- Composer: 최대 760px, 높이 약 114px, 반경 16px
- Composer 그림자: `0 6px 30px rgb(0 0 0 / 8%)`
- 필터 칩: 높이 약 38px, pill 형태
- 카드: 16:9, 반경 12px, 열 간격 16px, 행 간격 20px
- 카드 Hover: 이미지 `scale(1.02)`, 선택 버튼은 아래에서 위로 10px 이동하며 표시, 약 300ms

### 3.3 그대로 복제하지 않을 문제

- 390px에서 상단 모드 버튼 일부가 잘리는 현상은 재현하지 않는다. 줄바꿈 또는 가로 스크롤을 제공한다.
- 라이브 화면은 20개 카드에 264개 이미지가 DOM에 존재했다. 커버 1장만 지연 로드하고 Hover/가시 영역 진입 시 제한된 추가 프레임만 로드한다.
- Hover가 없는 터치 기기에는 항상 명확한 선택 상태와 탭 동작을 제공한다.

### 3.4 현재 구현과의 차이

- `UploadPage.tsx`의 `lg:max-w-[60%] xl:max-w-[54%]`가 Composer뿐 아니라 템플릿 영역까지 제한한다.
- `TemplateSelection.tsx`는 `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`이라 넓은 화면에서도 3열이 상한이다.
- `StructuredTemplateCard.tsx`는 16:9 이미지 아래 최소 168px 상세 영역을 포함해 탐색형 카탈로그보다 설정 카드에 가깝다.
- 생성 CTA가 하단 고정 바에 분리되어 프롬프트와 실행의 시각적 연속성이 약하다.
- 상세 설정이 펼침 영역 안에서 페이지 길이를 밀어내므로 첫 화면의 템플릿 탐색 밀도가 낮다.

따라서 기존 카드의 CSS만 조정하는 것으로는 목표 화면이 나오지 않는다. Hero와 Catalog의 폭 정책을 분리하고, 카탈로그 전용 Compact card를 추가해야 한다.

## 4. 목표 정보구조

```text
UploadExperience
├─ UploadHeader
│  ├─ Back
│  ├─ ProductNavigation (넓은 화면에서만)
│  └─ UtilityActions
├─ CreationHero
│  ├─ Title
│  ├─ GenerationModeControls
│  └─ PresentationComposer
│     ├─ PromptTextarea
│     ├─ AttachmentTrigger / SupportingDocChips
│     ├─ ConfigSummaryTrigger
│     ├─ GenerateButton
│     └─ InlineValidation
└─ TemplateCatalog
   ├─ CatalogTabs (탐색 / 내 템플릿)
   ├─ CatalogFilters (스타일 / 테마 / 정렬)
   ├─ CompactTemplateGrid
   │  ├─ AddTemplateCard
   │  └─ CompactTemplateCard[]
   └─ TemplatePreviewDialog
```

화면의 시각적 우선순위는 `무엇을 만들지 입력 → 어떤 스타일을 쓸지 탐색 → 생성` 순서다. 상세 생성 설정은 Composer의 설정 요약 버튼에서 기존 모달 또는 팝오버로 연다.

### 4.1 데스크톱 와이어프레임

```text
┌─ Header / optional sidebar ─────────────────────────────────────────────┐
│                                                                         │
│                         AI 프레젠테이션 만들기                           │
│                   [템플릿 기반] [AI 저작] [설정]                        │
│              ┌──────────────────────────────────────┐                   │
│              │ 만들 내용을 입력하세요…              │                   │
│              │                                      │                   │
│              │ [+ 첨부] [10장 · 한국어]       [생성]│                   │
│              └──────────────────────────────────────┘                   │
│                                                                         │
│  탐색   내 템플릿                                                       │
│  ────                                                                   │
│  [모든 스타일⌄] [모든 테마⌄] [추천순⌄]                                  │
│                                                                         │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ...        │
│  │ 내 템플릿 +│ │ 16:9 cover │ │ 16:9 cover │ │ 16:9 cover │            │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘            │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 모바일 와이어프레임

```text
┌──────────────────────────────┐
│ ←                    설정    │
│   AI 프레젠테이션 만들기     │
│ [템플릿 기반] [AI 저작]      │
│ ┌──────────────────────────┐ │
│ │ 만들 내용을 입력하세요… │ │
│ │ [+] [10장·한국어]  [생성]│ │
│ └──────────────────────────┘ │
│                              │
│ 탐색   내 템플릿             │
│ [스타일⌄] [테마⌄]            │
│ [추천순⌄]                    │
│ ┌────────────┐ ┌───────────┐ │
│ │ 16:9 card  │ │16:9 card  │ │
│ └────────────┘ └───────────┘ │
│ ┌────────────┐ ┌───────────┐ │
│ │ 16:9 card  │ │16:9 card  │ │
│ └────────────┘ └───────────┘ │
└──────────────────────────────┘
```

## 5. 레이아웃 설계

### 5.1 전체 Shell

- 페이지 자체가 아닌 콘텐츠 영역이 스크롤되도록 현재 앱 Shell 규칙과 맞춘다.
- `>= 1280px`에서만 66px 보조 내비게이션을 표시할 수 있다. 1차 구현에서는 기존 Header를 유지하고 Shell 확장은 별도 단계로 분리한다.
- Hero와 Catalog는 동일한 콘텐츠 루트에 있지만 폭 정책이 다르다.
  - Hero 내부 Composer: 최대 760px, 중앙 정렬
  - Catalog: 사용 가능한 콘텐츠 폭 100%
- 현재 `UploadPage.tsx`의 `lg:max-w-[60%] xl:max-w-[54%]`는 제거한다. 이 제한이 카탈로그의 자동 확장을 막는 직접 원인이다.

### 5.2 Composer

```css
.composer {
  inline-size: min(760px, calc(100% - 32px));
  min-block-size: 114px;
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  box-shadow: 0 6px 30px rgb(0 0 0 / 8%);
}
```

- 제목과 모드 컨트롤도 Composer 기준선에 정렬한다.
- 프롬프트는 최소 2줄, 내용에 따라 최대 8줄까지 자동 확장한 뒤 내부 스크롤한다.
- 하단 왼쪽에는 첨부와 설정 요약, 오른쪽에는 생성 버튼을 배치한다.
- 생성 버튼은 `prompt.trim()` 또는 첨부 문서가 있고 생성 중이 아닐 때 활성화한다.
- Enter는 생성, Shift+Enter는 줄바꿈이며 한글 IME 조합 중 Enter는 제출하지 않는다.

### 5.3 Catalog Container

```css
.catalog {
  container-type: inline-size;
  inline-size: 100%;
}

.templateGrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));
  column-gap: 16px;
  row-gap: 20px;
}

@container (max-width: 640px) {
  .templateGrid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
}
```

핵심 규칙:

- 열 개수에 최대값을 두지 않는다.
- 브라우저 viewport가 아닌 `.catalog`의 실제 inline size를 기준으로 한다.
- `auto-fit` 결과가 768px에서 2열, 약 1000px에서 3열, 약 1390px에서 4열, 약 1775px에서 5열이 되는지 검증한다.
- 640px 이하에서는 모바일 탐색 밀도를 위해 2열을 강제한다.
- 카드 내부 썸네일은 항상 `aspect-ratio: 16 / 9`와 `object-fit: cover`를 사용한다.
- 카드 제목·메타데이터가 필요하면 이미지 아래 1–2줄만 허용하고, 모든 카드의 행 높이를 통일한다.

### 5.4 모드와 필터의 줄바꿈

- 모드 컨트롤은 `display:flex; flex-wrap:wrap; gap:8px`을 기본으로 한다.
- 한 행을 유지해야 하는 영역은 `overflow-x:auto; scrollbar-width:none`을 사용하되 버튼 자체는 축소하지 않는다.
- 필터는 `flex-wrap:wrap`한다. 390px에서 세 번째 필터가 다음 행으로 내려가도 정상이다.
- 열 개수와 버튼 줄바꿈은 서로 독립적으로 계산한다.

## 6. 디자인 토큰

| 토큰 | 값 | 용도 |
|---|---|---|
| `--surface-page` | `#FFFFFF` | 페이지 배경 |
| `--surface-muted` | `#F6F7F8` | 선택·보조 버튼 |
| `--text-primary` | `#242629` | 제목·본문 |
| `--text-secondary` | `#7C828A` | Placeholder·메타 |
| `--border-subtle` | `#E7E9EC` | 카드·Composer |
| `--accent` | 현재 제품 Primary | 선택·Focus·생성 |
| `--radius-composer` | `16px` | Composer |
| `--radius-card` | `12px` | 템플릿 카드 |
| `--radius-control` | `999px` | 필터·모드 칩 |
| `--shadow-composer` | `0 6px 30px rgb(0 0 0 / 8%)` | Composer |
| `--duration-fast` | `160ms` | Focus·선택 |
| `--duration-preview` | `300ms` | 카드 Hover |

색상은 Genspark 값을 그대로 복제하지 않고 현재 프로젝트의 Primary 토큰을 사용한다. 구조와 밀도만 참고한다.

## 7. 컴포넌트 설계

### 7.1 권장 파일 구조

```text
servers/nextjs/app/(presentation-generator)/upload/
├─ components/
│  ├─ UploadPage.tsx                  # Shell 조합
│  ├─ CreationHero.tsx                # 제목·모드·Composer
│  ├─ PresentationComposer.tsx        # 입력·첨부·설정·생성
│  ├─ GenerationModeControls.tsx
│  ├─ TemplateCatalog.tsx             # 탭·필터·그리드
│  ├─ CatalogFilters.tsx
│  ├─ CompactTemplateGrid.tsx
│  ├─ CompactTemplateCard.tsx
│  ├─ AddTemplateCard.tsx
│  └─ TemplatePreviewDialog.tsx
├─ hooks/
│  ├─ useUploadPageState.ts            # 기존 생성 흐름 유지
│  └─ useTemplateCatalog.ts            # 목록 결합·필터·정렬
└─ types/
   └─ template-catalog.ts
```

### 7.2 기존 컴포넌트 처리

- `UploadPage.tsx`: 페이지 폭 제한과 상세 설정 펼침 구조를 제거하고 새 Shell을 조합한다.
- `PromptInput.tsx`: `PresentationComposer` 내부의 textarea primitive로 축소하거나 교체한다.
- `TemplateSelection.tsx`: 데이터 조회·선택 로직은 재사용하고, 화면 렌더링은 Catalog로 분리한다.
- `StructuredTemplateCard.tsx`: 상세 선택 화면에서 계속 사용한다. 홈 Catalog에는 별도 `CompactTemplateCard`를 사용해 책임과 밀도를 분리한다.
- `ThemeGallery.tsx`: 상세 설정 또는 필터의 테마 선택 Sheet/Dialog에서 재사용한다.
- `SupportingDoc.tsx`, `ConfigurationSelects`, `CurrentConfig`: Composer의 첨부·설정 요약 흐름에 재배치한다.
- `useUploadPageState.ts`: 생성 요청, 오류, 로딩, 라우팅 로직을 변경하지 않는다.

## 8. 화면 상태와 데이터 모델

기존 Redux 상태는 유지한다.

- `config`
- `files`
- `selectedTemplate`
- `selectedTheme`
- `authoredVisionQa`
- `authoredStyle`

카탈로그 표시 상태는 URL 또는 페이지 로컬 상태로 관리한다.

```ts
type CatalogTab = "explore" | "mine";
type CatalogSort = "default" | "name" | "recent";

type CatalogViewState = {
  tab: CatalogTab;
  styleFilter: string | null;
  themeFilter: string | null;
  sort: CatalogSort;
  previewItemId: string | null;
};
```

통합 카드 모델은 선택 대상이 서로 다른 두 파이프라인을 구분해야 한다.

```ts
type TemplateCatalogItem =
  | {
      kind: "structured";
      id: string;
      title: string;
      thumbnailUrl: string;
      styleTags: string[];
      themeIds: string[];
      source: "builtin" | "custom";
    }
  | {
      kind: "authored";
      id: string;
      title: string;
      thumbnailUrl: string;
      styleTags: string[];
      source: "builtin";
    };
```

선택 동작:

- `structured`: 기존 규칙으로 `selectedTemplate`을 설정한다.
- `authored`: `selectedTemplate = "authored"`, `authoredStyle = item.id`를 함께 설정한다.
- 테마 필터는 목록을 거르는 조건이며, 실제 `selectedTheme` 변경은 명시적인 선택 동작으로 분리한다.
- `내 템플릿 추가` 카드는 `/custom-template`로 이동한다.

스타일·테마 필터를 이름 추론으로 만들지 않는다. 1차에서는 기존 데이터에서 신뢰할 수 있는 항목만 노출하고, 장기적으로 템플릿 summary에 `styleTags`, `themeIds`, `createdAt`, `popularity`를 명시한다.

## 9. 모드 컨트롤 의미

Genspark의 `프로페셔널 / 크리에이티브 / 자동 / 가이드` 문구는 현재 제품 파이프라인과 의미가 다르므로 그대로 사용하지 않는다.

1차 권장 라벨:

- `템플릿 기반`: 현재 structured template 생성
- `AI 저작`: 현재 authored 생성
- `설정`: 기존 상세 설정 모달/패널 열기

`자동 추천`, `가이드 모드`는 추천 규칙과 단계형 입력 UX가 정의된 후 추가한다. 동작하지 않는 컨트롤은 노출하지 않는다. Composer 하단의 `표준` 형태 요약 칩에는 현재 슬라이드 수·언어·톤을 요약한다. 예: `10장 · 한국어`.

## 10. 주요 상호작용

### 10.1 템플릿 카드

- 기본: 썸네일과 짧은 제목만 표시한다.
- Hover/키보드 Focus: 이미지가 최대 1.02배 확대되고 `이 템플릿 사용` 액션이 나타난다.
- 클릭/Enter: 카드를 선택하고 Composer 가까이에 선택 요약을 표시한다.
- 별도 미리보기 버튼: Dialog를 열어 2–4장 대표 슬라이드를 보여준다.
- 선택 상태: 2px Primary outline, 체크 아이콘, `aria-checked=true`를 함께 사용한다.
- 터치: 첫 탭으로 선택한다. Hover 전용 액션에 의존하지 않는다.

### 10.2 탭과 필터

- 탭은 `role=tablist`, 방향키 이동, 활성 underline을 제공한다.
- `탐색`: 기본·공용 템플릿과 AI 저작 스타일을 표시한다.
- `내 템플릿`: 사용자 제작 템플릿과 첫 카드의 `내 템플릿 추가`를 표시한다.
- 필터 선택은 즉시 결과를 갱신하며 현재 결과 수를 스크린리더 live region에 알린다.
- 결과가 없으면 필터 초기화 액션과 템플릿 추가 액션을 제공한다.

### 10.3 생성

- 생성 버튼은 Composer 내부 우측 하단에 둔다.
- 생성 중에는 동일 위치에서 Progress 상태를 표시해 레이아웃 이동을 막는다.
- 템플릿을 바꾸어도 입력 프롬프트와 첨부 파일은 보존한다.
- 유효성 오류는 Toast만 띄우지 않고 Composer 내부 해당 컨트롤 아래에도 표시한다.

## 11. 접근성

- 모든 아이콘 버튼에 보이는 Tooltip과 `aria-label`을 제공한다.
- 버튼의 최소 터치 영역은 모바일 44×44px, 데스크톱 36×36px 이상으로 한다.
- 모드·템플릿 선택은 `radiogroup` 또는 `aria-pressed` 중 하나로 일관되게 구현한다.
- 카드 전체를 무분별하게 중첩 버튼으로 만들지 않는다. 선택 버튼과 미리보기 버튼의 포커스 순서를 분리한다.
- Focus ring은 배경 대비 3:1 이상으로 유지한다.
- `prefers-reduced-motion: reduce`에서는 확대·이동 전환을 제거한다.
- 이미지에는 템플릿 이름 기반 대체 텍스트를 제공하고 장식용 프레임은 빈 alt를 사용한다.
- 색상만으로 선택·오류·활성 상태를 전달하지 않는다.

## 12. 성능 설계

- 초기 카드당 커버 이미지 1개만 로드한다.
- 첫 화면 밖 이미지는 `loading="lazy"`, 적절한 `sizes`와 `srcset`을 사용한다.
- Hover/Focus 시 추가 미리보기는 최대 3장만 요청하고 취소 가능한 지연 요청으로 처리한다.
- 첫 화면 LCP 후보인 상단 첫 템플릿 1–2개만 우선 로드한다.
- 이미지 비율 공간을 먼저 예약해 CLS를 방지한다.
- 60개 이하에서는 CSS Grid를 유지한다. 목록이 커지면 pagination 또는 `IntersectionObserver` 기반 점진 로드를 추가한다.
- 필터 변경 시 서버를 재호출하지 않아도 되는 데이터는 메모이즈된 클라이언트 파생 상태로 처리한다.

## 13. 백엔드/API 영향

### 1차

백엔드 변경 없음. 현재 템플릿 summary와 authored style 데이터를 조합해 표시한다. 제공되지 않는 필터와 정렬은 UI에 노출하지 않는다.

### 후속 선택 기능

다음 필드를 서버 summary에 추가하면 완전한 카탈로그 경험을 제공할 수 있다.

```json
{
  "styleTags": ["professional", "minimal"],
  "themeIds": ["blue-business"],
  "createdAt": "2026-08-12T00:00:00Z",
  "popularity": 0,
  "previewUrls": ["...", "...", "..."]
}
```

- `popularity`가 없으면 `인기순`을 표시하지 않는다.
- 음성 입력은 STT 업로드/스트리밍 계약이 확정된 뒤 추가한다.
- 자동 추천은 프롬프트→템플릿 추천 계약이 확정된 뒤 추가한다.

## 14. 이벤트와 측정

기존 생성 이벤트는 유지하고 다음 이벤트를 추가한다.

- `upload_catalog_tab_changed`
- `upload_catalog_filter_changed`
- `upload_template_preview_opened`
- `upload_template_selected`
- `upload_composer_submitted`

공통 속성:

- `template_kind`
- `template_id`
- `catalog_tab`
- `container_width_bucket`
- `column_count`
- `has_attachments`

`container_width_bucket`은 기기명이 아니라 실제 Catalog 폭 구간을 기록한다.

## 15. 구현 단계

### P0 — 구조 교체

- `UploadPage`의 고정 최대 폭 제거
- 상단 `CreationHero`와 통합 `PresentationComposer` 구성
- 기존 하단 고정 생성 버튼을 Composer 내부로 이동
- 기존 생성·검증·라우팅 로직 연결

완료 조건: 프롬프트, 첨부, 설정, 생성이 기존과 동일하게 동작하고 페이지에 가로 overflow가 없다.

### P1 — 유동 카탈로그

- `TemplateCatalog`, `CompactTemplateGrid`, `CompactTemplateCard` 추가
- 구조화 템플릿과 AI 저작 스타일을 통합 모델로 변환
- Container Query 기반 자동 열 수 적용
- 선택 상태를 기존 Redux 상태에 연결

완료 조건: 실측 컨테이너 폭에서 2/2/3/3/4/5열 결과가 나오며 열 개수 상한이 없다.

### P2 — 탐색 기능

- `탐색 / 내 템플릿` 탭
- 제공 가능한 스타일·테마 필터와 정렬
- `내 템플릿 추가`, 빈 상태, 선택 요약
- 상세 설정을 Dialog/Sheet로 이동

### P3 — 미리보기·성능·접근성

- Hover/Focus 미리보기와 Preview Dialog
- 이미지 지연 로드·반응형 이미지·점진 로드
- 키보드, 스크린리더, reduced motion 검증

### P4 — 데이터 계약이 필요한 기능

- 인기순
- 프롬프트 기반 자동 템플릿 추천
- 단계형 가이드 모드
- 음성 입력

## 16. 검증 계획

### 기능 검증

- 프롬프트 Enter/Shift+Enter/한글 IME
- 파일 첨부·삭제·유효성 오류
- structured/authored 선택 후 생성 요청 값
- 탭·필터·정렬·빈 상태
- 생성 중 중복 요청 방지

### 반응형 검증

Viewport 숫자만 맞추지 말고 DevTools에서 Catalog 컨테이너 실제 폭과 계산된 열 수를 함께 기록한다.

| Catalog 폭 | 기대 결과 |
|---:|---|
| 약 343px | 2열, gap 12px, 가로 overflow 없음 |
| 약 721px | 2열, 카드 약 330px |
| 약 1001px | 3열, 카드 약 312px |
| 약 1135px | 3열, 카드 약 350px |
| 약 1391px | 4열, 카드 약 319px |
| 약 1775px | 5열, 카드 약 329px |

창 크기 변경뿐 아니라 사이드바 표시/숨김, 브라우저 줌 80/100/125%, OS 배율에서도 확인한다.

### 자동화 범위

- `UploadPage.cy.tsx`: Composer 생성 흐름과 기존 회귀
- `TemplateCatalog.cy.tsx`: 탭·필터·선택·빈 상태
- `CompactTemplateGrid.cy.tsx`: 대표 컨테이너 폭의 column 계산 또는 시각 회귀
- 접근성 검사: axe 기반 버튼 이름, tab/radio 의미, Dialog focus trap

## 17. 최종 인수 기준

1. `/upload`의 카탈로그가 페이지 고정 최대 폭에 갇히지 않는다.
2. 열 수는 viewport 이름이 아니라 Catalog 컨테이너 폭에서 자동 계산된다.
3. 약 343/721/1001/1135/1391/1775px에서 각각 2/2/3/3/4/5열을 확인할 수 있다.
4. 1920px보다 넓어져도 5열 상한 없이 적정 카드 폭에 따라 열이 늘어난다.
5. Composer는 760px를 넘지 않으며 좁은 화면에서는 좌우 16px 안쪽에 들어온다.
6. 390px에서 모드·필터가 잘리지 않고 가로 페이지 스크롤이 발생하지 않는다.
7. 템플릿 선택, 첨부, 설정, 프롬프트, 생성 결과는 기존 기능과 동일하다.
8. Hover가 없는 환경에서도 템플릿 선택과 미리보기가 가능하다.
9. 초기 로드에 카드별 다중 미리보기 이미지를 모두 다운로드하지 않는다.
10. 인기순·음성·자동 추천처럼 데이터 계약이 없는 기능은 동작하는 척 노출하지 않는다.

## 18. 구현 시 우선 결정사항

설계 권고안으로 다음을 기본 채택한다.

- 모드 명칭은 `템플릿 기반 / AI 저작 / 설정`을 사용한다.
- 첫 탭은 `탐색`, 정렬 기본값은 서버 제공 순서인 `추천순`으로 표시한다.
- `인기순`은 데이터가 생길 때까지 숨긴다.
- 모바일도 1열이 아니라 2열을 유지한다.
- 카탈로그 전용 Compact card를 새로 만들고 기존 상세 카드를 억지로 축소하지 않는다.
- 1차에는 사이드바를 새로 만들지 않고 기존 Header를 유지한다. 콘텐츠 구조가 안정된 뒤 앱 공통 내비게이션으로 확장한다.
