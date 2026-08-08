# 1. 아키텍처 개요 + 데이터 흐름

## 1.1 핵심 설계 동인: editable-PPTX DOM 계약(v0.2.9)

export 런타임은 슬라이드 JSON을 절대 받지 않습니다. `/pdf-maker?id=<uuid>`를 headless Chromium으로 렌더한 **최종 DOM만** 읽어 PPTX 도형으로 매핑합니다. 따라서 **렌더된 HTML/DOM이 전체 계약**이며, 신규 SlideSpec/렌더러가 무엇을 하든 다음 불변식은 깨질 수 없습니다(EXPORT findings의 `constraintsForNewRenderer` 1~7 그대로).

- 라우트 `/pdf-maker?id=<uuid>` 유지.
- DOM 골격 유지: `#presentation-slides-wrapper`(테마 CSS 변수 host) > `.slides-export-stack` > 슬라이드당 1개 `.main-slide`(정확히 1280×720, `data-speaker-note`, PDF page-break 경계) > `.slide-export-inner`(`data-layout`, `data-group`).
- 편집 요소 1개 = 식별 가능한 DOM leaf 1개(`<h1>/<p>/<span>/<li>`→textbox, `<img src>`→picture, inline `<svg>`→icon, `<table>`→table, Recharts `<svg>`→chart). `::before/::after`, background-image 텍스트, canvas 금지.
- export(isEditMode=false) 시 인터랙티브 레이어(Tiptap/EditableLayoutWrapper/hover/setTimeout) 없이 SlideSpec만으로 결정론적 페인트. 테마는 동일한 CSS 변수 hook, 폰트는 `useFontLoader`로 settle.
- transform scale 금지(geometry 왜곡), 1280×720 밖 배치 금지, OOXML 비호환 효과(복합 gradient/filter/clip-path/3D) 금지.

이 7개 제약이 2~7장의 모든 결정을 지배합니다.

## 1.2 데이터 흐름 (브리프 → 컴포저 → SlideSpec → 적응형 렌더러 → 에디터/export)

```
[Stage A] generate_content_brief() → ContentBrief        (그대로 유지, 더 중심적)
   ↓ ContentBrief.to_prompt_context()
[Stage B] generate_ppt_outline() → PresentationOutlineModel   (그대로 유지: web_search 그라운딩 + n_slides 계약)
   ↓ outline markdown + brief + archetype capacity menu
[Stage C ★신규] compose_slides()  →  PresentationComposition { slides: SlideSpec[] }
   (generate_presentation_structure + get_slide_content_from_type_and_outline 를 융합 대체)
   ↓ 검증: apply_capacity_fit()를 "validator"로 재사용 (생성→검증 분리 유지)
[영속화] 슬라이드별로:
   SlideModel.content      = SlideSpec(JSON dict)
   SlideModel.layout       = "adaptive:<archetype>"
   SlideModel.layout_group = "adaptive"
   PresentationModel.structure.slides[i] = archetype의 group 내 index
   ↓ process_slide_and_fetch_assets() (이미지/아이콘 마커 기반, 그대로)
[렌더] /pdf-maker 또는 /presentation?id  →  V1ContentRender → AdaptiveSlide(단일 렌더러)
   content.blocks[] → 아키타입별 영역 배치 → DOM leaf(+data-block-id) → Tailwind(theme token)
[에디터] 동일 AdaptiveSlide + BlockBindingWrapper(data-block-id 결정론적 바인딩) + 블록 CRUD
[export] /pdf-maker → 동일 DOM → presentation-export(v0.2.9) → editable PPTX / print-to-PDF
```

핵심: **생성 단계만 1+1을 1로 융합**하고, **렌더/영속/테마/asset/export 골격은 손대지 않습니다**. SlideModel 컬럼(`content/layout/layout_group/index/speaker_note/properties`)은 형태 그대로 유지됩니다(`slide.py:7-20`).

---

# 2. SlideSpec 데이터 모델

## 2.1 설계 원칙

1. **블록은 1차 시민**이며 각 블록(및 복합 블록 내 atomic 필드)은 **렌더러가 단일 DOM leaf로 방출**한다(export 계약 6번). 저장 형태가 아니라 렌더러가 1:1 매핑을 보장한다.
2. **기존 마커 규약 재사용**: 이미지=`{__image_url__, __image_prompt__}`, 아이콘=`{__icon_url__, __icon_query__}`. 그래야 `process_slide_and_fetch_assets`, `ImageEditor`, `IconsEditor`가 무변경으로 동작한다.
3. **capacity 호환**: 아키타입 json_schema가 블록 배열을 `maxLength`/`maxItems`로 기술하고, chart 블록은 chartType enum + 배열 형제, table 블록은 `rows`+`headers/columns` 규약을 따른다 → `compute_layout_capacity._walk`(layout_capacity.py:77-164)가 **무변경으로** text_chars/list_items/has_image/has_chart/has_table/kind를 산출한다.
4. **안정 식별자**: 모든 블록·항목에 `id`(string). 이것이 에디터 바인딩의 단일 근거(EDITOR findings가 지목한 "가장 큰 변경").

## 2.2 정규 모델 (frontend Zod / backend Pydantic 양쪽 동형)

