# Presenton Fork — Content-First (NotebookLM-style) Slide Generation Pipeline 설계서

본 문서는 `C:/project/PPT-agent/ppt-agent` 포크의 현행 `thin-outline -> structure -> capped-content` 흐름을, **Stage A Knowledge Brief(rich, grounded, uncapped) -> Stage B Deck Planner(capacity-aware allocation) -> Stage C Render(format pre-sized chunk)** 의 content-first 파이프라인으로 교체하기 위한 구현 직결 설계서다. 기존 2-phase API(`/prepare` + `/stream`), one-shot API(`/generate`, `/generate/async`), 에디터, PPTX/PDF export는 **무변경으로 계속 동작**해야 한다.

식별자/경로/코드/스키마 필드명/모델 id는 영어로 유지한다.

---

## 1. 아키텍처 개요 + 데이터 흐름

### 1.1 핵심 원칙

현행은 두 번의 독립 LLM 호출로 (1) outline을 슬라이드당 300~2500자 markdown 예산으로 "맹목적으로" 생성하고(`get_presentation_outline_model_with_n_slides`의 `min_length=300, max_length=2500`), (2) 그와 무관하게 `generate_presentation_structure`가 레이아웃을 선택한다. 그 결과 outline의 분량(300~2500자)과 실제 레이아웃 스키마의 `maxLength`(예: swift/Timeline body<=160)가 **이중 사이징 불일치**를 일으켜 Stage C 렌더에서 truncation/padding이 발생한다.

content-first 재설계의 단방향 의존은 다음과 같다.

```
Knowledge Brief (substance, uncapped)
        |
        v   (layout catalog + deterministic capacity profiles)
Deck Planner (content -> slides, capacity-fit layout 선택, split/merge, pre-sized 할당)
        |
        v   (DeckPlan -> (outline, structure) 어댑터)
Render (레이아웃 JSON 스키마로 포맷; 기존 generate_slide_content 무변경 재사용)
```

핵심 변화는 **Stage B가 현행 `generate_ppt_outline`의 슬라이드 사이징 책임과 `generate_presentation_structure`의 레이아웃 선택 책임을 하나의 capacity-aware allocation 단계로 융합**한다는 점이다. 이로써 outline_utils의 TOC 재조정 로직(슬라이드 삽입 후 outline/structure를 lockstep으로 맞추는 코드)도 대부분 소멸한다 — planner가 처음부터 정렬된 슬라이드 리스트를 한 번에 산출하므로 `len(structure.slides) == len(outline.slides)` 불변식이 자동 성립한다.

### 1.2 데이터 흐름 다이어그램 (text)

**Interactive flow (멀티 리퀘스트 UI):**

```
POST /presentation/create            -> row 생성 (metadata only)
GET  /outlines/stream/{id}  (SSE)    -> [STAGE A] generate_content_brief -> ContentBrief 영속화(deck_plan는 아직 없음; brief는 새 컬럼 or mem0)
                                        + (UI 유지를 위해) brief로부터 가벼운 outline 텍스트를 생성/스트림
   (사용자 outline 편집)
POST /presentation/prepare           -> [STAGE B] generate_deck_plan(brief, layout+capacity) -> DeckPlan
                                        -> set_deck_plan(plan)
                                        -> 어댑터로 outlines/structure/layout 동시 기록 (dual-write)
GET  /presentation/stream/{id} (SSE) -> [STAGE C] 기존 per-slide 루프 무변경; outline.slides[i] == allocated chunk
```

**One-shot API flow (headless):**

```
POST /presentation/generate(/async)
  -> check_if_api_request_is_valid (n_slides<=MAX_NUMBER_OF_SLIDES)
  -> DocumentsLoader -> additional_context
  -> [STAGE A] generate_content_brief(content, additional_context, web_search) -> ContentBrief
  -> get_layout_by_name(template) -> layout_model
  -> [STAGE B] generate_deck_plan(brief, layout_model+capacity, n_slides, ...) -> DeckPlan
  -> 어댑터: DeckPlan -> (PresentationOutlineModel, PresentationStructureModel)
  -> [STAGE C] 기존 batched get_slide_content_from_type_and_outline 루프 무변경
  -> persist (deck_plan + outlines + structure + layout + slides + assets) -> export
```

**Bypass 경로 (반드시 보존):**
- `slides_markdown` 존재 시: Stage A/B를 **건너뛴다**. 기존 `generate_presentation_structure(using_slides_markdown=True)` 경로 그대로 유지(markdown -> 레이아웃 선택만).
- `layout.ordered`: Stage B의 레이아웃 선택은 identity로 고정(`to_presentation_structure()`), planner는 content allocation만 수행.

---

## 2. 데이터 모델

### 2.1 ContentBrief — `models/content_brief_model.py` (신규)

```python
from typing import List, Optional
from pydantic import BaseModel, Field

class DataPoint(BaseModel):
    label: str = Field(description="Metric / category / row name, e.g. 'Q3 2025 revenue'")
    value: str = Field(description="Figure as text, keeping units/%/$/dates, e.g. '$4.2M', '37%', '2019'")
    context: Optional[str] = Field(default=None, description="Year, baseline/comparison, or source qualifier")

class BriefSection(BaseModel):
    heading: str = Field(description="Section heading, plain text")
    key_points: List[str] = Field(min_length=2, description="Substantive claims/insights, full sentences")
    facts_figures: List[str] = Field(min_length=1, description="Concrete facts: stats, dates, named examples")
    data_points: List[DataPoint] = Field(default_factory=list, description="Structured numeric/tabular data; empty if none")
    # heading 아래로 maxLength/maxItems 없음 -> Stage A는 uncapped/grounded.

class ContentBrief(BaseModel):
    title: str = Field(description="Concise presentation title, plain text")
    overview: Optional[str] = Field(default=None, description="1-3 sentence framing")
    sections: List[BriefSection] = Field(min_length=1, description="Logically ordered coverage")
```

설계 결정: Stage A는 슬라이드 수/레이아웃을 알지 못한다(`min_length`만으로 richness floor를 강제, `maxLength`/`maxItems` 전무). `strict=False`로 호출한다(uncapped string + optional field). `data_points`는 Stage B에서 chart/table 레이아웃으로 매핑되는 1급 데이터다.

### 2.2 DeckPlan / PlannedSlide — `models/deck_plan_model.py` (신규)

