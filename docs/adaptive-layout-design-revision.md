# Adaptive-Layout 설계 개정판 — R1~R6 (G1·G2·G3·G4 해소)

이 문서는 `adaptive-layout-design.md`를 **대체하지 않고 amend**한다. 리뷰(`adaptive-layout-design-review.md`, 종합 71)가 지적한 load-bearing seam을 코드로 정초해 닫는다. 각 항목은 수정 대상 §를 명시한다. 본 개정으로 목표 점수 mid-80s.

핵심 원칙 변경: **"무변경 재사용(no-change reuse)" 프레이밍을 철회**한다. 아래 4개 지점은 어댑터/분기가 필요한 *명시적 변경*으로 규정한다.

---

## R1 — capacity menu + validator 경로 재설계 (G1, §4·§6.2·§10 amend)

### 문제 (코드 확정)
- `apply_capacity_fit(outline, structure, layout)`(`utils/layout_capacity.py`)는 **markdown outline content**(`compute_content_volume` 정규식: 글자수·불릿수)와 **int structure index** 위에서 동작한다. 컴포저는 typed `blocks[]`(markdown 없음)를 방출 → **drop-in 불가**.
- Phase 1 archetype 스키마는 `z.array(BlockSchema=union)`이고 outer array에 `maxItems`가 없다 → `compute_layout_capacity._walk`가 array를 `n = maxItems||minItems||1 = 1`, items=union(anyOf MAX branch)로 계산 → **모든 archetype이 거의 동일 capacity** → 컴포저 메뉴 무의미.

### 해소 — "선언형 capacity 프로파일 + closed 컴포저 스키마 + 블록레벨 validator"

**(1) archetype별 capacity는 walker가 아니라 단일 config로 선언한다.** 신규 `utils/archetype_profiles.py` (frontend는 `adaptive/archetypes.ts`와 동형 상수):

```python
# 단일 진실원천: archetype -> 허용 블록·개수·필드 maxLength + 선언 capacity
ARCHETYPE_PROFILES = {
  "cover": {"kind":"title", "text_chars":260, "list_items":0,
            "slots":{"eyebrow":{"type":"eyebrow","max":40,"required":False},
                     "title":{"type":"title","max":80,"required":True},
                     "subtitle":{"type":"subtitle","max":140,"required":False}}},
  "one-column-bullets": {"kind":"list", "text_chars":900, "list_items":6,
            "slots":{"title":{"type":"title","max":80,"required":True},
                     "lead":{"type":"text","max":420,"required":False},
                     "bullets":{"type":"bullets","item_max":120,"max_items":6}}},
  "stat-hero": {"kind":"metric", "text_chars":300, "list_items":4,
            "slots":{"title":{"type":"title","max":80,"required":True},
                     "stats":{"type":"stat","max_items":4,
                              "value_max":8,"label_max":28,"delta_max":16,"caption_max":60}}},
  # … 나머지 11개 archetype 동형 (Phase 5에서 채움)
}
```
- **capacity menu** = 이 선언 프로파일(archetype별 distinct). `compute_layout_capacity`(walker)는 **legacy 경로 전용으로 한정**하고 adaptive에는 사용하지 않는다(union-degenerate 문제 회피).

**(2) closed per-archetype 컴포저 출력 스키마**: `get_dynamic_models.py::get_composition_model_with_n_slides(n, profiles)`가 프로파일에서 **닫힌** Pydantic 스키마를 생성한다 — union 배열이 아니라 archetype별 **명명 슬롯 + 동질 typed 배열(maxItems·maxLength 명시)**:
```python
# 예: stat-hero
class StatHeroSpec(BaseModel):
    archetype: Literal["stat-hero"]
    title: constr(max_length=80)
    stats: conlist(StatItem, min_length=1, max_length=4)   # 동질 배열, maxItems 4
class SlideSpecModel(RootModel):  # discriminated union over archetype
    root: Union[CoverSpec, OneColumnBulletsSpec, StatHeroSpec, …]  # discriminator="archetype"
class PresentationComposition(BaseModel):
    slides: conlist(SlideSpecModel, min_length=n, max_length=n)
    # n_slides=None(auto)면 min/max 미적용
```
- 이 스키마는 `prefixItems` 튜플을 쓰지 않는다(§13.1 해소: walker 미사용 + 동질 배열이라 undercount 없음).
- 렌더러 호환: AdaptiveSlide는 현재 `blocks[]`를 읽는다. **얇은 어댑터 `specToBlocks(spec)`**(frontend, 결정론)가 명명 슬롯 → 순서있는 `blocks[]`로 변환(예: cover → [eyebrow?, title, subtitle?])해 `SlideModel.content`로 저장. 즉 **저장·렌더는 `blocks[]` 유지**, **컴포저·capacity는 closed 스키마**. (또는 AdaptiveSlide를 명명 슬롯 직독으로 전환 — Phase 3 결정사항, 어댑터 안이 기본.)