```ts
// servers/nextjs/app/presentation-templates/adaptive/spec.ts  (Zod, 단일 소스)
type ArchetypeId =
  | "cover" | "section-divider" | "agenda" | "big-statement"
  | "stat-hero" | "one-column-bullets" | "two-column" | "card-grid"
  | "comparison" | "timeline" | "image-led" | "chart-insight"
  | "table" | "closing";

type BlockType =
  | "title" | "subtitle" | "eyebrow" | "text"
  | "bullets" | "stat" | "image" | "icon"
  | "quote" | "chart" | "table" | "columns" | "callout" | "divider";

interface BaseBlock { id: string; type: BlockType; }

interface TitleBlock    extends BaseBlock { type:"title";    text: string; }   // .max(80)
interface SubtitleBlock extends BaseBlock { type:"subtitle"; text: string; }   // .max(140)
interface EyebrowBlock  extends BaseBlock { type:"eyebrow";  text: string; }   // .max(40) (kicker/section no.)
interface TextBlock     extends BaseBlock { type:"text";     text: string; }   // markdown, .max(420)
interface BulletItem    { id: string; text: string; icon?: IconRef; }
interface BulletsBlock  extends BaseBlock { type:"bullets"; items: BulletItem[]; } // items.max(6), text.max(120)
interface StatBlock     extends BaseBlock { type:"stat";
  value: string; label: string; delta?: string; caption?: string; } // value.max(8), label.max(28)
interface ImageRef { __image_url__: string; __image_prompt__: string; alt?: string; }
interface ImageBlock    extends BaseBlock { type:"image"; image: ImageRef; }
interface IconRef  { __icon_url__: string; __icon_query__: string; }
interface IconBlock     extends BaseBlock { type:"icon"; icon: IconRef; }
interface QuoteBlock    extends BaseBlock { type:"quote"; text: string; attribution?: string; } // text.max(220)
interface ChartBlock    extends BaseBlock { type:"chart";
  chartType: "bar"|"line"|"area"|"pie"|"donut"|"radar"|"scatter";   // ← capacity의 CHART_TYPE_VOCAB 매칭
  series: { name: string; points: { x: string; y: number }[] }[];
  xLabel?: string; yLabel?: string; }
interface TableBlock    extends BaseBlock { type:"table";
  headers: string[]; rows: string[][]; }                            // ← _is_table_container 매칭
interface ColumnsBlock  extends BaseBlock { type:"columns";
  columns: { id: string; blocks: Block[] }[]; }                     // 2~3열 중첩 (two-column/comparison)
interface CalloutBlock  extends BaseBlock { type:"callout"; text: string; icon?: IconRef; tone?: "info"|"success"|"warning"|"danger"; }

interface SlideSpec {
  archetype: ArchetypeId;
  variant?: string;          // 밀도/배치 힌트 (예: "image-left", "2col", "3up") — 렌더러가 해석
  blocks: Block[];           // 순서 있는 typed 블록
  // speaker_note 는 SlideModel.speaker_note 컬럼으로 분리 저장 (현행 유지)
}
```

backend는 `models/slide_spec_model.py`에 동일 Pydantic 모델을 두고, `get_*_model_with_n_slides` 팩토리 패턴(`get_dynamic_models.py`)을 차용해 **선택 아키타입별 허용 블록·min/max·n_slides를 동적으로 강제**한다.

## 2.3 영속화와의 관계 (무변경 저장)

- `SlideModel.content` = `SlideSpec` dict 그대로. 자유형 JSON 컬럼이므로 마이그레이션 불필요(`slide.py:17`).
- `SlideModel.layout = "adaptive:" + archetype`, `layout_group = "adaptive"`. `getLayoutByLayoutId`가 group "adaptive"를 단일 `AdaptiveSlide` 컴포넌트로 resolve(5장).
- `PresentationModel.structure`(`List[int]`)는 그대로 유지: archetype id → "adaptive" group 내 index로 매핑해 채운다(`PresentationStructureModel`는 무변경, `presentation_structure_model.py`).
- `properties`(이미지 object-fit/focus) 컬럼·규약 그대로. ImageBlock의 itemIndex 매핑만 data-block-id 기준으로 정렬.
- **블록은 저장상 "named-field가 아니라 blocks[] 배열"이지만**, capacity walker가 `blocks` 배열을 객체 배열로 순회하며 maxLength/maxItems/마커를 그대로 집계하므로(2.1-3) 기존 capacity 파이프라인이 무변경으로 동작한다.

---

# 3. Theme(tone & manner) 스펙 — 유일한 스타일 캐리어

THEME findings의 `toneAndMannerPlan`을 확정안으로 채택한다. `presentation.theme`는 schemaless JSON 컬럼(`presentation.py:44`)이므로 **DB 마이그레이션 없이 additive**. `data.version`과 `normalizeTheme()`로 v1(16색+1폰트)에서 v2 토큰을 파생한다(하위호환).

## 3.1 v2 `data` 모델 (역할 기반)