```python
from typing import List
from pydantic import BaseModel, Field

class PlannedSlide(BaseModel):
    title: str = Field(description="Slide title, plain text")
    layout_id: str = Field(description="id of the chosen layout, EXACTLY as given in the catalog")
    allocated_content: str = Field(
        min_length=40,
        description=("Content assigned to THIS slide, Markdown, ALREADY trimmed to the chosen layout's "
                     "capacity (field char budgets and item counts). Start with '## title', then bullets / "
                     "a small table. Self-contained: the render stage adds or cuts nothing."),
    )
    speaker_note_hint: str = Field(default="", description="Surplus detail/figures for the speaker note")
    source_ref: List[str] = Field(default_factory=list, description="Brief section heading(s) this slide draws from")

class DeckPlan(BaseModel):
    title: str = Field(description="Presentation title (carry/refine from the brief)")
    slides: List[PlannedSlide] = Field(min_length=1, description="Ordered slides")
```

- `allocated_content`는 스키마 상 uncapped로 둔다(레이아웃마다 용량이 달라 단일 maxLength 부적합). 실제 한계는 (a) 프롬프트의 capacity 블록, (b) layout_capacity 결정론 안전망(§4), (c) Stage C가 사용하는 레이아웃 스키마 자체의 maxLength가 강제한다. 선택적으로 폭주 방지용 `maxLength≈3000` 가드만 둘 수 있다.
- `layout_id`는 catalog의 `SlideLayoutModel.id`(즉 `"<group>:<layoutId>"`)와 정확히 일치해야 한다. 파싱 후 catalog 대조 검증 + 무효 id는 random valid index로 폴백(§8).

### 2.3 영속화 + 하위호환 어댑터

**영속화 (KISS — `theme` 컬럼 추가 방식 그대로 미러링):** `presentations` 테이블에 **nullable JSON 컬럼 1개만** 추가. 새 테이블/기존 컬럼 재용도 금지.

`models/sql/presentation.py` (현행 컬럼은 16~44행, accessor는 64~81행):

```python
# 컬럼 추가 (theme 바로 옆, 44행 이후)
deck_plan: Optional[dict] = Field(sa_column=Column(JSON), default=None)

# accessor 추가 (get/set_structure 옆, 81행 이후)
def get_deck_plan(self):
    if not self.deck_plan:
        return None
    return DeckPlanModel(**self.deck_plan)   # = DeckPlan

def set_deck_plan(self, plan):
    self.deck_plan = plan.model_dump(mode="json")
```

`get_new_presentation()`(46~62행)의 복사 목록에 `deck_plan=self.deck_plan` 추가(`/derive`가 plan 보존). ContentBrief는 별도 컬럼이 아니라 mem0(이미 호출되는 `store_generation_context`/`store_generated_outlines`) 또는 동일 패턴의 추가 컬럼 중 하나를 선택한다 — 본 설계는 **brief를 mem0에 저장하고 deck_plan만 컬럼화**하는 KISS 안을 채택한다(brief는 prepare/stream에서 재참조될 필요가 없고, plan만 authoritative하면 충분하기 때문). brief를 prepare 단계에서 다시 읽어야 한다면(interactive 분기) ContentBrief도 동일 패턴의 `content_brief` 컬럼으로 승격한다(open question §11).

**하위호환 — Strategy A (dual-write / materialized projection, 채택):** DeckPlan을 새로운 source of truth로 하되, 모든 기존 write 지점에서 어댑터로 outlines/structure/layout을 **계속 함께 채운다**. 레거시 컬럼은 DeckPlan의 충실한 materialized view가 되어 모든 현행 reader(stream_presentation, slide.edit, chat memory_layer, `_resolve_presentation_fonts`, mem0, 테스트, `PresentationWithSlides`)가 **무변경**으로 동작한다.

`utils/deck_plan_adapter.py` (신규, pure):

```python
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from models.presentation_structure_model import PresentationStructureModel
import random

def deck_plan_to_outline(plan) -> PresentationOutlineModel:
    slides = []
    for ps in plan.slides:
        content = ps.allocated_content
        if ps.speaker_note_hint:
            content = f"{content}\n\nSpeaker note: {ps.speaker_note_hint}"
        slides.append(SlideOutlineModel(content=content))
    return PresentationOutlineModel(slides=slides)

def deck_plan_to_structure(plan, layout) -> PresentationStructureModel:
    total = len(layout.slides)
    indices = []
    for ps in plan.slides:
        try:
            idx = layout.get_slide_layout_index(ps.layout_id)  # 미존재 시 HTTPException(404)
        except Exception:
            idx = random.randint(0, total - 1)                  # presentation.py:323/812 폴백과 동형
        if idx >= total:
            idx = random.randint(0, total - 1)
        indices.append(idx)
    return PresentationStructureModel(slides=indices)
```

- `layout`은 오늘과 동일하게 snapshot으로 저장한다 — chat/fonts가 `presentation.layout["name"]`을 **accessor 없이 직접** 읽고, `get_layout()`에 None 가드가 없으므로 생략 불가. plan으로 재구성도 불가(json_schema 포함 self-contained snapshot).
- **불변식 보장:** outline과 structure를 **동일한 `plan.slides` 리스트**에서 lockstep으로 생성하므로 `len(structure.slides) == len(outline.slides)`가 구조적으로 성립. 별도 clamp/TOC 재조정 불필요(이것이 KISS의 핵심 이득). 다만 무효 layout_id에 대한 random-fallback clamp는 `deck_plan_to_structure` 안에 흡수해 기존 안전망(presentation.py:321-328, 810-817)과 동형으로 보존.

---

## 3. 3단계 상세

세 단계 모두 기존 빌더 컨벤션을 그대로 따른다: `get_client(config=get_llm_config())`, `model = get_model()`(코덱스 **gpt-5.5**로 해석됨), `prepare_schema_for_validation(...) + ensure_array_schemas_have_items(...)`, `JSONSchemaResponse(name="response", json_schema=..., strict=...)`, `generate_structured_with_schema_retries(client, model, messages=..., response_format=..., json_schema=..., strict=..., validate_schema=True)`.

### 3.1 Stage A — Knowledge Brief

- **파일:** `utils/llm_calls/generate_content_brief.py` (신규; `generate_presentation_outlines.py` 구조 미러링 — `get_system_prompt`/`get_user_prompt`/`get_messages`/`async generate_content_brief`).
- **입력:** `content: str`, `language: Optional[str]`, `additional_context: str`(DocumentsLoader 결과), `tone`, `verbosity`, `instructions`, `web_search: bool`. **`n_slides`는 입력하지 않는다**(soft breadth hint로만 선택적). 
- **출력:** `ContentBrief`.
- **모델:** codex **gpt-5.5** via `get_model()`. `web_search=True`면 `tools=[WebSearchTool()]`(outline 경로와 동일). 주 경로는 non-stream structured; 라이브 UI 스트림이 필요하면 `stream_generate_events` 패턴 재사용 가능.
- **호출 골격:**

