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

export const BlockSchema = z.union([
  TitleBlockSchema,
  SubtitleBlockSchema,
  EyebrowBlockSchema,
  TextBlockSchema,
  BulletsBlockSchema,
  StatBlockSchema,
  ImageBlockSchema,
  QuoteBlockSchema,
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