```
data: {
  version: 2,
  meta:        { mode: "light"|"dark", style: "corporate"|"editorial"|"playful"|"technical"|"minimal" },
  colors:      { primary, on_primary(=primary_text), secondary, accent,
                 background, on_background(=background_text),
                 surface(=card), surface_variant, on_surface, muted, on_muted,
                 border(=stroke), success, warning, danger, info, graph_0..graph_9 },  // 기존 키 alias 유지
  typography:  { headingFont{name,url}, bodyFont{name,url}, monoFont{name,url},
                 scale{ display,h1,h2,h3,h4,body,small,caption },   // rem/role
                 weight{ heading, body, emphasis },
                 lineHeight{ heading, body }, letterSpacing{ heading, body }, case{ heading:"none"|"upper" } },
  spacing:     { unit:4, density:"comfortable"|"compact"|"spacious",
                 slidePaddingX, slidePaddingY, sectionGap, blockGap, inlineGap },
  shape:       { radius{ sm,md,lg,pill }, borderWidth, shadow{ sm,md,lg }, style:"soft"|"hard"|"flat" },
  motif:       { style:"wave"|"geometric"|"dots"|"line"|"none", intensity:0..1,
                 color:"<role-ref e.g. accent>", opacity, placement:"corner"|"edge"|"full" },
  brand:       { companyName, logoUrl, logoId, logoPlacement, watermark:bool },
  fonts:       { textFont }   // LEGACY alias, normalizer가 typography로 매핑
}
```

## 3.2 CSS 변수 네임스페이스 (오늘의 16개의 superset)

- color roles: `--secondary-color, --accent-color, --surface-variant, --on-surface, --muted-color, --on-muted, --border-color`(+ `--stroke` alias로 `--stroke` vs `--*-color` 불일치 해소), `--success/--warning/--danger/--info`.
- typography: `--heading-font-family, --body-font-family, --mono-font-family, --fs-display/-h1/-h2/-h3/-h4/-body/-small/-caption, --fw-heading/-body/-emphasis, --lh-heading/-body, --ls-heading`.
- spacing: `--space-unit, --slide-pad-x, --slide-pad-y, --section-gap, --block-gap, --inline-gap`.
- shape: `--radius-sm/-md/-lg/-pill, --border-width, --shadow-sm/-md/-lg`.
- motif: `--motif-color, --motif-opacity` + wrapper에 `data-motif="<style>" / data-density`.

이 변수들은 export 계약 4번이 요구하는 동일 host(`#presentation-slides-wrapper`)에 인라인으로 set되어야 PPTX 텍스트/색이 computed style로 산출된다.

## 3.3 통합/소비