```python
response_model = get_content_brief_model(verbosity)        # get_dynamic_models.py 추가 (minItems-only)
schema = prepare_schema_for_validation(response_model.model_json_schema(), strict=False)
schema = ensure_array_schemas_have_items(schema)
response_format = JSONSchemaResponse(name="response", json_schema=schema, strict=False)
content = await generate_structured_with_schema_retries(
    client, model, messages=get_messages(...),
    response_format=response_format, json_schema=schema, strict=False, validate_schema=True)
return ContentBrief(**content)
```

- **프롬프트 스케치 (SYSTEM, 요지):** "expert subject-matter researcher; produce a COMPLETE, uncapped KNOWLEDGE BRIEF — raw substance BEFORE slides exist. NOT slides; ignore slide counts/layout shapes/per-field limits." Depth/grounding 문장은 `generate_presentation_outlines.py:79,87-89`의 완화된 strict-source + data mandate를 **그대로 차용**: "ground in source, may enrich with well-established domain knowledge; never fabricate precise stats/quotes/sources." + "proactively include figures/statistics/dates/named examples for every section." + "capture quantitative data as structured data_points." Output rules: plain text, no markdown headings/bold/emoji/$schema, language guideline, web search when current/factual info needed.
- **USER:** `Content / Language(_resolve_prompt_language) / Tone / Today's Date(datetime.now().strftime('%Y-%m-%d')) / Instructions / Context(additional_context or 'None')`. **'Number of Slides' 라인 없음**(슬라이드 수와 decoupled).

### 3.2 Stage B — Deck Planner

- **파일:** `utils/llm_calls/generate_deck_plan.py` (신규; `generate_presentation_structure.py` 미러링 — `get_messages` + `async generate_deck_plan`).
- **입력:** `content_brief: ContentBrief`, `presentation_layout: PresentationLayoutModel`(+ capacity), `n_slides: Optional[int]`, `language`, `include_title_slide: bool`, `include_table_of_contents: bool`, `instructions`.
- **출력:** `DeckPlan`.
- **모델:** codex **gpt-5.5** via `get_model()`. `strict=False`.
- **호출 골격:**

```python
response_model = get_deck_plan_model_with_n_slides(n_slides)   # n>0이면 slides min=max=n; else DeckPlan
schema = prepare_schema_for_validation(response_model.model_json_schema(), strict=False)
schema = ensure_array_schemas_have_items(schema)
response_format = JSONSchemaResponse(name="response", json_schema=schema, strict=False)
content = await generate_structured_with_schema_retries(
    client, model, messages=get_messages(presentation_layout, content_brief, n_slides,
        language, include_title_slide, include_table_of_contents, instructions),
    response_format=response_format, json_schema=schema, strict=False, validate_schema=True)
plan = DeckPlan(**content)
# 파싱 후: 각 layout_id를 catalog와 대조; 무효 id는 어댑터에서 random valid index로 폴백
return plan
```

- **프롬프트 스케치 (SYSTEM, 요지):** "presentation architect. Given a KNOWLEDGE BRIEF + a CATALOG OF SLIDE LAYOUTS each with a CAPACITY PROFILE. Plan an ordered deck: assign content to slides, choose a layout whose capacity FITS, write content already trimmed to fit." 핵심 규칙 블록:
  - *Match volume to capacity:* per-field char budget / item slot count·per-item budget / media(table·chart·image) 지원을 읽고 BEST-fit 선택. 용량 초과 레이아웃 금지, padding 필요한 oversized 레이아웃 금지. 예산 초과 시 더 짧게 rephrase, **fact는 절대 silently drop 금지 — 잉여는 `speaker_note_hint`로**.
  - *Split & merge:* 가장 큰 적합 레이아웃도 담지 못하면 연속 슬라이드로 SPLIT(각 split은 self-contained, `source_ref`에 동일 heading 반복). thin 섹션은 작은 용량 레이아웃 선택 또는 인접 thin 섹션 MERGE. filler 금지.
  - *Layout selection(기존 structure stage에서 이식):* numeric series/metrics -> chart/graph; n열 tabular는 n-1 charts 지원 레이아웃 우선·multi-chart 우선; non-numeric에 chart 금지; 실제 tabular text 없으면 table 금지; visual subject 없으면 image 레이아웃 금지; visual variety(인접 슬라이드 레이아웃 상이); opening/closing -> title 레이아웃; TOC 레이아웃은 TOC 요청 시에만.
  - *Allocation:* 모든 슬라이드는 title + catalog에서 **정확히 복사한 layout_id** + `source_ref`. `speaker_note_hint`는 잉여 figure의 안식처. 사실/수치/data_points 보존, brief와 수치 일관, 새 사실 도입 금지.
  - *Count:* n_slides 주어지면 EXACTLY n; 아니면 content volume에 맞게. `include_title_slide`/`include_table_of_contents` 준수(planner가 직접 title/TOC 슬라이드를 emit하므로 후처리 삽입 불필요).
- **USER (`get_messages`):**

```
# Layout catalog (with capacity)
{presentation_layout.to_string(with_capacity=True)}     # §4의 신규 렌더 모드
--------------------------------------
# Knowledge brief
{content_brief.model_dump_json()}
--------------------------------------
Number of Slides: {n_slides or 'auto-detect'}
Language: {_resolve_prompt_language(language)}
Include Title Slide: {include_title_slide}
Include Table Of Contents: {include_table_of_contents}
Instructions: {instructions or ''}
```

### 3.3 Stage C — Render (generate_slide_content **무변경**)

- **재사용 지점:** `utils/llm_calls/generate_slide_content.py::get_slide_content_from_type_and_outline(slide_layout, outline, language, tone, verbosity, instructions)` (172행). 시그니처/내부 무변경. `__speaker_note__` 필드(minLength=100, maxLength=500)는 이미 `add_field_in_schema`로 주입되므로 `speaker_note_hint`는 다운스트림 스키마 변경 불필요.
- **연결 방식 (orchestration 레이어 어댑터, 렌더러 0줄 변경):** DeckPlan을 stream/generate 루프가 이미 기대하는 두 모델로 변환(§2.3 어댑터). `outline.slides[i].content`가 이제 pre-sized chunk(+ `Speaker note:` trailer)가 되어 기존 batch 루프(`presentation.py:887-898`)와 stream 루프(`presentation.py:406-454`)가 **그대로** 구동된다.
- **선택적 1문장 프롬프트 nudge (이미 적용된 depth lever와 짝):** `SLIDE_CONTENT_SYSTEM_PROMPT`의 'Content Depth' 블록에 정확히 한 문장 추가 — *"The provided slide content is already scoped and sized to THIS layout — render its points faithfully into the layout's slots; do not summarize away facts and do not invent items beyond what the content supports."* 시그니처/필드 변화 없음. 이것이 Stage C delta의 전부다.

