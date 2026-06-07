import { TemplateWithData, TemplateGroupSettings, createTemplateEntry, TemplateLayoutsWithSettings } from "./utils";

// Korean business templates
import KoreanBizCover, { Schema as KoreanBizCoverSchema, layoutId as KoreanBizCoverId, layoutName as KoreanBizCoverName, layoutDescription as KoreanBizCoverDesc } from "./korean-biz/CoverSlideLayout";
import KoreanBizToc, { Schema as KoreanBizTocSchema, layoutId as KoreanBizTocId, layoutName as KoreanBizTocName, layoutDescription as KoreanBizTocDesc } from "./korean-biz/TableOfContentsSlideLayout";
import KoreanBizOverview, { Schema as KoreanBizOverviewSchema, layoutId as KoreanBizOverviewId, layoutName as KoreanBizOverviewName, layoutDescription as KoreanBizOverviewDesc } from "./korean-biz/SectionOverviewSlideLayout";
import KoreanBizBullets, { Schema as KoreanBizBulletsSchema, layoutId as KoreanBizBulletsId, layoutName as KoreanBizBulletsName, layoutDescription as KoreanBizBulletsDesc } from "./korean-biz/BulletPointsSlideLayout";
import KoreanBizMetrics, { Schema as KoreanBizMetricsSchema, layoutId as KoreanBizMetricsId, layoutName as KoreanBizMetricsName, layoutDescription as KoreanBizMetricsDesc } from "./korean-biz/MetricsSlideLayout";
import KoreanBizClosing, { Schema as KoreanBizClosingSchema, layoutId as KoreanBizClosingId, layoutName as KoreanBizClosingName, layoutDescription as KoreanBizClosingDesc } from "./korean-biz/ClosingSlideLayout";

// Enterprise single-layout groups (fork-curated)
import FinancialChartLayout, { Schema as FinancialChartSchema, layoutId as FinancialChartId, layoutName as FinancialChartName, layoutDescription as FinancialChartDesc } from "./financial-chart/FinancialChartSlideLayout";
import ComparisonTableLayout, { Schema as ComparisonTableSchema, layoutId as ComparisonTableId, layoutName as ComparisonTableName, layoutDescription as ComparisonTableDesc } from "./comparison-table/ComparisonTableSlideLayout";
import RoadmapLayout, { Schema as RoadmapSchema, layoutId as RoadmapId, layoutName as RoadmapName, layoutDescription as RoadmapDesc } from "./roadmap/RoadmapTimelineSlideLayout";
import OrgChartLayout, { Schema as OrgChartSchema, layoutId as OrgChartId, layoutName as OrgChartName, layoutDescription as OrgChartDesc } from "./org-chart/OrgChartSlideLayout";

// Adaptive layout group (single renderer + per-archetype schemas)
import AdaptiveSlide from "./adaptive/AdaptiveSlide";
import {
  AgendaSpecSchema,
  BigStatementSpecSchema,
  CardGridSpecSchema,
  ChartInsightSpecSchema,
  ClosingSpecSchema,
  ComparisonSpecSchema,
  CoverSpecSchema,
  ImageLedSpecSchema,
  OneColumnBulletsSpecSchema,
  SectionDividerSpecSchema,
  StatHeroSpecSchema,
  TableSpecSchema,
  TimelineSpecSchema,
  TwoColumnSpecSchema,
} from "./adaptive/spec";

// Template group settings
import koreanBizSettings from "./korean-biz/settings.json";
import financialChartSettings from "./financial-chart/settings.json";
import comparisonTableSettings from "./comparison-table/settings.json";
import roadmapSettings from "./roadmap/settings.json";
import orgChartSettings from "./org-chart/settings.json";
import adaptiveSettings from "./adaptive/settings.json";

// Korean business templates array
export const koreanBizTemplates: TemplateWithData[] = [
    createTemplateEntry(KoreanBizCover, KoreanBizCoverSchema, KoreanBizCoverId, KoreanBizCoverName, KoreanBizCoverDesc, "korean-biz", "CoverSlideLayout"),
    createTemplateEntry(KoreanBizToc, KoreanBizTocSchema, KoreanBizTocId, KoreanBizTocName, KoreanBizTocDesc, "korean-biz", "TableOfContentsSlideLayout"),
    createTemplateEntry(KoreanBizOverview, KoreanBizOverviewSchema, KoreanBizOverviewId, KoreanBizOverviewName, KoreanBizOverviewDesc, "korean-biz", "SectionOverviewSlideLayout"),
    createTemplateEntry(KoreanBizBullets, KoreanBizBulletsSchema, KoreanBizBulletsId, KoreanBizBulletsName, KoreanBizBulletsDesc, "korean-biz", "BulletPointsSlideLayout"),
    createTemplateEntry(KoreanBizMetrics, KoreanBizMetricsSchema, KoreanBizMetricsId, KoreanBizMetricsName, KoreanBizMetricsDesc, "korean-biz", "MetricsSlideLayout"),
    createTemplateEntry(KoreanBizClosing, KoreanBizClosingSchema, KoreanBizClosingId, KoreanBizClosingName, KoreanBizClosingDesc, "korean-biz", "ClosingSlideLayout"),
];