- `applyPresentationThemeDom.ts`, `ThemePanel.applyTheme`, `PdfMakerPage.applyTheme`, `PresentationCard`의 **4중 중복을 단일 `applyPresentationTheme(element, theme)`로 통합**, 내부에서 `normalizeTheme(data)` 실행 후 모든 변수 set. (export dup #3 `PdfMakerPage.tsx:176`도 동일 함수 사용 → 렌더/에디터/export가 동일 토큰.)
- **Tailwind를 토큰에 바인딩**: `tailwind.config`의 `fontSize/fontFamily/borderRadius/boxShadow/spacing/colors`를 `var(--...)`에 매핑(`rounded-lg`→`var(--radius-lg)`, `text-h1`→`var(--fs-h1)`, `p-section`→`var(--section-gap)`). 단일 `AdaptiveSlide`만 token class를 쓰면 221개 레거시 파일은 손대지 않아도 된다.
- 장식은 per-template SVG 대신 **단일 `<Motif>` 컴포넌트**가 `--motif-*`/`data-motif`로 wave/geometric/dots/none을 role color로 렌더(테마와 함께 recolor). 단 export 계약 5번상 motif는 **장식·저강도·콘텐츠 뒤**에 두어 flatten되어도 무해하게 한다.
- 로고/회사명은 `_logo_url__`/`__companyName__` 매직키 대신 `data.brand`로 구동되는 공유 header/footer 슬롯에서 렌더.

## 3.4 backend/generation

- `models/theme_data.py`(현재 flat 16색)와 stored nested `data`의 분기를 **단일 nested ThemeData(colors+typography+spacing+shape+motif+brand, versioned)로 통일**, `/v3/theme/generate` 응답을 stored shape에 정렬. OKLCH 색 수학(`theme_utils.py`)은 유지하고, `style` preset table이 typography scale/density/radius/shadow/motif/폰트 페어링을 동반 산출(색은 대비 보장을 위해 알고리즘 유지). 이는 추가적이고 기존 구조 확장.

---

# 4. Archetype 라이브러리

ARCHETYPE findings의 `recommendedArchetypes`를 ~14개로 확정한다. **각 아키타입 = (a) `AdaptiveSlide` 내부의 배치 규칙 1개 + (b) blocks 구성·capacity를 기술하는 Zod 스키마 1개**. 픽셀 레이아웃이 아니라 "어떤 블록을 어떤 영역에 어떤 밀도로" 규칙이다 → "fresh 컴포지션"은 (아키타입 선택)+(밀도 variant)+(블록 optional/count)에서 나온다.

| # | archetype id | 언제 | block 구성 (필수/선택) | 밀도 variant |
|---|---|---|---|---|
|1|`cover`|덱 오프닝|title, subtitle?, eyebrow?, image?(full-bleed), brand(slot)|centered / left / image-right|
|2|`section-divider`|섹션 전환(3~5장마다)|eyebrow(섹션번호), title, image?/color-field|number-left / centered|
|3|`agenda`|커버 직후|title, bullets(3~7, icon?)|1col / 2col(by count)|
|4|`big-statement`|리듬 전환·핵심 메시지|quote(text, attribution?) 또는 title(대형)|full-width|
|5|`stat-hero`|콘텐츠가 본질적으로 수치|stat × 1~4 (value/label/delta/caption)|1 huge / 2up / 2×2|
|6|`one-column-bullets`|단일 아이디어+3~6 근거|title, text?(lead), bullets|tight / lead+list|
|7|`two-column`|주장+근거, 서사+시각|columns[2] = {text|text, text|image, text|chart}|image-left/right|
|8|`card-grid`|동등 가중 병렬 항목|title, columns/cards × 3/4/6/8 (icon+title+text)|count≤4→count열, else 4열|
|9|`comparison`|vs/before-after/plan|title, columns[2~3] = {header + bullets/check rows}|2col / 3col|
|10|`timeline`|순서 단계 3~6|title, items[](eyebrow=step, title, text)|horizontal / vertical|
|11|`image-led`|임팩트·시각 휴지|image(dominant), title?(overlay), caption?|full-bleed / split|
|12|`chart-insight`|서사 있는 데이터|chart, title, bullets(1~3 takeaway)|chart-left / chart-top|
|13|`table`|진짜 표 데이터|title, table(headers+rows)|compact / wide|
|14|`closing`|감사·연락·next steps|title, bullets?(CTA), brand|centered / contact|

## 4.1 다양성(인접 슬라이드 상이) 규칙 — 컴포저가 1차 시민으로 강제

- 본문 아키타입은 덱 전체에서 **3~5종**만 사용하되 **연속 3회 동일 금지**, 그리고 **직전 슬라이드와 동일 archetype 금지**(adjacent-duplicate penalty).
- **3~5 콘텐츠 슬라이드마다 `section-divider` 또는 `big-statement`/`stat-hero`를 breather로 삽입**.
- 시각 가중 교대: dense(`table`/`card-grid` 8up) 다음엔 light(`image-led`/`big-statement`).
- **kind 우선 매칭 후 volume 매칭**: metrics→`stat-hero`, steps→`timeline`, "X vs Y"→`comparison`, 동등 항목→`card-grid`, 서사 데이터→`chart-insight`, tabular→`table`. capacity로 upgrade/split.
- 테마 토큰은 모든 아키타입에서 불변 → 구조 다양성이 일관성을 해치지 않음.

---

# 5. 단일 적응형 렌더러

## 5.1 등록·디스패치

`app/presentation-templates/adaptive/AdaptiveSlide.tsx` 1개 컴포넌트 + `index.tsx`에 group `"adaptive"` 등록. `getLayoutByLayoutId("adaptive:<archetype>", "adaptive")` → 항상 `AdaptiveSlide` 반환. `V1ContentRender.tsx`의 built-in/custom 분기에 **"adaptive" 분기**를 추가(레거시 group은 기존 컴포넌트 그대로 → 9장 공존). `data-layout=archetype`, `data-group="adaptive"`는 기존 메커니즘(`slide.layout/layout_group`)으로 자동 셋되므로 export 골격 유지.

기존 N개 고정 TSX 템플릿을 "교체"한다는 의미: **새 덱은 group "adaptive" 한 종류만 생성**하므로 렌더 경로가 단일 컴포넌트로 수렴한다. 레거시 group의 TSX들은 삭제하지 않고 과거 덱·커스텀 템플릿용으로 잔존(코드 경로 분리 유지).

## 5.2 내부 구조 (export 계약 안전)

```
AdaptiveSlide({ data /* SlideSpec */, isEditMode })
  <div class="adaptive-root w-[1280px] h-[720px] overflow-hidden relative
              p-[var(--slide-pad-y)_var(--slide-pad-x)]">   // 1280×720 하드 backstop
    <Motif/>                                                  // 장식, 콘텐츠 뒤, 저강도
    <BrandSlot brand=.../>                                    // 로고/회사명 (data.brand)
    <ArchetypeLayout archetype variant>                       // 헤더밴드(고정) + 본문(flex-1 min-h-0)
       blocks.map(b => <BlockRenderer key={b.id} block={b} editMode/>)
```

- **`BlockRenderer`**: type별로 **정확히 하나의 의미론적 leaf(또는 복합 블록은 항목별 leaf)**를 방출하고 `data-block-id={b.id}`(중첩 필드는 `data-path`)를 부여 → export DOM-walk와 에디터 바인딩이 동일 anchor 공유. title→`<h1>`, text→`<p>`(MarkdownInlineText), bullets→`<ul><li data-block-id=item.id>`, stat→`<div>`내 `value/label/delta` 각각 별 leaf, image→`<img src=absolute>`, icon→inline `<svg data-path>`, quote→`<blockquote><p>`+`<cite>`, chart→Recharts `<svg>`, table→`<table>`, columns→positional wrapper(배경 카드는 `bg-[var(--surface)] border rounded-[var(--radius-md)]` div = auto-shape).
- **오버플로 4층 방어(ARCHETYPE `fitTechniques`)**:
  1) 생성 제약: 모든 string `.max()`, array `.maxItems` (컴포저 계약 = capacity 입력).
  2) capacity 매칭/upgrade·split: `apply_capacity_fit` validator(6·10장).
  3) intra-slide 적응: 헤더밴드 고정 + 본문 `flex-1 min-h-0`; card/KPI/comparison은 공유 helper로 `count→열수`(≤4→count, else 4) 또는 `grid-template-columns: repeat(auto-fit, minmax(220px,1fr))`.
  4) 텍스트 auto-fit: `font-size: clamp(MIN, Ncqw, MAX)` + `container-type:size` wrapper; headline/hero/quote는 **JS fit-to-box**(scrollHeight/Width 측정 후 font-size 이진탐색 축소, `useLayoutEffect` 동기 실행, **transform 금지** — export scraper의 bounding box 보존), 최후엔 `-webkit-line-clamp` + `text-wrap:balance` + `overflow-wrap:anywhere`.