---

## 4. `utils/layout_capacity.py` 모듈 스펙 + 결정론 알고리즘

§3의 capacity profile은 **LLM 출력이 아니라 json_schema에서 결정론적으로 계산**한다. 본 설계는 이 알고리즘을 전용 모듈 `utils/layout_capacity.py`로 분리한다(자기완결적·재사용·테스트 용이; `schema_utils.py`의 dead helper `generate_constraint_sentences`(340행, `# ? Not used`)를 대체/은퇴시키고, `resolve_ref`만 `schema_utils`에서 재사용). 이 모듈은 **Stage B의 결정론 안전망**이다 — 프롬프트에 capacity 블록을 주입하고, planner의 할당을 사후 검증/clamp한다.

### 4.1 공개 API

```python
# utils/layout_capacity.py
from dataclasses import dataclass

CHART_TYPE_VOCAB = {"bar","line","area","pie","scatter","donut","doughnut","radar","column","bubble"}
MARKER_KEYS = {"__image_url__","__image_prompt__","__icon_url__","__icon_query__"}

@dataclass
class LayoutCapacity:
    text_chars: int
    list_items: int
    has_image: bool
    has_chart: bool
    has_table: bool
    kind: str   # "chart" > "table" > "image" > "list" > "text"

def compute_layout_capacity(json_schema: dict) -> LayoutCapacity: ...
def render_capacity_line(name: str, cap: LayoutCapacity) -> str: ...   # 프롬프트용 1줄
```

그리고 `templates/presentation_layout.py::PresentationLayoutModel`에 신규 렌더 모드 추가:

```python
def to_string(self, with_schema: bool = False, with_capacity: bool = False) -> str:
    # 기존 루프(46-59행) 유지; with_capacity일 때 각 슬라이드 블록에
    # render_capacity_line(...)로 만든 "- Capacity: ..." 한 줄 append
```

### 4.2 결정론 알고리즘 (single DFS, 정수 산술, 무작위성 없음)

입력은 단일 레이아웃의 컴파일된 `json_schema`(Zod 4 `z.toJSONSchema`, draft 2020-12). 누산기 초기화: `text_chars=0, list_items=0, has_image=False, has_chart=False, has_table=False`, `root`=전체 스키마.

순수 헬퍼(properties dict `P` 기반, 이름 기반):
- `is_chart_container(P)`: `P`의 어떤 키 스키마가 `enum`(또는 `const`)을 전부 `CHART_TYPE_VOCAB`에서 가지고, 동시에 형제 키 중 array(`type=="array"` 또는 `items` 보유)가 존재.
- `is_table_container(P)`: `"rows" in P AND ("headers" in P OR "columns" in P)`.

`walk(node, mult, suppress_list)` — `mult`=감싸는 모든 array의 `maxItems` 곱(시작 1), `suppress_list`=chart/table 컨테이너 서브트리 진입 시 True(시작 False):

1. **REF:** `$ref`(`"#/..."`)면 `schema_utils.resolve_ref(root=root, ref=...)`로 해소 후 진행(현행 레이아웃은 전부 inline이라 미발생하나 custom 대비).
2. **UNION/INTERSECTION:**
   - `anyOf`/`oneOf`: 각 branch를 isolated walk로 sub-result 계산. flags는 OR-merge(globals), text/list는 **MAX branch만** 가산(`text_chars += max(t_i); list_items += max(l_i)`). return.
   - `allOf`: 각 entry를 `walk(entry, mult, suppress_list)`(교집합 => 합). return.
3. `t = node.get("type")`(list면 첫 non-"null").
4. **OBJECT** (`t=="object"` 또는 `properties` 존재): `P=node["properties"]`. `"__image_url__" in P` -> `has_image=True`. `chart=is_chart_container(P)`, `table=is_table_container(P)` -> 각각 `has_chart/has_table` set. **marker 객체**(`"__image_url__" in P or "__icon_url__" in P`)면 내부 string(url/prompt/query)을 가산하지 않고 **return**. 그 외엔 `child_suppress = suppress_list or chart or table`로 각 child를 `walk`. return.
5. **ARRAY** (`t=="array"` 또는 `items` 존재): `n = maxItems or minItems or 1`; `child_mult = mult*n`; `items = node.items or (prefixItems or [{}])[0] or {}`. `items_is_object = items.type=="object" or "properties" in items`. `if items_is_object and not suppress_list: list_items += child_mult`. 그 후 `walk(items, child_mult, suppress_list)`(내부 string은 list-suppress여도 text는 계속 가산 — table cell / chart label은 실제 표시 텍스트). return.
6. **STRING** (`t=="string"`): `enum`/`const` 있으면 closed vocab -> return(0). `ml = maxLength`; `if ml: text_chars += mult * ml`. maxLength 없는 string은 0(보수적). return.
7. number/integer/boolean/null: 0. return.

종료 후 **KIND** (우선순위, flags+list_items 순수 도출): `has_chart -> "chart"`; `elif has_table -> "table"`; `elif has_image and list_items==0 -> "image"`; `elif list_items>=1 -> "list"`; `else -> "text"`.

핵심 성질: `text_chars` = 모든 non-marker·non-enum string leaf의 (maxLength × 감싸는 array maxItems 곱) 합(worst-case 표시 문자 예산, table cell·chart axis label 포함). `list_items` = chart/table 컨테이너 **밖**의 array-of-objects만 (maxItems × 곱) 합(chart data·table row는 has_chart/has_table로 신호, list_items=0 유지).

### 4.3 실제 샘플 용량값 (선적 스키마에서 검증)

| group | layout | kind | text_chars | list_items |
|---|---|---|---|---|
| general | NumberedBulletsSlideLayout | list | 730 | 3 |
| general | BulletWithIconsSlideLayout | list | 670 | 3 |
| financial-chart | FinancialChartSlideLayout | chart | 304 | 3 |
| comparison-table | ComparisonTableSlideLayout | table | 728 | 0 |
| roadmap | RoadmapTimelineSlideLayout | list | 510 | 5 |
| org-chart | OrgChartSlideLayout | list | 620 | 5 |

