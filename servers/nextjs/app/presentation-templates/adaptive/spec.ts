import * as z from "zod";

// Asset markers — reuse the existing convention so the asset pipeline,
// ImageEditor and IconsEditor keep working unchanged.
export const ImageRefSchema = z.object({
  __image_url__: z.string().default(""),
  __image_prompt__: z.string().default(""),
  alt: z.string().optional(),
});

export const IconRefSchema = z.object({
  __icon_url__: z.string().default(""),
  __icon_query__: z.string().default(""),
});

// Typed blocks — each renders to exactly one (or, for lists, one-per-item)
// semantic DOM leaf so editable PPTX export maps it to a discrete shape.
export const TitleBlockSchema = z.object({
  id: z.string(),
  type: z.literal("title"),
  text: z.string().max(80),
});
export const SubtitleBlockSchema = z.object({
  id: z.string(),
  type: z.literal("subtitle"),
  text: z.string().max(140),
});
export const EyebrowBlockSchema = z.object({
  id: z.string(),
  type: z.literal("eyebrow"),
  text: z.string().max(40),
});
export const TextBlockSchema = z.object({
  id: z.string(),
  type: z.literal("text"),
  text: z.string().max(420),
});
export const BulletItemSchema = z.object({
  id: z.string(),
  text: z.string().max(120),
  icon: IconRefSchema.optional(),
});
export const BulletsBlockSchema = z.object({
  id: z.string(),
  type: z.literal("bullets"),
  items: z.array(BulletItemSchema).max(6),
});
export const StatBlockSchema = z.object({
  id: z.string(),
  type: z.literal("stat"),
  value: z.string().max(8),
  label: z.string().max(28),
  delta: z.string().max(16).optional(),
  caption: z.string().max(60).optional(),
});
export const ImageBlockSchema = z.object({
  id: z.string(),
  type: z.literal("image"),
  image: ImageRefSchema,
});
export const QuoteBlockSchema = z.object({
  id: z.string(),
  type: z.literal("quote"),
  text: z.string().max(240),
  attribution: z.string().max(60).optional(),
});
export const CardBlockSchema = z.object({
  id: z.string(),
  type: z.literal("card"),
  title: z.string().max(40),
  text: z.string().max(140),
  icon: IconRefSchema.optional(),
});
export const ColumnBlockSchema = z.object({
  id: z.string(),
  type: z.literal("column"),
  heading: z.string().max(40),
  items: z.array(z.object({ id: z.string(), text: z.string().max(120) })).max(6),
});
export const StepBlockSchema = z.object({
  id: z.string(),
  type: z.literal("step"),
  label: z.string().max(20),
  title: z.string().max(40),
  text: z.string().max(120),
});
export const ChartBlockSchema = z.object({
  id: z.string(),
  type: z.literal("chart"),
  chartType: z.enum(["bar", "line", "area", "pie", "donut"]),
  data: z.array(z.object({ name: z.string().max(24), value: z.number() })).max(8),
});
export const TableBlockSchema = z.object({
  id: z.string(),
  type: z.literal("table"),
  headers: z.array(z.string()).max(6),
  rows: z.array(z.array(z.string())).max(8),
});

export const BlockSchema = z.union([
  TitleBlockSchema,
  SubtitleBlockSchema,
  EyebrowBlockSchema,
  TextBlockSchema,
  BulletsBlockSchema,
  StatBlockSchema,
  ImageBlockSchema,
  QuoteBlockSchema,
  CardBlockSchema,
  ColumnBlockSchema,
  StepBlockSchema,
  ChartBlockSchema,
  TableBlockSchema,
]);
export type Block = z.infer<typeof BlockSchema>;

export interface SlideSpec {
  archetype: string;
  variant?: string;
  blocks: Block[];
}

// --- Archetype schemas (Phase 1: cover, one-column-bullets, stat-hero) --- //
// Each archetype = one Zod schema describing its allowed blocks. All three map
// to the single AdaptiveSlide component, which dispatches on `archetype`.

export const CoverSpecSchema = z.object({
  archetype: z.literal("cover").default("cover"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "eyebrow", type: "eyebrow", text: "2026 사업 전략" },
    { id: "title", type: "title", text: "프레젠테이션 제목" },
    { id: "subtitle", type: "subtitle", text: "부제목 또는 한 줄 요약을 입력하세요" },
  ] as any),
});

export const OneColumnBulletsSpecSchema = z.object({
  archetype: z.literal("one-column-bullets").default("one-column-bullets"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "핵심 요점" },
    { id: "lead", type: "text", text: "이 슬라이드의 핵심 메시지를 한두 문장으로 제시합니다." },
    {
      id: "bullets",
      type: "bullets",
      items: [
        { id: "b1", text: "첫 번째 근거 또는 요점을 구체적으로 설명합니다" },
        { id: "b2", text: "두 번째 근거 또는 요점을 구체적으로 설명합니다" },
        { id: "b3", text: "세 번째 근거 또는 요점을 구체적으로 설명합니다" },
      ],
    },
  ] as any),
});

export const StatHeroSpecSchema = z.object({
  archetype: z.literal("stat-hero").default("stat-hero"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "핵심 지표" },
    { id: "s1", type: "stat", value: "37%", label: "연평균 성장률", delta: "+5%p" },
    { id: "s2", type: "stat", value: "4.2조", label: "시장 규모", caption: "2025년 기준" },
    { id: "s3", type: "stat", value: "1위", label: "국내 점유율" },
  ] as any),
});