- **결정론적 settle**: JS fit은 `useLayoutEffect`에서 동기 수행하고 `clamp()`를 fallback으로 병행 → 외부 export 런타임이 setTimeout 없이도 폰트 settle 시점에 올바른 크기를 읽도록 함(현행 250/1000/400ms 레이스 제거; 이는 v0.2.9 converter의 자체 대기창 안에서 끝나야 하므로 fit은 cheap·동기여야 함 — 위험은 13장).
- **PPTX-translatable styling만**: solid/simple fill, plain border, 표준 폰트, 단순 위치; gradient/filter/clip-path/회전 금지.

---

# 6. AI 슬라이드 컴포저 (생성 단계)

GENERATION findings의 `composerSlot`을 확정한다. **stage 3(`generate_presentation_structure`) + stage 5(`get_slide_content_from_type_and_outline`)를 단일 content-first 호출로 융합**한다. brief+outline(web 그라운딩·n_slides 계약)은 유지.

## 6.1 신규 모듈

`servers/fastapi/utils/llm_calls/compose_slides.py` — 기존 `get_system_prompt/get_user_prompt/get_messages/async def generate_*` 형태를 미러링. `get_client(config=get_llm_config())`, `get_model()`, `JSONSchemaResponse(strict=False)` + `generate_structured_with_schema_retries`, `prepare_schema_for_validation`, `handle_llm_client_exceptions` 전부 그대로 재사용. 모델은 `codex/gpt-5.5`(`llm_provider`의 codex 분기).

## 6.2 입력 / 출력

- 입력: `ContentBrief.to_prompt_context()`(주 substance) + outline markdown(슬라이드별, web 그라운딩 보존) + **archetype capacity menu**(`compute_layout_capacity(s.json_schema)`로 group "adaptive"의 각 아키타입에서 산출: kind/text_chars/list_items/has_image/chart/table) + tone/verbosity/instructions.
- 출력 동적 모델:

```python
# get_composition_model_with_n_slides(n, allowed_archetypes, caps) 팩토리 (get_dynamic_models 패턴)
class SlideSpecModel(BaseModel):
    archetype: ArchetypeEnum          # 닫힌 enum → out-of-range index 불가(클램프 루프 삭제)
    variant: Optional[str]
    blocks: conlist(BlockUnion, ...)  # 선택 archetype별 허용 블록·min/max 동적 강제
class PresentationComposition(BaseModel):
    slides: conlist(SlideSpecModel, min_length=n, max_length=n)  # n_slides 강제
```

## 6.3 프롬프트 스케치 (요지)

- system: "당신은 NotebookLM/Gamma류 슬라이드 컴포저다. 각 슬라이드에 대해 (1) 콘텐츠 kind/volume에 맞는 archetype을 capacity menu에서 선택, (2) 해당 archetype의 typed blocks를 brief/outline substance로 채우되 모든 maxLength/maxItems 준수, (3) **연속 동일 archetype 금지·직전과 다름·3~5장마다 breather**, (4) 콘텐츠가 capacity를 넘으면 더 큰 same-kind archetype 선택 또는 슬라이드 분할(네이티브로 슬라이드 수 조절, 단 총 n_slides 계약 준수)."
- TOC/divider는 enum의 `agenda`/`section-divider`로 네이티브 방출(현 `_insert_toc_layouts` 인덱스 저글링 제거).

## 6.4 통합 지점 (`endpoints/presentation.py`)

- `/prepare`: structure 블록(L312-321)+`apply_capacity_fit`(L354-356) → **compose_slides 1콜**로 대체, 결과 SlideSpec 영속화. (Stage A brief도 `/prepare`에 배선 — 현재 one-shot handler에만 존재.)
- `/stream`(L415-463): 영속된 SlideSpec을 그대로 렌더 + `process_slide_and_fetch_assets`만 호출(content-fill 콜 제거).
- `generate_presentation_handler`: structure+capacity(L827-876)+batched fill(L907-969) → **compose_slides 1패스 + asset fetch**.
- ordered-template 분기는 identity로 잔존. `apply_capacity_fit`는 **삭제하지 않고 validator/safety-net**으로 유지(생성 후 emitted blocks가 archetype capacity에 맞는지 검사, 미스 시 upgrade/split — 10장).
- HARD: archetype은 반드시 group "adaptive"의 `SlideLayoutModel.id`로 resolve되고 blocks는 그 json_schema로 serialize → 렌더러/asset/persistence 무변경 동작.

---

# 7. PPTX/PDF EDITABLE EXPORT 매핑 (핵심)

export 메커니즘은 **신규 불필요**(measure-then-map은 v0.2.9 converter가 이미 수행). 신규 렌더러가 만족해야 할 **export 계약**은 "각 block을 converter가 도형으로 추출할 수 있는 깨끗한 leaf로 방출"하는 것뿐이다.

## 7.1 block → PowerPoint 도형 매핑표