검증 산술 예: NumberedBullets = title 40 + image(marker 제외) + bulletPoints(maxItems 3)×{title 80 + description 150}=690 => 730, list_items 3. financial-chart = title 40 + chart container(has_chart; data array는 list-suppress이나 name label 7×12=84는 가산) + highlights(3)×{value12+label20+caption28}=180 => 304, list_items 3(highlights만). comparison-table = root table container(list-suppress): title 40 + columns(5×16=80) + rows(6)×{label 24, values(4)×16} + note 80 => 728, list_items 0.

**프롬프트 렌더 예 (`render_capacity_line`):**
```
### Layout: 2  NumberedBulletsSlideLayout
- Description: numbered bullet list with cover image
- Capacity: text<=730 chars; list items=3; media: chart=no table=no image=yes; kind=list
```

### 4.4 Stage B 결정론 안전망으로서의 사용 (이미 적용된 prompt depth lever와 결합)

이미 적용된 변경 두 가지가 Stage B의 안전망을 구성한다:
1. **prompt-level depth levers (적용됨):** outline 프롬프트(`generate_presentation_outlines.py:79,87-89`)와 slide-content 프롬프트의 grounding/data mandate 및 verbosity 기반 richness 문장. 이를 Stage A(`get_content_brief_model` verbosity floor)와 Stage B(capacity 프롬프트)에 그대로 차용해 **richness floor를 보장하되 capacity가 상한을 강제**한다. Stage C의 "do not invent items beyond content" 1문장이 pre-allocation 환경에서의 fabrication/padding을 차단한다.
2. **layout_capacity.py (계획됨):** (a) planner 프롬프트에 결정론 capacity 블록 주입, (b) **사후 검증**: planner가 산출한 `layout_id`별 capacity를 다시 계산해, allocated_content가 text_chars/list_items를 크게 초과하면(LLM 과할당) 로그/메트릭으로 표시하고, 무효 layout_id는 어댑터의 random-fallback(presentation.py:323/812와 동형)으로 clamp. 이로써 LLM이 결정론 예산을 벗어나도 렌더 단계가 깨지지 않는다.

---

## 5. API / 스트리밍 통합 (정확한 삽입점)

모든 경로는 `servers/fastapi/api/v1/ppt/endpoints/`.

### 5.1 prepare 핸들러 #1 — `presentation.py::prepare_presentation` (290~362행)

현행 310~348행(`ordered` 분기 + `generate_presentation_structure` + clamp(321-328) + TOC(330-348))을 **Stage B + 어댑터**로 교체:

```python
# 305행 이후. presentation에 brief가 있으면(mem0 또는 컬럼) 로드, 없으면 prepare 시점 생성은 하지 않고
#   interactive 경로는 outline 편집본을 brief 대체로 사용하거나, deck_plan을 prepare에서 생성.
plan = await generate_deck_plan(
    content_brief=brief,                      # Stage A 산출(또는 편집 outline 기반 경량 brief)
    presentation_layout=layout,
    n_slides=(presentation.n_slides if presentation.n_slides > 0 else None),
    language=presentation.language,
    include_title_slide=presentation.include_title_slide,
    include_table_of_contents=presentation.include_table_of_contents,
    instructions=presentation.instructions,
)
if layout.ordered:
    structure = layout.to_presentation_structure()           # identity 고정
    structure.slides = structure.slides[:len(plan.slides)]
else:
    structure = deck_plan_to_structure(plan, layout)         # random-fallback clamp 내장
outline_model = deck_plan_to_outline(plan)

# 350~360행 persist는 dual-write로 확장:
presentation.set_deck_plan(plan)                              # 신규 authoritative
presentation.outlines = outline_model.model_dump(mode="json")
presentation.title = title or plan.title or presentation.title
presentation.set_layout(layout)
presentation.set_structure(structure)
await sql_session.commit()
await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(presentation.id, presentation.outlines)
```

330~348행의 `get_no_of_toc_required_for_n_outlines`/`_insert_toc_layouts`/`get_presentation_outline_model_with_toc` 블록은 **제거**한다(planner가 TOC/title 슬라이드를 직접 emit). 단 ordered/bypass 호환을 위해 clamp는 어댑터로 흡수.

### 5.2 prepare 핸들러 #2 — `presentation.py::generate_presentation_handler` (628~1016행, one-shot)

- **Stage A 삽입:** DocumentsLoader(652~660행, `additional_context` 확정) **이후**, outline 메시지 빌드(673행) **이전**. `generate_ppt_outline` 누적 블록(701~744행)을 Stage A로 대체:

```python
# 660행 이후 (using_slides_markdown=False 분기 안)
content_brief = await generate_content_brief(
    content=request.content, language=language_to_use, additional_context=additional_context,
    tone=request.tone.value, verbosity=request.verbosity.value,
    instructions=request.instructions, web_search=request.web_search,
)
```

- **Stage B 삽입:** 레이아웃 로드(787~795행) 이후, 현행 798~840행(structure 분기 + clamp 810-817 + TOC 819-840)을 교체:

```python
if using_slides_markdown:
    # bypass 보존: 기존 generate_presentation_structure(markdown) 경로 그대로
    presentation_outlines = PresentationOutlineModel(slides=[SlideOutlineModel(content=m) for m in request.slides_markdown])
    presentation_structure = (layout_model.to_presentation_structure() if layout_model.ordered
        else await generate_presentation_structure(presentation_outlines, layout_model, request.instructions, True))
    presentation_structure.slides = presentation_structure.slides[:len(presentation_outlines.slides)]
    # 기존 OOB clamp(810-817) 유지
else:
    plan = await generate_deck_plan(
        content_brief=content_brief, presentation_layout=layout_model,
        n_slides=request.n_slides, language=language_to_use,
        include_title_slide=request.include_title_slide,
        include_table_of_contents=request.include_table_of_contents,
        instructions=request.instructions)
    if layout_model.ordered:
        presentation_structure = layout_model.to_presentation_structure()
        presentation_structure.slides = presentation_structure.slides[:len(plan.slides)]
    else:
        presentation_structure = deck_plan_to_structure(plan, layout_model)
    presentation_outlines = deck_plan_to_outline(plan)
```

- **PresentationModel 생성(846~861행)에 dual-write:** 생성자에 `deck_plan=plan.model_dump(mode="json")` 추가(non-markdown 분기), `outlines/layout/structure`는 어댑터 산출로. **strict count mismatch 검사(734~744행)는 제거/완화** — count의 권위는 planner(`get_deck_plan_model_with_n_slides`)로 이동.
- **Stage C 콘텐츠 루프(873~935행) 무변경:** `slide_layouts = [layout_model.slides[idx] for idx in presentation_structure.slides]`, `get_slide_content_from_type_and_outline(slide_layouts[i], presentation_outlines.slides[i], ...)` 그대로.