// Enterprise single-layout templates (each selectable as its own template)
export const financialChartTemplates: TemplateWithData[] = [
    createTemplateEntry(FinancialChartLayout, FinancialChartSchema, FinancialChartId, FinancialChartName, FinancialChartDesc, "financial-chart", "FinancialChartSlideLayout"),
    createTemplateEntry(KoreanBizCover, KoreanBizCoverSchema, KoreanBizCoverId, KoreanBizCoverName, KoreanBizCoverDesc, "financial-chart", "CoverSlideLayout"),
    createTemplateEntry(KoreanBizToc, KoreanBizTocSchema, KoreanBizTocId, KoreanBizTocName, KoreanBizTocDesc, "financial-chart", "TableOfContentsSlideLayout"),
    createTemplateEntry(KoreanBizClosing, KoreanBizClosingSchema, KoreanBizClosingId, KoreanBizClosingName, KoreanBizClosingDesc, "financial-chart", "ClosingSlideLayout"),
];

export const comparisonTableTemplates: TemplateWithData[] = [
    createTemplateEntry(ComparisonTableLayout, ComparisonTableSchema, ComparisonTableId, ComparisonTableName, ComparisonTableDesc, "comparison-table", "ComparisonTableSlideLayout"),
    createTemplateEntry(KoreanBizCover, KoreanBizCoverSchema, KoreanBizCoverId, KoreanBizCoverName, KoreanBizCoverDesc, "comparison-table", "CoverSlideLayout"),
    createTemplateEntry(KoreanBizToc, KoreanBizTocSchema, KoreanBizTocId, KoreanBizTocName, KoreanBizTocDesc, "comparison-table", "TableOfContentsSlideLayout"),
    createTemplateEntry(KoreanBizClosing, KoreanBizClosingSchema, KoreanBizClosingId, KoreanBizClosingName, KoreanBizClosingDesc, "comparison-table", "ClosingSlideLayout"),
];

export const roadmapTemplates: TemplateWithData[] = [
    createTemplateEntry(RoadmapLayout, RoadmapSchema, RoadmapId, RoadmapName, RoadmapDesc, "roadmap", "RoadmapTimelineSlideLayout"),
    createTemplateEntry(KoreanBizCover, KoreanBizCoverSchema, KoreanBizCoverId, KoreanBizCoverName, KoreanBizCoverDesc, "roadmap", "CoverSlideLayout"),
    createTemplateEntry(KoreanBizToc, KoreanBizTocSchema, KoreanBizTocId, KoreanBizTocName, KoreanBizTocDesc, "roadmap", "TableOfContentsSlideLayout"),
    createTemplateEntry(KoreanBizClosing, KoreanBizClosingSchema, KoreanBizClosingId, KoreanBizClosingName, KoreanBizClosingDesc, "roadmap", "ClosingSlideLayout"),
];

export const orgChartTemplates: TemplateWithData[] = [
    createTemplateEntry(OrgChartLayout, OrgChartSchema, OrgChartId, OrgChartName, OrgChartDesc, "org-chart", "OrgChartSlideLayout"),
    createTemplateEntry(KoreanBizCover, KoreanBizCoverSchema, KoreanBizCoverId, KoreanBizCoverName, KoreanBizCoverDesc, "org-chart", "CoverSlideLayout"),
    createTemplateEntry(KoreanBizToc, KoreanBizTocSchema, KoreanBizTocId, KoreanBizTocName, KoreanBizTocDesc, "org-chart", "TableOfContentsSlideLayout"),
    createTemplateEntry(KoreanBizClosing, KoreanBizClosingSchema, KoreanBizClosingId, KoreanBizClosingName, KoreanBizClosingDesc, "org-chart", "ClosingSlideLayout"),
];

// Adaptive templates array (theme-only + AI-composed adaptive slides)
export const adaptiveTemplates: TemplateWithData[] = [
    createTemplateEntry(AdaptiveSlide, CoverSpecSchema, "cover", "표지", "덱 표지 — 제목·부제·구분선", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, OneColumnBulletsSpecSchema, "one-column-bullets", "핵심 요점", "제목·리드 문장·불릿 목록", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, StatHeroSpecSchema, "stat-hero", "핵심 지표", "제목·핵심 수치 카드", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, SectionDividerSpecSchema, "section-divider", "섹션 구분", "섹션 전환 — 번호·구분 제목", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, BigStatementSpecSchema, "big-statement", "핵심 메시지", "큰 한 줄 메시지 또는 인용", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, AgendaSpecSchema, "agenda", "목차", "제목 + 2~8개 목차 항목", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, ClosingSpecSchema, "closing", "마무리", "마무리 — 제목·부제·연락/CTA", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, CardGridSpecSchema, "card-grid", "카드 그리드", "동등 항목 3~8개 카드(아이콘·제목·설명)", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, ComparisonSpecSchema, "comparison", "비교", "2~3개 열 비교(헤딩·체크 항목)", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, TimelineSpecSchema, "timeline", "타임라인", "순서 단계 3~6개(라벨·제목·설명)", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, TwoColumnSpecSchema, "two-column", "2단 구성", "좌측 텍스트/불릿 + 우측 이미지", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, ImageLedSpecSchema, "image-led", "이미지 중심", "큰 이미지 + 제목·캡션", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, ChartInsightSpecSchema, "chart-insight", "차트 인사이트", "차트 + 핵심 시사점 불릿", "adaptive", "AdaptiveSlide"),
    createTemplateEntry(AdaptiveSlide, TableSpecSchema, "table", "표", "제목 + 표(헤더·행)", "adaptive", "AdaptiveSlide"),
];