**(3) validator 교체**: adaptive 경로는 `apply_capacity_fit`(markdown)를 **호출하지 않는다**. 대신 신규 `validate_composition(spec, profile)`:
- 블록 개수·필드 길이가 프로파일 bound 내인지 검사(스키마가 1차 강제하므로 대부분 통과).
- 초과 시 (드묾 — closed 스키마의 `maxItems`/`maxLength`가 char/item 초과를 **구조적으로 차단**하므로 1차 발생 안 함): `validate_composition`은 bound 위반을 로깅하고 **컴포저에 1회 재요청**한다. **block-level split 함수는 두지 않는다(YAGNI)** — 분할이 필요한 분량은 *컴포저가 네이티브로* 여러 슬라이드로 나눈다(프롬프트 규칙). (`apply_capacity_fit._split_content`는 markdown-line 기반이라 typed `blocks[]`에 부적용 → adaptive에서 미사용. legacy 경로에서만 유지.)
- **오버플로 책임 분담 명시**: (a) **char/item 초과** = 스키마 bound + `validate_composition`(생성 시). (b) **pixel 초과**(720px) = AdaptiveSlide의 `overflow-hidden` 백스톱 + Phase 5의 JS fit-to-box(clamp). 두 클래스를 분리해 어느 레이어가 막는지 확정.

**검증**: `validate_composition` 단위테스트 + **archetype 프로파일이 서로 distinct함을 assert**하는 골든테스트(capacity menu가 의미있음을 보증). `compute_layout_capacity`는 legacy 골든값(NumberedBullets 730/3 등) 그대로 유지.

---

## R2 — data-block-id 에디터 바인딩 (G2, §5.2·§8.1·§8.2 amend)

### 사실 확정 (코드)
`TiptapTextReplacer.tsx`는 텍스트 leaf(직접 텍스트, 텍스트자식 없음, 길이>2)를 **className+style만 복사한 새 `<div>`로 `replaceChild`** 한다 → **`data-block-id`와 시맨틱 태그 소실**. `<li>`(텍스트자식 보유로 skip)·짧은 stat value(길이<3 skip)만 잔존. 바인딩은 `findDataPath`(문자열 동등 매칭)로 — 중복 텍스트·재정렬·빈 값에 취약.

**export 무영향 확정**: 변환기는 computed style+텍스트로 매핑하고 data-block-id를 읽지 않는다 → **G2는 순수 Phase 4 에디터 이슈**, export 계약은 이미 증명됨. (§7·§12의 export 주장은 G2와 무관.)

### 해소 — 외과적 2점 수정 (모든 템플릿에 무해, adaptive에 결정론 부여)
`TiptapTextReplacer.tsx`:
1. **교체 컨테이너에 anchor 이식**: 새 div 생성 시 원소의 `data-block-id`/`data-path`를 복사:
   ```ts
   // bullets: 편집되는 텍스트 leaf는 <li> 안의 <span>이고 id는 <li>에 있으므로 closest로 올라가 찾는다
   const bid = htmlElement.getAttribute("data-block-id")
             || htmlElement.closest("[data-block-id]")?.getAttribute("data-block-id") || null;
   if (bid) tiptapContainer.setAttribute("data-block-id", bid);
   ```
   (시맨틱 태그는 export가 computed style로 매핑하므로 보존 불필요. 단 heading 시각은 className/style로 보존됨.)
2. **anchor 우선 바인딩**: `data-block-id`가 있으면 `findDataPath` 문자열 매칭을 건너뛰고 **block-id를 바인딩 키로** 사용:
   ```ts
   const bid = htmlElement.getAttribute("data-block-id")
             || htmlElement.closest("[data-block-id]")?.getAttribute("data-block-id") || null;
   const binding = bid ? { kind:"blockId", key: bid } : { kind:"path", key: findDataPath(slideData, trimmedText).path };
   ```
   `onContentChange(content, binding, slideIndex)`. **caller 파급**: `V1ContentRender`의 edit 트리에서 `onContentChange` 시그니처(`path:string` → `binding`)를 함께 갱신(adaptive 분기만; legacy는 `binding.kind==="path"`로 기존과 동일).

**adaptive update reducer** (`store/slices/presentationGeneration`): 신규 `updateAdaptiveBlock({slideIndex, blockId, content})` — `content.blocks`에서 id로 블록(또는 `s1.label`처럼 `blockId.field`, 또는 bullets item id) 탐색해 text 갱신. 결정론(중복 텍스트/재정렬 안전), setTimeout 레이스 무관.