| block / 필드 | DOM leaf | PPTX 도형 | 비고 |
|---|---|---|---|
|title/subtitle/eyebrow/text|`<h1>/<h2>/<p>` 단일 연속 텍스트|text box (a:txBody, computed style→a:rPr runs)|필드 1개=leaf 1개|
|bullets.items[i]|`<li data-block-id>`|list region(멀티 paragraph) 또는 item별 textbox|항목별 독립 편집|
|stat.value/label/delta/caption|각각 `<div>/<span>`|개별 text box × 4|label+value 절대 병합 금지|
|image|`<img src=absolute>`|picture|`normalizeBackendAssetUrls` 절대 URL 필수|
|icon|inline `<svg [data-path]>`|icon shape (vector)|background-image 금지|
|quote.text/attribution|`<blockquote><p>`,`<cite>`|text box × 2| |
|chart|Recharts `<svg>`|native chart 또는 grouped vector|**canvas 금지**|
|table|`<table><tr><td>`|pptx table|진짜 표만|
|columns / card 배경|`<div bg/border/radius>`|rounded-rect auto-shape (vector, recolorable)|background-image 대신 CSS fill|
|motif|inline `<svg>` 저강도|decorative shape(또는 flatten)|콘텐츠 비간섭|

## 7.2 신규 렌더러/스펙이 만족해야 할 export 계약 (검증 대상)

1. 라우트/골격 불변: `/pdf-maker?id`, `#presentation-slides-wrapper > .slides-export-stack > .main-slide[data-speaker-note](1280×720) > .slide-export-inner[data-layout][data-group]`. PDF는 `PdfMakerPage` `PDF_PRINT_STYLE`(@page 1280×720, `.main-slide` page-break) 보존.
2. 1 editable element = 1 discrete leaf, computed CSS로 font/size/weight/color/align/line-height 해석 가능. 복합을 단일 painted container로 collapse 금지.
3. 텍스트는 실제 텍스트(::before/::after·SVG-text·이미지 baked 금지), 이미지는 `<img>`, 아이콘은 inline `<svg>`, 표는 `<table>`, 차트는 SVG.
4. transform scale 금지(font-size auto-fit만), 1280×720 밖 배치 금지, OOXML 비호환 효과 금지.
5. data-block-id/data-path는 **추가 속성**일 뿐 converter가 무시하는지 확인(13장). 어떤 DOM-contract 변경도 v0.2.9에 대해 검증·공동 버전 관리.

---

# 8. 에디터 변경

EDITOR findings: 렌더 프레임/레이아웃 레지스트리/테마/redux+autosave/이미지·아이콘 picker/new-slide picker 등 **~70%는 무변경 재사용**. 변경은 "어떻게 `content`를 주소지정하고 편집하는가"에 집중.

## 8.1 재사용 (그대로)

`PresentationRender.tsx`(SlideScale), `SlideErrorBoundary.tsx`, present mode, `getLayoutByLayoutId`/`index.tsx`, `applyPresentationThemeDom.ts`, slice의 `updateSlideContent/Image/Icon/Properties`(setNestedValue)·`undoRedoSlice`·`useAutoSave`·`usePresentationData`, `ImageEditor.tsx`/`IconsEditor.tsx`, `TiptapText.tsx`(rich-text 위젯), `NewSlide.tsx`.

## 8.2 신규/재작업

1. **결정론적 바인딩**: 현 `TiptapTextReplacer.findDataPath`(문자열 동등 매칭)·`EditableLayoutWrapper`(URL/파일명+DOM 위치 휴리스틱)을 **`data-block-id`/`data-path` 기반 바인딩으로 교체**. 신규 `BlockBindingWrapper`가 group "adaptive"일 때 동작: leaf의 `data-block-id`를 직접 slice path로 사용 → 중복 문자열·빈 값·재정렬에도 안전, setTimeout 레이스 제거(동기 마운트). `EditableLayoutWrapper`의 이미지/아이콘 탐지 로직은 재사용하되 URL 대신 `data-path`로 키.
2. **블록 CRUD**: slice에 array splice/reorder reducer 추가(블록·항목 add/delete/reorder), archetype의 Zod min/max로 제약(예: stat 1..4, bullets ..6). 캔버스 affordance(+/삭제/드래그) 추가.
3. **schema-driven 블록 속성 패널**: 기존 `getSchemaDefaults/z.toJSONSchema`(현재 server·custom-template SchemaEditor만 사용)를 슬라이드 에디터에 도입해 per-template 폼 대신 **제네릭 블록 패널** 구동.
4. **per-template 폼 불필요**: 생산 에디터는 원래 스키마 폼이 아니라 inline DOM 편집이었음 → 변경은 "휴리스틱 inline → block-id inline + 블록 ops"로 국한. 텍스트 편집은 `TiptapText` 그대로, 단 onBlur dispatch가 data-block-id로 결정론적.

## 8.3 공존

group이 "adaptive"가 아니면(레거시·custom) 기존 `TiptapTextReplacer`+`EditableLayoutWrapper` 경로 유지. `V1ContentRender`의 edit 트리에서 group 분기 1개만 추가.

---

# 9. 마이그레이션 / 공존