### 5.3 stream 핸들러 — `presentation.py::stream_presentation` (365~519행)

**완전 무변경.** 전제조건(372~381행: `presentation.structure`, `presentation.outlines` truthy)은 dual-write로 충족. `inner()`의 per-slide 루프(406~454행)는 `enumerate(structure.slides)` + `outline.slides[i]`를 그대로 join. SSE envelope(opening `{ "slides": [ `, per-slide `slide.model_dump_json()`, closing ` ] }`, `slide_assets`, `complete`)는 **byte-identical 유지**. 프런트 incremental-JSON 파서 보존.

### 5.4 outline stream — `outlines.py::stream_outlines` (37~179행)

- **Stage A 삽입점:** `additional_context` 확정(53~62행) 이후, `get_outline_messages`(75행)/`generate_ppt_outline`(103행) 이전. 여기서 `generate_content_brief(...)`를 호출해 brief를 mem0(이미 호출되는 `store_generation_context` 86~101행을 확장)에 저장.
- **TOC/title/n_slides/ordered 흡수:** interactive 경로에서 `/outlines/stream`은 사용자에게 보여줄 outline을 계속 스트림해야 한다(UX 유지). 두 옵션:
  - (KISS) brief에서 섹션 heading + key_points를 가벼운 markdown으로 직렬화해 기존 `chunk` 형식으로 스트림(별도 LLM 호출 없이). `n_slides==0`(auto sentinel)이면 brief 섹션 수를 잠정 표시.
  - 실제 슬라이드 수/레이아웃/TOC/ordered 결정은 모두 **Stage B(prepare)로 지연**된다. `presentation.n_slides`가 0이면 prepare의 planner가 count를 정하고(`final_n_slides = len(plan.slides)`) 그때 set. `include_table_of_contents`/`include_title_slide`/`ordered`는 prepare의 planner 입력으로만 흡수. 즉 outline stream은 더 이상 structure/TOC 책임을 지지 않는다.

### 5.5 SSE 불변

`models/sse_response.py`의 wire format(`event: response\ndata: {json}\n\n`)과 모든 `type`(chunk/slide_assets/complete/error/status) 유지. Stage A/B는 outline·render stream의 SSE 메시지 형태를 바꾸지 않는다.

---

## 6. 영속화 + 하위호환 + Alembic 마이그레이션

- **컬럼/accessor:** §2.3대로 `deck_plan` JSON 컬럼 + `get_deck_plan`/`set_deck_plan` + `get_new_presentation` 복사 목록 확장.
- **Dual-write 커밋 지점 3곳:** `prepare_presentation`(350~360행), `generate_presentation_handler`(846~861행 생성자 + 949~953행 commit), `stream_outlines`(162~168행, brief만 mem0/컬럼). 각 지점에서 `set_deck_plan(plan)` 후 어댑터로 `outlines/structure/layout/title` 동시 기록.
- **Alembic 마이그레이션 (파일 1개):** `alembic/versions/<rev>_add_deck_plan_column.py`, `down_revision = 'c7b70d0f31b1'`(현 head; chain: `00b3c27a13bc -> f42ad4074449 -> 82abdbc476a7 -> 95b5127e93cd -> c7b70d0f31b1`). `f42ad4074449`의 idempotent 패턴 복제:

```python
revision = '<new>'
down_revision = 'c7b70d0f31b1'

def _has_column(table_name, column_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {c["name"] for c in inspector.get_columns(table_name)}

def upgrade():
    if not _has_column('presentations', 'deck_plan'):
        op.add_column('presentations', sa.Column('deck_plan', sa.JSON(), nullable=True))

def downgrade():
    if _has_column('presentations', 'deck_plan'):
        op.drop_column('presentations', 'deck_plan')
```

`_has_column` 가드 필수(마이그레이션이 시작 시 실행 `MIGRATE_DATABASE_ON_STARTUP=true`, 기본 SQLite, legacy DB는 stamp-then-upgrade). **data backfill 불필요**(nullable; 기존 행은 legacy 컬럼으로 계속 동작, 새 행은 양쪽 채움). `migrations.py::_infer_revision_from_schema`에 새 컬럼 인지 추가는 선택(정확성에 불필요).

---

## 7. 에디터 / 프런트엔드 호환

에디터·export는 **outline/structure/layout/deck_plan을 전혀 읽지 않는다**. 읽는 표면은 `PresentationWithSlides`(`GET /presentation/{id}`, `/all`, `/update`, stream completion) — `{ id, title, n_slides, language, theme, slides[], (content/created_at/updated_at/tone/verbosity/fonts) }`. 따라서 어댑터가 만족시켜야 할 것은 **per-slide SlideModel 형태**뿐이다(deck_plan 자체는 노출 안 함).

어댑터가 매 슬라이드에 노출해야 하는 것(하드 불변식):
- **A. `id`, `presentation`**: 유효 UUID string, round-trip 간 **안정**. `update_presentation`(~547행)이 `uuid.UUID(slide.id/.presentation)` 후 delete+re-insert. Stage C가 `SlideModel`을 생성하므로 id는 자동 UUID이며, 어댑터는 별도 id 발급 불필요(기존 흐름과 동일).
- **B. `layout` = `"<group>:<layoutId>"`** (registry `getLayoutByLayoutId` 해소 가능), `layout_group` = 그 group id(또는 `custom-<uuid>`). planner의 `layout_id`는 catalog의 `SlideLayoutModel.id`(= `"<group>:<layoutId>"`)와 동일하므로, 어댑터는 임의 id를 발명하지 않고 catalog id를 그대로 통과시킨다. stream/generate 루프가 `slide_layout.id`/`layout_model.name`을 SlideModel에 set하는 기존 동작 유지.
- **C. `content`**: 해당 레이아웃 Zod Schema 키에 정합, image는 `{__image_url__, __image_prompt__}`, icon은 `{__icon_url__, __icon_query__}`. Stage C(generate_slide_content)가 스키마대로 생성하므로 자동 충족.
- **D. `index`**: 0-based contiguous(루프의 `enumerate`로 보장).
- **E. asset URL / F. theme / G. autosave full-replace**: 모두 기존과 동일(Stage A/B는 slides 테이블 산출물 형태를 바꾸지 않음).

결론: DeckPlan 도입은 프런트에 **불투명**하다. 어댑터가 DeckPlan을 (outline, structure)로 투영하고 Stage C가 동일한 SlideModel을 생성하므로, 에디터/PPTX/PDF export는 무변경 동작한다.

---