**공존**: group≠"adaptive"이면 기존 `findDataPath` 경로 그대로(무회귀). adaptive면 anchor 경로.

**검증(필수)**: readOnly 렌더 후 `h1/p/span`에서 `data-block-id` **생존을 단언**하는 regression test + 중복 텍스트 2블록 no-misbind 테스트. (이 테스트가 §12에 추가됨.)

---

## R3 — content-first 설계 조정 + prepare↔stream 영속 경계 (G3, §1·§2.3·§5·§6.4 amend)

### 사실 확정 (코드)
`/prepare`(`presentation.py:290-362`)는 `outlines`/`structure`/`layout`만 영속화하고 **SlideModel row를 만들지 않는다**. 슬라이드는 `/stream`(`presentation.py:365-519`) 루프가 `get_slide_content_from_type_and_outline`(LLM)로 생성한다.

### 권위 관계 확정
**`compose_slides`(adaptive)가 content-first-design.md의 Stage B `DeckPlan` planner를 supersede 한다.** 보존/폐기를 명시:
- **신설 (정직 재기재)**: `deck_plan` JSON 컬럼은 **현재 코드에 없다** (`models/sql/presentation.py` 미존재 — content-first-design.md §2.3이 *제안만* 한 미구현 항목). 따라서 adaptive 경로가 이를 **신규 추가**한다: nullable JSON 컬럼 + `get_deck_plan()/set_deck_plan()` accessor + alembic 마이그레이션 (additive·backfill 없음, 기존 `theme` 컬럼 패턴 복제). **타입 결정**: accessor는 content-first의 `DeckPlanModel`이 아니라 **adaptive `PresentationComposition(SlideSpec[])`** 를 반환한다(컬럼엔 그 `model_dump()` dict 저장). content-first DeckPlan은 폐기되므로 타입 충돌 없음.
- **폐기**: content-first의 DeckPlan LLM planner / dual-write 어댑터. (미구현 상태였으므로 제거 비용 0.)
- **유지**: content-first Phase 1의 Stage A `generate_content_brief`(이미 출하). 이게 `compose_slides`의 substance 입력.

### 영속 경계 (정확)
**`/prepare` (adaptive 분기, `ADAPTIVE_COMPOSER` on):**
1. `brief`(Stage A) + layout(group "adaptive") + 프로파일 → `compose_slides` → `PresentationComposition{ slides: SlideSpec[] }`.
2. `presentation.set_deck_plan(composition)` — **SlideSpec[]을 deck_plan 컬럼에 영속화**(authoritative).
3. **projection 동시 기록**(기존 reader·`/stream` 루프 호환): `outlines` = 각 슬라이드의 `specToBlocks`된 `SlideSpec`을 `SlideOutlineModel.content`(JSON 문자열)로; `structure.slides[i]` = 해당 archetype의 group "adaptive" 내 index(`layout.get_slide_layout_index("adaptive:"+archetype)`); `presentation.n_slides = len(slides)`.
4. **SlideModel row는 만들지 않는다** — 슬라이드 생성은 `/stream`에 유지(SSE envelope 보존).

**`/stream` (adaptive 분기):** 루프 `for i, idx in enumerate(structure.slides)`는 그대로. per-slide step만 분기:
```python
if layout_group_of(idx) == "adaptive":
    slide_content = deck_plan.slides[i]          # 영속 SlideSpec 직접 사용 — LLM 호출 없음
else:
    slide_content = await get_slide_content_from_type_and_outline(...)   # 기존 legacy 경로
```
이후 `process_slide_and_fetch_assets`(에셋, R6대로 깊이 무관 동작) → `SlideModel(content=slide_content, layout="adaptive:"+archetype, layout_group="adaptive", index=i, speaker_note=…(R5))` → **기존 SSE envelope 그대로**(opening `{ "slides": [ `, per-slide `slide.model_dump_json()`, `slide_assets`, closing, `complete`). delete+reinsert 보존.

**불변식**: `len(structure.slides)==len(outline.slides)==len(deck_plan.slides)`. → 에디터·export·`PresentationWithSlides` 모두 무변경(슬라이드 테이블 형태 동일). content-first와 adaptive가 **한 컬럼(deck_plan)·한 stream 루프**로 수렴, hand-wave 제거.

---

## R4 — export round-trip 검증 환경 (G4, §11 Phase 6·§12 amend)