export const SectionDividerSpecSchema = z.object({
  archetype: z.literal("section-divider").default("section-divider"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "eyebrow", type: "eyebrow", text: "01" },
    { id: "title", type: "title", text: "시장 분석" },
  ] as any),
});

export const BigStatementSpecSchema = z.object({
  archetype: z.literal("big-statement").default("big-statement"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    {
      id: "statement",
      type: "quote",
      text: "고객 경험의 혁신이 곧 시장 지배력입니다.",
      attribution: "대표이사 김OO",
    },
  ] as any),
});

export const AgendaSpecSchema = z.object({
  archetype: z.literal("agenda").default("agenda"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "목차" },
    {
      id: "bullets",
      type: "bullets",
      items: [
        { id: "a1", text: "시장 현황과 기회" },
        { id: "a2", text: "핵심 전략 방향" },
        { id: "a3", text: "실행 로드맵" },
        { id: "a4", text: "기대 효과" },
      ],
    },
  ] as any),
});

export const ClosingSpecSchema = z.object({
  archetype: z.literal("closing").default("closing"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "감사합니다" },
    { id: "subtitle", type: "subtitle", text: "함께 만들어갈 다음 단계를 논의하겠습니다" },
    {
      id: "bullets",
      type: "bullets",
      items: [
        { id: "c1", text: "contact@company.com" },
        { id: "c2", text: "www.company.com" },
      ],
    },
  ] as any),
});

export const CardGridSpecSchema = z.object({
  archetype: z.literal("card-grid").default("card-grid"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "핵심 역량" },
    { id: "card1", type: "card", title: "빠른 배포", text: "CI/CD로 출시 주기를 단축합니다." },
    { id: "card2", type: "card", title: "강력한 보안", text: "금융권 인증 체계를 충족합니다." },
    { id: "card3", type: "card", title: "무중단 확장", text: "트래픽 급증에도 안정적으로 확장합니다." },
  ] as any),
});

export const ComparisonSpecSchema = z.object({
  archetype: z.literal("comparison").default("comparison"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "도입 전후 비교" },
    { id: "col1", type: "column", heading: "기존 방식", items: [
      { id: "col1.1", text: "수작업 운영" }, { id: "col1.2", text: "높은 장애율" },
    ] },
    { id: "col2", type: "column", heading: "신규 방식", items: [
      { id: "col2.1", text: "운영 자동화" }, { id: "col2.2", text: "장애 60% 감소" },
    ] },
  ] as any),
});

export const TimelineSpecSchema = z.object({
  archetype: z.literal("timeline").default("timeline"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "추진 로드맵" },
    { id: "step1", type: "step", label: "1단계", title: "설계", text: "아키텍처와 표준을 확정합니다." },
    { id: "step2", type: "step", label: "2단계", title: "구축", text: "핵심 기능 MVP를 개발합니다." },
    { id: "step3", type: "step", label: "3단계", title: "확산", text: "전사 적용 및 안정화를 진행합니다." },
  ] as any),
});

export const TwoColumnSpecSchema = z.object({
  archetype: z.literal("two-column").default("two-column"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "시장 기회" },
    { id: "lead", type: "text", text: "디지털 전환 수요가 빠르게 확대되고 있습니다." },
    {
      id: "bullets",
      type: "bullets",
      items: [
        { id: "b1", text: "연 20% 성장하는 클라우드 시장" },
        { id: "b2", text: "규제 완화로 진입 장벽 하락" },
        { id: "b3", text: "대기업 수요 본격화" },
      ],
    },
    { id: "image", type: "image", image: { __image_url__: "", __image_prompt__: "modern city skyline at dusk, blue tones" } },
  ] as any),
});

export const ImageLedSpecSchema = z.object({
  archetype: z.literal("image-led").default("image-led"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "image", type: "image", image: { __image_url__: "", __image_prompt__: "wind turbines on a green hill, clear sky" } },
    { id: "title", type: "title", text: "친환경 에너지 전환" },
    { id: "caption", type: "text", text: "재생에너지 비중을 2030년까지 50%로 확대합니다." },
  ] as any),
});

export const ChartInsightSpecSchema = z.object({
  archetype: z.literal("chart-insight").default("chart-insight"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "매출 성장 추이" },
    {
      id: "chart",
      type: "chart",
      chartType: "line",
      data: [
        { name: "2022", value: 100 },
        { name: "2023", value: 124 },
        { name: "2024", value: 170 },
      ],
    },
    {
      id: "bullets",
      type: "bullets",
      items: [
        { id: "t1", text: "3년 연속 두 자릿수 성장" },
        { id: "t2", text: "신규 사업이 성장 견인" },
      ],
    },
  ] as any),
});

export const TableSpecSchema = z.object({
  archetype: z.literal("table").default("table"),
  variant: z.string().optional(),
  blocks: z.array(BlockSchema).default([
    { id: "title", type: "title", text: "요금제 비교" },
    {
      id: "table",
      type: "table",
      headers: ["구분", "베이직", "프로", "엔터프라이즈"],
      rows: [
        ["월 요금", "1만원", "3만원", "문의"],
        ["지원", "이메일", "우선 지원", "전담 매니저"],
        ["사용자", "5명", "50명", "무제한"],
      ],
    },
  ] as any),
});