## 8. 엣지 / 실패 처리

- **n_slides 고정 vs auto:** `n_slides>0`이면 `get_deck_plan_model_with_n_slides(n)`가 `slides` min=max=n으로 pin. `0`(auto sentinel) 또는 None이면 unconstrained DeckPlan, count는 planner가 결정. one-shot의 strict mismatch(734-744)는 제거 — planner가 권위. `final_n_slides`는 `len(plan.slides)`로 set(846-844 동형). create/validate의 `MAX_NUMBER_OF_SLIDES=50` 상한(presentation.py:253, 590)은 **그대로 유지**(Stage B는 그 안에서 동작; auto 경로도 planner가 50 초과 시 어댑터에서 trim하거나 프롬프트에 상한 명시).
- **ordered 템플릿:** layout 선택 bypass — `to_presentation_structure()` identity, planner는 content allocation만. structure를 `[:len(plan.slides)]`로 trim. ordered에서 planner가 layout_id를 산출해도 무시하고 position i -> layout i.
- **slides_markdown bypass:** Stage A/B **전부 skip**. 기존 `generate_presentation_structure(using_slides_markdown=True)` + markdown outline 직접 사용. TOC도 skip(기존 819~821 gating 유지).
- **MAX_NUMBER_OF_SLIDES:** 입력 검증은 `check_if_api_request_is_valid`(590)와 create(253)에서 선행. auto 경로에서 planner가 초과 산출하면 어댑터에서 `plan.slides = plan.slides[:MAX_NUMBER_OF_SLIDES]` clamp(KISS).
- **토큰 예산 / 대용량 문서:** Stage A는 uncapped지만 입력(`additional_context`)은 DocumentsLoader 결과. 대용량 시 Stage A 입력 truncation은 **현행 outline 경로와 동일한 정책 적용**(별도 신규 청킹 도입 금지 — YAGNI; 필요 시 open question §11). brief 출력이 과대하면 Stage B 프롬프트의 `content_brief.model_dump_json()`이 커지므로, 매우 큰 brief는 Stage B 입력에서 섹션 단위 요약(open question).
- **스키마 검증 retry:** 세 단계 모두 `generate_structured_with_schema_retries(..., validate_schema=True)` 사용 — 기존 retry/`get_schema_validation_errors` 메커니즘 그대로. `strict=False`(Stage A/B/C 공통).
- **부분 실패:**
  - Stage A 실패(파싱/검증): `handle_llm_client_exceptions` 경유 HTTPException -> one-shot은 400, stream은 `SSEErrorResponse`(기존 패턴).
  - Stage B 실패: 동일. 무효 `layout_id`는 **fatal 아님** — `deck_plan_to_structure`의 random-fallback으로 복구(presentation.py:323/812와 동형). count 미스핀은 어댑터 trim/pad로 복구.
  - Stage C per-slide 실패: 기존 stream 루프(410~419행)의 `{"type":"error"}` emit 후 return 그대로. 마지막에 delete+reinsert(492~506행)하므로 mid-stream 실패가 이전 렌더를 파괴하지 않음(보존).
  - TOC 누락(include_table_of_contents=True인데 planner가 TOC 슬라이드 미생성): 소프트 실패(허용) 또는 선택적 결정론 후처리(open question §11). 기존 TOC 삽입 코드를 안전망으로 남길지 여부는 §11.

---

## 9. 단계별 구현 계획

각 Phase는 파일 단위 변경 / 위험 / 검증을 명시한다. KISS·YAGNI·기존 구조 우선·파일 500라인 이내를 준수한다(신규 파일 모두 한도 이내).

### Phase 1 — Knowledge Brief (Stage A)

- **신규:** `models/content_brief_model.py`(§2.1), `utils/llm_calls/generate_content_brief.py`(§3.1).
- **편집:** `utils/get_dynamic_models.py`에 `get_content_brief_model(verbosity)`(minItems-only richness floor; maxItems/maxLength 금지) 추가. `outlines.py::stream_outlines`(62행 이후) + `presentation.py::generate_presentation_handler`(660행 이후)에 Stage A 호출 삽입. brief를 mem0에 저장.
- **위험:** brief가 과대해 다운스트림 토큰 폭증 / web_search tool 통합 회귀. 완화: verbosity floor만 강제(상한은 Stage B). web_search는 기존 outline 경로의 `WebSearchTool()` 사용을 그대로 재사용.
- **검증:** brief 모델 단위 테스트(min_length 강제), Stage A 호출의 mock LLM 테스트(`tests/mocks/`), `additional_context` 주입 회귀. 이 Phase는 Stage B/C에 아직 연결하지 않으므로(Stage B는 여전히 기존 outline 사용) **렌더/생성 회귀 0**으로 안전하게 머지 가능.

### Phase 2 — Deck Planner (Stage B)

- **신규:** `models/deck_plan_model.py`(§2.2), `utils/llm_calls/generate_deck_plan.py`(§3.2), `utils/layout_capacity.py`(§4), `utils/deck_plan_adapter.py`(§2.3).
- **편집:** `templates/presentation_layout.py`에 `to_string(with_capacity=True)`; `utils/get_dynamic_models.py`에 `get_deck_plan_model_with_n_slides(n)`; `models/sql/presentation.py`에 `deck_plan` 컬럼/accessor/`get_new_presentation` 복사; alembic 마이그레이션(§6); `schema_utils.py`의 dead `generate_constraint_sentences` 은퇴; `prepare_presentation`(310~360행)·`generate_presentation_handler`(798~861행)를 Stage B + 어댑터 + dual-write로 교체(§5.1, §5.2); strict count mismatch(734-744) 완화.
- **위험:** (1) 어댑터의 index-alignment 오류 -> stream 루프 IndexError. 완화: 어댑터에서 random-fallback clamp 내장 + 단위 테스트. (2) ordered/markdown bypass 회귀. 완화: 두 bypass를 분기로 명시 보존 + edge_cases 테스트. (3) TOC 후처리 제거로 TOC 슬라이드 누락. 완화: planner 프롬프트의 include_* 준수 + §11 안전망 결정.
- **검증:** `deck_plan_to_structure/outline` 단위 테스트(불변식 `len(structure)==len(outline)`, 무효 layout_id 폴백, ordered identity, clamp). layout_capacity는 §4.3 6개 샘플 값으로 golden 테스트. 통합: prepare -> stream end-to-end(mock LLM)로 `PresentationWithSlides` slides 수/layout/content 검증. one-shot generate -> export 파일 생성 회귀(`tests/integration/test_presentation_generation_flow.py`).

### Phase 3 — Render 정렬 + 결정론 안전망 (Stage C)