- **데이터 마이그레이션 없음**: 기존 슬라이드는 `layout_group ∈ {general, pitch-deck, ...}` 그대로 → 레거시 TSX로 렌더·편집·export. 신규 덱만 `layout_group="adaptive"`. `getLayoutByLayoutId`가 group으로 자동 분기하므로 **두 시스템이 영구 공존**한다(레거시 미삭제).
- **점진 롤아웃**: 생성 경로를 feature flag(env 또는 요청 파라미터, 예: `ADAPTIVE_COMPOSER=true`)로 게이트. flag off → 기존 structure+content 파이프라인, flag on → `compose_slides` + group "adaptive". 기본 off로 출하 후 archetype·fit 검증 완료 시 on.
- **하위호환**: ordered-template identity 분기 유지, custom-template 런타임 로딩 경로(`useCustomTemplates`+Tailwind CDN) 유지, SlideModel/PresentationModel 컬럼·`PresentationStructureModel` 형태 무변경.
- **export 공동버전**: data-block-id 등 신규 마커가 v0.2.9 converter에 무해함을 round-trip로 확인 후에만 DOM-contract 확정(7.2-5, 13장).

---

# 10. 엣지 / 실패

- **오버플로**: 5.2의 4층 방어 + `overflow-hidden h-[720px] w-[1280px]` 하드 backstop. JS fit 미실행 시에도 `clamp()`가 fit를 보장.
- **빈 콘텐츠**: optional 블록은 미방출(빈 leaf 금지 → export에 빈 textbox 안 생김). archetype 최소 블록(min) 미충족 시 컴포저 재시도(`generate_structured_with_schema_retries`), 최후 fallback archetype=`one-column-bullets`/`big-statement`.
- **과대 콘텐츠**: 컴포저가 capacity menu로 1차 회피 → 그래도 초과 시 `apply_capacity_fit` validator가 same-kind upgrade(`_find_fitting_layout`) 또는 split(`_split_content`, max_parts) 수행(layout_capacity.py:312-330). split 책임은 컴포저가 우선 보유, validator는 안전망.
- **archetype 미스**: archetype이 닫힌 enum → out-of-range 불가(클램프/random index 루프 삭제). kind 미스매치(예: table 콘텐츠에 stat-hero) 시 validator가 kind 기준 재매핑.
- **fit 실패**: clamp → JS fit → `-webkit-line-clamp`(truncatable body만) → overflow-hidden. 절대 캔버스 밖 페인트 안 함.
- **부분 실패**: per-slide `SlideErrorBoundary`(현존) 유지. 컴포저는 슬라이드별 검증·재시도; 특정 SlideSpec invalid 시 그 슬라이드만 최소 text archetype으로 폴백(덱 전체 실패 방지). asset fetch 실패는 placeholder 마커 유지(현행 동작).

---

# 11. 단계별 구현 계획

**Phase 1 — 렌더러+스펙 스캐폴드 (회귀 위험 최소, 생산 경로 무변경).**
- 파일: `app/presentation-templates/adaptive/spec.ts`(Zod), `app/presentation-templates/adaptive/AdaptiveSlide.tsx`(+ `BlockRenderer`, `ArchetypeLayout`, `Motif`, `BrandSlot`), `index.tsx`에 group "adaptive" 등록, `app/presentation-templates/adaptive/settings.json`. backend `models/slide_spec_model.py`.
- archetype 3종만(`cover`, `one-column-bullets`, `stat-hero`)으로 시작.
- 생성 경로는 건드리지 않음. 손으로 작성한 SlideSpec을 DB에 넣어 `/pdf-maker` 렌더 → **round-trip export 테스트**(PPTX 재오픈, 도형 수 assert)로 DOM 계약 확인.
- 위험: 낮음(기존 흐름 무영향). 검증: 렌더 스냅샷 + export 도형 수.

**Phase 2 — 테마 토큰 확장.**
- 파일: `models/theme_data.py`(nested 통일), `theme_generate.py`(style preset 동반 산출), `applyPresentationThemeDom.ts`(단일 `applyPresentationTheme`+`normalizeTheme`로 4중 통합), `tailwind.config`(토큰 var 매핑), `<Motif>`/`BrandSlot`.
- 위험: 중(4중 apply 통합, 레거시 v1 normalize). 검증: 기존 덱 시각 회귀 스냅샷 + 신규 토큰 적용.

**Phase 3 — AI 컴포저 (flag off 기본).**
- 파일: `utils/llm_calls/compose_slides.py`, `get_dynamic_models.py`(`get_composition_model_with_n_slides`), `endpoints/presentation.py`(`/prepare`·handler 통합 지점, flag), `apply_capacity_fit`를 validator로 호출.
- 위험: 중(LLM 출력 스키마 안정성). 검증: 스키마 검증·variety·fit 단위테스트 + 골든 브리프→덱 통합테스트.

**Phase 4 — 제네릭 블록 에디터.**
- 파일: `components/BlockBindingWrapper.tsx`(data-block-id 바인딩), `V1ContentRender.tsx`(group "adaptive" edit 분기), slice 블록 CRUD reducer, 블록 패널(`getSchemaDefaults`/`z.toJSONSchema` 재사용), `EditableLayoutWrapper` data-path 키 전환.
- 위험: 중(바인딩 교체). 검증: 중복 문자열 misbind 회귀 테스트, 블록 CRUD, autosave round-trip.