### 사실 확정
export 런타임 v0.2.9는 **`convert-linux-x64`만** 출하(`convert-win32.exe` 부재). FastAPI(Windows)는 win32 변환기를 찾으므로 **Windows dev box에서 byte-level PPTX 변환 불가**. (Bash·PowerShell node 모두 win32 — sync는 항상 Linux zip을 받음.)

### 해소 — 검증 레인 명시 + 게이트
- **byte-level round-trip(§12 #1 검증)은 Linux/Docker/CI 레인에서만 실행**: `docker compose up`(Linux 컨테이너 → convert-linux-x64 동작) 또는 CI에서 `SlideSpec→/pdf-maker→PPTX→python-pptx`로 archetype별 **도형 수·텍스트 assert**. CI 잡으로 고정.
- **로컬 Windows의 headless `/pdf-maker` DOM-contract assert는 "proxy"로 명시 라벨**(이미 증명: scaffold·1280×720·real text leaf·canvas 0) — shape-count 증명이 아님.
- **Phase 6 게이트**: `ADAPTIVE_COMPOSER` 기본 on 전, **CI 레인에서 green round-trip 필수**(blocking gate). 
- **패키징 비고**: Electron 빌드(`npm run build:all`)는 PyInstaller로 **Windows 변환기를 로컬 생성** → 패키지 데스크톱은 win32 export 동작. 즉 환경 공백은 dev-only.
- §13.6(DOM-contract 공동버전: `data-block-id` 등 신규 attribute가 v0.2.9 converter에 무해한지)도 이 **CI round-trip의 도형 수 동일성**으로 함께 확정.

---

## R5 — speaker_note provenance (G5, §6.2·§2.2 amend)
`generate_slide_content`(`__speaker_note__` 주입) 제거 시 누락되던 speaker_note를 **컴포저 출력에 1급 필드로** 추가:
- `SlideSpecModel`에 `speaker_note: constr(max_length=500) = ""` 추가(블록 아님, 슬라이드 메타).
- `/stream` adaptive 분기에서 `SlideModel.speaker_note = spec.speaker_note`로 매핑 → 기존 `.main-slide[data-speaker-note]` export 계약 보존.

## R6 — asset 깊이 무관 재귀 (G7 확정, §13.10 close)
`utils/dict_utils.py::get_dict_paths_with_key`는 **dict와 list를 완전 재귀**한다 → `process_slide_and_fetch_assets`가 중첩 `blocks[]`·`columns[].blocks[]` 내 `__image_url__`/`__icon_url__`를 **이미 깊이 무관하게 탐색**. **추가 코드 불요**. → 중첩 `ColumnsBlock`에 image/icon 마커를 넣은 **테스트 1개만** 추가해 회귀 방지(§12).

---

## R7 — brand-slot source of truth (G9, §3.3 amend)
빌드된 `AdaptiveSlide.tsx`는 `V1ContentRender`가 주입하는 **`_logo_url__`(밑줄 1개)·`__companyName__`** 키를 읽으며, 이는 `V1ContentRender.tsx:116-117`의 주입 키 및 legacy 템플릿(`BasicInfoSlideLayout` 등)과 **일치**한다(기존 코드 컨벤션이 `_logo_url__` 단일밑줄). 따라서 **source of truth = 주입 키**로 확정하고, design §3.3의 `data.brand`를 이 주입 키로 **정정**한다. AdaptiveSlide는 이미 올바르므로 **코드 변경 불요**(typo 아님 — 기존 컨벤션 준수). 테마의 `logo_url`/`company_name`이 V1ContentRender에서 이 키로 주입되는 경로 그대로 유지.

## Phase 영향 갱신
- **Phase 3**: closed per-archetype 스키마 + 선언 프로파일(R1) + `compose_slides` + deck_plan 영속 + /prepare·/stream 분기(R3) + speaker_note(R5). `apply_capacity_fit`는 legacy 전용으로 격리.
- **Phase 4**: TiptapTextReplacer anchor 수정(R2) + `updateAdaptiveBlock` reducer + regression test.
- **Phase 5**: archetype 11종 + 각 ARCHETYPE_PROFILES 항목 + JS fit-to-box(pixel 오버플로 책임).
- **Phase 6**: CI round-trip green 게이트(R4) 후 flag on.
- **리스크 등급 재평가(§11)**: composer 통합·JS fit 엔진은 '중'이 아니라 '상'으로 표기.

## 점수 영향 (목표)
G1(+4~5)·G2(+3~4)·G3(+3)·G4(+2)·G5/G7(+1~2) 해소 → 71 → **mid-80s**. 잔여 minor(G6 block-CRUD UX 상세, G8 composer 안정성 metric, chart/table fidelity)는 Phase 4·5 진입 시 상세화.