- **편집:** `generate_slide_content.py`의 `SLIDE_CONTENT_SYSTEM_PROMPT`에 §3.3의 1문장 nudge만. Stage B 어댑터가 이미 outline.content에 pre-sized chunk + speaker note trailer를 넣으므로 렌더러 시그니처 무변경. layout_capacity 사후 검증(§4.4)을 Stage B 호출 직후 메트릭/clamp로 연결.
- **위험:** nudge가 기존 depth lever와 충돌해 under-population. 완화: nudge는 "render faithfully into slots, don't invent beyond content"로 제한 — 기존 'populate MAXIMUM items' 압력을 상쇄하되 schema 슬롯은 채우도록.
- **검증:** capacity 사후 검증 단위 테스트(과할당 검출). 실제 레이아웃 6종에 대해 stream 1회씩 돌려 truncation/padding이 사라졌는지(allocated_content가 maxLength 내) 회귀. PPTX/PDF export 스냅샷 비교.

---

## 10. 테스트 계획

- **단위:**
  - `test_content_brief_model` — min_length floor, optional/default, `model_json_schema()`가 prepare_schema_for_validation 통과.
  - `test_deck_plan_model` — `get_deck_plan_model_with_n_slides(n)` min=max=n, 무지정 시 unconstrained.
  - `test_layout_capacity` — §4.3의 6개 golden 값 정확 일치 + kind 우선순위(chart>table>image>list>text) + marker 제외 + anyOf MAX branch + array maxItems 곱.
  - `test_deck_plan_adapter` — outline/structure 길이 동일, 무효 layout_id random-fallback, ordered identity, `MAX_NUMBER_OF_SLIDES` clamp, speaker_note trailer 결합.
  - `test_small_surfaces_coverage` 확장 — `get_deck_plan()`/`set_deck_plan()` 타입·None 가드, `get_new_presentation`이 deck_plan 복사.
- **통합:**
  - prepare -> stream(mock LLM) end-to-end: `PresentationWithSlides.slides` 수=plan 길이, 각 slide의 `layout`/`layout_group`/`content` 정합, index contiguous.
  - one-shot `/generate`: brief -> plan -> slides -> export 파일 생성(`test_presentation_generation_flow.py` 기존 단언 `response.get_layout()`, `response.layout["icon_weight"]` 보존 — dual-write로 통과).
  - `/derive` round-trip: deck_plan 보존.
- **edge_cases:** `slides_markdown` bypass(Stage A/B skip), `layout.ordered`(identity), `n_slides>MAX_NUMBER_OF_SLIDES`(검증 단계 400), n_slides=0 auto, web_search on/off, 빈/대용량 문서, planner 무효 layout_id, Stage A/B/C 부분 실패 시 SSE error envelope.
- **regression:** autosave full-replace round-trip(`PATCH /presentation/update`), chat memory_layer가 outlines/layout 폴백을 계속 읽음, mem0 store 호환.
- **마이그레이션:** 빈 DB / theme까지만 있는 legacy DB / 이미 deck_plan 있는 DB 세 경우에 idempotent upgrade·downgrade.

---

## 11. 미해결 결정사항 / 오픈 퀘스천

1. **ContentBrief 영속 위치:** mem0(KISS, 본 설계 기본) vs `content_brief` 컬럼(prepare/stream에서 재참조 필요 시). interactive 경로에서 prepare가 brief를 반드시 다시 읽어야 한다면 컬럼 승격 필요 — 확정 필요.
2. **TOC 안전망 보존 여부:** planner가 `include_table_of_contents=True`에도 TOC 슬라이드를 누락할 때, (a) 소프트 허용(현 설계) vs (b) `select_toc_or_list_slide_layout_index` + `_insert_toc_layouts`를 결정론 후처리 안전망으로 잔존. (b)는 index-alignment 재조정 부담을 다시 들여오므로 KISS 위배 — 기본은 (a).
3. **interactive outline UX:** `/outlines/stream`이 보여줄 outline을 brief 직렬화(별도 LLM 없음)로 할지, 별도 경량 outline 호출을 유지할지. 본 설계는 brief 직렬화(호출 1회 절감)를 권장하나, 사용자가 outline을 편집한 뒤 prepare의 planner가 그 편집을 어떻게 반영할지(편집 outline -> brief 재구성 vs 편집 outline을 planner 입력으로 직접) 확정 필요.
4. **대용량 문서 Stage A 입력 처리:** uncapped brief의 입력 토큰 한계. 현행 outline 경로와 동일 정책 채택(신규 청킹 미도입, YAGNI). 매우 큰 brief가 Stage B 입력을 초과할 때 섹션 요약 도입 여부 — 측정 후 결정.
5. **model id 확정:** 세 단계 모두 `get_model()`(codex **gpt-5.5**) 전제. 단계별 상이 모델(예: Stage A는 large, Stage B는 reasoning) 분리 필요성 — 현재는 단일 모델로 통일(KISS).
6. **`generate_presentation_structure.py` 잔존 범위:** content-first 경로에서는 미호출, markdown bypass에서만 사용. 향후 markdown 경로도 capacity-aware로 통합할지 vs 현행 유지 — 본 설계는 현행 유지(단방향 의존·KISS).

---

**핵심 파일 인덱스 (절대경로):**
- 신규: `C:/project/PPT-agent/ppt-agent/servers/fastapi/models/content_brief_model.py`, `.../models/deck_plan_model.py`, `.../utils/llm_calls/generate_content_brief.py`, `.../utils/llm_calls/generate_deck_plan.py`, `.../utils/layout_capacity.py`, `.../utils/deck_plan_adapter.py`, `.../alembic/versions/<rev>_add_deck_plan_column.py`
- 편집: `.../api/v1/ppt/endpoints/presentation.py`(prepare 290-362, generate 628-1016, stream 365-519 무변경), `.../api/v1/ppt/endpoints/outlines.py`(stream_outlines 37-179), `.../utils/get_dynamic_models.py`, `.../templates/presentation_layout.py`(to_string with_capacity), `.../utils/schema_utils.py`(generate_constraint_sentences 은퇴, resolve_ref 재사용), `.../models/sql/presentation.py`(deck_plan 컬럼/accessor), `.../utils/llm_calls/generate_slide_content.py`(SLIDE_CONTENT_SYSTEM_PROMPT 1문장)
- 무변경 재사용: `.../utils/llm_calls/generate_slide_content.py::get_slide_content_from_type_and_outline`(172), `.../models/presentation_with_slides.py`, `.../models/sql/slide.py`, `.../models/sse_response.py`, 프런트 전체.