**Phase 5 — archetype 라이브러리 완성 + fit 엔진.**
- 나머지 11개 archetype, 공유 density helper, clamp+JS fit+line-clamp, 컴포저 variety/adjacency 규칙 강화.
- 위험: 중. 검증: archetype별 min/max 밀도 Cypress 컴포넌트 + 오버플로 무발생.

**Phase 6 — 롤아웃.**
- `ADAPTIVE_COMPOSER` 기본 on(신규 덱), 레거시·custom 잔존. export 공동버전 확정.
- 위험: 중. 검증: 전체 회귀 + export round-trip 스위트.

---

# 12. 테스트 계획

- **Export round-trip(최우선, v0.2.9 pin)**: SlideSpec → `/pdf-maker` → PPTX → python-pptx 재오픈 → archetype별 기대 textbox/picture/table/chart **도형 수·텍스트 내용** assert. blocks→shape 1:1 보장 회귀 방지. PDF는 @page 1280×720·page-break 검증.
- **Capacity/fit**: 극단(빈/초과) 콘텐츠에서 bounding box ≤ 1280×720, 오버플로/클립 없음. `compute_layout_capacity`가 blocks 스키마에서 올바른 text_chars/list_items/kind 산출(walker 무변경 가정 검증).
- **컴포저**: archetype enum 유효성, 블록 min/max, variety(연속 동일 금지·breather 주기), fit-aware 선택, n_slides 계약. mock LLM(tests/mocks).
- **렌더러**: archetype별 min/max 밀도 Cypress 컴포넌트 스냅샷.
- **에디터**: data-block-id 결정론(중복 문자열·재정렬·빈 필드 no-misbind), 블록 CRUD splice/reorder, autosave PATCH round-trip, undo/redo.
- **회귀**: 레거시 group(general/pitch-deck/...) 렌더·편집·export 무영향. theme normalize v1→v2 시각 동등.
- **통합**: 골든 ContentBrief → compose → persist → render → export 전 구간.

---

# 13. 미해결 결정사항

1. **capacity walker 호환 정밀도**: blocks[]를 `prefixItems` 튜플로 기술하면 현 `_walk`가 `prefixItems[0]`만 읽어 과소집계(layout_capacity.py:148-150). 권장안은 blocks를 균질 객체 배열(maxItems+maxLength)로 기술해 walker 무변경 — 확정 전 archetype별 capacity 정확도 측정 필요. 부정확하면 `_walk`에 prefixItems 합산(소규모 additive) 추가.
2. **DOM-contract 공동버전**: `data-block-id`/`data-path`가 v0.2.9 converter에 무해(무시)한지 — synced `presentation-export/index.js` 또는 presenton-export@v0.2.9 소스 직접 확인 필요. 신규 마커가 도형 수에 영향 주면 export 공동버전 필요.
3. **bullets 매핑 단위**: `<li>` 묶음이 단일 멀티-paragraph textbox인지 항목별 textbox인지 converter 거동에 의존 — round-trip 도형 수로 확정.
4. **chart 편집 충실도**: Recharts SVG가 native chart로 추출되는지 vs grouped vector로 flatten되는지 — v0.2.9 확인. flatten되면 편집성 정의를 "vector 편집"으로 한정.
5. **motif as SVG**: PPTX에서 도형 복제/flatten 여부 — 장식·저강도 유지로 위험 흡수하되 검증.
6. **fit 동기성**: JS fit-to-box가 외부 converter의 자체 대기창(font/network idle) 안에서 settle하는지 — clamp fallback 병행으로 안전화하나, 필요 시 export 측 ready 신호 협의(공동버전).
7. **split 책임 경계**: 슬라이드 분할을 컴포저 네이티브로 둘지, `_split_content` validator로 둘지 — 둘 다 유지(컴포저 우선, validator 안전망)하되 중복 분할 방지 로직 확정.
8. **레거시 덱 마이그레이션**: 과거 슬라이드를 adaptive로 변환하지 않고 영구 공존(권장) — 제품팀 확정 필요.
9. **컴포저 모델**: `codex/gpt-5.5`의 strict 구조화 출력/스키마 retry 안정성을 `generate_structured_with_schema_retries`로 검증 후 확정.
10. **`process_slide_and_fetch_assets`의 deep-walk**: 중첩 blocks 내부 `__image_url__/__icon_url__` 마커를 현 asset 파이프라인이 재귀 탐색하는지 확인(미흡 시 소규모 재귀 보강).

**근거 파일(절대경로)**: `C:\project\PPT-agent\ppt-agent\servers\fastapi\models\sql\slide.py`, `models\content_brief_model.py`, `models\presentation_structure_model.py`, `models\theme_data.py`, `models\presentation_layout.py`(=`templates\presentation_layout.py`), `utils\layout_capacity.py`, `templates\get_layout_by_name.py`, `api\v1\ppt\endpoints\presentation.py`, `utils\llm_calls\generate_presentation_structure.py`, `generate_slide_content.py`, `services\export_task_service.py`, `utils\export_utils.py`; `C:\project\PPT-agent\ppt-agent\servers\nextjs\app\(export)\pdf-maker\PdfMakerPage.tsx`, `app\(presentation-generator)\components\{PresentationRender,V1ContentRender,TiptapTextReplacer,EditableLayoutWrapper,TiptapText}.tsx`, `presentation\utils\applyPresentationThemeDom.ts`, `app\presentation-templates\index.tsx`, `(dashboard)\theme\components\ThemePanel\{index.tsx,constants.ts}`.