// All templates combined (for layout-id resolution)
export const allLayouts: TemplateWithData[] = [
    ...adaptiveTemplates,
    ...koreanBizTemplates,
    ...financialChartTemplates,
    ...comparisonTableTemplates,
    ...roadmapTemplates,
    ...orgChartTemplates,
];

// Template groups with settings. The upstream generic fixed-template groups
// (general / modern / standard / swift / code / education / product-overview /
// report / pitch-deck / neo-*) were HARD-REMOVED (#5) — the `adaptive` composer is
// the default and the fork's curated groups are kept. Existing decks that still
// reference a removed group render a "layout not found" placeholder (V1ContentRender).
export const templates: TemplateLayoutsWithSettings[] = [
    {
        id: "adaptive",
        name: "적응형",
        description: adaptiveSettings.description,
        settings: adaptiveSettings as TemplateGroupSettings,
        layouts: adaptiveTemplates,
    },
    {
        id: "korean-biz",
        name: "한국형 비즈니스",
        description: koreanBizSettings.description,
        settings: koreanBizSettings as TemplateGroupSettings,
        layouts: koreanBizTemplates,
    },
    {
        id: "financial-chart",
        name: "재무·실적 차트",
        description: financialChartSettings.description,
        settings: financialChartSettings as TemplateGroupSettings,
        layouts: financialChartTemplates,
    },
    {
        id: "comparison-table",
        name: "데이터 비교표",
        description: comparisonTableSettings.description,
        settings: comparisonTableSettings as TemplateGroupSettings,
        layouts: comparisonTableTemplates,
    },
    {
        id: "roadmap",
        name: "로드맵·연혁",
        description: roadmapSettings.description,
        settings: roadmapSettings as TemplateGroupSettings,
        layouts: roadmapTemplates,
    },
    {
        id: "org-chart",
        name: "조직도",
        description: orgChartSettings.description,
        settings: orgChartSettings as TemplateGroupSettings,
        layouts: orgChartTemplates,
    },
];

// Legacy fixed-template groups were removed (#5), so nothing is "retired but kept"
// anymore. Kept as an (empty) export for API compatibility with importers.
export const RETIRED_GROUP_IDS = new Set<string>([]);

// Built-in groups offered for NEW decks (all remaining groups are selectable).
export const selectableTemplates: TemplateLayoutsWithSettings[] = templates.filter(
    (t) => !RETIRED_GROUP_IDS.has(t.id)
);

// Helper to get templates by group ID
export function getTemplatesByTemplateName(templateId: string): TemplateWithData[] {
    const template = templates.find((t) => t.id === templateId);
    return template?.layouts || [];
}

export function getSchemaByTemplateId(templateId: string): any {
    const template = templates.find((t) => t.id === templateId);
    return template?.layouts.map(t => {
        return {
            id: t.layoutId,
            name: t.layoutName,
            description: t.layoutDescription,
            json_schema: t.schemaJSON,
        }
    }) || {};
}
export function getSettingsByTemplateId(templateId: string): TemplateGroupSettings | undefined {
    const template = templates.find((t) => t.id === templateId);
    return template?.settings || undefined;
}
// Helper to get template by layout ID
export function getTemplateByLayoutId(layoutId: string): TemplateWithData | undefined {
    return allLayouts.find((t) => t.layoutId === layoutId);
}
export function getLayoutByLayoutId(layout: string, layoutGroup?: string): TemplateWithData | undefined {
    const templateName = layout.split(':')[0]
    const template = templates.find((t) => t.id === templateName)

    if (template) {
        return template.layouts.find((t) => t.layoutId === layout);
    }

    // Backward compatibility: persisted slides from fallback schema API may
    // store raw IDs like "general-intro-slide" (without "<group>:").
    if (layoutGroup) {
        const groupTemplate = templates.find((t) => t.id === layoutGroup);
        const qualifiedLayoutId = `${layoutGroup}:${layout}`;
        return groupTemplate?.layouts.find((t) => t.layoutId === qualifiedLayoutId);
    }

    return allLayouts.find((t) => t.layoutId.endsWith(`:${layout}`));
}
